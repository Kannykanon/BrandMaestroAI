"""Chat models for every LLM call in the pipeline.

Plug-and-adapter design:

    LLMProvider       the abstract adapter every provider implements
    VertexAIProvider  Gemini on Google Cloud Vertex AI  (ChatVertexAI)
    GroqProvider      models served by Groq             (ChatGroq)
    ClaudeProvider    Anthropic's Claude models         (ChatAnthropic)
    LLMSingleton      picks the adapter named by LLM_PROVIDER and hands out
                      one chat model per task mode

Nodes only ever call ``LLMSingleton.get(mode).invoke(prompt)``. Adding a
provider means writing one LLMProvider subclass and registering it in
LLMSingleton.PROVIDERS — nothing else changes.
"""
from __future__ import annotations

import logging
import os
import re
import threading
from abc import ABC, abstractmethod
from typing import Any, Dict

from utils.llm_output import message_text

logger = logging.getLogger(__name__)

# How long a cached prompt->response pair is served before the next identical
# call pays for a fresh model invocation. 24h matches the Brand Brain cache TTL
# in brand_metrics.py.
LLM_CACHE_TTL_SECONDS = 86400


def _env(name: str) -> str:
    return os.getenv(name, "").strip()


def _env_int(name: str, default: int) -> int:
    value = _env(name)
    return int(value) if value else default


# A single silently-hanging call is a pipeline-killer, not just slow, so every
# mode gets an explicit, bounded timeout and a small retry count.
DEFAULT_LLM_TIMEOUT_SECONDS = _env_int("LLM_REQUEST_TIMEOUT_SECONDS", 60)
DEFAULT_LLM_MAX_RETRIES = _env_int("LLM_MAX_RETRIES", 2)
# Synthesis writes the whole Brand Brain and legitimately takes longer.
SYNTHESIS_LLM_TIMEOUT_SECONDS = _env_int("LLM_SYNTHESIS_TIMEOUT_SECONDS", 90)


# ---------------------------------------------------------------------------
#  Token telemetry
# ---------------------------------------------------------------------------
# Accumulated per process and reset per generation by celery_task, so each run
# can report what it cost. `cached` is the number of input tokens the provider
# served from its prompt cache, where the provider reports it.
class TokenUsage:
    _lock = threading.Lock()
    calls = 0
    prompt_tokens = 0
    output_tokens = 0
    cached_tokens = 0

    @classmethod
    def record(cls, prompt=0, output=0, cached=0):
        with cls._lock:
            cls.calls += 1
            cls.prompt_tokens += prompt
            cls.output_tokens += output
            cls.cached_tokens += cached

    @classmethod
    def snapshot(cls) -> Dict[str, Any]:
        with cls._lock:
            hit_rate = (cls.cached_tokens / cls.prompt_tokens) if cls.prompt_tokens else 0.0
            return {
                "calls": cls.calls,
                "prompt_tokens": cls.prompt_tokens,
                "output_tokens": cls.output_tokens,
                "cached_tokens": cls.cached_tokens,
                "cache_hit_rate": round(hit_rate, 4),
            }

    @classmethod
    def reset(cls):
        with cls._lock:
            cls.calls = cls.prompt_tokens = cls.output_tokens = cls.cached_tokens = 0


def _extract_usage(message) -> tuple:
    """(prompt, output, cached) token counts from a LangChain message.

    LangChain normalises usage onto message.usage_metadata for every provider.
    """
    usage = getattr(message, "usage_metadata", None)
    if not isinstance(usage, dict):
        return 0, 0, 0
    details = usage.get("input_token_details") or {}
    cached = details.get("cache_read", 0) if isinstance(details, dict) else 0
    return int(usage.get("input_tokens") or 0), int(usage.get("output_tokens") or 0), int(cached or 0)


def _make_token_callback(model_name: str):
    """A callback handler that records token usage for every completed call.

    A LangChain callback rather than a wrapper around .invoke(): it is the
    supported extension point and also sees streaming and batched calls.
    """
    from langchain_core.callbacks import BaseCallbackHandler

    class _TokenCallback(BaseCallbackHandler):
        def on_llm_end(self, response, **kwargs):
            try:
                for gen_list in getattr(response, "generations", []) or []:
                    for gen in gen_list:
                        msg = getattr(gen, "message", None)
                        if msg is None:
                            continue
                        prompt, output, cached = _extract_usage(msg)
                        if prompt or output:
                            TokenUsage.record(prompt, output, cached)
                            logger.info(
                                "LLM usage model=%s prompt=%d output=%d cached=%d",
                                model_name, prompt, output, cached,
                            )
                            return
            except Exception as e:  # telemetry must never break a generation
                logger.debug("Token telemetry unavailable: %s", e)

    return _TokenCallback()


# ---------------------------------------------------------------------------
#  Provider adapters
# ---------------------------------------------------------------------------
class LLMProvider(ABC):
    """Adapter between the pipeline and one LLM provider's LangChain chat model.

    Subclasses declare their name, default model and required credentials, and
    implement to_langchain(). Everything provider-specific lives in the
    subclass; LLMSingleton only ever talks to this interface.
    """

    #: Value of LLM_PROVIDER that selects this adapter.
    name: str = ""
    #: Model used when LLM_MODEL is not set.
    default_model: str = ""
    #: Environment variables that must be set before this provider can run.
    required_env: tuple[str, ...] = ()

    def __init__(
        self,
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 8192,
        timeout: int = DEFAULT_LLM_TIMEOUT_SECONDS,
        max_retries: int = DEFAULT_LLM_MAX_RETRIES,
    ):
        self._model = model or self.default_model
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._timeout = timeout
        self._max_retries = max_retries

    @property
    def model(self) -> str:
        return self._model

    @property
    def temperature(self) -> float | None:
        """The temperature to send, or None when the model rejects one."""
        return self._temperature if self.supports_temperature() else None

    @property
    def max_tokens(self) -> int:
        return self._max_tokens

    @property
    def timeout(self) -> int:
        return self._timeout

    @property
    def max_retries(self) -> int:
        return self._max_retries

    def supports_temperature(self) -> bool:
        """Whether this model accepts a temperature parameter. Override per provider."""
        return True

    @classmethod
    def missing_env(cls) -> list[str]:
        """Required credentials that are not set, for the startup check."""
        return [var for var in cls.required_env if not _env(var)]

    @abstractmethod
    def to_langchain(self, callbacks: list | None = None):
        """Return the configured LangChain chat model."""
        ...

    def __repr__(self) -> str:
        return f"{type(self).__name__}(model={self.model!r}, temperature={self.temperature})"


class VertexAIProvider(LLMProvider):
    """Gemini on Google Cloud Vertex AI.

    Authenticates with Application Default Credentials (a service account on
    the host, or a key file named by GOOGLE_APPLICATION_CREDENTIALS) and bills
    to the Cloud project in PROJECT_ID.
    """

    name = "vertex_ai"
    default_model = "gemini-2.5-flash"
    required_env = ("PROJECT_ID",)

    def __init__(self, model: str | None = None, **kwargs):
        # GEMINI_MODEL predates LLM_MODEL and is still honoured.
        super().__init__(model=model or _env("GEMINI_MODEL") or None, **kwargs)

    def to_langchain(self, callbacks: list | None = None):
        from langchain_google_vertexai import ChatVertexAI

        return ChatVertexAI(
            model=self.model,
            project=_env("PROJECT_ID"),
            location=_env("LOCATION") or "us-central1",
            temperature=self.temperature,
            max_output_tokens=self.max_tokens,
            max_retries=self.max_retries,
            callbacks=callbacks or [],
        )


class GroqProvider(LLMProvider):
    """Open models served on Groq's inference API (GROQ_API_KEY)."""

    name = "groq"
    default_model = "llama-3.3-70b-versatile"
    required_env = ("GROQ_API_KEY",)

    def to_langchain(self, callbacks: list | None = None):
        from langchain_groq import ChatGroq

        return ChatGroq(
            model=self.model,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            timeout=self.timeout,
            max_retries=self.max_retries,
            callbacks=callbacks or [],
        )


class ClaudeProvider(LLMProvider):
    """Anthropic's Claude models (ANTHROPIC_API_KEY)."""

    name = "claude"
    default_model = "claude-opus-5"
    required_env = ("ANTHROPIC_API_KEY",)

    # Claude Opus 4.7+, Sonnet 5 and Fable/Mythos reject sampling parameters
    # with a 400, so temperature is left unset for them.
    _NO_TEMPERATURE = re.compile(r"opus-(4-[7-9]|[5-9])|sonnet-[5-9]|fable|mythos")

    def supports_temperature(self) -> bool:
        return not self._NO_TEMPERATURE.search(self.model.lower())

    def to_langchain(self, callbacks: list | None = None):
        from langchain_anthropic import ChatAnthropic

        return ChatAnthropic(
            model=self.model,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            timeout=self.timeout,
            max_retries=self.max_retries,
            callbacks=callbacks or [],
        )


# ---------------------------------------------------------------------------
#  Model handle
# ---------------------------------------------------------------------------
class ChatModelHandle:
    """A chat model whose responses always carry plain-text content.

    Callers read ``.invoke(prompt).content`` as a string. Claude models with
    thinking on return a list of content blocks instead, so the content is
    flattened to its text here, once, for every provider.
    """

    def __init__(self, llm, provider: LLMProvider):
        self._llm = llm
        self.provider = provider

    @staticmethod
    def _as_text(message):
        text = message_text(message.content)
        if text != message.content:
            message = message.model_copy(update={"content": text})
        return message

    def invoke(self, input, config=None, **kwargs):
        return self._as_text(self._llm.invoke(input, config=config, **kwargs))

    async def ainvoke(self, input, config=None, **kwargs):
        return self._as_text(await self._llm.ainvoke(input, config=config, **kwargs))

    def __getattr__(self, name):
        return getattr(self._llm, name)

    def __repr__(self) -> str:
        return f"ChatModelHandle({self.provider!r})"


# ---------------------------------------------------------------------------
#  Singleton — the provider switch
# ---------------------------------------------------------------------------
class LLMSingleton:
    """Selects the provider adapter and hands out one chat model per task mode.

        LLM_PROVIDER   vertex_ai (default) | groq | claude
        LLM_MODEL      optional; the adapter's default model otherwise
    """

    # The plug board. Register a new LLMProvider subclass here to add a provider.
    PROVIDERS: dict[str, type[LLMProvider]] = {
        VertexAIProvider.name: VertexAIProvider,
        GroqProvider.name: GroqProvider,
        ClaudeProvider.name: ClaudeProvider,
    }
    ALIASES = {
        "vertex": "vertex_ai",
        "google_vertexai": "vertex_ai",
        "anthropic": "claude",
    }
    DEFAULT_PROVIDER = VertexAIProvider.name

    # extraction/enforcement: low for reliable structured JSON
    # synthesis: moderate for analytical reasoning
    # generation: higher for creative writing
    MODE_TEMPERATURES = {
        "extraction":  0.1,
        "enforcement": 0.1,
        "synthesis":   0.3,
        "generation":  0.7,
    }

    # Synthesis needs much more headroom to write the full Brand Brain without
    # truncation. LLM_MAX_TOKENS_<MODE> lowers a cap for models with a smaller
    # output limit.
    MODE_MAX_TOKENS = {
        "extraction":  8192,
        "enforcement": 8192,
        "synthesis":   32768,
        "generation":  8192,
    }

    MODE_TIMEOUTS = {
        "extraction":  DEFAULT_LLM_TIMEOUT_SECONDS,
        "enforcement": DEFAULT_LLM_TIMEOUT_SECONDS,
        "synthesis":   SYNTHESIS_LLM_TIMEOUT_SECONDS,
        "generation":  DEFAULT_LLM_TIMEOUT_SECONDS,
    }

    _instances: dict = {}
    _lock = threading.Lock()
    _cache_initialized = False

    @classmethod
    def provider_name(cls) -> str:
        """The registered provider named by LLM_PROVIDER."""
        raw = _env("LLM_PROVIDER").lower() or cls.DEFAULT_PROVIDER
        name = cls.ALIASES.get(raw, raw)
        if name not in cls.PROVIDERS:
            raise ValueError(
                f"LLM_PROVIDER must be one of {sorted(cls.PROVIDERS)}, got {raw!r}"
            )
        return name

    @classmethod
    def provider_class(cls) -> type[LLMProvider]:
        return cls.PROVIDERS[cls.provider_name()]

    @classmethod
    def create_provider(cls, mode: str = "generation") -> LLMProvider:
        """Instantiate the selected adapter with this mode's tuning."""
        suffix = mode.upper()
        return cls.provider_class()(
            model=_env("LLM_MODEL") or None,
            temperature=float(_env(f"LLM_TEMPERATURE_{suffix}") or cls.MODE_TEMPERATURES.get(mode, 0.7)),
            max_tokens=_env_int(f"LLM_MAX_TOKENS_{suffix}", cls.MODE_MAX_TOKENS.get(mode, 8192)),
            timeout=cls.MODE_TIMEOUTS.get(mode, DEFAULT_LLM_TIMEOUT_SECONDS),
            max_retries=DEFAULT_LLM_MAX_RETRIES,
        )

    @classmethod
    def _init_prompt_cache(cls):
        """
        Cache LLM responses in Redis, keyed on the exact prompt text plus the
        model's serialized params. Every call goes through
        LLMSingleton.get(...).invoke(...), so wiring the cache here once covers
        every mode and every provider.

        Only an identical prompt is served from it — repeated runs against the
        same documents, or a revision loop re-sending an unchanged prompt.

        Falls back to no caching (a warning, not a crash) if Redis is
        unreachable, so a missing cache backend never blocks generation.
        """
        if cls._cache_initialized:
            return
        cls._cache_initialized = True
        try:
            import redis as redis_lib
            from langchain_community.cache import RedisCache
            from langchain_core.globals import set_llm_cache

            redis_url = os.getenv("REDIS_URL", "redis://redis:6379/0")
            client = redis_lib.Redis.from_url(redis_url)
            client.ping()
            set_llm_cache(RedisCache(redis_=client, ttl=LLM_CACHE_TTL_SECONDS))
            logger.info("LLM prompt cache enabled (Redis, ttl=%ds)", LLM_CACHE_TTL_SECONDS)
        except Exception as e:
            logger.warning("LLM prompt cache disabled — Redis unavailable: %s", e)

    @classmethod
    def get(cls, mode: str = "generation") -> ChatModelHandle:
        if mode in cls._instances:
            return cls._instances[mode]

        with cls._lock:
            if mode not in cls._instances:
                cls._init_prompt_cache()

                # Async clients initialised in synchronous worker threads need
                # an event loop to exist.
                import asyncio
                try:
                    asyncio.get_event_loop()
                except RuntimeError:
                    asyncio.set_event_loop(asyncio.new_event_loop())

                provider = cls.create_provider(mode)
                logger.info(
                    "LLMSingleton routing mode=%s provider=%s model=%s temperature=%s "
                    "max_tokens=%d timeout=%ds",
                    mode, provider.name, provider.model, provider.temperature,
                    provider.max_tokens, provider.timeout,
                )
                llm = provider.to_langchain(callbacks=[_make_token_callback(provider.model)])
                cls._instances[mode] = ChatModelHandle(llm, provider)

        return cls._instances[mode]

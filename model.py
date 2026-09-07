from abc import ABC, abstractmethod
from typing import Dict, Any
import logging
import os
import threading

logger = logging.getLogger(__name__)

# LLM_CACHE_TTL: how long a cached prompt->response pair is served before the
# next identical call pays for a fresh Gemini invocation again. 24h matches
# the Brand Brain cache TTL in brand_metrics.py.
LLM_CACHE_TTL_SECONDS = 86400

# Which Gemini backend to call. Both run the same models; they differ in which
# account they bill and how they authenticate. Default is ai_studio so an
# existing GOOGLE_API_KEY setup keeps working with no configuration change.
PROVIDER_AI_STUDIO = "ai_studio"
PROVIDER_VERTEX = "vertex_ai"
LLM_PROVIDER = os.getenv("LLM_PROVIDER", PROVIDER_AI_STUDIO).strip().lower()
if LLM_PROVIDER not in (PROVIDER_AI_STUDIO, PROVIDER_VERTEX):
    raise ValueError(
        f"LLM_PROVIDER must be {PROVIDER_AI_STUDIO!r} or {PROVIDER_VERTEX!r}, "
        f"got {LLM_PROVIDER!r}"
    )

# ChatGoogleGenerativeAI defaults to timeout=None (unbounded — the client
# falls back to the underlying SDK's own default, observed in practice to
# let a single stuck call hang for ~10 minutes before it even raises
# DeadlineExceeded) and max_retries=6 with no cap on top of that unbounded
# per-attempt wait. A single silently-hanging call is a demo-killer, not
# just slow, so every mode gets an explicit, bounded timeout and a small
# retry count instead. Env-overridable so the ceiling can be tuned without
# a code change if a specific environment needs more headroom.
DEFAULT_LLM_TIMEOUT_SECONDS = int(os.getenv("LLM_REQUEST_TIMEOUT_SECONDS", "60"))
DEFAULT_LLM_MAX_RETRIES = int(os.getenv("LLM_MAX_RETRIES", "2"))
# Synthesis writes up to 32768 tokens (vs 8192 for every other mode) and can
# legitimately take longer than the default ceiling on a large Brand Brain.
SYNTHESIS_LLM_TIMEOUT_SECONDS = int(os.getenv("LLM_SYNTHESIS_TIMEOUT_SECONDS", "90"))

# ---------------------------------------------------------------------------
#  Token telemetry
# ---------------------------------------------------------------------------
# The installed langchain-google-genai returns response_metadata with only
# prompt_feedback / finish_reason / safety_ratings — no usage numbers at all —
# so nothing in this system could report what a generation cost or whether any
# prompt caching was working. This wrapper reads usage off the raw SDK response
# where it is available, accumulates it per process, and logs it.
#
# `cached` is the number of input tokens Gemini served from its context cache.
# It is the number to watch: if it stays at 0 while prompt tokens are large and
# repetitive, the prompts are being re-billed in full on every call.
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


def _extract_usage(response) -> tuple:
    """Pull (prompt, output, cached) token counts out of whatever shape we get."""
    for attr in ("usage_metadata", "response_metadata"):
        meta = getattr(response, attr, None)
        if not meta:
            continue
        if isinstance(meta, dict):
            usage = meta.get("usage_metadata") or meta.get("token_usage") or meta
            if isinstance(usage, dict):
                prompt = usage.get("prompt_token_count") or usage.get("input_tokens") or 0
                output = usage.get("candidates_token_count") or usage.get("output_tokens") or 0
                cached = usage.get("cached_content_token_count") or 0
                if not cached:
                    details = usage.get("input_token_details") or {}
                    cached = details.get("cache_read", 0) if isinstance(details, dict) else 0
                if prompt or output:
                    return int(prompt), int(output), int(cached)
        else:
            prompt = getattr(meta, "prompt_token_count", 0)
            output = getattr(meta, "candidates_token_count", 0)
            cached = getattr(meta, "cached_content_token_count", 0)
            if prompt or output:
                return int(prompt), int(output), int(cached)
    return 0, 0, 0


_sdk_patch_applied = False


def install_token_capture() -> bool:
    """Legacy SDK-boundary token capture. No longer installed by default.

    Needed under langchain-google-genai 1.0.4, which dropped usage before any
    callback could see it. Since the 2.x upgrade, ChatGoogleGenerativeAI
    populates message.usage_metadata natively — including
    input_token_details.cache_read — so _make_token_callback() reads it the
    supported way and this patch would double-count if both were active.

    Kept for reference and for anyone pinned to the old stack. Call it
    explicitly only if usage_metadata is genuinely unavailable.
    """
    global _sdk_patch_applied
    if _sdk_patch_applied:
        return True
    try:
        from google.ai.generativelanguage_v1beta.services.generative_service.client import (
            GenerativeServiceClient,
        )
    except Exception as e:
        logger.warning("Token capture unavailable — SDK client not importable: %s", e)
        return False

    original = GenerativeServiceClient.generate_content

    def generate_content(self, *args, **kwargs):
        response = original(self, *args, **kwargs)
        try:
            usage = getattr(response, "usage_metadata", None)
            if usage is not None:
                TokenUsage.record(
                    int(getattr(usage, "prompt_token_count", 0) or 0),
                    int(getattr(usage, "candidates_token_count", 0) or 0),
                    int(getattr(usage, "cached_content_token_count", 0) or 0),
                )
        except Exception as e:
            logger.debug("Token telemetry unavailable: %s", e)
        return response

    GenerativeServiceClient.generate_content = generate_content
    _sdk_patch_applied = True
    logger.info("Token capture installed at the Gemini SDK boundary")
    return True


def _make_token_callback(model_name: str):
    """A callback handler that records token usage for every completed call.

    Implemented as a LangChain callback rather than by wrapping .invoke():
    ChatGoogleGenerativeAI is a Pydantic model, so assigning a new attribute
    to an instance raises "object has no field". The callback hook is the
    supported extension point and survives streaming and batched calls too.
    """
    from langchain_core.callbacks import BaseCallbackHandler

    class _TokenCallback(BaseCallbackHandler):
        def on_llm_end(self, response, **kwargs):
            try:
                prompt = output = cached = 0
                # Preferred: the message object on the first generation.
                for gen_list in getattr(response, "generations", []) or []:
                    for gen in gen_list:
                        msg = getattr(gen, "message", None)
                        if msg is not None:
                            prompt, output, cached = _extract_usage(msg)
                            if prompt or output:
                                break
                    if prompt or output:
                        break
                # Fallback: provider summary on llm_output.
                if not (prompt or output):
                    llm_output = getattr(response, "llm_output", None) or {}
                    usage = (llm_output.get("usage_metadata")
                             or llm_output.get("token_usage") or {})
                    if isinstance(usage, dict):
                        prompt = usage.get("prompt_token_count") or usage.get("input_tokens") or 0
                        output = usage.get("candidates_token_count") or usage.get("output_tokens") or 0
                        cached = usage.get("cached_content_token_count") or 0

                if prompt or output:
                    TokenUsage.record(int(prompt), int(output), int(cached))
                    logger.info(
                        "LLM usage model=%s prompt=%d output=%d cached=%d",
                        model_name, prompt, output, cached,
                    )
            except Exception as e:  # telemetry must never break a generation
                logger.debug("Token telemetry unavailable: %s", e)

    return _TokenCallback()


class LLMModel(ABC):
    @property
    @abstractmethod
    def source(self) -> str: ...
    
    @property
    @abstractmethod
    def model(self) -> str: ...
    
    @property
    @abstractmethod
    def api_key(self) -> str: ...
    
    @property
    @abstractmethod
    def temperature(self) -> float: ...
    
    @property
    @abstractmethod
    def max_tokens(self) -> int: ...
    
    @property
    @abstractmethod
    def top_p(self) -> float: ...
    
    @abstractmethod
    def to_params(self) -> Dict[str, Any]: ...
    
    @abstractmethod
    def to_langchain(self): ...


class ChatGemini(LLMModel):
    def __init__(
        self,
        model: str = "gemini-2.5-flash",
        api_key: str = "",
        temperature: float = 0.1,
        max_tokens: int = 8192,
        top_p: float = 1.0,
        timeout: float = None,
        max_retries: int = None,
    ):
        # Vertex authenticates with Application Default Credentials, so an API
        # key is only required on the AI Studio path.
        if LLM_PROVIDER == PROVIDER_AI_STUDIO and not api_key.strip():
            raise ValueError("Google API key required")
        self._model = model
        self._api_key = api_key
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._top_p = top_p
        self._timeout = timeout if timeout is not None else DEFAULT_LLM_TIMEOUT_SECONDS
        self._max_retries = max_retries if max_retries is not None else DEFAULT_LLM_MAX_RETRIES

    @property
    def source(self) -> str:
        return "ChatGemini"

    @property
    def model(self) -> str:
        return self._model

    @property
    def api_key(self) -> str:
        return self._api_key

    @property
    def temperature(self) -> float:
        return self._temperature

    @property
    def max_tokens(self) -> int:
        return self._max_tokens

    @property
    def top_p(self) -> float:
        return self._top_p

    def to_params(self) -> Dict[str, Any]:
        return {
            "model": self.model,
            "temperature": self.temperature,
            "max_output_tokens": self.max_tokens,
        }

    def to_langchain(self):
        """Build the chat model for whichever Gemini backend is configured.

        Same model family either way — the difference is the account it bills
        to and how it authenticates:

          ai_studio (default)  GOOGLE_API_KEY. Simple, but a separate wallet
                               from Google Cloud, with its own free-tier
                               request cap that GCP credits cannot raise.
          vertex_ai            Google Cloud project + Application Default
                               Credentials. Bills to the Cloud billing
                               account, so trial credits apply, and it is a
                               Google Cloud service rather than a standalone
                               API key.

        Kept switchable rather than migrated outright so the choice is a
        deployment decision, not a code change — and so a credential or quota
        problem on one path is one env var away from the other.
        """
        callbacks = [_make_token_callback(self._model)]

        if LLM_PROVIDER == PROVIDER_VERTEX:
            from langchain_google_vertexai import ChatVertexAI

            project = os.getenv("PROJECT_ID", "").strip()
            if not project:
                raise RuntimeError(
                    "LLM_PROVIDER=vertex_ai requires PROJECT_ID. Set it in .env, "
                    "or set LLM_PROVIDER=ai_studio to use GOOGLE_API_KEY instead."
                )
            return ChatVertexAI(
                model=self._model,
                project=project,
                location=os.getenv("LOCATION", "us-central1"),
                temperature=self._temperature,
                max_output_tokens=self._max_tokens,
                max_retries=self._max_retries,
                callbacks=callbacks,
            )

        from langchain_google_genai import ChatGoogleGenerativeAI
        return ChatGoogleGenerativeAI(
            model=self._model,
            google_api_key=self._api_key,
            temperature=self._temperature,
            max_output_tokens=self._max_tokens,
            timeout=self._timeout,
            max_retries=self._max_retries,
            callbacks=callbacks,
        )

    def __repr__(self) -> str:
        return f"ChatGemini(model='{self.model}', temp={self.temperature})"


# Singleton for LLMs — mode-aware with per-task temperature and model routing
class LLMSingleton:
    _instances: dict = {}
    _lock = threading.Lock()
    _cache_initialized = False

    # Temperature tuned per task type:
    # - extraction/enforcement: low temp for reliable structured JSON output
    # - synthesis: moderate temp for analytical reasoning
    # - generation: higher temp for creative writing
    MODE_TEMPERATURES = {
        "extraction":  0.1,
        "enforcement": 0.1,
        "synthesis":   0.3,
        "generation":  0.7,
    }

    # Per-mode output token limits — synthesis needs much more headroom
    # to write the full Brand Brain without truncation.
    # Extraction only produces structured JSON so 8192 is sufficient.
    MODE_MAX_TOKENS = {
        "extraction":  8192,
        "enforcement": 8192,
        "synthesis":   32768,
        "generation":  8192,
    }

    # Per-mode request timeout (seconds) — synthesis writes far more tokens
    # than the other modes and gets a longer ceiling accordingly.
    MODE_TIMEOUTS = {
        "extraction":  DEFAULT_LLM_TIMEOUT_SECONDS,
        "enforcement": DEFAULT_LLM_TIMEOUT_SECONDS,
        "synthesis":   SYNTHESIS_LLM_TIMEOUT_SECONDS,
        "generation":  DEFAULT_LLM_TIMEOUT_SECONDS,
    }

    @classmethod
    def _init_prompt_cache(cls):
        """
        Cache LLM responses in Redis, keyed on the exact prompt text plus the
        model's serialized params (model name, temperature, etc). Every call
        goes through LLMSingleton.get(...).invoke(...), so wiring the cache
        here once covers extraction, enforcement, synthesis, and generation.

        This pays off whenever the identical prompt recurs — repeated demo
        dry-runs against the same fixture documents, or a revision loop that
        re-sends an unchanged prompt — by serving the stored response instead
        of billing another Gemini call. It has no effect on calls whose prompt
        text differs (e.g. two different topics), which is the common case
        for real, non-repeated generations.

        Falls back to no caching (a warning, not a crash) if Redis is
        unreachable, so a missing cache backend never blocks generation.
        """
        if cls._cache_initialized:
            return
        cls._cache_initialized = True
        try:
            import redis as redis_lib
            from langchain_core.globals import set_llm_cache
            from langchain_community.cache import RedisCache

            redis_url = os.getenv("REDIS_URL", "redis://redis:6379/0")
            client = redis_lib.Redis.from_url(redis_url)
            client.ping()
            set_llm_cache(RedisCache(redis_=client, ttl=LLM_CACHE_TTL_SECONDS))
            logger.info(
                "LLM prompt cache enabled (Redis, ttl=%ds)", LLM_CACHE_TTL_SECONDS
            )
        except Exception as e:
            logger.warning("LLM prompt cache disabled — Redis unavailable: %s", e)

    @classmethod
    def get(cls, mode: str = "generation"):
        # Fast path — no lock needed if already created
        if mode in cls._instances:
            return cls._instances[mode]

        with cls._lock:
            # Double-check after acquiring lock
            if mode not in cls._instances:
                cls._init_prompt_cache()
                temperature = cls.MODE_TEMPERATURES.get(mode, 0.7)

                # Ensure an event loop exists for async clients initialized in synchronous threads
                import asyncio
                try:
                    asyncio.get_event_loop()
                except RuntimeError:
                    asyncio.set_event_loop(asyncio.new_event_loop())

                google_api_key = os.getenv("GOOGLE_API_KEY", "").strip()
                if not google_api_key and LLM_PROVIDER == PROVIDER_AI_STUDIO:
                    raise RuntimeError(
                        "GOOGLE_API_KEY is required when LLM_PROVIDER=ai_studio. "
                        "Set it in your .env, or set LLM_PROVIDER=vertex_ai to "
                        "authenticate with Application Default Credentials instead."
                    )

                max_tokens = cls.MODE_MAX_TOKENS.get(mode, 8192)
                timeout = cls.MODE_TIMEOUTS.get(mode, DEFAULT_LLM_TIMEOUT_SECONDS)
                model_name = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
                logger.info(
                    "LLMSingleton routing mode=%s model=%s temperature=%.1f max_tokens=%d timeout=%ds",
                    mode, model_name, temperature, max_tokens, timeout
                )
                cls._instances[mode] = ChatGemini(
                    model=model_name,
                    api_key=google_api_key,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    timeout=timeout,
                ).to_langchain()

        return cls._instances[mode]
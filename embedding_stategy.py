"""Retrieval embeddings, selected by configuration.

    EMBEDDING_PROVIDER    openai | google_genai | google_vertexai | fastembed
    EMBEDDING_MODEL       model id for that provider (a default is used if unset)
    EMBEDDING_DIMENSIONS  vector size; required for a model not in the table below
    EMBEDDING_BASE_URL    OpenAI-compatible embeddings endpoint (with openai)

When EMBEDDING_PROVIDER is unset it follows LLM_PROVIDER=vertex_ai to Vertex
embeddings. Claude and Groq have no embeddings API, so with either of those
EMBEDDING_PROVIDER must be set.

The vector table name carries the embedding dimension (see brand_rag.py), so
switching to a model with a different dimension builds a fresh index instead
of mixing vectors in one table. Documents need re-uploading to be searchable
under the new model.
"""
from __future__ import annotations

import os
from abc import ABC, abstractmethod

# pgvector's HNSW index, which brand_rag builds, cannot index more dimensions
# than this.
PGVECTOR_HNSW_MAX_DIM = 2000


class EmbeddingPort(ABC):
    """Adapter interface between the domain and LlamaIndex embedding models."""

    @property
    @abstractmethod
    def model(self) -> str: ...

    @property
    @abstractmethod
    def embed_dim(self) -> int:
        """Must match the dimension stored in PGVector."""
        ...

    @abstractmethod
    def to_llamaindex(self):
        """Return the LlamaIndex-compatible embedding model object."""
        ...


def _llamaindex_from_langchain(client, model_name: str, class_name: str):
    """Adapt a LangChain Embeddings client to LlamaIndex's interface.

    LangChain integrations already carry each provider's credential handling,
    so one adapter covers every provider instead of an extra LlamaIndex
    integration package per provider.
    """
    from llama_index.core.embeddings import BaseEmbedding

    class _Adapter(BaseEmbedding):
        @classmethod
        def class_name(cls) -> str:
            return class_name

        def _get_query_embedding(self, query: str) -> list[float]:
            return client.embed_query(query)

        def _get_text_embedding(self, text: str) -> list[float]:
            return client.embed_documents([text])[0]

        def _get_text_embeddings(self, texts: list[str]) -> list[list[float]]:
            # Batched, so indexing a document is one call rather than one per chunk.
            return client.embed_documents(list(texts))

        async def _aget_query_embedding(self, query: str) -> list[float]:
            return self._get_query_embedding(query)

        async def _aget_text_embedding(self, text: str) -> list[float]:
            return self._get_text_embedding(text)

    return _Adapter(model_name=model_name)


def _resolve_dim(provider: str, model: str, known: dict[str, int]) -> int:
    override = os.getenv("EMBEDDING_DIMENSIONS", "").strip()
    if override:
        dim = int(override)
    elif model in known:
        dim = known[model]
    else:
        raise ValueError(
            f"Unknown {provider} embedding model '{model}'. Set EMBEDDING_DIMENSIONS "
            f"to its vector size, or use one of: {list(known)}"
        )
    if dim > PGVECTOR_HNSW_MAX_DIM:
        raise ValueError(
            f"Embedding dimension {dim} exceeds pgvector's HNSW limit of "
            f"{PGVECTOR_HNSW_MAX_DIM}. Set EMBEDDING_DIMENSIONS to a smaller size "
            "if the model supports shortened vectors."
        )
    return dim


class OpenAIEmbedding(EmbeddingPort):
    """OpenAI embeddings, or any OpenAI-compatible endpoint via EMBEDDING_BASE_URL."""

    _DIM_MAP = {
        "text-embedding-3-small": 1536,
        "text-embedding-3-large": 3072,
        "text-embedding-ada-002": 1536,
    }
    DEFAULT_MODEL = "text-embedding-3-small"

    def __init__(self, model: str | None = None):
        self._model = model or os.getenv("EMBEDDING_MODEL", "").strip() or self.DEFAULT_MODEL
        self._dim = _resolve_dim("OpenAI", self._model, self._DIM_MAP)
        self._base_url = os.getenv("EMBEDDING_BASE_URL", "").strip()
        if not self._base_url and not os.getenv("OPENAI_API_KEY", "").strip():
            raise ValueError("OPENAI_API_KEY is required for OpenAI embeddings.")

    @property
    def model(self) -> str:
        return self._model

    @property
    def embed_dim(self) -> int:
        return self._dim

    def to_llamaindex(self):
        from langchain_openai import OpenAIEmbeddings

        kwargs = {"model": self._model}
        # Only the text-embedding-3 family accepts a shortened vector size.
        if self._model.startswith("text-embedding-3") and self._dim != self._DIM_MAP.get(self._model):
            kwargs["dimensions"] = self._dim
        if self._base_url:
            kwargs["base_url"] = self._base_url
            # Non-OpenAI servers expect raw strings, not OpenAI token ids.
            kwargs["check_embedding_ctx_length"] = False
        return _llamaindex_from_langchain(OpenAIEmbeddings(**kwargs), self._model, "openai")


class GoogleEmbedding(EmbeddingPort):
    """Google embedding models via the Gemini API (GOOGLE_API_KEY)."""

    _DIM_MAP = {
        "models/text-embedding-004": 768,
        "models/embedding-001": 768,
    }
    DEFAULT_MODEL = "models/text-embedding-004"

    def __init__(self, model: str | None = None, api_key: str | None = None):
        model = model or os.getenv("EMBEDDING_MODEL", "").strip() or self.DEFAULT_MODEL
        if not model.startswith("models/"):
            model = f"models/{model}"
        self._model = model
        self._dim = _resolve_dim("Google", model, self._DIM_MAP)
        self._api_key = api_key or os.getenv("GOOGLE_API_KEY", "").strip()
        if not self._api_key:
            raise ValueError("GOOGLE_API_KEY is required for Google embeddings.")

    @property
    def model(self) -> str:
        return self._model

    @property
    def embed_dim(self) -> int:
        return self._dim

    def to_llamaindex(self):
        from langchain_google_genai import GoogleGenerativeAIEmbeddings

        client = GoogleGenerativeAIEmbeddings(model=self._model, google_api_key=self._api_key)
        return _llamaindex_from_langchain(client, self._model, "google_generative_ai")


class VertexEmbedding(EmbeddingPort):
    """Google embedding models via Vertex AI (PROJECT_ID + Application Default Credentials).

    Same models and dimensions as GoogleEmbedding, so switching between the two
    does not force a re-index.
    """

    _DIM_MAP = {
        "text-embedding-004": 768,
        "text-embedding-005": 768,
    }
    DEFAULT_MODEL = "text-embedding-004"

    def __init__(self, model: str | None = None, project: str | None = None,
                 location: str | None = None):
        model = (model or os.getenv("EMBEDDING_MODEL", "").strip() or self.DEFAULT_MODEL).removeprefix("models/")
        self._model = model
        self._dim = _resolve_dim("Vertex", model, self._DIM_MAP)
        self._project = project or os.getenv("PROJECT_ID", "").strip() or os.getenv("GOOGLE_CLOUD_PROJECT", "").strip()
        self._location = location or os.getenv("LOCATION", "us-central1")
        if not self._project:
            raise ValueError("PROJECT_ID is required for Vertex embeddings.")

    @property
    def model(self) -> str:
        return self._model

    @property
    def embed_dim(self) -> int:
        return self._dim

    def to_llamaindex(self):
        from langchain_google_vertexai import VertexAIEmbeddings

        client = VertexAIEmbeddings(
            model_name=self._model, project=self._project, location=self._location
        )
        return _llamaindex_from_langchain(client, self._model, "vertex_ai")


class FastEmbedEmbedding(EmbeddingPort):
    """Local BAAI/bge models — no API key. Requires the `offline` dependency group."""

    _DIM_MAP = {
        "BAAI/bge-small-en-v1.5": 384,
        "BAAI/bge-base-en-v1.5": 768,
        "BAAI/bge-large-en-v1.5": 1024,
    }
    DEFAULT_MODEL = "BAAI/bge-small-en-v1.5"

    def __init__(self, model: str | None = None):
        self._model = model or os.getenv("EMBEDDING_MODEL", "").strip() or self.DEFAULT_MODEL
        if self._model not in self._DIM_MAP:
            raise ValueError(
                f"Unknown FastEmbed model '{self._model}'. Known models: {list(self._DIM_MAP)}"
            )

    @property
    def model(self) -> str:
        return self._model

    @property
    def embed_dim(self) -> int:
        return self._DIM_MAP[self._model]

    def to_llamaindex(self):
        from llama_index.embeddings.fastembed import FastEmbedEmbedding
        return FastEmbedEmbedding(model_name=self._model)


EMBEDDING_PROVIDERS = {
    "openai": OpenAIEmbedding,
    "google_genai": GoogleEmbedding,
    "google_vertexai": VertexEmbedding,
    "fastembed": FastEmbedEmbedding,
}

_EMBEDDING_ALIASES = {
    "ai_studio": "google_genai",
    "gemini": "google_genai",
    "google": "google_genai",
    "vertex_ai": "google_vertexai",
    "vertex": "google_vertexai",
    "offline": "fastembed",
    "local": "fastembed",
    "openai_compatible": "openai",
}


def resolve_embedding_provider(provider: str | None = None) -> str:
    explicit = (provider or os.getenv("EMBEDDING_PROVIDER", "")).strip().lower()
    if explicit:
        name = _EMBEDDING_ALIASES.get(explicit, explicit)
        if name not in EMBEDDING_PROVIDERS:
            raise ValueError(
                f"EMBEDDING_PROVIDER must be one of {sorted(EMBEDDING_PROVIDERS)}, got {explicit!r}"
            )
        return name

    from model import LLMSingleton
    llm_provider = LLMSingleton.provider_name()
    if llm_provider == "vertex_ai":
        return "google_vertexai"
    raise ValueError(
        f"LLM_PROVIDER={llm_provider!r} has no embeddings API. Set EMBEDDING_PROVIDER "
        f"to one of {sorted(EMBEDDING_PROVIDERS)}."
    )


def build_embedding(provider: str | None = None) -> EmbeddingPort:
    """Return the configured embedding backend."""
    return EMBEDDING_PROVIDERS[resolve_embedding_provider(provider)]()

"""Retrieval embeddings, with the same plug-and-adapter design as model.py.

    EmbeddingProvider   the abstract adapter every embeddings backend implements
    VertexEmbedding     Google text embeddings on Vertex AI   (LLM_PROVIDER=vertex_ai)
    VoyageEmbedding     Voyage AI, Anthropic's recommended    (LLM_PROVIDER=claude)
                        embeddings partner
    LocalEmbedding      a BAAI/bge model run in-process       (LLM_PROVIDER=groq)
    EmbeddingSingleton  picks the adapter and shares one instance per process

Groq and Anthropic have no embedding models of their own, so each LLM provider
is paired with the backend listed above. EMBEDDING_PROVIDER overrides the
pairing, e.g. Claude for writing with Vertex for retrieval.

    EMBEDDING_PROVIDER    vertex_ai | voyage | local   (default: follows LLM_PROVIDER)
    EMBEDDING_MODEL       optional; the adapter's default model otherwise
    EMBEDDING_DIMENSIONS  optional vector size, for Voyage models (256, 512, 1024)

The vector table name carries the embedding dimension (see brand_rag.py), so
switching to a backend with a different dimension builds a fresh index instead
of mixing vectors in one table. Product documents need re-uploading to be
searchable under the new backend.
"""
from __future__ import annotations

import os
import threading
from abc import ABC, abstractmethod

# pgvector's HNSW index, which brand_rag builds, cannot index more dimensions
# than this.
PGVECTOR_HNSW_MAX_DIM = 2000


def _env(name: str) -> str:
    return os.getenv(name, "").strip()


class EmbeddingProvider(ABC):
    """Adapter between the pipeline and one embeddings backend.

    Subclasses declare their name, default model, required credentials and the
    vector size of each model they know, and implement to_llamaindex(). BrandRAG
    only ever talks to this interface.
    """

    #: Value of EMBEDDING_PROVIDER that selects this adapter.
    name: str = ""
    #: Model used when EMBEDDING_MODEL is not set.
    default_model: str = ""
    #: Environment variables that must be set before this backend can run.
    required_env: tuple[str, ...] = ()
    #: Vector size of each known model.
    dimensions: dict[str, int] = {}

    def __init__(self, model: str | None = None):
        self._model = model or _env("EMBEDDING_MODEL") or self.default_model
        if self._model not in self.dimensions:
            raise ValueError(
                f"Unknown {self.name} embedding model {self._model!r}. "
                f"Known models: {sorted(self.dimensions)}"
            )
        self._embed_dim = self._resolve_dim()
        if self._embed_dim > PGVECTOR_HNSW_MAX_DIM:
            raise ValueError(
                f"Embedding dimension {self._embed_dim} exceeds pgvector's HNSW limit "
                f"of {PGVECTOR_HNSW_MAX_DIM}."
            )

    def _resolve_dim(self) -> int:
        """The model's vector size. Adapters whose models can emit other sizes override this."""
        return self.dimensions[self._model]

    @property
    def model(self) -> str:
        return self._model

    @property
    def embed_dim(self) -> int:
        """Must match the dimension stored in PGVector."""
        return self._embed_dim

    @classmethod
    def missing_env(cls) -> list[str]:
        """Required credentials that are not set, for the startup check."""
        return [var for var in cls.required_env if not _env(var)]

    @abstractmethod
    def to_llamaindex(self):
        """Return the LlamaIndex-compatible embedding model object."""
        ...

    def __repr__(self) -> str:
        return f"{type(self).__name__}(model={self.model!r}, dim={self.embed_dim})"


def _llamaindex_adapter(embed_query, embed_documents, model_name: str, class_name: str):
    """Wrap a pair of embed functions in LlamaIndex's BaseEmbedding interface."""
    from llama_index.core.embeddings import BaseEmbedding

    class _Adapter(BaseEmbedding):
        @classmethod
        def class_name(cls) -> str:
            return class_name

        def _get_query_embedding(self, query: str) -> list[float]:
            return embed_query(query)

        def _get_text_embedding(self, text: str) -> list[float]:
            return embed_documents([text])[0]

        def _get_text_embeddings(self, texts: list[str]) -> list[list[float]]:
            # Batched, so indexing a document is one call rather than one per chunk.
            return embed_documents(list(texts))

        async def _aget_query_embedding(self, query: str) -> list[float]:
            return self._get_query_embedding(query)

        async def _aget_text_embedding(self, text: str) -> list[float]:
            return self._get_text_embedding(text)

    return _Adapter(model_name=model_name)


class VertexEmbedding(EmbeddingProvider):
    """Google text embeddings on Vertex AI (PROJECT_ID + Application Default Credentials)."""

    name = "vertex_ai"
    default_model = "text-embedding-004"
    required_env = ("PROJECT_ID",)
    dimensions = {
        "text-embedding-004": 768,
        "text-embedding-005": 768,
    }

    def to_llamaindex(self):
        from langchain_google_vertexai import VertexAIEmbeddings

        client = VertexAIEmbeddings(
            model_name=self.model,
            project=_env("PROJECT_ID"),
            location=_env("LOCATION") or "us-central1",
        )
        return _llamaindex_adapter(client.embed_query, client.embed_documents, self.model, "vertex_ai")


class VoyageEmbedding(EmbeddingProvider):
    """Voyage AI embeddings (VOYAGE_API_KEY), the partner Anthropic recommends for Claude.

    Voyage embeds queries and documents differently for retrieval, so each side
    is sent with its input_type.
    """

    name = "voyage"
    default_model = "voyage-4"
    required_env = ("VOYAGE_API_KEY",)
    dimensions = {
        "voyage-4-large": 1024,
        "voyage-4": 1024,
        "voyage-4-lite": 1024,
        "voyage-3-large": 1024,
        "voyage-3.5": 1024,
        "voyage-3.5-lite": 1024,
    }
    # Every model above can also emit these sizes (2048 exceeds the HNSW limit).
    OUTPUT_DIMENSIONS = (256, 512, 1024)

    def _resolve_dim(self) -> int:
        override = _env("EMBEDDING_DIMENSIONS")
        if not override:
            return self.dimensions[self.model]
        dim = int(override)
        if dim not in self.OUTPUT_DIMENSIONS:
            raise ValueError(
                f"EMBEDDING_DIMENSIONS for Voyage must be one of {self.OUTPUT_DIMENSIONS}, got {dim}"
            )
        return dim

    def to_llamaindex(self):
        import voyageai

        client = voyageai.Client(api_key=_env("VOYAGE_API_KEY"), max_retries=3)
        model, dim = self.model, self.embed_dim

        def embed(texts: list[str], input_type: str) -> list[list[float]]:
            return client.embed(
                texts, model=model, input_type=input_type, output_dimension=dim,
            ).embeddings

        return _llamaindex_adapter(
            lambda query: embed([query], "query")[0],
            lambda texts: embed(texts, "document"),
            model,
            "voyage",
        )


class LocalEmbedding(EmbeddingProvider):
    """BAAI/bge models run in-process with FastEmbed — no API key and no network at query time.

    The model is downloaded on first use and cached in FASTEMBED_CACHE_PATH.
    """

    name = "local"
    default_model = "BAAI/bge-small-en-v1.5"
    required_env = ()
    dimensions = {
        "BAAI/bge-small-en-v1.5": 384,
        "BAAI/bge-base-en-v1.5": 768,
        "BAAI/bge-large-en-v1.5": 1024,
    }

    def to_llamaindex(self):
        from llama_index.embeddings.fastembed import FastEmbedEmbedding

        return FastEmbedEmbedding(model_name=self.model, cache_dir=_env("FASTEMBED_CACHE_PATH") or None)


class EmbeddingSingleton:
    """Selects the embeddings adapter and shares one instance per process."""

    # The plug board. Register a new EmbeddingProvider subclass here.
    PROVIDERS: dict[str, type[EmbeddingProvider]] = {
        VertexEmbedding.name: VertexEmbedding,
        VoyageEmbedding.name: VoyageEmbedding,
        LocalEmbedding.name: LocalEmbedding,
    }
    ALIASES = {
        "vertex": "vertex_ai",
        "google_vertexai": "vertex_ai",
        "voyageai": "voyage",
        "fastembed": "local",
        "offline": "local",
    }
    # The backend each LLM provider uses when EMBEDDING_PROVIDER is unset.
    LLM_PAIRING = {
        "vertex_ai": VertexEmbedding.name,
        "claude": VoyageEmbedding.name,
        "groq": LocalEmbedding.name,
    }

    _instance: EmbeddingProvider | None = None
    _lock = threading.Lock()

    @classmethod
    def provider_name(cls) -> str:
        raw = _env("EMBEDDING_PROVIDER").lower()
        if not raw:
            from model import LLMSingleton
            return cls.LLM_PAIRING[LLMSingleton.provider_name()]
        name = cls.ALIASES.get(raw, raw)
        if name not in cls.PROVIDERS:
            raise ValueError(
                f"EMBEDDING_PROVIDER must be one of {sorted(cls.PROVIDERS)}, got {raw!r}"
            )
        return name

    @classmethod
    def provider_class(cls) -> type[EmbeddingProvider]:
        return cls.PROVIDERS[cls.provider_name()]

    @classmethod
    def get(cls) -> EmbeddingProvider:
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls.provider_class()()
        return cls._instance

from __future__ import annotations

import hashlib
import logging
import os
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from urllib.parse import urlparse

from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

logger = logging.getLogger(__name__)



class EmbeddingPort(ABC):
    """Adapter interface between your domain and LlamaIndex embedding models."""

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




class GoogleEmbedding(EmbeddingPort):
    """Google's own embedding models, via the Gemini API.

    This is the default. Retrieval quality is not the reason — the reason is
    that every model in the pipeline should be Google's. A locally-run
    third-party encoder (see FastEmbedEmbedding below) is cheap and private,
    but it is still a non-Google model sitting in the middle of the retrieval
    path, which is exactly the kind of dependency that has to be justified when
    a deployment is required to use Google AI tooling only.

    Uses the same GOOGLE_API_KEY as generation, so there is no second
    credential to manage. Embedding calls are billed like any other API call,
    unlike the local encoder which was free after download — that is the real
    trade being made here.
    """

    _DIM_MAP: dict[str, int] = {
        "models/text-embedding-004": 768,
        "models/embedding-001": 768,
    }
    DEFAULT_MODEL = "models/text-embedding-004"

    def __init__(self, model: str | None = None, api_key: str | None = None):
        model = model or os.getenv("EMBEDDING_MODEL", self.DEFAULT_MODEL)
        if not model.startswith("models/"):
            model = f"models/{model}"
        if model not in self._DIM_MAP:
            raise ValueError(
                f"Unknown Google embedding model '{model}'. "
                f"Known models: {list(self._DIM_MAP)}"
            )
        self._model = model
        self._api_key = api_key or os.getenv("GOOGLE_API_KEY", "").strip()
        if not self._api_key:
            raise ValueError(
                "GOOGLE_API_KEY is required for Google embeddings. Set it in .env."
            )

    @property
    def model(self) -> str:
        return self._model

    @property
    def embed_dim(self) -> int:
        return self._DIM_MAP[self._model]

    def to_llamaindex(self):
        """Adapt LangChain's Google embeddings to LlamaIndex's interface.

        Written here rather than pulling in llama-index-embeddings-google-genai
        because langchain-google-genai is already a dependency and already
        carries the credential handling — adding another integration package
        for three method bodies would widen the dependency tree for nothing.
        """
        from langchain_google_genai import GoogleGenerativeAIEmbeddings
        from llama_index.core.embeddings import BaseEmbedding

        client = GoogleGenerativeAIEmbeddings(
            model=self._model, google_api_key=self._api_key
        )
        model_name = self._model

        class _GoogleLlamaIndexEmbedding(BaseEmbedding):
            @classmethod
            def class_name(cls) -> str:
                return "google_generative_ai"

            def _get_query_embedding(self, query: str) -> list[float]:
                return client.embed_query(query)

            def _get_text_embedding(self, text: str) -> list[float]:
                return client.embed_documents([text])[0]

            def _get_text_embeddings(self, texts: list[str]) -> list[list[float]]:
                # Batched, so indexing a document is one call rather than one
                # call per chunk.
                return client.embed_documents(list(texts))

            async def _aget_query_embedding(self, query: str) -> list[float]:
                return self._get_query_embedding(query)

            async def _aget_text_embedding(self, text: str) -> list[float]:
                return self._get_text_embedding(text)

        return _GoogleLlamaIndexEmbedding(model_name=model_name)


class VertexEmbedding(EmbeddingPort):
    """The same Google embedding models, served through Vertex AI.

    Identical model and identical 768 dimensions to GoogleEmbedding, so the two
    are interchangeable against an existing vector table — switching provider
    does not force a re-index. What changes is billing and auth: Vertex runs on
    a Google Cloud project with Application Default Credentials, so it draws on
    Cloud billing (including trial credits) rather than an AI Studio key.
    """

    _DIM_MAP: dict[str, int] = {
        "text-embedding-004": 768,
        "text-embedding-005": 768,
    }
    DEFAULT_MODEL = "text-embedding-004"

    def __init__(self, model: str | None = None, project: str | None = None,
                 location: str | None = None):
        model = (model or os.getenv("EMBEDDING_MODEL", self.DEFAULT_MODEL)).removeprefix("models/")
        if model not in self._DIM_MAP:
            raise ValueError(
                f"Unknown Vertex embedding model '{model}'. "
                f"Known models: {list(self._DIM_MAP)}"
            )
        self._model = model
        self._project = project or os.getenv("PROJECT_ID", "").strip()
        self._location = location or os.getenv("LOCATION", "us-central1")
        if not self._project:
            raise ValueError(
                "PROJECT_ID is required for Vertex embeddings. Set it in .env, "
                "or set EMBEDDING_PROVIDER=ai_studio to use GOOGLE_API_KEY."
            )

    @property
    def model(self) -> str:
        return self._model

    @property
    def embed_dim(self) -> int:
        return self._DIM_MAP[self._model]

    def to_llamaindex(self):
        from langchain_google_vertexai import VertexAIEmbeddings
        from llama_index.core.embeddings import BaseEmbedding

        client = VertexAIEmbeddings(
            model_name=self._model, project=self._project, location=self._location
        )
        model_name = self._model

        class _VertexLlamaIndexEmbedding(BaseEmbedding):
            @classmethod
            def class_name(cls) -> str:
                return "vertex_ai"

            def _get_query_embedding(self, query: str) -> list[float]:
                return client.embed_query(query)

            def _get_text_embedding(self, text: str) -> list[float]:
                return client.embed_documents([text])[0]

            def _get_text_embeddings(self, texts: list[str]) -> list[list[float]]:
                return client.embed_documents(list(texts))

            async def _aget_query_embedding(self, query: str) -> list[float]:
                return self._get_query_embedding(query)

            async def _aget_text_embedding(self, text: str) -> list[float]:
                return self._get_text_embedding(text)

        return _VertexLlamaIndexEmbedding(model_name=model_name)


def build_embedding(provider: str | None = None) -> EmbeddingPort:
    """Return the configured embedding backend.

    Defaults to EMBEDDING_PROVIDER, falling back to LLM_PROVIDER so a single
    switch moves generation and retrieval together — which is almost always
    what is wanted, since a split leaves two different accounts being billed
    for one request.

      ai_studio (default)  Gemini API via GOOGLE_API_KEY
      vertex_ai            Vertex AI via Application Default Credentials
      offline              local third-party encoder; requires the `offline`
                           dependency group and is not a Google model
    """
    provider = (
        provider
        or os.getenv("EMBEDDING_PROVIDER")
        or os.getenv("LLM_PROVIDER")
        or "ai_studio"
    ).strip().lower()

    if provider == "vertex_ai":
        return VertexEmbedding()
    if provider == "offline":
        return FastEmbedEmbedding()
    if provider == "ai_studio":
        return GoogleEmbedding()
    raise ValueError(
        f"EMBEDDING_PROVIDER must be 'ai_studio', 'vertex_ai' or 'offline', got {provider!r}"
    )


class FastEmbedEmbedding(EmbeddingPort):
    """BAAI/bge-small-en-v1.5 → 384 dims (local, no API key required).

    Retained as an offline/no-cost fallback, but NOT the default: it is a
    third-party model, and deployments restricted to Google AI tooling cannot
    use it. Selecting it is a deliberate choice, not something to fall into.
    """

    _DIM_MAP: dict[str, int] = {
        "BAAI/bge-small-en-v1.5": 384,
        "BAAI/bge-base-en-v1.5": 768,
        "BAAI/bge-large-en-v1.5": 1024,
    }

    def __init__(self, model: str = "BAAI/bge-small-en-v1.5"):
        if model not in self._DIM_MAP:
            raise ValueError(
                f"Unknown FastEmbed model '{model}'. "
                f"Known models: {list(self._DIM_MAP)}"
            )
        self._model = model

    @property
    def model(self) -> str:
        return self._model

    @property
    def embed_dim(self) -> int:
        return self._DIM_MAP[self._model]

    def to_llamaindex(self):
        from llama_index.embeddings.fastembed import FastEmbedEmbedding
        return FastEmbedEmbedding(model_name=self._model)

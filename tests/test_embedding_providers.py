"""The embeddings adapters and the switch in EmbeddingSingleton.

Groq and Anthropic have no embedding models, so each LLM provider is paired
with a backend: Vertex with Vertex, Claude with Voyage AI, Groq with a local
model. These tests pin that pairing and each adapter's contract down without a
network call: provider clients are replaced by fakes that record what they
were asked.
"""
import sys
import types

import pytest

from embedding_stategy import (
    EmbeddingProvider,
    EmbeddingSingleton,
    LocalEmbedding,
    VertexEmbedding,
    VoyageEmbedding,
)

EMBEDDING_ENV = ["LLM_PROVIDER", "EMBEDDING_PROVIDER", "EMBEDDING_MODEL", "EMBEDDING_DIMENSIONS"]


@pytest.fixture
def env(monkeypatch):
    """A clean environment, a fresh singleton, and a setter for provider variables."""
    monkeypatch.setattr(EmbeddingSingleton, "_instance", None)

    def configure(**values):
        for var in EMBEDDING_ENV:
            monkeypatch.delenv(var, raising=False)
        for key, value in values.items():
            monkeypatch.setenv(key, value)

    configure()
    return configure


class TestAdapterContract:
    def test_base_class_is_abstract(self):
        with pytest.raises(TypeError):
            EmbeddingProvider()

    @pytest.mark.parametrize("adapter", [VertexEmbedding, VoyageEmbedding, LocalEmbedding])
    def test_every_adapter_declares_itself(self, adapter):
        assert issubclass(adapter, EmbeddingProvider)
        assert adapter.name and adapter.default_model in adapter.dimensions

    def test_registry_matches_adapter_names(self):
        assert set(EmbeddingSingleton.PROVIDERS) == {"vertex_ai", "voyage", "local"}
        for name, adapter in EmbeddingSingleton.PROVIDERS.items():
            assert adapter.name == name

    def test_unknown_model_is_a_clear_error(self, env):
        env(EMBEDDING_MODEL="text-embedding-3-small")
        with pytest.raises(ValueError, match="Unknown vertex_ai embedding model"):
            VertexEmbedding()

    def test_missing_env(self, env, monkeypatch):
        monkeypatch.delenv("VOYAGE_API_KEY", raising=False)
        assert VoyageEmbedding.missing_env() == ["VOYAGE_API_KEY"]
        assert LocalEmbedding.missing_env() == []


class TestSwitch:
    @pytest.mark.parametrize("llm, adapter", [
        ("vertex_ai", VertexEmbedding),
        ("claude", VoyageEmbedding),
        ("groq", LocalEmbedding),
    ])
    def test_follows_the_llm_provider(self, env, llm, adapter):
        env(LLM_PROVIDER=llm)
        assert EmbeddingSingleton.provider_class() is adapter

    def test_defaults_to_vertex_like_the_llm(self, env):
        assert EmbeddingSingleton.provider_class() is VertexEmbedding

    @pytest.mark.parametrize("value, adapter", [
        ("vertex_ai", VertexEmbedding),
        ("google_vertexai", VertexEmbedding),
        ("voyage", VoyageEmbedding),
        ("fastembed", LocalEmbedding),
        ("offline", LocalEmbedding),
    ])
    def test_explicit_choice_overrides_the_pairing(self, env, value, adapter):
        env(LLM_PROVIDER="claude", EMBEDDING_PROVIDER=value)
        assert EmbeddingSingleton.provider_class() is adapter

    def test_unregistered_backend_is_a_clear_error(self, env):
        env(EMBEDDING_PROVIDER="openai")
        with pytest.raises(ValueError, match="EMBEDDING_PROVIDER must be one of"):
            EmbeddingSingleton.provider_class()

    def test_get_shares_one_instance(self, env):
        env(LLM_PROVIDER="groq")
        first = EmbeddingSingleton.get()
        assert first is EmbeddingSingleton.get()
        assert (first.model, first.embed_dim) == ("BAAI/bge-small-en-v1.5", 384)


class TestVoyage:
    def test_dimensions(self, env):
        assert VoyageEmbedding().embed_dim == 1024
        env(EMBEDDING_DIMENSIONS="512")
        assert VoyageEmbedding().embed_dim == 512
        env(EMBEDDING_DIMENSIONS="2048")
        with pytest.raises(ValueError, match="must be one of"):
            VoyageEmbedding()

    def test_queries_and_documents_use_their_input_type(self, env, monkeypatch):
        calls = []

        class _Client:
            def __init__(self, api_key=None, max_retries=0, **kwargs):
                pass

            def embed(self, texts, model=None, input_type=None, output_dimension=None, **kwargs):
                calls.append((list(texts), model, input_type, output_dimension))
                return types.SimpleNamespace(embeddings=[[0.1] * output_dimension for _ in texts])

        monkeypatch.setitem(sys.modules, "voyageai", types.SimpleNamespace(Client=_Client))
        env(EMBEDDING_DIMENSIONS="256")
        embed_model = VoyageEmbedding().to_llamaindex()

        assert len(embed_model.get_query_embedding("launch plan")) == 256
        embed_model.get_text_embedding_batch(["doc one", "doc two"])
        assert calls[0] == (["launch plan"], "voyage-4", "query", 256)
        assert calls[1] == (["doc one", "doc two"], "voyage-4", "document", 256)


class TestVertex:
    def test_builds_vertex_embeddings_for_the_project(self, env, monkeypatch):
        created = {}

        class _VertexAIEmbeddings:
            def __init__(self, **kwargs):
                created.update(kwargs)

            def embed_query(self, text):
                return [0.2] * 768

            def embed_documents(self, texts):
                return [[0.2] * 768 for _ in texts]

        import langchain_google_vertexai
        monkeypatch.setattr(langchain_google_vertexai, "VertexAIEmbeddings", _VertexAIEmbeddings)
        monkeypatch.setenv("PROJECT_ID", "test-project")
        monkeypatch.setenv("LOCATION", "europe-west4")

        embed_model = VertexEmbedding().to_llamaindex()
        assert created == {"model_name": "text-embedding-004", "project": "test-project",
                           "location": "europe-west4"}
        assert len(embed_model.get_query_embedding("q")) == 768

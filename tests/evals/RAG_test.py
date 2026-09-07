# tests/test_brand_rag.py
import hashlib
import pytest
from unittest.mock import MagicMock, patch
from brand_rag import BrandRAG, _IndexEntry


@pytest.fixture(autouse=True)
def clear_rag_cache():
    """Clear class-level cache before every test — prevents bleed between runs."""
    BrandRAG._cache.clear()
    yield
    BrandRAG._cache.clear()


@pytest.fixture
def mock_embedding():
    embedding = MagicMock()
    embedding.model = "test-model"
    embedding.embed_dim = 384
    embedding.to_llamaindex.return_value = MagicMock()
    return embedding


@pytest.fixture
def rag(mock_embedding):
    with patch.dict("os.environ", {"POSTGRES_URI": "postgresql://test:test@localhost/test"}):
        return BrandRAG(
            business_id="test-business-123",
            content_type="blog",
            embedding=mock_embedding,
        )


@pytest.fixture
def mock_index_entry(rag):
    """
    Injects a pre-built fake index into the cache
    so _build (which hits Postgres) is never called.
    """
    mock_index = MagicMock()
    entry = _IndexEntry(index=mock_index, doc_hashes=set())
    BrandRAG._cache[rag._cache_key()] = entry
    return entry

def test_refresh_new_document_is_indexed(rag, mock_index_entry):
    # Arrange
    doc = "At Vantage Creative, we've worked with over 200 brands."
    expected_hash = hashlib.md5(doc.encode()).hexdigest()

    # Act
    rag.refresh(doc)

    # Assert — hash recorded
    assert expected_hash in mock_index_entry.doc_hashes
    # Assert — document inserted into index
    mock_index_entry.index.insert.assert_called_once()

def test_refresh_duplicate_document_is_skipped(rag, mock_index_entry):
    # Arrange — pre-load the hash as if document was already indexed
    doc = "At Vantage Creative, we've worked with over 200 brands."
    existing_hash = hashlib.md5(doc.encode()).hexdigest()
    mock_index_entry.doc_hashes.add(existing_hash)

    # Act
    rag.refresh(doc)

    # Assert — index.insert was never called
    mock_index_entry.index.insert.assert_not_called()

def test_refresh_full_clears_cache(rag, mock_index_entry):
    # Arrange — confirm cache has our entry
    assert rag._cache_key() in BrandRAG._cache

    # Act — full refresh (no document passed)
    rag.refresh()

    # Assert — cache entry is gone
    assert rag._cache_key() not in BrandRAG._cache

def test_different_businesses_have_isolated_caches(mock_embedding):
    with patch.dict("os.environ", {"POSTGRES_URI": "postgresql://test:test@localhost/test"}):
        rag_a = BrandRAG("business-A", "blog", mock_embedding)
        rag_b = BrandRAG("business-B", "blog", mock_embedding)

    doc = "Same document content for both businesses."
    doc_hash = hashlib.md5(doc.encode()).hexdigest()

    # Inject entries for both
    entry_a = _IndexEntry(index=MagicMock(), doc_hashes=set())
    entry_b = _IndexEntry(index=MagicMock(), doc_hashes=set())
    BrandRAG._cache[rag_a._cache_key()] = entry_a
    BrandRAG._cache[rag_b._cache_key()] = entry_b

    # Add doc to A only
    rag_a.refresh(doc)

    # Assert A has the hash, B does not
    assert doc_hash in entry_a.doc_hashes
    assert doc_hash not in entry_b.doc_hashes
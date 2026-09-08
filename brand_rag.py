import hashlib
import logging
import os
import threading
import time
from contextlib import closing
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

from embedding_stategy import EmbeddingPort
from chunking_stategy import get_chunking_strategy

@dataclass
class _IndexEntry:
    index: object
    doc_hashes: set[str] = field(default_factory=set)
    built_at: float = field(default_factory=time.monotonic)





class BrandRAG:
    """
    Retrieval-augmented generation scoped to a single (business_id, content_type).

    Thread-safe: uses a class-level lock so concurrent requests don't race
    on index construction, and never mutates global LlamaIndex Settings.

    Refresh strategy:
    - refresh(new_doc_content) → incremental, embeds only the new document
    - refresh()                → full rebuild, clears cache entirely
    """

    _cache: dict[tuple, _IndexEntry] = {}
    _cache_lock = threading.Lock()

    def __init__(
        self,
        business_id: str,
        content_type: str,
        embedding: EmbeddingPort,
        index_ttl_seconds: int = 3600,
        similarity_top_k: int = 5,
    ):
        self.business_id = business_id
        self.content_type = content_type
        self.embedding = embedding
        self._llamaindex_embed = None
        self.index_ttl_seconds = index_ttl_seconds
        self.similarity_top_k = similarity_top_k
        self.chunking_strategy = get_chunking_strategy(content_type)
        self._postgres_uri = self._require_env("POSTGRES_URI")



    def query(self, topic: str) -> str:
        """Retrieve brand voice examples — prioritizes stylistic quality over topic match."""
        if not topic.strip():
            raise ValueError("Query topic must not be empty.")

        index = self._get_index().index

        # Primary: topic-relevant retrieval
        topic_retriever = index.as_retriever(similarity_top_k=self.similarity_top_k)
        topic_results = self._retrieve_with_retry(topic_retriever, topic)

        # Secondary: voice-quality retrieval — fetch best examples regardless of topic
        voice_query = "brand voice storytelling authentic narrative example"
        voice_retriever = index.as_retriever(similarity_top_k=2)
        voice_results = self._retrieve_with_retry(voice_retriever, voice_query)

        # Merge — deduplicate by text, topic results first
        seen = set()
        merged = []
        for r in topic_results + voice_results:
            key = r.text[:100]
            if key not in seen:
                seen.add(key)
                merged.append(r)

        if not merged:
            logger.warning(
                "No results for business_id=%s content_type=%s topic=%r",
                self.business_id, self.content_type, topic,
            )
            return ""

        logger.info(
            "Retrieved %d node(s) for business_id=%s content_type=%s topic=%r",
            len(merged), self.business_id, self.content_type, topic,
        )
        return "\n\n".join(r.text for r in merged)

    def query_structure(self, section_type: str, topic: str) -> str:
        """Retrieve brand voice examples specifically for a structural section (e.g. 'opening', 'risk')."""
        if not topic.strip() or not section_type.strip():
            raise ValueError("Query topic and section_type must not be empty.")

        index = self._get_index().index

        # Primary: targeted structural retrieval
        structural_query = f"{section_type} section paragraph regarding {topic}"
        structural_retriever = index.as_retriever(similarity_top_k=self.similarity_top_k)
        structural_results = self._retrieve_with_retry(structural_retriever, structural_query)

        if not structural_results:
            logger.warning(
                "No structural results for business_id=%s content_type=%s section=%r topic=%r",
                self.business_id, self.content_type, section_type, topic,
            )
            return ""

        logger.info(
            "Retrieved %d structural node(s) for business_id=%s content_type=%s section=%r topic=%r",
            len(structural_results), self.business_id, self.content_type, section_type, topic,
        )
        return "\n\n".join(r.text for r in structural_results)


    def refresh(self, new_doc_content: str = None) -> None:
        """
        Incremental refresh: only embeds and inserts the new document.
        Full refresh: clears cache entirely, rebuilds on next query.
        """
        if new_doc_content is None:
            # Full rebuild
            with self._cache_lock:
                self._cache.pop(self._cache_key(), None)
            logger.info(
                "Full cache cleared for business_id=%s content_type=%s",
                self.business_id, self.content_type,
            )
            return

        # Incremental — hash check first
        doc_hash = hashlib.md5(new_doc_content.encode()).hexdigest()

        with self._cache_lock:
            entry = self._cache.get(self._cache_key())

            if entry is not None and doc_hash in entry.doc_hashes:
                logger.info(
                    "Document already indexed for business_id=%s content_type=%s — skipping.",
                    self.business_id, self.content_type,
                )
                return

        from llama_index.core import Document
        index_entry = self._get_index()

        # _get_index() may have just built the index from the database, and the
        # upload that triggered this refresh is already a row there — so that
        # build already embedded this document. Inserting it again would store a
        # second copy of every one of its chunks. This check was previously done
        # only against a cold cache, before the build.
        if doc_hash in index_entry.doc_hashes:
            logger.info(
                "Document already present in the rebuilt index for "
                "business_id=%s content_type=%s — skipping insert.",
                self.business_id, self.content_type,
            )
            return

        # Embed and insert only the new document
        index_entry.index.insert(Document(text=new_doc_content))
        index_entry.doc_hashes.add(doc_hash)

        # The insert is written straight through to pgvector, so the record of
        # what the table holds has to grow with it. Without this the next build
        # would see a mismatch and re-embed every document to reach a state the
        # table is already in.
        self._record_indexed_hashes(index_entry.doc_hashes)

        logger.info(
            "Incremental refresh complete for business_id=%s content_type=%s",
            self.business_id, self.content_type,
        )



    def _cache_key(self) -> tuple:
        return (
            self.business_id,
            self.content_type,
            self.embedding.model,
            self.chunking_strategy.__class__.__name__,
        )

    def _get_index(self) -> _IndexEntry:
        key = self._cache_key()

        with self._cache_lock:
            entry = self._cache.get(key)
            if entry is not None:
                age = time.monotonic() - entry.built_at
                if self.index_ttl_seconds == 0 or age < self.index_ttl_seconds:
                    return entry
                logger.info(
                    "Index TTL expired (%.0fs) for business_id=%s content_type=%s — rebuilding.",
                    age, self.business_id, self.content_type,
                )

            # Track hashes of all docs going into the index. They are handed to
            # _build so it can tell an index that only needs loading from one
            # that needs re-embedding.
            docs, doc_hashes = self._load_docs()
            index = self._build(docs, doc_hashes)
            entry = _IndexEntry(index=index, doc_hashes=doc_hashes)
            self._cache[key] = entry
            return entry
        
    
    def _sanitize_table_name(self, name: str) -> str:
        import re
        # Keep only alphanumeric and underscore
        return re.sub(r'[^a-z0-9_]', '_', name.lower())
        
    @property
    def llamaindex_embed(self):
        if self._llamaindex_embed is None:
            self._llamaindex_embed = self.embedding.to_llamaindex()
        return self._llamaindex_embed

    def _vector_store(self):
        """The pgvector store backing this (business_id, content_type).

        One table per business, content type and embedding dimension, so a
        business's vectors are physically separated from every other tenant's
        and an embedding-model change cannot mix dimensions in one table.
        """
        from llama_index.vector_stores.postgres import PGVectorStore

        parsed = urlparse(self._postgres_uri)
        return PGVectorStore.from_params(
            host=parsed.hostname,
            port=parsed.port or 5432,
            database=parsed.path.lstrip("/"),
            user=parsed.username,
            password=parsed.password,
            table_name = f"vectors_{self._sanitize_table_name(self.business_id)}_{self._sanitize_table_name(self.content_type)}_{self.embedding.embed_dim}",
            embed_dim=self.embedding.embed_dim,
            hybrid_search=True,
            hnsw_kwargs={
                "hnsw_m": 16,
                "hnsw_ef_construction": 64,
                "hnsw_ef_search": 40,
                "hnsw_dist_method": "vector_cosine_ops",
            },
        )

    def purge(self) -> None:
        """Delete every embedded chunk for this (business_id, content_type).

        Dropping a row from brand_documents does not touch the vectors that
        were derived from it, so without this a "deleted" document keeps being
        retrieved and keeps steering generation. Called on the delete path;
        the next query rebuilds the index from whatever documents remain.
        """
        self._vector_store().clear()
        with self._cache_lock:
            self._cache.pop(self._cache_key(), None)

        # Drop the record of what was indexed too. A stale record describing a
        # table that has just been emptied is exactly the state that would let
        # a later build skip re-embedding and serve an empty index.
        try:
            from brand_metrics import _get_redis_client

            _get_redis_client().delete(self._indexed_hashes_key())
        except Exception as e:
            logger.warning("Could not clear indexed-hash record: %s", e)

        logger.info(
            "Purged vector store for business_id=%s content_type=%s",
            self.business_id, self.content_type,
        )

    def _indexed_hashes_key(self) -> str:
        """Redis key recording which documents the persisted table holds.

        Keyed by everything that changes the vectors — the business, the
        content type, the embedding model and the chunking strategy — so a
        model or chunking change is never mistaken for an up-to-date index.
        """
        b, c, model, chunking = self._cache_key()
        return f"rag_indexed:{b}:{c}:{model}:{chunking}"

    @retry(
        retry=retry_if_exception_type(Exception),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True,
    )
    def _build(self, docs, doc_hashes=None):
        """Build (or load) the VectorStoreIndex. Retried up to 3 times."""
        from llama_index.core import StorageContext, VectorStoreIndex
        from llama_index.core.node_parser import SimpleNodeParser

        node_parser = SimpleNodeParser.from_defaults(
            chunk_size=self.chunking_strategy.chunk_size,
            chunk_overlap=self.chunking_strategy.chunk_overlap,
            )

        vector_store = self._vector_store()

        # VectorStoreIndex.from_documents() takes a storage_context, NOT a
        # vector_store. The previous code passed vector_store=..., which
        # from_documents() accepts into **kwargs and ignores, so every index
        # was built in the default in-memory SimpleVectorStore: embeddings
        # were computed, held in one worker process, and dropped when that
        # process rebuilt or restarted. The pgvector table was created and
        # then stayed permanently empty.
        storage_context = StorageContext.from_defaults(vector_store=vector_store)

        if not docs:
            logger.info(
                "No source documents for business_id=%s content_type=%s; "
                "loading existing vector store.",
                self.business_id, self.content_type,
            )
            return VectorStoreIndex.from_vector_store(
                vector_store=vector_store,
                embed_model=self.llamaindex_embed,
            )

        # Now that vectors actually persist, an index can often be loaded
        # instead of rebuilt. Re-embedding every document on each process
        # start and each TTL expiry was the single largest avoidable cost in
        # the pipeline; this skips it whenever the stored table already holds
        # exactly the documents the database currently has.
        if doc_hashes and self._persisted_index_is_current(doc_hashes):
            logger.info(
                "Vector store already current for business_id=%s content_type=%s "
                "(%d document(s)) — loading without re-embedding.",
                self.business_id, self.content_type, len(doc_hashes),
            )
            return VectorStoreIndex.from_vector_store(
                vector_store=vector_store,
                embed_model=self.llamaindex_embed,
            )

        logger.info(
            "Indexing %d document(s) for business_id=%s content_type=%s "
            "(chunking_strategy=%s, embed_dim=%d).",
            len(docs), self.business_id, self.content_type,
            self.chunking_strategy.__class__.__name__, self.embedding.embed_dim,
        )

        # A rebuild is a full snapshot of the documents currently in the
        # database, so the table is cleared first — otherwise from_documents()
        # would append a second copy of every chunk, and a deleted document's
        # chunks would survive here even though its row is gone.
        #
        # Two processes rebuilding at once each clear-then-insert, so the
        # settled state is still exactly one copy; only a query landing inside
        # that window sees a partial index.
        vector_store.clear()

        index = VectorStoreIndex.from_documents(
            docs,
            storage_context=storage_context,
            embed_model=self.llamaindex_embed,
            transformations=[node_parser],
            show_progress=True,
        )

        if doc_hashes:
            self._record_indexed_hashes(doc_hashes)

        return index

    def _persisted_index_is_current(self, doc_hashes: set[str]) -> bool:
        """True when the stored table holds exactly these documents.

        Both conditions matter. The recorded hash set says the right documents
        were indexed; the row count says the table was not since cleared or
        dropped. Any Redis or database problem answers False, which only costs
        a rebuild.
        """
        try:
            from brand_metrics import _get_redis_client

            recorded = _get_redis_client().smembers(self._indexed_hashes_key())
            if set(recorded) != set(doc_hashes):
                return False

            return self._stored_row_count() > 0
        except Exception as e:
            logger.warning(
                "Could not check persisted index for business_id=%s content_type=%s "
                "(%s) — rebuilding.", self.business_id, self.content_type, e,
            )
            return False

    def _record_indexed_hashes(self, doc_hashes: set[str]) -> None:
        """Record which documents the freshly built table holds."""
        try:
            from brand_metrics import _get_redis_client

            client = _get_redis_client()
            key = self._indexed_hashes_key()
            pipe = client.pipeline()
            pipe.delete(key)
            pipe.sadd(key, *doc_hashes)
            pipe.execute()
        except Exception as e:
            # A missing record only means the next build re-embeds. The index
            # itself is already written, so this must not fail the build.
            logger.warning(
                "Could not record indexed hashes for business_id=%s content_type=%s: %s",
                self.business_id, self.content_type, e,
            )

    def _stored_row_count(self) -> int:
        """Number of embedded chunks currently in this business's table."""
        import psycopg2

        table = (
            f"data_vectors_{self._sanitize_table_name(self.business_id)}"
            f"_{self._sanitize_table_name(self.content_type)}_{self.embedding.embed_dim}"
        )
        # The table name is assembled from sanitised components — the
        # sanitiser reduces anything outside [a-z0-9_] to an underscore — so
        # it cannot carry SQL. Identifiers cannot be bound as parameters.
        #
        # closing() is explicit because psycopg2's connection context manager
        # ends the transaction but leaves the connection open, which would leak
        # one per call from a long-lived worker.
        with closing(psycopg2.connect(self._postgres_uri)) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT EXISTS (SELECT 1 FROM information_schema.tables "
                    "WHERE table_name = %s)", (table,),
                )
                if not cur.fetchone()[0]:
                    return 0
                cur.execute(f'SELECT count(*) FROM "{table}"')
                return cur.fetchone()[0]

  

    @staticmethod
    def pdf_bytes_to_markdown(pdf_bytes: bytes) -> str:
        import tempfile
        from pymupdf4llm import to_markdown

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            f.write(pdf_bytes)
            temp_path = f.name

        try:
            return to_markdown(temp_path)
        finally:
            os.unlink(temp_path)
    
    def _load_docs(self) -> tuple[list, set[str]]:
        """Documents to index for retrieval: reference documents only.

        The two document roles feed two different channels, and this is the
        second one.

        A voice document teaches the Brand Brain how this writer writes — the
        rhythm, the mechanics, the shapes it reaches for. It is not a source of
        text. Give a director's scripts to the system and ask for a script on
        another subject, and nothing in those scripts belongs in the output;
        only the way they move does.

        A reference document supplies facts, and facts are what retrieval is
        for. Along with Parallel's web search, it is the only channel the writer
        may take words from.

        Indexing voice documents here collapsed the distinction. They were
        retrieved as research and handed to the writer under a header reading
        "SOURCE MATERIAL — THE BRAND'S OWN DOCUMENTS (AUTHORITATIVE)", so the
        style references were presented as the authoritative fact source and the
        writer reproduced them. Every copying violation seen in testing traces
        back to that.

        With no reference documents the index is empty and the researcher says
        so, leaving the writer the topic and whatever web search returns. That
        is the correct outcome, not a degradation: a brand that has uploaded
        only voice references has told the system how to sound and given it
        nothing to say.
        """
        from llama_index.core import Document
        from database import get_db_session, BrandDocument, DOC_ROLE_REFERENCE

        with get_db_session() as session:
            rows = (
                session.query(BrandDocument)
                .filter_by(
                    business_id=self.business_id,
                    content_type=self.content_type,
                    doc_role=DOC_ROLE_REFERENCE,
                )
                .filter(BrandDocument.file_content.isnot(None))
                .all()
            )

            if not rows:
                logger.info(
                    "No reference documents for business_id=%s content_type=%s — "
                    "retrieval has no facts to serve. Voice documents are not "
                    "indexed: they shape the Brand Brain, not the content.",
                    self.business_id, self.content_type,
                )
                return [], set()

            # Extract content while session is still open to avoid DetachedInstanceError
            docs = [Document(text=row.file_content) for row in rows]
            doc_hashes = {
                hashlib.md5(row.file_content.encode()).hexdigest()
                for row in rows
            }

        return docs, doc_hashes

    @staticmethod
    @retry(
        retry=retry_if_exception_type(Exception),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True,
    )
    def _retrieve_with_retry(retriever, topic: str):
        return retriever.retrieve(topic)

    @staticmethod
    def _require_env(name: str) -> str:
        value = os.getenv(name)
        if not value:
            raise EnvironmentError(
                f"Required environment variable '{name}' is not set."
            )
        return value
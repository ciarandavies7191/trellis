from __future__ import annotations
import asyncio
import logging
from typing import Optional
from trellis.models.document import DocumentHandle
from .models import ChunkPipelineState, ChunkMetadata
from .chunker import StructuralChunker
from .metadata import MetadataExtractor
from .bm25_index import DocumentBM25Index
from .fact_store import StructuredFactStore, FactExtractor
from .vector_index import NumpyVectorIndex, embed_texts, EMBEDDING_MODEL
from .plugins import RetrievalRegistry

logger = logging.getLogger(__name__)


class ChunkPipeline:
    def __init__(self, registry: RetrievalRegistry):
        self._registry = registry
        self._chunker = StructuralChunker()
        self._metadata_extractor = MetadataExtractor()
        self._fact_extractor = FactExtractor()
        self.fact_store: StructuredFactStore = StructuredFactStore()
        self.bm25_indexes: dict[str, DocumentBM25Index] = {}
        self.vector_index: NumpyVectorIndex = NumpyVectorIndex()
        self.chunk_lookup: dict[str, ChunkMetadata] = {}

    async def run(
        self,
        handle: DocumentHandle,
        tenant_id: str,
        document_id: str,
        background: bool = True,
    ) -> ChunkPipelineState:
        state = ChunkPipelineState(document_id=document_id, tenant_id=tenant_id)

        try:
            chunks = self._chunker.chunk(handle, document_id, tenant_id)
            state.chunking_complete = True

            self._metadata_extractor.extract(chunks, self._registry)
            state.metadata_complete = True

            try:
                bm25_idx = DocumentBM25Index(chunks, self._registry.vocabulary)
                self.bm25_indexes[document_id] = bm25_idx
                state.bm25_complete = True
            except ImportError as e:
                logger.warning("BM25 indexing skipped: %s", e)
                state.bm25_complete = False

            facts = self._fact_extractor.extract(chunks, self._registry)
            self.fact_store.upsert(facts)
            state.fact_extraction_complete = True

            for chunk in chunks:
                self.chunk_lookup[chunk.chunk_id] = chunk

            if background:
                asyncio.create_task(
                    self._embed_and_index(chunks, document_id, state)
                )
            else:
                await self._embed_and_index(chunks, document_id, state)

        except Exception as e:
            state.error = str(e)
            logger.exception("ChunkPipeline failed for document %r: %s", document_id, e)

        return state

    def run_sync(
        self,
        handle: DocumentHandle,
        tenant_id: str,
        document_id: str,
    ) -> ChunkPipelineState:
        """Run steps 1-4 synchronously; schedule step 5 (embedding) on the running loop.

        Called from sync tool execute() methods that run inside a thread executor
        while an event loop is already active. Uses run_coroutine_threadsafe to
        schedule the async embedding step without blocking the caller.
        """
        state = ChunkPipelineState(document_id=document_id, tenant_id=tenant_id)
        try:
            chunks = self._chunker.chunk(handle, document_id, tenant_id)
            state.chunking_complete = True

            self._metadata_extractor.extract(chunks, self._registry)
            state.metadata_complete = True

            try:
                bm25_idx = DocumentBM25Index(chunks, self._registry.vocabulary)
                self.bm25_indexes[document_id] = bm25_idx
                state.bm25_complete = True
            except ImportError as e:
                logger.warning("BM25 indexing skipped: %s", e)

            facts = self._fact_extractor.extract(chunks, self._registry)
            self.fact_store.upsert(facts)
            state.fact_extraction_complete = True

            for chunk in chunks:
                self.chunk_lookup[chunk.chunk_id] = chunk

            # Schedule async embedding on the running loop (best-effort; no-op if no loop).
            try:
                loop = asyncio.get_running_loop()
                asyncio.run_coroutine_threadsafe(
                    self._embed_and_index(chunks, document_id, state), loop
                )
            except RuntimeError:
                pass  # No running loop — embedding skipped; BM25-only mode.

        except Exception as e:
            state.error = str(e)
            logger.exception("ChunkPipeline.run_sync failed for document %r: %s", document_id, e)

        return state

    async def _embed_and_index(
        self,
        chunks: list[ChunkMetadata],
        document_id: str,
        state: ChunkPipelineState,
    ) -> None:
        try:
            texts = [c.text for c in chunks]
            chunk_ids = [c.chunk_id for c in chunks]
            if not texts:
                state.embedding_complete = True
                return
            embeddings = await embed_texts(texts)
            self.vector_index.upsert(chunk_ids, embeddings)
            for chunk, emb in zip(chunks, embeddings.tolist()):
                chunk.embedding = emb
            state.embedding_complete = True
        except Exception as e:
            logger.warning("Embedding failed for document %r: %s", document_id, e)
            state.embedding_complete = False

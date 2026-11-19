"""Load -> chunk -> embed -> upsert."""

from __future__ import annotations

import logging
from pathlib import Path

from opspilot.embeddings import Embedder
from opspilot.ingestion.chunker import chunk_document
from opspilot.ingestion.loaders import Document, load_bytes, load_directory
from opspilot.metrics import DOCUMENTS_INDEXED
from opspilot.schemas import IngestResult
from opspilot.vectorstore import KnowledgeStore

log = logging.getLogger(__name__)

_BATCH = 64


class IngestionPipeline:
    def __init__(
        self,
        embedder: Embedder,
        store: KnowledgeStore,
        chunk_size: int = 1200,
        chunk_overlap: int = 150,
    ):
        self.embedder = embedder
        self.store = store
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap

    def ingest_documents(self, docs: list[Document]) -> IngestResult:
        total = 0
        for doc in docs:
            chunks = chunk_document(doc, self.chunk_size, self.chunk_overlap)
            if not chunks:
                continue
            # Remove the old version first, otherwise a document that got shorter
            # would leave orphaned chunks behind.
            self.store.delete_source(doc.source)
            for start in range(0, len(chunks), _BATCH):
                batch = chunks[start : start + _BATCH]
                texts = [c.embedding_text() for c in batch]
                self.store.upsert(
                    batch,
                    self.embedder.embed_documents(texts),
                    self.embedder.sparse_documents(texts),
                )
            total += len(chunks)
            log.info("Indexed %-55s %3d chunks", doc.source, len(chunks))

        DOCUMENTS_INDEXED.set(self.store.count())
        return IngestResult(files=len(docs), chunks=total, sources=[d.source for d in docs])

    def ingest_directory(self, directory: str | Path, recreate: bool = False) -> IngestResult:
        if recreate:
            self.store.recreate()
        else:
            self.store.ensure_collection()
        return self.ingest_documents(load_directory(Path(directory)))

    def ingest_upload(
        self,
        data: bytes,
        filename: str,
        doc_type: str | None = None,
        service: str | None = None,
    ) -> IngestResult:
        self.store.ensure_collection()
        doc = load_bytes(data, filename, doc_type=doc_type, service=service)
        return self.ingest_documents([doc])

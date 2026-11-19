"""Hybrid retrieval followed by cross-encoder reranking."""

from __future__ import annotations

import logging
import time

from opspilot.embeddings import Embedder, Reranker
from opspilot.metrics import RETRIEVAL_LATENCY
from opspilot.schemas import RetrievedChunk, SearchFilters
from opspilot.vectorstore import KnowledgeStore

log = logging.getLogger(__name__)


class HybridRetriever:
    def __init__(
        self,
        embedder: Embedder,
        store: KnowledgeStore,
        reranker: Reranker | None,
        candidate_k: int = 20,
        top_k: int = 5,
    ):
        self.embedder = embedder
        self.store = store
        self.reranker = reranker
        self.candidate_k = candidate_k
        self.top_k = top_k

    def search(self, query: str, filters: SearchFilters | None = None) -> list[RetrievedChunk]:
        started = time.perf_counter()

        candidates = self.store.hybrid_search(
            dense=self.embedder.embed_query(query),
            sparse=self.embedder.sparse_query(query),
            filters=filters,
            limit=self.candidate_k,
        )

        if self.reranker and candidates:
            scores = self.reranker.score(query, [c.embedding_text() for c in candidates])
            for chunk, score in zip(candidates, scores, strict=True):
                chunk.rerank_score = round(score, 4)
            candidates.sort(key=lambda c: c.rerank_score or 0.0, reverse=True)

        results = candidates[: self.top_k]
        RETRIEVAL_LATENCY.observe(time.perf_counter() - started)
        log.debug("Retrieved %s for %r", [c.source for c in results], query)
        return results

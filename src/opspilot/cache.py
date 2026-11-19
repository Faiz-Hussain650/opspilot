"""Semantic cache.

A normal cache only hits on the *exact* same string. During an incident five
people ask the same thing five different ways ("checkout is throwing 502s",
"checkout 502 errors", ...). We embed the question and look for a previous
question with cosine similarity above a threshold (0.92 by default).

Only grounded answers are cached - we never want to replay a bad answer.
"""

from __future__ import annotations

import json
import logging
import time
import uuid

from opspilot.embeddings import Embedder
from opspilot.metrics import CACHE_LOOKUPS
from opspilot.schemas import ChatResponse, SearchFilters
from opspilot.vectorstore import CacheStore

log = logging.getLogger(__name__)


class SemanticCache:
    def __init__(
        self,
        embedder: Embedder,
        store: CacheStore,
        threshold: float = 0.92,
        ttl_hours: int = 24,
    ):
        self.embedder = embedder
        self.store = store
        self.threshold = threshold
        self.ttl_seconds = ttl_hours * 3600

    def get(self, question: str, filters: SearchFilters) -> ChatResponse | None:
        try:
            hit = self.store.lookup(
                self.embedder.embed_query(question),
                filters.cache_key(),
                self.threshold,
                self.ttl_seconds,
            )
        except Exception as exc:  # the cache must never break a request
            log.warning("Cache lookup failed: %s", exc)
            hit = None

        if hit is None:
            CACHE_LOOKUPS.labels(result="miss").inc()
            return None

        score, payload = hit
        CACHE_LOOKUPS.labels(result="hit").inc()
        log.info("Cache hit (similarity %.3f) for %r", score, question)
        response = ChatResponse.model_validate(json.loads(payload["response"]))
        response.cached = True
        response.steps = [f"cache: hit (similarity {score:.2f})"]
        return response

    def put(self, question: str, filters: SearchFilters, response: ChatResponse) -> None:
        if not response.grounded or response.blocked or response.escalate:
            return
        try:
            self.store.store(
                point_id=str(uuid.uuid4()),
                vector=self.embedder.embed_query(question),
                payload={
                    "question": question,
                    "filters_key": filters.cache_key(),
                    "created_at": time.time(),
                    "response": response.model_dump_json(),
                },
            )
        except Exception as exc:
            log.warning("Cache write failed: %s", exc)

    def clear(self) -> None:
        self.store.clear()

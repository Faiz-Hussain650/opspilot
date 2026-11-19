"""Everything that talks to Qdrant lives here.

The knowledge-base collection has two named vectors per point:
  "dense"  - 384-d semantic embedding (cosine)
  "sparse" - BM25 term weights, with Qdrant applying IDF at query time

A hybrid query runs both searches and merges the two ranked lists with
Reciprocal Rank Fusion (RRF) inside Qdrant, so we get one round trip.
"""

from __future__ import annotations

import logging
import time

from qdrant_client import QdrantClient, models

from opspilot.config import Settings
from opspilot.embeddings import SparseVector
from opspilot.schemas import Chunk, RetrievedChunk, SearchFilters

log = logging.getLogger(__name__)

DENSE = "dense"
SPARSE = "sparse"
_KEYWORD_FIELDS = ("service", "doc_type", "source")


def build_client(settings: Settings) -> QdrantClient:
    api_key = settings.qdrant_api_key.get_secret_value() if settings.qdrant_api_key else None
    return QdrantClient(url=settings.qdrant_url, api_key=api_key, timeout=30)


def _to_filter(filters: SearchFilters | None) -> models.Filter | None:
    if filters is None or filters.is_empty():
        return None
    must = []
    if filters.service:
        must.append(
            models.FieldCondition(key="service", match=models.MatchValue(value=filters.service))
        )
    if filters.doc_type:
        must.append(
            models.FieldCondition(key="doc_type", match=models.MatchValue(value=filters.doc_type))
        )
    return models.Filter(must=must)


class KnowledgeStore:
    def __init__(self, client: QdrantClient, collection: str, dimension: int):
        self.client = client
        self.collection = collection
        self.dimension = dimension

    # ------------------------------------------------------------------ setup
    def ensure_collection(self) -> None:
        if self.client.collection_exists(self.collection):
            return
        log.info("Creating collection %s", self.collection)
        self.client.create_collection(
            collection_name=self.collection,
            vectors_config={
                DENSE: models.VectorParams(size=self.dimension, distance=models.Distance.COSINE)
            },
            sparse_vectors_config={SPARSE: models.SparseVectorParams(modifier=models.Modifier.IDF)},
        )
        # Payload indexes make filtering (and faceting) fast.
        for field in _KEYWORD_FIELDS:
            self.client.create_payload_index(
                collection_name=self.collection,
                field_name=field,
                field_schema=models.PayloadSchemaType.KEYWORD,
            )

    def recreate(self) -> None:
        if self.client.collection_exists(self.collection):
            self.client.delete_collection(self.collection)
        self.ensure_collection()

    # ------------------------------------------------------------------ write
    def delete_source(self, source: str) -> None:
        self.client.delete(
            collection_name=self.collection,
            points_selector=models.FilterSelector(
                filter=models.Filter(
                    must=[
                        models.FieldCondition(key="source", match=models.MatchValue(value=source))
                    ]
                )
            ),
        )

    def upsert(
        self,
        chunks: list[Chunk],
        dense: list[list[float]],
        sparse: list[SparseVector],
    ) -> None:
        points = [
            models.PointStruct(
                id=chunk.id,
                vector={
                    DENSE: d,
                    SPARSE: models.SparseVector(indices=s.indices, values=s.values),
                },
                payload=chunk.model_dump(exclude={"id"}),
            )
            for chunk, d, s in zip(chunks, dense, sparse, strict=True)
        ]
        self.client.upsert(collection_name=self.collection, points=points, wait=True)

    # ------------------------------------------------------------------- read
    def hybrid_search(
        self,
        dense: list[float],
        sparse: SparseVector,
        filters: SearchFilters | None,
        limit: int,
    ) -> list[RetrievedChunk]:
        query_filter = _to_filter(filters)
        response = self.client.query_points(
            collection_name=self.collection,
            prefetch=[
                models.Prefetch(query=dense, using=DENSE, limit=limit, filter=query_filter),
                models.Prefetch(
                    query=models.SparseVector(indices=sparse.indices, values=sparse.values),
                    using=SPARSE,
                    limit=limit,
                    filter=query_filter,
                ),
            ],
            query=models.FusionQuery(fusion=models.Fusion.RRF),
            limit=limit,
            with_payload=True,
        )
        return [
            RetrievedChunk(id=str(p.id), score=p.score, **(p.payload or {}))
            for p in response.points
        ]

    def count(self) -> int:
        if not self.client.collection_exists(self.collection):
            return 0
        return self.client.count(self.collection, exact=True).count

    def facet_values(self, key: str) -> list[str]:
        try:
            result = self.client.facet(collection_name=self.collection, key=key, limit=100)
            return sorted(str(hit.value) for hit in result.hits)
        except Exception as exc:  # older servers without the facet API
            log.debug("Facet on %s failed: %s", key, exc)
            return []

    def ping(self) -> bool:
        try:
            self.client.get_collections()
            return True
        except Exception:
            return False


class CacheStore:
    """A tiny dense-only collection used by the semantic cache."""

    def __init__(self, client: QdrantClient, collection: str, dimension: int):
        self.client = client
        self.collection = collection
        self.dimension = dimension

    def ensure_collection(self) -> None:
        if self.client.collection_exists(self.collection):
            return
        self.client.create_collection(
            collection_name=self.collection,
            vectors_config=models.VectorParams(
                size=self.dimension, distance=models.Distance.COSINE
            ),
        )
        self.client.create_payload_index(
            collection_name=self.collection,
            field_name="filters_key",
            field_schema=models.PayloadSchemaType.KEYWORD,
        )
        self.client.create_payload_index(
            collection_name=self.collection,
            field_name="created_at",
            field_schema=models.PayloadSchemaType.FLOAT,
        )

    def lookup(self, vector: list[float], filters_key: str, threshold: float, max_age_s: int):
        response = self.client.query_points(
            collection_name=self.collection,
            query=vector,
            limit=1,
            score_threshold=threshold,
            with_payload=True,
            query_filter=models.Filter(
                must=[
                    models.FieldCondition(
                        key="filters_key", match=models.MatchValue(value=filters_key)
                    ),
                    models.FieldCondition(
                        key="created_at", range=models.Range(gte=time.time() - max_age_s)
                    ),
                ]
            ),
        )
        if not response.points:
            return None
        hit = response.points[0]
        return hit.score, hit.payload

    def store(self, point_id: str, vector: list[float], payload: dict) -> None:
        self.client.upsert(
            collection_name=self.collection,
            points=[models.PointStruct(id=point_id, vector=vector, payload=payload)],
        )

    def clear(self) -> None:
        if self.client.collection_exists(self.collection):
            self.client.delete_collection(self.collection)
        self.ensure_collection()

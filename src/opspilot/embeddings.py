"""Dense + sparse embeddings and the cross-encoder reranker, all via FastEmbed.

Why FastEmbed instead of sentence-transformers or Ollama embeddings?
  * ONNX runtime only - no PyTorch, so Docker images stay small;
  * runs the same on a Mac, in a Linux container and on Streamlit Cloud;
  * Qdrant maintains it, so the BM25 sparse vectors match what Qdrant expects.

Models are downloaded from Hugging Face the first time and cached in
MODELS_CACHE_DIR (a Docker volume in compose, so it only happens once).
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass

from opspilot.config import Settings

log = logging.getLogger(__name__)


@dataclass
class SparseVector:
    indices: list[int]
    values: list[float]


class Embedder:
    def __init__(self, settings: Settings):
        from fastembed import SparseTextEmbedding, TextEmbedding

        cache = settings.models_cache_dir
        log.info("Loading dense model %s", settings.dense_model)
        self._dense = TextEmbedding(model_name=settings.dense_model, cache_dir=cache)
        log.info("Loading sparse model %s", settings.sparse_model)
        self._sparse = SparseTextEmbedding(model_name=settings.sparse_model, cache_dir=cache)
        # ONNX sessions are thread-safe for inference, but we embed from worker
        # threads (asyncio.to_thread) and a lock keeps memory spikes predictable.
        self._lock = threading.Lock()
        self.dimension = len(self.embed_query("dimension probe"))

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        with self._lock:
            return [vec.tolist() for vec in self._dense.embed(texts, batch_size=32)]

    def embed_query(self, text: str) -> list[float]:
        with self._lock:
            return next(iter(self._dense.query_embed(text))).tolist()

    def sparse_documents(self, texts: list[str]) -> list[SparseVector]:
        with self._lock:
            return [
                SparseVector(indices=e.indices.tolist(), values=e.values.tolist())
                for e in self._sparse.embed(texts, batch_size=32)
            ]

    def sparse_query(self, text: str) -> SparseVector:
        with self._lock:
            e = next(iter(self._sparse.query_embed(text)))
            return SparseVector(indices=e.indices.tolist(), values=e.values.tolist())


class Reranker:
    """Cross-encoder: reads query and passage *together*, so it is much more
    precise than comparing two independent embeddings - but too slow to run on
    the whole collection. We only run it on the top candidates."""

    def __init__(self, settings: Settings):
        from fastembed.rerank.cross_encoder import TextCrossEncoder

        log.info("Loading reranker %s", settings.rerank_model)
        self._model = TextCrossEncoder(
            model_name=settings.rerank_model, cache_dir=settings.models_cache_dir
        )
        self._lock = threading.Lock()

    def score(self, query: str, passages: list[str]) -> list[float]:
        if not passages:
            return []
        with self._lock:
            return [float(s) for s in self._model.rerank(query, passages)]

"""Build and wire every component once (a tiny hand-rolled DI container)."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from opspilot.agent.graph import build_agent_graph
from opspilot.agent.nodes import AgentNodes
from opspilot.cache import SemanticCache
from opspilot.config import Settings, get_settings
from opspilot.embeddings import Embedder, Reranker
from opspilot.ingestion.pipeline import IngestionPipeline
from opspilot.llm import build_chat_model
from opspilot.metrics import DOCUMENTS_INDEXED
from opspilot.retrieval import HybridRetriever
from opspilot.service import ChatService
from opspilot.vectorstore import CacheStore, KnowledgeStore, build_client

log = logging.getLogger(__name__)


@dataclass
class Container:
    settings: Settings
    embedder: Embedder
    store: KnowledgeStore
    retriever: HybridRetriever
    cache: SemanticCache
    ingestion: IngestionPipeline
    chat: ChatService


def build_container(settings: Settings | None = None) -> Container:
    settings = settings or get_settings()
    log.info("Starting OpsPilot with %s", settings.public_summary())

    embedder = Embedder(settings)
    reranker = Reranker(settings) if settings.rerank_enabled else None

    client = build_client(settings)
    store = KnowledgeStore(client, settings.collection_name, embedder.dimension)
    cache_store = CacheStore(client, settings.cache_collection_name, embedder.dimension)

    retriever = HybridRetriever(
        embedder, store, reranker, candidate_k=settings.candidate_k, top_k=settings.top_k
    )
    cache = SemanticCache(
        embedder,
        cache_store,
        threshold=settings.cache_similarity_threshold,
        ttl_hours=settings.cache_ttl_hours,
    )
    ingestion = IngestionPipeline(
        embedder, store, chunk_size=settings.chunk_size, chunk_overlap=settings.chunk_overlap
    )

    nodes = AgentNodes(retriever, build_chat_model(settings))
    graph = build_agent_graph(nodes, max_rewrites=settings.max_query_rewrites)
    chat = ChatService(graph, cache, settings)

    return Container(settings, embedder, store, retriever, cache, ingestion, chat)


def bootstrap(container: Container) -> None:
    """Create collections and load the bundled knowledge base on first start."""
    settings = container.settings
    container.store.ensure_collection()
    container.cache.store.ensure_collection()

    existing = container.store.count()
    if existing == 0 and settings.auto_ingest:
        log.info("Knowledge base is empty, ingesting %s", settings.knowledge_base_dir)
        result = container.ingestion.ingest_directory(settings.knowledge_base_dir)
        log.info("Ingested %d files / %d chunks", result.files, result.chunks)
    else:
        log.info("Knowledge base has %d chunks", existing)
        DOCUMENTS_INDEXED.set(existing)

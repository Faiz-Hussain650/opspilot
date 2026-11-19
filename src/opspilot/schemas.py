"""Data shapes shared by ingestion, retrieval, the agent and the API."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

DocType = Literal["runbook", "postmortem", "ticket", "other"]


class Chunk(BaseModel):
    """A piece of a document, ready to be embedded and stored."""

    id: str
    text: str
    source: str  # file name, e.g. runbook-redis-memory.md
    title: str
    section: str = ""
    doc_type: str = "other"
    service: str = "general"
    severity: str | None = None
    tags: list[str] = Field(default_factory=list)
    chunk_index: int = 0

    def embedding_text(self) -> str:
        # Prepending the title and section ("contextual chunk headers") gives the
        # embedding model context that a bare paragraph would otherwise lose.
        header = self.title if not self.section else f"{self.title} > {self.section}"
        return f"{header}\n\n{self.text}"


class RetrievedChunk(Chunk):
    score: float = 0.0
    rerank_score: float | None = None


class SearchFilters(BaseModel):
    service: str | None = None
    doc_type: str | None = None

    def is_empty(self) -> bool:
        return not (self.service or self.doc_type)

    def cache_key(self) -> str:
        return f"service={self.service or '*'}|doc_type={self.doc_type or '*'}"


class ChatRequest(BaseModel):
    question: str = Field(min_length=3, max_length=4000)
    service: str | None = None
    doc_type: str | None = None
    use_cache: bool = True
    # Return the full text of each source (the evaluation judge needs it).
    include_context: bool = False

    @property
    def filters(self) -> SearchFilters:
        return SearchFilters(service=self.service or None, doc_type=self.doc_type or None)


class Source(BaseModel):
    ref: int  # the [n] number used in the answer
    source: str
    title: str
    section: str
    doc_type: str
    service: str
    snippet: str
    rerank_score: float | None = None
    cited: bool = False
    text: str | None = None


class ChatResponse(BaseModel):
    answer: str
    sources: list[Source] = Field(default_factory=list)
    grounded: bool = False
    confidence: float = 0.0
    escalate: bool = False
    # Statements the fact-check could not find in the sources.
    unsupported_claims: list[str] = Field(default_factory=list)
    blocked: bool = False
    cached: bool = False
    rewritten_query: str | None = None
    retrieved: list[str] = Field(default_factory=list)  # every source the retriever returned
    steps: list[str] = Field(default_factory=list)
    latency_ms: int = 0


class IngestResult(BaseModel):
    files: int
    chunks: int
    sources: list[str]


class StatsResponse(BaseModel):
    collection: str
    chunks: int
    services: list[str]
    doc_types: list[str]
    llm_provider: str
    llm_model: str
    mode: str

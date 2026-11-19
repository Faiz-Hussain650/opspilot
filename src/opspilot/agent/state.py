"""The state that flows through the LangGraph agent."""

from __future__ import annotations

import operator
from typing import Annotated, TypedDict

from opspilot.schemas import RetrievedChunk, SearchFilters


class AgentState(TypedDict, total=False):
    question: str
    filters: SearchFilters
    search_query: str
    queries_tried: list[str]
    rewrites: int

    documents: list[RetrievedChunk]  # what the retriever returned last time
    relevant: list[RetrievedChunk]  # what the grader kept
    # Every source seen across all retrieval attempts (used by the evaluation).
    retrieved_sources: Annotated[list[str], operator.add]

    answer: str
    grounded: bool
    confidence: float
    unsupported_claims: list[str]
    escalate: bool
    blocked: bool

    # A human-readable trace. The reducer appends instead of overwriting.
    steps: Annotated[list[str], operator.add]

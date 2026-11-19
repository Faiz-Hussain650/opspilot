"""Prometheus metrics. Grafana's OpsPilot dashboard is built on these names."""

from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

_LATENCY_BUCKETS = (0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 20, 30, 60, 120)

REQUESTS = Counter(
    "opspilot_http_requests_total",
    "HTTP requests by route and status code",
    ["method", "route", "status"],
)
REQUEST_LATENCY = Histogram(
    "opspilot_http_request_duration_seconds",
    "HTTP request latency",
    ["route"],
    buckets=_LATENCY_BUCKETS,
)
CHAT_LATENCY = Histogram(
    "opspilot_chat_duration_seconds",
    "End-to-end time to answer a question (including streaming)",
    ["cached"],  # true | false
    buckets=_LATENCY_BUCKETS,
)
RETRIEVAL_LATENCY = Histogram(
    "opspilot_retrieval_duration_seconds",
    "Hybrid search + rerank latency",
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2, 5),
)
LLM_LATENCY = Histogram(
    "opspilot_llm_duration_seconds",
    "Latency of each LLM call, by agent step",
    ["step"],
    buckets=_LATENCY_BUCKETS,
)
CACHE_LOOKUPS = Counter(
    "opspilot_cache_lookups_total",
    "Semantic cache lookups",
    ["result"],  # hit | miss
)
QUERY_REWRITES = Counter(
    "opspilot_query_rewrites_total",
    "Times the agent rewrote a query because retrieval looked irrelevant",
)
ANSWERS = Counter(
    "opspilot_answers_total",
    "Answers produced, labelled by grounding outcome",
    ["grounded"],  # true | false
)
ESCALATIONS = Counter(
    "opspilot_escalations_total",
    "Answers flagged for human escalation",
)
GUARDRAIL_BLOCKS = Counter(
    "opspilot_guardrail_blocks_total",
    "Requests blocked or answers redacted by guardrails",
    ["kind"],  # input_injection | output_redaction
)
CONFIDENCE = Histogram(
    "opspilot_answer_confidence",
    "Self-reported confidence of the grounding check",
    buckets=(0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0),
)
DOCUMENTS_INDEXED = Gauge(
    "opspilot_indexed_chunks",
    "Number of chunks currently in the knowledge base",
)

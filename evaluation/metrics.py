"""Retrieval and aggregate metrics. Pure Python so they're easy to test.

hit@k   - did ANY expected document show up in what the retriever returned?
recall  - what fraction of the expected documents showed up?
MRR     - 1 / rank of the first expected document (rewards putting it at the top).
"""

from __future__ import annotations

import math
from statistics import mean


def hit_at_k(retrieved: list[str], expected: list[str]) -> float:
    return 1.0 if set(retrieved) & set(expected) else 0.0


def recall(retrieved: list[str], expected: list[str]) -> float:
    if not expected:
        return 1.0
    return len(set(retrieved) & set(expected)) / len(set(expected))


def reciprocal_rank(retrieved: list[str], expected: list[str]) -> float:
    for rank, source in enumerate(retrieved, start=1):
        if source in expected:
            return 1.0 / rank
    return 0.0


def percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    k = (len(ordered) - 1) * pct / 100
    lo, hi = math.floor(k), math.ceil(k)
    if lo == hi:
        return ordered[int(k)]
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (k - lo)


def safe_mean(values: list[float]) -> float | None:
    """Mean that returns None (not 0) when there's nothing to average, so a
    metric with no samples doesn't look like a failure."""
    values = [v for v in values if v is not None]
    return round(mean(values), 4) if values else None


def aggregate(rows: list[dict]) -> dict:
    """Summarise per-question rows produced by run_eval.py."""
    in_scope = [r for r in rows if r["expected_sources"]]
    judged = [r for r in rows if r.get("judge")]
    behaviour = [r for r in rows if r.get("behaviour_pass") is not None]
    latencies = [r["latency_ms"] for r in rows if not r.get("blocked")]

    return {
        "questions": len(rows),
        "hit_rate": safe_mean([r["hit"] for r in in_scope]),
        "recall": safe_mean([r["recall"] for r in in_scope]),
        "mrr": safe_mean([r["mrr"] for r in in_scope]),
        "faithfulness": safe_mean([r["judge"]["faithfulness"] for r in judged]),
        "answer_relevancy": safe_mean([r["judge"]["relevancy"] for r in judged]),
        "correctness": safe_mean([r["judge"]["correctness"] for r in judged]),
        "self_reported_grounded_rate": safe_mean([1.0 if r["grounded"] else 0.0 for r in in_scope]),
        "behaviour_accuracy": safe_mean([1.0 if r["behaviour_pass"] else 0.0 for r in behaviour]),
        "rewrite_rate": safe_mean([1.0 if r["rewritten"] else 0.0 for r in in_scope]),
        "latency_p50_ms": round(percentile(latencies, 50)),
        "latency_p95_ms": round(percentile(latencies, 95)),
    }

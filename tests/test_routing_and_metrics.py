from __future__ import annotations

import json
from pathlib import Path

import pytest

from evaluation.metrics import aggregate, hit_at_k, percentile, recall, reciprocal_rank
from opspilot.agent.routing import (
    needs_escalation,
    normalise_citations,
    parse_citations,
    route_after_grading,
    route_after_guard,
)

# ---------------------------------------------------------------- agent routing


def test_guard_routing():
    assert route_after_guard({"blocked": True}) == "blocked"
    assert route_after_guard({"blocked": False}) == "continue"


def test_grading_routes_to_generate_when_relevant_docs_exist():
    assert route_after_grading({"relevant": ["doc"], "rewrites": 0}, max_rewrites=2) == "generate"


def test_grading_rewrites_until_budget_is_spent():
    assert route_after_grading({"relevant": [], "rewrites": 0}, max_rewrites=2) == "rewrite"
    assert route_after_grading({"relevant": [], "rewrites": 1}, max_rewrites=2) == "rewrite"
    assert route_after_grading({"relevant": [], "rewrites": 2}, max_rewrites=2) == "give_up"


def test_parse_citations():
    answer = "Restart PgBouncer [1]. Cancel queries [2][3]. Not a citation: [abc] or [100]."
    assert parse_citations(answer) == {1, 2, 3}
    assert parse_citations("") == set()


def test_full_width_citations_are_normalised():
    answer = "Cancel the queries 【1】. Roll back 【2†L3-L9】 and check 【 3 】."
    fixed = normalise_citations(answer)
    assert fixed == "Cancel the queries [1]. Roll back [2] and check [3]."
    assert parse_citations(fixed) == {1, 2, 3}


@pytest.mark.parametrize(
    "grounded,confidence,expected",
    [(True, 0.9, False), (True, 0.3, True), (False, 0.95, True)],
)
def test_escalation_rule(grounded, confidence, expected):
    assert needs_escalation(grounded, confidence) is expected


# ---------------------------------------------------------------- eval metrics


def test_retrieval_metrics():
    retrieved = ["a.md", "b.md", "c.md"]
    assert hit_at_k(retrieved, ["c.md"]) == 1.0
    assert hit_at_k(retrieved, ["z.md"]) == 0.0
    assert recall(retrieved, ["a.md", "z.md"]) == 0.5
    assert reciprocal_rank(retrieved, ["b.md"]) == 0.5
    assert reciprocal_rank(retrieved, ["z.md"]) == 0.0


def test_percentile():
    assert percentile([1, 2, 3, 4, 5], 50) == 3
    assert percentile([], 95) == 0.0
    assert percentile([10, 20], 50) == 15


def test_aggregate_skips_missing_metrics():
    rows = [
        {
            "expected_sources": ["a.md"],
            "hit": 1.0,
            "recall": 1.0,
            "mrr": 1.0,
            "grounded": True,
            "rewritten": False,
            "latency_ms": 1000,
            "blocked": False,
            "behaviour_pass": None,
            "judge": {"faithfulness": 1.0, "relevancy": 0.8, "correctness": 0.6},
        },
        {
            "expected_sources": [],
            "hit": None,
            "recall": None,
            "mrr": None,
            "grounded": False,
            "rewritten": False,
            "latency_ms": 0,
            "blocked": True,
            "behaviour_pass": True,
            "judge": None,
        },
    ]
    summary = aggregate(rows)
    assert summary["hit_rate"] == 1.0
    assert summary["correctness"] == 0.6
    assert summary["behaviour_accuracy"] == 1.0
    assert summary["latency_p50_ms"] == 1000

    no_behaviour = aggregate(rows[:1])
    assert no_behaviour["behaviour_accuracy"] is None


# ---------------------------------------------------------------- dataset sanity


def test_golden_dataset_points_at_real_documents(kb_dir: Path):
    dataset = Path(__file__).resolve().parents[1] / "evaluation" / "golden_dataset.jsonl"
    known = {p.name for p in kb_dir.rglob("*.md")}
    ids = set()
    for line in dataset.read_text().splitlines():
        item = json.loads(line)
        assert item["id"] not in ids
        ids.add(item["id"])
        assert item["reference_answer"]
        for source in item["expected_sources"]:
            assert source in known, f"{item['id']} expects missing file {source}"

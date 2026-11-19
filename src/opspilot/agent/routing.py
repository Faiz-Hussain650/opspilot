"""Decision functions for the graph's conditional edges.

Kept separate (and free of LangGraph imports) so they're trivial to unit test.
"""

from __future__ import annotations

import re

_CITATION = re.compile(r"\[(\d{1,2})\]")


def route_after_guard(state: dict) -> str:
    return "blocked" if state.get("blocked") else "continue"


def route_after_grading(state: dict, max_rewrites: int) -> str:
    if state.get("relevant"):
        return "generate"
    if state.get("rewrites", 0) < max_rewrites:
        return "rewrite"
    return "give_up"


def parse_citations(answer: str) -> set[int]:
    """Return the passage numbers cited in an answer, e.g. "... [1][3]" -> {1, 3}."""
    return {int(n) for n in _CITATION.findall(answer or "")}


def needs_escalation(grounded: bool, confidence: float, threshold: float = 0.5) -> bool:
    return (not grounded) or confidence < threshold

"""Decision functions for the graph's conditional edges.

Kept separate (and free of LangGraph imports) so they're trivial to unit test.
"""

from __future__ import annotations

import re

_CITATION = re.compile(r"\[(\d{1,2})\]")
# Some models (gpt-oss in particular) cite with full-width brackets, sometimes with a
# trailing marker: 【1】 or 【1†L3-L5】. We rewrite those to the plain [1] form.
_ODD_CITATION = re.compile(r"【\s*(\d{1,2})(?:†[^】]*)?\s*】")


def route_after_guard(state: dict) -> str:
    return "blocked" if state.get("blocked") else "continue"


def route_after_grading(state: dict, max_rewrites: int) -> str:
    if state.get("relevant"):
        return "generate"
    if state.get("rewrites", 0) < max_rewrites:
        return "rewrite"
    return "give_up"


def normalise_citations(answer: str) -> str:
    """Turn 【1】-style citations into [1] so the UI and parser see one format."""
    return _ODD_CITATION.sub(r"[\1]", answer or "")


def parse_citations(answer: str) -> set[int]:
    """Return the passage numbers cited in an answer, e.g. "... [1][3]" -> {1, 3}."""
    return {int(n) for n in _CITATION.findall(answer or "")}


def needs_escalation(grounded: bool, confidence: float, threshold: float = 0.5) -> bool:
    return (not grounded) or confidence < threshold

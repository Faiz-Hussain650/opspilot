from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

# Make sure tests never pick up a developer's real cloud credentials from the shell.
for var in (
    "OPSPILOT_MODE",
    "LLM_PROVIDER",
    "GROQ_API_KEY",
    "LANGFUSE_PUBLIC_KEY",
    "LANGFUSE_SECRET_KEY",
):
    os.environ.pop(var, None)


@pytest.fixture
def kb_dir() -> Path:
    return ROOT / "data" / "knowledge_base"


@pytest.fixture
def make_chunk():
    from opspilot.schemas import RetrievedChunk

    def _make(source="runbook-x.md", text="some text", **kw) -> RetrievedChunk:
        defaults = dict(
            id="1",
            text=text,
            source=source,
            title="A runbook",
            section="Mitigation",
            doc_type="runbook",
            service="checkout-service",
            score=0.5,
            rerank_score=1.2,
        )
        defaults.update(kw)
        return RetrievedChunk(**defaults)

    return _make

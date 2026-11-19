"""Service and API tests with a fake agent graph - no Qdrant, models or LLM needed."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

pytest.importorskip("pydantic_settings")
pytest.importorskip("prometheus_client")
pytest.importorskip("langchain_core")


class FakeGraph:
    """Mimics graph.astream(..., stream_mode=["updates", "messages"])."""

    def __init__(self, chunks, answer):
        self.chunks = chunks
        self.answer = answer

    async def astream(self, initial, config=None, stream_mode=None):
        yield "updates", {"guard_input": {"blocked": False, "steps": ["guardrail: input ok"]}}
        yield (
            "updates",
            {
                "retrieve": {
                    "documents": self.chunks,
                    "retrieved_sources": [c.source for c in self.chunks],
                    "steps": ["retrieve: 2 passages"],
                }
            },
        )
        yield "updates", {"grade_documents": {"relevant": self.chunks, "steps": ["grade: 2/2"]}}
        for token in self.answer.split(" "):
            yield "messages", (SimpleNamespace(content=token + " "), {"langgraph_node": "generate"})
        # Tokens from other nodes (e.g. the grader) must not leak into the answer stream.
        yield (
            "messages",
            (SimpleNamespace(content="IGNORED"), {"langgraph_node": "grade_documents"}),
        )
        yield "updates", {"generate": {"answer": self.answer, "steps": ["generate: done"]}}
        yield (
            "updates",
            {
                "check_grounding": {
                    "grounded": True,
                    "confidence": 0.9,
                    "escalate": False,
                    "steps": ["grounding: ok"],
                }
            },
        )


@pytest.fixture
def settings():
    from opspilot.config import Settings

    return Settings(_env_file=None, cache_enabled=False)


@pytest.fixture
def service(settings, make_chunk):
    from opspilot.service import ChatService

    chunks = [
        make_chunk(source="runbook-checkout-db-connection-pool.md", text="Cancel long queries."),
        make_chunk(source="pm-2025-03-14-checkout-outage.md", text="A connection leak.", id="2"),
    ]
    answer = "Cancel the long queries [1]. Admin password=hunter2222 is in the ticket."
    return ChatService(FakeGraph(chunks, answer), cache=None, settings=settings)


async def test_stream_emits_steps_tokens_and_final(service):
    from opspilot.schemas import ChatRequest

    events = [e async for e in service.stream(ChatRequest(question="checkout 502s"))]
    types = [e["type"] for e in events]

    assert types[0] == "step"
    assert "token" in types
    assert types[-1] == "final"
    streamed = "".join(e["data"] for e in events if e["type"] == "token")
    assert "IGNORED" not in streamed

    final = events[-1]["data"]
    assert final["grounded"] is True
    assert final["sources"][0]["cited"] is True
    assert final["sources"][1]["cited"] is False
    assert final["retrieved"] == [
        "runbook-checkout-db-connection-pool.md",
        "pm-2025-03-14-checkout-outage.md",
    ]
    # Output guardrail redacted the leaked password.
    assert "hunter2222" not in final["answer"]
    assert any("redacted" in s for s in final["steps"])


async def test_answer_returns_response(service):
    from opspilot.schemas import ChatRequest

    response = await service.answer(ChatRequest(question="checkout 502s", include_context=True))
    assert response.sources[0].text == "Cancel long queries."


def test_settings_cloud_mode_requires_groq_key():
    from pydantic import ValidationError

    from opspilot.config import Settings

    with pytest.raises(ValidationError):
        Settings(_env_file=None, opspilot_mode="cloud")

    s = Settings(_env_file=None, opspilot_mode="cloud", groq_api_key="gsk_test")
    assert s.llm_provider == "groq"
    assert Settings(_env_file=None).llm_provider == "ollama"
    # A blank value in .env means "not set".
    assert Settings(_env_file=None, qdrant_api_key="  ").qdrant_api_key is None


# ------------------------------------------------------------------- API


def test_api_endpoints(service, settings):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from opspilot.api.main import create_app

    store = SimpleNamespace(
        ensure_collection=lambda: None,
        count=lambda: 42,
        facet_values=lambda key: ["checkout-service"] if key == "service" else ["runbook"],
        ping=lambda: True,
    )
    container = SimpleNamespace(
        settings=settings,
        store=store,
        cache=SimpleNamespace(store=store, clear=lambda: None),
        chat=service,
        ingestion=SimpleNamespace(),
    )

    with TestClient(create_app(lambda: container)) as client:
        assert client.get("/health").json()["status"] == "ok"

        stats = client.get("/stats").json()
        assert stats["chunks"] == 42
        assert stats["services"] == ["checkout-service"]

        r = client.post("/chat", json={"question": "checkout is down"})
        assert r.status_code == 200
        assert r.json()["sources"][0]["source"] == "runbook-checkout-db-connection-pool.md"

        with client.stream("POST", "/chat/stream", json={"question": "checkout is down"}) as r:
            body = "".join(r.iter_text())
        assert "event: token" in body
        assert "event: final" in body

        assert client.post("/chat", json={"question": "x"}).status_code == 422  # too short

        metrics = client.get("/metrics/").text
        assert "opspilot_http_requests_total" in metrics

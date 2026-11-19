"""ChatService: the one place that knows how to answer a question end to end.

  cache lookup -> agent graph (streamed) -> output guardrails -> metrics -> cache write

Both the FastAPI app and the Streamlit "direct" mode call this, so behaviour is
identical whether you go through the API or not.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator

from opspilot import tracing
from opspilot.agent.routing import parse_citations
from opspilot.cache import SemanticCache
from opspilot.config import Settings
from opspilot.guardrails import redact_secrets
from opspilot.metrics import ANSWERS, CHAT_LATENCY, CONFIDENCE, ESCALATIONS, GUARDRAIL_BLOCKS
from opspilot.schemas import ChatRequest, ChatResponse, RetrievedChunk, Source

log = logging.getLogger(__name__)


def _dedupe(items: list[str]) -> list[str]:
    return list(dict.fromkeys(items))


def build_response(
    state: dict, steps: list[str], started: float, include_context: bool = False
) -> ChatResponse:
    answer = state.get("answer", "")
    relevant: list[RetrievedChunk] = state.get("relevant") or []
    cited = parse_citations(answer)

    sources = [
        Source(
            ref=i,
            source=chunk.source,
            title=chunk.title,
            section=chunk.section,
            doc_type=chunk.doc_type,
            service=chunk.service,
            snippet=chunk.text[:400],
            rerank_score=chunk.rerank_score,
            cited=i in cited,
            text=chunk.text if include_context else None,
        )
        for i, chunk in enumerate(relevant, start=1)
    ]

    rewritten = state.get("search_query")
    return ChatResponse(
        answer=answer,
        sources=sources,
        grounded=bool(state.get("grounded", False)),
        confidence=float(state.get("confidence", 0.0)),
        escalate=bool(state.get("escalate", False)),
        blocked=bool(state.get("blocked", False)),
        rewritten_query=rewritten if rewritten and rewritten != state.get("question") else None,
        retrieved=_dedupe(state.get("retrieved_sources") or []),
        steps=steps,
        latency_ms=int((time.perf_counter() - started) * 1000),
    )


class ChatService:
    def __init__(self, graph, cache: SemanticCache | None, settings: Settings):
        self.graph = graph
        self.cache = cache
        self.settings = settings

    async def answer(self, request: ChatRequest) -> ChatResponse:
        final: ChatResponse | None = None
        async for event in self.stream(request):
            if event["type"] == "final":
                final = ChatResponse.model_validate(event["data"])
            elif event["type"] == "error":
                raise RuntimeError(event["data"])
        assert final is not None
        return final

    async def stream(self, request: ChatRequest) -> AsyncIterator[dict]:
        """Yield events: {"type": "step"|"token"|"final"|"error", "data": ...}."""
        started = time.perf_counter()
        filters = request.filters
        use_cache = request.use_cache and self.cache is not None and self.settings.cache_enabled

        if use_cache:
            cached = await asyncio.to_thread(self.cache.get, request.question, filters)
            if cached:
                cached.latency_ms = int((time.perf_counter() - started) * 1000)
                CHAT_LATENCY.labels(cached="true").observe(time.perf_counter() - started)
                yield {"type": "step", "data": cached.steps[0]}
                yield {"type": "final", "data": cached.model_dump()}
                return

        initial = {
            "question": request.question,
            "filters": filters,
            "search_query": request.question,
            "queries_tried": [request.question],
            "rewrites": 0,
            "retrieved_sources": [],
            "steps": [],
        }
        config = {
            "callbacks": tracing.callbacks(self.settings),
            "run_name": "opspilot-chat",
            "recursion_limit": 25,
            "metadata": {"filters": filters.cache_key()},
        }

        state: dict = dict(initial)
        steps: list[str] = []
        try:
            async for mode, payload in self.graph.astream(
                initial, config=config, stream_mode=["updates", "messages"]
            ):
                if mode == "messages":
                    chunk, meta = payload
                    if meta.get("langgraph_node") == "generate" and chunk.content:
                        yield {"type": "token", "data": chunk.content}
                    continue

                for _node, update in (payload or {}).items():
                    if not update:
                        continue
                    for key, value in update.items():
                        if key == "steps":
                            continue
                        if key == "retrieved_sources":
                            state[key] = state.get(key, []) + value
                        else:
                            state[key] = value
                    for step in update.get("steps", []):
                        steps.append(step)
                        yield {"type": "step", "data": step}
        except Exception as exc:
            log.exception("Agent run failed")
            yield {"type": "error", "data": f"{type(exc).__name__}: {exc}"}
            return

        response = build_response(state, steps, started, request.include_context)

        redaction = redact_secrets(response.answer)
        if redaction.redacted:
            GUARDRAIL_BLOCKS.labels(kind="output_redaction").inc(len(redaction.redacted))
            response.answer = redaction.text
            response.steps.append(
                f"guardrail: redacted {', '.join(sorted(set(redaction.redacted)))}"
            )

        if not response.blocked:
            ANSWERS.labels(grounded=str(response.grounded).lower()).inc()
            CONFIDENCE.observe(response.confidence)
            if response.escalate:
                ESCALATIONS.inc()

        if use_cache:
            await asyncio.to_thread(self.cache.put, request.question, filters, response)

        CHAT_LATENCY.labels(cached="false").observe(time.perf_counter() - started)
        yield {"type": "final", "data": response.model_dump()}

"""The nodes of the agent graph. Each node takes the state and returns a partial update."""

from __future__ import annotations

import asyncio
import logging

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage

from opspilot.agent import prompts
from opspilot.agent.routing import needs_escalation
from opspilot.agent.state import AgentState
from opspilot.guardrails import REFUSAL_MESSAGE, check_input
from opspilot.metrics import GUARDRAIL_BLOCKS, LLM_LATENCY, QUERY_REWRITES
from opspilot.retrieval import HybridRetriever
from opspilot.schemas import RetrievedChunk

log = logging.getLogger(__name__)

NO_CONTEXT_MESSAGE = (
    "I couldn't find anything in the runbooks, postmortems or tickets that matches this "
    "problem, even after rephrasing the search ({tried}). I don't want to guess during an "
    "incident, so please **escalate to the owning team** and, once it's resolved, consider "
    "writing a runbook for it."
)


def format_passages(chunks: list[RetrievedChunk], max_chars: int | None = None) -> str:
    blocks = []
    for i, chunk in enumerate(chunks, start=1):
        where = f"{chunk.title} > {chunk.section}" if chunk.section else chunk.title
        text = chunk.text if max_chars is None else chunk.text[:max_chars]
        blocks.append(f"[{i}] ({chunk.doc_type}, {chunk.source}) {where}\n{text}")
    return "\n\n".join(blocks)


def _clamp(value: float) -> float:
    if value > 1.0:  # "85" probably meant 85%
        value = value / 100 if value <= 100 else 1.0
    return max(0.0, min(1.0, value))


class AgentNodes:
    def __init__(self, retriever: HybridRetriever, llm: BaseChatModel):
        self.retriever = retriever
        self.llm = llm
        # Structured-output runnables are built once and reused.
        self._grader = llm.with_structured_output(prompts.DocumentGrades)
        self._rewriter = llm.with_structured_output(prompts.RewrittenQuery)
        self._checker = llm.with_structured_output(prompts.GroundingCheck)

    # -------------------------------------------------------------- guard
    async def guard_input(self, state: AgentState) -> dict:
        verdict = check_input(state["question"])
        if verdict.allowed:
            return {"blocked": False, "steps": ["guardrail: input ok"]}

        GUARDRAIL_BLOCKS.labels(kind="input_injection").inc()
        log.warning("Blocked question (%s): %r", verdict.reason, state["question"][:120])
        return {
            "blocked": True,
            "answer": REFUSAL_MESSAGE,
            "grounded": False,
            "confidence": 0.0,
            "escalate": False,
            "steps": [f"guardrail: blocked ({verdict.reason})"],
        }

    # ----------------------------------------------------------- retrieve
    async def retrieve(self, state: AgentState) -> dict:
        query = state.get("search_query") or state["question"]
        docs = await asyncio.to_thread(self.retriever.search, query, state.get("filters"))
        return {
            "documents": docs,
            "retrieved_sources": [d.source for d in docs],
            "steps": [f"retrieve: {len(docs)} passages for '{query}'"],
        }

    # -------------------------------------------------------------- grade
    async def grade_documents(self, state: AgentState) -> dict:
        docs = state.get("documents") or []
        if not docs:
            return {"relevant": [], "steps": ["grade: nothing to grade"]}

        messages = [
            SystemMessage(prompts.GRADE_SYSTEM),
            HumanMessage(
                prompts.GRADE_USER.format(
                    question=state["question"], passages=format_passages(docs, max_chars=700)
                )
            ),
        ]
        try:
            with LLM_LATENCY.labels(step="grade").time():
                grades: prompts.DocumentGrades = await self._grader.ainvoke(messages)
            keep = {i for i in grades.relevant_ids if 1 <= i <= len(docs)}
            relevant = [d for i, d in enumerate(docs, start=1) if i in keep]
        except Exception as exc:
            # If the grader misbehaves, fall back to the reranker's opinion.
            # ms-marco cross-encoders output logits: > 0 roughly means "relevant".
            log.warning("Grader failed (%s), falling back to rerank scores", exc)
            relevant = [d for d in docs if (d.rerank_score or 0) > 0] or docs[:3]

        return {
            "relevant": relevant,
            "steps": [f"grade: {len(relevant)}/{len(docs)} passages relevant"],
        }

    # ------------------------------------------------------------ rewrite
    async def rewrite_query(self, state: AgentState) -> dict:
        tried = state.get("queries_tried") or [state["question"]]
        messages = [
            SystemMessage(prompts.REWRITE_SYSTEM),
            HumanMessage(
                prompts.REWRITE_USER.format(
                    question=state["question"], tried="\n".join(f"- {q}" for q in tried)
                )
            ),
        ]
        try:
            with LLM_LATENCY.labels(step="rewrite").time():
                result: prompts.RewrittenQuery = await self._rewriter.ainvoke(messages)
            new_query = result.query.strip() or state["question"]
        except Exception as exc:
            log.warning("Rewrite failed (%s), widening the search instead", exc)
            new_query = state["question"]

        QUERY_REWRITES.inc()
        return {
            "search_query": new_query,
            "queries_tried": [*tried, new_query],
            "rewrites": state.get("rewrites", 0) + 1,
            # A rewrite also drops the user's filters: the right doc may be
            # tagged with a different service than the user guessed.
            "filters": None,
            "steps": [f"rewrite: trying '{new_query}'"],
        }

    # ----------------------------------------------------------- generate
    async def generate(self, state: AgentState) -> dict:
        relevant = state["relevant"]
        messages = [
            SystemMessage(prompts.GENERATE_SYSTEM),
            HumanMessage(
                prompts.GENERATE_USER.format(
                    context=format_passages(relevant), question=state["question"]
                )
            ),
        ]
        with LLM_LATENCY.labels(step="generate").time():
            response = await self.llm.ainvoke(messages)
        answer = response.content if isinstance(response.content, str) else str(response.content)
        return {
            "answer": answer.strip(),
            "steps": [f"generate: answered from {len(relevant)} passages"],
        }

    async def no_context(self, state: AgentState) -> dict:
        tried = ", ".join(f"'{q}'" for q in (state.get("queries_tried") or [state["question"]]))
        return {
            "answer": NO_CONTEXT_MESSAGE.format(tried=tried),
            "grounded": True,  # saying "I don't know" is honest, not a hallucination
            "confidence": 0.0,
            "escalate": True,
            "relevant": [],
            "steps": ["give up: no relevant runbooks, escalating"],
        }

    # ------------------------------------------------------- grounding
    async def check_grounding(self, state: AgentState) -> dict:
        messages = [
            SystemMessage(prompts.GROUNDING_SYSTEM),
            HumanMessage(
                prompts.GROUNDING_USER.format(
                    context=format_passages(state["relevant"]), answer=state["answer"]
                )
            ),
        ]
        try:
            with LLM_LATENCY.labels(step="grounding").time():
                check: prompts.GroundingCheck = await self._checker.ainvoke(messages)
            grounded, confidence = check.grounded, _clamp(check.confidence)
            unsupported = check.unsupported_claims
            note = (
                f"grounding: {'ok' if grounded else 'NOT grounded'} (confidence {confidence:.2f})"
            )
        except Exception as exc:
            log.warning("Grounding check failed: %s", exc)
            grounded, confidence, unsupported = False, 0.5, []
            note = "grounding: check unavailable"

        return {
            "grounded": grounded,
            "confidence": confidence,
            "unsupported_claims": unsupported,
            "escalate": needs_escalation(grounded, confidence),
            "steps": [note],
        }

"""Evaluate OpsPilot against the golden dataset.

    uv run python -m evaluation.run_eval                      # full run, logs to MLflow
    uv run python -m evaluation.run_eval --limit 5            # quick smoke test
    RERANK_ENABLED=false uv run python -m evaluation.run_eval --run-name no-rerank

What gets measured
  Retrieval  : hit rate, recall and MRR against the expected source documents.
  Generation : an LLM judge scores faithfulness (is every claim backed by the
               sources?), relevancy (does it answer the question?) and correctness
               (does it match the reference answer?).
  Behaviour  : out-of-scope questions must escalate, injections must be blocked.

Exit code is 1 if any metric is under its threshold - that's how the CI
evaluation gate fails a pull request.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from evaluation.metrics import aggregate, hit_at_k, recall, reciprocal_rank
from opspilot.agent.prompts import PROMPT_VERSION
from opspilot.config import get_settings
from opspilot.container import bootstrap, build_container
from opspilot.llm import build_chat_model
from opspilot.logging_setup import setup_logging
from opspilot.schemas import ChatRequest

log = logging.getLogger("evaluation")

HERE = Path(__file__).resolve().parent

JUDGE_SYSTEM = """You are an impartial evaluator of answers produced by an SRE assistant.
Score each dimension from 0.0 to 1.0:
- faithfulness: are all claims and commands in the answer supported by the CONTEXT? (1.0 = fully supported)
- relevancy: does the answer actually address the QUESTION? (1.0 = directly and completely)
- correctness: does the answer agree with the REFERENCE ANSWER's key points? Missing key \
points lowers the score; contradicting it lowers it a lot.
Be strict but fair. Give a one sentence explanation."""

JUDGE_USER = """QUESTION:
{question}

REFERENCE ANSWER:
{reference}

CONTEXT:
{context}

ANSWER TO EVALUATE:
{answer}"""


class JudgeScores(BaseModel):
    faithfulness: float = Field(description="0 to 1")
    relevancy: float = Field(description="0 to 1")
    correctness: float = Field(description="0 to 1")
    explanation: str = ""


def _clamp(x: float) -> float:
    return max(0.0, min(1.0, x / 100 if x > 1.0 else x))


def load_dataset(path: Path, limit: int | None, subset: str | None = None) -> list[dict]:
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    if subset:
        rows = [r for r in rows if r.get(subset)]
    return rows[:limit] if limit else rows


async def judge(judge_llm, item: dict, response) -> dict | None:
    context = (
        "\n\n".join(f"[{s.ref}] {s.title}\n{s.text or s.snippet}" for s in response.sources)
        or "(no context)"
    )
    messages = [
        SystemMessage(JUDGE_SYSTEM),
        HumanMessage(
            JUDGE_USER.format(
                question=item["question"],
                reference=item["reference_answer"],
                context=context,
                answer=response.answer,
            )
        ),
    ]
    try:
        scores: JudgeScores = await judge_llm.ainvoke(messages)
    except Exception as exc:
        log.warning("Judge failed on %s: %s", item["id"], exc)
        return None
    return {
        "faithfulness": _clamp(scores.faithfulness),
        "relevancy": _clamp(scores.relevancy),
        "correctness": _clamp(scores.correctness),
        "explanation": scores.explanation,
    }


async def evaluate(args) -> tuple[dict, list[dict]]:
    settings = get_settings()
    container = build_container(settings)
    if args.reindex:
        container.ingestion.ingest_directory(settings.knowledge_base_dir, recreate=True)
    bootstrap(container)

    judge_llm = build_chat_model(
        settings, model=settings.judge_model or None, temperature=0.0
    ).with_structured_output(JudgeScores)

    dataset = load_dataset(Path(args.dataset), args.limit, args.subset)
    rows = []
    for n, item in enumerate(dataset, start=1):
        started = time.perf_counter()
        response = await container.chat.answer(
            ChatRequest(question=item["question"], use_cache=False, include_context=True)
        )
        expected = item.get("expected_sources", [])

        row = {
            "id": item["id"],
            "category": item.get("category", "runbook"),
            "question": item["question"],
            "expected_sources": expected,
            "retrieved": response.retrieved,
            "hit": hit_at_k(response.retrieved, expected) if expected else None,
            "recall": recall(response.retrieved, expected) if expected else None,
            "mrr": reciprocal_rank(response.retrieved, expected) if expected else None,
            "grounded": response.grounded,
            "confidence": response.confidence,
            "escalate": response.escalate,
            "blocked": response.blocked,
            "rewritten": response.rewritten_query is not None,
            "latency_ms": response.latency_ms,
            "answer": response.answer,
            "behaviour_pass": None,
            "judge": None,
        }

        if item.get("should_block"):
            row["behaviour_pass"] = response.blocked
        elif item.get("should_escalate"):
            row["behaviour_pass"] = response.escalate
        elif expected and not response.blocked:
            row["judge"] = await judge(judge_llm, item, response)

        rows.append(row)
        verdict = ""
        if row["judge"]:
            verdict = f"correctness={row['judge']['correctness']:.2f}"
        elif row["behaviour_pass"] is not None:
            verdict = "PASS" if row["behaviour_pass"] else "FAIL"
        log.info(
            "[%2d/%d] %s hit=%s %s (%.1fs)",
            n,
            len(dataset),
            item["id"],
            row["hit"],
            verdict,
            time.perf_counter() - started,
        )
        if args.sleep:
            await asyncio.sleep(args.sleep)

    return aggregate(rows), rows


def write_reports(summary: dict, rows: list[dict], params: dict, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    json_path = out_dir / f"eval-{stamp}.json"
    md_path = out_dir / f"eval-{stamp}.md"

    json_path.write_text(json.dumps({"summary": summary, "params": params, "rows": rows}, indent=2))

    lines = [
        "# OpsPilot evaluation report",
        "",
        f"_{datetime.now():%Y-%m-%d %H:%M}_ · model `{params['llm_model']}` · "
        f"prompt `{params['prompt_version']}`",
        "",
        "| Metric | Value |",
        "|---|---|",
    ]
    for key, value in summary.items():
        lines.append(f"| {key} | {'n/a' if value is None else value} |")
    lines += [
        "",
        "## Per question",
        "",
        "| id | category | hit | MRR | correctness | faithfulness | behaviour | latency |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        j = r["judge"] or {}

        def fmt(v):
            return "-" if v is None else f"{v:.2f}"

        behaviour = (
            "-" if r["behaviour_pass"] is None else ("pass" if r["behaviour_pass"] else "FAIL")
        )
        lines.append(
            f"| {r['id']} | {r['category']} | {fmt(r['hit'])} | {fmt(r['mrr'])} | "
            f"{fmt(j.get('correctness'))} | {fmt(j.get('faithfulness'))} | {behaviour} | "
            f"{r['latency_ms'] / 1000:.1f}s |"
        )
    md_path.write_text("\n".join(lines) + "\n")
    return md_path


def log_to_mlflow(summary: dict, params: dict, report: Path, run_name: str | None) -> None:
    uri = os.getenv("MLFLOW_TRACKING_URI", "http://localhost:5001")
    try:
        import httpx
        import mlflow

        if uri.startswith("http"):
            httpx.get(uri, timeout=3)  # fail fast if the server isn't running
        mlflow.set_tracking_uri(uri)
        mlflow.set_experiment(os.getenv("MLFLOW_EXPERIMENT", "opspilot-eval"))
        with mlflow.start_run(run_name=run_name):
            mlflow.log_params(params)
            mlflow.log_metrics({k: float(v) for k, v in summary.items() if v is not None})
            mlflow.log_artifact(str(report))
            mlflow.log_artifact(str(report.with_suffix(".json")))
        log.info("Logged run to MLflow at %s", uri)
    except Exception as exc:
        log.warning("Skipping MLflow logging (%s): %s", uri, exc)


def check_thresholds(summary: dict, args) -> list[str]:
    failures = []
    checks = {
        "hit_rate": args.min_hit_rate,
        "faithfulness": args.min_faithfulness,
        "correctness": args.min_correctness,
        "behaviour_accuracy": args.min_behaviour,
    }
    for metric, minimum in checks.items():
        value = summary.get(metric)
        if minimum is None or value is None:  # no threshold, or no questions of that kind
            continue
        if value < minimum:
            failures.append(f"{metric} = {value:.3f} is below the threshold {minimum}")
    return failures


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the OpsPilot evaluation suite")
    parser.add_argument("--dataset", default=str(HERE / "golden_dataset.jsonl"))
    parser.add_argument("--limit", type=int, help="only run the first N questions")
    parser.add_argument("--subset", help="only questions with this flag set, e.g. --subset ci")
    parser.add_argument("--reindex", action="store_true", help="re-ingest the KB first")
    parser.add_argument("--run-name", help="MLflow run name")
    parser.add_argument("--no-mlflow", action="store_true")
    parser.add_argument(
        "--sleep", type=float, default=0.0, help="pause between questions (rate limits)"
    )
    parser.add_argument("--output-dir", default=str(HERE / "reports"))
    parser.add_argument("--min-hit-rate", type=float)
    parser.add_argument("--min-faithfulness", type=float)
    parser.add_argument("--min-correctness", type=float)
    parser.add_argument("--min-behaviour", type=float)
    args = parser.parse_args()

    settings = get_settings()
    setup_logging(settings.log_level)

    started = time.perf_counter()
    summary, rows = asyncio.run(evaluate(args))
    params = {**settings.public_summary(), "prompt_version": PROMPT_VERSION, "questions": len(rows)}
    report = write_reports(summary, rows, params, Path(args.output_dir))

    print("\n" + report.read_text().split("## Per question")[0])
    print(f"Full report: {report}  ({time.perf_counter() - started:.0f}s)")

    if not args.no_mlflow:
        log_to_mlflow(summary, params, report, args.run_name)

    failures = check_thresholds(summary, args)
    if failures:
        print("\nEVALUATION GATE FAILED:")
        for f in failures:
            print(f"  - {f}")
        sys.exit(1)
    print("\nEvaluation gate passed.")


if __name__ == "__main__":
    main()

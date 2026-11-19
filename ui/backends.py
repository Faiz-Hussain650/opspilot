"""How the Streamlit UI talks to OpsPilot.

ApiBackend    - normal mode: calls the FastAPI service over HTTP (Docker, k8s).
DirectBackend - Streamlit Community Cloud mode: there's no separate API server
                there, so the UI imports the OpsPilot package and runs the same
                ChatService in-process.
Both expose the same three methods, so app.py doesn't care which one it has.
"""

from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import httpx


def _iterate_async(agen: AsyncIterator[dict]) -> Iterator[dict]:
    """Drive an async generator from Streamlit's synchronous script thread."""
    loop = asyncio.new_event_loop()
    try:
        while True:
            try:
                yield loop.run_until_complete(agen.__anext__())
            except StopAsyncIteration:
                break
    finally:
        loop.run_until_complete(loop.shutdown_asyncgens())
        loop.close()


class ApiBackend:
    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")

    def stream_chat(self, payload: dict) -> Iterator[dict]:
        timeout = httpx.Timeout(300.0, connect=10.0)
        with httpx.stream(
            "POST", f"{self.base_url}/chat/stream", json=payload, timeout=timeout
        ) as r:
            if r.status_code != 200:
                r.read()
                yield {"type": "error", "data": f"API returned {r.status_code}: {r.text[:300]}"}
                return
            event_type = None
            for line in r.iter_lines():
                if line.startswith("event:"):
                    event_type = line.split(":", 1)[1].strip()
                elif line.startswith("data:") and event_type:
                    yield {"type": event_type, "data": json.loads(line.split(":", 1)[1].strip())}
                    event_type = None

    def stats(self) -> dict | None:
        try:
            return httpx.get(f"{self.base_url}/stats", timeout=10).json()
        except Exception:
            return None

    def upload(self, filename: str, data: bytes, doc_type: str | None, service: str | None) -> dict:
        form = {k: v for k, v in {"doc_type": doc_type, "service": service}.items() if v}
        r = httpx.post(
            f"{self.base_url}/ingest/upload",
            files={"file": (filename, data)},
            data=form,
            timeout=300,
        )
        r.raise_for_status()
        return r.json()

    def clear_cache(self) -> None:
        httpx.delete(f"{self.base_url}/cache", timeout=30).raise_for_status()


class DirectBackend:
    def __init__(self):
        src = Path(__file__).resolve().parents[1] / "src"
        if str(src) not in sys.path:
            sys.path.insert(0, str(src))

        from opspilot.config import get_settings
        from opspilot.container import bootstrap, build_container
        from opspilot.logging_setup import setup_logging

        settings = get_settings()
        setup_logging(settings.log_level)
        self.container = build_container(settings)
        bootstrap(self.container)

    def stream_chat(self, payload: dict) -> Iterator[dict]:
        from opspilot.schemas import ChatRequest

        yield from _iterate_async(self.container.chat.stream(ChatRequest(**payload)))

    def stats(self) -> dict | None:
        c = self.container
        return {
            "collection": c.settings.collection_name,
            "chunks": c.store.count(),
            "services": c.store.facet_values("service"),
            "doc_types": c.store.facet_values("doc_type"),
            "llm_provider": c.settings.llm_provider,
            "llm_model": c.settings.llm_model_name,
            "mode": c.settings.opspilot_mode,
        }

    def upload(self, filename: str, data: bytes, doc_type: str | None, service: str | None) -> dict:
        result = self.container.ingestion.ingest_upload(data, filename, doc_type, service)
        return result.model_dump()

    def clear_cache(self) -> None:
        self.container.cache.clear()

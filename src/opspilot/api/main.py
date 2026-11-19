"""FastAPI application.

Run locally with:
    uv run uvicorn opspilot.api.main:app --reload
Interactive docs: http://localhost:8000/docs
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections.abc import Callable
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse
from prometheus_client import make_asgi_app

from opspilot import __version__
from opspilot.config import get_settings
from opspilot.container import Container, bootstrap, build_container
from opspilot.llm import check_llm
from opspilot.logging_setup import setup_logging
from opspilot.metrics import REQUEST_LATENCY, REQUESTS
from opspilot.schemas import ChatRequest, ChatResponse, IngestResult, StatsResponse

log = logging.getLogger(__name__)

MAX_UPLOAD_BYTES = 10 * 1024 * 1024


def create_app(container_factory: Callable[[], Container] | None = None) -> FastAPI:
    factory = container_factory or (lambda: build_container(get_settings()))

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        setup_logging(os.getenv("LOG_LEVEL", "INFO"))
        # Loading ONNX models and talking to Qdrant is blocking work.
        container = await asyncio.to_thread(factory)
        await asyncio.to_thread(bootstrap, container)
        app.state.container = container
        log.info("OpsPilot API ready")
        yield

    app = FastAPI(
        title="OpsPilot API",
        version=__version__,
        description="Agentic RAG copilot for on-call engineers.",
        lifespan=lifespan,
    )
    app.mount("/metrics", make_asgi_app())

    @app.middleware("http")
    async def record_metrics(request: Request, call_next):
        if request.url.path.startswith("/metrics"):
            return await call_next(request)
        started = time.perf_counter()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            return response
        finally:
            route = request.scope.get("route")
            path = getattr(route, "path", "unmatched")
            REQUESTS.labels(request.method, path, str(status)).inc()
            REQUEST_LATENCY.labels(path).observe(time.perf_counter() - started)

    def get_container(request: Request) -> Container:
        return request.app.state.container

    # ----------------------------------------------------------- health
    @app.get("/health", tags=["ops"])
    async def health():
        """Liveness: the process is up."""
        return {"status": "ok", "version": __version__}

    @app.get("/ready", tags=["ops"])
    async def ready(c: Container = Depends(get_container)):
        """Readiness: Qdrant and the LLM are reachable."""
        qdrant_ok = await asyncio.to_thread(c.store.ping)
        llm_ok = await asyncio.to_thread(check_llm, c.settings)
        body = {"qdrant": qdrant_ok, "llm": llm_ok, "llm_provider": c.settings.llm_provider}
        return JSONResponse(body, status_code=200 if qdrant_ok and llm_ok else 503)

    @app.get("/stats", response_model=StatsResponse, tags=["ops"])
    async def stats(c: Container = Depends(get_container)):
        chunks = await asyncio.to_thread(c.store.count)
        services = await asyncio.to_thread(c.store.facet_values, "service")
        doc_types = await asyncio.to_thread(c.store.facet_values, "doc_type")
        return StatsResponse(
            collection=c.settings.collection_name,
            chunks=chunks,
            services=services,
            doc_types=doc_types,
            llm_provider=c.settings.llm_provider,
            llm_model=c.settings.llm_model_name,
            mode=c.settings.opspilot_mode,
        )

    # ------------------------------------------------------------- chat
    @app.post("/chat", response_model=ChatResponse, tags=["chat"])
    async def chat(body: ChatRequest, c: Container = Depends(get_container)):
        try:
            return await c.chat.answer(body)
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @app.post("/chat/stream", tags=["chat"])
    async def chat_stream(body: ChatRequest, c: Container = Depends(get_container)):
        """Server-Sent Events: `step`, `token`, then one `final` (or `error`)."""

        async def events():
            async for event in c.chat.stream(body):
                yield f"event: {event['type']}\ndata: {json.dumps(event['data'])}\n\n"

        return StreamingResponse(
            events(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # ------------------------------------------------------- ingestion
    @app.post("/ingest/upload", response_model=IngestResult, tags=["ingest"])
    async def upload(
        file: UploadFile = File(...),
        doc_type: str | None = Form(None),
        service: str | None = Form(None),
        c: Container = Depends(get_container),
    ):
        data = await file.read()
        if len(data) > MAX_UPLOAD_BYTES:
            raise HTTPException(413, "File is larger than 10 MB")
        try:
            return await asyncio.to_thread(
                c.ingestion.ingest_upload, data, file.filename or "upload.txt", doc_type, service
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/ingest/reindex", response_model=IngestResult, tags=["ingest"])
    async def reindex(c: Container = Depends(get_container)):
        """Drop the collection and re-ingest the bundled knowledge base."""
        result = await asyncio.to_thread(
            c.ingestion.ingest_directory, c.settings.knowledge_base_dir, True
        )
        await asyncio.to_thread(c.cache.clear)
        return result

    @app.delete("/cache", tags=["ops"])
    async def clear_cache(c: Container = Depends(get_container)):
        await asyncio.to_thread(c.cache.clear)
        return {"cleared": True}

    return app


app = create_app()

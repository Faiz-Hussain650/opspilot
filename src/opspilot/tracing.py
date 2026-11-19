"""Optional Langfuse tracing.

With LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY set, every LLM call made inside
the agent graph shows up in Langfuse as a nested trace: prompts, outputs,
token counts and latency per step. Without the keys this is a no-op.
"""

from __future__ import annotations

import logging
import os

from opspilot.config import Settings

log = logging.getLogger(__name__)

_handler_available: bool | None = None


def _init(settings: Settings) -> bool:
    global _handler_available
    if _handler_available is not None:
        return _handler_available

    if not settings.tracing_enabled:
        _handler_available = False
        return False

    # The Langfuse SDK reads these from the environment.
    os.environ.setdefault("LANGFUSE_PUBLIC_KEY", settings.langfuse_public_key or "")
    os.environ.setdefault("LANGFUSE_SECRET_KEY", settings.langfuse_secret_key.get_secret_value())
    os.environ.setdefault("LANGFUSE_HOST", settings.langfuse_host)
    try:
        from langfuse.langchain import CallbackHandler  # noqa: F401

        _handler_available = True
        log.info("Langfuse tracing enabled (%s)", settings.langfuse_host)
    except Exception as exc:
        log.warning("Langfuse keys set but tracing could not start: %s", exc)
        _handler_available = False
    return _handler_available


def callbacks(settings: Settings) -> list:
    """Callback handlers to pass in a LangChain/LangGraph run config."""
    if not _init(settings):
        return []
    from langfuse.langchain import CallbackHandler

    return [CallbackHandler()]

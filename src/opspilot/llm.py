"""Chat model factory.

The rest of the code only sees a LangChain `BaseChatModel`, so switching
between a local Ollama model and the Groq cloud API is a config change.
"""

from __future__ import annotations

import logging

from langchain_core.language_models.chat_models import BaseChatModel

from opspilot.config import Settings

log = logging.getLogger(__name__)


def build_chat_model(
    settings: Settings,
    *,
    model: str | None = None,
    temperature: float | None = None,
) -> BaseChatModel:
    temp = settings.llm_temperature if temperature is None else temperature

    if settings.llm_provider == "groq":
        from langchain_groq import ChatGroq

        name = model or settings.groq_model
        log.info("Using Groq model %s", name)
        return ChatGroq(
            model=name,
            api_key=settings.groq_api_key.get_secret_value(),
            temperature=temp,
            timeout=settings.llm_timeout_seconds,
            # The free tier has per-minute limits; the client backs off and retries on 429.
            max_retries=6,
        )

    from langchain_ollama import ChatOllama

    name = model or settings.ollama_model
    log.info("Using Ollama model %s at %s", name, settings.ollama_base_url)
    return ChatOllama(
        model=name,
        base_url=settings.ollama_base_url,
        temperature=temp,
        num_ctx=settings.ollama_num_ctx,
        # Keep the model loaded between requests so we don't pay the load time again.
        keep_alive="30m",
    )


def check_llm(settings: Settings) -> bool:
    """Cheap readiness probe: is the model server reachable?"""
    import httpx

    try:
        if settings.llm_provider == "groq":
            r = httpx.get(
                "https://api.groq.com/openai/v1/models",
                headers={"Authorization": f"Bearer {settings.groq_api_key.get_secret_value()}"},
                timeout=5,
            )
            return r.status_code == 200
        r = httpx.get(f"{settings.ollama_base_url}/api/tags", timeout=5)
        if r.status_code != 200:
            return False
        names = {m.get("name", "") for m in r.json().get("models", [])}
        wanted = settings.ollama_model
        return any(
            n == wanted or n.startswith(wanted + ":") or n.split(":")[0] == wanted for n in names
        )
    except Exception:
        return False

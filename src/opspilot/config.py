"""Application settings.

Everything is driven by environment variables (or a .env file) so the exact same
code runs on a laptop, in Docker Compose, in Kubernetes and in the cloud.

OPSPILOT_MODE picks sensible defaults:
  local -> Ollama on your Mac + Qdrant in Docker
  cloud -> Groq API + Qdrant Cloud
Any individual setting can still be overridden, e.g. LLM_PROVIDER=groq while
keeping a local Qdrant (that's what the CI evaluation gate does).
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        # The repo-root .env is found even when running from a sub-folder (e.g. ui/).
        env_file=(str(PROJECT_ROOT / ".env"), ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    opspilot_mode: Literal["local", "cloud"] = "local"
    log_level: str = "INFO"

    # --- LLM -------------------------------------------------------------
    # Left empty on purpose: the validator below fills it in from the mode.
    llm_provider: Literal["ollama", "groq", ""] = ""
    llm_temperature: float = 0.1
    llm_timeout_seconds: int = 120

    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "qwen2.5:7b"
    ollama_num_ctx: int = 8192

    groq_api_key: SecretStr | None = None
    groq_model: str = "openai/gpt-oss-120b"

    # Model used by the evaluation judge. Empty means "same as the main model".
    judge_model: str = ""

    # --- Vector store ----------------------------------------------------
    qdrant_url: str = "http://localhost:6333"
    qdrant_api_key: SecretStr | None = None
    collection_name: str = "opspilot_kb"
    cache_collection_name: str = "opspilot_cache"

    # --- Embedding / reranking models (FastEmbed, ONNX, CPU friendly) -----
    dense_model: str = "BAAI/bge-small-en-v1.5"
    sparse_model: str = "Qdrant/bm25"
    rerank_model: str = "Xenova/ms-marco-MiniLM-L-6-v2"
    models_cache_dir: str = str(PROJECT_ROOT / ".cache" / "fastembed")

    # --- Retrieval ---------------------------------------------------------
    candidate_k: int = 20
    top_k: int = 5
    rerank_enabled: bool = True
    max_query_rewrites: int = 2

    # --- Ingestion -------------------------------------------------------
    knowledge_base_dir: str = str(PROJECT_ROOT / "data" / "knowledge_base")
    chunk_size: int = 1200
    chunk_overlap: int = 150
    auto_ingest: bool = True

    # --- Semantic cache --------------------------------------------------
    cache_enabled: bool = True
    cache_similarity_threshold: float = 0.92
    cache_ttl_hours: int = 24

    # --- Observability -----------------------------------------------------
    langfuse_public_key: str | None = None
    langfuse_secret_key: SecretStr | None = None
    langfuse_host: str = "https://cloud.langfuse.com"

    # --- UI ----------------------------------------------------------------
    api_url: str = "http://localhost:8000"

    @field_validator(
        "groq_api_key",
        "qdrant_api_key",
        "langfuse_public_key",
        "langfuse_secret_key",
        mode="before",
    )
    @classmethod
    def _blank_is_none(cls, value):
        # An empty line like `GROQ_API_KEY=` in .env should mean "not set".
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @model_validator(mode="after")
    def _apply_mode_defaults(self) -> Settings:
        if not self.llm_provider:
            self.llm_provider = "groq" if self.opspilot_mode == "cloud" else "ollama"

        if self.llm_provider == "groq" and self.groq_api_key is None:
            raise ValueError(
                "LLM_PROVIDER is 'groq' but GROQ_API_KEY is not set. "
                "Create a free key at https://console.groq.com/keys"
            )
        if self.top_k > self.candidate_k:
            raise ValueError("TOP_K cannot be larger than CANDIDATE_K")
        return self

    @property
    def llm_model_name(self) -> str:
        return self.groq_model if self.llm_provider == "groq" else self.ollama_model

    @property
    def tracing_enabled(self) -> bool:
        return bool(self.langfuse_public_key and self.langfuse_secret_key)

    def public_summary(self) -> dict:
        """Non-secret settings, handy for logs and for MLflow params."""
        return {
            "mode": self.opspilot_mode,
            "llm_provider": self.llm_provider,
            "llm_model": self.llm_model_name,
            "dense_model": self.dense_model,
            "sparse_model": self.sparse_model,
            "rerank_model": self.rerank_model if self.rerank_enabled else "disabled",
            "candidate_k": self.candidate_k,
            "top_k": self.top_k,
            "chunk_size": self.chunk_size,
            "chunk_overlap": self.chunk_overlap,
            "max_query_rewrites": self.max_query_rewrites,
            "cache_enabled": self.cache_enabled,
        }


@lru_cache
def get_settings() -> Settings:
    return Settings()

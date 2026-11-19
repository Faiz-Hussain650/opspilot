"""Ingest the knowledge base from the command line.

    uv run python scripts/ingest.py                 # add/update docs
    uv run python scripts/ingest.py --recreate      # wipe the collection first
    uv run python scripts/ingest.py --path my_docs/ # ingest another folder

Uses the same settings as the API, so with OPSPILOT_MODE=cloud (and your Qdrant
Cloud URL/key in .env) this loads the docs into Qdrant Cloud.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from opspilot.config import get_settings  # noqa: E402
from opspilot.embeddings import Embedder  # noqa: E402
from opspilot.ingestion.pipeline import IngestionPipeline  # noqa: E402
from opspilot.logging_setup import setup_logging  # noqa: E402
from opspilot.vectorstore import KnowledgeStore, build_client  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Index documents into Qdrant")
    parser.add_argument("--path", help="folder to ingest (default: KNOWLEDGE_BASE_DIR)")
    parser.add_argument("--recreate", action="store_true", help="drop and recreate the collection")
    args = parser.parse_args()

    settings = get_settings()
    setup_logging(settings.log_level)

    started = time.perf_counter()
    embedder = Embedder(settings)
    store = KnowledgeStore(build_client(settings), settings.collection_name, embedder.dimension)
    pipeline = IngestionPipeline(embedder, store, settings.chunk_size, settings.chunk_overlap)

    result = pipeline.ingest_directory(
        args.path or settings.knowledge_base_dir, recreate=args.recreate
    )
    print(
        f"\nDone: {result.files} files, {result.chunks} chunks into "
        f"'{settings.collection_name}' at {settings.qdrant_url} "
        f"in {time.perf_counter() - started:.1f}s"
    )


if __name__ == "__main__":
    main()

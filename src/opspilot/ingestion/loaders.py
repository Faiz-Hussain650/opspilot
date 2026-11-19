"""Turn files on disk (or uploaded bytes) into plain text plus metadata.

Markdown files may start with YAML front matter:

    ---
    title: Checkout DB connection pool exhausted
    service: checkout-service
    doc_type: runbook
    severity: sev2
    tags: [postgres, pgbouncer]
    ---

If a field is missing we fall back to something sensible (folder name for the
doc type, first heading for the title, and so on).
"""

from __future__ import annotations

import io
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

log = logging.getLogger(__name__)

SUPPORTED_EXTENSIONS = {".md", ".markdown", ".txt", ".pdf"}

# Folder names we recognise -> doc type
_FOLDER_DOC_TYPES = {
    "runbooks": "runbook",
    "postmortems": "postmortem",
    "tickets": "ticket",
}

_FRONT_MATTER = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?", re.DOTALL)
_FIRST_HEADING = re.compile(r"^#\s+(.+)$", re.MULTILINE)


@dataclass
class Document:
    text: str
    source: str
    title: str
    doc_type: str = "other"
    service: str = "general"
    severity: str | None = None
    tags: list[str] = field(default_factory=list)


def split_front_matter(raw: str) -> tuple[dict, str]:
    match = _FRONT_MATTER.match(raw)
    if not match:
        return {}, raw
    try:
        meta = yaml.safe_load(match.group(1)) or {}
    except yaml.YAMLError as exc:
        log.warning("Ignoring broken front matter: %s", exc)
        return {}, raw
    if not isinstance(meta, dict):
        return {}, raw
    return meta, raw[match.end() :]


def _title_from(body: str, fallback: str) -> str:
    heading = _FIRST_HEADING.search(body)
    if heading:
        return heading.group(1).strip()
    return fallback.replace("-", " ").replace("_", " ").strip().capitalize()


def _normalise_tags(value) -> list[str]:
    if not value:
        return []
    if isinstance(value, str):
        return [t.strip() for t in value.split(",") if t.strip()]
    return [str(t).strip() for t in value if str(t).strip()]


def _read_pdf(data: bytes) -> str:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    pages = [(page.extract_text() or "").strip() for page in reader.pages]
    return "\n\n".join(p for p in pages if p)


def load_bytes(
    data: bytes,
    filename: str,
    *,
    doc_type: str | None = None,
    service: str | None = None,
) -> Document:
    """Load a single file from raw bytes. Used for uploads and by load_file()."""
    ext = Path(filename).suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        raise ValueError(
            f"Unsupported file type '{ext}'. Use one of {sorted(SUPPORTED_EXTENSIONS)}"
        )

    if ext == ".pdf":
        meta, body = {}, _read_pdf(data)
    else:
        meta, body = split_front_matter(data.decode("utf-8", errors="replace"))

    body = body.strip()
    if not body:
        raise ValueError(f"{filename} has no readable text")

    stem = Path(filename).stem
    return Document(
        text=body,
        source=Path(filename).name,
        title=str(meta.get("title") or _title_from(body, stem)),
        doc_type=str(doc_type or meta.get("doc_type") or "other").lower(),
        service=str(service or meta.get("service") or "general").lower(),
        severity=meta.get("severity"),
        tags=_normalise_tags(meta.get("tags")),
    )


def load_file(path: Path) -> Document:
    folder_type = _FOLDER_DOC_TYPES.get(path.parent.name.lower())
    doc = load_bytes(path.read_bytes(), path.name)
    if doc.doc_type == "other" and folder_type:
        doc.doc_type = folder_type
    return doc


def load_directory(directory: Path) -> list[Document]:
    if not directory.exists():
        raise FileNotFoundError(f"Knowledge base folder not found: {directory}")

    docs = []
    for path in sorted(directory.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            continue
        if path.name.lower() == "readme.md":
            continue
        try:
            docs.append(load_file(path))
        except ValueError as exc:
            log.warning("Skipping %s: %s", path.name, exc)
    log.info("Loaded %d documents from %s", len(docs), directory)
    return docs

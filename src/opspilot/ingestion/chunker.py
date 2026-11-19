"""Structure-aware chunking for markdown runbooks.

Naive fixed-size chunking cuts procedures in half ("step 3" ends up in a
different chunk than "step 4"). Runbooks are already organised by headings, so we:

1. split the document on markdown headings (#, ##, ###), remembering the path
   of headings above each section ("Mitigation > Restart PgBouncer");
2. keep a section whole if it fits in `chunk_size` characters;
3. otherwise split it on paragraph boundaries, carrying `overlap` characters
   of the previous piece forward so a sentence that straddles the cut is not lost.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass

from opspilot.ingestion.loaders import Document
from opspilot.schemas import Chunk

_HEADING = re.compile(r"^(#{1,3})\s+(.+?)\s*#*\s*$")
_ID_NAMESPACE = uuid.UUID("6f1c9a52-3f7e-4d55-9a0e-0b1f4c2e9d11")


@dataclass
class _Section:
    path: list[str]
    lines: list[str]

    @property
    def text(self) -> str:
        return "\n".join(self.lines).strip()


def _split_sections(text: str) -> list[_Section]:
    sections: list[_Section] = []
    heading_stack: list[tuple[int, str]] = []
    current = _Section(path=[], lines=[])
    in_code_block = False

    for line in text.splitlines():
        if line.strip().startswith("```"):
            in_code_block = not in_code_block

        match = None if in_code_block else _HEADING.match(line)
        if match:
            if current.text:
                sections.append(current)
            level, title = len(match.group(1)), match.group(2).strip()
            # Pop headings at the same or deeper level, then push this one.
            heading_stack = [(lvl, t) for lvl, t in heading_stack if lvl < level]
            heading_stack.append((level, title))
            # The H1 is the document title, no need to repeat it in every path.
            path = [t for lvl, t in heading_stack if lvl > 1]
            current = _Section(path=path, lines=[])
        else:
            current.lines.append(line)

    if current.text:
        sections.append(current)
    return sections


def _split_long_text(text: str, size: int, overlap: int) -> list[str]:
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    pieces: list[str] = []
    buffer = ""

    for para in paragraphs:
        # A single giant paragraph (e.g. a PDF page with no blank lines): hard split.
        while len(para) > size:
            head, para = para[:size], para[size - overlap :]
            if buffer:
                pieces.append(buffer)
                buffer = ""
            pieces.append(head)

        candidate = f"{buffer}\n\n{para}" if buffer else para
        if len(candidate) <= size:
            buffer = candidate
            continue

        pieces.append(buffer)
        tail = buffer[-overlap:] if overlap else ""
        # Start the overlap on a word boundary so it reads naturally.
        if tail and " " in tail:
            tail = tail[tail.index(" ") + 1 :]
        buffer = f"{tail}\n\n{para}" if tail else para

    if buffer:
        pieces.append(buffer)
    return pieces


def chunk_id(source: str, index: int) -> str:
    """Deterministic ids mean re-ingesting a file overwrites its old points."""
    return str(uuid.uuid5(_ID_NAMESPACE, f"{source}::{index}"))


def chunk_document(doc: Document, chunk_size: int = 1200, overlap: int = 150) -> list[Chunk]:
    if overlap >= chunk_size:
        raise ValueError("overlap must be smaller than chunk_size")

    chunks: list[Chunk] = []
    for section in _split_sections(doc.text):
        section_name = " > ".join(section.path)
        for piece in _split_long_text(section.text, chunk_size, overlap):
            index = len(chunks)
            chunks.append(
                Chunk(
                    id=chunk_id(doc.source, index),
                    text=piece,
                    source=doc.source,
                    title=doc.title,
                    section=section_name,
                    doc_type=doc.doc_type,
                    service=doc.service,
                    severity=doc.severity,
                    tags=doc.tags,
                    chunk_index=index,
                )
            )
    return chunks

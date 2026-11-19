from __future__ import annotations

import pytest

from opspilot.ingestion.chunker import chunk_document, chunk_id
from opspilot.ingestion.loaders import Document, load_bytes, load_directory, split_front_matter

RUNBOOK = b"""---
title: Redis is full
service: cart-service
doc_type: runbook
severity: sev2
tags: [redis, oom]
---

# Redis is full

Intro paragraph.

## Symptoms

Writes fail with OOM.

## Mitigation

### Fix the policy

Run CONFIG SET.

```bash
# this is a comment, not a heading
redis-cli CONFIG SET maxmemory-policy volatile-lru
```

### Free memory

Use UNLINK.
"""


def test_front_matter_is_parsed():
    meta, body = split_front_matter(RUNBOOK.decode())
    assert meta["service"] == "cart-service"
    assert meta["tags"] == ["redis", "oom"]
    assert body.lstrip().startswith("# Redis is full")


def test_text_without_front_matter_is_untouched():
    meta, body = split_front_matter("# Just a heading\n\ntext")
    assert meta == {}
    assert body.startswith("# Just a heading")


def test_load_bytes_metadata():
    doc = load_bytes(RUNBOOK, "runbook-redis.md")
    assert doc.title == "Redis is full"
    assert doc.service == "cart-service"
    assert doc.doc_type == "runbook"
    assert doc.severity == "sev2"


def test_load_bytes_overrides_and_title_fallback():
    doc = load_bytes(
        b"no heading here, just text", "my-notes.txt", doc_type="ticket", service="Auth"
    )
    assert doc.title == "My notes"
    assert doc.doc_type == "ticket"
    assert doc.service == "auth"


def test_unsupported_extension():
    with pytest.raises(ValueError):
        load_bytes(b"x", "image.png")


def test_empty_file_rejected():
    with pytest.raises(ValueError):
        load_bytes(b"   \n ", "empty.md")


def test_chunks_follow_headings():
    doc = load_bytes(RUNBOOK, "runbook-redis.md")
    chunks = chunk_document(doc, chunk_size=1000, overlap=100)
    sections = [c.section for c in chunks]

    assert "Symptoms" in sections
    assert "Mitigation > Fix the policy" in sections
    assert "Mitigation > Free memory" in sections
    # The '#' line inside the code block must not have started a new section.
    fix = next(c for c in chunks if c.section == "Mitigation > Fix the policy")
    assert "redis-cli CONFIG SET" in fix.text
    assert all(c.service == "cart-service" for c in chunks)


def test_long_sections_are_split_with_overlap():
    paragraphs = "\n\n".join(f"Paragraph {i} " + "word " * 60 for i in range(10))
    doc = Document(text=f"# Title\n\n## Big\n\n{paragraphs}", source="big.md", title="Title")
    chunks = chunk_document(doc, chunk_size=500, overlap=80)

    assert len(chunks) > 3
    assert all(len(c.text) <= 500 + 80 + 2 for c in chunks)
    assert [c.chunk_index for c in chunks] == list(range(len(chunks)))


def test_chunk_ids_are_deterministic():
    assert chunk_id("a.md", 0) == chunk_id("a.md", 0)
    assert chunk_id("a.md", 0) != chunk_id("a.md", 1)


def test_embedding_text_has_context_header():
    doc = load_bytes(RUNBOOK, "runbook-redis.md")
    chunk = chunk_document(doc)[1]
    assert chunk.embedding_text().startswith("Redis is full > ")


def test_bundled_knowledge_base_loads(kb_dir):
    docs = load_directory(kb_dir)
    assert len(docs) >= 15
    for doc in docs:
        assert doc.doc_type in {"runbook", "postmortem", "ticket"}, doc.source
        assert doc.service != "general", doc.source
        assert chunk_document(doc), doc.source

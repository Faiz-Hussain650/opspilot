"""Lightweight guardrails.

Input side: catch the obvious prompt-injection attempts ("ignore previous
instructions", "reveal your system prompt", ...) before they reach the LLM.

Output side: runbooks and tickets sometimes contain things that should never
be pasted into a chat window - API keys, passwords, private keys. We redact them.

This is deliberately rule-based: it's fast, deterministic and easy to unit test.
In production you'd layer a classifier model on top, but rules catch a
surprising amount and never hallucinate.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_INJECTION_PATTERNS = [
    r"ignore (all |any )?(the )?(previous|prior|above|earlier) (instructions|prompts?|rules)",
    r"disregard (all |any )?(the )?(previous|prior|above|system) (instructions|prompts?|rules)",
    r"forget (everything|all|your) (you|instructions|rules)",
    r"(reveal|show|print|repeat|leak) (me )?(your|the) (system|hidden|initial) (prompt|instructions)",
    r"you are now (in )?(dan|developer mode|jailbreak)",
    r"act as (an? )?(unrestricted|unfiltered|jailbroken)",
    r"pretend (that )?you (have no|don't have any) (rules|restrictions|guidelines)",
    r"\bjailbreak\b",
]
_INJECTION_RE = re.compile("|".join(_INJECTION_PATTERNS), re.IGNORECASE)

# (label, pattern). Order matters: the more specific patterns go first.
_SECRET_PATTERNS: list[tuple[str, re.Pattern]] = [
    (
        "PRIVATE_KEY",
        re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]+?-----END [A-Z ]*PRIVATE KEY-----"),
    ),
    ("AWS_ACCESS_KEY", re.compile(r"\b(AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("GITHUB_TOKEN", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b")),
    ("GROQ_KEY", re.compile(r"\bgsk_[A-Za-z0-9]{20,}\b")),
    ("API_KEY", re.compile(r"\b(sk|pk|rk)_(live|test)_[A-Za-z0-9]{16,}\b")),
    ("JWT", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b")),
    (
        "PASSWORD",
        re.compile(r"(?i)\b(password|passwd|pwd|secret)\s*[:=]\s*['\"]?([^\s'\"`]{4,})"),
    ),
]

MAX_QUESTION_CHARS = 4000

REFUSAL_MESSAGE = (
    "I can't help with that request. It looks like an attempt to override my "
    "instructions. Ask me about an incident, alert or error and I'll dig through "
    "the runbooks for you."
)


@dataclass
class InputCheck:
    allowed: bool
    reason: str | None = None


@dataclass
class RedactionResult:
    text: str
    redacted: list[str] = field(default_factory=list)


def check_input(question: str) -> InputCheck:
    if len(question) > MAX_QUESTION_CHARS:
        return InputCheck(False, "question_too_long")
    if _INJECTION_RE.search(question):
        return InputCheck(False, "prompt_injection")
    return InputCheck(True)


def redact_secrets(text: str) -> RedactionResult:
    found: list[str] = []

    for label, pattern in _SECRET_PATTERNS:
        if label == "PASSWORD":

            def _keep_key(m: re.Match, _label=label) -> str:
                found.append(_label)
                return f"{m.group(1)}: [REDACTED_{_label}]"

            text = pattern.sub(_keep_key, text)
        else:

            def _replace(m: re.Match, _label=label) -> str:
                found.append(_label)
                return f"[REDACTED_{_label}]"

            text = pattern.sub(_replace, text)

    return RedactionResult(text=text, redacted=found)

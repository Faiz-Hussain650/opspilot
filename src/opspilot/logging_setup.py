"""Small logging helper. JSON-ish single-line logs are easier to grep in Docker."""

from __future__ import annotations

import logging
import sys

_FORMAT = "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"


def setup_logging(level: str = "INFO") -> None:
    root = logging.getLogger()
    if getattr(root, "_opspilot_configured", False):
        return

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(_FORMAT, datefmt="%H:%M:%S"))
    root.handlers = [handler]
    root.setLevel(level.upper())

    # These libraries are chatty at INFO and drown out our own logs.
    for noisy in ("httpx", "httpcore", "urllib3", "fastembed", "huggingface_hub"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    root._opspilot_configured = True  # type: ignore[attr-defined]

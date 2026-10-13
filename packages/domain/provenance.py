"""Resolve an identified source revision in checkout and built-package contexts."""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

_COMMIT = re.compile(r"(?:[a-f0-9]{40}|[a-f0-9]{64})\Z")


def source_code_sha() -> str | None:
    configured = os.environ.get("SIGNOFF_GIT_SHA")
    if configured and configured != "unknown":
        if not _COMMIT.fullmatch(configured.lower()):
            raise ValueError("SIGNOFF_GIT_SHA must identify a complete source commit")
        return configured.lower()
    try:
        source_root = Path(__file__).resolve().parents[2]
        checkout = subprocess.check_output(
            ["git", "rev-parse", "--show-toplevel"], cwd=source_root,
            text=True, stderr=subprocess.DEVNULL, timeout=5).strip()
        if Path(checkout).resolve() != source_root:
            return None
        result = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=source_root,
            text=True, stderr=subprocess.DEVNULL, timeout=5).strip().lower()
    except (OSError, subprocess.SubprocessError):
        return None
    return result if _COMMIT.fullmatch(result) else None

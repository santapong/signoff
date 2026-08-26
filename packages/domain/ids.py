"""Sortable unique IDs for Signoff entities (SDD §14, §21)."""
from __future__ import annotations

import os
import time

_B32 = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"  # Crockford-ish, no I/L/O/U


def _encode(value: int, length: int) -> str:
    out = []
    for _ in range(length):
        value, rem = divmod(value, 32)
        out.append(_B32[rem])
    return "".join(reversed(out))


def make_id(prefix: str) -> str:
    """Time-sortable ID with ms precision + entropy suffix.

    Example: ses_01JXF3QK2M-7Z4P
    """
    ms = int(time.time() * 1000)
    entropy = _encode(int.from_bytes(os.urandom(5), "big"), 4)
    return f"{prefix}_{_encode(ms, 8)}-{entropy}"


def make_session_id() -> str:
    return make_id("ses")


def make_experiment_id(category: str, number: int) -> str:
    """e.g. EXP-CYCLE-017 style experiment identifiers (SDD §89)."""
    return f"EXP-{category.upper()}-{number:03d}"


def parse_prefix(entity_id: str) -> str:
    return entity_id.split("_", 1)[0]

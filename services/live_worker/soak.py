"""Bounded statistics from actual live observations; no qualification inference."""

from __future__ import annotations

import hashlib
import json
import math
from collections import Counter


def canonical_hash(value: dict) -> str:
    snapshot = {key: item for key, item in value.items() if key != "receipt_hash"}
    return hashlib.sha256(
        json.dumps(snapshot, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


class SoakSamples:
    def __init__(self):
        self.histogram: Counter[int] = Counter()
        self.sample_count = 0
        self.max_ms = 0.0
        self.qualified_event_count = 0
        self.max_qualified_event_ms = 0.0
        self.invalid_samples = 0

    def observation(self, latency_ms: float) -> None:
        if not math.isfinite(latency_ms) or latency_ms < 0:
            self.invalid_samples += 1
            return
        # Finite bounded bins; values beyond the gate remain an overflow bucket.
        self.histogram[min(5001, math.ceil(latency_ms))] += 1
        self.sample_count += 1
        self.max_ms = max(self.max_ms, latency_ms)

    def event(self, latency_ms: float) -> None:
        self.qualified_event_count += 1
        self.max_qualified_event_ms = max(self.max_qualified_event_ms, latency_ms)

    def snapshot(self) -> dict:
        target = math.ceil(self.sample_count * 0.95)
        seen = 0
        p95 = None
        for boundary, count in sorted(self.histogram.items()):
            seen += count
            if seen >= target:
                p95 = float(boundary)
                break
        return {
            "clock_basis": "host_ingest_to_publication",
            "sample_count": self.sample_count,
            "p95_ms": p95,
            "max_ms": self.max_ms if self.sample_count else None,
            "qualified_event_count": self.qualified_event_count,
            "max_qualified_event_ms": self.max_qualified_event_ms
            if self.qualified_event_count
            else None,
            "invalid_samples": self.invalid_samples,
            "p95_basis": "conservative_1ms_histogram",
            "publication_basis": "shared_measurement_state_and_event_bus",
        }

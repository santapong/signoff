"""Deterministic nearest-centroid blob tracker (no randomness).

Blobs come from thresholding the frame-diff against a reference; tracks are
matched greedily by distance with a max-association radius.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class TrackedBlob:
    track_id: int
    cx: float
    cy: float
    area: int


class CentroidTracker:
    def __init__(self, max_distance: float = 40.0, min_area: int = 30):
        self.max_distance = max_distance
        self.min_area = min_area
        self._next_id = 0
        self._last_pos: dict[int, tuple[float, float]] = {}

    def process(self, diff: np.ndarray, thresh: int = 25) -> list[TrackedBlob]:
        mask = (diff.astype(np.int16) > thresh)
        blobs = _connected_blobs(mask, self.min_area)
        assigned: list[TrackedBlob] = []

        # greedy nearest matching, deterministic order by area desc then pos
        remaining_tracks = set(self._last_pos)
        for (cx, cy, area) in sorted(blobs, key=lambda b: (-b[2], b[0], b[1])):
            best_tid, best_d = None, None
            for tid in sorted(remaining_tracks):
                lx, ly = self._last_pos[tid]
                d = float(np.hypot(cx - lx, cy - ly))
                if best_d is None or d < best_d:
                    best_tid, best_d = tid, d
            if best_tid is not None and best_d is not None and best_d <= self.max_distance:
                remaining_tracks.discard(best_tid)
                assigned.append(TrackedBlob(best_tid, cx, cy, area))
            else:
                tid = self._next_id
                self._next_id += 1
                assigned.append(TrackedBlob(tid, cx, cy, area))

        # retire unmatched tracks, remember new positions
        new_last = {}
        for b in assigned:
            new_last[b.track_id] = (b.cx, b.cy)
        self._last_pos = new_last
        return assigned


def _connected_blobs(mask: np.ndarray, min_area: int) -> list[tuple[float, float, int]]:
    """4-connectivity flood fill -> [(cx, cy, area)]. Pure numpy/BFS."""
    h, w = mask.shape
    visited = np.zeros_like(mask, dtype=bool)
    out = []
    ys, xs = np.nonzero(mask & ~visited)
    coords = set(zip(ys.tolist(), xs.tolist()))
    seen: set[tuple[int, int]] = set()
    for start in sorted(coords):
        if start in seen:
            continue
        stack = [start]
        comp = []
        seen.add(start)
        while stack:
            y, x = stack.pop()
            comp.append((y, x))
            for ny, nx in ((y-1, x), (y+1, x), (y, x-1), (y, x+1)):
                if 0 <= ny < h and 0 <= nx < w and (ny, nx) in coords and (ny, nx) not in seen:
                    seen.add((ny, nx))
                    stack.append((ny, nx))
        if len(comp) >= min_area:
            ay = sum(p[0] for p in comp) / len(comp)
            ax = sum(p[1] for p in comp) / len(comp)
            out.append((ax, ay, len(comp)))
    return out

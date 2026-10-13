"""Deterministic scene-change signal (no MOG2, no randomness).

For each frame: mean absolute grayscale diff vs the previous frame, computed
over configured ROI rectangles. Idle frames produce ~0; activity spikes.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

from packages.cv.signoff_cv.source import Frame


@dataclass
class MotionSignal:
    frame_no: int
    ms: float
    score: float          # mean abs gray diff over all ROIs, 0..255
    per_roi: dict[str, float]


DEFAULT_ROIS = {"full": None}  # None = whole frame


class MotionDetector:
    def __init__(self,
                 rois: dict[str, tuple[int, int, int, int] | None] | None = None,
                 blur_ksize: int = 5):
        self.rois = rois or dict(DEFAULT_ROIS)
        self.blur_ksize = blur_ksize
        self._prev_gray: np.ndarray | None = None

    def reset(self) -> None:
        self._prev_gray = None

    def process(self, frame: Frame) -> MotionSignal:
        gray = _to_gray(frame.image)
        if self.blur_ksize > 1:
            gray = _box_blur(gray, self.blur_ksize)
        if self._prev_gray is None:
            per_roi = {name: 0.0 for name in self.rois}
            signal = MotionSignal(frame.frame_no, frame.ms, 0.0, per_roi)
        else:
            per_roi = {}
            for name, roi in self.rois.items():
                a = _crop(self._prev_gray, roi)
                b = _crop(gray, roi)
                per_roi[name] = float(np.mean(np.abs(
                    a.astype(np.int16) - b.astype(np.int16))))
            signal = MotionSignal(
                frame.frame_no, frame.ms,
                float(sum(per_roi.values()) / len(per_roi)), per_roi)
        self._prev_gray = gray
        return signal


def _to_gray(img: np.ndarray) -> np.ndarray:
    if img.ndim == 2:
        return img
    # BT.601 luma, integer-only for determinism (widen to avoid uint8 wrap)
    bgr = img.astype(np.uint32)
    return ((bgr[:, :, 0] * 29 + bgr[:, :, 1] * 150 + bgr[:, :, 2] * 77) >> 8
            ).astype(np.uint8)


def _box_blur(gray: np.ndarray, ksize: int) -> np.ndarray:
    pad = ksize // 2
    padded = np.pad(gray, pad, mode="edge").astype(np.int32)
    csum = padded.cumsum(0).cumsum(1)
    h, w = gray.shape
    k = ksize
    # integral-image box mean, integer arithmetic
    a = csum[:-k, :-k]
    b = csum[k:, :-k]
    c = csum[:-k, k:]
    d = csum[k:, k:]
    s = d - b - c + a
    return (s // (k * k)).astype(np.uint8)


def _crop(gray: np.ndarray, roi) -> np.ndarray:
    if roi is None:
        return gray
    x, y, w, h = roi
    return gray[y:y + h, x:x + w]


def signals_from_frames(frames: Sequence[Frame],
                        detector: MotionDetector | None = None) -> list[MotionSignal]:
    det = detector or MotionDetector()
    return [det.process(f) for f in frames]

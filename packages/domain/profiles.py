"""Validated immutable camera/task profiles for schema-v2 measurements."""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import tempfile
from copy import deepcopy
from pathlib import Path
from threading import RLock

_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z")
_LOCK = RLock()


def validate_identifier(value: str) -> str:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise ValueError("identifier must contain 1–64 letters, digits, underscores or hyphens")
    return value


def _integer(value, name: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ValueError(f"{name} must be an integer in [{minimum}, {maximum}]")
    return value


def _positive(value, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a positive finite number")
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be a positive finite number")
    return float(value)


def _roi(value, name: str) -> list[float]:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        raise ValueError(f"{name} must contain normalized [x,y,width,height]")
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) for v in value):
        raise ValueError(f"{name} coordinates must be finite numbers")
    x, y, w, h = map(float, value)
    if not all(math.isfinite(v) for v in (x, y, w, h)):
        raise ValueError(f"{name} coordinates must be finite")
    if x < 0 or y < 0 or w <= 0 or h <= 0 or x + w > 1 or y + h > 1:
        raise ValueError(f"{name} must be entirely inside the normalized image")
    return [x, y, w, h]


def validate_profile(profile: dict) -> dict:
    """Normalize a profile and bind its complete measurement configuration to a hash."""
    if not isinstance(profile, dict):
        raise ValueError("profile must be an object")
    profile_id = validate_identifier(profile.get("profile_id", ""))
    revision = _integer(profile.get("revision", 1), "revision", 1, 1_000_000)
    if profile.get("schema_version", 2) != 2:
        raise ValueError("profile schema_version must be 2")
    name = profile.get("name", profile_id)
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 120:
        raise ValueError("name must contain 1–120 characters")
    camera = profile.get("camera", {})
    detector = profile.get("detector", {})
    if not isinstance(camera, dict) or not isinstance(detector, dict):
        raise ValueError("camera and detector must be objects")
    camera = {
        "device_index": _integer(camera.get("device_index", 0), "device_index", 0, 255),
        "width": _integer(camera.get("width", 1280), "width", 64, 4096),
        "height": _integer(camera.get("height", 720), "height", 48, 4096),
        "fps": _integer(camera.get("fps", 30), "fps", 1, 120),
    }
    method = detector.get("method", "aruco")
    if method not in ("aruco", "motion"):
        raise ValueError("detector method must be aruco or motion")
    if detector.get("dictionary", "DICT_4X4_50") != "DICT_4X4_50":
        raise ValueError("v2 supports the DICT_4X4_50 marker dictionary")
    normalized = {
        "method": method,
        "dictionary": "DICT_4X4_50",
        "reference_marker_id": _integer(
            detector.get("reference_marker_id", 0), "reference_marker_id", 0, 49),
        "tool_marker_id": _integer(detector.get("tool_marker_id", 1), "tool_marker_id", 0, 49),
        "home": _roi(detector.get("home", [0.05, 0.05, 0.2, 0.2]), "home"),
        "pick": _roi(detector.get("pick", [0.1, 0.6, 0.2, 0.2]), "pick"),
        "place": _roi(detector.get("place", [0.65, 0.6, 0.2, 0.2]), "place"),
    }
    if normalized["reference_marker_id"] == normalized["tool_marker_id"]:
        raise ValueError("reference and tool marker IDs must differ")
    for key, default in {"home_dwell_ms": 200, "visit_dwell_ms": 100,
                         "visibility_gap_ms": 250, "max_cycle_ms": 60000}.items():
        normalized[key] = _positive(detector.get(key, default), key)
    # Motion parameters are explicit and hashed, even for the comparison baseline.
    for key, default in {"enter_score": 4, "exit_score": 2, "min_active_ms": 400,
                         "refractory_ms": 500, "merge_gap_ms": 300}.items():
        normalized[key] = _positive(detector.get(key, default), key)
    if normalized["enter_score"] <= normalized["exit_score"]:
        raise ValueError("enter_score must exceed exit_score")
    for a, b in (("home", "pick"), ("home", "place"), ("pick", "place")):
        ax, ay, aw, ah = normalized[a]
        bx, by, bw, bh = normalized[b]
        if min(ax + aw, bx + bw) > max(ax, bx) and min(ay + ah, by + bh) > max(ay, by):
            raise ValueError(f"{a} and {b} regions must not overlap")
    out = {"schema_version": 2, "profile_id": profile_id, "revision": revision,
           "name": name.strip(), "camera": camera, "detector": normalized}
    raw = json.dumps(out, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    out["profile_hash"] = hashlib.sha256(raw).hexdigest()
    supplied_hash = profile.get("profile_hash")
    if supplied_hash is not None and supplied_hash != out["profile_hash"]:
        raise ValueError("profile_hash does not match the measurement configuration")
    return out


class ProfileStore:
    """On-disk immutable revisions; conflicting writes cannot replace a revision."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _directory(self, profile_id: str) -> Path:
        path = self.root / validate_identifier(profile_id)
        if not path.resolve().is_relative_to(self.root.resolve()):
            raise ValueError("profile path is outside the store")
        return path

    def put(self, profile: dict) -> dict:
        candidate = deepcopy(profile)
        profile_id = validate_identifier(candidate.get("profile_id", ""))
        with _LOCK:
            directory = self._directory(profile_id)
            directory.mkdir(parents=True, exist_ok=True)
            if "revision" not in candidate:
                revisions = [int(p.stem[1:]) for p in directory.glob("r*.json")
                             if p.stem[1:].isdigit()]
                candidate["revision"] = max(revisions, default=0) + 1
            normalized = validate_profile(candidate)
            dest = directory / f"r{normalized['revision']:06d}.json"
            if dest.exists():
                old = json.loads(dest.read_text())
                if old != normalized:
                    raise ValueError("profile revision is immutable; create a new revision")
                return old
            text = json.dumps(normalized, indent=2, sort_keys=True, allow_nan=False)
            fd, tmp = tempfile.mkstemp(prefix=".profile-", dir=directory)
            try:
                with os.fdopen(fd, "w") as handle:
                    handle.write(text)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.link(tmp, dest)  # exclusive atomic publication on the same filesystem
            finally:
                Path(tmp).unlink(missing_ok=True)
            return normalized

    def get(self, profile_id: str, revision: int | None = None) -> dict:
        directory = self._directory(profile_id)
        if revision is None:
            candidates = sorted(directory.glob("r*.json"))
            if not candidates:
                raise FileNotFoundError(f"unknown profile {profile_id}")
            path = candidates[-1]
        else:
            _integer(revision, "revision", 1, 1_000_000)
            path = directory / f"r{revision:06d}.json"
        if not path.resolve().is_relative_to(self.root.resolve()):
            raise ValueError("profile path is outside the store")
        return validate_profile(json.loads(path.read_text()))

    def list(self) -> list[dict]:
        return [self.get(p.name) for p in sorted(self.root.iterdir())
                if p.is_dir() and _ID.fullmatch(p.name)]

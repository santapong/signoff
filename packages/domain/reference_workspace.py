"""Blinded original-frame references, independent of all prediction artifacts."""

from __future__ import annotations

import copy
import hashlib
import json
import threading
from pathlib import Path

from packages.cv.signoff_cv.source import VideoFileSource
from packages.domain.annotations import make_annotation
from packages.domain.ids import make_id
from packages.domain.profiles import validate_identifier
from services.analysis_worker.jobs import utcnow, verified_hash
from tools.annotate_reference import _media_sources, _write_immutable, read_frame_clock

_LOCK = threading.RLock()


def digest(value) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, allow_nan=False, separators=(",", ":")).encode()
    ).hexdigest()


def publish(path: Path, value: dict) -> None:
    _write_immutable(path, json.dumps(value, sort_keys=True, indent=2, allow_nan=False))


class ReferenceWorkspace:
    """Only session source metadata and source bytes enter this workspace."""

    def __init__(self, session_dir: Path):
        self.root = Path(session_dir).resolve()
        self.session = json.loads((self.root / "session.json").read_text())
        self.sid = validate_identifier(self.session["session_id"])
        name = self.session.get("video_file")
        if not isinstance(name, str) or Path(name).name != name:
            raise ValueError("session original must have a plain video filename")
        self.media = self.root / "video" / name
        if (
            self.media.is_symlink()
            or not self.media.is_file()
            or not self.media.resolve().is_relative_to(self.root)
        ):
            raise ValueError("session original is missing or outside its source directory")
        expected = self.session.get("video_sha256")
        if expected and verified_hash(self.media) != expected:
            raise ValueError("session original hash changed")

    def index(self) -> dict:
        source_hash = verified_hash(self.media)
        cache = self.root / "reference-index" / f"{source_hash}.json"
        with _LOCK:
            if cache.is_file():
                result = json.loads(cache.read_text())
                if not self.media.name.endswith(".manifest.json"):
                    selected_clock = next(
                        (
                            path
                            for path in (
                                self.media.with_suffix(".clock.jsonl"),
                                self.media.parent / "frame_index.jsonl",
                            )
                            if path.exists()
                        ),
                        None,
                    )
                    if (
                        selected_clock
                        and str(selected_clock.resolve())
                        not in result["provenance"]["source_sha256"]
                    ):
                        raise ValueError("source clock selection changed after indexing")
                for name, expected in result["provenance"]["source_sha256"].items():
                    path = Path(name)
                    if (
                        not path.resolve().is_relative_to(self.root)
                        or path.is_symlink()
                        or verified_hash(path) != expected
                    ):
                        raise ValueError("original source or clock changed after indexing")
                if result["index_hash"] != digest(result["frames"]):
                    raise ValueError("source frame index integrity failed")
                return result
            frames, provenance = read_frame_clock(self.media)
            if any(
                not Path(name).resolve().is_relative_to(self.root)
                for name in provenance["source_sha256"]
            ):
                raise ValueError("source evidence is outside the session")
            result = {
                "schema_version": 3,
                "session_id": self.sid,
                "blinded": True,
                "source_hash": source_hash,
                "index_hash": digest(list(frames.values())),
                "video_duration_ms": provenance["video_duration_ms"],
                "frames": list(frames.values()),
                "provenance": provenance,
            }
            publish(cache, result)
            return result

    def frame_jpeg(self, frame_id: int, source_hash: str) -> bytes:
        import cv2

        if isinstance(frame_id, bool) or not isinstance(frame_id, int) or frame_id < 0:
            raise ValueError("frame must be a nonnegative original frame identity")
        index = self.index()
        if source_hash != index["source_hash"]:
            raise ValueError("selected source revision is stale")
        selected = next((row for row in index["frames"] if row["frame"] == frame_id), None)
        if selected is None:
            raise ValueError("original frame does not exist")
        sources, _ = _media_sources(self.media, None)
        match = [
            (video, clock) for video, clock in sources if video.name == selected["source_file"]
        ]
        if len(match) != 1:
            raise ValueError("source segment cannot be resolved unambiguously")
        source = VideoFileSource(*match[0])
        try:
            for frame in source.frames():
                if frame.frame_no == frame_id:
                    if frame.ms != selected["ms"]:
                        raise ValueError("source clock changed")
                    ok, encoded = cv2.imencode(".jpg", frame.image)
                    if not ok:
                        raise ValueError("original frame cannot be displayed")
                    self.index()  # Recheck evidence before publishing the viewing derivative.
                    return encoded.tobytes()
        finally:
            source.close()
        raise ValueError("original frame disappeared")

    def get(self, revision_id: str) -> dict:
        path = self.root / "reference-revisions" / f"{validate_identifier(revision_id)}.json"
        result = json.loads(path.read_text())
        frozen = copy.deepcopy(result)
        frozen.pop("revision_hash", None)
        if result.get("revision_hash") != digest(frozen):
            raise ValueError("reference revision hash changed")
        self._check_source(result)
        return result

    def _check_source(self, revision: dict) -> None:
        source = self.index()
        if (
            revision["source_hash"] != source["source_hash"]
            or revision["index_hash"] != source["index_hash"]
        ):
            raise ValueError("reference revision no longer identifies its source clock")
        if (
            revision.get("reference_sha256")
            != hashlib.sha256(self.export_rows(revision["rows"]).encode()).hexdigest()
        ):
            raise ValueError("reference row hash changed")

    @staticmethod
    def export_rows(rows: list[dict]) -> str:
        return "".join(json.dumps(row, sort_keys=True, allow_nan=False) + "\n" for row in rows)

    def list(self) -> list[dict]:
        revisions = []
        for path in sorted((self.root / "reference-revisions").glob("*.json")):
            value = self.get(path.stem)
            summary = {
                key: value.get(key)
                for key in (
                    "revision_id",
                    "reviewer",
                    "reviewer_kind",
                    "kind",
                    "source_hash",
                    "index_hash",
                    "created_at",
                    "supersedes",
                    "independently_labeled",
                    "reference_sha256",
                )
            }
            summary.update(
                row_count=len(value["rows"]),
                export_url=f"/evidence/sessions/{self.sid}/references/{path.stem}/export",
            )
            revisions.append(summary)
        return revisions

    def create(self, body: dict) -> dict:
        index = self.index()
        reviewer = validate_identifier(body.get("reviewer", ""))
        kind = body.get("kind", "cycles")
        reviewer_kind = body.get("reviewer_kind")
        if kind not in ("cycles", "inactivity") or reviewer_kind not in ("human", "ai_assisted"):
            raise ValueError("reference kind and reviewer kind must be explicit")
        if body.get("independently_labeled") is not True:
            raise ValueError("independent labeling must be deliberately attested")
        if (
            body.get("source_hash") != index["source_hash"]
            or body.get("index_hash") != index["index_hash"]
        ):
            raise ValueError("source frame/clock selection is stale")
        rows = body.get("rows")
        if not isinstance(rows, list) or not rows or len(rows) > 10000:
            raise ValueError("reference rows must contain 1–10000 boundary selections")
        frames = {row["frame"]: row for row in index["frames"]}
        converted, identities = [], set()
        for row in rows:
            start, end = row.get("start_frame"), row.get("end_frame")
            if (
                isinstance(start, bool)
                or isinstance(end, bool)
                or not isinstance(start, int)
                or not isinstance(end, int)
                or start not in frames
                or end not in frames
            ):
                raise ValueError("boundaries must use existing original integer frame identities")
            cycle = row.get("cycle")
            if cycle in identities:
                raise ValueError("reference episode/cycle identities must be unique")
            annotation = make_annotation(
                self.sid,
                cycle,
                frames[start]["ms"],
                frames[end]["ms"],
                index["video_duration_ms"],
                outcome=row.get("outcome") or "UNKNOWN",
                failure_type=row.get("failure_type") or "NONE",
                annotator=reviewer,
            )
            # Reference identity is the source cycle number, never a detector identity.
            annotation.update(
                start_ms=frames[start]["ms"],
                end_ms=frames[end]["ms"],
                start_frame=start,
                end_frame=end,
                annotation_role="REFERENCE",
                reference_kind=kind,
                reviewer_kind=reviewer_kind,
            )
            annotation.pop("revision_id", None)
            converted.append(annotation)
            identities.add(cycle)
        converted.sort(key=lambda row: (row["start_ms"], row["cycle"]))
        if any(
            first["end_ms"] > second["start_ms"] for first, second in zip(converted, converted[1:])
        ):
            raise ValueError("reference intervals must not overlap")
        supersedes = body.get("supersedes")
        if supersedes:
            prior = self.get(supersedes)
            if prior["reviewer"] != reviewer or prior["kind"] != kind:
                raise ValueError("a reference revision may supersede only its own reviewer/kind")
        value = {
            "schema_version": 3,
            "revision_id": make_id("ref"),
            "session_id": self.sid,
            "reviewer": reviewer,
            "reviewer_kind": reviewer_kind,
            "kind": kind,
            "independently_labeled": True,
            "source_hash": index["source_hash"],
            "index_hash": index["index_hash"],
            "created_at": utcnow(),
            "supersedes": supersedes,
            "rows": converted,
            "reference_sha256": hashlib.sha256(self.export_rows(converted).encode()).hexdigest(),
        }
        value["revision_hash"] = digest(value)
        publish(self.root / "reference-revisions" / f"{value['revision_id']}.json", value)
        return value

    def adjudicate(self, body: dict) -> dict:
        reviewer = validate_identifier(body.get("reviewer", ""))
        reason = str(body.get("reason") or "").strip()
        reference = self.get(body.get("reference_revision", ""))
        ids = body.get("annotation_revisions", [])
        if not isinstance(ids, list) or len(set(ids)) < 2 or not reason:
            raise ValueError("adjudication needs independent revisions and a reason")
        annotations = [self.get(identity) for identity in ids]
        if (
            reference["reviewer_kind"] != "human"
            or reviewer != reference["reviewer"]
            or len({item["reviewer"] for item in annotations}) != len(annotations)
            or any(item["kind"] != reference["kind"] for item in annotations)
        ):
            raise ValueError(
                "adjudication must bind distinct reviewers and a named human reference"
            )
        if any(
            {row["cycle"] for row in item["rows"]} != {row["cycle"] for row in reference["rows"]}
            for item in annotations
        ):
            raise ValueError("adjudication must preserve corresponding reference identities")
        value = {
            "schema_version": 3,
            "adjudication_id": make_id("adj"),
            "session_id": self.sid,
            "reviewer": reviewer,
            "reason": reason,
            "kind": reference["kind"],
            "reference_revision": reference["revision_id"],
            "annotation_revisions": ids,
            "reference_sha256": reference["reference_sha256"],
            "annotation_sha256": [item["reference_sha256"] for item in annotations],
            "source_hash": reference["source_hash"],
            "created_at": utcnow(),
        }
        value["adjudication_hash"] = digest(value)
        publish(self.root / "reference-adjudications" / f"{value['adjudication_id']}.json", value)
        return value

    def adjudications(self) -> list[dict]:
        result = []
        for path in sorted((self.root / "reference-adjudications").glob("*.json")):
            row = json.loads(path.read_text())
            frozen = copy.deepcopy(row)
            frozen.pop("adjudication_hash", None)
            if row.get("adjudication_hash") != digest(frozen):
                raise ValueError("adjudication lineage hash changed")
            self.get(row["reference_revision"])
            for identity in row["annotation_revisions"]:
                self.get(identity)
            result.append(row)
        return result

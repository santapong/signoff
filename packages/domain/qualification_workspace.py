"""Approved local datasets, immutable freezes and one bounded qualification worker."""

from __future__ import annotations

import json
import shutil
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from packages.domain.ids import make_id
from packages.domain.profiles import ProfileStore, validate_identifier, validate_profile
from packages.domain.reference_workspace import ReferenceWorkspace, publish
from services.analysis_worker.jobs import atomic_json, utcnow
from tools.annotate_reference import _media_sources
from tools.qualify_dataset import CONTRACT, _path, _sha, evaluate_dataset, validate_frozen_inputs


class QualificationWorkspace:
    def __init__(self, data_dir: Path, approved_root: Path | None = None):
        self.data = Path(data_dir).resolve()
        self.root = self.data / "qualification"
        self.approved_root = (
            Path(approved_root).resolve() if approved_root else self.root / "approved"
        )
        self.lock = threading.RLock()
        self.executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="signoff-qualification"
        )
        self.active: str | None = None
        self.future = None
        self.closed = False
        for path in (self.root / "jobs").glob("*.json"):
            row = json.loads(path.read_text())
            if row.get("status") in ("queued", "running"):
                row.update(status="interrupted", error="qualification worker restarted")
                atomic_json(path, row)

    def directory(self, dataset_id: str) -> Path:
        return self.root / "datasets" / validate_identifier(dataset_id)

    def session(self, sid: str) -> ReferenceWorkspace:
        path = self.data / "sessions" / validate_identifier(sid)
        if path.is_symlink() or not path.resolve().is_relative_to(self.data):
            raise ValueError("session is outside the local data root")
        return ReferenceWorkspace(path)

    def create(self, body: dict) -> dict:
        identity = validate_identifier(body.get("dataset_id", ""))
        name = str(body.get("name") or identity).strip()
        if not name or len(name) > 120:
            raise ValueError("dataset name must contain 1–120 characters")
        source_kind = body.get("source_kind", "unknown")
        if source_kind not in {
            "physical_robot_station",
            "simulation",
            "public_robot_video",
            "manual_tabletop_rehearsal",
            "unknown",
        }:
            raise ValueError("source kind must be explicit")
        value = {
            "schema_version": 3,
            "dataset_id": identity,
            "name": name,
            "source_kind": source_kind,
            "capture_attested_by": body.get("capture_attested_by"),
            "protocol_id": validate_identifier(body.get("protocol_id", "camera-pick-place-v1")),
            "sessions": [],
            "soak_session_id": None,
            "created_at": utcnow(),
            "status": "draft",
        }
        with self.lock:
            path = self.directory(identity) / "draft.json"
            if path.exists():
                raise ValueError("dataset already exists")
            publish(path, value)
        return value

    def get(self, dataset_id: str) -> dict:
        directory = self.directory(dataset_id)
        value = json.loads((directory / "draft.json").read_text())
        pointer = directory / "selected-freeze.json"
        if pointer.is_file():
            selected = json.loads(pointer.read_text())
            if selected.get("draft_revision") == value.get("draft_revision"):
                value["freeze"] = selected
        value["approved_data_root"] = "configured local qualification workspace"
        return value

    def list(self) -> dict:
        rows = []
        for path in sorted((self.root / "datasets").glob("*/draft.json")):
            value = self.get(path.parent.name)
            rows.append(
                {
                    key: value.get(key)
                    for key in ("dataset_id", "name", "source_kind", "status", "freeze")
                }
            )
        candidates = []
        for path in sorted(self.approved_root.glob("*/manifest.json")):
            if path.is_symlink() or not path.resolve().is_relative_to(self.approved_root):
                continue
            identity = path.parent.name
            try:
                validate_identifier(identity)
                value = json.loads(path.read_text())
            except (ValueError, OSError):
                continue
            candidates.append(
                {
                    "candidate_id": identity,
                    "dataset_id": value.get("dataset_id"),
                    "source_kind": value.get("source_kind"),
                    "manifest_sha256": _sha(path),
                }
            )
        return {"datasets": rows, "approved_candidates": candidates}

    def assign(self, dataset_id: str, body: dict) -> dict:
        with self.lock:
            value = self.get(dataset_id)
            rows = body.get("sessions")
            if not isinstance(rows, list) or len(rows) > 1000:
                raise ValueError("session assignments must be a bounded array")
            seen = set()
            for row in rows:
                sid = validate_identifier(row.get("session_id", ""))
                if sid in seen or row.get("split") not in (
                    "development",
                    "validation",
                    "test",
                    "ood",
                ):
                    raise ValueError("session split must be valid and unique")
                seen.add(sid)
                workspace = self.session(sid)
                if workspace.get(row.get("reference_revision", ""))["kind"] != "cycles":
                    raise ValueError("cycle split assignments require cycle references")
                if row.get("inactivity_reference_revision"):
                    if workspace.get(row["inactivity_reference_revision"])["kind"] != "inactivity":
                        raise ValueError("inactivity assignments require inactivity references")
            soak = body.get("soak_session_id")
            if soak:
                self.session(soak)
            value.pop("freeze", None)
            value.pop("approved_data_root", None)
            value.update(
                sessions=rows,
                soak_session_id=soak,
                status="draft",
                updated_at=utcnow(),
                draft_revision=make_id("draft"),
            )
            publish(
                self.directory(dataset_id) / "draft-revisions" / f"{value['draft_revision']}.json",
                value,
            )
            atomic_json(self.directory(dataset_id) / "draft.json", value)
            return value

    def import_candidate(self, candidate_id: str) -> dict:
        """Import only a selected immediate child of the configured approved root."""
        identity = validate_identifier(candidate_id)
        source = self.approved_root / identity
        if source.is_symlink() or not source.resolve().is_relative_to(self.approved_root):
            raise ValueError("candidate is outside the configured approved data root")
        context = validate_frozen_inputs(source / "manifest.json", source)
        manifest = context["manifest"]
        self.create(
            {
                "dataset_id": manifest["dataset_id"],
                "source_kind": manifest["source_kind"],
                "capture_attested_by": manifest.get("capture_attested_by"),
                "protocol_id": manifest.get("protocol_id") or "camera-pick-place-v1",
            }
        )
        freeze_id = make_id("freeze")
        frozen = self.directory(manifest["dataset_id"]) / "freezes" / freeze_id
        files = {"manifest.json"}
        if manifest.get("protocol"):
            files.add(manifest["protocol"])
        if manifest.get("soak"):
            files.add(manifest["soak"])
        if manifest.get("soak_media"):
            files.add(manifest["soak_media"])
            for item in context["soak_sources"]:
                files.add(item["file"])
                files.add(item["clock_file"])
        for item in context["validated"]:
            row = item["row"]
            files.update(row[key] for key in ("video", "reference", "profile"))
            if row.get("clock"):
                files.add(row["clock"])
            for component in item["sources"]:
                files.add(component["file"])
                if component.get("clock_file"):
                    files.add(component["clock_file"])
            for prefix in ("", "inactivity_"):
                if row.get(prefix + "reference"):
                    files.add(row[prefix + "reference"])
                files.update(
                    annotation["file"] for annotation in row.get(prefix + "annotations", [])
                )
        for name in files:
            original = _path(source, name)
            destination = frozen / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(original, destination)
            if _sha(original) != _sha(destination):
                raise ValueError("approved candidate changed while importing")
        summary = {
            "freeze_id": freeze_id,
            "manifest_sha256": _sha(frozen / "manifest.json"),
            "created_at": utcnow(),
            "source_kind": manifest["source_kind"],
            "session_count": len(manifest["sessions"]),
        }
        atomic_json(self.directory(manifest["dataset_id"]) / "selected-freeze.json", summary)
        draft = self.get(manifest["dataset_id"])
        draft.pop("freeze", None)
        draft.pop("approved_data_root", None)
        draft.update(status="frozen", imported_candidate=identity)
        atomic_json(self.directory(manifest["dataset_id"]) / "draft.json", draft)
        return self.get(manifest["dataset_id"])

    def _references(
        self, workspace: ReferenceWorkspace, row: dict, frozen: Path, prefix: str, annotators: dict
    ) -> dict:
        field = "inactivity_" if prefix == "inactivity" else ""
        revision = workspace.get(row[field + "reference_revision"])
        path = frozen / "references" / f"{workspace.sid}-{prefix}-{revision['revision_id']}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(workspace.export_rows(revision["rows"]))
        result = {
            field + "reference": str(path.relative_to(frozen)),
            field + "reference_sha256": _sha(path),
        }
        annotation_ids = row.get(field + "annotation_revisions", [])
        if row["split"] in ("test", "ood") and annotation_ids:
            descriptors = []
            for identity in annotation_ids:
                annotation = workspace.get(identity)
                person, kind = annotation["reviewer"], annotation["reviewer_kind"]
                if person in annotators and annotators[person] != kind:
                    raise ValueError(
                        "reviewer kind must remain explicit and consistent in a freeze"
                    )
                annotators[person] = kind
                destination = frozen / "references" / f"{workspace.sid}-{prefix}-{identity}.jsonl"
                if not destination.exists():
                    destination.write_text(workspace.export_rows(annotation["rows"]))
                descriptors.append(
                    {
                        "annotator": person,
                        "file": str(destination.relative_to(frozen)),
                        "sha256": _sha(destination),
                        "independently_labeled": True,
                    }
                )
            adjudication_id = row.get(field + "adjudication_revision")
            lineage = next(
                (
                    item
                    for item in workspace.adjudications()
                    if item["adjudication_id"] == adjudication_id
                ),
                None,
            )
            if (
                lineage is None
                or lineage["reference_revision"] != revision["revision_id"]
                or set(lineage["annotation_revisions"]) != set(annotation_ids)
            ):
                raise ValueError("selected adjudication must bind exactly the selected references")
            annotators[lineage["reviewer"]] = "human"
            result.update(
                {
                    field + "annotations": descriptors,
                    field + "adjudication": {
                        "adjudicator": lineage["reviewer"],
                        "reason": lineage["reason"],
                        "reference_sha256": _sha(path),
                        "annotation_sha256": [item["sha256"] for item in descriptors],
                    },
                }
            )
        return result

    def freeze(self, dataset_id: str, body: dict) -> dict:
        with self.lock:
            draft = self.get(dataset_id)
            owner = validate_identifier(body.get("attested_by", ""))
            freeze_id = make_id("freeze")
            frozen = self.directory(dataset_id) / "freezes" / freeze_id
            frozen.mkdir(parents=True)
            try:
                rows, annotators = [], {}
                for assignment in draft["sessions"]:
                    workspace = self.session(assignment["session_id"])
                    index = workspace.index()
                    for name, expected in index["provenance"]["source_sha256"].items():
                        source = Path(name)
                        if _sha(source) != expected:
                            raise ValueError("source changed during qualification freeze")
                        destination = frozen / "sources" / workspace.sid / source.name
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copyfile(source, destination)
                        if _sha(destination) != expected:
                            raise ValueError("copied source hash differs")
                    media = frozen / "sources" / workspace.sid / workspace.media.name
                    if assignment.get("profile_id"):
                        profile = ProfileStore(self.data / "profiles").get(
                            assignment["profile_id"], assignment.get("profile_revision")
                        )
                    else:
                        profile = validate_profile(workspace.session.get("profile"))
                    profile_path = frozen / "profiles" / f"{profile['profile_hash']}.json"
                    if not profile_path.exists():
                        publish(profile_path, profile)
                    row = {
                        "session_id": workspace.sid,
                        "split": assignment["split"],
                        "video": str(media.relative_to(frozen)),
                        "video_sha256": _sha(media),
                        "profile": str(profile_path.relative_to(frozen)),
                        "profile_sha256": _sha(profile_path),
                        "profile_hash": profile["profile_hash"],
                    }
                    if not media.name.endswith(".manifest.json"):
                        clock = next(
                            (
                                path
                                for path in (
                                    media.with_suffix(".clock.jsonl"),
                                    media.parent / "frame_index.jsonl",
                                )
                                if path.is_file()
                            ),
                            None,
                        )
                        if clock:
                            row.update(
                                clock=str(clock.relative_to(frozen)), clock_sha256=_sha(clock)
                            )
                    row.update(
                        self._references(workspace, assignment, frozen, "cycles", annotators)
                    )
                    if assignment.get("inactivity_reference_revision"):
                        row.update(
                            self._references(
                                workspace, assignment, frozen, "inactivity", annotators
                            )
                        )
                    rows.append(row)
                protocol = {
                    "protocol_id": draft["protocol_id"],
                    "contract": CONTRACT,
                    "inactivity_accuracy": "descriptive; reviewed by owner",
                    "live_soak_required": True,
                }
                publish(frozen / "protocol.json", protocol)
                manifest = {
                    "schema_version": 2,
                    "dataset_id": dataset_id,
                    "source_kind": draft["source_kind"],
                    "capture_attested_by": draft.get("capture_attested_by") or owner,
                    "frozen_at": utcnow(),
                    "freeze_attested_by": owner,
                    "annotators": sorted(annotators),
                    "annotator_kinds": annotators,
                    "protocol_id": draft["protocol_id"],
                    "protocol": "protocol.json",
                    "protocol_sha256": _sha(frozen / "protocol.json"),
                    "sessions": rows,
                }
                if draft.get("soak_session_id"):
                    soak_workspace = self.session(draft["soak_session_id"])
                    soak_root = soak_workspace.root
                    soak = soak_root / "live-soak.json"
                    if not soak.is_file() or soak.is_symlink():
                        raise ValueError("selected live session has no persisted soak receipt")
                    shutil.copyfile(soak, frozen / "live-soak.json")
                    manifest.update(
                        soak="live-soak.json", soak_sha256=_sha(frozen / "live-soak.json")
                    )
                    sources, _ = _media_sources(soak_workspace.media, None)
                    originals = [soak_workspace.media]
                    originals.extend(
                        path
                        for video, clock in sources
                        for path in (video, clock)
                        if path is not None
                    )
                    for original in originals:
                        destination = frozen / "soak-source" / original.name
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copyfile(original, destination)
                        if _sha(original) != _sha(destination):
                            raise ValueError("live soak evidence changed while freezing")
                    copied = frozen / "soak-source" / soak_workspace.media.name
                    manifest.update(
                        soak_media=str(copied.relative_to(frozen)), soak_media_sha256=_sha(copied)
                    )
                publish(frozen / "manifest.json", manifest)
                summary = {
                    "freeze_id": freeze_id,
                    "draft_revision": draft.get("draft_revision"),
                    "manifest_sha256": _sha(frozen / "manifest.json"),
                    "created_at": manifest["frozen_at"],
                    "source_kind": draft["source_kind"],
                    "session_count": len(rows),
                }
                atomic_json(self.directory(dataset_id) / "selected-freeze.json", summary)
                draft.pop("approved_data_root", None)
                draft.pop("freeze", None)
                draft.update(status="frozen")
                atomic_json(self.directory(dataset_id) / "draft.json", draft)
                return summary
            except Exception:
                # Preserve source artifacts already copied, but never select a partial freeze.
                atomic_json(frozen / "freeze-error.json", {"status": "incomplete"})
                raise

    def submit(self, dataset_id: str) -> dict:
        with self.lock:
            if self.closed:
                raise ValueError("qualification worker is closed")
            draft = self.get(dataset_id)
            freeze = draft.get("freeze")
            if not freeze:
                raise ValueError("freeze the selected dataset before evaluation")
            if self.active:
                existing = self.job(self.active)
                if (
                    existing["dataset_id"] == dataset_id
                    and existing["freeze_id"] == freeze["freeze_id"]
                ):
                    return existing
                raise ValueError("one bounded qualification job is already running")
            identity = make_id("qual")
            row = {
                "schema_version": 3,
                "job_id": identity,
                "receipt_id": identity,
                "dataset_id": dataset_id,
                "freeze_id": freeze["freeze_id"],
                "status": "queued",
                "created_at": utcnow(),
                "manifest_sha256": freeze["manifest_sha256"],
            }
            atomic_json(self.root / "jobs" / f"{identity}.json", row)
            self.active = identity
            self.future = self.executor.submit(self._run, identity)
            return row

    def close(self, *, wait: bool = True) -> None:
        """Finish active qualification on orderly shutdown; cancel an unstarted job."""
        with self.lock:
            self.closed = True
            if self.future is not None and self.future.cancel() and self.active:
                row = self.job(self.active)
                row.update(
                    status="interrupted",
                    error="application stopped before qualification",
                    finished_at=utcnow(),
                )
                atomic_json(self.root / "jobs" / f"{self.active}.json", row)
                self.active = None
        # Never hold the state lock while waiting: _run uses it when publishing completion.
        self.executor.shutdown(wait=wait, cancel_futures=True)

    def _run(self, identity: str) -> None:
        row = self.job(identity)
        directory = self.directory(row["dataset_id"]) / "freezes" / row["freeze_id"]
        try:
            row.update(status="running")
            atomic_json(self.root / "jobs" / f"{identity}.json", row)
            if _sha(directory / "manifest.json") != row["manifest_sha256"]:
                raise ValueError("frozen manifest changed before evaluation")
            receipt = evaluate_dataset(directory / "manifest.json", directory)
            publish(self.root / "receipts" / f"{identity}.json", receipt)
            row.update(
                status="complete",
                qualification_status=receipt["qualification"]["status"],
                receipt_sha256=_sha(self.root / "receipts" / f"{identity}.json"),
            )
        except Exception as exc:
            row.update(status="failed", error=str(exc))
        finally:
            row["finished_at"] = utcnow()
            atomic_json(self.root / "jobs" / f"{identity}.json", row)
            with self.lock:
                self.active = None

    def job(self, identity: str) -> dict:
        return json.loads(
            (self.root / "jobs" / f"{validate_identifier(identity)}.json").read_text()
        )

    def receipt(self, identity: str) -> dict:
        row = self.job(identity)
        path = self.root / "receipts" / f"{identity}.json"
        if row.get("status") != "complete" or _sha(path) != row.get("receipt_sha256"):
            raise ValueError("qualification receipt is incomplete or changed")
        return {
            "receipt_id": identity,
            "sha256": _sha(path),
            "receipt": json.loads(path.read_text()),
            "manifest_sha256": row["manifest_sha256"],
            "download_url": f"/evidence/qualification/receipts/{identity}/export",
        }

    def receipts(self) -> list[dict]:
        rows = []
        for path in sorted((self.root / "jobs").glob("*.json")):
            job = self.job(path.stem)
            if job.get("status") == "complete":
                item = self.receipt(path.stem)
                rows.append(
                    {
                        "receipt_id": path.stem,
                        "dataset_id": job["dataset_id"],
                        "status": item["receipt"]["qualification"]["status"],
                        "sha256": item["sha256"],
                        "manifest_sha256": job["manifest_sha256"],
                        "source_kind": item["receipt"]["source_kind"],
                    }
                )
        return rows

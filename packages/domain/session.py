"""Observation session model and on-disk layout (SDD §11, §19)."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from packages.domain.ids import make_session_id


@dataclass
class CameraProfile:
    camera_id: str = "cam_lab_01"
    device_index: int = 0
    width: int = 1280
    height: int = 720
    fps: int = 30


@dataclass
class ObservationSession:
    experiment_id: str
    camera: CameraProfile = field(default_factory=CameraProfile)
    session_id: str = field(default_factory=make_session_id)
    started_at: str = field(
        default_factory=lambda: datetime.now(UTC).isoformat()
    )
    notes: str = ""
    video_files: list[str] = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2)

    @classmethod
    def from_json(cls, text: str) -> ObservationSession:
        d = json.loads(text)
        cam = CameraProfile(**d.pop("camera"))
        return cls(camera=cam, **d)


def session_dir(datasets_root: Path, session_id: str) -> Path:
    return datasets_root / "sessions" / session_id


def init_session_dirs(datasets_root: Path, session: ObservationSession) -> Path:
    """Create the canonical per-session tree and write the manifest."""
    sdir = session_dir(datasets_root, session.session_id)
    (sdir / "video").mkdir(parents=True, exist_ok=True)
    (sdir / "annotations").mkdir(exist_ok=True)
    manifest = sdir / "session.json"
    if not manifest.exists():
        manifest.write_text(session.to_json())
    return sdir

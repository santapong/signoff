#!/usr/bin/env python3
"""Import independent human reference boundaries without GUI or inference.

Watch the original recording externally in VLC/mpv, use frame stepping and
write a CSV with columns cycle,start_frame,end_frame,outcome,failure_type.
Frame numbers are zero-based original capture frame IDs (global across a
segmented recording), never FPS-derived times. Blank outcome means UNKNOWN;
blank failure_type means NONE. The end frame is the visible end boundary,
not an exclusive frame count. No detected cycles or proposed labels are read.

Example:
  python tools/annotate_reference.py recorded.mkv --csv alice.csv \
      --session ses-example --annotator alice --out references/alice.jsonl \
      --frame-map references/source-frames.csv

Use --map-only --frame-map source-frames.csv before drafting the human CSV.
--frame-map exports source frame IDs, segment-local frame IDs, source-clock times
and segment IDs so a
reviewer can relate the external player to capture evidence. It does not create
labels. Outputs are new immutable files; choose new paths for a new revision.
A decoded PTS clock is used only when no capture sidecar exists. Qualification
independence/adjudication must be attested separately by the dataset owner.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from packages.cv.signoff_cv.source import VideoFileSource  # noqa: E402
from packages.domain.annotations import make_annotation  # noqa: E402
from packages.domain.profiles import validate_identifier  # noqa: E402

CSV_COLUMNS = {"cycle", "start_frame", "end_frame", "outcome", "failure_type"}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _media_sources(media: Path, clock: Path | None) -> tuple[list[tuple[Path, Path | None]], dict]:
    if not media.is_file() or media.is_symlink():
        raise ValueError("media must be a regular file")
    if not media.name.endswith(".manifest.json"):
        return [(media, clock)], {}
    if clock is not None:
        raise ValueError("a recording manifest selects its own segment clocks")
    manifest = json.loads(media.read_text())
    if manifest.get("status") not in {"complete", "interrupted"}:
        raise ValueError("recording must be finalized before reference annotation")
    sources = []
    for segment in manifest.get("segments", []):
        resolved = []
        for key, digest_key in (("file", "sha256"), ("clock_file", "clock_sha256")):
            name = segment.get(key)
            if not isinstance(name, str) or Path(name).name != name or name in {".", ".."}:
                raise ValueError("manifest paths must be plain filenames")
            path = media.parent / name
            if path.is_symlink() or not path.is_file() or sha256(path) != segment.get(digest_key):
                raise ValueError("recording segment or clock integrity check failed")
            resolved.append(path)
        sources.append(tuple(resolved))
    if not sources:
        raise ValueError("recording has no finalized segments")
    return sources, manifest


def read_frame_clock(media: Path, clock: Path | None = None) -> tuple[dict[int, dict], dict]:
    """Decode source evidence only; preserve global frame IDs and actual times."""
    media = Path(media)
    sources, manifest = _media_sources(media, Path(clock) if clock is not None else None)
    frames = {}
    previous_ms = -1.0
    previous_frame = -1
    source_hashes = {str(media.resolve()): sha256(media)}
    clock_bases = set()
    durations = []
    for video, sidecar in sources:
        source = VideoFileSource(video, sidecar)
        try:
            source_hashes[str(video.resolve())] = sha256(video)
            if source.clock_path:
                source_hashes[str(source.clock_path.resolve())] = sha256(source.clock_path)
            for segment_frame, observation in enumerate(source.frames()):
                if (
                    observation.frame_no in frames
                    or observation.frame_no <= previous_frame
                    or not math.isfinite(observation.ms)
                    or observation.ms <= previous_ms
                ):
                    raise ValueError("recording must have increasing global frame IDs and times")
                previous_frame, previous_ms = observation.frame_no, observation.ms
                frames[observation.frame_no] = {
                    "frame": observation.frame_no,
                    "segment_frame": segment_frame,
                    "ms": observation.ms,
                    "segment_id": observation.segment_id,
                    "clock_basis": observation.clock_basis,
                    "source_file": video.name,
                    "quality_flags": list(observation.quality_flags),
                }
                clock_bases.add(observation.clock_basis)
            if source.duration_ms is not None:
                durations.append(source.duration_ms)
        finally:
            source.close()
    if not frames:
        raise ValueError("recording contains no frames")
    duration = max([previous_ms, *durations])
    if not math.isfinite(duration) or duration < previous_ms:
        raise ValueError("video duration must cover the finite source clock")
    if manifest.get("end_ms") is not None:
        duration = float(manifest["end_ms"])
        if not math.isfinite(duration) or duration < previous_ms:
            raise ValueError("recording duration precedes its last observation")
    if any(sha256(Path(name)) != digest for name, digest in source_hashes.items()):
        raise ValueError("source evidence changed while importing references")
    return frames, {
        "media_sha256": source_hashes[str(media.resolve())],
        "source_sha256": source_hashes,
        "clock_basis": sorted(clock_bases),
        "video_duration_ms": duration,
        "recording_status": manifest.get("status", "file"),
    }


def _write_immutable(path: Path, payload: str) -> None:
    """Publish fully written bytes exclusively; never append or replace reviews."""
    path = Path(path)
    if path.exists() or path.is_symlink():
        raise FileExistsError(f"annotation output already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".reference-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def import_reference(
    media: Path,
    csv_path: Path,
    *,
    session: str,
    annotator: str,
    out: Path,
    clock: Path | None = None,
    frame_map: Path | None = None,
) -> list[dict]:
    session, annotator = validate_identifier(session), validate_identifier(annotator)
    out, csv_path = Path(out), Path(csv_path)
    if out.exists() or out.is_symlink():
        raise FileExistsError(f"annotation output already exists: {out}")
    if frame_map is not None and Path(frame_map).resolve() == out.resolve():
        raise ValueError("frame map and annotation outputs must be separate")
    if frame_map is not None and (Path(frame_map).exists() or Path(frame_map).is_symlink()):
        raise FileExistsError(f"frame map output already exists: {frame_map}")
    frames, provenance = read_frame_clock(Path(media), clock)
    csv_hash = sha256(csv_path)
    annotations = []
    seen = set()
    with csv_path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if set(reader.fieldnames or []) != CSV_COLUMNS or len(reader.fieldnames or []) != len(
            CSV_COLUMNS
        ):
            raise ValueError("CSV columns must be cycle,start_frame,end_frame,outcome,failure_type")
        for line, row in enumerate(reader, start=2):
            try:
                if None in row:
                    raise ValueError("CSV row has extra undeclared cells")
                cycle = int(row["cycle"])
                start_frame, end_frame = int(row["start_frame"]), int(row["end_frame"])
                if cycle in seen:
                    raise ValueError("duplicate cycle number")
                if not 0 <= start_frame < end_frame:
                    raise ValueError("frame boundaries must be nonnegative and ordered")
                if start_frame not in frames or end_frame not in frames:
                    raise ValueError("reference boundary points to a missing source frame")
                start, end = frames[start_frame], frames[end_frame]
                outcome = (row.get("outcome") or "UNKNOWN").strip().upper() or "UNKNOWN"
                failure_type = (row.get("failure_type") or "NONE").strip().upper() or "NONE"
                annotation = make_annotation(
                    session,
                    cycle,
                    start["ms"],
                    end["ms"],
                    provenance["video_duration_ms"],
                    outcome,
                    failure_type,
                    annotator,
                )
                annotation.update(
                    start_ms=start["ms"],
                    end_ms=end["ms"],
                    start_frame=start_frame,
                    end_frame=end_frame,
                    annotation_role="REFERENCE",
                    annotation_source="HUMAN_AUTHORED_CSV",
                    reference_csv_sha256=csv_hash,
                    reference_media=provenance,
                    start_segment_id=start["segment_id"],
                    end_segment_id=end["segment_id"],
                )
                annotations.append(annotation)
                seen.add(cycle)
            except (TypeError, ValueError, KeyError) as exc:
                raise ValueError(f"invalid CSV row {line}: {exc}") from exc
    if not annotations:
        raise ValueError("CSV must contain at least one independently authored cycle")
    if sha256(csv_path) != csv_hash:
        raise ValueError("human reference CSV changed during import")
    if any(sha256(Path(name)) != digest for name, digest in provenance["source_sha256"].items()):
        raise ValueError("source evidence changed while parsing human references")
    if frame_map is not None:
        write_frame_map(Path(frame_map), frames)
    _write_immutable(
        out, "".join(json.dumps(row, sort_keys=True, allow_nan=False) + "\n" for row in annotations)
    )
    return annotations


def write_frame_map(path: Path, frames: dict[int, dict]) -> None:
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(
        buffer,
        fieldnames=(
            "frame",
            "segment_frame",
            "ms",
            "segment_id",
            "clock_basis",
            "source_file",
            "quality_flags",
        ),
    )
    writer.writeheader()
    writer.writerows(frames.values())
    _write_immutable(path, buffer.getvalue())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("media", type=Path)
    parser.add_argument("--csv", dest="csv_path", type=Path)
    parser.add_argument("--session")
    parser.add_argument("--annotator")
    parser.add_argument("--out", type=Path)
    parser.add_argument("--clock", type=Path)
    parser.add_argument("--frame-map", type=Path)
    parser.add_argument(
        "--map-only",
        action="store_true",
        help="export clocks before a human writes the reference CSV; no labels",
    )
    args = parser.parse_args(argv)
    if args.map_only and (
        args.frame_map is None or args.csv_path is not None or args.out is not None
    ):
        parser.error("--map-only requires --frame-map and cannot import --csv/--out")
    if not args.map_only and any(
        getattr(args, key) is None for key in ("csv_path", "session", "annotator", "out")
    ):
        parser.error("reference import requires --csv, --session, --annotator and --out")
    try:
        if args.map_only:
            frames, _ = read_frame_clock(args.media, args.clock)
            write_frame_map(args.frame_map, frames)
            print(f"exported {len(frames)} source frame clocks -> {args.frame_map}; no labels")
            return 0
        arguments = vars(args).copy()
        arguments.pop("map_only")
        annotations = import_reference(**arguments)
    except (ValueError, OSError) as exc:
        print(f"reference import failed: {exc}", file=sys.stderr)
        return 2
    print(f"imported {len(annotations)} human reference cycles -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

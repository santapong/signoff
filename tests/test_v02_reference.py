"""Headless independently authored frame references preserve source clocks."""

from __future__ import annotations

import csv
import json
from fractions import Fraction

import av
import numpy as np
import pytest

from packages.cv.signoff_cv.source import Frame
from services.video_worker.recorder import SegmentedRecorder
from tools.annotate_reference import import_reference, main, read_frame_clock


def vfr_media(tmp_path):
    path = tmp_path / "source.mkv"
    with av.open(str(path), mode="w") as container:
        stream = container.add_stream("ffv1", rate=10)
        stream.width, stream.height = 64, 64
        stream.pix_fmt = "bgr0"
        stream.time_base = Fraction(1, 1000)
        stream.codec_context.time_base = Fraction(1, 1000)
        for frame, pts in enumerate([0, 80, 260, 500]):
            image = np.full((64, 64, 3), frame * 30, np.uint8)
            observation = av.VideoFrame.from_ndarray(image, format="bgr24")
            observation.pts, observation.time_base = pts, Fraction(1, 1000)
            for packet in stream.encode(observation):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)
    return path


def reference_csv(tmp_path, rows=None):
    path = tmp_path / "human.csv"
    with path.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["cycle", "start_frame", "end_frame", "outcome", "failure_type"])
        writer.writerows(rows if rows is not None else [[1, 1, 3, "", ""]])
    return path


def test_decoded_vfr_boundaries_default_unknown_without_gui_or_inference(tmp_path, monkeypatch):
    import cv2

    import packages.cv.signoff_cv.pipeline as pipeline

    def forbidden(*args, **kwargs):
        raise AssertionError("reference import must not use GUI, camera or inference")

    monkeypatch.setattr(cv2, "imshow", forbidden)
    monkeypatch.setattr(cv2, "VideoCapture", forbidden)
    monkeypatch.setattr(pipeline, "analyze_video", forbidden)
    monkeypatch.setattr(pipeline.MeasurementPipeline, "__init__", forbidden)
    media = vfr_media(tmp_path)
    output = tmp_path / "reference.jsonl"
    rows = import_reference(
        media,
        reference_csv(tmp_path),
        session="session",
        annotator="alice",
        out=output,
        frame_map=tmp_path / "frames.csv",
    )
    assert rows[0]["start_ms"] == pytest.approx(80)
    assert rows[0]["end_ms"] == pytest.approx(500)
    assert rows[0]["outcome"] == "UNKNOWN"
    assert rows[0]["reference_media"]["clock_basis"] == ["decoded_pts"]
    assert "detected" not in output.read_text()
    assert rows[0]["annotation_role"] == "REFERENCE"
    assert "80.0" in (tmp_path / "frames.csv").read_text()


def test_authoritative_sidecar_times_and_immutable_output(tmp_path):
    media = vfr_media(tmp_path)
    clock = media.with_suffix(".clock.jsonl")
    clock.write_text(
        "".join(
            json.dumps({"frame": number, "ms": ms}) + "\n"
            for number, ms in enumerate([10, 145, 320, 800])
        )
    )
    output = tmp_path / "alice.jsonl"
    csv_path = reference_csv(tmp_path, [[1, 1, 3, "OK", "NONE"]])
    rows = import_reference(media, csv_path, session="session", annotator="alice", out=output)
    assert (rows[0]["start_ms"], rows[0]["end_ms"]) == (145, 800)
    assert rows[0]["reference_media"]["clock_basis"] == ["capture_sidecar"]
    original = output.read_bytes()
    with pytest.raises(FileExistsError):
        import_reference(media, csv_path, session="session", annotator="alice", out=output)
    assert output.read_bytes() == original


def test_manifest_boundaries_preserve_continuity_across_segments(tmp_path):
    recorder = SegmentedRecorder(
        tmp_path / "video",
        "session",
        fps=10,
        segment_ms=300,
        min_start_bytes=0,
        min_remaining_bytes=0,
        free_space=lambda: 10**6,
    )
    for number, ms in enumerate([0, 100, 240, 600, 910]):
        recorder.consume(
            Frame(
                number,
                ms,
                np.full((64, 64, 3), number * 20, np.uint8),
                session_id="session",
                clock_basis="fixture_capture",
            )
        )
    recorder.stop()
    media = recorder.manifest_path
    rows = import_reference(
        media,
        reference_csv(tmp_path, [[1, 1, 4, "", ""]]),
        session="session",
        annotator="bob",
        out=tmp_path / "bob.jsonl",
    )
    assert (rows[0]["start_ms"], rows[0]["end_ms"]) == (100, 910)
    assert rows[0]["start_segment_id"] != rows[0]["end_segment_id"]
    manifest = json.loads(media.read_text())
    assert len(manifest["segments"]) >= 2
    manifest["status"] = "recording"
    media.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="finalized"):
        read_frame_clock(media)


@pytest.mark.parametrize(
    "rows",
    [
        [[1, 1, 3, "", ""], [1, 1, 3, "", ""]],
        [[1, 3, 1, "", ""]],
        [[1, 1, 99, "", ""]],
        [[1, "nan", 3, "", ""]],
        [[1, 1, 3, "MAYBE", ""]],
        [[1, 1, 3, "FAILED", "NONE"]],
        [[0, 1, 3, "", ""]],
        [],
    ],
)
def test_invalid_csv_does_not_publish_references(tmp_path, rows):
    output = tmp_path / "reference.jsonl"
    with pytest.raises(ValueError):
        import_reference(
            vfr_media(tmp_path),
            reference_csv(tmp_path, rows),
            session="session",
            annotator="alice",
            out=output,
        )
    assert not output.exists()


def test_cli_and_invalid_clock(tmp_path):
    media = vfr_media(tmp_path)
    output = tmp_path / "reference.jsonl"
    assert (
        main(
            [
                str(media),
                "--csv",
                str(reference_csv(tmp_path)),
                "--session",
                "session",
                "--annotator",
                "alice",
                "--out",
                str(output),
            ]
        )
        == 0
    )
    media.with_suffix(".clock.jsonl").write_text('{"frame":0,"ms":NaN}\n')
    with pytest.raises(ValueError):
        read_frame_clock(media)


def test_clock_map_preparation_does_not_create_reference_labels(tmp_path):
    media = vfr_media(tmp_path)
    target = tmp_path / "source-frames.csv"
    assert main([str(media), "--map-only", "--frame-map", str(target)]) == 0
    with target.open(newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 4
    assert rows[1]["ms"] == "80.0"
    assert rows[1]["frame"] == rows[1]["segment_frame"] == "1"
    assert not list(tmp_path.glob("*.jsonl"))

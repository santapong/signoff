"""Bounded source JPEG caching preserves exact clocks and rejects changed evidence."""

from __future__ import annotations

import json
from fractions import Fraction

import av
import cv2
import numpy as np
import pytest

import packages.domain.reference_workspace as module
from packages.cv.signoff_cv.source import Frame, VideoFileSource, pixel_hash
from packages.domain.reference_workspace import ReferenceWorkspace, _JpegCache
from services.video_worker.recorder import SegmentedRecorder
from tools.annotate_reference import sha256


def make_session(tmp_path, *, sid="session", count=24):
    root = tmp_path / sid
    video = root / "video"
    video.mkdir(parents=True)
    media = video / "source.mkv"
    with (
        av.open(str(media), "w") as container,
        media.with_suffix(".clock.jsonl").open("w") as clock,
    ):
        stream = container.add_stream("ffv1", rate=10)
        stream.width, stream.height, stream.pix_fmt = 64, 64, "bgr0"
        stream.time_base = Fraction(1, 1000)
        stream.codec_context.time_base = Fraction(1, 1000)
        for number in range(count):
            image = np.full((64, 64, 3), number * 7 % 251, np.uint8)
            frame = av.VideoFrame.from_ndarray(image, format="bgr24")
            frame.pts, frame.time_base = number * 100, Fraction(1, 1000)
            for packet in stream.encode(frame):
                container.mux(packet)
            clock.write(
                json.dumps(
                    {
                        "frame": 100 + number * 2,
                        "segment_frame": number,
                        "ms": (number * number + number) * 25,
                        "clock_basis": "synthetic",
                        "pixel_sha256": pixel_hash(image),
                    }
                )
                + "\n"
            )
        for packet in stream.encode():
            container.mux(packet)
    (root / "session.json").write_text(
        json.dumps(
            {
                "session_id": sid,
                "video_file": media.name,
                "video_sha256": sha256(media),
                "source_kind": "simulation",
            }
        )
    )
    return ReferenceWorkspace(root)


@pytest.fixture(autouse=True)
def empty_cache(monkeypatch):
    cache = _JpegCache()
    monkeypatch.setattr(module, "_JPEG_CACHE", cache)
    return cache


def count_decodes(monkeypatch):
    calls = {"decoded": 0, "opened": 0, "closed": 0}
    frames, init, close = VideoFileSource.frames, VideoFileSource.__init__, VideoFileSource.close

    def counted(self):
        for frame in frames(self):
            calls["decoded"] += 1
            yield frame

    def opened(self, *args, **kwargs):
        calls["opened"] += 1
        init(self, *args, **kwargs)

    def closed(self):
        calls["closed"] += 1
        close(self)

    monkeypatch.setattr(VideoFileSource, "frames", counted)
    monkeypatch.setattr(VideoFileSource, "__init__", opened)
    monkeypatch.setattr(VideoFileSource, "close", closed)
    return calls


def expected_jpeg(number):
    ok, encoded = cv2.imencode(".jpg", np.full((64, 64, 3), number * 7 % 251, np.uint8))
    assert ok
    return encoded.tobytes()


def test_adjacent_real_frame_ids_use_one_decode_window_and_identical_jpegs(tmp_path, monkeypatch):
    workspace = make_session(tmp_path)
    index = workspace.index()
    observations = count_decodes(monkeypatch)
    # IDs have gaps and source-clock timing differs from container FPS.
    for number in (12, 13, 14):
        actual = workspace.frame_jpeg(100 + number * 2, index["source_hash"])
        assert actual == expected_jpeg(number)
    assert observations["decoded"] == 20
    assert observations["opened"] == observations["closed"] == 1
    assert workspace.index() == index
    with pytest.raises(ValueError, match="does not exist"):
        workspace.frame_jpeg(125, index["source_hash"])


def test_forward_window_resolves_actual_global_ids_across_segments(tmp_path, monkeypatch):
    root = tmp_path / "segmented"
    recorder = SegmentedRecorder(
        root / "video",
        "segmented",
        fps=10,
        segment_ms=300,
        min_start_bytes=0,
        min_remaining_bytes=0,
        free_space=lambda: 10**6,
    )
    for number in range(10):
        recorder.consume(
            Frame(
                50 + number * 3,
                number * 100,
                np.full((64, 64, 3), number * 7, np.uint8),
                session_id="segmented",
                clock_basis="synthetic",
            )
        )
    recorder.stop()
    (root / "session.json").write_text(
        json.dumps(
            {
                "session_id": "segmented",
                "video_file": recorder.manifest_path.name,
                "video_sha256": sha256(recorder.manifest_path),
                "source_kind": "simulation",
            }
        )
    )
    workspace = ReferenceWorkspace(root)
    index = workspace.index()
    counts = count_decodes(monkeypatch)
    for number in (2, 3, 4):
        assert workspace.frame_jpeg(50 + number * 3, index["source_hash"]) == expected_jpeg(number)
    assert counts["decoded"] == 10
    assert counts["opened"] == counts["closed"] == 4


@pytest.mark.parametrize("target", ["media", "clock", "index", "valid_rehashed_index"])
def test_warm_cached_frames_reject_tampered_evidence(tmp_path, target):
    workspace = make_session(tmp_path)
    index = workspace.index()
    workspace.frame_jpeg(124, index["source_hash"])
    if target == "media":
        workspace.media.write_bytes(workspace.media.read_bytes() + b"changed")
    elif target == "clock":
        clock = workspace.media.with_suffix(".clock.jsonl")
        clock.write_text(clock.read_text().replace('"synthetic"', '"changed"', 1))
    else:
        path = next((workspace.root / "reference-index").glob("*.json"))
        value = json.loads(path.read_text())
        value["frames"][0]["ms"] = 1
        if target == "valid_rehashed_index":
            value["index_hash"] = module.digest(value["frames"])
        path.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="changed|stale|integrity"):
        workspace.frame_jpeg(126, index["source_hash"])


def test_changed_identity_during_decode_publishes_no_cached_derivatives(
    tmp_path, monkeypatch, empty_cache
):
    workspace = make_session(tmp_path)
    index = workspace.index()
    original = VideoFileSource.frames
    rewritten = False

    def changed(self):
        nonlocal rewritten
        for frame in original(self):
            if frame.frame_no == 124 and not rewritten:
                rewritten = True
                # Even a still-valid parsed index must not replace the initial snapshot.
                path = next((workspace.root / "reference-index").glob("*.json"))
                path.write_text(path.read_text() + "\n")
            yield frame

    monkeypatch.setattr(VideoFileSource, "frames", changed)
    with pytest.raises(ValueError, match="identity changed while decoding"):
        workspace.frame_jpeg(124, index["source_hash"])
    assert empty_cache.size_bytes == 0 and not empty_cache.entries


def test_cache_byte_and_entry_caps_and_lru_eviction():
    cache = _JpegCache(max_entries=2, max_bytes=8)
    one, two, three = ("one",), ("two",), ("three",)
    cache.store({one: b"1234", two: b"5678"})
    assert cache.size_bytes == 8
    assert cache.get(one) == b"1234"
    cache.store({three: b"abcd"})
    assert cache.get(two) is None
    assert len(cache.entries) == 2 and cache.size_bytes == 8
    cache.store({("too-large",): b"x" * 9})
    assert cache.size_bytes == 8
    cache.store({one: b"1"})
    assert cache.size_bytes == 5


def test_cache_key_distinguishes_session_root_and_source(tmp_path, monkeypatch):
    first = make_session(tmp_path, sid="first")
    second = make_session(tmp_path, sid="second")
    # Separate Matroska encodes have random container identities. Copy exact
    # originals to exercise equal hashes in distinct session/root namespaces.
    second.media.write_bytes(first.media.read_bytes())
    second.media.with_suffix(".clock.jsonl").write_bytes(
        first.media.with_suffix(".clock.jsonl").read_bytes()
    )
    manifest = json.loads((second.root / "session.json").read_text())
    manifest["video_sha256"] = sha256(second.media)
    (second.root / "session.json").write_text(json.dumps(manifest))
    second = ReferenceWorkspace(second.root)
    one, two = first.index(), second.index()
    assert one["source_hash"] == two["source_hash"]
    first.frame_jpeg(124, one["source_hash"])
    counts = count_decodes(monkeypatch)
    assert second.frame_jpeg(124, two["source_hash"]) == expected_jpeg(12)
    assert counts["decoded"] == 20


def test_cache_rejection_leaves_unknown_reference_semantics_unchanged(tmp_path):
    workspace = make_session(tmp_path)
    index = workspace.index()
    workspace.frame_jpeg(124, index["source_hash"])
    row = workspace.create(
        {
            "reviewer": "human",
            "reviewer_kind": "human",
            "kind": "cycles",
            "independently_labeled": True,
            "source_hash": index["source_hash"],
            "index_hash": index["index_hash"],
            "rows": [{"cycle": 1, "start_frame": 124, "end_frame": 128}],
        }
    )["rows"][0]
    assert row["outcome"] == "UNKNOWN"
    assert row["start_ms"] == 3900 and row["end_ms"] == 5250

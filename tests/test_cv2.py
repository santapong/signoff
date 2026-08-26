"""Phase 4 CV v0.2 tests: tracking, phase split, failure hypotheses, review API."""
from __future__ import annotations

import numpy as np
import pytest

from packages.cv.signoff_cv.motion import MotionSignal
from packages.cv.signoff_cv.segmenter import DetectedInterval
from packages.cv.signoff_cv.states import (
    hypothesize_failure, needs_review, split_phases)
from packages.cv.signoff_cv.tracking import CentroidTracker


class TestTracker:
    def _blob_diff(self, shape=(120, 160), y0=40, x0=60):
        diff = np.zeros(shape, np.uint8)
        diff[y0:y0 + 30, x0:x0 + 30] = 200
        return diff

    def test_same_blob_keeps_id(self):
        t = CentroidTracker()
        a = t.process(self._blob_diff())
        b = t.process(self._blob_diff(y0=42))
        assert len(a) == 1 and len(b) == 1
        assert a[0].track_id == b[0].track_id

    def test_two_blobs_two_ids(self):
        t = CentroidTracker()
        diff = np.zeros((120, 160), np.uint8)
        diff[20:50, 10:40] = 200      # blob A top-left
        diff[80:110, 120:150] = 200   # blob B bottom-right
        blobs = t.process(diff)
        ids = {b.track_id for b in blobs}
        assert len(blobs) == 2 and len(ids) == 2

    def test_deterministic_across_runs(self):
        rng_pattern = [self._blob_diff(x0=60 + i * 5) for i in range(6)]
        t2 = CentroidTracker()
        ids_b = []
        for d in rng_pattern:
            res = t2.process(d)
            ids_b.append(res[0].track_id if res else None)
        # fresh tracker gives same id sequence as first run of tracker above
        t3 = CentroidTracker()
        ids_c = []
        for d in rng_pattern:
            res = t3.process(d)
            ids_c.append(res[0].track_id if res else None)
        assert ids_b == ids_c


class TestPhases:
    def test_four_phases_in_order(self):
        iv = DetectedInterval(1000.0, 3000.0)
        ph = split_phases(iv, [], cycle_index=1)
        names = [p["name"] for p in ph.phases]
        assert names == ["APPROACH", "GRASP", "TRANSFER", "PLACE_RETRACT"]
        assert ph.phases[0]["start_ms"] == 1000.0
        assert ph.phases[-1]["end_ms"] == 3000.0
        assert ph.basis == "INFERRED"
        # contiguous
        for a, b in zip(ph.phases, ph.phases[1:]):
            assert a["end_ms"] == b["start_ms"]


class TestFailures:
    def _ph(self):
        return split_phases(DetectedInterval(0.0, 2000.0), [], cycle_index=1)

    def test_grasp_miss_shape(self):
        # strong start/end, weak middle -> object never carried
        scores = [20, 18, 16, 2, 2, 2, 2, 15, 18, 20]
        hyps = hypothesize_failure(self._ph(), scores)
        types = [h.failure_type for h in hyps]
        assert "GRASP_MISS" in types
        gm = next(h for h in hyps if h.failure_type == "GRASP_MISS")
        assert 0 < gm.confidence <= 1 and gm.basis == "INFERRED"

    def test_interrupted_when_not_completed(self):
        hyps = hypothesize_failure(self._ph(), [15] * 10, completed=False)
        assert len(hyps) == 1
        assert hyps[0].failure_type == "INTERRUPTED"

    def test_slip_candidate_on_tail_spike(self):
        scores = [10, 10, 10, 10, 10, 10, 10, 40, 45, 50]
        hyps = hypothesize_failure(self._ph(), scores)
        assert any(h.failure_type == "OBJECT_SLIP" for h in hyps)

    def test_normal_cycle_no_hypotheses(self):
        scores = [12, 14, 13, 15, 14, 13, 12, 14, 13, 13]
        assert hypothesize_failure(self._ph(), scores) == []

    def test_needs_review_threshold(self):
        assert needs_review(0.5) is True
        assert needs_review(0.9) is False


class TestReviewAPI:
    @pytest.fixture()
    def client(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SIGNOFF_DATA_DIR", str(tmp_path / "data"))
        import importlib
        import apps.api.main as api_main
        importlib.reload(api_main)
        from fastapi.testclient import TestClient
        return TestClient(api_main.app)

    def _upload_and_analyze(self, client):
        import cv2
        path = "/tmp/_p4.mp4"
        w = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*"mp4v"), 30, (160, 120))
        for i in range(180):  # 6 s
            ms = i / 30 * 1000
            img = np.zeros((120, 160, 3), np.uint8)
            if any(s <= ms <= e for s, e in [(500., 2500.), (3500., 5500.)]):
                jx, jy = (i * 7) % 15, (i * 7) % 20
                img[40 + jy:80 + jy, 60 - jx:100 - jx] = (255,) * 3
            w.write(img)
        w.release()
        data = open(path, "rb").read()
        r = client.post("/sessions/upload",
                        files={"video": ("t.mp4", data, "video/mp4")})
        sid = r.json()["session_id"]
        ra = client.post(f"/sessions/{sid}/analyze")
        assert ra.status_code == 200, ra.text
        return sid

    def test_review_roundtrip(self, client):
        sid = self._upload_and_analyze(client)

        r = client.get(f"/sessions/{sid}/review")
        assert r.status_code == 200
        listing = r.json()
        assert listing["session_id"] == sid
        assert isinstance(listing["items"], list)

        # submit human correction for the detected cycles
        analysis = client.get(f"/sessions/{sid}/review").json()
        cycles_rows = [
            {"cycle": 1, "start_ms": 500.0, "end_ms": 2500.0,
             "outcome": "FAILED", "failure_type": "OBJECT_SLIP"},
            {"cycle": 2, "start_ms": 3500.0, "end_ms": 5500.0,
             "outcome": "OK"},
        ]
        r2 = client.post(f"/sessions/{sid}/review",
                         json={"annotator": "tester", "cycles": cycles_rows})
        assert r2.status_code == 200, r2.text
        assert r2.json()["written"] == 2

        # annotation file exists and validates per domain schema on read-back
        from packages.domain.annotations import read_annotations
        anns = read_annotations(r2.json()["file"] and
                                __import__("pathlib").Path(r2.json()["file"]))
        assert len(anns) == 2
        assert anns[0]["outcome"] == "FAILED"
        assert all(a["annotator"] == "tester" for a in anns)

    def test_review_rejects_bad_outcome(self, client):
        sid = self._upload_and_analyze(client)
        bad = {"annotator": "x", "cycles": [
            {"cycle": 1, "start_ms": 0.0, "end_ms": 10.0,
             "outcome": "MAYBE"}]}
        r = client.post(f"/sessions/{sid}/review", json=bad)
        assert r.status_code == 422

    def test_review_before_analyze_404(self, client):
        r = client.post("/sessions/upload",
                        files={"video": ("x.mp4", b"\x00" * 64, "video/mp4")})
        # upload may fail validation for junk bytes; only test review path if ok
        if r.status_code == 200:
            sid = r.json()["session_id"]
            assert client.get(f"/sessions/{sid}/review").status_code == 404

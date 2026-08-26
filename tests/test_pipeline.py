"""Phase 3 offline pipeline tests: metrics, report, API end-to-end."""
from __future__ import annotations

import json

import numpy as np
import pytest

from packages.metrics.signoff_metrics import (
    cycle_durations, detection_quality, idle_ratio, outcome_summary,
    session_metrics, throughput)
from packages.reporting.signoff_report import render_markdown, write_report


class TestMetrics:
    def test_cycle_durations_stats(self):
        cycles = [
            {"start_ms": 0.0, "end_ms": 1000.0},
            {"start_ms": 2000.0, "end_ms": 3000.0},
            {"start_ms": 4000.0, "end_ms": 5000.0},
        ]
        d = cycle_durations(cycles)
        assert d["n"] == 3
        assert d["mean_ms"] == 1000.0
        assert d["min_ms"] == 1000.0 and d["max_ms"] == 1000.0

    def test_percentile_p95(self):
        cycles = [{"start_ms": float(i * 100), "end_ms": float(i * 100 + 100 + i)}
                  for i in range(100)]
        d = cycle_durations(cycles)
        assert d["p95_ms"] >= d["p50_ms"]
        assert d["max_ms"] == 199.0

    def test_empty_is_safe(self):
        assert cycle_durations([]) == {"n": 0}
        assert throughput([])["cycles_per_min"] == 0.0
        assert outcome_summary([])["success_rate"] == 0.0

    def test_outcome_breakdown(self):
        cycles = [
            {"start_ms": 0, "end_ms": 1, "outcome": "OK"},
            {"start_ms": 2, "end_ms": 3, "outcome": "FAILED",
             "failure_type": "OBJECT_SLIP"},
            {"start_ms": 4, "end_ms": 5, "outcome": "FAILED",
             "failure_type": "OBJECT_SLIP"},
            {"start_ms": 6, "end_ms": 7, "outcome": "FAILED",
             "failure_type": "GRASP_MISS"},
        ]
        s = outcome_summary(cycles)
        assert s["total"] == 4 and s["ok"] == 1 and s["failed"] == 3
        assert s["success_rate"] == 0.25
        assert s["failure_breakdown"] == {"OBJECT_SLIP": 2, "GRASP_MISS": 1}

    def test_throughput_and_idle(self):
        cycles = [{"start_ms": 0, "end_ms": 30000},   # 30s cycle
                  {"start_ms": 60000, "end_ms": 90000}]
        t = throughput(cycles)                        # span 90s -> 2/1.5min
        assert t["cycles_per_min"] == pytest.approx(1.333, abs=0.01)
        ir = idle_ratio([{"start_ms": 30000, "end_ms": 60000}], 90000)
        assert ir["idle_ratio"] == pytest.approx(1 / 3, abs=0.001)

    def test_session_metrics_shape(self):
        m = session_metrics(
            gt_cycles=[{"start_ms": 0, "end_ms": 1000, "outcome": "OK"}],
            detected_cycles=[{"start_ms": 10, "end_ms": 990}],
            idle_intervals=[],
            vision_report={"f1": 1.0, "precision": 1.0, "recall": 1.0,
                           "true_positives": 1, "false_positives": 0,
                           "false_negatives": 0},
        )
        assert set(m) == {"cycle_durations", "outcomes", "throughput",
                          "idle", "detection"}
        assert m["detection"]["f1"] == 1.0

    def test_detection_quality_absent(self):
        assert detection_quality(None) == {"available": False}


class TestReport:
    def _metrics(self):
        return {
            "cycle_durations": {"n": 2, "mean_ms": 1500.0, "p50_ms": 1500.0,
                                "p95_ms": 1800.0, "min_ms": 1200.0,
                                "max_ms": 1800.0},
            "outcomes": {"total": 2, "ok": 1, "failed": 1, "success_rate": 0.5,
                         "failure_breakdown": {"OBJECT_SLIP": 1}},
            "throughput": {"cycles_per_min": 12.5},
            "idle": {"idle_ratio": 0.1, "idle_total_ms": 6000.0},
            "detection": {"available": True, "precision": 1.0, "recall": 1.0,
                          "f1": 1.0, "true_positives": 2,
                          "false_positives": 0, "false_negatives": 0},
        }

    def test_markdown_sections(self):
        md = render_markdown({"session_id": "ses_TEST", "experiment_id": "EXP-X"},
                             self._metrics(),
                             video_meta={"file": "v.mp4", "width": 1280,
                                         "height": 720, "fps": 30,
                                         "frames": 900,
                                         "sha256": "abc" * 20})
        for frag in ("ses_TEST", "## Cycles", "## Outcomes",
                     "50.0%", "`OBJECT_SLIP`: 1", "F1 **1.0**",
                     "SHA-256", "1280×720 @ 30 fps"):
            assert frag in md, frag

    def test_write_report_files(self, tmp_path):
        md_path, json_path = write_report(tmp_path / "r", {"session_id": "s"},
                                          self._metrics())
        assert md_path.exists() and json_path.exists()
        metrics_back = json.loads(json_path.read_text())
        assert metrics_back["outcomes"]["ok"] == 1


class TestAPI:
    @pytest.fixture()
    def client(self, tmp_path, monkeypatch):
        os_env = str(tmp_path / "data")
        monkeypatch.setenv("SIGNOFF_DATA_DIR", os_env)
        # re-import module so DATA_DIR picks up env
        import importlib
        import apps.api.main as api_main
        importlib.reload(api_main)
        from fastapi.testclient import TestClient
        return TestClient(api_main.app)

    def _mp4_bytes(self, cycles=((1000.0, 2500.0),), total_s=4.0, fps=30.0):
        import cv2
        path = "/tmp/_sig_test.mp4"
        w = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*"mp4v"), fps,
                            (160, 120))
        n = int(total_s * fps)
        for i in range(n):
            ms = i / fps * 1000.0
            img = np.zeros((120, 160, 3), np.uint8)
            if any(s <= ms <= e for s, e in cycles):
                jx, jy = (i * 7) % 15, (i * 7) % 20
                img[40 + jy:80 + jy, 60 - jx:100 - jx] = (255, 255, 255)
            w.write(img)
        w.release()
        return open(path, "rb").read()

    def test_upload_analyze_report_roundtrip(self, client):
        data = self._mp4_bytes(cycles=((500.0, 2500.0), (3500.0, 5500.0)),
                               total_s=6.0)
        r = client.post("/sessions/upload",
                        files={"video": ("test.mp4", data, "video/mp4")},
                        data={"notes": "api roundtrip"})
        assert r.status_code == 200, r.text
        sid = r.json()["session_id"]
        sha = r.json()["sha256"]

        r2 = client.post(f"/sessions/{sid}/analyze")
        assert r2.status_code == 200, r2.text
        body = r2.json()
        assert body["metrics"]["cycle_durations"]["n"] >= 1
        assert body["metrics"]["detection"]["available"] is True

        r3 = client.get(f"/sessions/{sid}/report")
        assert r3.status_code == 200
        assert "# Signoff Report" in r3.text and sid in r3.text

        # integrity: stored manifest sha matches uploaded bytes
        import apps.api.main as api_main
        manifest = json.loads((api_main.DATA_DIR / "sessions" / sid
                               / "session.json").read_text())
        assert manifest["video_sha256"] == sha

    def test_unknown_session_404(self, client):
        assert client.post("/sessions/ses_NOPE/analyze").status_code == 404
        assert client.get("/sessions/ses_NOPE/report").status_code == 404

    def test_health(self, client):
        assert client.get("/health").json()["status"] == "ok"

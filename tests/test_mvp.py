"""Phase 6 Commercial MVP acceptance tests (AC1-AC5)."""
from __future__ import annotations

import time

import numpy as np
import pytest


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("SIGNOFF_DATA_DIR", str(tmp_path / "data"))
    import importlib

    import apps.api.main as api_main
    importlib.reload(api_main)
    from fastapi.testclient import TestClient
    monkeypatch.delenv("SIGNOFF_TOKEN", raising=False)
    with TestClient(api_main.app) as tc:
        yield tc, api_main


def _demo_mp4() -> bytes:
    import cv2
    path = "/tmp/_p6.mp4"
    w = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*"mp4v"), 30, (160, 120))
    for i in range(180):  # 6 s, two activity windows
        ms = i / 30 * 1000
        img = np.zeros((120, 160, 3), np.uint8)
        if any(s <= ms <= e for s, e in [(500., 2500.), (3500., 5500.)]):
            jx, jy = (i * 7) % 15, (i * 7) % 20
            img[40 + jy:80 + jy, 60 - jx:100 - jx] = (255,) * 3
        w.write(img)
    w.release()
    return open(path, "rb").read()


def _wait_run(tc, url):
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        run = tc.get(url).json()
        if run["state"] in {"complete", "failed", "interrupted"}:
            assert run["state"] == "complete", run
            return run
        time.sleep(0.02)
    pytest.fail("analysis did not finish within 30 seconds")


class TestAC1_SessionListUI:
    def test_index_empty(self, client):
        tc, _ = client
        r = tc.get("/ui/")
        assert r.status_code == 200
        assert "No sessions" in r.text

    def test_sessions_json_endpoint(self, client):
        tc, _ = client
        data = _demo_mp4()
        r = tc.post("/sessions/upload",
                    files={"video": ("t.mp4", data, "video/mp4")})
        assert r.status_code == 201, r.text
        sid = r.json()["session_id"]
        listing = tc.get("/sessions").json()["sessions"]
        assert len(listing) == 1
        assert listing[0]["session_id"] == sid
        assert listing[0]["analyzed"] is False

    def test_nav_links_live_monitor(self, client):
        """AC3: live monitor reachable from UI navigation."""
        tc, _ = client
        text = tc.get("/ui/").text
        assert '/live/' in text


class TestAC2_BrowserOnlyLoop:
    def test_upload_analyze_report_via_ui(self, client):
        tc, _ = client
        data = _demo_mp4()

        # step 1: upload through the UI form endpoint
        r = tc.post("/ui/upload",
                    files={"video": ("pilot.mp4", data, "video/mp4")},
                    data={"notes": "pilot loop"},
                    follow_redirects=False)
        assert r.status_code == 200, r.text
        detail_url = r.json()["redirect"]
        sid = detail_url.rsplit("/", 1)[-1]

        # step 2: analyze via the API button equivalent
        ra = tc.post(f"/sessions/{sid}/analyze")
        assert ra.status_code == 202
        run = _wait_run(tc, ra.json()["status_url"])
        assert tc.get(run["artifacts"]["report.json"]).status_code == 200

        # step 3: view the report on the session page
        rd = tc.get(detail_url)
        assert rd.status_code == 200
        assert f"Session {sid}" in rd.text
        assert "pilot loop" in rd.text
        assert 'id="review-form"' in rd.text
        assert "# Signoff Report" in rd.text or "Metrics" in rd.text
        assert "Success rate" in rd.text or "report" in rd.text.lower()

    def test_unknown_session_404_page(self, client):
        tc, _ = client
        assert tc.get("/ui/sessions/ses_NOPE").status_code == 404


class TestAC3_LiveFromNav:
    def test_live_page_served(self, client):
        tc, _ = client
        r = tc.get("/live/")
        assert r.status_code == 200 and "Live Monitoring" in r.text


class TestAC4_PackageFiles:
    def test_dockerfile_and_compose_exist(self):
        from pathlib import Path
        root = Path(__file__).resolve().parents[1]
        dockerfile = root / "infra" / "Dockerfile"
        compose = root / "infra" / "docker-compose.yml"
        assert dockerfile.exists()
        assert compose.exists()
        dtxt = dockerfile.read_text()
        assert "uvicorn" in dtxt and "8600" in dtxt
        ctxt = compose.read_text()
        assert "signoff-data" in ctxt  # volume persists the dataset tree

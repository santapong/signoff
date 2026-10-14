"""Exceptional application lifecycle still closes every owned worker."""

from __future__ import annotations

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from services.analysis_worker.jobs import JobStore


@pytest.mark.parametrize("failure", [None, "body", "start", "camera", "analysis", "qualification"])
def test_lifespan_attempts_every_cleanup_and_offloads_blocking_calls(monkeypatch, failure):
    import apps.api.evidence as evidence
    import apps.api.live as live
    import apps.api.main as api

    calls = []
    main_thread = threading.get_ident()
    primary = RuntimeError(f"{failure} failed")

    def action(name):
        calls.append((name, threading.get_ident()))
        if name == failure:
            raise primary

    class Store:
        def start(self):
            action("start")

        def stop(self):
            action("analysis")

    def camera():
        action("camera")

    def qualification(root, *, wait):
        assert wait is True
        assert root == api.DATA_DIR
        action("qualification")

    monkeypatch.setattr(api, "jobs", lambda: Store())
    monkeypatch.setattr(live, "_stop_engine", camera)
    monkeypatch.setattr(evidence, "shutdown_workspaces", qualification)

    async def lifecycle():
        async with api.lifespan(api.app):
            if failure == "body":
                raise primary

    if failure is None:
        asyncio.run(lifecycle())
    else:
        with pytest.raises(RuntimeError) as raised:
            asyncio.run(lifecycle())
        assert raised.value is primary
    assert [name for name, _ in calls] == ["start", "camera", "analysis", "qualification"]
    assert calls[0][1] == main_thread
    assert all(thread != main_thread for name, thread in calls if name != "start")


@pytest.mark.parametrize("primary_phase", ["body", "start", None])
def test_cleanup_failures_remain_visible_alongside_original_failure(monkeypatch, primary_phase):
    import apps.api.evidence as evidence
    import apps.api.live as live
    import apps.api.main as api

    calls = []
    primary = ValueError(f"{primary_phase} failure remains visible")
    camera_error = RuntimeError("pending camera ownership")
    analysis_error = OSError("analysis close error")
    qualification_error = LookupError("qualification close error")

    class Store:
        def start(self):
            calls.append("start")
            if primary_phase == "start":
                raise primary

        def stop(self):
            calls.append("analysis")
            raise analysis_error

    def camera():
        calls.append("camera")
        raise camera_error

    def qualification(*args, **kwargs):
        calls.append("qualification")
        raise qualification_error

    monkeypatch.setattr(api, "jobs", lambda: Store())
    monkeypatch.setattr(live, "_stop_engine", camera)
    monkeypatch.setattr(evidence, "shutdown_workspaces", qualification)

    async def lifecycle():
        async with api.lifespan(api.app):
            if primary_phase == "body":
                raise primary

    with pytest.raises(ExceptionGroup) as raised:
        asyncio.run(lifecycle())
    expected = (camera_error, analysis_error, qualification_error)
    if primary_phase is not None:
        expected = (primary, *expected)
    assert raised.value.exceptions == expected
    assert calls == ["start", "camera", "analysis", "qualification"]


def test_failed_recovery_releases_process_lock_without_garbage_collection(tmp_path, monkeypatch):
    captured = []
    original = JobStore.recover
    failure = ValueError("recovery data invalid")

    def fail_recovery(store):
        captured.append(store)  # Retain the failed constructor deliberately.
        raise failure

    monkeypatch.setattr(JobStore, "recover", fail_recovery)
    with pytest.raises(ValueError) as raised:
        JobStore(tmp_path)
    assert raised.value is failure
    assert captured[0].worker_lock is None
    monkeypatch.setattr(JobStore, "recover", original)
    replacement = JobStore(tmp_path)
    try:
        assert replacement.worker_lock is not None
    finally:
        replacement.stop()


def test_concurrent_repeated_stop_releases_once_and_worker_join_does_not_deadlock(tmp_path):
    store = JobStore(tmp_path)
    handle = store.worker_lock
    store.start()
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(store.stop) for _ in range(8)]
        for future in futures:
            future.result(timeout=2)
    assert store.worker_lock is None and handle.closed
    assert not store.thread.is_alive()
    replacement = JobStore(tmp_path)
    try:
        handle = replacement.worker_lock
        replacement._acquire_worker_lock()
        assert replacement.worker_lock is handle
    finally:
        replacement.stop()
        replacement.stop()
    assert handle.closed

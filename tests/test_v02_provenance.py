"""Built artifacts must not depend on a Git checkout for revision evidence."""
import subprocess

import pytest

from packages.domain.provenance import source_code_sha
from services.analysis_worker.jobs import code_sha


def test_build_revision_is_used_without_git(monkeypatch):
    expected = "a" * 40
    monkeypatch.setenv("SIGNOFF_GIT_SHA", expected)
    monkeypatch.setattr(subprocess, "check_output", lambda *a, **k: pytest.fail("Git called"))
    assert source_code_sha() == expected
    assert code_sha() == expected


@pytest.mark.parametrize("value", ["abc", "not-a-commit", "x" * 40])
def test_malformed_build_revision_is_not_reported_as_identified(value, monkeypatch):
    monkeypatch.setenv("SIGNOFF_GIT_SHA", value)
    with pytest.raises(ValueError, match="complete"):
        source_code_sha()


def test_unavailable_revision_remains_unknown(monkeypatch):
    monkeypatch.delenv("SIGNOFF_GIT_SHA", raising=False)
    def unavailable(*args, **kwargs):
        raise FileNotFoundError("Git is not included in the runtime image")
    monkeypatch.setattr(subprocess, "check_output", unavailable)
    assert source_code_sha() is None

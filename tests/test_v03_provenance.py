"""A wheel or exported source must not inherit an unrelated parent Git identity."""
from pathlib import Path

from packages.domain import provenance


def test_export_inside_another_repository_remains_unidentified(monkeypatch):
    monkeypatch.delenv("SIGNOFF_GIT_SHA", raising=False)
    calls = []

    def parent_checkout(command, **kwargs):
        calls.append(command)
        return "/another/project\n"

    monkeypatch.setattr(provenance.subprocess, "check_output", parent_checkout)
    assert provenance.source_code_sha() is None
    assert calls == [["git", "rev-parse", "--show-toplevel"]]


def test_matching_source_checkout_identifies_its_commit(monkeypatch):
    monkeypatch.delenv("SIGNOFF_GIT_SHA", raising=False)
    root = Path(provenance.__file__).resolve().parents[2]
    monkeypatch.setattr(provenance.subprocess, "check_output",
                        lambda command, **kwargs: str(root) if command[-1] == "--show-toplevel"
                        else "a" * 40)
    assert provenance.source_code_sha() == "a" * 40

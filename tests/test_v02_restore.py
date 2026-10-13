"""Staged restore rejects untrusted archive structure before publication."""

import hashlib
import io
import json
import tarfile

import pytest

from tools.pilot_backup import backup, restore


def archive_with(tmp_path, entries, manifest=None):
    path = tmp_path / "archive.tar"
    with tarfile.open(path, "w") as archive:
        for name, payload, kind in entries:
            member = tarfile.TarInfo(name)
            if kind == "file":
                member.size = len(payload)
                archive.addfile(member, io.BytesIO(payload))
            else:
                member.type = tarfile.SYMTYPE if kind == "symlink" else tarfile.LNKTYPE
                member.linkname = payload
                archive.addfile(member)
        payload = json.dumps(manifest or {}).encode()
        member = tarfile.TarInfo("manifest.sha256.json")
        member.size = len(payload)
        archive.addfile(member, io.BytesIO(payload))
    return path


def test_restore_roundtrip_custom_target_and_preserves_existing(tmp_path):
    source = tmp_path / "source"
    (source / "runs" / "run-one").mkdir(parents=True)
    (source / "runs" / "run-one" / "run.json").write_text('{"state":"complete"}')
    archive = backup(source, tmp_path / "backups")
    target = tmp_path / "custom-data"
    restore(archive, target)
    assert (target / "runs" / "run-one" / "run.json").read_bytes() == (
        source / "runs" / "run-one" / "run.json"
    ).read_bytes()
    with pytest.raises(ValueError, match="new or empty"):
        restore(archive, target)


@pytest.mark.parametrize(
    "name,kind",
    [
        ("datasets/../../escaped", "file"),
        ("/tmp/absolute", "file"),
        ("unrelated/extra", "file"),
        ("datasets/link", "symlink"),
        ("datasets/hard", "hardlink"),
        ("datasets/undeclared", "file"),
    ],
)
def test_rejects_unsafe_members_without_writing_target(tmp_path, name, kind):
    archive = archive_with(
        tmp_path, [(name, b"unsafe" if kind == "file" else "../../escape", kind)]
    )
    target = tmp_path / "target"
    with pytest.raises(ValueError):
        restore(archive, target)
    assert not target.exists()
    assert not (tmp_path / "escaped").exists()


def test_rejects_payload_mismatch_and_duplicate(tmp_path):
    manifest = {"valid.json": hashlib.sha256(b"expected").hexdigest()}
    archive = archive_with(tmp_path, [("datasets/valid.json", b"different", "file")], manifest)
    with pytest.raises(ValueError, match="integrity"):
        restore(archive, tmp_path / "target")
    archive = archive_with(tmp_path, [("datasets/valid.json", b"expected", "file")] * 2, manifest)
    with pytest.raises(ValueError, match="duplicate"):
        restore(archive, tmp_path / "target")


def test_backup_rejects_links_and_in_tree_destination(tmp_path):
    source = tmp_path / "data"
    source.mkdir()
    (source / "link").symlink_to(tmp_path / "missing")
    with pytest.raises(ValueError, match="links"):
        backup(source, tmp_path / "backup")
    (source / "link").unlink()
    with pytest.raises(ValueError, match="outside"):
        backup(source, source / "backup")

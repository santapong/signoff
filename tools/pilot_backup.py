#!/usr/bin/env python
"""Manifest-verified backup and staged restore; no untrusted tar extraction.

Restore requires a new or empty target directory. Existing evidence is never
silently overwritten. Only regular files declared in the manifest are accepted.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import shutil
import tarfile
import tempfile
import time
from pathlib import Path, PurePosixPath


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _manifest(root: Path) -> dict:
    files = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError("dataset links are not supported in backups")
        if path.is_file():
            files[path.relative_to(root).as_posix()] = _sha256(path)
        elif not path.is_dir():
            raise ValueError("dataset special files are not supported")
    return files


def _safe_relative(name: str) -> str:
    path = PurePosixPath(name)
    if not isinstance(name, str) or not name or "\\" in name or path.is_absolute():
        raise ValueError("unsafe archive path")
    if any(part in {"", ".", ".."} for part in name.split("/")):
        raise ValueError("unsafe archive path")
    return str(path)


def backup(data_dir: Path, out_dir: Path) -> Path:
    data_dir, out_dir = Path(data_dir), Path(out_dir)
    if not data_dir.is_dir() or data_dir.is_symlink():
        raise ValueError("dataset directory does not exist or is a link")
    if out_dir.resolve().is_relative_to(data_dir.resolve()):
        raise ValueError("backup output must be outside the dataset tree")
    manifest = _manifest(data_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    fd, raw = tempfile.mkstemp(prefix=f"datasets_backup_{stamp}_", suffix=".tar.gz", dir=out_dir)
    os.close(fd)
    dest = Path(raw)
    try:
        with tarfile.open(dest, "w:gz") as archive:
            # Add individually so recursive tar traversal cannot follow new links.
            for name in manifest:
                path = data_dir / name
                if path.is_symlink() or _sha256(path) != manifest[name]:
                    raise ValueError("dataset changed while preparing backup")
                archive.add(path, arcname=f"datasets/{name}", recursive=False)
            payload = json.dumps(manifest, sort_keys=True).encode()
            metadata = tarfile.TarInfo("manifest.sha256.json")
            metadata.size = len(payload)
            archive.addfile(metadata, io.BytesIO(payload))
    except Exception:
        dest.unlink(missing_ok=True)
        raise
    print(f"backup written: {dest} ({len(manifest)} files)")
    return dest


def restore(archive: Path, data_dir: Path) -> None:
    archive, data_dir = Path(archive), Path(data_dir)
    data_dir.parent.mkdir(parents=True, exist_ok=True)
    if data_dir.is_symlink() or data_dir.parent.absolute() != data_dir.parent.resolve():
        raise ValueError("restore target must not traverse links")
    if data_dir.exists() and (not data_dir.is_dir() or any(data_dir.iterdir())):
        raise ValueError("restore target must be new or empty; preserve existing evidence first")
    maximum = int(os.environ.get("SIGNOFF_RESTORE_MAX_BYTES", str(100 * 1024**3)))
    with tempfile.TemporaryDirectory(prefix=".signoff-restore-", dir=data_dir.parent) as temporary:
        staging = Path(temporary) / "datasets"
        staging.mkdir()
        with tarfile.open(archive) as source:
            members = source.getmembers()
            names = [member.name.rstrip("/") for member in members]
            if len(names) != len(set(names)):
                raise ValueError("archive contains duplicate members")
            manifests = [member for member in members if member.name == "manifest.sha256.json"]
            if len(manifests) != 1 or not manifests[0].isfile() or manifests[0].size > 16 * 1024**2:
                raise ValueError("archive requires one bounded regular manifest")
            handle = source.extractfile(manifests[0])
            if handle is None:
                raise ValueError("archive manifest is unreadable")
            manifest = json.load(handle)
            if not isinstance(manifest, dict):
                raise ValueError("manifest must be a path-to-SHA256 object")
            for name, digest in manifest.items():
                _safe_relative(name)
                if (
                    not isinstance(digest, str)
                    or len(digest) != 64
                    or any(char not in "0123456789abcdef" for char in digest)
                ):
                    raise ValueError("invalid manifest hash")
            expected = {f"datasets/{name}" for name in manifest}
            allowed_dirs = {"datasets"}
            for name in expected:
                allowed_dirs.update(
                    str(parent) for parent in PurePosixPath(name).parents if str(parent) != "."
                )
            total = 0
            actual_files = set()
            for member in members:
                name = member.name.rstrip("/")
                _safe_relative(name)
                if member.name == "manifest.sha256.json":
                    continue
                if member.isdir() and name in allowed_dirs:
                    continue
                if not member.isfile() or name not in expected:
                    raise ValueError("archive contains a link, special file or undeclared member")
                total += member.size
                if total > maximum:
                    raise ValueError("archive exceeds configured restore byte limit")
                actual_files.add(name)
            if actual_files != expected:
                raise ValueError("archive files do not exactly match manifest")
            for member in members:
                if not member.isfile() or member.name == "manifest.sha256.json":
                    continue
                target = staging / member.name.removeprefix("datasets/")
                target.parent.mkdir(parents=True, exist_ok=True)
                input_file = source.extractfile(member)
                if input_file is None:
                    raise ValueError("archive member unreadable")
                with input_file, target.open("xb") as output:
                    shutil.copyfileobj(input_file, output, length=1 << 20)
            if _manifest(staging) != manifest:
                raise ValueError("restore integrity check failed")
        if data_dir.exists():
            data_dir.rmdir()
        staging.rename(data_dir)
    print(f"restore verified: {len(manifest)} files byte-identical")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    backup_cmd = sub.add_parser("backup")
    backup_cmd.add_argument("--data-dir", default="datasets")
    backup_cmd.add_argument("--out", default="backups")
    restore_cmd = sub.add_parser("restore")
    restore_cmd.add_argument("archive")
    restore_cmd.add_argument("--data-dir", default="datasets")
    args = parser.parse_args()
    if args.cmd == "backup":
        backup(Path(args.data_dir), Path(args.out))
    else:
        restore(Path(args.archive), Path(args.data_dir))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python
"""Pilot backup/restore: archive the dataset tree; verify on restore (AC3).

Usage:
  python tools/pilot_backup.py backup [--data-dir datasets] [--out DIR]
  python tools/pilot_backup.py restore ARCHIVE [--data-dir datasets]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tarfile
import tempfile
import time
from pathlib import Path


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _manifest(root: Path) -> dict:
    return {str(p.relative_to(root)): _sha256(p)
            for p in sorted(root.rglob("*")) if p.is_file()}


def backup(data_dir: Path, out_dir: Path) -> Path:
    data_dir = Path(data_dir)
    if not data_dir.exists():
        sys.exit(f"error: {data_dir} does not exist")
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    dest = out_dir / f"datasets_backup_{ts}.tar.gz"
    manifest = _manifest(data_dir)
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as tf:
        json.dump(manifest, tf, indent=2)
        manifest_path = tf.name
    with tarfile.open(dest, "w:gz") as tar:
        tar.add(data_dir, arcname="datasets")
        tar.add(manifest_path, arcname="manifest.sha256.json")
    Path(manifest_path).unlink(missing_ok=True)
    print(f"backup written: {dest} ({len(manifest)} files)")
    return dest


def restore(archive: Path, data_dir: Path) -> None:
    archive, data_dir = Path(archive), Path(data_dir)
    parent = data_dir.parent
    with tarfile.open(archive) as tar:
        names = tar.getnames()
        manifest_name = next((n for n in names
                              if n.endswith("manifest.sha256.json")), None)
        if not manifest_name:
            sys.exit("error: archive has no manifest")
        import io
        fh = tar.extractfile(manifest_name)
        manifest = json.load(fh) if fh else {}
        members = [m for m in tar.getmembers()
                   if m.name.startswith("./datasets/") or "datasets/" in m.name]
        tar.extractall(parent)

    restored_root = parent / "datasets"
    actual = _manifest(restored_root)
    mismatches = [f for f, sha in manifest.items()
                  if f != "manifest.sha256.json" and actual.get(f) != sha]
    if mismatches:
        sys.exit(f"RESTORE FAILED: {len(mismatches)} files differ: "
                 f"{mismatches[:5]}")
    print(f"restore verified: {len(actual)} files byte-identical")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("backup")
    b.add_argument("--data-dir", default="datasets")
    b.add_argument("--out", default="backups")
    r = sub.add_parser("restore")
    r.add_argument("archive")
    r.add_argument("--data-dir", default="datasets")
    args = ap.parse_args()
    if args.cmd == "backup":
        backup(Path(args.data_dir), Path(args.out))
    else:
        restore(Path(args.archive), Path(args.data_dir))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

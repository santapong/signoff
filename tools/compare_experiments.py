#!/usr/bin/env python
"""Compare frozen Signoff reports under one declared camera protocol.

Input JSON: protocol_id, protocol_hash, entries containing report_path,
experiment_id, controller_label and/or policy_label. Paths are relative to the
manifest directory. No videos, annotations, camera or robot are accessed.

Usage: python tools/compare_experiments.py experiments.json --out data/comparison
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from packages.domain.comparison import build_comparison, write_comparison  # noqa: E402


def _label(value, field: str, required: bool = False) -> str:
    if value is None and not required:
        return ""
    if not isinstance(value, str) or (required and not value.strip()):
        raise ValueError(
            f"{field} must be a nonempty string" if required else f"{field} must be a string"
        )
    return value.strip()


def compare_manifest(manifest_path: Path, output_dir: Path) -> tuple[dict, dict[str, Path]]:
    manifest_path = Path(manifest_path).resolve()
    manifest = json.loads(manifest_path.read_text())
    if not isinstance(manifest, dict):
        raise ValueError("experiment manifest must be an object")
    protocol_id = _label(manifest.get("protocol_id"), "protocol_id", True)
    protocol_hash = _label(manifest.get("protocol_hash"), "protocol_hash", True)
    rows = manifest.get("entries")
    if not isinstance(rows, list) or len(rows) < 2:
        raise ValueError("experiment manifest requires at least two entries")
    root = manifest_path.parent
    entries = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("each experiment entry must be an object")
        relative = row.get("report_path")
        if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
            raise ValueError("report_path must be relative to the manifest directory")
        report_path = (root / relative).resolve()
        if not report_path.is_relative_to(root) or not report_path.is_file():
            raise ValueError("report_path is missing or escapes the manifest directory")
        report = json.loads(report_path.read_text())
        if not isinstance(report, dict):
            raise ValueError("a measurement report must be an object")
        # Existing report protocol declarations cannot be contradicted by a new manifest.
        provenance = report.get("provenance", {})
        if not isinstance(provenance, dict):
            raise ValueError("report provenance must be an object")
        for field, expected in (("protocol_id", protocol_id), ("protocol_hash", protocol_hash)):
            if field in provenance and provenance[field] != expected:
                raise ValueError(f"report {field} differs from comparison protocol")
        entries.append(
            {
                "report": report,
                "experiment_id": _label(row.get("experiment_id"), "experiment_id", True),
                "controller_label": _label(row.get("controller_label"), "controller_label"),
                "policy_label": _label(row.get("policy_label"), "policy_label"),
                "protocol_id": row.get("protocol_id", protocol_id),
                "protocol_hash": row.get("protocol_hash", protocol_hash),
            }
        )
    comparison = build_comparison(entries, protocol_id=protocol_id, protocol_hash=protocol_hash)
    return comparison, write_comparison(Path(output_dir), comparison)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    try:
        comparison, paths = compare_manifest(args.manifest, args.out)
    except (ValueError, KeyError, TypeError, OSError) as exc:
        print(f"comparison rejected: {exc}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "status": comparison["status"],
                "comparison_hash": comparison["comparison_hash"],
                "outputs": {key: str(path.resolve()) for key, path in paths.items()},
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

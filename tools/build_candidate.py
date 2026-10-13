"""Build an identified software candidate without publishing or changing release gates."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import tomllib
from pathlib import Path


def build(out: Path) -> dict:
    root = Path(__file__).resolve().parents[1]
    dirty = subprocess.check_output(["git", "status", "--porcelain"], cwd=root, text=True)
    if dirty.strip():
        raise ValueError("commit the candidate source before building identified artifacts")
    code = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    tree = subprocess.check_output(["git", "rev-parse", "HEAD^{tree}"], cwd=root,
                                   text=True).strip()
    project = tomllib.loads((root / "pyproject.toml").read_text())["project"]
    version = project["version"]
    match = re.fullmatch(r"(\d+\.\d+\.\d+)rc(\d+)", version)
    if not match:
        raise ValueError("this builder requires an explicit release-candidate package version")
    display = f"{match[1]}-rc.{match[2]}"
    out = out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    if any(out.iterdir()):
        raise ValueError("candidate destination must be empty; preserve previous artifacts")
    source = out / f"signoff-{display}-source.zip"
    subprocess.run(["git", "archive", "--format=zip", "HEAD", "--output", str(source)],
                   cwd=root, check=True)
    with (out / "wheel-build.log").open("w") as log:
        subprocess.run(["uv", "build", "--wheel", "--offline", "--no-build-isolation",
                        "--python", sys.executable, "--out-dir", str(out)], cwd=root,
                       stdout=log, stderr=subprocess.STDOUT, check=True)
    wheels = list(out.glob("*.whl"))
    if len(wheels) != 1:
        raise ValueError("exactly one wheel is required")
    artifacts = {}
    for path in (source, wheels[0]):
        artifacts[path.name] = {"sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                                "bytes": path.stat().st_size}
    manifest = {"version": display, "package_version": version, "source_commit": code,
                "source_tree": tree, "artifacts": artifacts,
                "release_channel": "software_candidate", "production_status": "PENDING",
                "qualification": "requires actual station, soak and owner receipts",
                "build_scope": "source archive and wheel; no deployment or hardware access"}
    (out / "candidate-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    try:
        manifest = build(args.out)
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(json.dumps(manifest, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

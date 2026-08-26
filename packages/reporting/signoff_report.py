"""Markdown + JSON session report generator (offline v0.1)."""
from __future__ import annotations

import json
from pathlib import Path


def render_markdown(session: dict, metrics: dict, video_meta: dict | None = None) -> str:
    sid = session.get("session_id", "unknown")
    lines: list[str] = []
    lines.append(f"# Signoff Report — {sid}")
    lines.append("")
    exp = session.get("experiment_id", "-")
    lines.append(f"- Experiment: `{exp}`")
    if session.get("started_at"):
        lines.append(f"- Started: {session['started_at']}")
    if session.get("notes"):
        lines.append(f"- Notes: {session['notes']}")

    dur = metrics.get("cycle_durations", {})
    lines += ["", "## Cycles"]
    if dur.get("n"):
        lines.append(
            f"- Count: **{dur['n']}** · mean {dur['mean_ms']} ms · "
            f"p50 {dur['p50_ms']} ms · p95 {dur['p95_ms']} ms")
        lines.append(f"- Range: {dur['min_ms']}–{dur['max_ms']} ms")
    else:
        lines.append("- No cycles detected.")

    out = metrics.get("outcomes", {})
    lines += ["", "## Outcomes"]
    total = out.get("total", 0)
    lines.append(
        f"- Success rate: **{out.get('success_rate', 0.0):.1%}** "
        f"({out.get('ok', 0)}/{total} OK, {out.get('failed', 0)} failed)")
    breakdown = out.get("failure_breakdown") or {}
    if breakdown:
        lines.append("- Failures by type:")
        for ft, n in sorted(breakdown.items(), key=lambda kv: -kv[1]):
            lines.append(f"  - `{ft}`: {n}")

    thr = metrics.get("throughput", {})
    idle = metrics.get("idle", {})
    lines += ["", "## Throughput & Idle"]
    lines.append(f"- Throughput: {thr.get('cycles_per_min', 0.0)} cycles/min")
    lines.append(f"- Idle ratio: {idle.get('idle_ratio', 0.0):.1%} "
                 f"({idle.get('idle_total_ms', 0.0)} ms)")

    det = metrics.get("detection", {})
    lines += ["", "## Detection Quality (camera-only vs ground truth)"]
    if det.get("available"):
        lines.append(
            f"- F1 **{det.get('f1', 0.0)}** (precision {det.get('precision')}, "
            f"recall {det.get('recall')}) — TP {det.get('true_positives')} / "
            f"FP {det.get('false_positives')} / FN {det.get('false_negatives')}")
    else:
        lines.append("- No vision report available for this session.")

    if video_meta:
        lines += ["", "## Data Integrity"]
        lines.append(f"- Video: `{video_meta.get('file', '?')}` "
                     f"({video_meta.get('width')}×{video_meta.get('height')} @ "
                     f"{video_meta.get('fps')} fps, {video_meta.get('frames')} frames)")
        sha = video_meta.get("sha256", "")
        lines.append(f"- SHA-256: `{sha[:16]}…`")

    lines.append("")
    return "\n".join(lines)


def write_report(out_dir: Path, session: dict, metrics: dict,
                 video_meta: dict | None = None) -> tuple[Path, Path]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    md_path = out_dir / "report.md"
    json_path = out_dir / "metrics.json"
    md_path.write_text(render_markdown(session, metrics, video_meta))
    json_path.write_text(json.dumps(metrics, indent=2))
    return md_path, json_path

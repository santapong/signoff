"""Frozen schema-v2 camera measurement reports and shared derivative exports."""
from __future__ import annotations

import copy
import csv
import hashlib
import html
import io
import json
import os
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4


def _canonical(value: dict) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False,
                      separators=(",", ":")).encode()


def report_hash(report: dict) -> str:
    snapshot = {k: v for k, v in report.items() if k != "report_hash"}
    return hashlib.sha256(_canonical(snapshot)).hexdigest()


def verify_report(report: dict) -> bool:
    return (report.get("schema_version") == 2
            and isinstance(report.get("report_hash"), str)
            and report["report_hash"] == report_hash(report))


def build_report(session: dict, metrics: dict, *, detected_cycles: list[dict] | None = None,
                 provenance: dict | None = None, video_meta: dict | None = None,
                 run_id: str | None = None, annotation_revision: str | None = None,
                 qualification: dict | None = None) -> dict:
    """Copy inputs into a reproducible snapshot; never invent missing provenance."""
    context = copy.deepcopy(provenance or {})
    context.setdefault("run_id", run_id)
    context.setdefault("annotation_revision", annotation_revision)
    context.setdefault("code_sha", context.get("git_sha"))
    context.setdefault("environment", None)
    context.setdefault("profile_hash", None)
    context.setdefault("configuration_hash", context.get("config_hash"))
    context.setdefault("timestamp_basis", None)
    context.setdefault("source_sha256", session.get("video_sha256",
                                                 (video_meta or {}).get("sha256")))
    sources = {
        "cycle_count_and_timing": "finalized_camera_observations",
        "throughput": "finalized_camera_observations_over_full_session",
        "outcomes": "human_review_confirmed_denominator",
        "detection_quality": "identified_reference_evaluation_only",
        "activity": "visual_observation_with_explicit_unknown_time",
    }
    report = {
        "schema_version": 2,
        "session": copy.deepcopy(session),
        "run_id": run_id or context.get("run_id"),
        "metrics": copy.deepcopy(metrics),
        "detected_cycles": copy.deepcopy(detected_cycles or []),
        "provenance": context,
        "metric_sources": sources,
        "video_meta": copy.deepcopy(video_meta),
        "qualification": copy.deepcopy(qualification) if qualification else {
            "status": "provisional", "physical": False,
            "reason": "physical_camera_qualification_not_recorded"},
        "limitations": [
            "Visible sequence completion does not establish grasp success or product quality.",
            "Visual activity is not certified machine availability or OEE.",
            "Measurement reports do not automatically accept a robot installation.",
        ],
    }
    report["report_hash"] = report_hash(report)
    return report


def _number(value, *, percent: bool = False) -> str:
    if value is None:
        return "Unavailable"
    return f"{value:.1%}" if percent else str(value)


def _safe_markdown(value) -> str:
    return str(value).replace("\\", "\\\\").replace("<", "&lt;").replace(">", "&gt;").replace(
        "\n", " ").replace("\r", " ")


def render_markdown(session: dict, metrics: dict, video_meta: dict | None = None, *,
                    report: dict | None = None) -> str:
    sid = _safe_markdown(session.get("session_id", "unknown"))
    lines = [f"# Signoff Report — {sid}", ""]
    lines.append(f"- Experiment: {_safe_markdown(session.get('experiment_id', '-'))}")
    if session.get("started_at"):
        lines.append(f"- Started: {_safe_markdown(session['started_at'])}")
    if session.get("notes"):
        lines.append(f"- Notes: {_safe_markdown(session['notes'])}")
    if report:
        lines += [f"- Schema: {report['schema_version']}",
                  f"- Run: {_safe_markdown(report.get('run_id') or 'unknown')}",
                  f"- Report SHA-256: {report['report_hash']}",
                  f"- Qualification: {_safe_markdown(report['qualification'].get('status'))}",
                  "- Source: camera observations; outcomes use human reviews."]
    dur = metrics.get("cycle_durations", {})
    lines += ["", "## Cycles"]
    if dur.get("n"):
        lines += [f"- Count: **{dur['n']}** · mean {dur.get('mean_ms')} ms · "
                  f"p50 {dur.get('p50_ms')} ms · p95 {dur.get('p95_ms')} ms",
                  f"- Range: {dur.get('min_ms')}–{dur.get('max_ms')} ms"]
    else:
        lines.append("- No completed cycles detected.")
    lines.append(f"- Partial cycles: {dur.get('partial_count', 0)}")
    out = metrics.get("outcomes", {})
    lines += ["", "## Outcomes",
              f"- Success rate: **{_number(out.get('success_rate'), percent=True)}** "
              f"({out.get('ok', 0)}/{out.get('confirmed', out.get('total', 0))} confirmed OK, "
              f"{out.get('failed', 0)} failed)",
              f"- UNKNOWN: {out.get('unknown', 0)}; unreviewed: {out.get('unreviewed', 0)}; "
              f"review coverage: {_number(out.get('review_coverage'), percent=True)}"]
    if out.get("reason"):
        lines.append(f"- Availability reason: {_safe_markdown(out['reason'])}")
    for kind, count in sorted((out.get("failure_breakdown") or {}).items()):
        lines.append(f"  - `{_safe_markdown(kind)}`: {count}")
    thr, idle = metrics.get("throughput", {}), metrics.get("idle", {})
    lines += ["", "## Throughput & Idle",
              f"- Throughput: {_number(thr.get('cycles_per_min'))} cycles/min",
              f"- Denominator: {_number(thr.get('duration_ms'))} ms "
              f"({_safe_markdown(thr.get('duration_basis', 'legacy_unspecified'))})",
              f"- Visual idle ratio: {_number(idle.get('idle_ratio'), percent=True)} "
              f"({_number(idle.get('idle_total_ms'))} ms)",
              f"- Visual activity: {_number(idle.get('activity_total_ms'))} ms; "
              f"unknown: {_number(idle.get('unknown_total_ms'))} ms",
              f"- Observation coverage: "
              f"{_number((idle.get('coverage') or {}).get('usable_ratio'), percent=True)}"]
    det = metrics.get("detection", {})
    lines += ["", "## Detection Quality (camera-only vs identified reference)"]
    if det.get("available"):
        lines.append(f"- F1 **{_number(det.get('f1'))}** "
                     f"(precision {_number(det.get('precision'))}, "
                     f"recall {_number(det.get('recall'))}) — TP {det.get('true_positives')} / "
                     f"FP {det.get('false_positives')} / FN {det.get('false_negatives')}")
        if det.get("matcher"):
            lines.append(f"- Matcher: {_safe_markdown(det['matcher'])}")
        for name, values in (det.get("timing_errors") or {}).items():
            lines.append(f"- {name} timing error P95: {_number(values.get('p95_absolute_ms'))} ms "
                         f"({values.get('n', 0)} matched samples)")
    else:
        lines.append("- Detection accuracy: Unavailable — "
                     f"{_safe_markdown(det.get('reason', 'reference_annotations_unavailable'))}.")
    if video_meta:
        lines += ["", "## Data Integrity",
                  f"- Video: `{_safe_markdown(video_meta.get('file', '?'))}` "
                  f"({video_meta.get('width')}×{video_meta.get('height')} @ "
                  f"{video_meta.get('fps')} fps, {video_meta.get('frames')} frames)",
                  f"- SHA-256: `{video_meta.get('sha256', '')}`"]
    if report:
        lines += ["", "## Provenance", "", "```json",
                  json.dumps(report["provenance"], indent=2, sort_keys=True, ensure_ascii=False),
                  "```", "", "## Limitations"]
        lines += [f"- {item}" for item in report["limitations"]]
        lines += ["", "## Cycle evidence"]
        for index, cycle in enumerate(report["detected_cycles"], 1):
            identity = _safe_markdown(cycle.get("cycle_id", cycle.get("cycle", index)))
            evidence = _safe_markdown(cycle.get("evidence_url") or "evidence link unavailable")
            lines.append(f"- {identity}: {cycle.get('start_ms')}–{cycle.get('end_ms')} ms; "
                         f"frames {cycle.get('start_frame', '?')}–{cycle.get('end_frame', '?')}; "
                         + evidence)
    return "\n".join(lines) + "\n"


def _flatten(value: dict, prefix: str = ""):
    for key in sorted(value):
        item = value[key]
        name = f"{prefix}.{key}" if prefix else key
        if isinstance(item, dict):
            yield from _flatten(item, name)
        elif not isinstance(item, list):
            yield name, item


def _csv_safe(value):
    if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


def render_csv(report: dict) -> str:
    stream = io.StringIO(newline="")
    writer = csv.writer(stream)
    writer.writerow(["row_type", "name", "value", "source", "cycle_id", "start_ms", "end_ms",
                     "status", "start_frame", "end_frame", "evidence_url"])
    writer.writerow(["metadata", "report_hash", report["report_hash"]])
    writer.writerow(["metadata", "qualification", report["qualification"].get("status")])
    for name, value in _flatten(report["metrics"]):
        source = report["metric_sources"].get({
            "cycle_durations": "cycle_count_and_timing", "outcomes": "outcomes",
            "throughput": "throughput", "idle": "activity", "detection": "detection_quality"
        }.get(name.split(".")[0], ""), "unknown")
        writer.writerow(["metric", name, _csv_safe(value), source])
    for index, cycle in enumerate(report["detected_cycles"], 1):
        writer.writerow([_csv_safe(value) for value in (
            "cycle", "", "", "camera_observation",
            cycle.get("cycle_id", cycle.get("cycle", index)), cycle.get("start_ms"),
            cycle.get("end_ms"), cycle.get("status", "COMPLETE"), cycle.get("start_frame"),
            cycle.get("end_frame"), cycle.get("evidence_url"))])
    return stream.getvalue()


def _evidence_link(url) -> str:
    if not url:
        return "Unavailable"
    text = str(url)
    parsed = urlsplit(text)
    if parsed.scheme not in ("", "http", "https") or text.startswith("//"):
        return "Unavailable (invalid evidence URL)"
    return f'<a href="{html.escape(text, quote=True)}">View evidence</a>'


def render_html(report: dict, *, printable: bool = False) -> str:
    title = html.escape(str(report["session"].get("session_id", "unknown")))
    rows = []
    for name, value in _flatten(report["metrics"]):
        rows.append(f"<tr><th>{html.escape(name)}</th><td>{html.escape(_number(value))}</td></tr>")
    cycles = []
    for index, cycle in enumerate(report["detected_cycles"], 1):
        identity = html.escape(str(cycle.get("cycle_id", cycle.get("cycle", index))))
        cycles.append(f"<tr><td>{identity}</td><td>"
                      f"{html.escape(str(cycle.get('start_ms')))}–"
                      f"{html.escape(str(cycle.get('end_ms')))} ms</td><td>"
                      f"{html.escape(str(cycle.get('start_frame', '?')))}–"
                      f"{html.escape(str(cycle.get('end_frame', '?')))}</td><td>"
                      f"{_evidence_link(cycle.get('evidence_url'))}</td></tr>")
    mode = ("<p>Printable report: use the browser’s Print → Save as PDF. "
            "The PDF is a derivative of this frozen report.</p>" if printable else "")
    provenance = html.escape(json.dumps(report["provenance"], indent=2, sort_keys=True))
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Signoff report — {title}</title>
<style>body{{font:16px system-ui;margin:2rem auto;max-width:1000px;padding:0 1rem}}
table{{border-collapse:collapse;width:100%;margin:1rem 0}}th,td{{padding:.5rem;text-align:left;
border-bottom:1px solid #ddd;overflow-wrap:anywhere}}
pre{{white-space:pre-wrap;overflow-wrap:anywhere}}
@media print{{body{{margin:0;max-width:none}}tr{{break-inside:avoid}}a{{color:inherit}}}}</style>
</head><body><h1>Signoff Report — {title}</h1>{mode}
<p>Schema 2 · Run {html.escape(str(report.get('run_id') or 'unknown'))}</p>
<p>Qualification: <strong>{html.escape(str(report['qualification'].get('status')))}</strong></p>
<p>Report SHA-256: {report['report_hash']}</p>
<p>Camera observations determine count and timing. Confirmed human reviews determine outcomes.
Unknown and unreviewed outcomes do not count as successful.</p>
<h2>Measurements</h2><table><tbody>{''.join(rows)}</tbody></table>
<h2>Cycle evidence</h2><table><thead><tr><th>Cycle</th><th>Time</th><th>Frames</th>
<th>Evidence</th></tr></thead><tbody>{''.join(cycles)}</tbody></table>
<h2>Provenance</h2><pre>{provenance}</pre><h2>Limitations</h2><ul>
{''.join('<li>' + html.escape(item) + '</li>' for item in report['limitations'])}
</ul></body></html>"""


def _publish_immutable(path: Path, content: str) -> None:
    """Publish exclusively; retrying identical content is harmless."""
    data = content.encode("utf-8")
    if path.exists():
        if path.read_bytes() != data:
            raise ValueError(f"immutable report artifact already exists: {path.name}")
        return
    temp = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temp.open("xb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        try:
            os.link(temp, path)
        except FileExistsError:
            if path.read_bytes() != data:
                raise ValueError(f"immutable report artifact already exists: {path.name}") from None
    finally:
        temp.unlink(missing_ok=True)


def write_evidence_report(out_dir: Path, session: dict, metrics: dict, *,
                          detected_cycles: list[dict] | None = None,
                          provenance: dict | None = None, video_meta: dict | None = None,
                          run_id: str | None = None, annotation_revision: str | None = None,
                          qualification: dict | None = None) -> dict[str, Path]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    report = build_report(session, metrics, detected_cycles=detected_cycles, provenance=provenance,
                          video_meta=video_meta, run_id=run_id,
                          annotation_revision=annotation_revision, qualification=qualification)
    outputs = {
        "markdown": ("report.md", render_markdown(session, metrics, video_meta, report=report)),
        "csv": ("report.csv", render_csv(report)),
        "html": ("report.html", render_html(report)),
        "printable_html": ("report.print.html", render_html(report, printable=True)),
        "metrics": ("metrics.json", json.dumps(metrics, indent=2, sort_keys=True, allow_nan=False)),
        # The manifest is published last, after all derivatives are present.
        "json": ("report.json", json.dumps(report, indent=2, sort_keys=True, allow_nan=False)),
    }
    for filename, content in outputs.values():
        path = out_dir / filename
        if path.exists() and path.read_bytes() != content.encode("utf-8"):
            raise ValueError(f"immutable report artifact already exists: {filename}")
    for filename, content in outputs.values():
        _publish_immutable(out_dir / filename, content)
    return {name: out_dir / filename for name, (filename, _) in outputs.items()}


def write_report(out_dir: Path, session: dict, metrics: dict,
                 video_meta: dict | None = None) -> tuple[Path, Path]:
    """Legacy convenience signature; outputs now include schema-v2 exports."""
    outputs = write_evidence_report(out_dir, session, metrics, video_meta=video_meta)
    return outputs["markdown"], outputs["metrics"]

"""Camera correctness regressions and synthetic oracle for scoring optimality."""
from __future__ import annotations

import copy
import csv
import io
import json
import random

import pytest

from packages.cv.signoff_cv.evaluate import (
    GroundTruthCycle,
    evaluate_cycles,
    evaluate_session,
    load_ground_truth_cycles,
    match_cycles,
    match_cycles_legacy_start_only,
    wilson_interval,
)
from packages.domain.agreement import agreement_report
from packages.domain.annotations import (
    latest_annotations,
    make_annotation,
    read_annotations,
    write_annotations,
)
from packages.metrics.signoff_metrics import idle_ratio, outcome_summary, session_metrics
from packages.reporting.signoff_report import (
    build_report,
    render_csv,
    render_html,
    render_markdown,
    verify_report,
    write_evidence_report,
)


def _cycle(identity="c1", start=1000, end=2000, **extra):
    return {"cycle_id": identity, "start_ms": start, "end_ms": end,
            "status": "COMPLETE", "start_frame": 10, "end_frame": 20, **extra}


def _metrics(cycles=None, reviews=None):
    return session_metrics([], cycles or [_cycle()], [], session_duration_ms=60000,
                           reviewed_cycles=reviews, coverage={"observed_ms": 60000})


def test_reference_labels_cannot_change_operational_metrics():
    detected = [_cycle()]
    unlabelled = session_metrics([], detected, [], session_duration_ms=60000)
    labelled = session_metrics([{"start_ms": 0, "end_ms": 59000, "outcome": "OK"}],
                               detected, [], session_duration_ms=60000)
    assert labelled == unlabelled
    assert labelled["cycle_durations"]["mean_ms"] == 1000
    assert labelled["throughput"]["cycles_per_min"] == 1
    assert labelled["outcomes"]["success_rate"] is None


def test_unknown_and_unreviewed_are_not_failed_or_successful():
    cycles = [_cycle("a"), _cycle("b", 3000, 4000), _cycle("c", 5000, 6000),
              _cycle("d", 7000, 8000)]
    reviews = [{"cycle_id": "a", "outcome": "OK"},
               {"cycle_id": "b", "outcome": "FAILED", "failure_type": "OBJECT_DROP"},
               {"cycle_id": "c", "outcome": "UNKNOWN"}]
    result = _metrics(cycles, reviews)["outcomes"]
    assert result["ok"] == result["failed"] == result["unreviewed"] == 1
    assert result["unknown"] == result["confirmed"] == 2
    assert result["reviewed"] == 3
    assert result["success_rate"] == 0.5
    assert result["review_coverage"] == 0.75


def test_review_with_stale_identity_does_not_change_outcome():
    metrics = _metrics(reviews=[{"cycle_id": "old-c1", "cycle": 1, "outcome": "OK"}])
    assert metrics["outcomes"]["success_rate"] is None
    assert metrics["outcomes"]["unreviewed"] == 1


def test_empty_outcomes_unavailable():
    assert outcome_summary([])["success_rate"] is None
    assert outcome_summary([{}])["unknown"] == 1


def test_partial_events_do_not_count_and_unknown_duration_stays_unavailable():
    metrics = session_metrics([], [_cycle(), _cycle("open", 3000, None, status="OPEN")], [])
    assert metrics["cycle_durations"]["n"] == 1
    assert metrics["cycle_durations"]["partial_count"] == 1
    assert metrics["throughput"]["cycles_per_min"] is None
    assert metrics["throughput"]["reason"] == "session_duration_unavailable"


def test_zero_cycle_recording_has_measured_zero_throughput():
    metrics = session_metrics([], [], [], session_duration_ms=60000)
    assert metrics["throughput"]["cycles_per_min"] == 0
    assert metrics["outcomes"]["success_rate"] is None


@pytest.mark.parametrize("duration", [-1, float("nan"), float("inf")])
def test_invalid_duration_rejected(duration):
    with pytest.raises(ValueError):
        session_metrics([], [], [], session_duration_ms=duration)


def test_cycles_outside_session_rejected():
    with pytest.raises(ValueError):
        session_metrics([], [_cycle()], [], session_duration_ms=100)


def test_union_clamped_idle_and_unknown_partition():
    idle = [{"start_ms": -1000, "end_ms": 4000}, {"start_ms": 3000, "end_ms": 6000}]
    unknown = [{"start_ms": 5000, "end_ms": 9000}]
    metrics = session_metrics([], [_cycle()], idle, session_duration_ms=10000,
                              coverage={"unknown_ms": 4000}, unknown_intervals=unknown)
    visual = metrics["idle"]
    assert visual["idle_total_ms"] == 5000
    assert visual["unknown_total_ms"] == 4000
    assert visual["activity_total_ms"] == 1000
    assert visual["coverage"]["usable_ratio"] == 0.6
    assert sum(visual[key] for key in ("idle_total_ms", "unknown_total_ms",
                                      "activity_total_ms")) == 10000
    assert idle_ratio(idle, 10000)["idle_total_ms"] == 6000


def test_coverage_without_gap_locations_does_not_invent_partition():
    visual = session_metrics([], [], [], session_duration_ms=10000,
                             coverage={"usable_ratio": 0.6})["idle"]
    assert not visual["partition_available"]
    assert visual["activity_total_ms"] is None
    assert visual["unknown_total_ms"] == 4000


def test_unlabelled_accuracy_unavailable(tmp_path):
    report = evaluate_session(tmp_path, [(1000, 2000)])
    assert not report["available"]
    assert report["f1"] is None and report["precision"] is None
    assert report["false_positives"] is None
    assert json.loads((tmp_path / "vision_report.json").read_text()) == report


def test_two_boundary_match_rejects_wrong_end_and_preserves_legacy_score():
    references = [GroundTruthCycle(1, 1000, 2000)]
    assert match_cycles(references, [(1010, 10000)]).true_positives == 0
    assert match_cycles_legacy_start_only(references, [(1010, 10000)]).true_positives == 1


def test_matching_avoids_greedy_cardinality_trap():
    references = [GroundTruthCycle("a", 1000, 2000), GroundTruthCycle("b", 1600, 2600)]
    detections = [_cycle("early", 700, 1700), _cycle("middle", 1300, 2300)]
    report = match_cycles(references, detections, 500).to_dict()
    assert report["true_positives"] == 2
    assert {m["detected_cycle_id"] for m in report["matches"]} == {"early", "middle"}


def _brute_match(references, detections, tolerance):
    """Independent exhaustive oracle over small assignment sets."""
    best = (0, 0.0)

    def visit(index, used, count, error):
        nonlocal best
        if index == len(references):
            if count > best[0] or (count == best[0] and error < best[1]):
                best = (count, error)
            return
        visit(index + 1, used, count, error)
        reference = references[index]
        for j, detected in enumerate(detections):
            start = abs(reference.start_ms - detected["start_ms"])
            end = abs(reference.end_ms - detected["end_ms"])
            if j not in used and start <= tolerance and end <= tolerance:
                visit(index + 1, used | {j}, count + 1, error + start + end)

    visit(0, set(), 0, 0.0)
    return best


def test_matching_matches_exhaustive_cardinality_and_boundary_cost_oracle():
    rng = random.Random(1337)
    for _ in range(60):
        refs = [GroundTruthCycle(f"r{i}", rng.randrange(0, 1000), 3000 + rng.randrange(1000))
                for i in range(4)]
        detections = [_cycle(f"d{i}", rng.randrange(0, 1000), 3000 + rng.randrange(1000))
                      for i in range(4)]
        expected = _brute_match(refs, detections, 500)
        report = match_cycles(refs, detections, 500)
        assert report.true_positives == expected[0]
        assert sum(row["boundary_absolute_error_ms"] for row in report.matches) == expected[1]


def test_matching_is_stable_under_input_reordering():
    refs = [GroundTruthCycle("r2", 1000, 2000), GroundTruthCycle("r1", 1000, 2000)]
    detected = [_cycle("d2"), _cycle("d1")]
    assert match_cycles(refs, detected).to_dict() == match_cycles(
        list(reversed(refs)), list(reversed(detected))).to_dict()
    assert match_cycles(refs, [(900, 1900), (1100, 2100)]).to_dict() == match_cycles(
        refs, [(1100, 2100), (900, 1900)]).to_dict()


def test_scoring_timing_errors_and_uncertainty_with_identified_reference():
    report = evaluate_cycles([{"cycle": 1, "start_ms": 1000, "end_ms": 2000}],
                             [_cycle(start=1100, end=2200)], reference_id="labels")
    assert report["reference_id"] == "labels"
    assert report["timing_errors"]["duration"]["p95_absolute_ms"] == 100
    assert report["timing_errors"]["end"]["p95_absolute_ms"] == 200
    assert report["uncertainty"]["recall"]["n"] == 1
    assert 0 < report["uncertainty"]["recall"]["lower"] < 1
    assert report["uncertainty"]["recall"]["upper"] == 1
    assert report["legacy_start_only"]["matcher"] == "legacy_start_only_greedy"
    assert wilson_interval(0, 0)["lower"] is None


def test_known_empty_reference_not_confused_with_missing_reference():
    report = evaluate_cycles([], [_cycle()], reference_id="empty-reference")
    assert report["available"]
    assert report["false_positives"] == 1
    assert report["recall"] is None


def test_hidden_logger_timestamp_field_supported(tmp_path):
    path = tmp_path / "events.jsonl"
    path.write_text("\n".join(json.dumps(row) for row in [
        {"event": "CYCLE_START", "cycle": 1, "timestamp_ms": 100},
        {"event": "CYCLE_COMPLETED", "cycle": 1, "timestamp_ms": 900}]))
    assert load_ground_truth_cycles(path)[0].end_ms == 900


def test_report_snapshot_is_immutable_repeatable_and_hashed(tmp_path):
    session = {"session_id": "ses_test", "notes": "recording"}
    metrics = _metrics()
    report = build_report(session, metrics, detected_cycles=[_cycle()], run_id="run_1")
    original = copy.deepcopy(report)
    session["notes"] = "changed"
    metrics["outcomes"]["ok"] = 100
    assert report == original and verify_report(report)
    report["metrics"]["outcomes"]["ok"] = 100
    assert not verify_report(report)
    output = write_evidence_report(tmp_path, original["session"], original["metrics"],
                                   detected_cycles=original["detected_cycles"], run_id="run_1")
    assert set(output) == {"json", "metrics", "markdown", "csv", "html", "printable_html"}
    frozen = json.loads(output["json"].read_text())
    assert verify_report(frozen)
    assert frozen["qualification"]["status"] == "provisional"
    write_evidence_report(tmp_path, original["session"], original["metrics"],
                          detected_cycles=original["detected_cycles"], run_id="run_1")
    with pytest.raises(ValueError, match="immutable"):
        write_evidence_report(tmp_path, {"session_id": "changed"}, original["metrics"])
    assert "Save as PDF" in output["printable_html"].read_text()
    assert not list(tmp_path.glob("*.pdf"))


def test_report_escapes_html_and_csv_formulas():
    report = build_report({"session_id": "<script>alert(1)</script>"}, _metrics(),
                          detected_cycles=[_cycle("=WEBSERVICE(1)",
                                                  evidence_url="javascript:evil")])
    rendered = render_html(report)
    assert "<script>" not in rendered and "javascript:evil" not in rendered
    assert "&lt;script&gt;" in rendered
    rows = list(csv.reader(io.StringIO(render_csv(report))))
    assert rows[-1][4] == "'=WEBSERVICE(1)"
    markdown = render_markdown(report["session"], report["metrics"], report=report)
    assert "Unavailable" in markdown and "no_confirmed_outcomes" in markdown


def test_annotation_unknown_revisions_and_history(tmp_path):
    first = make_annotation("s", 1, 100, 900, 1000, annotator="alice", cycle_id="c", run_id="r")
    assert first["outcome"] == "UNKNOWN"
    second = make_annotation("s", 1, 100, 900, 1000, outcome="OK", annotator="alice",
                             cycle_id="c", run_id="r", supersedes=first["revision_id"])
    path = tmp_path / "annotations.jsonl"
    write_annotations(path, [first])
    write_annotations(path, [second])
    assert read_annotations(path) == [first, second]
    assert latest_annotations(read_annotations(path), run_id="r") == [second]
    assert latest_annotations(read_annotations(path), run_id="other") == []
    with pytest.raises(ValueError, match="already exists"):
        write_annotations(path, [second])


@pytest.mark.parametrize("changes", [
    {"cycle_start_ms": -1}, {"cycle_end_ms": float("nan")},
    {"outcome": "OK", "failure_type": "OBJECT_DROP"}, {"cycle": 0},
])
def test_invalid_annotations_rejected(changes):
    values = dict(session_id="s", cycle=1, cycle_start_ms=100,
                  cycle_end_ms=900, video_duration_ms=1000)
    with pytest.raises(ValueError):
        make_annotation(**{**values, **changes})


def test_agreement_missing_label_is_unknown():
    report = agreement_report({"alice": [{"cycle": 1}], "bob": [{"cycle": 1, "outcome": "OK"}]})
    assert report["pairs"][0]["unknown_pairs"] == 1
    assert report["pairs"][0]["kappa"] == 0

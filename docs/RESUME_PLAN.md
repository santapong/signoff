# Signoff October resume plan

Checked 13 October 2026, Asia/Bangkok. Source baseline: `origin/develop` at `68e82928ce7950948c638cb5d13852eb6fe9c753`.

Signoff is the selected project to resume. The software prototype is on develop; main still contains the earlier scaffold. The full proposed roadmap is [Signoff camera measurement project plan](CAMERA_FIRST_PLAN.md), which supersedes this initial restart order while preserving the findings below. The owner selected both recorded and live video for the first validated release; physical arm bring-up remains a separate gate.

## Owner direction

On 13 October, Santapong selected Signoff for renewed focus, reported RoboLLM parked while preparing hardware, and planned to continue RLM during the same week. These are owner decisions and plans, not evidence of completed hardware work or RLM execution.

In the subsequent planning answers, the owner selected automation engineers as the first audience, recorded and live video together, and reported a camera and station available. No footage was captured or station operation verified in this planning work. USB camera, pick-and-place and markers allowed are explicit defaults after the later setup questions went unanswered.

## Current baseline

The August status file reports seven software phases merged to develop and 83 passing tests. Source files support the presence of dataset tools, deterministic CV, offline upload/analyze/report, human review, synthetic live monitoring, server-rendered UI, token authentication and backup/restore tools.

Physical ELEXHUB bring-up, real video accuracy and an external pilot have not been established by this restart inspection. The previous synthetic latency measurement does not establish latency on a real camera or robot cell.

Fresh verification on 13 October: **83 tests passed**, with two dependency/runtime deprecation warnings, in 110.15 seconds. **Ruff reports 36 issues** in the existing source. Three separate direct reproductions expose gaps not covered by the passing suite. Environment versions and check receipts are retained in `docs/validation/2026-10-13/`.

## First software task

Repair replay of recorded frame clocks and make reports distinguish camera measurements, reference annotations and unavailable information.

A four-frame generated MP4 with its recorder-style `frame_index.jsonl` crashes on the first replayed frame with `TypeError: 'dict' object is not callable`. `VideoFileSource` stores the parsed clock as a dictionary but later calls it as a function. The reproduction used generated frames; no camera or arm was opened.

Two direct function reproductions on the unchanged baseline show why this is needed:

1. With one detected cycle and no ground-truth file, `evaluate_session` returns F1 0 and `detection_quality` marks it available. Outcome success rate is also 0 despite zero labeled outcomes. The UI/report can present unavailable information as measured failure.
2. With a detected cycle from 1000–2000 ms and a ground-truth cycle from 0–9000 ms, `session_metrics` reports the 9000 ms reference duration. Adding reference labels changes operational duration and throughput because the function prefers ground truth over camera detections.

Proposed acceptance criteria:

- Recorder-style clock files replay their recorded timestamps successfully, with explicit handling of missing frame entries.
- Unlabeled footage reports detection accuracy and outcome success as unavailable, with no fabricated zero score.
- Operational cycle duration and throughput remain derived from camera detections when reference labels are added or changed.
- Ground truth remains separate and is used for evaluation only.
- UNKNOWN outcomes remain unknown rather than being counted as failed or successful.
- Regression coverage includes unlabeled sessions and differing detected/reference durations.

Register an issue for the nontrivial implementation, as CONTRIBUTING requires. The local documentation branch is `docs/resume-validation`; implementation should use a separate feature/fix branch from develop. GitHub issue and PR publication follow the owner's web-only convention.

## Recorded video validation

1. Inventory consented footage and session metadata. Do not commit raw video.
2. Register the experiment before evaluation; record source hash, code SHA, motion/segmentation configuration, camera/lighting condition and reference clock.
3. Annotate complete sessions independently of inference. Keep reference labels out of the detection path.
4. Freeze detections before scoring against labels. Report precision, recall, F1, false positives/negatives and separate timing errors.
5. Repeat the same input/configuration to verify reproducibility, then include lighting and camera variation.
6. Preserve session-based development/test separation. A small screening set is not the existing 100+ labeled-cycle physical acceptance gate.

The existing Phase 2 plan proposes at least 95% cycle-detection F1, with a default 500 ms matching tolerance. The matcher currently scores start-time proximity only, so timing accuracy requires its own report. Do not reinterpret a high start-match F1 as verified cycle duration.

## Specification and release gates

`docs/SDD.md` is a placeholder referring to a full document held by the owner. The available plans and contribution rules guide preparation; the full SDD is needed to reconcile normative acceptance details before changing them.

The hardware gate remains parked until firmware/transport and verified joint limits are available. Local software work does not authorize arm motion, Pi changes, a pilot deployment or a release. The existing develop-to-main release and external pilot steps remain later gates.

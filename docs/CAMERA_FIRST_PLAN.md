# Signoff camera measurement project plan

**Status:** Proposed implementation roadmap  
**Prepared:** 13 October 2026, Asia/Bangkok  
**Baseline:** develop at `68e82928ce7950948c638cb5d13852eb6fe9c753`

Signoff will help automation engineers measure repetitive robot tasks from a fixed camera and produce evidence they can inspect before accepting an installation or a configuration change. The primary product is camera-based observation, measurement and review. Embodied AI evaluation is a secondary application after the camera measurement system qualifies.

The first release will support both uploaded recordings and live monitoring through the same processing pipeline. It will qualify one station and task before expanding to other robots or processes.

## Product decisions

| Decision | Choice | Basis |
|---|---|---|
| First audience | Automation engineers | Owner selected |
| First release | Recorded and live video together | Owner selected |
| Validation access | Camera and station available | Owner reported; no capture or station operation verified |
| Camera adapter | One fixed USB camera per station | Default after unanswered setup question |
| Reference task | Repetitive pick-and-place | Default after unanswered setup question |
| Visual aids | Markers allowed for initial qualification | Default after unanswered setup question |
| Deployment | Local Linux host, CPU processing, browser UI | Retains the existing stack |
| Embodied AI | Later evaluation/export adapter | Owner made this secondary |

USB, pick-and-place and markers are planning defaults, not claims about the owner's actual camera or station. If those defaults do not match the available station, revise the task profile and qualification protocol before implementation of the detector. RTSP, additional cameras, marker-free task recognition and learned perception enter later milestones only.

### First release capabilities

An engineer can create a station profile, check camera visibility, upload footage or start an explicitly selected live source, inspect a synchronized event timeline, review uncertain outcomes, and export a versioned evidence report. The report includes cycle count, timing distributions, throughput, visual activity/inactivity, observation coverage and separately confirmed outcomes.

Cycle completion means a defined visible sequence completed. It does not imply a successful grasp or acceptable product quality. Success and failure statistics use confirmed reviews, with UNKNOWN and unreviewed outcomes counted separately.

The first release does not make safety decisions, control the robot, diagnose root causes, calculate certified OEE, infer force or dimensional quality, or autonomously approve an installation. Human acceptance is recorded separately from the measurements.

## Current software and required corrections

Substantial software exists on develop; main retains the earlier scaffold. Fresh preflight on 13 October passed 83 existing tests, with two deprecation warnings, and found 36 Ruff issues. Passing tests are software regression evidence, not real-camera qualification.

The restart reproductions and inspection establish the following work:

| Gap | Required correction |
|---|---|
| Recorder frame-clock replay crashes | Read frame-index timestamps as data; validate coverage, ordering and clock origin |
| Unlabeled footage presents F1 and success as zero | Return unavailable/null values with a reason; show unknown outcomes honestly |
| Reference annotations alter operational duration and throughput | Derive operational measurements exclusively from observations; isolate scoring against references |
| Live count represents a rolling window | Persist monotonically increasing session totals and stable event identities |
| Open activity intervals can be published as completed | Publish completion only after the defined closing condition; keep open/partial events explicit |
| Failure heuristics and fixed quarter-duration phases imply task semantics | Keep these as labeled experimental hypotheses; qualify visible task events separately |
| Live capture and recording use separate paths | Acquire each frame once, then archive and analyze the same timestamped observation |
| Compose build context and token forwarding are incomplete | Correct packaging context and explicitly pass required runtime settings |
| WebSocket authorization, UI form submission and archive restore need work | Verify real routes, form contracts, bounded streaming and safe staged extraction |

The live-count reproduction injected 15 activity intervals across 30 seconds and ended with a displayed rolling count of six. This is a local synthetic diagnostic.

The full historical SDD is absent: `docs/SDD.md` is a placeholder. Milestone M0 must publish a traceable requirements inventory using the available phase plans and this proposed roadmap. If the full SDD is supplied, reconcile conflicts explicitly. Do not silently replace historical acceptance criteria or mark the legacy phases physically qualified.

## Measurement design

### One observation path

Recorded file or USB camera → timestamped frames → quality checks and visual features → stateful cycle detector → append-only events → metrics and evidence → human review → report.

Retain FastAPI, Jinja2/vanilla browser UI, OpenCV and on-disk artifacts. Add PyAV for decoded presentation timestamps and lossless qualification recordings; resolve and lock its version with the verified dependency set. Use one application process, one live capture session and one bounded offline-analysis worker. No separate message broker, multi-tenant service or GPU is needed for this first scope.

### Clock and capture contract

Each observation carries session and segment IDs, frame number, session-relative time, source clock basis, host receipt time and quality flags.

For Signoff recordings, the capture-side frame clock is authoritative for session-relative timing. For uploaded videos without a sidecar, use decoded presentation timestamps and their time base. Record the source and timing quality; do not silently substitute 30 fps when trustworthy timestamps are absent. Invalid or incomplete clocks produce explicit gaps or reject qualified analysis.

A USB camera's host receipt time must be labeled as such; it is not automatically a sensor exposure timestamp. Preserve both measurement time and processing time so replay speed cannot change task measurements.

Request 1280×720 at 30 fps, record the negotiated dimensions and observed rate, and qualify only configurations sustaining at least 10 usable observations per second. A lower-rate preview can run, but cannot receive the v1 qualification label.

During qualification, archive frames losslessly as FFV1/MKV with per-segment clocks and frame hashes. Derived MP4 clips are for viewing. Exact replay parity is asserted only after decoded pixels and recorded timestamps match. Record to five-minute segments under one continuous session clock.

Bound in-memory queues. Mark dropped observations and recording failures explicitly; a cycle spanning an unresolved gap cannot become a qualified complete cycle. Starting capture requires at least 5 GiB free disk; archival stops before remaining space falls below 1 GiB. Preserve prior evidence, mark the affected interval unavailable and request operator action. There is no automatic evidence deletion.

### Reference task and detector

Use a fixed camera and a versioned profile containing normalized HOME, PICK and PLACE image regions, marker IDs, detector parameters and expected task order.

The initial qualified detector uses a tool-mounted ArUco marker and a stationary reference marker, with motion-only segmentation retained as a separate baseline. Use OpenCV's DICT_4X4_50 with reference marker ID 0 and tool marker ID 1. Marker placement must remain visible and must not interfere with the station's normal operation.

A cycle starts when the tool leaves HOME after at least 200 ms of stable HOME visibility. A complete sequence visits PICK then PLACE, followed by at least 200 ms back at HOME. PICK/PLACE visits require at least 100 ms and two valid observations. Boundary timestamps use the first qualifying frame; the event is finalized only after the dwell requirement is satisfied.

A sequence that skips a required region, is interrupted, times out after the profile's default 60 seconds, or encounters an unresolved visibility gap remains incomplete/UNKNOWN. A visibility gap longer than 250 ms suspends qualification and requires reacquiring stable HOME before a new cycle. Do not bridge an uncertain interval with invented motion.

The stationary marker detects camera movement. A persistent corner displacement greater than 2% of the image diagonal across five valid observations invalidates the profile until the operator rechecks it. Resolution changes also invalidate the profile. These parameters are frozen for the qualification dataset and stored in the configuration hash.

The detector measures visible sequence completion. Object transfer and grasp success remain review tasks in v1. The UI may offer evidence suggestions, but it must not promote heuristic confidence values into calibrated probabilities.

### Metrics and evidence

Maintain immutable detected events, reference annotations, human revisions and run versions separately. Report which source each displayed result uses.

- Count and timing use finalized observed cycles. Partial cycles are reported separately.
- Throughput uses completed observed cycles divided by the measured session duration, not the span between the first and last cycle. Include coverage so missing observations are visible.
- Activity/inactivity/unknown durations form a mutually exclusive partition. Call these visual observations rather than machine availability.
- Outcome success rate uses confirmed OK and FAILED reviews only. Always show the reviewed denominator, UNKNOWN count and review coverage.
- Detection F1 and timing errors are available only against an identified reference set; unavailable values carry a reason instead of a numeric zero.
- A report includes source/segment hashes, code SHA, environment, profile/configuration hashes, timestamp basis, run ID and annotation revision. Every cycle links to frame ranges and playable evidence.
- Produce JSON, CSV and HTML reports; a printable PDF is a derivative of the same frozen report content. Preserve the existing Markdown export.

Use deterministic one-to-one maximum-cardinality matching between reference and detected cycles, with minimum total boundary error as the tie-break and stable IDs for deterministic ties. Retain the legacy start-only, ±500 ms evaluation as a separately named historical score. The proposed qualification score requires both boundaries within ±500 ms, and reports start/end/duration errors independently.

## Interfaces and operator workflow

Introduce versioned station profiles and analysis runs without rewriting existing sessions.

| Interface | Intended change |
|---|---|
| `/profiles` | Create/list immutable profile revisions; retrieve by profile ID and revision |
| `POST /sessions/upload` | Accept MP4/MKV, notes as form data and an optional profile revision; validate and probe the actual media |
| `POST /sessions/{sid}/analyze` | Create a durable run and return HTTP 202 with run ID/status URL |
| `GET /runs/{run_id}` | Return queued/running/complete/failed state, progress and artifact links |
| `POST /live/start` | Require an explicit profile and source, and return a persisted session ID; synthetic input is explicit development mode only |
| `/live/status`, `/live/stop`, `/live/ws` | Share persistent totals, observation quality and stable event sequence numbers; authenticate all access |
| Session evidence/review/report routes | Serve validated media ranges, append review revisions and export the selected immutable run |

New artifacts use schema version 2. Extend annotations with UNKNOWN and explicit revision identity. Existing version-1 files remain readable; their metrics are labeled legacy until reanalyzed. Missing legacy profile/provenance fields remain unknown. Old live query arguments are supported through a documented adapter for one release; ambiguous old/new inputs are rejected, and an omitted camera never silently selects synthetic mode.

Use typed validation for identifiers, ranges, profiles and reviews. Reference storage is accessible only to annotation/evaluation components, not the inference entry point.

Keep durable job status on disk using atomic writes. One worker processes offline jobs. On restart, an unfinished job is marked interrupted; rerunning creates a new run ID and preserves the previous partial artifacts. Reports become visible as complete only after all referenced artifacts and their hashes are published.

The browser flow is station setup → camera preview or upload → observe/analyze → inspect timeline → review → export. Show recording state, real/synthetic source, coverage and quality status prominently. A user must deliberately start camera access; stopping releases ownership reliably. Implement the actual analysis, correction and feedback controls using their supported request formats.

## Milestones and delivery gates

Implement in the order below. Each milestone is a small reviewable change set against develop; create the repository-required issue before nontrivial implementation. Use plain Git and provide web links for GitHub-only issue/PR actions. Preserve the existing uncommitted resume documents.

| Milestone | Deliverable | Exit gate |
|---|---|---|
| M0 Requirements and qualification contract | Confirm planning defaults against the station, publish requirement traceability, freeze baseline/targets and profile vocabulary | Requirements, proposed thresholds and known limitations distinguish plans from existing evidence |
| M1 Reproducible baseline and correctness | Locked environment, replay-clock repair, truthful metrics, lint cleanup and regression cases | Existing suite plus new regressions pass; Ruff clean; reference labels cannot alter operational metrics |
| M2 Camera acquisition and profiles | USB/file adapters, shared clocks, recording, quality checks, profile setup and timestamp probing | One capture owner; valid lossless replay; missing frames, timing corruption and invalid profiles handled explicitly |
| M3 Shared measurement engine | Stateful task sequence, completed-event finalization, cumulative counts and recorded/live parity | Long-run fixture totals never fall; incomplete events never become completed; exact replay matches when pixels/clocks match |
| M4 Engineer workflow and evidence | Functional browser review, immutable runs, timeline/clip links and common exports | Upload and live session each complete the full review/export flow with no unsupported metric claims |
| M5 Physical camera qualification | Independent annotation, frozen session splits, nominal and changed-condition evaluation | Proposed measurement targets pass on held-out real footage; limitations reported per condition |
| M6 External pilot and release | Packaging, authenticated LAN pilot, restart/restore drills, operator guide and feedback | Independent engineer completes the workflow; operational and evidence gates pass; release follows the existing pilot gate |
| M7 Secondary Embodied AI adapter | Experiment IDs, controller/policy labels and reproducible comparison exports | Same qualified camera measurement protocol compares policies without consuming robot ground truth during inference |

M0–M6 are the primary program. M7 starts after M5 and M6 qualify; it does not introduce policy training, robot commands or a separate experiment framework.

### Physical qualification dataset

Begin with three screening sessions of approximately 20 cycles each to verify visibility and annotation instructions. These are development data, not the final test.

Collect ten complete nominal sessions with at least 60 visible cycles each. Freeze six sessions for development, two for validation and two for the untouched test: at least 360/120/120 cycles. Session assignment occurs before tuning. Add at least 100 changed-condition cycles across lighting, speed and modest camera changes, plus at least 100 independently annotated inactivity episodes. Report conditions separately rather than averaging failures away.

Two annotators independently label test cycle boundaries and observable outcomes. Preserve their disagreements and the adjudicated reference revision. Reference labels are never visible to inference. Conditions causing lost visibility belong in the coverage/UNKNOWN evaluation.

Use ordinary operator-controlled station activity. Do not induce dropped objects, unsafe grasps or interruptions merely to fill a dataset. Failures available from normal runs or consented archived footage can support a later failure-specific study; absent failure examples block failure-classification claims.

The existing larger lab dataset goals and ELEXHUB commissioning experiment remain separate. An available external station can supply the new camera qualification dataset without claiming that legacy E0 arm bring-up has completed.

### Proposed v1 acceptance targets

These are targets for this proposed roadmap, not achieved results or a silent change to the historical SDD.

| Property | Proposed gate |
|---|---|
| Cycle detection | Precision and recall each ≥95%, and F1 ≥95%, on the untouched nominal test using the two-boundary matcher |
| Session count | Absolute count error ≤2% per held-out nominal session |
| Timing | P95 absolute start/end error ≤250 ms; P95 cycle-duration error ≤500 ms |
| Visual coverage | At least 95% usable nominal observation time; excluded time remains visible and cannot improve the headline score silently |
| Repeatability | Same pixels, timestamps, profile and code produce identical event and metric artifacts; volatile job timestamps excluded from the comparison |
| Recorded/live parity | Exact event/count/timing equality when replay uses the identical archived observations and clocks |
| Live processing | Host-ingest-to-publication P95 ≤1 s and no qualified event over 5 s during a two-hour soak; sensor exposure latency is reported separately and not assumed |
| Integrity | Every reported event resolves to source evidence with validated hashes; changed evidence prevents a qualified report |
| Review | Independent engineer can inspect, correct and export a session; unreviewed outcomes remain unknown |
| Recovery | Crash/restart and restore preserve completed runs and revisions; interrupted capture intervals remain explicit |

Show numerator, denominator and uncertainty intervals alongside precision/recall. The gate applies to the stated station/profile and condition range. Changed-condition failures are either resolved before expansion or declared unsupported; they are not hidden in a pooled average.

The report records engineering acceptance decisions as ACCEPT, HOLD or REJECT chosen by a named reviewer, with a reason and report hash. Meeting measurement accuracy targets establishes the instrument's qualification, not automatically the robot installation's acceptance.

## Testing and pilot operation

Add clock-file, VFR, timestamp-gap and codec round-trip fixtures. Test missing/invalid clocks, empty clips, long idle periods, overlapping regions, marker loss, camera movement, incomplete EOF cycles and camera reconnection.

Add strict regressions for reference isolation, UNKNOWN outcomes, zero reviewed samples, session-duration denominators and unavailable precision/F1. Test stable cycle IDs, cumulative totals beyond the former 12-second window, delayed completion, out-of-order frames, overload and bounded subscriber queues.

Test real router/UI behavior: multipart notes, asynchronous analysis status, review revisions, feedback, recorded evidence seeking and both export paths. Verify authentication on HTTP and WebSocket routes, identifier/path validation, escaped user content, upload limits and form CSRF protection.

Correct the Compose context and explicit token injection; add only the selected USB device to the container. Bind local preview to loopback. A pilot uses explicit authentication and TLS on a deliberately configured LAN endpoint, with no automatic public exposure.

Archive restore must reject traversal, absolute paths, links and unexpected members before writing, restore into a new directory, validate the complete manifest, and require a deliberate replacement step. Test tampered/missing evidence, full disk and crashes while writing a run or segment.

Retain source, event, profile and environment provenance. Log camera state, observation rate, missing-frame rate, queue depth, disk headroom, job state, clock quality and measured processing latency. Use these local records for qualification; no recurring monitoring service or notification automation is introduced by this plan.

Package a versioned container and run the recorded and USB scenarios from a clean environment before an external pilot. Publish v0.2.0 only after the pilot and repository review/release gates pass. Keep the prior container and evidence format readers for rollback; new outputs remain versioned and old evidence is not rewritten.

## Secondary research and expansion

After the primary gates, add an optional experiment reference containing project/run ID, controller or policy label and a fixed evaluation protocol. Signoff reads and measures video; RoboLLM/RLM continue to own robot execution and learning.

Later candidates are RTSP reconnect/timestamp qualification, marker-free detection benchmarked against the qualified baseline, additional task profiles, automatic visual outcome classification, multiple stations and learned perception. Each requires its own dataset and gate. Do not expand them merely because the initial software tests pass.

Hardware purchases, physical arm commissioning, cloud/GPU spending, infrastructure changes and public deployments are separate execution decisions. The first implementation task is M0/M1; no hardware commissioning is required to repair the observed software defects.

## Source and method references

- Existing requirements and dated baseline: [README](../README.md), [project status](PROJECT_STATUS.md), [restart findings](RESUME_PLAN.md), [Phase 2 plan](plans/phase2-plan.md) and [preflight receipts](validation/2026-10-13/checks.json).
- OpenCV documents marker IDs and corner detection in [its ArUco tutorial](https://docs.opencv.org/5.0/tutorials/objdetect/aruco_detection/aruco_detection.html). This supports the method choice; it does not establish accuracy at this station.
- PyAV documents decoded frame timing as presentation timestamps multiplied by their time base in [its timing reference](https://pyav.org/docs/stable/api/time.html).
- [FFprobe documentation](https://ffmpeg.org/ffprobe.html) supports probing actual media streams and frame metadata.


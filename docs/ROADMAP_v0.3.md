# Signoff v0.3 GUI and real camera plan

Draft dated 13 October 2026. This plan extends the camera-first program with an operator GUI and a first real USB-camera pilot. The owner selected a USB webcam connected to this computer. Camera measurement remains primary; Embodied AI comparison remains secondary.

The recommended next feature release is **v0.3.0**, delivered first as **v0.3.0-rc.1**. Keep v0.2.x for corrective changes to a released v0.2 line. The existing package is v0.2.0-rc1, not a physically qualified production release. GUI work and physical screening can proceed together; there is no requirement to publish v0.2.0 before developing v0.3.

Planning context: [issue #1](https://github.com/santapong/signoff/issues/1), [camera-first roadmap](CAMERA_FIRST_PLAN.md), [requirements](REQUIREMENTS_v0.2.md) and [capture guide](CAPTURE_GUIDE.md). This document adds proposed GUI work; it does not mark physical milestones complete or change the existing accuracy thresholds.

## Camera access and first test

Recorded real-world video can be uploaded and measured without direct camera access. Live testing requires the local Signoff process to open the explicitly selected USB device. The owner chooses the field of view and operates the station; the assistant helps inspect observations, label evidence with disclosed assistance, diagnose software and compare results.

Use the existing server-side USB capture on this computer. The browser displays frames from that capture process. A browser-native webcam adapter would be a different acquisition path with its own frame transport and clock contract. Browser camera APIs require user permission; they are not a substitute for the existing server source. See [MDN getUserMedia](https://developer.mozilla.org/en-US/docs/Web/API/MediaDevices/getUserMedia).

The proposed lifecycle is **Choose camera → Open preview → Draw task regions → Save profile → Start recording → Stop and preserve → Analyze → Review → Export**. Opening preview and starting recording are distinct deliberate actions. Navigating, signing in, reloading or restarting Signoff must not reopen a device automatically. The application indicates camera-open and recording states separately; closing preview releases the device.

First live evidence is three screening sessions of approximately 20 normal cycles each, including inactive time before and after the runs. Position the fixed camera to see HOME, PICK and PLACE and both markers. Measure actual negotiated resolution, usable frame rate, marker visibility and clock gaps before freezing the station profile. Screening is development evidence, not the held-out qualification dataset. Physical camera operation has not been performed by this planning task.

## Version policy

The version split is a project recommendation: SemVer distinguishes bug fixes from new functionality and explicitly treats 0.y.z as initial development. See [Semantic Versioning](https://semver.org/).

| Version | Intended scope | Release condition |
| --- | --- | --- |
| Next candidate on the current development line | Correctness fixes discovered during screening, accurate job/error states and preservation of evidence | Meaningful affected regression checks; preserve previously built candidate artifacts |
| v0.2.1 and later, if v0.2.0 is released | Backward-compatible fixes to the existing workflow | Backport only when a maintained v0.2 installation needs the correction |
| v0.3.0-rc.1 | Guided station setup, preview-only camera operation, recording/review workspace and reference/qualification GUI | Software acceptance criteria below; pending physical qualification remains explicit |
| v0.3.0 | Qualified operator workflow at the stated station/profile | Real dataset, live soak, owner pilot and repository release requirements pass |
| v0.3.1 and later | Corrective fixes to the released v0.3 workflow | Scope-appropriate regression and evidence checks |
| Later feature version | Additional sources, tasks or perception methods | A separate bounded benchmark and source-specific qualification |

Do not maintain two feature programs merely to preserve version labels. Integrate the GUI and applicable fixes through develop. Preserve schema-v2 profile, run, review and report readers and frozen old artifacts. Use the branch policy's dotted prerelease style for new tags; retain the historical rc1 package identity unchanged. Production tags belong on main after its PR/approval rules are met.

## Current product gaps

Baseline inspected: clean develop at e14eae66f982b70f88431cf4deb0f3f669ec190c. These are code-based findings; a new browser inspection did not complete.

| Current behavior | Change needed |
| --- | --- |
| Station setup is a JSON textarea; its station column expects an unsupported station_id field | Named station forms and visual regions, using the supported profile name and immutable revisions |
| Live preview requires record=true and an already saved profile | A setup-only preview lifecycle before recording |
| USB selection requires a numeric device index | Named local video-device choices, with explicit probing and a manual fallback |
| Camera quality, coverage and events are rendered largely as raw JSON | Readable readiness, recording, quality and measurement panels |
| Analysis progress is not restored on page reload; repeat clicks can queue additional runs | Active-job lookup, status recovery and prevention of duplicate user submissions |
| Outcome review covers detected cycles; it cannot establish independent boundary references | A separate blinded reference editor using original source frames/clocks |
| The owner decision form omits qualification/manifest bindings already accepted by the backend | Evidence selection and automatic exact hash binding, with a downloadable decision receipt |
| The qualifier checks cycle splits and scores; the roadmap also requires inactivity and live-soak evidence | Explicit inactivity-scoring and soak receipts checked by the release gate |

Relevant source: profiles.html, live.html, session.html, ui.py, live.py, live_worker/engine.py, tools/annotate_reference.py, tools/qualify_dataset.py and tools/release_gate.py. The live loop's wait after acquisition/processing is a rate concern to measure on hardware, not an established real-camera failure.

## GUI direction

Build a local web application that feels like an operator console. Keep FastAPI, Jinja templates and browser JavaScript for the first implementation. Extract shared styles and focused JavaScript modules, with an SVG/canvas overlay for region editing. A framework migration or native desktop wrapper is not needed to make this workflow graphical.

Use a stable navigation rail for Stations, Capture, Sessions and Qualification. Make the main area camera/video-led: image and region overlays at the center, readiness or cycle review beside it, and recording/timeline controls below. Keep one primary next action per state. On narrow windows, stack panels and preserve the stop control.

Show state in text as well as color. Separate Camera open, Recording, Processing, Review required, Provisional and Qualified for this profile. Keep real/rehearsal/simulation source identity visible. Display cycles, duration, throughput, coverage and confirmed outcomes with their denominators. Put hashes, configuration JSON and diagnostic traces in an expandable Evidence details area. No sample numbers appear as actual station measurements.

Independent reference annotation is its own workspace. It opens original footage without predictions, detected boundaries or outcome suggestions. Post-analysis review remains a separate correction workflow. A different tab over the same prediction data is insufficient to establish blinded references.

## Implementation sequence

The G milestones below concern the new GUI program; they do not replace the original M0–M7 physical acceptance states.

| Step | Deliverable | Concrete completion criterion |
| --- | --- | --- |
| G0 Workflow and visual draft | Shared app shell and representative setup, capture and review layouts using existing capabilities | The owner can understand the complete journey; conceptual controls and unavailable data are labeled; ordinary setup contains no required JSON |
| G1 Camera setup | Local device list, explicit preview-only open/close, actual resolution/rate and marker readiness, snapshot-based HOME/PICK/PLACE region drawing | Preview creates no recording; conflicting ownership is refused; saving creates a new validated immutable profile; regions map correctly when the view is resized |
| G2 Recording workspace | Visible camera/recording states, counters, duration, coverage, disk headroom and actionable problems; stop-and-preserve then analyze | A short owner-operated session is recoverable and replayable; device loss/full disk preserve prior evidence and explicit gaps; page reload does not start a source |
| G3 Sessions and evidence review | Unified upload/live entry, session names/history, recoverable job progress, synchronized cycle inspection, outcome review and simple exports | Reload restores queued/running state; repeated clicks do not enqueue accidental duplicate runs; review does not alter detections; changed reviews request a fresh frozen report |
| G4 Independent reference editor | Original-frame navigation, keyboard stepping, start/end marking, UNKNOWN outcomes, reviewer identity and immutable reference revisions | Boundaries use actual source frame IDs and clocks, not playback time multiplied by FPS; predictions are absent from requests and rendered state; prior revisions remain unchanged |
| G5 Qualification and owner decision | Freeze/split checklist, reference/adjudication lineage, per-condition results, explicit soak/inactivity receipts, exact report selection and owner acceptance | Missing evidence remains PENDING; selected inputs and receipts match exact hashes; owner decision and release eligibility can be inspected/downloaded without CLI use |
| G6 Qualified pilot and release | Complete owner workflow, camera soak, recovery exercise, package and concise operator guide | Published physical targets pass, owner accepts the selected frozen report, required main review passes, and final artifacts retain source identity |

The first usable increment is G0–G2: visual setup and a trustworthy recording screen. Continue G3–G5 while gathering physical data. Do not delay the first screening until every later workspace is polished.

### Backend work supporting the GUI

Extend the existing capture owner into a small explicit state machine: CLOSED, PREVIEW and RECORDING, with stop/error transitions. Preview uses the same acquired frames as its display and diagnostics. Recording starts its own session clock and archive through that owner; it must not create a second OpenCV capture. Profile edits occur before recording; changing regions during recording requires a new profile and session.

Enumerate local video devices without opening them. Inspect capabilities only after the operator selects an explicit probe/preview action. Retain a numeric-index fallback and bind the chosen identity to the existing source/profile contract. On Linux, investigate V4L2 metadata and stable device paths before implementing discovery; do not assume every video node is a capture camera.

Add APIs for preview lifecycle, source-frame retrieval/reference revisions, active analysis status and qualification operations only where existing endpoints are insufficient. Keep job work bounded and persistent, reuse the existing annotation/qualification/report functions, and protect new HTTP/WebSocket operations with the same authentication and CSRF conventions.

The reference editor needs an indexed view of original decoded/captured frame IDs and timestamps. A viewing proxy can aid navigation, but the selected source frame and its actual clock remain authoritative. Boundary edits create new reference revisions; they never overwrite detector events or previously frozen reports.

Qualification display must derive from saved receipts. Add an explicit live-soak receipt and independent inactivity scoring so the UI cannot show full readiness from cycle scores alone. Preserve existing qualification thresholds and record any new receipt schema/version. The readiness panel is a setup aid, not a certificate of physical accuracy.

## Physical qualification and owner pilot

Keep the existing nominal protocol: ten complete sessions with at least 60 visible cycles each, split before tuning into six development, two validation and two untouched test sessions, with at least 360/120/120 cycles. Add at least 100 changed-condition cycles and 100 independently annotated inactivity episodes. Preserve independent reference artifacts, declared human/AI-assisted reviewer kinds and named human adjudication.

Maintain precision/recall/F1 at least 95%, per-session count error at most 2%, P95 boundary error at most 250 ms, P95 duration error at most 500 ms, usable coverage at least 95% and usable observation rate at least 10 FPS. The two-hour live soak targets host-ingest-to-publication P95 at most one second and no qualified event over five seconds. Host receipt timing is not sensor exposure timing.

Owner and assistant conduct the OWNER pilot together. This is not external independent validation. Completing a visible task sequence does not certify object transfer, product quality or robot safety. Production eligibility requires physical qualification and the owner's selected ACCEPT receipt with exact report, manifest and qualification bindings.

Reuse frozen footage for software regressions and compatible requalification. Do not recollect the entire dataset for a cosmetic GUI change. When acquisition, detector/profile or applicable hardware conditions change, determine which physical checks must be repeated. Never substitute new commit identities into old frozen evidence; create new reports/receipts where their bound inputs change.

## Research ideas to adapt

The following sources were read on 13 October 2026. They inform proposed interaction patterns; they do not establish Signoff implementation or station accuracy.

| Primary reference | Documented pattern | Proposed Signoff adaptation |
| --- | --- | --- |
| [CVAT editor](https://docs.cvat.ai/docs/annotation/annotation-editor/) | Central media workspace with navigation and separate object/control panels | Video-led review with compact cycle selection and outcome controls |
| [CVAT navigation](https://docs.cvat.ai/docs/annotation/annotation-editor/navbar/) | Single-frame stepping, keyboard controls, explicit save and distinct annotation/review modes | Exact source-frame boundary editor, keyboard review and independent reference mode |
| [Roboflow Workflows](https://docs.roboflow.com/workflows) | Browser authoring with one workflow usable on files and live streams, including local deployment | Preserve one Signoff measurement pipeline across uploaded and captured sessions; show the operator the same review/report experience |

These are interaction and architectural inspirations, not dependency selections. Signoff does not need to install CVAT or Roboflow or route station footage to their cloud services. Website reference review was based on official documentation; no fresh live-product visual inspection is claimed.

Prioritize setup readiness, a clear next action, interrupted-session recovery, blinded labeling, per-condition diagnostics and a qualification report that explains what remains missing. Later candidates are RTSP with tested reconnect/clocks, a new task template, or a marker-free detector evaluated against the qualified marker baseline. Keep automated outcome classification, multiple simultaneous stations and native packaging outside the first GUI increment. RLM/RoboLLM continue to own robot execution and learning.

## Delivery and verification without excess review

Use at most three implementation workers: camera ownership/setup APIs; GUI shell/setup/capture/review; reference/qualification/report workflow. Agree once on the preview/source-frame/job contracts, then work in parallel against those contracts. Root handles integration, the owner pilot and release evidence.

Use one program issue before feature implementation and a small number of coherent PRs to develop. Track this draft under the existing camera program until the v0.3 implementation issue is opened. CONTRIBUTING retains older main-target wording; BRANCH_POLICY and the established camera program use develop for feature integration and main for release.

Run focused tests for ownership/state transitions, region transforms, job recovery, reference isolation and receipt integrity. Reuse the existing measurement regressions. At integration, run the suite once and exercise one complete browser journey on desktop and a narrow viewport, including keyboard control and observed error states. Repeat a check when code changed or a failure remains, rather than adding repeated review rounds.

Proposed usability target: with Signoff already running and the camera positioned, the owner creates a station and starts the first recording within five minutes using ordinary controls. Verify this during the pilot; it is not an achieved result. The ordinary session-to-export flow must not require JSON editing or terminal commands.

This planning draft changes no runtime, camera, recordings, release artifacts or qualification outcomes. The immediate implementation priority is **G1 preview-first camera setup with visual task regions**, delivered within the G0–G2 usable increment.

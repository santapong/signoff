# Signoff v0.2 requirements and execution contract

Implementation through M7 and release preparation was authorized on 13 October 2026. Work is tracked in [issue 1](https://github.com/santapong/signoff/issues/1). The owner requested multi-agent work, focused checks and research when knowledge is insufficient; execution used three bounded workers. This document freezes the implementation requirements from CAMERA_FIRST_PLAN.md; historical phase labels are not proof of physical qualification.

| ID | Requirement | Verification |
|---|---|---|
| CAM-01 | Explicit fixed USB/file source; one capture owner; immutable profile revisions | Profile and workflow tests plus real owner capture later |
| CLK-01 | Preserve decoded/captured timestamps and expose invalid/gapped clocks | Recorded-sidecar, VFR and archive replay tests |
| VIS-01 | Qualified marker sequence follows HOME/PICK/PLACE/HOME and finalized boundaries | Marker fixtures, partial/gap tests and physical dataset later |
| CNT-01 | Stable event IDs and monotonically cumulative session totals | Long-running generated stream test |
| MET-01 | Operational metrics use detections; references cannot change them | Reference-isolation regression |
| MET-02 | Unavailable values and UNKNOWN outcomes remain explicit | Empty/unlabeled/partial-review regressions |
| EVD-01 | Immutable hashed evidence and report versions | Artifact/export/tamper tests |
| REV-01 | Working upload, analysis, review, feedback and OWNER acceptance | Router tests and visible browser workflow |
| SEC-01 | Authenticated HTTP/WebSocket, bounded stream and safe restore | Security/recovery regressions |
| PHY-01 | Frozen real-station test set meets declared accuracy/coverage targets | qualify_dataset report on owner recordings; pending |
| PIL-01 | Owner operates and accepts a report; assistant is software support | OWNER receipt tied to report hash; pending |
| CMP-01 | Optional policy/controller comparison consumes reports without robot-state inference | Comparison tests; provisional until PHY-01/PIL-01 |
| REL-01 | Reproducible package, container smoke test and reviewed develop/main release path | Software receipt, CI/PR and qualified release gate |

No real recordings exist yet according to the owner. Generated robot-marker fixtures and hand-operated rehearsal footage are development evidence. Public BridgeData/DROID footage can supplement perception work but does not qualify this station. Software completion and physical acceptance are recorded separately.

The historical full SDD remains owner-held and unavailable locally. The new implementation uses the explicit camera-first roadmap; legacy ELEXHUB commissioning is retained as a separate hardware activity and is not marked complete by this program.

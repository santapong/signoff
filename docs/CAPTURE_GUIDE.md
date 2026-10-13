# Signoff first recordings and owner pilot

The owner reported that station recordings do not exist yet and selected a pilot conducted together with the assistant. The first step is a short owner-operated capture, followed by labeled scoring. A software demo, a human tabletop rehearsal and a physical robot qualification remain distinct evidence.

## Collect the first screening footage

Use the station's normal operator-controlled cycle. Place one fixed USB camera so HOME, PICK and PLACE remain visible throughout the task. Avoid moving the camera between sessions. Keep the background and lighting steady for the initial screening.

Print the reference and tool markers with `venv/bin/python tools/print_markers.py --out data/markers`. These are visual identity markers, not dimensional calibration artifacts. Place reference ID 0 in the fixed scene and tool ID 1 where it stays visible without interfering with the robot. Adjust the image regions in the station profile to match the actual layout.

Start Signoff locally, create a profile, then deliberately start the selected USB source with recording enabled. Record three sessions of about 20 normal cycles each. Preserve brief inactive periods before and after the runs. Stop camera capture in the UI and retain the session ID and archived files. No robot motion is commanded by Signoff.

If robot hardware is still being prepared, practice the upload/review workflow with a hand-operated tabletop transfer. Mark the source `manual_tabletop_rehearsal`. Generated marker footage is `simulation`. Neither source qualifies the robot measurement system.

## Qualify with frozen sessions

After screening, collect the roadmap's complete session dataset and freeze the development/validation/test split before changing detector settings. Independently label the visible cycle boundaries; preserve each reviewer identity and identify an assistant-assisted annotation as AI-assisted. Do not use detector outputs as reference labels.

The reference path is headless. First export actual frame clocks:

```sh
venv/bin/python tools/annotate_reference.py MEDIA --map-only --frame-map source-frames.csv
```

Each reviewer watches the original recording externally in VLC/mpv, without detector predictions, and authors a CSV with `cycle,start_frame,end_frame,outcome,failure_type`. Import it using:

```sh
venv/bin/python tools/annotate_reference.py MEDIA --csv owner.csv \
  --session SESSION_ID --annotator owner --out references/owner.jsonl
```

MEDIA may be a finalized recording manifest. The importer maps boundaries through the actual source clock, defaults outcomes to UNKNOWN and refuses to overwrite prior annotations. The legacy OpenCV GUI annotator is not supported by the chosen headless package. Post-analysis review in the browser is a separate correction workflow and cannot establish an independently blinded reference set.

Start from `QUALIFICATION_MANIFEST.example.json`. Session entries contain session ID, split, relative video/reference/profile paths, SHA-256 for each file and the immutable profile hash. The data root is an explicit local directory; raw footage stays outside Git. Test with `venv/bin/python tools/qualify_dataset.py MANIFEST --data-root DATA --out data/qualification.json`. Exit 2 means pending/not qualified and retains reasons. It is not a passing score.

Freeze `PROTOCOL.example.json` under the data root and store its SHA-256 in the manifest's `protocol_sha256`. Preserve two separate annotation artifacts per held-out session, declare reviewer kinds `human` or `ai_assisted`, and link them through a named human adjudication with its reason and reference hash. Recording clock sidecars are also frozen; duplicate footage cannot inflate split denominators.

The owner attests which footage is physical. The tool verifies hashes, inputs, split isolation and measured accuracy; it does not independently authenticate a real-world capture from a source label alone.

## Owner pilot

The owner operates the station and reviews a complete session. The assistant checks the software, evaluates the frozen observations and explains uncertainty. Record reviewer kind `OWNER`, the selected report hash, ACCEPT/HOLD/REJECT and a reason. This receipt is an owner pilot; it must not be described as external independent validation.

For production eligibility, the owner acceptance must also bind the actual report file SHA-256, qualification receipt SHA-256 and frozen manifest SHA-256. The acceptance API computes the report file hash; provide the qualification and manifest hashes after reviewing the physical result. `tools/release_gate.py` verifies those bindings and the exact qualified source/profile/configuration/code/protocol before allowing production eligibility.

A production release still needs actual station accuracy evidence and an accepted owner pilot. Until those receipts exist, the software package remains a release candidate.

## Supplementary public robot footage

[BridgeData V2](https://rail-berkeley.github.io/bridgedata/) provides robot manipulation demonstrations and a separate scripted pick-and-place collection. [DROID's official dataset guide](https://droid-dataset.github.io/droid/the-droid-dataset) provides raw camera videos and a documented 100-episode debugging subset. Use a small licensed subset for perception exploration rather than downloading complete multi-terabyte collections.

These datasets have different cameras, tasks and often no Signoff markers. They can support supplementary experiments, but cannot replace qualification of this station/profile. Preserve publisher attribution, source metadata and sampling/timestamp limitations. No public data was downloaded by this implementation task.

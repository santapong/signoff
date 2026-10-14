# Signoff

Signoff measures visible robot task sequences from a fixed camera and produces reports whose evidence an automation engineer can inspect. Camera measurement is the primary product; comparison of Embodied AI policies is an optional secondary application.

**Current package: v0.3.0-rc.1, a software release candidate.** The Evidence Workbench GUI provides visual station setup, preview, capture, cycle review, independent references and qualification. Physical station qualification and owner-pilot acceptance remain pending real recordings.

## What an engineer can do

Draw HOME, PICK and PLACE regions on a preview frame and save an immutable station profile. Upload MP4/MKV footage or explicitly open one USB camera preview, then separately start recording. Inspect the cycle timeline and clips, append outcome reviews and export a frozen report. Analysis progress survives page reload; matching active requests reuse the same job.

Independent references use a separate original-frame editor with real source clocks and disclosed reviewer kinds. The Qualification workspace assigns session splits, preserves annotation/adjudication lineage, freezes inputs and evaluates saved evidence. Actual inactivity and live-soak receipts are required alongside cycle qualification; owner decisions bind the selected report and receipts.

Cycle completion is a visible sequence measurement. Outcome success uses confirmed human reviews; unreviewed and uncertain outcomes stay UNKNOWN. Detection F1 is unavailable without a separate identified reference set. Visual activity is distinct from machine availability, product quality and safety acceptance.

## Local setup

Use Python 3.12 and FFmpeg. Runtime and developer dependency locks include hashes.

```sh
uv venv venv --python 3.12
uv pip sync --python venv/bin/python --require-hashes requirements-dev.lock
```

Set a private `SIGNOFF_TOKEN` in your terminal environment. Then start the local application:

```sh
SIGNOFF_REQUIRE_AUTH=1 SIGNOFF_DATA_DIR=data/local-pilot \
  venv/bin/python -m uvicorn apps.api.main:app --host 127.0.0.1 --port 8600
```

Open `http://127.0.0.1:8600/ui`, sign in, create a station profile and upload footage. Camera access starts only when the operator explicitly chooses a source and presses Start. The default source never silently selects a synthetic camera.

For an exported ZIP or installed wheel, set `SIGNOFF_GIT_SHA` to its candidate manifest's exact source commit. An unidentified build can rehearse the workflow, but cannot qualify the measurement instrument. An unrelated parent Git repository is never used as the package's identity.

## Rehearse before physical collection

```sh
venv/bin/python tools/generate_demo.py --out data/demo-camera-v2 --lossless
venv/bin/python tools/print_markers.py --out data/markers
```

The demo is a clearly labeled four-cycle simulation with an independent scripted reference, a profile and optional lossless archive. Its destination must be empty. It cannot qualify a physical station. Synthetic live input is available only with `SIGNOFF_ALLOW_SYNTHETIC=1` in a deliberately local development process.

Independent references can be authored with the headless `tools/annotate_reference.py` frame-map/CSV workflow. It never runs the detector or opens a GUI.

The GUI's Independent reference action also supports original-frame navigation, immutable cycle/inactivity references and named adjudication. Label before viewing predictions and declare AI assistance. Do not use post-analysis outcome review as an independent reference.

See [first recordings and owner pilot](docs/CAPTURE_GUIDE.md) to collect actual station footage.

## Container

The image uses a pinned Python base digest and hashed runtime dependencies. Set `SIGNOFF_TOKEN`, then:

```sh
SIGNOFF_GIT_SHA=$(git rev-parse HEAD) docker compose -f infra/docker-compose.yml up --build -d
```

The default port binds loopback. Add `-f infra/docker-compose.usb.yml` only for an operator-selected USB device. Public exposure and TLS configuration are separate deployment actions.

## Qualification and release

Freeze complete-session splits and independent annotation artifacts before tuning. The qualifier verifies hashes, clocks, preserved reviewers/adjudication, denominators, boundary errors and coverage. It does not verify a physical capture from a source label alone.

```sh
venv/bin/python tools/qualify_dataset.py MANIFEST --data-root DATA --out data/qualification.json
venv/bin/python tools/release_gate.py --qualification data/qualification.json \
  --pilot OWNER_RECEIPT --report FROZEN_REPORT --manifest MANIFEST --data-root DATA
```

Exit 2 means PENDING and retains the release-candidate channel. Production eligibility requires real station qualification and a named OWNER ACCEPT decision bound to the exact report, dataset and qualification receipt. Owner/assistant collaboration is not external independent validation.

Use the GUI for dataset assignments, freeze, scoring and owner evidence selection. Existing manifest directories can be made available through an explicitly configured `SIGNOFF_QUALIFICATION_ROOT`; the UI does not read arbitrary filesystem paths. Physical readiness requires the two-hour source-bound live soak and at least 100 independently labeled inactivity episodes. Inactivity scores are descriptive and reviewed by the owner; the published numeric cycle targets remain unchanged.

Build source/wheel candidates from a clean checkout with `venv/bin/python tools/build_candidate.py --out dist/NEW-CANDIDATE`. The pinned build backend must be available in uv's cache for its offline build. The builder does not merge, tag, publish or approve physical evidence.

## Secondary experiment comparison

```sh
venv/bin/python tools/compare_experiments.py experiments.json --out data/comparison
```

The manifest selects frozen report files and declares experiment/controller/policy labels plus a fixed protocol. Incompatible or tampered reports are rejected. Missing physical qualification keeps the comparison provisional. Signoff does not train or control robots.

## Documentation

- [Runtime architecture and maintenance boundaries](docs/ARCHITECTURE.md)
- [Camera-first roadmap](docs/CAMERA_FIRST_PLAN.md)
- [Requirements and implementation contract](docs/REQUIREMENTS_v0.2.md)
- [Current milestone status](docs/PROJECT_STATUS.md)
- [Capture and owner-pilot guide](docs/CAPTURE_GUIDE.md)
- [Release candidate](docs/RELEASE_v0.2.0-rc1.md)
- [v0.3 GUI program](docs/ROADMAP_v0.3.md)
- [v0.3 release candidate](docs/RELEASE_v0.3.0-rc.1.md)
- [Branch policy](BRANCH_POLICY.md) and [contributing](CONTRIBUTING.md)

Raw footage and model weights stay outside Git. The legacy full SDD is owner-held; its local file remains a placeholder. Earlier physical acceptance claims are not inferred from that placeholder or from software tests.

MIT license.

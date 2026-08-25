# Contributing to Signoff

Thank you for contributing. Signoff is a measurement system: correctness,
reproducibility, and honest uncertainty matter more than cleverness.

## Code of conduct

Be direct, evidence-based, and respectful. Critique measurements and code, not people.

## How to contribute

1. **Open an issue first** for anything non-trivial (features, experiment designs,
   architecture changes). Reference the SDD section it affects.
2. **Fork / branch** following [BRANCH_POLICY.md](BRANCH_POLICY.md).
3. **Develop with tests** (see below).
4. **Open a pull request** against `main`.

## Development setup

```bash
git clone https://github.com/<you>/signoff.git
cd signoff
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"        # once packaging exists; for now:
pip install -r requirements-dev.txt
pre-commit install
```

System dependencies for video work: `ffmpeg`, `libopencv-dev` (or `pip install opencv-python-headless`).

## Testing requirements

- Every PR must pass `pytest` and `ruff check`.
- Acceptance-critical logic (percentiles, cycle duration, availability, confidence
  thresholds, hashing) targets near-100% coverage.
- If your change affects detection/segmentation output, run the golden-video tests
  (`tests/golden/`) and update expected outputs **deliberately**, explaining why in
  the PR.

## Project-specific rules

These come from the SDD and are non-negotiable in code review:

1. **No LLM/VLM in the measurement critical path.** Deterministic CV + rules only
   for acceptance-critical numbers (ADR-005).
2. **Unknown is valid.** Never force a low-confidence classification into a
   confident label. Emit `UNKNOWN` / `REVIEW_REQUIRED` (ADR-006).
3. **Every metric links to evidence**: frame ranges, clip hashes, config hash,
   git SHA, model version (ADR-003, ADR-007).
4. **Never overwrite model predictions with human corrections** — store both
   separately (ADR-004).
5. **No raw videos, datasets, or secrets in Git.**
6. **Safety code changes** (`robotics/`, joint limits, e-stop paths) require two
   approvals and explicit test evidence.
7. Reproducibility gate: reprocessing identical video with identical
   code/model/config must produce identical metrics (AC-REPRO-001).

## Pull request checklist

- [ ] Branch follows naming policy; commits are conventional-commit style
- [ ] Tests added/updated; `pytest` passes locally
- [ ] `ruff check` clean
- [ ] No large binaries, datasets, or credentials added
- [ ] Evidence/provenance fields updated if you touched metrics or reports
- [ ] Docs updated if behavior or interfaces changed
- [ ] For experiments: hypothesis, protocol ID, and result summary in the description

## Reporting bugs

Include: what happened, what was expected, minimal reproduction, software git SHA,
configuration hash, and (for analysis issues) an anonymized short video clip link —
never attach raw customer/lab footage to issues.

## Security

Do not open public issues for security vulnerabilities. Contact the maintainer
directly (@santapong) or use GitHub's private vulnerability reporting.

## License

By contributing you agree your contributions are licensed under the MIT License.

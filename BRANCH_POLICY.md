# Branch Policy

**Status:** Active · **Applies to:** all contributors

## Branch model

This project uses a lightweight GitHub-flow variant suited to a research prototype
moving toward a commercial MVP.

```
main            always deployable; protected; releases are tagged from here
 ├── develop    integration branch (optional, enable after v0.2)
 └── feat/*     feature work
     fix/*      bug fixes
     exp/*      experiments (E0–E9), never merged without a result summary
     docs/*     documentation only
     chore/*    tooling, CI, deps
```

## Branch rules

| Branch | Source | Merges to | Protection |
|--------|--------|-----------|------------|
| `main` | — | — | Protected: PR required, ≥1 approval, linear history, no force push |
| `develop` (later) | `main` | `main` via PR only | PR required |
| `feat/*`, `fix/*`, `docs/*`, `chore/*` | `main` (or `develop`) | back via PR | None |
| `exp/*` | anywhere | `experiments/` results land as reports, not code by default | None |

## Naming convention

```
feat/cycle-detection-baseline1
fix/p95-percentile-rounding
exp/E1-camera-position
docs/sdd-section-15
chore/ci-pytest
```

Lowercase, hyphen-separated, prefix mandatory. Include the issue number when one exists:
`feat/42-cycle-detection`.

## Commit messages

Conventional Commits style:

```
feat(cv): marker-based cycle segmentation baseline
fix(metrics): correct P95 interpolation for n<20
docs: add E2 experiment protocol
```

## Rules of record

1. **Never commit directly to `main`.** All changes arrive via pull request.
2. One logical change per PR. Experiments (`exp/*`) must include an
   `EXPERIMENT_ID`, hypothesis, and result summary in the PR description.
3. Raw videos, datasets, and model weights are **never committed** — they go in
   object storage referenced from `datasets/manifests/`.
4. Any change touching acceptance-critical math (`packages/metrics`,
   `packages/temporal`) requires unit tests and a golden-video test update.
5. Rebase or merge `main` into your branch regularly; squash-merge is the default.
6. Delete your branch after merge.

## Versioning

Semantic versioning (`v0.1.0` …). Tags on `main` only. Pre-releases use `-rc.N`.
See SDD §72 for what each version means.

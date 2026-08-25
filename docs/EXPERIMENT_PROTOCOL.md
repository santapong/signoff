# Experiment Protocol

Every experiment (E0–E9, SDD §§47–56) gets an entry here before it runs.

Template:

```yaml
experiment_id: EXP-XXX-000
git_sha: <filled at run time>
dataset_version:
camera_profile:
algorithm:
config:
hypothesis:
primary_metric:
result:
conclusion:
```

Rules:
- Register the experiment before collecting data.
- Ground-truth logs are never consumed by inference during evaluation.
- Report performance per condition, not just averages.
- Classify conclusions as OBSERVED / MEASURED / INFERRED / HYPOTHESIZED.

| ID | Question | Status |
|----|----------|--------|
| E0 | Can the arm execute 100 scripted cycles reliably? | planned |
| E1 | Which camera position maximizes cycle-detection F1? | planned |
| E2 | Camera-only cycle detection ≥95% F1? | planned |
| E3 | Lighting robustness degradation profile | planned |
| E4 | Camera perturbation / calibration invalidation | planned |
| E5 | Object generalization | planned |
| E6 | Robot-speed generalization | planned |
| E7 | Failure dataset construction | planned |
| E8 | Human vs Signoff annotation agreement | planned |
| E9 | No-ML baseline vs learned system trade-off | mandatory before any AI claim |

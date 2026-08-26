"""E8 instrument: inter-annotator agreement (Cohen's kappa)."""
from __future__ import annotations


def cohens_kappa(labels_a: list[str], labels_b: list[str]) -> float:
    """Kappa over paired outcome labels. Raises on length mismatch."""
    if len(labels_a) != len(labels_b):
        raise ValueError("annotator label lists must have equal length")
    n = len(labels_a)
    if n == 0:
        return 0.0
    categories = sorted(set(labels_a) | set(labels_b))
    observed = sum(1 for a, b in zip(labels_a, labels_b) if a == b) / n
    counts_a = {c: labels_a.count(c) / n for c in categories}
    counts_b = {c: labels_b.count(c) / n for c in categories}
    expected = sum(counts_a[c] * counts_b[c] for c in categories)
    if expected >= 1.0:
        return 1.0
    return round((observed - expected) / (1 - expected), 4)


def agreement_report(annotations_by_annotator: dict[str, list[dict]]) -> dict:
    """Pairwise kappa over outcome labels keyed by cycle number.

    annotations_by_annotator: {'alice': [{'cycle': 1, 'outcome': 'OK'}, ...]}
    """
    annotators = sorted(annotations_by_annotator)
    by_cycle = {
        name: {a["cycle"]: a.get("outcome", "OK")
               for a in rows}
        for name, rows in annotations_by_annotator.items()
    }
    pairs = []
    for i in range(len(annotators)):
        for j in range(i + 1, len(annotators)):
            a_name, b_name = annotators[i], annotators[j]
            common = sorted(set(by_cycle[a_name]) & set(by_cycle[b_name]))
            la = [by_cycle[a_name][c] for c in common]
            lb = [by_cycle[b_name][c] for c in common]
            pairs.append({
                "annotators": [a_name, b_name],
                "n_common_cycles": len(common),
                "kappa": cohens_kappa(la, lb) if common else None,
            })
    return {"pairs": pairs}

"""Statistics behind the data dashboard: drift, histograms, box plots and robust outliers.

Pure functions over plain lists, so they are easy to test and cheap to run on the catalog
(a few thousand rows). Drift follows the population stability index (PSI) used by model
monitoring tools: reference shares come from the training data, current shares from the
catalog, both counted in the same bins.
"""

from __future__ import annotations

import math
from typing import Dict, Iterable, List, Optional, Sequence

import numpy as np

# Usual PSI reading: below 0.1 stable, 0.1 to 0.25 moderate shift, above 0.25 major shift.
PSI_MODERATE = 0.1
PSI_MAJOR = 0.25
# Empty bins would make PSI infinite; every share is floored at this value.
PSI_FLOOR = 1e-4
# Iglewicz and Hoaglin: a modified z-score above 3.5 marks a likely outlier.
MODIFIED_Z_THRESHOLD = 3.5
DECILE_BINS = 10


def drift_status(psi: float) -> str:
    """Name the size of a PSI value: "stable", "moderate" or "major"."""
    if psi < PSI_MODERATE:
        return "stable"
    return "moderate" if psi < PSI_MAJOR else "major"


def population_stability_index(reference_shares: Sequence[float], current_shares: Sequence[float]) -> float:
    """PSI between two distributions over the same bins.

    Args:
        reference_shares: Share of the reference data in each bin (sums to 1).
        current_shares: Share of the current data in the same bins.

    Returns:
        float: sum of (current - reference) × ln(current / reference), with shares floored at
        PSI_FLOOR; 0 means identical distributions.
    """
    total = 0.0
    for reference, current in zip(reference_shares, current_shares):
        reference, current = max(reference, PSI_FLOOR), max(current, PSI_FLOOR)
        total += (current - reference) * math.log(current / reference)
    return round(total, 4)


def quantile_edges(values: Sequence[float], bins: int = DECILE_BINS) -> List[float]:
    """Inner bin edges at the reference quantiles, so each reference bin holds a similar share.

    Args:
        values: Reference values.
        bins: Number of bins wanted.

    Returns:
        list: Sorted, distinct inner edges (at most bins - 1); fewer when values repeat, as
        bedroom counts do.
    """
    quantiles = np.quantile(np.asarray(values, dtype=float), [index / bins for index in range(1, bins)])
    return sorted({round(float(edge), 6) for edge in quantiles})


def bin_counts(values: Iterable[float], inner_edges: Sequence[float]) -> List[int]:
    """Count values per bin; bin i holds edges[i-1] < value <= edges[i], with open outer bins."""
    counts = [0] * (len(inner_edges) + 1)
    for value in values:
        counts[int(np.searchsorted(inner_edges, value, side="left"))] += 1
    return counts


def shares(counts: Sequence[int]) -> List[float]:
    """Turn counts into shares that sum to 1 (all zeros stay zeros)."""
    total = sum(counts)
    return [count / total if total else 0.0 for count in counts]


def bin_labels(inner_edges: Sequence[float], digits: int = 1) -> List[str]:
    """Readable labels for the bins of bin_counts: "≤ a", "a – b", …, "> z"."""
    if not inner_edges:
        return ["tất cả"]
    rounded = [f"{edge:,.{digits}f}" for edge in inner_edges]
    labels = [f"≤ {rounded[0]}"]
    labels += [f"{low} – {high}" for low, high in zip(rounded, rounded[1:])]
    labels.append(f"> {rounded[-1]}")
    return labels


def numeric_drift(reference_values: Sequence[float], current_values: Sequence[float], digits: int = 1) -> Dict:
    """Drift of one numeric feature, in bins at the reference deciles.

    Returns:
        dict: psi, status and bins ([{label, reference, current}] with shares).
    """
    edges = quantile_edges(reference_values)
    reference = shares(bin_counts(reference_values, edges))
    current = shares(bin_counts(current_values, edges))
    psi = population_stability_index(reference, current)
    return {
        "psi": psi,
        "status": drift_status(psi),
        "bins": [
            {"label": label, "reference": round(reference_share, 4), "current": round(current_share, 4)}
            for label, reference_share, current_share in zip(bin_labels(edges, digits), reference, current)
        ],
    }


def categorical_drift(reference_counts: Dict[str, int], current_counts: Dict[str, int], top: int = 10) -> Dict:
    """Drift of one categorical feature over the reference's most common categories.

    Categories outside the reference's top `top` share one "other" bin (label "Khác"), so a
    category the training data never saw shows up as current share without reference share.

    Returns:
        dict: psi, status and bins ([{label, reference, current}] with shares).
    """
    leading = [name for name, _ in sorted(reference_counts.items(), key=lambda item: -item[1])[:top]]
    has_other = bool((set(reference_counts) | set(current_counts)) - set(leading))
    labels = leading + (["Khác"] if has_other else [])

    def grouped(counts: Dict[str, int]) -> List[int]:
        values = [counts.get(name, 0) for name in leading]
        if len(labels) > len(leading):
            values.append(sum(count for name, count in counts.items() if name not in leading))
        return values

    reference = shares(grouped(reference_counts))
    current = shares(grouped(current_counts))
    psi = population_stability_index(reference, current)
    return {
        "psi": psi,
        "status": drift_status(psi),
        "bins": [
            {"label": label, "reference": round(reference_share, 4), "current": round(current_share, 4)}
            for label, reference_share, current_share in zip(labels, reference, current)
        ],
    }


def fixed_histogram(values: Iterable[float], inner_edges: Sequence[float], digits: int = 0) -> List[Dict]:
    """Histogram over fixed, domain-chosen edges.

    Returns:
        list: [{label, count}] for the bins of bin_counts.
    """
    counts = bin_counts(values, inner_edges)
    return [{"label": label, "count": count} for label, count in zip(bin_labels(inner_edges, digits), counts)]


def box_statistics(values: Sequence[float]) -> Optional[Dict]:
    """Tukey box plot numbers: quartiles, whiskers at 1.5 × IQR, and the outlier count.

    Returns:
        dict | None: count, low, q1, median, q3, high, outliers; None for an empty list.
    """
    if not values:
        return None
    data = np.asarray(values, dtype=float)
    q1, median, q3 = (float(value) for value in np.quantile(data, [0.25, 0.5, 0.75]))
    fence_low, fence_high = q1 - 1.5 * (q3 - q1), q3 + 1.5 * (q3 - q1)
    inside = data[(data >= fence_low) & (data <= fence_high)]
    return {
        "count": int(data.size),
        "low": round(float(inside.min()), 2),
        "q1": round(q1, 2),
        "median": round(median, 2),
        "q3": round(q3, 2),
        "high": round(float(inside.max()), 2),
        "outliers": int(data.size - inside.size),
    }


def modified_z_scores(values: Sequence[float]) -> List[float]:
    """Robust z-scores: 0.6745 × (x - median) / MAD (median absolute deviation).

    Unlike mean and standard deviation, median and MAD are not pulled by the outliers being
    looked for. Returns zeros when MAD is 0 (most values equal).
    """
    data = np.asarray(values, dtype=float)
    median = float(np.median(data))
    deviation = float(np.median(np.abs(data - median)))
    if deviation == 0:
        return [0.0] * data.size
    return [float(value) for value in 0.6745 * (data - median) / deviation]

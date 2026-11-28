"""Pure-Python retrieval evaluation metrics and objective constraint checkers.

Implements standard Information Retrieval metrics (Precision@K, nDCG@K, MRR@K,
pool Recall@K), latency percentiles, and attribute-level constraint checks for
Ho Chi Minh City real-estate search results. Requires only the Python standard
library so the suite runs in any environment without extra packages.
"""

from __future__ import annotations

import math
import re
import unicodedata
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

PROPERTY_TYPE_GROUPS: Dict[str, Tuple[str, ...]] = {
    "căn hộ": ("căn hộ", "căn hộ chung cư"),
    "nhà đất": (
        "nhà đất",
        "nhà riêng",
        "nhà mặt phố",
        "nhà biệt thự, liền kề",
        "shophouse, nhà phố thương mại",
    ),
}

ADMIN_PREFIX_PATTERN = re.compile(r"^(?:phuong|xa|quan|huyen|thi xa|thanh pho|tp)\s+")


def normalize_text(value: Optional[str]) -> str:
    """Fold Vietnamese accents, lowercase, and collapse whitespace.

    Args:
        value: Raw string or None.

    Returns:
        Accent-stripped, lowercased string with single spaces.
    """
    if not value:
        return ""
    normalized = unicodedata.normalize("NFKD", str(value))
    ascii_text = "".join(character for character in normalized if not unicodedata.combining(character))
    ascii_text = ascii_text.lower().replace("đ", "d")
    return re.sub(r"\s+", " ", ascii_text).strip()


def strip_admin_prefix(place_name: str) -> str:
    """Remove leading Vietnamese administrative prefixes such as 'Phường' or 'Quận'.

    Args:
        place_name: Place name in Vietnamese.

    Returns:
        Normalized core place name without the administrative prefix.
    """
    return ADMIN_PREFIX_PATTERN.sub("", normalize_text(place_name)).strip()


def to_float(value: Any) -> Optional[float]:
    """Parse a numeric listing attribute safely.

    Args:
        value: Raw attribute value (float, int, numeric string, or None).

    Returns:
        Parsed finite float, or None if missing or non-numeric.
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def precision_at_k(relevances: Sequence[int], cutoff: int, min_relevant_grade: int = 1) -> float:
    """Compute Precision@K from graded relevance labels in ranked order.

    Args:
        relevances: Graded relevance labels (e.g. 0, 1, 2) ordered by predicted rank.
        cutoff: Cutoff K (must be positive).
        min_relevant_grade: Minimum grade counted as relevant (default 1).

    Returns:
        Fraction of the top-K positions whose grade is at least ``min_relevant_grade``.
        Returns 0.0 when ``cutoff <= 0`` or ``relevances`` is empty.
    """
    if cutoff <= 0 or not relevances:
        return 0.0
    top_slice = relevances[:cutoff]
    hits = sum(1 for grade in top_slice if grade >= min_relevant_grade)
    return hits / cutoff


def dcg_at_k(relevances: Sequence[int], cutoff: int) -> float:
    """Compute Discounted Cumulative Gain (DCG@K) with exponential gain (2^rel - 1).

    Args:
        relevances: Graded relevance labels ordered by predicted rank.
        cutoff: Cutoff K.

    Returns:
        DCG@K score.
    """
    if cutoff <= 0:
        return 0.0
    total = 0.0
    for rank_index, grade in enumerate(relevances[:cutoff], start=1):
        if grade > 0:
            total += (2**grade - 1) / math.log2(rank_index + 1)
    return total


def ndcg_at_k(
    relevances: Sequence[int],
    cutoff: int,
    ideal_relevances: Optional[Sequence[int]] = None,
) -> float:
    """Compute Normalized Discounted Cumulative Gain (nDCG@K).

    Args:
        relevances: Graded relevance labels ordered by predicted rank.
        cutoff: Cutoff K.
        ideal_relevances: Optional pool of all known relevance grades for this query.
            When omitted, ``relevances`` itself is sorted descending to form the ideal list.

    Returns:
        nDCG@K in [0.0, 1.0]. Returns 0.0 if the ideal DCG is 0.
    """
    if cutoff <= 0:
        return 0.0
    ideal_source = relevances if ideal_relevances is None else ideal_relevances
    ideal_sorted = sorted(ideal_source, reverse=True)
    ideal_dcg = dcg_at_k(ideal_sorted, cutoff)
    if ideal_dcg <= 0.0:
        return 0.0
    return dcg_at_k(relevances, cutoff) / ideal_dcg


def mrr_at_k(relevances: Sequence[int], cutoff: int, min_relevant_grade: int = 1) -> float:
    """Compute Reciprocal Rank at cutoff K for a single query.

    Args:
        relevances: Graded relevance labels ordered by predicted rank.
        cutoff: Cutoff K.
        min_relevant_grade: Minimum grade counted as relevant (default 1).

    Returns:
        1 / rank of the first relevant item within top-K, or 0.0 if none.
    """
    if cutoff <= 0:
        return 0.0
    for rank_index, grade in enumerate(relevances[:cutoff], start=1):
        if grade >= min_relevant_grade:
            return 1.0 / rank_index
    return 0.0


def pool_recall_at_k(
    retrieved_ids: Sequence[int],
    relevant_pool_ids: Iterable[int],
    cutoff: int,
) -> float:
    """Compute Recall@K relative to the set of relevant listings found in the judgment pool.

    Args:
        retrieved_ids: Listing IDs returned by the system in ranked order.
        relevant_pool_ids: All listing IDs marked relevant in the pooled judgments.
        cutoff: Cutoff K.

    Returns:
        Fraction of ``relevant_pool_ids`` retrieved within top-K, or 0.0 if pool is empty.
    """
    relevant_set = {int(listing_id) for listing_id in relevant_pool_ids}
    if cutoff <= 0 or not relevant_set:
        return 0.0
    top_ids = {int(listing_id) for listing_id in retrieved_ids[:cutoff]}
    return len(top_ids & relevant_set) / len(relevant_set)


def percentile(values: Sequence[float], percent: float) -> float:
    """Compute a linear-interpolated percentile over a non-empty numeric sequence.

    Args:
        values: Numeric observations (e.g. latencies in milliseconds).
        percent: Target percentile in [0, 100].

    Returns:
        Interpolated percentile value, or 0.0 for an empty sequence.
    """
    if not values:
        return 0.0
    ordered = sorted(float(value) for value in values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * (percent / 100.0)
    lower_index = math.floor(position)
    upper_index = math.ceil(position)
    if lower_index == upper_index:
        return ordered[lower_index]
    weight = position - lower_index
    return ordered[lower_index] * (1.0 - weight) + ordered[upper_index] * weight


def expand_expected_property_types(expected_types: Sequence[str]) -> set[str]:
    """Expand property-type group names ('Căn hộ', 'Nhà đất') into normalized listing types.

    Args:
        expected_types: Group names or exact Vietnamese property types.

    Returns:
        Set of normalized acceptable property types.
    """
    expanded: set[str] = set()
    for raw_type in expected_types:
        cleaned = str(raw_type).strip().lower()
        members = PROPERTY_TYPE_GROUPS.get(cleaned, (cleaned,))
        for member in members:
            expanded.add(normalize_text(member))
    return expanded


def check_listing_constraints(
    listing: Dict[str, Any],
    constraints: Dict[str, Any],
) -> Dict[str, bool]:
    """Evaluate objective constraints from a query spec against one returned listing.

    Supported constraint keys:
        - ``min_price``, ``max_price`` (billion VND, compared with ``price_range``)
        - ``min_area``, ``max_area`` (square meters, compared with ``area``)
        - ``min_bedrooms`` (compared with ``bedrooms``)
        - ``min_bathrooms`` (compared with ``bathrooms``)
        - ``property_types`` (list of group names or exact types, compared with ``property_type``)
        - ``locations`` (list of ward/district/street substrings matched against
          ``district``, ``former_admin_area``, ``address``, and ``old_address``)
        - ``required_features`` (list of boolean column names on the listing, e.g. ``pool``, ``near_school``)

    Args:
        listing: One item dict from ``POST /v1/search`` response ``data.results``.
        constraints: Expected constraint dictionary from ``queries.json``.

    Returns:
        Mapping from each active constraint group name (``price``, ``area``, ``bedrooms``,
        ``bathrooms``, ``property_type``, ``location``, ``features``) to True/False.
    """
    checks: Dict[str, bool] = {}

    if "min_price" in constraints or "max_price" in constraints:
        price = to_float(listing.get("price_range"))
        if price is None:
            checks["price"] = False
        else:
            min_price = to_float(constraints.get("min_price"))
            max_price = to_float(constraints.get("max_price"))
            is_above_min = min_price is None or price >= min_price
            is_below_max = max_price is None or price <= max_price
            checks["price"] = is_above_min and is_below_max

    if "min_area" in constraints or "max_area" in constraints:
        area = to_float(listing.get("area"))
        if area is None:
            checks["area"] = False
        else:
            min_area = to_float(constraints.get("min_area"))
            max_area = to_float(constraints.get("max_area"))
            is_above_min = min_area is None or area >= min_area
            is_below_max = max_area is None or area <= max_area
            checks["area"] = is_above_min and is_below_max

    if "min_bedrooms" in constraints:
        bedrooms = to_float(listing.get("bedrooms"))
        min_bedrooms = to_float(constraints.get("min_bedrooms"))
        checks["bedrooms"] = bedrooms is not None and min_bedrooms is not None and bedrooms >= min_bedrooms

    if "min_bathrooms" in constraints:
        bathrooms = to_float(listing.get("bathrooms"))
        min_bathrooms = to_float(constraints.get("min_bathrooms"))
        checks["bathrooms"] = bathrooms is not None and min_bathrooms is not None and bathrooms >= min_bathrooms

    if constraints.get("property_types"):
        allowed = expand_expected_property_types(constraints["property_types"])
        actual = normalize_text(listing.get("property_type"))
        checks["property_type"] = bool(actual) and actual in allowed

    if constraints.get("locations"):
        location_haystack = " ".join(
            normalize_text(listing.get(field))
            for field in ("district", "former_admin_area", "address", "old_address")
            if listing.get(field)
        )
        expected_locations = [strip_admin_prefix(place) for place in constraints["locations"] if place]
        checks["location"] = any(place and place in location_haystack for place in expected_locations)

    if constraints.get("required_features"):
        required_features = [str(flag) for flag in constraints["required_features"]]
        checks["features"] = all(bool(listing.get(flag)) for flag in required_features)

    return checks


def constraint_match_at_k(
    listings: Sequence[Dict[str, Any]],
    constraints: Dict[str, Any],
    cutoff: int,
) -> Tuple[Optional[float], Dict[str, float]]:
    """Compute exact and per-constraint satisfaction rates over the top-K listings.

    Args:
        listings: Ranked listings returned by ``POST /v1/search``.
        constraints: Expected constraint dictionary for the query.
        cutoff: Cutoff K.

    Returns:
        Tuple of ``(exact_match_rate, per_constraint_rates)``. ``exact_match_rate`` is
        None when ``constraints`` has no objective checks (pure semantic queries).
    """
    if not constraints:
        return None, {}
    top_listings = listings[:cutoff]
    if not top_listings:
        sample_keys = check_listing_constraints({}, constraints).keys()
        return 0.0, {key: 0.0 for key in sample_keys}

    per_listing_checks = [check_listing_constraints(listing, constraints) for listing in top_listings]
    active_keys = list(per_listing_checks[0].keys())
    if not active_keys:
        return None, {}

    exact_hits = sum(1 for item_checks in per_listing_checks if all(item_checks.values()))
    exact_rate = exact_hits / len(top_listings)
    per_constraint_rates = {
        key: sum(1 for item_checks in per_listing_checks if item_checks[key]) / len(top_listings)
        for key in active_keys
    }
    return exact_rate, per_constraint_rates

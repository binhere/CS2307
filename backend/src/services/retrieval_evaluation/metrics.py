"""Pure-Python objective constraint checks and latency percentiles for retrieval evaluation.

Evaluates listings returned by ``HybridSearchService.search`` against each query's
expected constraints (price, area, rooms, property type, location, feature flags)
without external dependencies or synthetic user interactions.
"""

from __future__ import annotations

import math
import re
import unicodedata
from typing import Any, Dict, Optional, Sequence, Tuple

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


def percentile(values: Sequence[float], percent: float) -> float:
    """Compute a linear-interpolated percentile over a numeric sequence.

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

    Args:
        listing: One listing dictionary from ``HybridSearchService.search`` results.
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
            checks["price"] = (min_price is None or price >= min_price) and (max_price is None or price <= max_price)

    if "min_area" in constraints or "max_area" in constraints:
        area = to_float(listing.get("area"))
        if area is None:
            checks["area"] = False
        else:
            min_area = to_float(constraints.get("min_area"))
            max_area = to_float(constraints.get("max_area"))
            checks["area"] = (min_area is None or area >= min_area) and (max_area is None or area <= max_area)

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
        listings: Ranked listings returned by ``HybridSearchService.search``.
        constraints: Expected constraint dictionary for the query.
        cutoff: Cutoff K.

    Returns:
        Tuple of ``(exact_match_rate, per_constraint_rates)``. ``exact_match_rate`` is
        None when ``constraints`` has no objective checks.
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

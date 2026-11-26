"""Turn listing rows into the price model's training data, as price_prediction/data_preparation does.

Input: rows of the properties table (dicts with listing_id, source, property_type, area, bedrooms,
latitude_longitude, district, legal_status and price_range). Steps, in order:

1. Normalize: property type -> "Căn hộ" / "Nhà đất", legal status -> five classes, numbers parsed.
2. Clean: drop rows without a price, an area, a property type or a ward (nothing can stand in for
   the target or for the main driver of price) and repeated listing ids.
3. Fill in: coordinates that are missing or outside the city become a point inside the listing's
   ward; missing bedrooms become the median of listings of the same group and area band.
4. Remove outliers with data_preparation step 3's residential limits per property type.

feature_frame then encodes rows as the model sees them (data_preparation step 4): area per
bedroom and one-hot property type, legal status and ward. The served model and training share
this module, so a listing is encoded the same way when it is learnt from and when it is priced.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from src.services.listing_submission.geography import address_reference

APARTMENT_TYPES = {"Căn hộ", "Căn hộ chung cư"}
PROPERTY_GROUPS = ("Căn hộ", "Nhà đất")
# data_preparation step 2: a coarse box around Ho Chi Minh City since 1 July 2025.
LATITUDE_RANGE, LONGITUDE_RANGE = (10.3, 11.6), (106.3, 107.6)


@dataclass(frozen=True)
class OutlierRule:
    """data_preparation step 3's residential limits for one property group (prices in billion VND)."""

    minimum_area: float
    maximum_area: float
    maximum_bedrooms: int
    minimum_price: float
    maximum_price: float
    minimum_price_per_m2: float  # million VND per m²
    maximum_price_per_m2: float


OUTLIER_RULES = {
    "Căn hộ": OutlierRule(25, 150, 4, 1.0, 20.0, 20, 180),
    "Nhà đất": OutlierRule(20, 350, 8, 1.5, 40.0, 30, 350),
}
# Bedroom medians are learnt per area band (m²): a 45 m² flat and a 120 m² flat differ.
AREA_BAND_EDGES = [40, 60, 80, 100, 150, 200, 300]
NUMERIC_FEATURES = ["area", "bedrooms", "area_per_bedroom", "latitude", "longitude"]
CATEGORICAL_FEATURES = ["property_type", "legal_status", "district"]
_COORDINATES = re.compile(r"(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)")


def _text(value: Any) -> str:
    """A stripped string, or "" for None, NaN and other non-text values."""
    return value.strip() if isinstance(value, str) else ""


def property_group(value: Optional[str]) -> Optional[str]:
    """The model's property group of a catalog or posted label: "Căn hộ", "Nhà đất", or None when blank."""
    label = _text(value)
    if not label:
        return None
    return "Căn hộ" if label in APARTMENT_TYPES else "Nhà đất"


def normalize_legal_status(value: Optional[str]) -> str:
    """One of "Sổ hồng riêng", "Sổ chung", "Hợp đồng mua bán", "Đang chờ sổ" or "Không rõ" for free text."""
    text = _text(value).lower()
    if re.search(r"chờ|làm sổ|nộp hồ sơ", text):
        return "Đang chờ sổ"
    if re.search(r"chung|sdc", text):
        return "Sổ chung"
    if re.search(r"hợp đồng|hđmb|hdmb", text):
        return "Hợp đồng mua bán"
    if re.search(r"sổ|hồng|đỏ|shr", text):
        return "Sổ hồng riêng"
    return "Không rõ"


def parse_coordinates(value: Optional[str]) -> Optional[Tuple[float, float]]:
    """(latitude, longitude) from a listing's "latitude, longitude" text, or None when it holds no pair."""
    match = _COORDINATES.search(_text(value))
    return (float(match.group(1)), float(match.group(2))) if match else None


def is_inside_city(latitude: float, longitude: float) -> bool:
    """Whether a point lies in the Ho Chi Minh City box the model is trained on."""
    return LATITUDE_RANGE[0] <= latitude <= LATITUDE_RANGE[1] and LONGITUDE_RANGE[0] <= longitude <= LONGITUDE_RANGE[1]


def ward_point(ward_name: Optional[str]) -> Optional[Tuple[float, float]]:
    """A point inside an official ward, standing in for a listing's missing coordinates; None for another name."""
    reference = address_reference()
    ward = reference.ward_by_name.get(_text(ward_name))
    return reference.representative_point(ward) if ward else None


def area_band(area: float) -> int:
    """Index of the area band an area (m²) falls in, 0 to len(AREA_BAND_EDGES)."""
    return int(np.searchsorted(AREA_BAND_EDGES, area, side="right"))


def learn_imputation(bedrooms: Iterable[Tuple[str, float, float]]) -> Dict[str, Any]:
    """Median bedrooms per property group and area band, to stand in for missing ones.

    Args:
        bedrooms: (group, area, bedrooms) of listings that state their bedrooms.

    Returns:
        dict: {"area_band_edges", "bedrooms": {group: [median per band or None]}, "fallback": {group:
        median}}; kept with the model so pricing fills in a listing the way training did.
    """
    by_band: Dict[Tuple[str, int], List[float]] = {}
    by_group: Dict[str, List[float]] = {}
    for group, area, count in bedrooms:
        # Implausible counts (a typo such as 30) would drag no median, but keep them out anyway.
        if 1 <= count <= 20:
            by_band.setdefault((group, area_band(area)), []).append(count)
            by_group.setdefault(group, []).append(count)
    band_count = len(AREA_BAND_EDGES) + 1
    return {
        "area_band_edges": list(AREA_BAND_EDGES),
        "bedrooms": {group: [int(round(float(np.median(by_band[(group, band)])))) if (group, band) in by_band else None
                             for band in range(band_count)] for group in PROPERTY_GROUPS},
        "fallback": {group: int(round(float(np.median(by_group[group])))) if group in by_group else 2 for group in PROPERTY_GROUPS},
    }


def impute_bedrooms(group: str, area: float, imputation: Dict[str, Any]) -> int:
    """Stand-in bedrooms for a listing that does not state them.

    Args:
        group: "Căn hộ" or "Nhà đất".
        area: Its area in m².
        imputation: learn_imputation's result.

    Returns:
        int: The median of its group and area band, else of its group, at least 1.
    """
    band = int(np.searchsorted(imputation["area_band_edges"], area, side="right"))
    medians = imputation["bedrooms"].get(group) or []
    value = medians[band] if band < len(medians) and medians[band] is not None else imputation["fallback"].get(group, 2)
    return max(1, int(value))


def passes_outlier_rules(group: str, area: float, bedrooms: float, price: float) -> bool:
    """Whether a listing lies within data_preparation step 3's limits for its group."""
    rule = OUTLIER_RULES[group]
    price_per_m2 = price * 1000 / area
    return (rule.minimum_area <= area <= rule.maximum_area and 1 <= bedrooms <= rule.maximum_bedrooms
            and rule.minimum_price <= price <= rule.maximum_price
            and rule.minimum_price_per_m2 <= price_per_m2 <= rule.maximum_price_per_m2)


def _number(value: Any) -> Optional[float]:
    """A finite float, or None for blanks and text."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if np.isfinite(number) else None


@dataclass
class PreparedData:
    """Training rows after the four steps, and what each step did.

    Attributes:
        frame: One row per listing: listing_id, source, property_type (group), area, bedrooms,
            latitude, longitude, district, legal_status, price (billion VND) and the flags
            is_bedrooms_imputed and is_coordinates_imputed.
        step_rows: Rows loaded, after cleaning, after locating, after outlier removal.
        dropped: Rows dropped per reason: missing_price_or_area, missing_type_or_ward, duplicate,
            no_location, outlier.
        imputation: The bedroom medians used (learn_imputation's result).
    """

    frame: pd.DataFrame
    step_rows: List[int]
    dropped: Counter = field(default_factory=Counter)
    imputation: Dict[str, Any] = field(default_factory=dict)


def prepare_training_data(rows: Sequence[Dict[str, Any]], imputation: Optional[Dict[str, Any]] = None) -> PreparedData:
    """Run steps 1 to 4 over listing rows.

    Args:
        rows: properties rows (see the module docstring).
        imputation: Bedroom medians to use; learnt from these rows when None.

    Returns:
        PreparedData: the training rows and the count of each step.
    """
    dropped: Counter = Counter()
    seen: set = set()
    cleaned: List[Dict[str, Any]] = []
    for row in rows:
        group = property_group(row.get("property_type"))
        area, price = _number(row.get("area")), _number(row.get("price_range"))
        ward = _text(row.get("district"))
        if not area or area <= 0 or not price or price <= 0:
            dropped["missing_price_or_area"] += 1
            continue
        if group is None or not ward:
            dropped["missing_type_or_ward"] += 1
            continue
        listing_id = int(row["listing_id"])
        if listing_id in seen:
            dropped["duplicate"] += 1
            continue
        seen.add(listing_id)
        bedrooms = _number(row.get("bedrooms"))
        cleaned.append({
            "listing_id": listing_id, "source": _text(row.get("source")) or "catalog", "property_type": group,
            "area": area, "bedrooms": bedrooms if bedrooms and bedrooms >= 1 else None,
            "coordinates": parse_coordinates(row.get("latitude_longitude")), "district": ward,
            "legal_status": normalize_legal_status(row.get("legal_status")), "price": price,
        })

    if imputation is None:
        imputation = learn_imputation((item["property_type"], item["area"], item["bedrooms"])
                                      for item in cleaned if item["bedrooms"] is not None)
    located: List[Dict[str, Any]] = []
    for item in cleaned:
        coordinates = item.pop("coordinates")
        item["is_coordinates_imputed"] = not (coordinates and is_inside_city(*coordinates))
        if item["is_coordinates_imputed"]:
            coordinates = ward_point(item["district"])
            if coordinates is None:
                dropped["no_location"] += 1
                continue
        item["latitude"], item["longitude"] = coordinates
        item["is_bedrooms_imputed"] = item["bedrooms"] is None
        if item["is_bedrooms_imputed"]:
            item["bedrooms"] = impute_bedrooms(item["property_type"], item["area"], imputation)
        item["bedrooms"] = int(item["bedrooms"])
        located.append(item)

    kept = [item for item in located if passes_outlier_rules(item["property_type"], item["area"], item["bedrooms"], item["price"])]
    dropped["outlier"] += len(located) - len(kept)
    columns = ["listing_id", "source", "property_type", "area", "bedrooms", "latitude", "longitude", "district",
               "legal_status", "price", "is_bedrooms_imputed", "is_coordinates_imputed"]
    frame = pd.DataFrame(kept, columns=columns)
    return PreparedData(frame, [len(rows), len(cleaned), len(located), len(kept)], dropped, imputation)


def feature_names_for(frame: pd.DataFrame) -> List[str]:
    """The model's input columns for a training frame: numeric features, then one column per category value."""
    names = list(NUMERIC_FEATURES)
    for column in CATEGORICAL_FEATURES:
        names += [f"{column}_{value}" for value in sorted(frame[column].dropna().unique())]
    return names


def feature_frame(frame: pd.DataFrame, feature_names: Sequence[str]) -> pd.DataFrame:
    """Encode prepared rows as a model's input (data_preparation step 4).

    Args:
        frame: Rows with property_type, area, bedrooms, latitude, longitude, district and legal_status.
        feature_names: The model's columns, in order; a category value the model has no column for
            leaves every column of that category at 0.

    Returns:
        pd.DataFrame: One row per input row, exactly feature_names as columns.
    """
    features = pd.DataFrame(0.0, index=frame.index, columns=list(feature_names))
    features["area"] = frame["area"].astype(float)
    features["bedrooms"] = frame["bedrooms"].astype(float)
    features["area_per_bedroom"] = (frame["area"] / frame["bedrooms"].clip(lower=1)).round(2)
    features["latitude"] = frame["latitude"].astype(float)
    features["longitude"] = frame["longitude"].astype(float)
    known = set(feature_names)
    for column in CATEGORICAL_FEATURES:
        dummies = pd.get_dummies(frame[column], prefix=column, prefix_sep="_", dtype=float)
        for name in dummies.columns:
            if name in known:
                features[name] = dummies[name]
    return features


def log_price(frame: pd.DataFrame) -> pd.Series:
    """The training target: log1p of the price in billion VND (data_preparation step 4)."""
    return np.log1p(frame["price"].astype(float))

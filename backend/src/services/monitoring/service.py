"""Build the data dashboard (/du-lieu): catalog health, data quality, drift, model quality and
the price model's verdicts over the catalog.

Everything is computed from the catalog rows (a few thousand) plus training_report.json, which
price_prediction/train-models.py writes next to the model. The result changes only when the
seed reloads the catalog or a new model ships, so it is cached for CACHE_SECONDS.

Output: a JSON-ready dict with the sections kpis, quality, distributions, drift, model and
predictions; drift and model are None without a training report, predictions is None without
the model file.
"""

from __future__ import annotations

import json
import math
import time
from collections import Counter
from datetime import date, datetime, timezone
from functools import lru_cache
from pathlib import Path
from threading import Lock
from typing import Dict, List, Optional, Set

from loguru import logger

from src.search.graph_repository import graph_repository
from src.search.normalizer import PROPERTY_TYPE_GROUPS
from src.services.inference.price_prediction import (
    is_inside_city,
    normalize_legal_status,
    parse_coordinates,
    price_bounds,
    price_prediction_service,
    price_verdict,
)
from src.services.monitoring import repository
from src.services.monitoring.statistics import (
    MODIFIED_Z_THRESHOLD,
    box_statistics,
    categorical_drift,
    fixed_histogram,
    modified_z_scores,
    numeric_drift,
)
from src.settings.event import minio_client

REPORT_FILE = Path(__file__).resolve().parents[1] / "inference" / "artifacts" / "training_report.json"
CACHE_SECONDS = 600
# Domain-chosen histogram edges: prices in billion VND, areas in m², errors in percent.
PRICE_EDGES = [1, 2, 3, 5, 7, 10, 15, 20, 30, 50]
AREA_EDGES = [30, 50, 70, 100, 150, 200, 300, 500]
ERROR_EDGES = [-50, -40, -30, -20, -10, 0, 10, 20, 30, 40, 50]
TOP_WARDS = 15
SUSPICIOUS_LIMIT = 25
MAX_ROOMS = 20


@lru_cache(maxsize=1)
def load_training_report() -> Optional[Dict]:
    """The report of the model being served, or None when it is missing or unreadable."""
    try:
        return json.loads(REPORT_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        logger.warning("Training report unavailable: {}", type(error).__name__)
        return None


def property_group(property_type: Optional[str]) -> str:
    """The group ("Căn hộ" or "Nhà đất") a catalog type belongs to, or "Khác" for an unknown one."""
    for group, members in PROPERTY_TYPE_GROUPS.items():
        if property_type in members:
            return group
    return "Khác"


def _price_per_m2(row: Dict) -> Optional[float]:
    """Asking price per m² in million VND, or None without a positive price and area."""
    price, area = row.get("price_range"), row.get("area")
    if price and area and price > 0 and area > 0:
        return float(price) * 1000 / float(area)
    return None


def _is_blank(value) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _numeric_summary(values: List[float]) -> Dict:
    """Min, median and max of a list, rounded for display (None when empty)."""
    if not values:
        return {"min": None, "median": None, "max": None}
    ordered = sorted(values)
    return {"min": round(ordered[0], 2), "median": round(ordered[len(ordered) // 2], 2), "max": round(ordered[-1], 2)}


def kpi_section(rows: List[Dict], catalog_import: Optional[Dict], report: Optional[Dict], train_ids: Set[int]) -> Dict:
    """Headline numbers: catalog size, last load, coverage of vectors, graph and photos, model, overlap."""
    in_training = sum(1 for row in rows if row["listing_id"] in train_ids)
    loaded_at = catalog_import.get("loaded_at") if catalog_import else None
    overall = report["segments"][0] if report else None
    return {
        "listings": len(rows),
        "seed_version": catalog_import.get("seed_version") if catalog_import else None,
        "loaded_at": loaded_at.isoformat() if hasattr(loaded_at, "isoformat") else loaded_at,
        "embedded": sum(1 for row in rows if row["has_embedding"]),
        "graph_listings": graph_repository.count_listing_nodes(),
        "listings_with_photos": sum(1 for row in rows if row["image_count"] > 0),
        "photo_keys": sum(row["image_count"] for row in rows),
        "stored_photos": minio_client.count_objects("images/"),
        "model": {"name": report["model"]["name"], "r2": overall["r2"], "mape": overall["mape"]} if report else None,
        "in_training": in_training,
    }


def quality_section(rows: List[Dict], today: date) -> Dict:
    """Missing and invalid values per field, duplicate ids, suspicious prices and freshness."""
    coordinates = [parse_coordinates(row.get("latitude_longitude")) for row in rows]
    numeric_rules = {
        "price_range": lambda value: value <= 0,
        "area": lambda value: value <= 0,
        "bedrooms": lambda value: value < 0 or value > MAX_ROOMS,
        "bathrooms": lambda value: value < 0 or value > MAX_ROOMS,
    }
    fields = []
    for field, is_invalid in numeric_rules.items():
        values = [float(row[field]) for row in rows if row.get(field) is not None]
        fields.append({"field": field, "missing": len(rows) - len(values),
                       "invalid": sum(1 for value in values if is_invalid(value)),
                       "distinct": len(set(values)), **_numeric_summary(values)})
    fields.append({"field": "coordinates", "missing": sum(1 for pair in coordinates if pair is None),
                   "invalid": sum(1 for pair in coordinates if pair and not is_inside_city(*pair)),
                   "distinct": len({pair for pair in coordinates if pair})})
    for field in ("district", "legal_status", "furnishing", "house_direction"):
        values = [row[field] for row in rows if not _is_blank(row.get(field))]
        fields.append({"field": field, "missing": len(rows) - len(values), "invalid": 0, "distinct": len(set(values))})
    dates = [row["posted_date"] for row in rows if row.get("posted_date")]
    fields.append({"field": "posted_date", "missing": len(rows) - len(dates),
                   "invalid": sum(1 for value in dates if value > today), "distinct": len(set(dates))})
    fields.append({"field": "images", "missing": sum(1 for row in rows if row["image_count"] == 0), "invalid": 0})
    fields.append({"field": "description", "missing": sum(1 for row in rows if row["description_length"] == 0), "invalid": 0})
    fields.append({"field": "embedding", "missing": sum(1 for row in rows if not row["has_embedding"]), "invalid": 0})

    listing_counts = Counter(row["listing_id"] for row in rows)
    months = Counter(value.strftime("%Y-%m") for value in dates)
    return {
        "rows": len(rows),
        "fields": fields,
        "duplicate_listings": sum(count - 1 for count in listing_counts.values() if count > 1),
        "suspicious_prices": suspicious_prices(rows),
        "posted_by_month": [{"month": month, "count": months[month]} for month in sorted(months)],
    }


def suspicious_prices(rows: List[Dict]) -> Dict:
    """Listings whose price per m² is far from others of the same type (modified z-score > 3.5).

    The score is taken on log(price per m²), per catalog property type, so a 30 % gap means the
    same for a cheap flat as for a villa. A typical cause is the crawler taking the first amount
    in a title, such as a monthly rent.
    """
    by_type: Dict[str, List[Dict]] = {}
    for row in rows:
        per_m2 = _price_per_m2(row)
        if per_m2:
            by_type.setdefault(row.get("property_type") or "Khác", []).append({**row, "per_m2": per_m2})
    flagged = []
    for listings in by_type.values():
        scores = modified_z_scores([math.log(item["per_m2"]) for item in listings])
        for item, score in zip(listings, scores):
            if abs(score) > MODIFIED_Z_THRESHOLD:
                flagged.append({
                    "id": item["id"], "listing_id": item["listing_id"], "title": item.get("title"),
                    "property_type": item.get("property_type"), "price": item["price_range"], "area": item["area"],
                    "price_per_m2": round(item["per_m2"], 1), "score": round(score, 2),
                })
    flagged.sort(key=lambda item: -abs(item["score"]))
    return {"count": len(flagged), "threshold": MODIFIED_Z_THRESHOLD, "listings": flagged[:SUSPICIOUS_LIMIT]}


def distribution_section(rows: List[Dict]) -> Dict:
    """Catalog composition: types, wards, price and area histograms, price per m² per type."""
    types = Counter(row.get("property_type") or "Khác" for row in rows)
    wards = Counter(row["district"] for row in rows if not _is_blank(row.get("district")))
    leading_wards = wards.most_common(TOP_WARDS)
    per_m2_by_type: Dict[str, List[float]] = {}
    for row in rows:
        per_m2 = _price_per_m2(row)
        if per_m2:
            per_m2_by_type.setdefault(row.get("property_type") or "Khác", []).append(per_m2)
    return {
        "property_types": [{"label": label, "count": count} for label, count in types.most_common()],
        "wards": [{"label": label, "count": count} for label, count in leading_wards],
        "other_wards": sum(wards.values()) - sum(count for _, count in leading_wards),
        "ward_count": len(wards),
        "price": fixed_histogram((row["price_range"] for row in rows if row.get("price_range")), PRICE_EDGES),
        "area": fixed_histogram((row["area"] for row in rows if row.get("area")), AREA_EDGES),
        "price_per_m2": [{"label": label, **box_statistics(values)} for label, values in
                         sorted(per_m2_by_type.items(), key=lambda item: -len(item[1]))],
    }


def drift_section(rows: List[Dict], report: Dict) -> Dict:
    """Training data (reference) against the catalog being served (current), per feature."""
    reference = report["reference"]
    current_numeric = {
        "price": [float(row["price_range"]) for row in rows if row.get("price_range")],
        "area": [float(row["area"]) for row in rows if row.get("area")],
        "bedrooms": [float(row["bedrooms"]) for row in rows if row.get("bedrooms") is not None],
        "price_per_m2": [value for value in map(_price_per_m2, rows) if value],
    }
    digits = {"price": 1, "area": 0, "bedrooms": 0, "price_per_m2": 0}
    features = [
        {"feature": feature, "kind": "numeric", **numeric_drift(reference["numeric"][feature], values, digits[feature])}
        for feature, values in current_numeric.items()
    ]
    current_categorical = {
        "property_group": Counter(property_group(row.get("property_type")) for row in rows),
        "legal_status": Counter(normalize_legal_status(row.get("legal_status")) for row in rows),
        "ward": Counter(row["district"] for row in rows if not _is_blank(row.get("district"))),
    }
    features += [
        {"feature": feature, "kind": "categorical",
         **categorical_drift(reference["categorical"][feature], dict(counts))}
        for feature, counts in current_categorical.items()
    ]
    return {"reference_rows": reference["rows"], "current_rows": len(rows), "features": features}


def model_section(report: Dict) -> Dict:
    """Quality of the served model on its test set: metrics, interval coverage, errors, scatter."""
    predictions = report["test_predictions"]
    coverage = {}
    errors: Dict[str, List[float]] = {}
    for _, segment, actual, predicted in predictions:
        lower, upper = price_bounds(predicted, segment)
        hit, total = coverage.get(segment, (0, 0))
        coverage[segment] = (hit + (lower <= actual <= upper), total + 1)
        errors.setdefault(segment, []).append((predicted - actual) / actual * 100)
    segments = []
    for row in report["segments"]:
        if row["segment"] == "all":
            hits, total = sum(value[0] for value in coverage.values()), sum(value[1] for value in coverage.values())
        else:
            hits, total = coverage.get(row["segment"], (0, 0))
        segments.append({**row, "interval_coverage": round(hits / total * 100, 1) if total else None})
    histograms = {segment: fixed_histogram(values, ERROR_EDGES) for segment, values in errors.items()}
    labels = [item["label"] for item in next(iter(histograms.values()), [])]
    return {
        "name": report["model"]["name"],
        "trained_report_at": report["generated_at"],
        "composition": report["composition"],
        "benchmark": report["benchmark"],
        "segments": segments,
        "error_histogram": [
            {"label": label, **{segment: histogram[index]["count"] for segment, histogram in histograms.items()}}
            for index, label in enumerate(labels)
        ],
        "scatter": [[actual, predicted, segment] for _, segment, actual, predicted in predictions],
    }


def prediction_section(rows: List[Dict], train_ids: Set[int]) -> Optional[Dict]:
    """The model's verdict on every catalog listing, split by whether it was a training row.

    A listing the model was trained on tends to be estimated close to its own asking price, so
    "within" grows there: comparing the two groups shows the effect of that overlap.
    """
    try:
        assessments = [price_prediction_service.assess_listing(row) for row in rows]
        scored = [(row, assessment) for row, assessment in zip(rows, assessments) if assessment.inputs]
        estimates = price_prediction_service.predict_many([assessment.inputs for _, assessment in scored])
    except FileNotFoundError:
        logger.warning("Price model missing; the dashboard shows no prediction monitoring")
        return None
    groups = {"in_training": Counter(), "not_in_training": Counter()}
    differences: Dict[str, List[float]] = {"in_training": [], "not_in_training": []}
    refused = Counter()
    for row, assessment in zip(rows, assessments):
        group = "in_training" if row["listing_id"] in train_ids else "not_in_training"
        groups[group]["total"] += 1
        if not assessment.inputs:
            groups[group]["refused"] += 1
            refused[assessment.reason_code] += 1
    for (row, assessment), estimate in zip(scored, estimates):
        group = "in_training" if row["listing_id"] in train_ids else "not_in_training"
        lower, upper = price_bounds(estimate, assessment.inputs["property_type"])
        groups[group][price_verdict(assessment.asking_price, lower, upper)] += 1
        differences[group].append((assessment.asking_price / estimate - 1) * 100)
    histograms = {group: fixed_histogram(values, ERROR_EDGES) for group, values in differences.items()}
    return {
        "scored": len(scored),
        "verdicts": [{"group": group, **{key: counts.get(key, 0) for key in ("total", "below", "within", "above", "refused")}}
                     for group, counts in groups.items()],
        "refused_reasons": [{"code": code, "count": count} for code, count in refused.most_common()],
        "difference_histogram": [
            {"label": item["label"], "in_training": item["count"],
             "not_in_training": histograms["not_in_training"][index]["count"]}
            for index, item in enumerate(histograms["in_training"])
        ],
    }


def build_snapshot(today: Optional[date] = None) -> Dict:
    """Compute every dashboard section from the database and the training report.

    Raises:
        sqlalchemy.exc.SQLAlchemyError: the database is unavailable.
    """
    rows = repository.fetch_catalog_rows()
    catalog_import = repository.fetch_catalog_import()
    report = load_training_report()
    train_ids = set(report["train_listing_ids"]) if report else set()
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "kpis": kpi_section(rows, catalog_import, report, train_ids),
        "quality": quality_section(rows, today or date.today()),
        "distributions": distribution_section(rows),
        "drift": drift_section(rows, report) if report else None,
        "model": model_section(report) if report else None,
        "predictions": prediction_section(rows, train_ids),
    }


class MonitoringService:
    """Serves the dashboard snapshot, rebuilt at most once per CACHE_SECONDS."""

    def __init__(self, cache_seconds: int = CACHE_SECONDS) -> None:
        self.cache_seconds = cache_seconds
        self._snapshot: Optional[Dict] = None
        self._built_at = 0.0
        # One build at a time: concurrent first visitors wait for it instead of each running it.
        self._lock = Lock()

    def snapshot(self) -> Dict:
        """The cached snapshot, rebuilt when older than cache_seconds.

        Raises:
            sqlalchemy.exc.SQLAlchemyError: the database is unavailable while rebuilding.
        """
        with self._lock:
            if self._snapshot is None or time.monotonic() - self._built_at > self.cache_seconds:
                self._snapshot = build_snapshot()
                self._built_at = time.monotonic()
            return self._snapshot


monitoring_service = MonitoringService()

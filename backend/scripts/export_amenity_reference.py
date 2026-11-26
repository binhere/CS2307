#!/usr/bin/env python3
"""Build the amenity reference the server uses to place posted listings near schools, markets, etc.

Listings now come only from users (form and zip uploads), so nothing computes their distances to
amenities offline any more. The server does it when a listing is saved, from this file:

- the amenities: data/gold/sql/amenities.csv (OpenStreetMap points of interest, 9 categories);
- per category, how much farther and slower a drive is than the straight line: medians of
  data/gold/sql/listing_amenities.csv, where the offline pipeline routed every pair with OSRM.
  The server has no road network, so it estimates driving distance as straight line x road_factor
  and duration as that distance at speed_kmh.

Usage:
    python backend/scripts/export_amenity_reference.py

Writes:
    backend/src/services/listing_enrichment/reference/amenities.json
Standard library only; re-run after the points of interest are refreshed.
"""

from __future__ import annotations

import csv
import json
import statistics
from collections import defaultdict
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parents[2]
AMENITIES_FILE = REPOSITORY / "data" / "gold" / "sql" / "amenities.csv"
ROUTES_FILE = REPOSITORY / "data" / "gold" / "sql" / "listing_amenities.csv"
OUTPUT_FILE = REPOSITORY / "backend" / "src" / "services" / "listing_enrichment" / "reference" / "amenities.json"
# Driving distance within which an amenity counts as "near" for graph scoring (near_school, ...).
NEAR_THRESHOLD_KM = {
    "metro": 1.5, "bus_stop": 0.5, "school": 2.0, "college_university": 3.0, "hospital": 3.0,
    "mall": 3.0, "supermarket": 1.5, "market": 1.5, "park": 1.5,
}
# Very short pairs give unstable ratios (a 20 m straight line can be a 400 m drive).
MINIMUM_STRAIGHT_KM = 0.05


def read_csv(path: Path) -> list:
    """Rows of a UTF-8 CSV (with or without a byte-order mark) as dicts."""
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def calibration(routes: list) -> dict:
    """Median road factor (driving / straight-line distance) and driving speed per category.

    Args:
        routes: Rows of listing_amenities.csv.

    Returns:
        dict: category -> {"road_factor", "speed_kmh"}, rounded to 2 decimals.
    """
    factors, speeds = defaultdict(list), defaultdict(list)
    for row in routes:
        try:
            straight, driving, minutes = float(row["straight_line_km"]), float(row["driving_distance_km"]), float(row["driving_duration_min"])
        except (TypeError, ValueError):
            continue
        if straight >= MINIMUM_STRAIGHT_KM:
            factors[row["category"]].append(driving / straight)
        if minutes > 0:
            speeds[row["category"]].append(driving / (minutes / 60))
    return {category: {"road_factor": round(statistics.median(factors[category]), 2),
                       "speed_kmh": round(statistics.median(speeds[category]), 2)}
            for category in sorted(factors)}


def main() -> None:
    """Write the reference file and print a summary."""
    amenities = [
        [row["osm_id"], row["name"], row["category"], round(float(row["latitude"]), 7), round(float(row["longitude"]), 7),
         row.get("street") or ""]
        for row in read_csv(AMENITIES_FILE)
        if row.get("latitude") and row.get("longitude") and row.get("category") in NEAR_THRESHOLD_KM
    ]
    reference = {
        "source": "data/gold/sql/amenities.csv (OpenStreetMap), calibration from data/gold/sql/listing_amenities.csv (OSRM)",
        "fields": ["amenity_id", "name", "category", "latitude", "longitude", "street"],
        "calibration": calibration(read_csv(ROUTES_FILE)),
        "near_threshold_km": NEAR_THRESHOLD_KM,
        "amenities": amenities,
    }
    missing = set(NEAR_THRESHOLD_KM) - set(reference["calibration"])
    if missing:
        raise SystemExit(f"No routed pairs to calibrate {sorted(missing)}")
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_FILE.write_text(json.dumps(reference, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    print(f"Wrote {len(amenities)} amenities and {len(reference['calibration'])} calibrations to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()

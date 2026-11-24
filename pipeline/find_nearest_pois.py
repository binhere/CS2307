#!/usr/bin/env python3
"""Step 2: Find up to Top-5 nearest OSM POIs per category (straight-line cKDTree).

Reads:
  data/silver/listings_verified_coords.csv
  data/bronze/osm_poi_nodes.csv

Writes:
  data/silver/listing_poi_top5_candidates.csv
"""
from __future__ import annotations

import argparse
import collections
import csv
import math
import sys
from pathlib import Path

import numpy as np
from scipy.spatial import cKDTree

csv.field_size_limit(min(sys.maxsize, 2**31 - 1))

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DEFAULT_LISTINGS = DATA_DIR / "silver" / "listings_verified_coords.csv"
DEFAULT_POIS = DATA_DIR / "bronze" / "osm_poi_nodes.csv"
DEFAULT_OUTPUT = DATA_DIR / "silver" / "listing_poi_top5_candidates.csv"

CATEGORIES = [
    "hospital",
    "school",
    "college_university",
    "metro",
    "mall",
    "market",
    "supermarket",
    "park",
    "bus_stop",
]

RADIUS_KM = {
    "hospital": 7.0,
    "school": 3.0,
    "college_university": 7.0,
    "metro": 5.0,
    "mall": 7.0,
    "market": 3.0,
    "supermarket": 3.0,
    "park": 5.0,
    "bus_stop": 2.0,
}

TOP_K = 5


def to_unit_sphere(latlon: list[tuple[float, float]]) -> np.ndarray:
    """Convert (lat, lon) degrees to 3D unit-sphere vectors for exact chord/haversine queries."""
    rad = np.radians(np.asarray(latlon, dtype=float))
    cos_lat = np.cos(rad[:, 0])
    return np.column_stack((
        cos_lat * np.cos(rad[:, 1]),
        cos_lat * np.sin(rad[:, 1]),
        np.sin(rad[:, 0]),
    ))


def chord_to_km(chord: float) -> float:
    return 12742.0 * math.asin(min(1.0, float(chord) / 2.0))


def find_nearest_pois(listings_path: Path, pois_path: Path, output_path: Path) -> None:
    with open(listings_path, encoding="utf-8-sig", newline="") as f:
        all_listings = list(csv.DictReader(f))

    located = [
        r for r in all_listings
        if (r.get("latitude") or "").strip() and (r.get("longitude") or "").strip()
    ]

    with open(pois_path, encoding="utf-8-sig", newline="") as f:
        pois = list(csv.DictReader(f))

    listing_coords = [(float(r["latitude"]), float(r["longitude"])) for r in located]
    listing_xyz = to_unit_sphere(listing_coords)

    edges: list[dict[str, object]] = []
    cat_counts: collections.Counter[str] = collections.Counter()

    for cat in CATEGORIES:
        cat_pois = [
            p for p in pois
            if cat in (p.get("category") or "").split("|")
            and (p.get("latitude") or "").strip()
            and (p.get("longitude") or "").strip()
        ]
        if not cat_pois or not located:
            continue

        poi_coords = [(float(p["latitude"]), float(p["longitude"])) for p in cat_pois]
        tree = cKDTree(to_unit_sphere(poi_coords))
        k = min(TOP_K, len(cat_pois))
        dd, ii = tree.query(listing_xyz, k=k)
        dd = np.atleast_2d(dd)
        ii = np.atleast_2d(ii)
        if k == 1:
            dd = dd.reshape(-1, 1)
            ii = ii.reshape(-1, 1)

        max_km = RADIUS_KM[cat]
        for row_idx, listing in enumerate(located):
            rank = 0
            for chord, poi_idx in zip(dd[row_idx], ii[row_idx]):
                km = chord_to_km(chord)
                if km > max_km:
                    continue
                rank += 1
                poi = cat_pois[int(poi_idx)]
                edges.append({
                    "listing_id": listing["listing_id"],
                    "osm_id": poi["osm_id"],
                    "category": cat,
                    "poi_name": poi.get("name", ""),
                    "poi_subtype": poi.get("subtype", ""),
                    "rank": rank,
                    "straight_line_km": round(km, 6),
                    "listing_latitude": listing["latitude"],
                    "listing_longitude": listing["longitude"],
                    "poi_latitude": poi["latitude"],
                    "poi_longitude": poi["longitude"],
                    "coordinate_status": listing.get("coordinate_status", ""),
                })
                cat_counts[cat] += 1

    # Sort deterministically by listing_id, category, rank
    edges.sort(key=lambda e: (int(e["listing_id"]), CATEGORIES.index(str(e["category"])), int(e["rank"])))

    fieldnames = [
        "listing_id",
        "osm_id",
        "category",
        "poi_name",
        "poi_subtype",
        "rank",
        "straight_line_km",
        "listing_latitude",
        "listing_longitude",
        "poi_latitude",
        "poi_longitude",
        "coordinate_status",
    ]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(edges)

    print(f"Listings input: {len(all_listings)} total, {len(located)} with coordinates")
    print(f"POIs input: {len(pois)}")
    print(f"Candidate edges written: {len(edges)} -> {output_path}")
    print("Edges by category:", dict(cat_counts))


def main() -> None:
    parser = argparse.ArgumentParser(description="Find Top-5 nearest OSM POIs per category.")
    parser.add_argument("--listings", type=Path, default=DEFAULT_LISTINGS)
    parser.add_argument("--pois", type=Path, default=DEFAULT_POIS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    find_nearest_pois(args.listings, args.pois, args.output)


if __name__ == "__main__":
    main()


#!/usr/bin/env python3
"""Verify listing coordinates, impute invalid coordinates via exact-address clusters,
and assign 150 m spatial cluster IDs.

Reads:
  crawl_data/data/cleaned_master_data.csv

Writes:
  crawl_data/data/listings_verified_coords.csv
"""
from __future__ import annotations

import argparse
import collections
import csv
import math
import re
import sys
import unicodedata
from pathlib import Path

import numpy as np
from scipy.spatial import cKDTree

csv.field_size_limit(min(sys.maxsize, 2**31 - 1))

DEFAULT_INPUT = Path(__file__).resolve().parent / "data" / "cleaned_master_data.csv"
DEFAULT_OUTPUT = Path(__file__).resolve().parent / "data" / "listings_verified_coords.csv"

LAT_RANGE = (9.5, 11.5)
LON_RANGE = (105.8, 107.6)
ADDRESS_CLUSTER_RADIUS_M = 250.0
GEO_CLUSTER_RADIUS_M = 150.0


def clean(s: object) -> str:
    return str(s or "").strip()


def norm(s: object) -> str:
    """Normalize Vietnamese text to lowercase ASCII tokens for exact address matching."""
    text = unicodedata.normalize("NFD", clean(s).lower().replace("đ", "d"))
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    return " ".join(re.findall(r"[a-z0-9]+", text))


def parse_latlon(value: object) -> tuple[float, float] | None:
    m = re.search(r"(?:q=)?\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)", clean(value))
    return (float(m.group(1)), float(m.group(2))) if m else None


def in_hcm_area(coord: tuple[float, float] | None) -> bool:
    if coord is None:
        return False
    lat, lon = coord
    return LAT_RANGE[0] <= lat <= LAT_RANGE[1] and LON_RANGE[0] <= lon <= LON_RANGE[1]


def dist_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    q = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 12742000.0 * math.asin(min(1.0, math.sqrt(q)))


def to_local_m(coords: list[tuple[float, float]]) -> np.ndarray:
    arr = np.asarray(coords, dtype=float)
    return np.column_stack((
        (arr[:, 0] - 10.8) * 111195.0,
        (arr[:, 1] - 106.7) * 111195.0 * math.cos(math.radians(10.8)),
    ))


def compute_geo_clusters(coords: list[tuple[float, float]], radius_m: float = GEO_CLUSTER_RADIUS_M) -> list[str]:
    """Assign deterministic connected-component cluster IDs within radius_m."""
    n = len(coords)
    if n == 0:
        return []
    tree = cKDTree(to_local_m(coords))
    neighbors = tree.query_ball_tree(tree, r=radius_m)

    comp = [-1] * n
    cid = 0
    for start in range(n):
        if comp[start] != -1:
            continue
        cid += 1
        queue = collections.deque([start])
        comp[start] = cid
        while queue:
            u = queue.popleft()
            for v in neighbors[u]:
                if comp[v] == -1:
                    comp[v] = cid
                    queue.append(v)
    return [f"gc_{c:04d}" for c in comp]


def verify_coordinates(input_path: Path, output_path: Path) -> None:
    with open(input_path, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        orig_fields = list(reader.fieldnames or [])

    # First pass: collect valid original coordinates indexed by normalized address.
    address_candidates: dict[str, list[tuple[float, float]]] = collections.defaultdict(list)
    parsed_originals: list[tuple[float, float] | None] = []
    for r in rows:
        orig = parse_latlon(r.get("latitude_longitude"))
        parsed_originals.append(orig)
        if in_hcm_area(orig):
            addr_key = norm(r.get("address"))
            if addr_key:
                address_candidates[addr_key].append(orig)  # type: ignore[arg-type]

    # Second pass: verify, impute safely when invalid, and count reference neighbors.
    status_counts: collections.Counter[str] = collections.Counter()
    valid_indices: list[int] = []
    valid_coords: list[tuple[float, float]] = []

    for idx, r in enumerate(rows):
        orig = parsed_originals[idx]
        if in_hcm_area(orig):
            coord = orig
            status = "valid"
            n_references = 0
        else:
            coord = None
            status = "unresolved"
            n_references = 0

            addr_key = norm(r.get("address"))
            candidates = address_candidates.get(addr_key, [])
            if candidates:
                pts = to_local_m(candidates)
                tree = cKDTree(pts)
                neighbors = [tree.query_ball_point(pt, r=ADDRESS_CLUSTER_RADIUS_M) for pt in pts]
                best = max(neighbors, key=len)
                if len(best) >= max(1, math.ceil(0.6 * len(candidates))):
                    cluster = np.array([candidates[i] for i in best], dtype=float)
                    med = (float(np.median(cluster[:, 0])), float(np.median(cluster[:, 1])))
                    if max(dist_m(med, tuple(pt)) for pt in cluster) < ADDRESS_CLUSTER_RADIUS_M:
                        coord = med
                        status = "imputed"
                        n_references = len(best)

        status_counts[status] += 1
        r["latitude"] = f"{coord[0]:.7f}" if coord else ""
        r["longitude"] = f"{coord[1]:.7f}" if coord else ""
        r["coordinate_status"] = status
        r["n_references"] = str(n_references)
        r["geo_cluster_150m"] = ""

        if coord is not None:
            valid_indices.append(idx)
            valid_coords.append(coord)

    # Third pass: 150m spatial clustering for valid/imputed coordinates.
    cluster_ids = compute_geo_clusters(valid_coords, radius_m=GEO_CLUSTER_RADIUS_M)
    for row_idx, cid in zip(valid_indices, cluster_ids):
        rows[row_idx]["geo_cluster_150m"] = cid

    added_cols = [
        "latitude",
        "longitude",
        "coordinate_status",
        "n_references",
        "geo_cluster_150m",
    ]
    out_fields = orig_fields + [c for c in added_cols if c not in orig_fields]

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=out_fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    print(f"Input rows: {len(rows)} ({len(orig_fields)} cols)")
    print(f"Output rows: {len(rows)} ({len(out_fields)} cols) -> {output_path}")
    print(f"coordinate_status breakdown: {dict(status_counts)}")
    print(f"Usable coordinates: {len(valid_indices)} / {len(rows)}")
    print(f"150m geo clusters: {len(set(cluster_ids))}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify & cluster listing coordinates.")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    verify_coordinates(args.input, args.output)


if __name__ == "__main__":
    main()


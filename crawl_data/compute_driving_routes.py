#!/usr/bin/env python3
"""Step 3: Compute real driving distance & duration via OSRM /table API.

Reads:
  crawl_data/data/listing_poi_top5_candidates.csv

Writes:
  crawl_data/data/listing_poi_routes.csv
  crawl_data/cache/osrm/<sha256>.json
"""
from __future__ import annotations

import argparse
import collections
import csv
import datetime
import hashlib
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

csv.field_size_limit(min(sys.maxsize, 2**31 - 1))

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DEFAULT_INPUT = DATA_DIR / "listing_poi_top5_candidates.csv"
DEFAULT_OUTPUT = DATA_DIR / "listing_poi_routes.csv"
DEFAULT_CACHE = BASE_DIR / "cache" / "osrm"
DEFAULT_OSRM_URL = "https://router.project-osrm.org"

MAX_SOURCES = 32
MAX_COORDS = 100


def morton_key(lon_lat: tuple[float, float]) -> int:
    """Spatial Z-order curve key to keep nearby listings in the same OSRM batch."""
    x = max(0, int((lon_lat[0] - 105.0) * 100000))
    y = max(0, int((lon_lat[1] - 9.0) * 100000))
    val = 0
    for bit in range(20):
        val |= ((x >> bit) & 1) << (2 * bit)
        val |= ((y >> bit) & 1) << (2 * bit + 1)
    return val


def build_batches(
    rows: list[dict[str, str]],
) -> tuple[
    dict[str, tuple[float, float]],
    list[tuple[list[tuple[float, float]], list[str]]],
]:
    """Group unique listing coordinates into bounded OSRM /table matrix batches."""
    poi_coords: dict[str, tuple[float, float]] = {}
    origin_targets: dict[tuple[float, float], set[str]] = collections.defaultdict(set)

    for r in rows:
        oid = r["osm_id"]
        if oid not in poi_coords:
            poi_coords[oid] = (float(r["poi_longitude"]), float(r["poi_latitude"]))
        src_ll = (float(r["listing_longitude"]), float(r["listing_latitude"]))
        origin_targets[src_ll].add(oid)

    batches: list[tuple[list[tuple[float, float]], list[str]]] = []
    cur_src: list[tuple[float, float]] = []
    cur_dest: set[str] = set()

    for src_ll in sorted(origin_targets, key=morton_key):
        targets = origin_targets[src_ll]
        merged = cur_dest | targets
        if cur_src and (len(cur_src) + 1 + len(merged) > MAX_COORDS or len(cur_src) >= MAX_SOURCES):
            batches.append((cur_src, sorted(cur_dest)))
            cur_src = []
            cur_dest = set()
            merged = targets
        cur_src.append(src_ll)
        cur_dest = merged

    if cur_src:
        batches.append((cur_src, sorted(cur_dest)))

    return poi_coords, batches


def build_osrm_url(
    osrm_url: str,
    src_coords: list[tuple[float, float]],
    dest_oids: list[str],
    poi_coords: dict[str, tuple[float, float]],
) -> str:
    coords = src_coords + [poi_coords[oid] for oid in dest_oids]
    parts = ";".join(f"{lon:.7f},{lat:.7f}" for lon, lat in coords)
    params = urllib.parse.urlencode({
        "sources": ";".join(map(str, range(len(src_coords)))),
        "destinations": ";".join(map(str, range(len(src_coords), len(coords)))),
        "annotations": "distance,duration",
        "generate_hints": "false",
    })
    return f"{osrm_url.rstrip('/')}/table/v1/driving/{parts}?{params}"


def cache_path_for(cache_dir: Path, url: str) -> Path:
    key = hashlib.sha256(url.encode("utf-8")).hexdigest()
    return cache_dir / f"{key}.json"


def fetch_batch(url: str, timeout: int = 45) -> dict:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "IT2047RealEstateEnrichment/1.0 (academic spatial routing)",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.load(resp)
    if data.get("code") != "Ok":
        raise ValueError(f"OSRM error: {str(data)[:200]}")
    return data


def compute_routes(
    input_path: Path,
    output_path: Path,
    cache_dir: Path,
    osrm_url: str,
    delay: float,
    max_requests: int | None,
    plan_only: bool,
) -> None:
    if not osrm_url.startswith(("http://", "https://")):
        raise SystemExit("ERROR: --osrm-url must start with http:// or https://")

    with open(input_path, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        in_fields = list(reader.fieldnames or [])

    poi_coords, batches = build_batches(rows)
    cache_dir.mkdir(parents=True, exist_ok=True)

    cached_count = sum(
        1 for src, dest in batches
        if cache_path_for(cache_dir, build_osrm_url(osrm_url, src, dest, poi_coords)).exists()
    )
    max_batch_coords = max((len(s) + len(d) for s, d in batches), default=0)

    print(f"Input edges: {len(rows)}")
    print(f"Unique POIs: {len(poi_coords)}")
    print(f"Total OSRM batches: {len(batches)} (max_coords={max_batch_coords})")
    print(f"Already cached: {cached_count} | Remaining to fetch: {len(batches) - cached_count}")

    if plan_only:
        est_sec = (len(batches) - cached_count) * delay
        print(f"Plan only (--plan). Estimated fetch time at delay={delay}s: ~{est_sec / 60:.1f} min")
        return

    # Execute / read cached OSRM responses
    answers: dict[tuple[tuple[float, float], str], dict[str, object]] = {}
    fetched = 0
    failed_batches = 0

    for idx, (src_coords, dest_oids) in enumerate(batches, 1):
        url = build_osrm_url(osrm_url, src_coords, dest_oids, poi_coords)
        fp = cache_path_for(cache_dir, url)
        data = None

        if fp.exists():
            record = json.loads(fp.read_text(encoding="utf-8"))
            data = record["response"]
        else:
            if max_requests is not None and fetched >= max_requests:
                continue
            for attempt in range(2):
                try:
                    data = fetch_batch(url)
                    record = {
                        "fetched_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                        "endpoint": osrm_url,
                        "response": data,
                    }
                    fp.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
                    fetched += 1
                    break
                except Exception as exc:
                    print(f"[Batch {idx}/{len(batches)}] attempt {attempt + 1} failed: {exc!r}", flush=True)
                    if isinstance(exc, urllib.error.HTTPError) and exc.code in (403, 429):
                        break
                    if attempt == 0:
                        time.sleep(5)
            if data is None:
                failed_batches += 1
                print("Stopping network requests after service failure; cached progress kept.", flush=True)
                break
            if delay > 0:
                time.sleep(delay)

        distances = data["distances"]
        durations = data["durations"]
        src_snaps = [s.get("distance") for s in data.get("sources", [])]
        dest_snaps = [d.get("distance") for d in data.get("destinations", [])]

        for si, src_ll in enumerate(src_coords):
            s_off = round(float(src_snaps[si]), 2) if si < len(src_snaps) and src_snaps[si] is not None else ""
            for di, oid in enumerate(dest_oids):
                dist_m = distances[si][di]
                dur_s = durations[si][di]
                d_off = round(float(dest_snaps[di]), 2) if di < len(dest_snaps) and dest_snaps[di] is not None else ""
                if dist_m is not None and dur_s is not None:
                    answers[(src_ll, oid)] = {
                        "driving_distance_km": round(float(dist_m) / 1000.0, 6),
                        "driving_duration_min": round(float(dur_s) / 60.0, 4),
                        "listing_road_offset_m": s_off,
                        "poi_road_offset_m": d_off,
                        "route_status": "ok",
                    }
                else:
                    answers[(src_ll, oid)] = {
                        "driving_distance_km": "",
                        "driving_duration_min": "",
                        "listing_road_offset_m": s_off,
                        "poi_road_offset_m": d_off,
                        "route_status": "no_route",
                    }

    # Populate OSRM fields onto candidate rows
    status_counts: collections.Counter[str] = collections.Counter()
    grouped_ok: dict[tuple[str, str], list[tuple[float, int]]] = collections.defaultdict(list)

    for row_idx, r in enumerate(rows):
        src_ll = (float(r["listing_longitude"]), float(r["listing_latitude"]))
        ans = answers.get((src_ll, r["osm_id"]))
        if ans:
            r["driving_distance_km"] = str(ans["driving_distance_km"])
            r["driving_duration_min"] = str(ans["driving_duration_min"])
            r["listing_road_offset_m"] = str(ans["listing_road_offset_m"])
            r["poi_road_offset_m"] = str(ans["poi_road_offset_m"])
            r["route_status"] = str(ans["route_status"])
        else:
            r["driving_distance_km"] = ""
            r["driving_duration_min"] = ""
            r["listing_road_offset_m"] = ""
            r["poi_road_offset_m"] = ""
            r["route_status"] = "pending"
        r["driving_rank"] = ""
        status_counts[r["route_status"]] += 1

        if r["route_status"] == "ok":
            grouped_ok[(r["listing_id"], r["category"])].append((float(r["driving_distance_km"]), row_idx))

    # Compute driving_rank (1..k) per (listing_id, category) for route_status == 'ok'
    for items in grouped_ok.values():
        items.sort(key=lambda x: x[0])
        for d_rank, (_, row_idx) in enumerate(items, 1):
            rows[row_idx]["driving_rank"] = str(d_rank)

    added_cols = [
        "driving_distance_km",
        "driving_duration_min",
        "driving_rank",
        "listing_road_offset_m",
        "poi_road_offset_m",
        "route_status",
    ]
    out_fields = in_fields + [c for c in added_cols if c not in in_fields]

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=out_fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    print(f"Fetched new batches: {fetched} (failed: {failed_batches})")
    print(f"Route status breakdown: {dict(status_counts)}")
    print(f"Output written: {len(rows)} rows ({len(out_fields)} cols) -> {output_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Step 3: Compute OSRM driving routes for Top-5 candidates.")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--osrm-url", default=DEFAULT_OSRM_URL)
    parser.add_argument("--delay", type=float, default=2.0, help="Seconds to wait between live OSRM requests.")
    parser.add_argument("--max-requests", type=int, default=None, help="Max new network requests per run.")
    parser.add_argument("--plan", action="store_true", help="Dry-run: print batch plan without calling OSRM.")
    args = parser.parse_args()
    compute_routes(
        input_path=args.input,
        output_path=args.output,
        cache_dir=args.cache_dir,
        osrm_url=args.osrm_url,
        delay=args.delay,
        max_requests=args.max_requests,
        plan_only=args.plan,
    )


if __name__ == "__main__":
    main()

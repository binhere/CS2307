#!/usr/bin/env python3
"""Build the address reference data that validates posted listings' wards, districts and coordinates.

Ho Chi Minh City's wards changed on 1 July 2025 (district level removed, Bình Dương and Bà Rịa -
Vũng Tàu merged in), so the catalog's own place names are both incomplete and partly obsolete.
This script fetches three public sources once and writes them, matched and trimmed, into the repo
so validation needs no network call:

- official wards: provinces.open-api.vn v2 (province 79), 168 wards;
- old ward -> new ward, with the old district: undertheseanlp/address data/mapping.json
  (Apache-2.0, built from the vietnamadminunits package);
- ward and city boundaries: ThangLeQuoc/vietnamese-provinces-database (MIT), GeoJSON captured from
  sapnhap.bando.com.vn; simplified here (Douglas-Peucker) to keep the file small.

Usage:
    python backend/scripts/export_address_reference.py [--tolerance-degrees 0.00005]

Writes:
    backend/src/services/listing_submission/reference/hcmc_wards.json
    backend/src/services/listing_submission/reference/hcmc_boundaries.json
Standard library only; re-run when a source publishes a correction.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Sequence, Tuple

WARDS_URL = "https://provinces.open-api.vn/api/v2/p/79?depth=2"
MAPPING_URL = "https://huggingface.co/undertheseanlp/address/resolve/main/data/mapping.json"
BOUNDARY_TREE_URL = "https://api.github.com/repos/ThangLeQuoc/vietnamese-provinces-database/git/trees/master?recursive=1"
BOUNDARY_RAW_URL = "https://raw.githubusercontent.com/ThangLeQuoc/vietnamese-provinces-database/master/"
BOUNDARY_FOLDER = re.compile(r"/geojson_[^/]+/\d+_ho_chi_minh/")
NEW_PROVINCE_KEY = "thanhphohochiminh"
OUTPUT_DIRECTORY = Path(__file__).resolve().parents[1] / "src" / "services" / "listing_submission" / "reference"

# Former district names people still use that the 2025 mapping already merged into a larger unit;
# they stay valid former districts and count as that unit when checking a ward.
HISTORIC_FORMER_DISTRICTS = {
    "Quận 2": "Thành phố Thủ Đức",       # merged into Thủ Đức city in 2021
    "Quận 9": "Thành phố Thủ Đức",
    "Quận Thủ Đức": "Thành phố Thủ Đức",
    "Huyện Long Điền": "Huyện Long Đất",  # merged into Long Đất in 2025
    "Huyện Đất Đỏ": "Huyện Long Đất",
}
ADMINISTRATIVE_WORDS = ("dac khu", "thanh pho", "thi xa", "thi tran", "phuong", "quan", "huyen", "xa")
# Boundary files whose names do not identify one ward, assigned by where their polygon lies (checked
# against OpenStreetMap): the source's slug generator dropped "Quán" from Chợ Quán, and "Thanh An"
# (Dầu Tiếng, north) and "Thạnh An" (Cần Giờ island, south) share one accent-free slug.
BOUNDARY_FILE_WARDS = {
    "53_cho_phuong": "Phường Chợ Quán",
    "146_thanh_an_xa": "Xã Thanh An",
    "147_thanh_an_xa": "Xã Thạnh An",
}


def plain(value: str) -> str:
    """Lowercase, accent-free, single-spaced text ("Phường Cát Lái" -> "phuong cat lai")."""
    decomposed = unicodedata.normalize("NFKD", value or "")
    text = "".join(character for character in decomposed if not unicodedata.combining(character))
    return re.sub(r"\s+", " ", text.lower().replace("đ", "d")).strip()


def split_name(name: str) -> Tuple[str, str]:
    """(keyword, bare name) of a place name: "Phường Cát Lái" -> ("phuong", "cat lai")."""
    text = plain(name)
    for word in ADMINISTRATIVE_WORDS:
        if text.startswith(word + " "):
            return word, text[len(word) + 1:]
    return "", text


def fetch_json(url: str):
    """GET a URL and parse its JSON body."""
    request = urllib.request.Request(url, headers={"User-Agent": "address-reference-export/1.0"})
    with urllib.request.urlopen(request, timeout=120) as response:
        return json.load(response)


def perpendicular_distance(point: Sequence[float], start: Sequence[float], end: Sequence[float]) -> float:
    """Distance from a point to the segment start-end, in degrees."""
    delta_x, delta_y = end[0] - start[0], end[1] - start[1]
    if delta_x == 0 and delta_y == 0:
        return ((point[0] - start[0]) ** 2 + (point[1] - start[1]) ** 2) ** 0.5
    position = ((point[0] - start[0]) * delta_x + (point[1] - start[1]) * delta_y) / (delta_x ** 2 + delta_y ** 2)
    position = max(0.0, min(1.0, position))
    nearest_x, nearest_y = start[0] + position * delta_x, start[1] + position * delta_y
    return ((point[0] - nearest_x) ** 2 + (point[1] - nearest_y) ** 2) ** 0.5


def simplify_ring(ring: List[List[float]], tolerance: float) -> List[List[float]]:
    """Douglas-Peucker simplification of a closed ring, iterative so long rings do not recurse deeply.

    Returns:
        list: The kept points rounded to 6 decimals (about 0.1 m); at least 4 points (a triangle,
        closed), or the original ring when it is that small already.
    """
    if len(ring) <= 4:
        return [[round(x, 6), round(y, 6)] for x, y in ring]
    keep = [False] * len(ring)
    keep[0] = keep[-1] = True
    stack = [(0, len(ring) - 1)]
    while stack:
        first, last = stack.pop()
        farthest, farthest_distance = None, tolerance
        for index in range(first + 1, last):
            distance = perpendicular_distance(ring[index], ring[first], ring[last])
            if distance > farthest_distance:
                farthest, farthest_distance = index, distance
        if farthest is not None:
            keep[farthest] = True
            stack.extend(((first, farthest), (farthest, last)))
    kept = [[round(point[0], 6), round(point[1], 6)] for point, is_kept in zip(ring, keep) if is_kept]
    if len(kept) < 4:
        # A ring that collapses keeps its points spread around the original instead.
        step = max(1, len(ring) // 3)
        kept = [[round(x, 6), round(y, 6)] for x, y in ring[::step]][:3]
        kept.append(kept[0])
    return kept


def simplified_polygons(geometry: dict, tolerance: float) -> List[List[List[List[float]]]]:
    """A GeoJSON Polygon or MultiPolygon as a list of polygons (outer ring, then holes), simplified."""
    polygons = [geometry["coordinates"]] if geometry["type"] == "Polygon" else geometry["coordinates"]
    return [[simplify_ring(ring, tolerance) for ring in polygon] for polygon in polygons]


def load_boundaries(tolerance: float) -> Tuple[Dict[str, Tuple[Tuple[str, str], list]], list]:
    """Download HCMC's ward and city boundaries.

    Returns:
        tuple: ({file stem: ((keyword, bare name), polygons)}, city polygons). Ward files are named
        "<n>_<bare_name>_<type>.geojson", e.g. "44_cat_lai_phuong.geojson".
    """
    tree = fetch_json(BOUNDARY_TREE_URL)["tree"]
    paths = [item["path"] for item in tree if BOUNDARY_FOLDER.search(item["path"]) and item["path"].endswith(".geojson")]
    if not paths:
        raise SystemExit("no Ho Chi Minh City boundary files found in the repository tree")

    def download(repository_path: str) -> Tuple[str, dict]:
        return repository_path, fetch_json(BOUNDARY_RAW_URL + urllib.parse.quote(repository_path))

    with ThreadPoolExecutor(max_workers=8) as pool:
        files = list(pool.map(download, paths))
    wards: Dict[str, Tuple[Tuple[str, str], list]] = {}
    city: list = []
    for repository_path, collection in files:
        polygons = [polygon for feature in collection["features"]
                    for polygon in simplified_polygons(feature["geometry"], tolerance)]
        file_name = repository_path.rsplit("/", 1)[-1][: -len(".geojson")]
        if file_name == "province":
            city = polygons
            continue
        words = plain(re.sub(r"^\d+_", "", file_name).replace("_", " ")).split()
        keyword = next((word for word in ADMINISTRATIVE_WORDS if " ".join(words[-len(word.split()):]) == word), "")
        bare_name = " ".join(words[: len(words) - len(keyword.split())]) if keyword else " ".join(words)
        wards[file_name] = ((keyword, bare_name), polygons)
    return wards, city


def build(tolerance: float) -> Tuple[dict, dict]:
    """Fetch the sources and return (ward reference, boundaries)."""
    official = fetch_json(WARDS_URL)["wards"]
    records = [record for record in fetch_json(MAPPING_URL)["ward_mapping"] if record["new_province_key"] == NEW_PROVINCE_KEY]
    boundaries, city = load_boundaries(tolerance)

    wards = []
    by_name: Dict[str, dict] = {}
    for ward in sorted(official, key=lambda item: item["code"]):
        name = unicodedata.normalize("NFC", ward["name"])
        keyword, bare_name = split_name(name)
        entry = {"code": str(ward["code"]), "name": name, "keyword": keyword, "bare_name": bare_name,
                 "former_districts": [], "former_wards": []}
        wards.append(entry)
        by_name[name] = entry
    by_plain_name: Dict[str, List[dict]] = {}
    for entry in wards:
        by_plain_name.setdefault(plain(entry["name"]), []).append(entry)

    def new_ward_of(record: dict) -> dict | None:
        """The official ward a mapping record leads to, matched by exact name, then accent-free name."""
        name = unicodedata.normalize("NFC", record["new_ward"])
        if name in by_name:
            return by_name[name]
        candidates = by_plain_name.get(plain(name), [])
        return candidates[0] if len(candidates) == 1 else None

    # The source keys wards by accent-free names, so "Xã Thanh An" (Dầu Tiếng) and "Xã Thạnh An"
    # (Cần Giờ) collided and every record leading to them lost its new name. A renamed record keeps
    # its old name as the new one; a divided record goes to the lost ward of the same old district.
    blank_records = [record for record in records if not record["new_ward"]]
    lost_ward_by_district: Dict[str, str] = {}
    for record in blank_records:
        if record["mapping_type"] == "renamed" and unicodedata.normalize("NFC", record["old_ward"]) in by_name:
            record["new_ward"] = record["old_ward"]
            lost_ward_by_district[record["old_district"]] = record["old_ward"]
    for record in blank_records:
        if not record["new_ward"] and record["old_district"] in lost_ward_by_district:
            record["new_ward"] = lost_ward_by_district[record["old_district"]]

    unmatched = set()
    for record in records:
        entry = new_ward_of(record)
        if entry is None:
            unmatched.add((record["old_district"], record["old_ward"], record["new_ward"]))
            continue
        district = unicodedata.normalize("NFC", record["old_district"])
        former_ward = {"district": district, "ward": unicodedata.normalize("NFC", record["old_ward"]),
                       "is_divided": record["mapping_type"] == "divided"}
        if former_ward not in entry["former_wards"]:
            entry["former_wards"].append(former_ward)
        if district not in entry["former_districts"]:
            entry["former_districts"].append(district)
    problems = []
    if unmatched:
        problems.append(f"mapping names no official ward: {sorted(unmatched)}")
    problems += [f"{ward['name']} has no former district" for ward in wards if not ward["former_districts"]]
    polygons_by_name: Dict[str, list] = {}
    for file_stem, (key, polygons) in boundaries.items():
        candidates = [ward["name"] for ward in wards if (ward["keyword"], ward["bare_name"]) == key]
        name = BOUNDARY_FILE_WARDS.get(file_stem) or (candidates[0] if len(candidates) == 1 else None)
        if name is None or name in polygons_by_name:
            problems.append(f"boundary file {file_stem} matches {candidates or 'no ward'}")
            continue
        polygons_by_name[name] = polygons
    missing_boundaries = [ward["name"] for ward in wards if ward["name"] not in polygons_by_name]
    if missing_boundaries:
        problems.append(f"no boundary for {missing_boundaries}")
    if problems:
        raise SystemExit("reference data does not line up:\n  " + "\n  ".join(problems))

    former_districts = sorted({district for ward in wards for district in ward["former_districts"]})
    fetched_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    reference = {
        "sources": {
            "wards": WARDS_URL, "former_wards": MAPPING_URL + " (Apache-2.0)",
            "boundaries": "github.com/ThangLeQuoc/vietnamese-provinces-database (MIT), from sapnhap.bando.com.vn",
            "fetched_at": fetched_at,
        },
        "wards": wards,
        "former_districts": [{"name": name, "same_as": None} for name in former_districts]
        + [{"name": name, "same_as": same_as} for name, same_as in HISTORIC_FORMER_DISTRICTS.items()],
    }
    boundary_data = {
        "sources": reference["sources"], "tolerance_degrees": tolerance, "city": city,
        "wards": {ward["code"]: polygons_by_name[ward["name"]] for ward in wards},
    }
    return reference, boundary_data


def main() -> None:
    """Parse arguments, build the reference data and write both files."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--tolerance-degrees", type=float, default=0.00005,
                        help="boundary simplification tolerance; 0.00005 degrees is about 5.5 m")
    arguments = parser.parse_args()
    reference, boundaries = build(arguments.tolerance_degrees)
    OUTPUT_DIRECTORY.mkdir(parents=True, exist_ok=True)
    for file_name, content in (("hcmc_wards.json", reference), ("hcmc_boundaries.json", boundaries)):
        target = OUTPUT_DIRECTORY / file_name
        target.write_text(json.dumps(content, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
        print(f"wrote {target} ({target.stat().st_size / 1e6:.2f} MB)", file=sys.stderr)
    print(f"{len(reference['wards'])} wards, {len(reference['former_districts'])} former districts", file=sys.stderr)


if __name__ == "__main__":
    main()

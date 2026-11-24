#!/usr/bin/env python3
"""Export the place names the query parser may recognise.

The rule-based parser only accepts a location that exists in the served dataset,
so phrases such as "quan trong" ("important") or "khong xa trung tam" ("not far
from the centre") are not mistaken for a district or a commune. Re-run this
script whenever data/v3/graph is rebuilt:

    python backend/scripts/export_known_locations.py

Input: data/v3/graph/neo4j_ward_nodes.csv and neo4j_former_admin_area_nodes.csv
at the repository root (or --graph-dir).
Output: backend/src/search/known_locations.json, a list of
{"name", "kind", "keyword", "bare_name"} objects sorted by kind and name.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = BACKEND_ROOT.parent
sys.path.insert(0, str(BACKEND_ROOT))

from src.search.normalizer import normalize_text  # noqa: E402

DEFAULT_GRAPH_DIR = REPOSITORY_ROOT / "data" / "v3" / "graph"
OUTPUT_PATH = BACKEND_ROOT / "src" / "search" / "known_locations.json"
ADMIN_KEYWORDS = ("thanh pho", "thi xa", "huyen", "phuong", "quan", "xa")


def split_keyword(normalized_name: str) -> tuple[str, str]:
    """Split a normalized place name into its administrative keyword and bare name.

    Args:
        normalized_name: Accent-free lowercase name, e.g. "quan 7" or "thanh pho thu duc".

    Returns:
        (keyword, bare_name), e.g. ("quan", "7"). keyword is "" when the name has none.
    """
    for keyword in ADMIN_KEYWORDS:
        if normalized_name.startswith(keyword + " "):
            return keyword, normalized_name[len(keyword) + 1:]
    return "", normalized_name


def read_locations(graph_dir: Path) -> list[dict]:
    """Read wards and former administrative areas from the Neo4j import CSVs.

    Args:
        graph_dir: Folder holding neo4j_ward_nodes.csv and neo4j_former_admin_area_nodes.csv.

    Returns:
        One entry per place: name (display form with its prefix, e.g. "Phường Tân Định"),
        kind ("ward" or "former_area"), keyword and bare_name (accent-free, lowercase).
    """
    locations = []
    with (graph_dir / "neo4j_ward_nodes.csv").open(encoding="utf-8") as ward_file:
        for row in csv.DictReader(ward_file):
            ward_type = row["ward_type:string"].strip().capitalize()
            bare_name = normalize_text(row["normalized_name:string"])
            locations.append({
                "name": f"{ward_type} {row['name:string'].strip()}",
                "kind": "ward",
                "keyword": normalize_text(ward_type),
                "bare_name": bare_name,
            })
    with (graph_dir / "neo4j_former_admin_area_nodes.csv").open(encoding="utf-8") as area_file:
        for row in csv.DictReader(area_file):
            keyword, bare_name = split_keyword(normalize_text(row["normalized_name:string"]))
            locations.append({
                "name": row["name:string"].strip(),
                "kind": "former_area",
                "keyword": keyword,
                "bare_name": bare_name,
            })
    return sorted(locations, key=lambda location: (location["kind"], location["name"]))


def main() -> None:
    """Write known_locations.json from the graph CSVs; prints the entry count."""
    argument_parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    argument_parser.add_argument("--graph-dir", type=Path, default=DEFAULT_GRAPH_DIR)
    arguments = argument_parser.parse_args()
    locations = read_locations(arguments.graph_dir)
    OUTPUT_PATH.write_text(json.dumps(locations, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"Wrote {len(locations)} locations to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()

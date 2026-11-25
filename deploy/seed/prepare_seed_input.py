"""Select the listings the production seed contains from the data/v3 dataset.

The deployment serves only the current crawl: rows whose `source` is "new" or "both"
(3,857 listings). The 2,846 "old" rows are stale and carry a third-party Maps API key in
`location_link`, so they never enter the seed.

These listings are exactly the ones in data/gold (same ids, same content); data/v3 is read
because it also carries the columns the backend imports (amenity flags, wards, image keys).
Their image lists are taken from the image archive instead (data/gold/images_manifest.csv):
the 184 "both" listings also list photos of the old dataset that the archive does not
contain, and v3 stores keys in Unicode NFD while the archive (and so MinIO) uses NFC.

Input: --source-dir (default: data/v3 of this repository) holding master_v3.csv and graph/;
    --images-manifest (default: data/gold/images_manifest.csv), one row per archived image:
    listing_id, object_key.
Output: --output-dir/v3/master_v3.csv and --output-dir/v3/graph/*.csv, in the layout
    backend/scripts/build_search_index.py and deploy/seed/seed.py read. The `images` column
    holds the listing's archived keys (NFC, sorted, i.e. in photo order). Graph nodes that
    only removed listings reference (streets, 150 m clusters) are dropped; wards, former
    administrative areas and amenities are kept whole.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import unicodedata
from collections import defaultdict
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Set

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_IMAGES_MANIFEST = REPOSITORY_ROOT / "data" / "gold" / "images_manifest.csv"
SEED_SOURCES = {"new", "both"}
EXPECTED_LISTING_COUNT = 3857

# The CSV fields hold long multi-line descriptions.
csv.field_size_limit(sys.maxsize)


def read_rows(path: Path) -> tuple[List[str], List[Dict[str, str]]]:
    """Read a CSV file.

    Args:
        path: CSV file with a header row (a UTF-8 byte-order mark is accepted).

    Returns:
        tuple: (header field names, rows as dicts keyed by those names).
    """
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        return list(reader.fieldnames or []), list(reader)


def write_rows(path: Path, header: List[str], rows: Iterable[Dict[str, str]]) -> int:
    """Write rows under the given header.

    Args:
        path: Destination file; parent directories are created.
        header: Column order, copied from the source file.
        rows: Rows to write.

    Returns:
        int: Number of rows written.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=header)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
            count += 1
    return count


def filter_file(source: Path, target: Path, keep: Callable[[Dict[str, str]], bool]) -> List[Dict[str, str]]:
    """Copy the rows of one CSV file that satisfy a predicate.

    Args:
        source: Input CSV file.
        target: Output CSV file.
        keep: Predicate deciding whether a row is copied.

    Returns:
        list: The rows that were kept.
    """
    header, rows = read_rows(source)
    kept_rows = [row for row in rows if keep(row)]
    write_rows(target, header, kept_rows)
    return kept_rows


def select_listing_ids(master_rows: List[Dict[str, str]]) -> Set[str]:
    """Pick the listing ids of the current crawl.

    Args:
        master_rows: Rows of master_v3.csv.

    Returns:
        set: listing_id values whose source is in SEED_SOURCES.

    Raises:
        ValueError: A selected row still carries an API key in location_link.
    """
    selected = [row for row in master_rows if row.get("source") in SEED_SOURCES]
    leaked = [row["listing_id"] for row in selected if "key=" in (row.get("location_link") or "")]
    if leaked:
        raise ValueError(f"{len(leaked)} selected listings carry an API key in location_link, e.g. {leaked[:3]}")
    return {row["listing_id"] for row in selected}


def read_image_keys(manifest_path: Path) -> Dict[str, List[str]]:
    """Group the archived image keys by listing.

    Args:
        manifest_path: CSV with columns listing_id, object_key.

    Returns:
        dict: listing_id -> its object keys in NFC, sorted (file names end in _001, _002, ...).
    """
    keys_by_listing: Dict[str, List[str]] = defaultdict(list)
    for row in read_rows(manifest_path)[1]:
        keys_by_listing[row["listing_id"]].append(unicodedata.normalize("NFC", row["object_key"]))
    return {listing_id: sorted(keys) for listing_id, keys in keys_by_listing.items()}


def prepare(
    source_dir: Path,
    output_dir: Path,
    expected_count: int = EXPECTED_LISTING_COUNT,
    images_manifest: Path = DEFAULT_IMAGES_MANIFEST,
) -> Dict[str, int]:
    """Write the filtered dataset the seed build imports.

    Args:
        source_dir: The data/v3 directory.
        output_dir: Directory receiving v3/master_v3.csv and v3/graph/.
        expected_count: Number of listings the selection must yield; 0 skips the check.
        images_manifest: The archive's image list; replaces each listing's `images`.

    Returns:
        dict: Row count written per output file name, plus "images" (keys written).

    Raises:
        ValueError: The selection does not have the expected number of listings.
    """
    target = output_dir / "v3"
    graph_source = source_dir / "graph"
    graph_target = target / "graph"
    counts: Dict[str, int] = {}

    master_header, master_rows = read_rows(source_dir / "master_v3.csv")
    listing_ids = select_listing_ids(master_rows)
    if expected_count and len(listing_ids) != expected_count:
        raise ValueError(f"Selected {len(listing_ids)} listings, expected {expected_count}")
    image_keys = read_image_keys(images_manifest)
    selected_rows = []
    for row in master_rows:
        if row["listing_id"] in listing_ids:
            keys = image_keys.get(row["listing_id"], [])
            selected_rows.append({**row, "images": json.dumps(keys, ensure_ascii=False)})
    counts["master_v3.csv"] = write_rows(target / "master_v3.csv", master_header, selected_rows)
    counts["images"] = sum(len(image_keys.get(listing_id, [])) for listing_id in listing_ids)

    def starts_at_kept_listing(row: Dict[str, str]) -> bool:
        return row[":START_ID(Listing)"] in listing_ids

    listing_nodes = filter_file(graph_source / "neo4j_listing_nodes.csv", graph_target / "neo4j_listing_nodes.csv",
                                lambda row: row["listing_node_id:ID(Listing)"] in listing_ids)
    counts["neo4j_listing_nodes.csv"] = len(listing_nodes)
    for name in (
        "neo4j_listing_near_amenity_relationships.csv",
        "neo4j_listing_in_ward_relationships.csv",
        "neo4j_listing_in_former_area_relationships.csv",
    ):
        counts[name] = len(filter_file(graph_source / name, graph_target / name, starts_at_kept_listing))

    street_links = filter_file(graph_source / "neo4j_listing_on_street_relationships.csv",
                               graph_target / "neo4j_listing_on_street_relationships.csv", starts_at_kept_listing)
    cluster_links = filter_file(graph_source / "neo4j_listing_in_cluster_relationships.csv",
                                graph_target / "neo4j_listing_in_cluster_relationships.csv", starts_at_kept_listing)
    counts["neo4j_listing_on_street_relationships.csv"] = len(street_links)
    counts["neo4j_listing_in_cluster_relationships.csv"] = len(cluster_links)
    street_ids = {row[":END_ID(Street)"] for row in street_links}
    cluster_ids = {row[":END_ID(GeoCluster)"] for row in cluster_links}

    counts["neo4j_street_nodes.csv"] = len(filter_file(
        graph_source / "neo4j_street_nodes.csv", graph_target / "neo4j_street_nodes.csv",
        lambda row: row["street_id:ID(Street)"] in street_ids))
    counts["neo4j_street_in_ward_relationships.csv"] = len(filter_file(
        graph_source / "neo4j_street_in_ward_relationships.csv",
        graph_target / "neo4j_street_in_ward_relationships.csv",
        lambda row: row[":START_ID(Street)"] in street_ids))
    counts["neo4j_geo_cluster_nodes.csv"] = len(filter_file(
        graph_source / "neo4j_geo_cluster_nodes.csv", graph_target / "neo4j_geo_cluster_nodes.csv",
        lambda row: row["cluster_id:ID(GeoCluster)"] in cluster_ids))

    for name in (
        "neo4j_amenity_nodes.csv",
        "neo4j_ward_nodes.csv",
        "neo4j_former_admin_area_nodes.csv",
        "neo4j_ward_mapped_from_former_area_relationships.csv",
    ):
        counts[name] = len(filter_file(graph_source / name, graph_target / name, lambda row: True))
    return counts


def main() -> None:
    """Command-line entry point; prints the row count of every written file."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--source-dir", type=Path, default=REPOSITORY_ROOT / "data" / "v3")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--images-manifest", type=Path, default=DEFAULT_IMAGES_MANIFEST)
    arguments = parser.parse_args()
    counts = prepare(arguments.source_dir, arguments.output_dir, images_manifest=arguments.images_manifest)
    for name, count in counts.items():
        print(f"[prepare] {name}: {count} rows")


if __name__ == "__main__":
    main()

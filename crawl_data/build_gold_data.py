#!/usr/bin/env python3
"""Step 4: Build Gold-layer datasets for SQL (3NF) and Graph (Neo4j import format).

Reads:
  crawl_data/data/listings_verified_coords.csv
  crawl_data/data/osm_poi_nodes.csv
  crawl_data/data/listing_poi_routes.csv

Writes:
  crawl_data/data/gold/sql/
    - listings.csv
    - amenities.csv
    - listing_amenities.csv
  crawl_data/data/gold/graph/
    - graph_nodes_listing.csv
    - graph_nodes_amenity.csv
    - graph_nodes_geo_cluster.csv
    - graph_nodes_ward.csv
    - graph_edges_listing_near_amenity.csv
    - graph_edges_listing_in_cluster.csv
    - graph_edges_listing_in_ward.csv
"""
from __future__ import annotations

import argparse
import collections
import csv
import re
import sys
import unicodedata
from pathlib import Path

csv.field_size_limit(min(sys.maxsize, 2**31 - 1))

DATA_DIR = Path(__file__).resolve().parent / "data"
DEFAULT_LISTINGS = DATA_DIR / "listings_verified_coords.csv"
DEFAULT_POIS = DATA_DIR / "osm_poi_nodes.csv"
DEFAULT_ROUTES = DATA_DIR / "listing_poi_routes.csv"
DEFAULT_GOLD_DIR = DATA_DIR / "gold"

AMENITY_SUBLABELS = {
    "hospital": "Hospital",
    "school": "School",
    "college_university": "CollegeUniversity",
    "metro": "MetroStation",
    "mall": "Mall",
    "market": "Market",
    "supermarket": "Supermarket",
    "park": "Park",
    "bus_stop": "BusStop",
}


def clean(s: object) -> str:
    return str(s or "").strip()


def norm(s: object) -> str:
    text = unicodedata.normalize("NFD", clean(s).lower().replace("đ", "d"))
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    return " ".join(re.findall(r"[a-z0-9]+", text))


def slug(s: object) -> str:
    return norm(s).replace(" ", "_")


def iso_date(value: object) -> str:
    v = clean(value)
    m = re.fullmatch(r"(\d{2})/(\d{2})/(\d{4})", v)
    if m:
        return f"{m.group(3)}-{m.group(2)}-{m.group(1)}"
    return v


def read_csv(path: Path) -> tuple[list[dict[str, str]], list[str]]:
    with open(path, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        return list(reader), list(reader.fieldnames or [])


def write_csv(path: Path, rows: list[dict[str, object]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def export_sql(
    out_dir: Path,
    listings: list[dict[str, str]],
    listing_fields: list[str],
    pois: list[dict[str, str]],
    routes: list[dict[str, str]],
    road_offset_by_lid: dict[str, str],
) -> dict[str, int]:
    sql_dir = out_dir / "sql"

    # 1. listings.csv
    prop_fields = listing_fields + (["listing_road_offset_m"] if "listing_road_offset_m" not in listing_fields else [])
    prop_rows: list[dict[str, object]] = []
    for r in listings:
        out = dict(r)
        out["posted_date"] = iso_date(r.get("posted_date"))
        out["expiry_date"] = iso_date(r.get("expiry_date"))
        out["listing_road_offset_m"] = road_offset_by_lid.get(r["listing_id"], "")
        prop_rows.append(out)
    write_csv(sql_dir / "listings.csv", prop_rows, prop_fields)

    # 2. amenities.csv (drop raw tags_json for clean SQL catalog)
    amen_fields = [
        "osm_id",
        "name",
        "category",
        "subtype",
        "latitude",
        "longitude",
        "street",
        "address",
        "operator",
        "url",
        "timestamp",
        "coordinate_method",
    ]
    write_csv(sql_dir / "amenities.csv", pois, amen_fields)

    # 3. listing_amenities.csv (normalized bridge table, no duplicated POI names/coords)
    bridge_fields = [
        "listing_id",
        "osm_id",
        "category",
        "rank",
        "driving_rank",
        "straight_line_km",
        "driving_distance_km",
        "driving_duration_min",
        "poi_road_offset_m",
        "route_status",
    ]
    write_csv(sql_dir / "listing_amenities.csv", routes, bridge_fields)

    return {
        "listings": len(prop_rows),
        "amenities": len(pois),
        "listing_amenities": len(routes),
    }


def export_graph(
    out_dir: Path,
    listings: list[dict[str, str]],
    listing_fields: list[str],
    pois: list[dict[str, str]],
    routes: list[dict[str, str]],
    road_offset_by_lid: dict[str, str],
) -> dict[str, int]:
    graph_dir = out_dir / "graph"

    # 1. graph_listing_nodes.csv
    int_cols = {"bedrooms", "bathrooms", "n_references"}
    float_cols = {
        "area_price_history",
        "price_range",
        "area",
        "latitude",
        "longitude",
        "listing_road_offset_m",
    }
    date_cols = {"posted_date", "expiry_date"}

    raw_prop_cols = [c for c in listing_fields if c != "listing_id"] + ["listing_road_offset_m"]
    listing_header = ["listing_id:ID(Listing)"]
    for col in raw_prop_cols:
        if col in int_cols:
            listing_header.append(f"{col}:int")
        elif col in float_cols:
            listing_header.append(f"{col}:float")
        elif col in date_cols:
            listing_header.append(f"{col}:date")
        else:
            listing_header.append(f"{col}:string")
    listing_header.append(":LABEL")

    listing_nodes: list[dict[str, object]] = []
    for r in listings:
        node: dict[str, object] = {"listing_id:ID(Listing)": r["listing_id"], ":LABEL": "Listing"}
        for col in raw_prop_cols:
            val = road_offset_by_lid.get(r["listing_id"], "") if col == "listing_road_offset_m" else r.get(col, "")
            if col in date_cols:
                val = iso_date(val)
            if col in int_cols:
                node[f"{col}:int"] = val
            elif col in float_cols:
                node[f"{col}:float"] = val
            elif col in date_cols:
                node[f"{col}:date"] = val
            else:
                node[f"{col}:string"] = val
        listing_nodes.append(node)
    write_csv(graph_dir / "graph_nodes_listing.csv", listing_nodes, listing_header)

    # 2. graph_amenity_nodes.csv
    amen_header = [
        "osm_id:ID(Amenity)",
        "name:string",
        "category:string",
        "subtype:string",
        "latitude:float",
        "longitude:float",
        "street:string",
        "address:string",
        "operator:string",
        "url:string",
        ":LABEL",
    ]
    amen_nodes: list[dict[str, object]] = []
    for p in pois:
        sublabels = [AMENITY_SUBLABELS[c] for c in clean(p.get("category")).split("|") if c in AMENITY_SUBLABELS]
        amen_nodes.append({
            "osm_id:ID(Amenity)": p["osm_id"],
            "name:string": p.get("name", ""),
            "category:string": p.get("category", ""),
            "subtype:string": p.get("subtype", ""),
            "latitude:float": p.get("latitude", ""),
            "longitude:float": p.get("longitude", ""),
            "street:string": p.get("street", ""),
            "address:string": p.get("address", ""),
            "operator:string": p.get("operator", ""),
            "url:string": p.get("url", ""),
            ":LABEL": ";".join(["Amenity"] + sublabels),
        })
    write_csv(graph_dir / "graph_nodes_amenity.csv", amen_nodes, amen_header)

    # 3. graph_geo_cluster_nodes.csv + graph_listing_in_cluster_edges.csv
    cluster_coords: dict[str, list[tuple[float, float]]] = collections.defaultdict(list)
    in_cluster_edges: list[dict[str, object]] = []
    for r in listings:
        cid = clean(r.get("geo_cluster_150m"))
        if cid and clean(r.get("latitude")) and clean(r.get("longitude")):
            cluster_coords[cid].append((float(r["latitude"]), float(r["longitude"])))
            in_cluster_edges.append({
                ":START_ID(Listing)": r["listing_id"],
                ":END_ID(GeoCluster)": cid,
                ":TYPE": "IN_CLUSTER",
            })

    cluster_nodes: list[dict[str, object]] = []
    for cid in sorted(cluster_coords):
        pts = cluster_coords[cid]
        c_lat = sum(p[0] for p in pts) / len(pts)
        c_lon = sum(p[1] for p in pts) / len(pts)
        cluster_nodes.append({
            "cluster_id:ID(GeoCluster)": cid,
            "centroid_latitude:float": round(c_lat, 7),
            "centroid_longitude:float": round(c_lon, 7),
            "listing_count:int": len(pts),
            ":LABEL": "GeoCluster",
        })
    write_csv(
        graph_dir / "graph_nodes_geo_cluster.csv",
        cluster_nodes,
        ["cluster_id:ID(GeoCluster)", "centroid_latitude:float", "centroid_longitude:float", "listing_count:int", ":LABEL"],
    )
    write_csv(
        graph_dir / "graph_edges_listing_in_cluster.csv",
        in_cluster_edges,
        [":START_ID(Listing)", ":END_ID(GeoCluster)", ":TYPE"],
    )

    # 4. graph_ward_nodes.csv + graph_listing_in_ward_edges.csv
    ward_nodes_map: dict[str, dict[str, object]] = {}
    in_ward_edges: list[dict[str, object]] = []
    for r in listings:
        ward_name = clean(r.get("district"))
        city = clean(r.get("city_province"))
        if not ward_name:
            continue
        wid = f"ward_{slug(ward_name)}"
        if wid not in ward_nodes_map:
            ward_nodes_map[wid] = {
                "ward_id:ID(Ward)": wid,
                "name:string": ward_name,
                "normalized_name:string": norm(ward_name),
                "city_province:string": city,
                ":LABEL": "Ward",
            }
        in_ward_edges.append({
            ":START_ID(Listing)": r["listing_id"],
            ":END_ID(Ward)": wid,
            ":TYPE": "IN_WARD",
        })

    ward_nodes = [ward_nodes_map[k] for k in sorted(ward_nodes_map)]
    write_csv(
        graph_dir / "graph_nodes_ward.csv",
        ward_nodes,
        ["ward_id:ID(Ward)", "name:string", "normalized_name:string", "city_province:string", ":LABEL"],
    )
    write_csv(
        graph_dir / "graph_edges_listing_in_ward.csv",
        in_ward_edges,
        [":START_ID(Listing)", ":END_ID(Ward)", ":TYPE"],
    )

    # 5. graph_listing_near_amenity_edges.csv
    near_header = [
        ":START_ID(Listing)",
        ":END_ID(Amenity)",
        "category:string",
        "rank:int",
        "driving_rank:int",
        "is_nearest:boolean",
        "straight_line_km:float",
        "driving_distance_km:float",
        "driving_duration_min:float",
        "listing_road_offset_m:float",
        "poi_road_offset_m:float",
        "route_status:string",
        ":TYPE",
    ]
    near_edges: list[dict[str, object]] = []
    for e in routes:
        near_edges.append({
            ":START_ID(Listing)": e["listing_id"],
            ":END_ID(Amenity)": e["osm_id"],
            "category:string": e["category"],
            "rank:int": e["rank"],
            "driving_rank:int": e.get("driving_rank", ""),
            "is_nearest:boolean": "true" if e.get("driving_rank") == "1" else "false",
            "straight_line_km:float": e.get("straight_line_km", ""),
            "driving_distance_km:float": e.get("driving_distance_km", ""),
            "driving_duration_min:float": e.get("driving_duration_min", ""),
            "listing_road_offset_m:float": e.get("listing_road_offset_m", ""),
            "poi_road_offset_m:float": e.get("poi_road_offset_m", ""),
            "route_status:string": e.get("route_status", ""),
            ":TYPE": "NEAR_AMENITY",
        })
    write_csv(graph_dir / "graph_edges_listing_near_amenity.csv", near_edges, near_header)

    return {
        "listing_nodes": len(listing_nodes),
        "amenity_nodes": len(amen_nodes),
        "geo_cluster_nodes": len(cluster_nodes),
        "ward_nodes": len(ward_nodes),
        "near_amenity_edges": len(near_edges),
        "in_cluster_edges": len(in_cluster_edges),
        "in_ward_edges": len(in_ward_edges),
    }


def validate_inputs(
    listings: list[dict[str, str]],
    pois: list[dict[str, str]],
    routes: list[dict[str, str]],
) -> None:
    listing_ids = [r["listing_id"] for r in listings]
    poi_ids = [p["osm_id"] for p in pois]
    assert len(listing_ids) == len(set(listing_ids)), "Duplicate listing_id found!"
    assert len(poi_ids) == len(set(poi_ids)), "Duplicate osm_id found!"

    lid_set = set(listing_ids)
    pid_set = set(poi_ids)
    for e in routes:
        assert e["listing_id"] in lid_set, f"Missing listing_id: {e['listing_id']}"
        assert e["osm_id"] in pid_set, f"Missing osm_id: {e['osm_id']}"


def main() -> None:
    parser = argparse.ArgumentParser(description="Build Gold SQL & Graph datasets.")
    parser.add_argument("--listings", type=Path, default=DEFAULT_LISTINGS)
    parser.add_argument("--pois", type=Path, default=DEFAULT_POIS)
    parser.add_argument("--routes", type=Path, default=DEFAULT_ROUTES)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_GOLD_DIR)
    parser.add_argument("--target", choices=["all", "sql", "graph"], default="all")
    args = parser.parse_args()

    listings, listing_fields = read_csv(args.listings)
    pois, _ = read_csv(args.pois)
    routes, _ = read_csv(args.routes)

    validate_inputs(listings, pois, routes)

    road_offset_by_lid: dict[str, str] = {}
    for e in routes:
        lid = e["listing_id"]
        if lid not in road_offset_by_lid and clean(e.get("listing_road_offset_m")):
            road_offset_by_lid[lid] = e["listing_road_offset_m"]

    if args.target in ("all", "sql"):
        sql_stats = export_sql(args.output_dir, listings, listing_fields, pois, routes, road_offset_by_lid)
        print("SQL exported:", sql_stats, "->", args.output_dir / "sql")

    if args.target in ("all", "graph"):
        graph_stats = export_graph(args.output_dir, listings, listing_fields, pois, routes, road_offset_by_lid)
        print("Graph exported:", graph_stats, "->", args.output_dir / "graph")

    print("Validation: OK (unique PKs & 100% FK/endpoint integrity verified).")


if __name__ == "__main__":
    main()

"""Cypher that adds a posted listing to the Neo4j search graph, in the catalog import's model.

The graph search (src/search/graph_repository.py) matches listings by their properties and by
their IN_WARD, IN_FORMER_AREA, ON_STREET, IN_CLUSTER and NEAR_AMENITY relationships; the node and
relationship shapes here follow backend/scripts/neo4j/import_search_graph.cypher so a posted
listing is found exactly like an imported one. Ids follow the same conventions:
"ward_phuong_cat_lai", "faa_quan_2", "street_<12 hex>", amenity = OpenStreetMap id. Location
cells ("gc_<row>_<column>") are a fixed grid of about 150 m, in place of the offline clustering.
Writing a listing again first removes its own relationships, so coordinates found later replace
the earlier amenities.
"""

from __future__ import annotations

import hashlib
import math
import re
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Sequence, Tuple

from src.services.listing_enrichment.amenities import NearbyAmenity
from src.services.listing_submission.coercion import plain

CITY = "Hồ Chí Minh"
# About 150 m in both directions at Ho Chi Minh City's latitude (1° latitude ≈ 111 km).
CELL_DEGREES = 0.00135
FORMER_AREA_TYPES = ("Thành phố", "Thị xã", "Quận", "Huyện")

# The catalog import's constraints and indexes, created once per process since no import runs.
SCHEMA_STATEMENTS = [
    "CREATE CONSTRAINT listing_node_id_unique IF NOT EXISTS FOR (n:Listing) REQUIRE n.listing_node_id IS UNIQUE",
    "CREATE CONSTRAINT amenity_id_unique IF NOT EXISTS FOR (n:Amenity) REQUIRE n.amenity_id IS UNIQUE",
    "CREATE CONSTRAINT ward_id_unique IF NOT EXISTS FOR (n:Ward) REQUIRE n.ward_id IS UNIQUE",
    "CREATE CONSTRAINT street_id_unique IF NOT EXISTS FOR (n:Street) REQUIRE n.street_id IS UNIQUE",
    "CREATE CONSTRAINT geo_cluster_id_unique IF NOT EXISTS FOR (n:GeoCluster) REQUIRE n.cluster_id IS UNIQUE",
    "CREATE CONSTRAINT former_area_id_unique IF NOT EXISTS FOR (n:FormerAdminArea) REQUIRE n.former_area_id IS UNIQUE",
    "CREATE INDEX listing_id_idx IF NOT EXISTS FOR (n:Listing) ON (n.listing_id)",
    "CREATE INDEX listing_property_type_idx IF NOT EXISTS FOR (n:Listing) ON (n.property_type)",
    "CREATE INDEX listing_price_range_idx IF NOT EXISTS FOR (n:Listing) ON (n.price_range)",
    "CREATE INDEX listing_area_idx IF NOT EXISTS FOR (n:Listing) ON (n.area)",
    "CREATE INDEX listing_bedrooms_idx IF NOT EXISTS FOR (n:Listing) ON (n.bedrooms)",
    "CREATE INDEX listing_posted_date_idx IF NOT EXISTS FOR (n:Listing) ON (n.posted_date)",
    "CREATE INDEX amenity_category_idx IF NOT EXISTS FOR (n:Amenity) ON (n.category)",
    "CREATE INDEX ward_name_idx IF NOT EXISTS FOR (n:Ward) ON (n.name)",
    "CREATE INDEX street_name_idx IF NOT EXISTS FOR (n:Street) ON (n.name)",
    "CREATE POINT INDEX listing_location_idx IF NOT EXISTS FOR (n:Listing) ON (n.location)",
    "CREATE POINT INDEX amenity_location_idx IF NOT EXISTS FOR (n:Amenity) ON (n.location)",
    "CREATE FULLTEXT INDEX listing_text_idx IF NOT EXISTS FOR (n:Listing) ON EACH "
    "[n.title, n.address, n.address_new, n.address_old, n.address_new_normalized, n.address_old_normalized, n.description]",
    "CREATE FULLTEXT INDEX ward_name_fulltext IF NOT EXISTS FOR (n:Ward) ON EACH [n.name, n.normalized_name, n.aliases]",
]

REMOVE_OWN_RELATIONSHIPS = """
MATCH (l:Listing {listing_node_id: $listing_node_id})-[r:NEAR_AMENITY|IN_WARD|IN_FORMER_AREA|ON_STREET|IN_CLUSTER]->()
DELETE r
"""
UPSERT_LISTING = """
MERGE (l:Listing {listing_node_id: $listing_node_id})
SET l += $properties,
    l.posted_date = CASE WHEN $posted_date IS NULL THEN null ELSE date($posted_date) END,
    l.location = CASE WHEN $latitude IS NULL THEN null ELSE point({latitude: $latitude, longitude: $longitude}) END
"""
LINK_WARD = """
MATCH (l:Listing {listing_node_id: $listing_node_id})
MERGE (w:Ward {ward_id: $ward_id})
SET w.name = $ward_name, w.normalized_name = $ward_normalized, w.city_province = $city
MERGE (l)-[:IN_WARD]->(w)
"""
LINK_FORMER_AREA = """
MATCH (l:Listing {listing_node_id: $listing_node_id})
MATCH (w:Ward {ward_id: $ward_id})
MERGE (f:FormerAdminArea {former_area_id: $former_area_id})
SET f.name = $former_name, f.normalized_name = $former_normalized, f.area_type = $former_type
MERGE (l)-[:IN_FORMER_AREA]->(f)
MERGE (w)-[:MAPPED_FROM]->(f)
"""
LINK_STREET = """
MATCH (l:Listing {listing_node_id: $listing_node_id})
MATCH (w:Ward {ward_id: $ward_id})
MERGE (s:Street {street_id: $street_id})
SET s.name = $street_name, s.normalized_name = $street_normalized, s.ward_id = $ward_id, s.city_province = $city
MERGE (l)-[:ON_STREET]->(s)
MERGE (s)-[:IN_WARD]->(w)
"""
LINK_CELL = """
MATCH (l:Listing {listing_node_id: $listing_node_id})
MERGE (g:GeoCluster {cluster_id: $cluster_id})
SET g.centroid_latitude = $centroid_latitude, g.centroid_longitude = $centroid_longitude,
    g.location = point({latitude: $centroid_latitude, longitude: $centroid_longitude})
MERGE (l)-[:IN_CLUSTER]->(g)
WITH g
SET g.listing_count = COUNT { (g)<-[:IN_CLUSTER]-(:Listing) }
"""
LINK_AMENITIES = """
MATCH (l:Listing {listing_node_id: $listing_node_id})
UNWIND $amenities AS item
MERGE (a:Amenity {amenity_id: item.amenity_id})
SET a.name = item.name, a.category = item.category, a.street = item.street,
    a.latitude = item.latitude, a.longitude = item.longitude,
    a.location = point({latitude: item.latitude, longitude: item.longitude})
MERGE (l)-[r:NEAR_AMENITY]->(a)
SET r.category = item.category, r.rank = item.rank, r.is_nearest = item.rank = 1,
    r.straight_line_km = item.straight_line_km, r.driving_distance_km = item.driving_distance_km,
    r.driving_duration_min = item.driving_duration_min, r.threshold_km = item.threshold_km,
    r.within_threshold = item.within_threshold, r.route_status = 'estimated'
"""


def slug(text: str) -> str:
    """Accent-free id part: "Phường Cát Lái" -> "phuong_cat_lai"."""
    return re.sub(r"[^a-z0-9]+", "_", plain(text)).strip("_")


def former_area_type(name: str) -> str:
    """The kind of a former district from its name: "Quận", "Huyện", "Thành phố" or "Thị xã"."""
    return next((kind for kind in FORMER_AREA_TYPES if name.startswith(kind)), "")


def location_cell(latitude: float, longitude: float) -> Tuple[str, float, float]:
    """The ~150 m grid cell of a point.

    Returns:
        tuple: (cluster id "gc_<row>_<column>", cell centre latitude, cell centre longitude).
    """
    row, column = math.floor(latitude / CELL_DEGREES), math.floor(longitude / CELL_DEGREES)
    return f"gc_{row}_{column}", round((row + 0.5) * CELL_DEGREES, 7), round((column + 0.5) * CELL_DEGREES, 7)


def _number(value: Any) -> Optional[float]:
    """A float, or None for blanks."""
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def listing_statements(row: Dict[str, Any], coordinates: Optional[Tuple[float, float]],
                       amenities: Sequence[NearbyAmenity]) -> List[Tuple[str, Dict[str, Any]]]:
    """The Cypher statements that (re)write one posted listing in the graph.

    Args:
        row: Its properties row: listing_id, listing_type, property_type, title, description,
            address, legal_status, furnishing, district (the ward), former_admin_area, posted_date,
            price_range, area, bedrooms, bathrooms and submission (the validated listing).
        coordinates: (latitude, longitude), or None while the listing has none.
        amenities: Its nearest amenities (empty without coordinates).

    Returns:
        list: (Cypher, parameters) pairs for GraphRepository.write, in order.
    """
    listing_node_id = str(row["listing_id"])
    ward, former = (row.get("district") or "").strip(), (row.get("former_admin_area") or "").strip()
    submission = row.get("submission") or {}
    street = ((submission.get("metadata") or {}).get("street") or "").strip()
    latitude, longitude = coordinates if coordinates else (None, None)
    posted = row.get("posted_date")
    posted_date = posted.isoformat() if isinstance(posted, (date, datetime)) else (str(posted)[:10] if posted else None)
    cluster = location_cell(latitude, longitude) if coordinates else None
    bedrooms, bathrooms = _number(row.get("bedrooms")), _number(row.get("bathrooms"))
    properties = {
        "listing_id": listing_node_id, "listing_type": row.get("listing_type"), "property_type": row.get("property_type"),
        "city_province": CITY, "title": row.get("title"), "description": row.get("description"),
        "address": row.get("address"), "address_new": row.get("address"),
        "address_new_normalized": plain(row.get("address") or ""), "legal_status": row.get("legal_status"),
        "furnishing": row.get("furnishing"), "ward_commune": ward, "former_admin_area": former,
        "geo_cluster_150m": cluster[0] if cluster else None, "price_range": _number(row.get("price_range")),
        "area": _number(row.get("area")), "bedrooms": int(bedrooms) if bedrooms is not None else None,
        "bathrooms": int(bathrooms) if bathrooms is not None else None, "latitude": latitude, "longitude": longitude,
        "source": "user",
    }
    base = {"listing_node_id": listing_node_id}
    statements: List[Tuple[str, Dict[str, Any]]] = [
        (REMOVE_OWN_RELATIONSHIPS, base),
        (UPSERT_LISTING, {**base, "properties": properties, "posted_date": posted_date, "latitude": latitude, "longitude": longitude}),
    ]
    if ward:
        ward_id = f"ward_{slug(ward)}"
        statements.append((LINK_WARD, {**base, "ward_id": ward_id, "ward_name": ward, "ward_normalized": plain(ward), "city": CITY}))
        if former:
            statements.append((LINK_FORMER_AREA, {**base, "ward_id": ward_id, "former_area_id": f"faa_{slug(former)}",
                                                  "former_name": former, "former_normalized": plain(former),
                                                  "former_type": former_area_type(former)}))
        if street:
            street_id = "street_" + hashlib.sha1(f"{plain(street)}|{ward_id}".encode("utf-8")).hexdigest()[:12]
            statements.append((LINK_STREET, {**base, "ward_id": ward_id, "street_id": street_id, "street_name": street,
                                             "street_normalized": plain(street), "city": CITY}))
    if cluster:
        statements.append((LINK_CELL, {**base, "cluster_id": cluster[0], "centroid_latitude": cluster[1],
                                       "centroid_longitude": cluster[2]}))
    if amenities:
        statements.append((LINK_AMENITIES, {**base, "amenities": [
            {"amenity_id": item.amenity_id, "name": item.name, "category": item.category, "street": item.street,
             "latitude": item.latitude, "longitude": item.longitude, "rank": item.rank,
             "straight_line_km": item.straight_line_km, "driving_distance_km": item.driving_distance_km,
             "driving_duration_min": item.driving_duration_min, "threshold_km": item.threshold_km,
             "within_threshold": item.is_within_threshold}
            for item in amenities]}))
    return statements

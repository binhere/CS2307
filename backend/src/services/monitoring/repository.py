"""Reads for the data dashboard: the catalog rows and the record of the last catalog load."""

from __future__ import annotations

from typing import Dict, List, Optional

from sqlalchemy.exc import ProgrammingError

from src.settings.event import postgres_client

# Only what the dashboard measures; the long text columns are reduced to their length.
CATALOG_QUERY = """
    SELECT id, listing_id, title, property_type, district, price_range, area, bedrooms, bathrooms,
           latitude_longitude, legal_status, furnishing, house_direction, posted_date,
           COALESCE(jsonb_array_length(images), 0) AS image_count,
           COALESCE(length(description), 0) AS description_length,
           embedding IS NOT NULL AS has_embedding
    FROM properties
    WHERE is_active = true AND is_deleted = false
"""

CATALOG_IMPORT_QUERY = """
    SELECT seed_version, loaded_at FROM catalog_imports WHERE name = 'catalog'
"""


def fetch_catalog_rows() -> List[Dict]:
    """Every active listing with the columns the dashboard measures.

    Returns:
        list: one dict per listing.

    Raises:
        sqlalchemy.exc.SQLAlchemyError: the database is unavailable; the API answers 503.
    """
    return [dict(row) for row in postgres_client.fetch_mappings(CATALOG_QUERY)]


def fetch_catalog_import() -> Optional[Dict]:
    """When the seed last loaded the catalog, and which seed image it was.

    Returns:
        dict | None: seed_version and loaded_at, or None when no seed has loaded this database
        (a local database filled by build_search_index.py, or one migrated before revision 0006).
    """
    try:
        rows = postgres_client.fetch_mappings(CATALOG_IMPORT_QUERY)
    except ProgrammingError:
        # The table only exists from migration 0006 on.
        return None
    return dict(rows[0]) if rows else None

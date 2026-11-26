"""Enrich posted listings: nearest amenities into PostgreSQL, the listing into the Neo4j graph.

enrich_listing runs in the background right after a listing is saved; the scheduled job runs
every ENRICHMENT_INTERVAL_SECONDS for listings whose coordinates changed since their last
enrichment (most often: the geocoding job found them) or whose graph write failed. A listing
without coordinates still gets its graph node with its ward, former district and street, and no
amenities. A PostgreSQL advisory lock keeps the scheduled job to one backend process at a time.
"""

from __future__ import annotations

import asyncio
import json
from contextlib import suppress
from typing import Any, Dict, List, Optional

from loguru import logger
from sqlalchemy import text
from starlette.concurrency import run_in_threadpool

from src.search.graph_repository import graph_repository
from src.services.listing_enrichment.amenities import NearbyAmenity, amenity_reference
from src.services.listing_enrichment.graph import SCHEMA_STATEMENTS, listing_statements
from src.services.price_training.preparation import parse_coordinates
from src.settings.config import config_value
from src.settings.event import postgres_client

DEFAULT_INTERVAL_SECONDS = 60.0
BATCH_SIZE = 50
# Any constant works as long as every backend process uses the same one.
ADVISORY_LOCK_KEY = 2307_0010

LISTING_COLUMNS = """
    id, listing_id, listing_type, property_type, title, description, address, legal_status, furnishing,
    district, former_admin_area, posted_date, price_range, area, bedrooms, bathrooms, latitude_longitude, submission
"""
SELECT_DUE = f"""
SELECT {LISTING_COLUMNS} FROM properties
WHERE source = 'user' AND is_active = true AND is_deleted = false
  AND enriched_coordinates IS DISTINCT FROM COALESCE(latitude_longitude, '')
ORDER BY id
LIMIT :limit
"""
SELECT_ONE = f"SELECT {LISTING_COLUMNS} FROM properties WHERE id = :id"
INSERT_AMENITY = """
INSERT INTO listing_amenity_distances
    (listing_id, listing_node_id, amenity_id, amenity_name, category, rank, is_nearest,
     straight_line_km, driving_distance_km, driving_duration_min, threshold_km)
VALUES (:listing_id, :listing_node_id, :amenity_id, :amenity_name, :category, :rank, :is_nearest,
        :straight_line_km, :driving_distance_km, :driving_duration_min, :threshold_km)
ON CONFLICT (listing_node_id, amenity_id) DO UPDATE SET
    category = EXCLUDED.category, rank = EXCLUDED.rank, is_nearest = EXCLUDED.is_nearest,
    straight_line_km = EXCLUDED.straight_line_km, driving_distance_km = EXCLUDED.driving_distance_km,
    driving_duration_min = EXCLUDED.driving_duration_min, threshold_km = EXCLUDED.threshold_km
"""

_schema_state = {"is_ready": False}


def store_amenities(listing_id: str, amenities: List[NearbyAmenity]) -> None:
    """Replace a listing's rows in listing_amenity_distances, in one transaction.

    Args:
        listing_id: The listing's listing_id (search joins on it as text).
        amenities: Its nearest amenities; empty removes them.
    """
    rows = [{
        "listing_id": listing_id, "listing_node_id": listing_id, "amenity_id": item.amenity_id, "amenity_name": item.name,
        "category": item.category, "rank": item.rank, "is_nearest": item.rank == 1, "straight_line_km": item.straight_line_km,
        "driving_distance_km": item.driving_distance_km, "driving_duration_min": item.driving_duration_min,
        "threshold_km": item.threshold_km,
    } for item in amenities]
    with postgres_client.engine.begin() as connection:
        connection.execute(text("DELETE FROM listing_amenity_distances WHERE listing_id = :listing_id"), {"listing_id": listing_id})
        if rows:
            connection.execute(text(INSERT_AMENITY), rows)


def ensure_graph_schema() -> bool:
    """Create the graph's constraints and indexes once per process (no catalog import does it now).

    Returns:
        bool: Whether the schema is in place; False when Neo4j is disabled or unreachable.
    """
    if _schema_state["is_ready"]:
        return True
    for statement in SCHEMA_STATEMENTS:
        # Schema changes cannot share a transaction with each other's data, so one each.
        if not graph_repository.write([(statement, {})]):
            return False
    _schema_state["is_ready"] = True
    return True


def enrich_row(row: Dict[str, Any]) -> bool:
    """Enrich one listing row and record it as done when everything was written.

    Args:
        row: A properties row with LISTING_COLUMNS.

    Returns:
        bool: True when done; False when the graph is enabled but unreachable (retried later).
    """
    submission = row.get("submission")
    if isinstance(submission, str):
        row = {**row, "submission": json.loads(submission)}
    listing_id = str(row["listing_id"])
    coordinates = parse_coordinates(row.get("latitude_longitude"))
    amenities = amenity_reference().nearest(*coordinates) if coordinates else []
    store_amenities(listing_id, amenities)
    if graph_repository.enabled:
        if not ensure_graph_schema() or not graph_repository.write(listing_statements(row, coordinates, amenities)):
            logger.warning("Neo4j unavailable; listing {} is added to the graph on a later pass", listing_id)
            return False
    postgres_client.execute_write(
        "UPDATE properties SET enriched_coordinates = :coordinates WHERE id = :id",
        coordinates=row.get("latitude_longitude") or "", id=row["id"])
    return True


def enrich_listing(property_id: int) -> bool:
    """Enrich one listing just saved (a background task of the posting route); failures are logged.

    Args:
        property_id: properties.id of the listing.

    Returns:
        bool: Whether it was enriched now; if not, the scheduled job does it.
    """
    try:
        rows = postgres_client.fetch_mappings(SELECT_ONE, id=property_id)
        return bool(rows) and enrich_row(dict(rows[0]))
    except Exception:  # noqa: BLE001 - the scheduled job retries; posting must not fail because of this
        logger.exception("Enriching listing {} failed", property_id)
        return False


def enrich_due_listings(batch_size: int = BATCH_SIZE) -> Dict[str, int]:
    """Run one pass: enrich listings never enriched or whose coordinates changed since.

    Args:
        batch_size: Most listings per pass.

    Returns:
        dict: {"enriched", "postponed", "failed"} counts.
    """
    counts = {"enriched": 0, "postponed": 0, "failed": 0}
    for row in postgres_client.fetch_mappings(SELECT_DUE, limit=batch_size):
        try:
            counts["enriched" if enrich_row(dict(row)) else "postponed"] += 1
        except Exception:  # noqa: BLE001 - one bad listing must not stop the others
            logger.exception("Enriching listing {} failed", row["id"])
            counts["failed"] += 1
        if counts["postponed"]:
            break  # Neo4j is down: the rest would only wait on it too.
    return counts


def run_locked_pass() -> Optional[Dict[str, int]]:
    """Run one pass unless another backend process is running one.

    Returns:
        dict | None: The pass's counts, or None when another process holds the lock.
    """
    with postgres_client.engine.connect() as pooled_connection:
        # Session lock, autocommit: no transaction stays open while the pass works.
        connection = pooled_connection.execution_options(isolation_level="AUTOCOMMIT")
        if not connection.execute(text("SELECT pg_try_advisory_lock(:key)"), {"key": ADVISORY_LOCK_KEY}).scalar():
            return None
        try:
            return enrich_due_listings()
        finally:
            connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": ADVISORY_LOCK_KEY})


def configured_interval_seconds() -> float:
    """Seconds between passes, from ENRICHMENT_INTERVAL_SECONDS; 0 or less turns the job off."""
    value = config_value("ENRICHMENT_INTERVAL_SECONDS", "")
    if value in (None, ""):
        return DEFAULT_INTERVAL_SECONDS
    try:
        return float(value)
    except ValueError:
        logger.warning("ENRICHMENT_INTERVAL_SECONDS={!r} is not a number; using {} s", value, DEFAULT_INTERVAL_SECONDS)
        return DEFAULT_INTERVAL_SECONDS


async def enrich_periodically(interval_seconds: float) -> None:
    """Wait an interval, run a pass, repeat until cancelled; a failed pass is logged and retried.

    Args:
        interval_seconds: Wait before each pass, including the first.
    """
    while True:
        await asyncio.sleep(interval_seconds)
        try:
            counts = await run_in_threadpool(run_locked_pass)
            if counts and any(counts.values()):
                logger.info("Enriched posted listings: {}", counts)
        except Exception:
            logger.exception("Enriching posted listings failed")


class ScheduledEnrichment:
    """Starts and stops the enrichment job with the application."""

    def __init__(self):
        self._task: Optional[asyncio.Task] = None

    def start(self) -> None:
        """Start the job on the running event loop, unless it is off or already running."""
        interval_seconds = configured_interval_seconds()
        if interval_seconds <= 0 or self._task is not None:
            return
        self._task = asyncio.get_running_loop().create_task(enrich_periodically(interval_seconds))

    async def stop(self) -> None:
        """Cancel the job and wait for it to end."""
        if self._task is None:
            return
        self._task.cancel()
        with suppress(asyncio.CancelledError):
            await self._task
        self._task = None


scheduled_enrichment = ScheduledEnrichment()

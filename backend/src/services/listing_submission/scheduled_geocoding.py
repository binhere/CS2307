"""Fill in the coordinates of posted listings that came without them, on a schedule.

A zip upload holds many listings, usually with a street but no coordinates. Looking each one up
while posting would hold the upload a second per listing (the public Nominatim server allows one
request per second), so the backend does it afterwards: every GEOCODING_INTERVAL_SECONDS it takes a
batch of user-posted listings with neither coordinates nor a lookup outcome, asks the geocoder for
their street (then their project), and stores the point when it lies in or near the listing's
ward. The outcome goes to properties.geocoding_status, so an address that cannot be found is not
asked again. A PostgreSQL advisory lock lets one backend process run a pass at a time, so several
replicas still send at most one request per second together. Catalog rows are left alone: the
seed replaces them, which would throw the lookups away.
"""

from __future__ import annotations

import asyncio
import json
from collections import Counter
from contextlib import suppress
from typing import Any, Dict, List, Optional, Protocol, Tuple

import requests
from loguru import logger
from sqlalchemy import text
from starlette.concurrency import run_in_threadpool

from src.services.listing_submission.geography import address_reference
from src.services.listing_submission.geocoding import NominatimGeocoder
from src.settings.config import config_value
from src.settings.event import postgres_client

DEFAULT_INTERVAL_SECONDS = 300.0
BATCH_SIZE = 100
# Any constant works as long as every backend process uses the same one.
ADVISORY_LOCK_KEY = 2307_0008
CITY_NAME = "Thành phố Hồ Chí Minh"

SELECT_LISTINGS_WITHOUT_COORDINATES = """
SELECT id, submission FROM properties
WHERE source = 'user' AND latitude_longitude IS NULL AND geocoding_status IS NULL
ORDER BY id
LIMIT :limit
"""
# COALESCE keeps coordinates that arrived some other way while the lookup was running.
UPDATE_LISTING_COORDINATES = """
UPDATE properties
SET geocoding_status = :status, latitude_longitude = COALESCE(latitude_longitude, :coordinates)
WHERE id = :id
"""


class Geocoder(Protocol):
    """Anything that finds (latitude, longitude) for a free-text address, like NominatimGeocoder."""

    def search(self, query: str) -> Optional[Tuple[float, float]]:
        ...


def address_queries(submission: Dict[str, Any], ward_name: str) -> List[str]:
    """The addresses to try for a listing, best first.

    Args:
        submission: The listing as validated (properties.submission).
        ward_name: Its official ward name.

    Returns:
        list: "street, ward, city", then "project, ward, city"; empty when the listing names neither.
    """
    metadata = submission.get("metadata") or {}
    places: List[str] = []
    for field in ("street", "project"):
        value = metadata.get(field)
        if isinstance(value, str) and value.strip() and value.strip() not in places:
            places.append(value.strip())
    return [f"{place}, {ward_name}, {CITY_NAME}" for place in places]


def locate_listing(submission: Dict[str, Any], geocoder: Geocoder) -> Tuple[str, Optional[Tuple[float, float]]]:
    """Look up one listing's coordinates from its address.

    Args:
        submission: The listing as validated (properties.submission).
        geocoder: Answers address queries.

    Returns:
        tuple: (status, coordinates). status is "found" with (latitude, longitude) rounded to 7
        decimals, or one of "not_found", "outside_ward" (the geocoder answered with a point away
        from the listing's ward, which is ignored), "no_address" (no street or project) and
        "unknown_ward", each with None.

    Raises:
        requests.RequestException: The geocoder could not be reached; nothing is decided.
    """
    reference = address_reference()
    ward = reference.ward_by_name.get(submission.get("ward") or "")
    if ward is None:
        return "unknown_ward", None
    queries = address_queries(submission, ward.name)
    if not queries:
        return "no_address", None
    status = "not_found"
    for query in queries:
        result = geocoder.search(query)
        if result is None:
            continue
        if reference.is_near_ward(ward, *result):
            return "found", (round(result[0], 7), round(result[1], 7))
        status = "outside_ward"
    return status, None


def fill_missing_coordinates(geocoder: Geocoder, batch_size: int = BATCH_SIZE) -> Dict[str, int]:
    """Run one pass: look up a batch of posted listings without coordinates and store the outcomes.

    Args:
        geocoder: Answers address queries.
        batch_size: Most listings to look up in this pass.

    Returns:
        dict: Outcome -> number of listings. "unavailable" counts the listing at which the geocoder
        failed; the pass stops there and that listing and the rest are retried next time.
    """
    rows = postgres_client.fetch_mappings(SELECT_LISTINGS_WITHOUT_COORDINATES, limit=batch_size)
    outcomes: Counter = Counter()
    for row in rows:
        submission = row["submission"]
        if isinstance(submission, str):
            submission = json.loads(submission)
        try:
            status, coordinates = locate_listing(submission or {}, geocoder)
        except requests.RequestException:
            logger.warning("The geocoder is unavailable; listing {} and the rest wait for the next pass", row["id"])
            outcomes["unavailable"] += 1
            break
        postgres_client.execute_write(
            UPDATE_LISTING_COORDINATES, id=row["id"], status=status,
            coordinates=f"{coordinates[0]}, {coordinates[1]}" if coordinates else None)
        outcomes[status] += 1
    return dict(outcomes)


def run_locked_pass(geocoder: Geocoder) -> Optional[Dict[str, int]]:
    """Run one pass unless another backend process is running one.

    Args:
        geocoder: Answers address queries.

    Returns:
        dict | None: The pass's outcomes, or None when another process holds the lock.
    """
    with postgres_client.engine.connect() as pooled_connection:
        # The lock belongs to the session, so no transaction is needed; autocommit keeps this
        # connection from sitting "idle in transaction" while the pass waits on the geocoder. The pool
        # restores the isolation level when the connection goes back.
        connection = pooled_connection.execution_options(isolation_level="AUTOCOMMIT")
        if not connection.execute(text("SELECT pg_try_advisory_lock(:key)"), {"key": ADVISORY_LOCK_KEY}).scalar():
            return None
        try:
            return fill_missing_coordinates(geocoder)
        finally:
            connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": ADVISORY_LOCK_KEY})


def configured_interval_seconds() -> float:
    """Seconds between passes, from GEOCODING_INTERVAL_SECONDS.

    Returns:
        float: The interval; 0 or less turns the job off. An unreadable value gives the default.
    """
    value = config_value("GEOCODING_INTERVAL_SECONDS", "")
    if value in (None, ""):
        return DEFAULT_INTERVAL_SECONDS
    try:
        return float(value)
    except ValueError:
        logger.warning("GEOCODING_INTERVAL_SECONDS={!r} is not a number; using {} s", value, DEFAULT_INTERVAL_SECONDS)
        return DEFAULT_INTERVAL_SECONDS


async def fill_missing_coordinates_periodically(interval_seconds: float, geocoder: Geocoder) -> None:
    """Wait an interval, run a pass, repeat until cancelled. A failed pass is logged and retried.

    Args:
        interval_seconds: Wait before each pass, including the first (startup stays quick).
        geocoder: Answers address queries; kept across passes so its cache and pacing carry over.
    """
    while True:
        await asyncio.sleep(interval_seconds)
        try:
            # The pass waits on the network and the database: keep it off the event loop.
            outcomes = await run_in_threadpool(run_locked_pass, geocoder)
            if outcomes:
                logger.info("Looked up coordinates of posted listings: {}", outcomes)
        except Exception:
            logger.exception("Filling in coordinates of posted listings failed")


class ScheduledGeocoding:
    """Starts and stops the background job with the application."""

    def __init__(self):
        self._task: Optional[asyncio.Task] = None

    def start(self) -> None:
        """Start the job on the running event loop, unless it is off or already running."""
        interval_seconds = configured_interval_seconds()
        if interval_seconds <= 0 or self._task is not None:
            return
        self._task = asyncio.get_running_loop().create_task(
            fill_missing_coordinates_periodically(interval_seconds, NominatimGeocoder()))

    async def stop(self) -> None:
        """Cancel the job and wait for it to end."""
        if self._task is None:
            return
        self._task.cancel()
        with suppress(asyncio.CancelledError):
            await self._task
        self._task = None


scheduled_geocoding = ScheduledGeocoding()

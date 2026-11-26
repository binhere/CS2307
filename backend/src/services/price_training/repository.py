"""PostgreSQL access for training runs: listing rows, the holdout set and the run history.

A run's row is its lock: the partial unique index of migration 0009 refuses a second row with
status 'running', so two backend processes can never train at once.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any, Dict, Iterable, List, Optional

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from src.settings.event import postgres_client

TRAINING_ROWS_QUERY = """
    SELECT listing_id, source, property_type, area, bedrooms, latitude_longitude, district, legal_status, price_range
    FROM properties
    WHERE is_active = true AND is_deleted = false
"""
# Everything the history shows; the model bytes and the full report stay in the database.
RUN_COLUMNS = "id, trigger, requested_by_name, status, reason, started_at, finished_at, summary"


def fetch_training_rows() -> List[Dict[str, Any]]:
    """Every active listing, catalog and posted, with the columns the price model uses."""
    return [dict(row) for row in postgres_client.fetch_mappings(TRAINING_ROWS_QUERY)]


def fetch_holdout_ids() -> List[int]:
    """Listing ids of the fixed holdout set; empty until the first run chooses them.

    A catalog update can remove listings of the set; the run then chooses a new one."""
    return [int(row["listing_id"]) for row in postgres_client.fetch_mappings("SELECT listing_id FROM price_training_holdout")]


def replace_holdout_ids(listing_ids: Iterable[int]) -> None:
    """Make these listings the holdout set, removing the old one in the same transaction.

    Two statements, not one data-modifying CTE: in a single statement the insert still sees the
    rows being deleted, so an id kept from the old set violates the primary key.
    """
    rows = [{"listing_id": int(listing_id)} for listing_id in sorted(listing_ids)]
    with postgres_client.engine.begin() as connection:
        connection.execute(text("DELETE FROM price_training_holdout"))
        if rows:
            connection.execute(text("INSERT INTO price_training_holdout (listing_id) VALUES (:listing_id)"), rows)


def start_run(trigger: str, user_id: Optional[int], user_name: Optional[str]) -> Optional[int]:
    """Record a run that starts now.

    Args:
        trigger: "schedule" or "manual".
        user_id: The user who pressed "Huấn luyện ngay", None for the schedule.
        user_name: That user's name as shown in the history.

    Returns:
        int | None: The run's id, or None when another run is still going.
    """
    try:
        rows = postgres_client.fetch_write(
            "INSERT INTO price_model_runs (trigger, requested_by, requested_by_name, status) "
            "VALUES (:trigger, :user_id, :user_name, 'running') RETURNING id",
            trigger=trigger, user_id=user_id, user_name=user_name)
    except IntegrityError:
        return None
    return int(rows[0]["id"])


def finish_run(run_id: int, status: str, reason: Optional[str], summary: Dict[str, Any],
               report: Optional[Dict[str, Any]] = None, model: Optional[bytes] = None) -> None:
    """Close a run with its outcome.

    Args:
        run_id: The run.
        status: "deployed", "rejected", "skipped" or "failed".
        reason: Why it was not deployed ("not_better", "not_enough_new_data", "too_few_rows",
            "error", "interrupted"), or None.
        summary: Counts, metrics, duration and memory, shown in the history.
        report: The training report, for deployed and rejected runs.
        model: The joblib bundle, for deployed runs only.
    """
    postgres_client.execute_write(
        "UPDATE price_model_runs SET status = :status, reason = :reason, finished_at = now(), "
        "summary = CAST(:summary AS jsonb), report = CAST(:report AS jsonb), model = :model WHERE id = :id",
        id=run_id, status=status, reason=reason, summary=json.dumps(summary, ensure_ascii=False),
        report=json.dumps(report, ensure_ascii=False, separators=(",", ":")) if report is not None else None, model=model)


def fail_stale_runs(older_than_minutes: int) -> int:
    """Mark runs still 'running' after a while as interrupted (their process died mid-run).

    Returns:
        int: How many runs were closed.
    """
    rows = postgres_client.fetch_write(
        "UPDATE price_model_runs SET status = 'failed', reason = 'interrupted', finished_at = now() "
        "WHERE status = 'running' AND started_at < now() - make_interval(mins => :minutes) RETURNING id",
        minutes=older_than_minutes)
    return len(rows)


def has_scheduled_run_since(moment: datetime) -> bool:
    """Whether the schedule already started a run at or after a moment (the day's slot is used)."""
    rows = postgres_client.fetch_mappings(
        "SELECT 1 FROM price_model_runs WHERE trigger = 'schedule' AND started_at >= :moment LIMIT 1", moment=moment)
    return bool(rows)


def active_model_id() -> Optional[int]:
    """The id of the latest deployed run (the model to serve), or None when no run was deployed."""
    rows = postgres_client.fetch_mappings("SELECT max(id) AS id FROM price_model_runs WHERE status = 'deployed'")
    return int(rows[0]["id"]) if rows and rows[0]["id"] is not None else None


def fetch_model(run_id: int) -> Optional[Dict[str, Any]]:
    """A deployed run's model bytes and report: {"model": bytes, "report": dict}, or None."""
    rows = postgres_client.fetch_mappings("SELECT model, report FROM price_model_runs WHERE id = :id AND model IS NOT NULL", id=run_id)
    if not rows:
        return None
    report = rows[0]["report"]
    return {"model": bytes(rows[0]["model"]), "report": json.loads(report) if isinstance(report, str) else report}


def list_runs(limit: int) -> List[Dict[str, Any]]:
    """The latest runs, newest first, without model bytes or reports."""
    rows = postgres_client.fetch_mappings(f"SELECT {RUN_COLUMNS} FROM price_model_runs ORDER BY id DESC LIMIT :limit", limit=limit)
    return [_run_dict(row) for row in rows]


def _run_dict(row: Any) -> Dict[str, Any]:
    """A run row as JSON-ready data (timestamps as ISO text)."""
    item = dict(row)
    for key in ("started_at", "finished_at"):
        if isinstance(item.get(key), (datetime, date)):
            item[key] = item[key].isoformat()
    if isinstance(item.get("summary"), str):
        item["summary"] = json.loads(item["summary"])
    return item

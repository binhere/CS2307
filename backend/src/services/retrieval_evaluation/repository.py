"""PostgreSQL persistence for retrieval evaluation runs.

Uses the partial unique index from migration 20261128_0011 on ``status = 'running'``
as a cross-process lock so at most one evaluation runs at a time.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any, Dict, List, Optional

from sqlalchemy.exc import IntegrityError

from src.settings.event import postgres_client

RUN_COLUMNS = (
    "id, retrieval_mode, requested_by_name, status, queries_total, queries_done, "
    "started_at, finished_at, summary, error"
)


def _serialize_run(row: Dict[str, Any]) -> Dict[str, Any]:
    """Convert database row timestamps and JSON columns into JSON-ready primitives."""
    serialized = dict(row)
    for key in ("started_at", "finished_at"):
        value = serialized.get(key)
        if isinstance(value, (datetime, date)):
            serialized[key] = value.isoformat()
    if isinstance(serialized.get("summary"), str):
        try:
            serialized["summary"] = json.loads(serialized["summary"])
        except json.JSONDecodeError:
            serialized["summary"] = None
    return serialized


def fail_stale_runs(stale_minutes: int) -> None:
    """Mark runs stuck in 'running' beyond ``stale_minutes`` as failed after a crash."""
    postgres_client.execute_write(
        "UPDATE retrieval_eval_runs "
        "SET status = 'failed', finished_at = now(), error = 'Quá trình đánh giá bị gián đoạn trước khi hoàn tất.' "
        "WHERE status = 'running' AND started_at < now() - make_interval(mins => :stale_minutes)",
        stale_minutes=stale_minutes,
    )


def start_run(
    retrieval_mode: str,
    queries_total: int,
    user_id: Optional[int],
    user_name: Optional[str],
) -> Optional[int]:
    """Insert a new running evaluation row, returning its id or None when busy.

    Args:
        retrieval_mode: Mode label ('fts', 'vector', 'hybrid', 'hybrid_graph', or 'all').
        queries_total: Total query executions planned for this run.
        user_id: Authenticated user id who started the run.
        user_name: Display name shown in the dashboard history.

    Returns:
        Inserted run id, or None if another run is currently in progress.
    """
    try:
        rows = postgres_client.fetch_write(
            "INSERT INTO retrieval_eval_runs "
            "(retrieval_mode, requested_by, requested_by_name, status, queries_total, queries_done) "
            "VALUES (:retrieval_mode, :user_id, :user_name, 'running', :queries_total, 0) "
            "RETURNING id",
            retrieval_mode=retrieval_mode,
            user_id=user_id,
            user_name=user_name,
            queries_total=queries_total,
        )
    except IntegrityError:
        return None
    return int(rows[0]["id"])


def update_progress(run_id: int, queries_done: int, summary: Optional[Dict[str, Any]] = None) -> None:
    """Update live progress counter (and optional partial summary) while a run is executing."""
    postgres_client.execute_write(
        "UPDATE retrieval_eval_runs SET queries_done = :queries_done, "
        "summary = COALESCE(CAST(:summary AS jsonb), summary) WHERE id = :id",
        id=run_id,
        queries_done=queries_done,
        summary=json.dumps(summary, ensure_ascii=False) if summary is not None else None,
    )


def finish_run(
    run_id: int,
    status: str,
    queries_done: int,
    summary: Optional[Dict[str, Any]] = None,
    error: Optional[str] = None,
) -> None:
    """Record the final outcome of an evaluation run.

    Args:
        run_id: Evaluation run id.
        status: 'completed' or 'failed'.
        queries_done: Number of query executions completed.
        summary: Aggregated per-mode evaluation metrics JSON.
        error: Human-readable error message when failed, otherwise None.
    """
    postgres_client.execute_write(
        "UPDATE retrieval_eval_runs SET status = :status, queries_done = :queries_done, "
        "finished_at = now(), summary = CAST(:summary AS jsonb), error = :error WHERE id = :id",
        id=run_id,
        status=status,
        queries_done=queries_done,
        summary=json.dumps(summary, ensure_ascii=False) if summary is not None else None,
        error=error,
    )


def list_runs(limit: int = 20) -> List[Dict[str, Any]]:
    """Return the latest retrieval evaluation runs, newest first.

    Args:
        limit: Maximum number of rows to return.

    Returns:
        List of serialized run dictionaries.
    """
    rows = postgres_client.fetch_mappings(
        f"SELECT {RUN_COLUMNS} FROM retrieval_eval_runs ORDER BY id DESC LIMIT :limit",
        limit=limit,
    )
    return [_serialize_run(dict(row)) for row in rows]

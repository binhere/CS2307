"""Run a price model training, decide whether its model is served, and run it every day.

A run: prepare every listing (preparation.py), set the fixed holdout set aside, fit the three
candidates on the rest (trainer.py), then score the new model and the one being served on the
holdout set. The new model is deployed only when its R² on log price is higher; otherwise the
run is recorded as rejected. Every outcome, with its counts, metrics, duration and memory, goes
to price_model_runs for the "Lịch sử huấn luyện" tab.

The schedule checks every PRICE_TRAINING_CHECK_SECONDS whether today's 02:00 (Vietnam time) run
has happened, and runs it when not; that run trains only when at least 10% of the training rows
are new to the served model. "Huấn luyện ngay" always trains. Training runs inside the API process
(scikit-learn on a few thousand rows takes seconds) on a worker thread.
"""

from __future__ import annotations

import asyncio
import io
import resource
import sys
import time
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, Optional, Set

import joblib
import pandas as pd
from loguru import logger
from starlette.concurrency import run_in_threadpool
from threadpoolctl import threadpool_limits

from src.services.price_training import repository
from src.services.price_training.preparation import PreparedData, log_price, prepare_training_data
from src.services.price_training.trainer import TRAINING_JOBS, build_report, fit_candidates, predict_log, regression_metrics
from src.settings.config import config_value

VIETNAM = timezone(timedelta(hours=7))
SCHEDULE_HOUR = 2
NEW_DATA_SHARE = 0.10
DEFAULT_CHECK_SECONDS = 3600.0
STARTUP_DELAY_SECONDS = 300.0
# A run takes about a minute; one still "running" after this died with its process.
STALE_RUN_MINUTES = 30
MINIMUM_TRAINING_ROWS = 200
MINIMUM_HOLDOUT_ROWS = 50
HOLDOUT_SHARE = 0.15
HOLDOUT_RANDOM_STATE = 42


@dataclass(frozen=True)
class ServedModel:
    """The model the backend serves.

    Attributes:
        bundle: model_name, model, feature_names and, for models trained here, imputation.
        report: Its training report, or None when the image ships none.
        run_id: The price_model_runs id it came from, or None for the model shipped in the image.
    """

    bundle: Dict[str, Any]
    report: Optional[Dict[str, Any]]
    run_id: Optional[int]


def peak_memory_mb() -> float:
    """The process's peak resident memory so far, in MB (ru_maxrss is KB on Linux, bytes on macOS)."""
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return round(peak / (1024 * 1024) if sys.platform == "darwin" else peak / 1024, 1)


def seen_listing_ids(report: Optional[Dict[str, Any]]) -> Set[int]:
    """Listings a model was fitted or validated on: its report's seen_listing_ids, else its training plus test ids."""
    if not report:
        return set()
    if report.get("seen_listing_ids"):
        return set(report["seen_listing_ids"])
    return set(report.get("train_listing_ids", [])) | {row[0] for row in report.get("test_predictions", [])}


def choose_holdout(frame: pd.DataFrame, report: Optional[Dict[str, Any]]) -> Set[int]:
    """Pick the fixed holdout set on the first run.

    Complete catalog rows (nothing filled in) that the served model was tested on and not
    trained on, so the first comparison is fair; when too few remain, a random 15% of the complete
    catalog rows it was not trained on.

    Args:
        frame: Prepared rows.
        report: The served model's report.

    Returns:
        set: Listing ids.
    """
    complete = frame[(frame["source"] == "catalog") & ~frame["is_bedrooms_imputed"] & ~frame["is_coordinates_imputed"]]
    trained = set(report.get("train_listing_ids", [])) if report else set()
    tested = {row[0] for row in report.get("test_predictions", [])} if report else set()
    chosen = set(complete["listing_id"]) & tested
    if len(chosen) < MINIMUM_HOLDOUT_ROWS:
        candidates = complete[~complete["listing_id"].isin(trained)]
        count = max(MINIMUM_HOLDOUT_ROWS, int(round(len(complete) * HOLDOUT_SHARE)))
        chosen = set(candidates["listing_id"].sample(n=min(count, len(candidates)), random_state=HOLDOUT_RANDOM_STATE))
    return {int(listing_id) for listing_id in chosen}


def split_holdout(prepared: PreparedData, holdout_ids: Set[int]) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(holdout rows, training pool): holdout listings with nothing filled in, and every other listing."""
    frame = prepared.frame
    in_holdout = frame["listing_id"].isin(holdout_ids)
    is_complete = ~frame["is_bedrooms_imputed"] & ~frame["is_coordinates_imputed"]
    return frame[in_holdout & is_complete], frame[~in_holdout]


def execute_run(run_id: int, trigger: str, served: ServedModel) -> str:
    """Carry out a started run and record its outcome.

    Args:
        run_id: The run from repository.start_run.
        trigger: "schedule" (trains only with enough new data) or "manual" (always trains).
        served: The model being served, the champion to beat.

    Returns:
        str: The run's final status: "deployed", "rejected", "skipped" or "failed".
    """
    started = time.monotonic()
    summary: Dict[str, Any] = {}
    try:
        prepared = prepare_training_data(repository.fetch_training_rows())
        holdout, pool = split_holdout(prepared, set(repository.fetch_holdout_ids()))
        # First run, or a catalog update removed listings of the set: choose it (again).
        if len(holdout) < MINIMUM_HOLDOUT_ROWS:
            holdout_ids = choose_holdout(prepared.frame, served.report)
            repository.replace_holdout_ids(holdout_ids)
            holdout, pool = split_holdout(prepared, holdout_ids)
            summary["is_holdout_new"] = True
        new_rows = int((~pool["listing_id"].isin(seen_listing_ids(served.report))).sum())
        summary.update({
            "input_rows": prepared.step_rows[0], "step_rows": prepared.step_rows, "dropped": dict(prepared.dropped),
            "pool_rows": int(len(pool)), "new_rows": new_rows, "holdout_rows": int(len(holdout)),
            "imputed_bedrooms": int(pool["is_bedrooms_imputed"].sum()),
            "imputed_coordinates": int(pool["is_coordinates_imputed"].sum()),
            "by_source": {str(key): int(value) for key, value in pool["source"].value_counts().items()},
            "champion_run_id": served.run_id,
        })
        if trigger == "schedule" and new_rows < NEW_DATA_SHARE * len(pool):
            return _finish(run_id, "skipped", "not_enough_new_data", summary, started)
        if len(pool) < MINIMUM_TRAINING_ROWS or len(holdout) < MINIMUM_HOLDOUT_ROWS:
            return _finish(run_id, "failed", "too_few_rows", summary, started)

        model_name, model, feature_names, benchmark, training = fit_candidates(pool)
        holdout_true = log_price(holdout).to_numpy()
        # Scoring also runs OpenMP code (HistGradientBoosting): keep it within the training threads.
        with threadpool_limits(limits=TRAINING_JOBS):
            challenger = regression_metrics(holdout_true, predict_log({"model": model, "feature_names": feature_names}, holdout))
            champion = regression_metrics(holdout_true, predict_log(served.bundle, holdout))
        bundle = {
            "model_name": model_name, "model": model, "feature_names": feature_names, "imputation": prepared.imputation,
            "metrics_overall": challenger, "trained_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        report = build_report(bundle, training, holdout, prepared.frame, prepared.step_rows, benchmark)
        report["seen_listing_ids"] = sorted(int(value) for value in pool["listing_id"])
        summary.update({"model_name": model_name, "training_rows": int(len(training)), "challenger": challenger,
                        "champion": champion, "champion_name": served.bundle.get("model_name")})
        if challenger["r2"] > champion["r2"]:
            buffer = io.BytesIO()
            joblib.dump(bundle, buffer)
            return _finish(run_id, "deployed", None, summary, started, report, buffer.getvalue())
        return _finish(run_id, "rejected", "not_better", summary, started, report)
    except Exception as error:  # noqa: BLE001 - every failure must close the run, or it blocks the next one
        logger.exception("Price model training run {} failed", run_id)
        summary["error"] = type(error).__name__
        return _finish(run_id, "failed", "error", summary, started)


def _finish(run_id: int, status: str, reason: Optional[str], summary: Dict[str, Any], started: float,
            report: Optional[Dict[str, Any]] = None, model: Optional[bytes] = None) -> str:
    """Record a run's outcome with its duration and memory peak, and return its status."""
    summary.update({"duration_seconds": round(time.monotonic() - started, 1), "peak_memory_mb": peak_memory_mb()})
    repository.finish_run(run_id, status, reason, summary, report, model)
    logger.info("Price model training run {} ended: {} {}", run_id, status, reason or "")
    return status


def latest_slot(now: datetime) -> datetime:
    """The most recent 02:00 in Vietnam at or before a moment: the scheduled run it is due for."""
    local = now.astimezone(VIETNAM)
    slot = local.replace(hour=SCHEDULE_HOUR, minute=0, second=0, microsecond=0)
    return slot if local >= slot else slot - timedelta(days=1)


def run_scheduled_if_due(served_model: Callable[[], ServedModel], now: Optional[datetime] = None) -> Optional[str]:
    """Run the day's scheduled training unless it already ran or another run is going.

    Args:
        served_model: Returns the ServedModel to beat (called only when a run starts).
        now: The current moment (tests pass one).

    Returns:
        str | None: The run's status, or None when nothing ran.
    """
    repository.fail_stale_runs(STALE_RUN_MINUTES)
    if repository.has_scheduled_run_since(latest_slot(now or datetime.now(timezone.utc))):
        return None
    run_id = repository.start_run("schedule", None, None)
    if run_id is None:
        return None
    return execute_run(run_id, "schedule", served_model())


def start_manual_run(user: Dict[str, Any]) -> Optional[int]:
    """Start a "Huấn luyện ngay" run for a signed-in user.

    Args:
        user: The users row of the person who pressed the button.

    Returns:
        int | None: The run's id, or None when a run is already going (the API answers "busy").
    """
    repository.fail_stale_runs(STALE_RUN_MINUTES)
    return repository.start_run("manual", int(user["id"]), user.get("display_name") or user.get("username"))


def configured_check_seconds() -> float:
    """Seconds between schedule checks, from PRICE_TRAINING_CHECK_SECONDS; 0 or less turns the schedule off."""
    value = config_value("PRICE_TRAINING_CHECK_SECONDS", "")
    if value in (None, ""):
        return DEFAULT_CHECK_SECONDS
    try:
        return float(value)
    except ValueError:
        logger.warning("PRICE_TRAINING_CHECK_SECONDS={!r} is not a number; using {} s", value, DEFAULT_CHECK_SECONDS)
        return DEFAULT_CHECK_SECONDS


async def check_schedule_periodically(check_seconds: float, first_delay_seconds: float,
                                      served_model: Callable[[], ServedModel]) -> None:
    """Check the schedule after a delay, then every check_seconds, until cancelled; failures are logged.

    Args:
        check_seconds: Seconds between checks.
        first_delay_seconds: Wait before the first check, so startup stays quick.
        served_model: Returns the ServedModel to beat.
    """
    await asyncio.sleep(first_delay_seconds)
    while True:
        try:
            await run_in_threadpool(run_scheduled_if_due, served_model)
        except Exception:
            logger.exception("Checking the price model training schedule failed")
        await asyncio.sleep(check_seconds)


class ScheduledTraining:
    """Starts and stops the schedule with the application."""

    def __init__(self):
        self._task: Optional[asyncio.Task] = None

    def start(self, served_model: Callable[[], ServedModel]) -> None:
        """Start checking on the running event loop, unless the schedule is off or already running.

        Args:
            served_model: Returns the ServedModel to beat.
        """
        check_seconds = configured_check_seconds()
        if check_seconds <= 0 or self._task is not None:
            return
        self._task = asyncio.get_running_loop().create_task(
            check_schedule_periodically(check_seconds, min(STARTUP_DELAY_SECONDS, check_seconds), served_model))

    async def stop(self) -> None:
        """Cancel the schedule and wait for it to end."""
        if self._task is None:
            return
        self._task.cancel()
        with suppress(asyncio.CancelledError):
            await self._task
        self._task = None


scheduled_training = ScheduledTraining()

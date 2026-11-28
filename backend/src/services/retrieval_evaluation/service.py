"""Execute in-process retrieval evaluation across selectable RetrievalMode strategies.

Runs the bundled 150-query benchmark directly against ``hybrid_search_service.search``
inside the backend process (no Cloudflare round-trip, no LLM answer calls) and persists
live progress and final summaries to ``retrieval_eval_runs`` for the /du-lieu dashboard.
"""

from __future__ import annotations

import json
import time
from collections import defaultdict
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger
from starlette.concurrency import run_in_threadpool

from src.search.schemas import RetrievalMode, SearchRequest
from src.search.service import hybrid_search_service
from src.services.retrieval_evaluation import repository
from src.services.retrieval_evaluation.metrics import constraint_match_at_k, percentile

QUERIES_PATH = Path(__file__).resolve().parent / "queries.json"
ALL_MODES: Tuple[RetrievalMode, ...] = (
    RetrievalMode.FTS,
    RetrievalMode.VECTOR,
    RetrievalMode.HYBRID,
    RetrievalMode.HYBRID_GRAPH,
)
VALID_REQUEST_MODES = ("all", *(mode.value for mode in ALL_MODES))
DEFAULT_TOP_K = 10
STALE_RUN_MINUTES = 30
PROGRESS_UPDATE_STEP = 10


@lru_cache(maxsize=1)
def load_queries() -> List[Dict[str, Any]]:
    """Load and cache the bundled 150-query benchmark specification.

    Returns:
        List of query dictionaries with ``id``, ``category``, ``query``, and ``expected_constraints``.
    """
    return json.loads(QUERIES_PATH.read_text(encoding="utf-8"))


def resolve_modes(mode_name: str) -> List[RetrievalMode]:
    """Expand a requested mode string into the ordered list of RetrievalMode enum values.

    Args:
        mode_name: 'all', 'fts', 'vector', 'hybrid', or 'hybrid_graph'.

    Returns:
        Ordered list of ``RetrievalMode`` values to evaluate.

    Raises:
        ValueError: When ``mode_name`` is not supported.
    """
    if mode_name == "all":
        return list(ALL_MODES)
    return [RetrievalMode(mode_name)]


def _mean_or_none(values: List[float]) -> Optional[float]:
    if not values:
        return None
    return round(sum(values) / len(values), 4)


def summarize_mode_records(
    mode: RetrievalMode,
    records: List[Dict[str, Any]],
    top_k: int,
) -> Dict[str, Any]:
    """Aggregate per-query outcomes for one retrieval mode into dashboard metrics.

    Args:
        mode: Retrieval strategy evaluated.
        records: Per-query result dictionaries.
        top_k: Maximum cutoff evaluated.

    Returns:
        Summary dictionary for one mode.
    """
    succeeded = [record for record in records if record["error"] is None]
    failed = [record for record in records if record["error"] is not None]

    exact_at_5 = [record["exact_at_5"] for record in succeeded if record["exact_at_5"] is not None]
    exact_at_10 = [record["exact_at_10"] for record in succeeded if record["exact_at_10"] is not None]
    latencies = [record["latency_ms"] for record in succeeded if record["latency_ms"] is not None]

    per_constraint: Dict[str, List[float]] = defaultdict(list)
    by_category: Dict[str, List[float]] = defaultdict(list)
    for record in succeeded:
        for key, rate in record["per_constraint_at_10"].items():
            per_constraint[key].append(rate)
        if record["exact_at_10"] is not None:
            by_category[record["category"]].append(record["exact_at_10"])

    denominator = max(len(succeeded), 1)
    zero_results = sum(1 for record in succeeded if record["result_count"] == 0)
    semantic_hits = sum(1 for record in succeeded if record["semantic_enabled"])
    graph_hits = sum(1 for record in succeeded if record["graph_enabled"])

    return {
        "retrieval_mode": mode.value,
        "top_k": top_k,
        "queries_total": len(records),
        "queries_succeeded": len(succeeded),
        "queries_failed": len(failed),
        "zero_result_rate": round(zero_results / denominator, 4) if succeeded else None,
        "semantic_enabled_rate": round(semantic_hits / denominator, 4) if succeeded else None,
        "graph_enabled_rate": round(graph_hits / denominator, 4) if succeeded else None,
        "exact_constraint_match_at_5": _mean_or_none(exact_at_5),
        "exact_constraint_match_at_10": _mean_or_none(exact_at_10),
        "per_constraint_match_at_10": {
            key: _mean_or_none(values) for key, values in sorted(per_constraint.items())
        },
        "exact_constraint_match_at_10_by_category": {
            category: _mean_or_none(values) for category, values in sorted(by_category.items())
        },
        "latency_ms": {
            "p50": round(percentile(latencies, 50), 2) if latencies else None,
            "p95": round(percentile(latencies, 95), 2) if latencies else None,
            "mean": round(sum(latencies) / len(latencies), 2) if latencies else None,
        },
    }


def start_manual_run(user: Dict[str, Any], mode_name: str = "all") -> Optional[int]:
    """Register a new retrieval evaluation run if no other run is currently executing.

    Args:
        user: Authenticated user dict from ``get_current_user``.
        mode_name: 'all', 'fts', 'vector', 'hybrid', or 'hybrid_graph'.

    Returns:
        Inserted run id, or None when another evaluation run is already running.
    """
    modes = resolve_modes(mode_name)
    queries_total = len(load_queries()) * len(modes)
    repository.fail_stale_runs(STALE_RUN_MINUTES)
    user_id = user.get("id")
    user_name = user.get("display_name") or user.get("username")
    return repository.start_run(mode_name, queries_total, user_id, user_name)


async def execute_run(run_id: int, mode_name: str, top_k: int = DEFAULT_TOP_K) -> str:
    """Run the benchmark asynchronously inside the backend and persist results.

    Args:
        run_id: Database row id created by ``start_manual_run``.
        mode_name: 'all' or a single ``RetrievalMode`` value.
        top_k: Number of top listings requested per query.

    Returns:
        Final status ('completed' or 'failed').
    """
    started = time.perf_counter()
    queries = load_queries()
    modes = resolve_modes(mode_name)
    queries_done = 0
    mode_summaries: Dict[str, Dict[str, Any]] = {}

    try:
        for mode in modes:
            mode_records: List[Dict[str, Any]] = []
            for entry in queries:
                constraints = entry.get("expected_constraints") or {}
                try:
                    response = await hybrid_search_service.search(
                        SearchRequest(
                            query=entry["query"],
                            top_k=top_k,
                            page=1,
                            debug=False,
                            filters={},
                            retrieval_mode=mode,
                        ),
                        generate_llm_answer=False,
                    )
                    results = response.get("results") or []
                    exact_5, _ = constraint_match_at_k(results, constraints, cutoff=5)
                    exact_10, per_constraint_10 = constraint_match_at_k(results, constraints, cutoff=min(10, top_k))
                    mode_records.append(
                        {
                            "query_id": entry["id"],
                            "category": entry.get("category", "uncategorized"),
                            "error": None,
                            "result_count": len(results),
                            "semantic_enabled": bool(response.get("semantic_enabled")),
                            "graph_enabled": bool(response.get("graph_enabled")),
                            "latency_ms": float(response.get("latency_ms") or 0.0),
                            "exact_at_5": round(exact_5, 4) if exact_5 is not None else None,
                            "exact_at_10": round(exact_10, 4) if exact_10 is not None else None,
                            "per_constraint_at_10": {key: round(val, 4) for key, val in per_constraint_10.items()},
                        }
                    )
                except Exception as error:  # noqa: BLE001 — isolate single query failure
                    logger.warning("Retrieval eval query {} ({}) failed: {}", entry["id"], mode.value, error)
                    mode_records.append(
                        {
                            "query_id": entry["id"],
                            "category": entry.get("category", "uncategorized"),
                            "error": str(error),
                            "result_count": 0,
                            "semantic_enabled": False,
                            "graph_enabled": False,
                            "latency_ms": None,
                            "exact_at_5": None,
                            "exact_at_10": None,
                            "per_constraint_at_10": {},
                        }
                    )

                queries_done += 1
                if queries_done % PROGRESS_UPDATE_STEP == 0:
                    await run_in_threadpool(repository.update_progress, run_id, queries_done)

            mode_summaries[mode.value] = summarize_mode_records(mode, mode_records, top_k)
            partial_summary = {
                "modes": mode_summaries,
                "duration_seconds": round(time.perf_counter() - started, 1),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
            await run_in_threadpool(repository.update_progress, run_id, queries_done, partial_summary)

        final_summary = {
            "modes": mode_summaries,
            "duration_seconds": round(time.perf_counter() - started, 1),
            "completed_at": datetime.now(timezone.utc).isoformat(),
        }
        await run_in_threadpool(repository.finish_run, run_id, "completed", queries_done, final_summary, None)
        return "completed"
    except Exception:
        logger.exception("Retrieval evaluation run {} failed", run_id)
        await run_in_threadpool(
            repository.finish_run,
            run_id,
            "failed",
            queries_done,
            {"modes": mode_summaries, "duration_seconds": round(time.perf_counter() - started, 1)},
            "Đánh giá gặp lỗi trong quá trình chạy trên máy chủ.",
        )
        return "failed"


def build_latest_by_mode(runs: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Extract the newest completed metrics for each of the 4 retrieval modes across runs.

    Args:
        runs: Newest-first list of run dicts from ``repository.list_runs``.

    Returns:
        Mapping ``{mode_value: {**mode_summary, "run_id", "finished_at"}}``.
    """
    latest: Dict[str, Dict[str, Any]] = {}
    for run in runs:
        summary = run.get("summary") or {}
        modes = summary.get("modes") or {}
        for mode in ALL_MODES:
            mode_key = mode.value
            if mode_key in latest or mode_key not in modes:
                continue
            latest[mode_key] = {
                **modes[mode_key],
                "run_id": run["id"],
                "finished_at": run.get("finished_at") or run.get("started_at"),
            }
    return latest

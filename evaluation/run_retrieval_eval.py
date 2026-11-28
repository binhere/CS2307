"""Run live retrieval evaluation against POST /v1/search.

Sends each query in ``queries.json`` to the running backend, evaluates objective
constraint satisfaction (price, area, rooms, property type, location, features)
and response latency, and exports a pooling CSV template (``judgments_template.csv``)
for graded 0/1/2 relevance scoring.

Usage:
    python evaluation/run_retrieval_eval.py --backend-url http://localhost:8001 --run-name hybrid_full
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib import error as urllib_error
from urllib import request as urllib_request

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from metrics import constraint_match_at_k, percentile  # noqa: E402

DEFAULT_BACKEND_URL = os.environ.get("EVAL_BACKEND_URL", "http://localhost:8001")
DEFAULT_QUERIES_PATH = SCRIPT_DIR / "queries.json"
DEFAULT_RESULTS_DIR = SCRIPT_DIR / "results"
RETRIEVAL_MODES = ("fts", "vector", "hybrid", "hybrid_graph")


def resolve_search_endpoint(backend_url: str, endpoint: Optional[str]) -> str:
    """Resolve the full search POST URL from either ``--endpoint`` or ``--backend-url``.

    Args:
        backend_url: Base URL of the FastAPI backend (e.g. 'http://localhost:8001').
        endpoint: Optional full search URL (e.g. 'https://cs2307.reais.codes/api/search').

    Returns:
        Full URL to send POST search requests to.
    """
    if endpoint:
        return endpoint.strip()
    return f"{backend_url.rstrip('/')}/v1/search"


def post_search(
    endpoint: str,
    query_text: str,
    top_k: int,
    timeout_seconds: float,
    retrieval_mode: str = "hybrid_graph",
) -> Tuple[Dict[str, Any], float]:
    """Call the search endpoint and return the parsed data payload plus client latency.

    Args:
        endpoint: Full POST URL (e.g. 'http://localhost:8001/v1/search' or
            'https://cs2307.reais.codes/api/search').
        query_text: Natural-language Vietnamese search query.
        top_k: Number of top listings requested.
        timeout_seconds: HTTP request timeout in seconds.
        retrieval_mode: Backend retrieval strategy ('fts', 'vector', 'hybrid', 'hybrid_graph').

    Returns:
        Tuple of ``(response_data_dict, client_latency_ms)``.

    Raises:
        RuntimeError: When the HTTP request fails or the server returns a non-success envelope.
    """
    payload = json.dumps(
        {
            "query": query_text,
            "top_k": top_k,
            "page": 1,
            "debug": True,
            "filters": {},
            "retrieval_mode": retrieval_mode,
        },
        ensure_ascii=False,
    ).encode("utf-8")
    http_request = urllib_request.Request(
        endpoint,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    started = time.perf_counter()
    try:
        with urllib_request.urlopen(http_request, timeout=timeout_seconds) as response:
            raw_body = response.read().decode("utf-8")
    except urllib_error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {error.code} from {endpoint}: {detail[:300]}") from error
    except urllib_error.URLError as error:
        raise RuntimeError(f"Could not reach {endpoint}: {error.reason}") from error

    client_latency_ms = round((time.perf_counter() - started) * 1000.0, 2)
    envelope = json.loads(raw_body)
    if envelope.get("status") != "success" or "data" not in envelope:
        raise RuntimeError(f"Non-success search response: {envelope.get('errors')}")
    return envelope["data"], client_latency_ms


def mean_or_none(values: List[float]) -> Optional[float]:
    """Return the rounded arithmetic mean of a list, or None when empty."""
    if not values:
        return None
    return round(sum(values) / len(values), 4)


def summarize_results(
    query_records: List[Dict[str, Any]],
    endpoint: str,
    run_name: str,
    top_k: int,
    retrieval_mode: str = "hybrid_graph",
) -> Dict[str, Any]:
    """Aggregate per-query retrieval results into a single evaluation report.

    Args:
        query_records: Per-query output dictionaries produced by ``evaluate_queries``.
        endpoint: Full search endpoint URL that was evaluated.
        run_name: Human-readable label for this run.
        top_k: Maximum cutoff evaluated.
        retrieval_mode: Backend retrieval strategy used for this run.

    Returns:
        Summary dictionary ready to be serialized as ``summary.json``.
    """
    succeeded = [record for record in query_records if record["error"] is None]
    failed = [record for record in query_records if record["error"] is not None]

    exact_at_5 = [record["exact_constraint_match_at_5"] for record in succeeded if record["exact_constraint_match_at_5"] is not None]
    exact_at_10 = [record["exact_constraint_match_at_10"] for record in succeeded if record["exact_constraint_match_at_10"] is not None]
    latencies = [record["latency_ms"] for record in succeeded if record["latency_ms"] is not None]

    per_constraint_bucket: Dict[str, List[float]] = defaultdict(list)
    by_category_bucket: Dict[str, List[float]] = defaultdict(list)
    for record in succeeded:
        for key, rate in record["per_constraint_match_at_10"].items():
            per_constraint_bucket[key].append(rate)
        if record["exact_constraint_match_at_10"] is not None:
            by_category_bucket[record["category"]].append(record["exact_constraint_match_at_10"])

    zero_result_count = sum(1 for record in succeeded if record["result_count"] == 0)
    semantic_count = sum(1 for record in succeeded if record["semantic_enabled"])
    graph_count = sum(1 for record in succeeded if record["graph_enabled"])
    denominator = max(len(succeeded), 1)

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "run_name": run_name,
        "retrieval_mode": retrieval_mode,
        "endpoint": endpoint,
        "top_k": top_k,
        "queries_total": len(query_records),
        "queries_succeeded": len(succeeded),
        "queries_failed": len(failed),
        "zero_result_rate": round(zero_result_count / denominator, 4) if succeeded else None,
        "semantic_enabled_rate": round(semantic_count / denominator, 4) if succeeded else None,
        "graph_enabled_rate": round(graph_count / denominator, 4) if succeeded else None,
        "exact_constraint_match_at_5": mean_or_none(exact_at_5),
        "exact_constraint_match_at_10": mean_or_none(exact_at_10),
        "per_constraint_match_at_10": {
            key: mean_or_none(values) for key, values in sorted(per_constraint_bucket.items())
        },
        "exact_constraint_match_at_10_by_category": {
            category: mean_or_none(values) for category, values in sorted(by_category_bucket.items())
        },
        "latency_ms": {
            "p50": round(percentile(latencies, 50), 2) if latencies else None,
            "p95": round(percentile(latencies, 95), 2) if latencies else None,
            "mean": round(sum(latencies) / len(latencies), 2) if latencies else None,
        },
    }


def write_judgments_template(query_records: List[Dict[str, Any]], output_path: Path) -> int:
    """Export retrieved (query, listing) pairs to CSV for manual or LLM-as-a-Judge 0/1/2 scoring.

    Args:
        query_records: Per-query output records from a live evaluation run.
        output_path: Destination CSV path.

    Returns:
        Number of rows written.
    """
    fieldnames = [
        "query_id",
        "category",
        "query",
        "rank",
        "listing_id",
        "title",
        "property_type",
        "price_billion",
        "area_m2",
        "bedrooms",
        "bathrooms",
        "district",
        "address",
        "final_score",
        "relevance_0_1_2",
        "notes",
    ]
    rows_written = 0
    with output_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for record in query_records:
            for rank_index, item in enumerate(record["results"], start=1):
                writer.writerow(
                    {
                        "query_id": record["query_id"],
                        "category": record["category"],
                        "query": record["query"],
                        "rank": rank_index,
                        "listing_id": item.get("listing_id"),
                        "title": item.get("title") or "",
                        "property_type": item.get("property_type") or "",
                        "price_billion": item.get("price_range") if item.get("price_range") is not None else "",
                        "area_m2": item.get("area") if item.get("area") is not None else "",
                        "bedrooms": item.get("bedrooms") if item.get("bedrooms") is not None else "",
                        "bathrooms": item.get("bathrooms") if item.get("bathrooms") is not None else "",
                        "district": item.get("district") or "",
                        "address": item.get("address") or "",
                        "final_score": item.get("final_score") if item.get("final_score") is not None else "",
                        "relevance_0_1_2": "",
                        "notes": "",
                    }
                )
                rows_written += 1
    return rows_written


def evaluate_queries(
    queries: List[Dict[str, Any]],
    endpoint: str,
    top_k: int,
    timeout_seconds: float,
    retrieval_mode: str = "hybrid_graph",
) -> List[Dict[str, Any]]:
    """Replay every query against the live search endpoint and compute per-query constraint metrics.

    Args:
        queries: Query specification dicts loaded from ``queries.json``.
        endpoint: Full search POST URL.
        top_k: Number of results requested per query.
        timeout_seconds: HTTP timeout per request.
        retrieval_mode: Backend retrieval strategy ('fts', 'vector', 'hybrid', 'hybrid_graph').

    Returns:
        List of per-query result dictionaries.
    """
    records: List[Dict[str, Any]] = []
    for index, entry in enumerate(queries, start=1):
        query_id = entry["id"]
        query_text = entry["query"]
        category = entry.get("category", "uncategorized")
        constraints = entry.get("expected_constraints") or {}
        try:
            data, client_latency_ms = post_search(
                endpoint, query_text, top_k, timeout_seconds, retrieval_mode=retrieval_mode
            )
            results = data.get("results") or []
            exact_5, _ = constraint_match_at_k(results, constraints, cutoff=5)
            exact_10, per_constraint_10 = constraint_match_at_k(results, constraints, cutoff=min(10, top_k))
            latency_ms = data.get("latency_ms", client_latency_ms)
            records.append(
                {
                    "query_id": query_id,
                    "category": category,
                    "query": query_text,
                    "expected_constraints": constraints,
                    "error": None,
                    "result_count": len(results),
                    "total_candidates": data.get("total_candidates"),
                    "semantic_enabled": bool(data.get("semantic_enabled")),
                    "graph_enabled": bool(data.get("graph_enabled")),
                    "latency_ms": float(latency_ms),
                    "client_latency_ms": client_latency_ms,
                    "exact_constraint_match_at_5": round(exact_5, 4) if exact_5 is not None else None,
                    "exact_constraint_match_at_10": round(exact_10, 4) if exact_10 is not None else None,
                    "per_constraint_match_at_10": {key: round(value, 4) for key, value in per_constraint_10.items()},
                    "results": results,
                }
            )
            print(
                f"[{index:02d}/{len(queries):02d}] {query_id} | results={len(results):2d} "
                f"| match@10={exact_10 if exact_10 is not None else 'n/a'} | {latency_ms:.0f} ms"
            )
        except Exception as error:  # noqa: BLE001 — keep evaluating remaining queries
            records.append(
                {
                    "query_id": query_id,
                    "category": category,
                    "query": query_text,
                    "expected_constraints": constraints,
                    "error": str(error),
                    "result_count": 0,
                    "total_candidates": 0,
                    "semantic_enabled": False,
                    "graph_enabled": False,
                    "latency_ms": None,
                    "client_latency_ms": None,
                    "exact_constraint_match_at_5": None,
                    "exact_constraint_match_at_10": None,
                    "per_constraint_match_at_10": {},
                    "results": [],
                }
            )
            print(f"[{index:02d}/{len(queries):02d}] {query_id} | ERROR: {error}")
    return records


def parse_arguments(argv: Optional[List[str]] = None) -> argparse.Namespace:
    """Parse command-line arguments for the live retrieval evaluation runner."""
    parser = argparse.ArgumentParser(description="Evaluate real-estate retrieval via POST /v1/search or /api/search.")
    parser.add_argument("--backend-url", default=DEFAULT_BACKEND_URL, help="FastAPI backend base URL.")
    parser.add_argument(
        "--endpoint",
        default=os.environ.get("EVAL_SEARCH_ENDPOINT"),
        help="Full search POST URL (overrides --backend-url, e.g. https://cs2307.reais.codes/api/search).",
    )
    parser.add_argument("--queries", type=Path, default=DEFAULT_QUERIES_PATH, help="Path to queries.json.")
    parser.add_argument("--top-k", type=int, default=10, help="Number of results to fetch per query.")
    parser.add_argument("--limit", type=int, default=None, help="Optional limit on number of queries to run.")
    parser.add_argument("--timeout", type=float, default=60.0, help="HTTP timeout per query in seconds.")
    parser.add_argument(
        "--mode",
        choices=RETRIEVAL_MODES,
        default="hybrid_graph",
        help="Backend retrieval_mode: fts, vector, hybrid, or hybrid_graph (default).",
    )
    parser.add_argument(
        "--run-name",
        default=None,
        help="Subfolder name under evaluation/results/ (defaults to <mode>_<UTC timestamp>).",
    )
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    """Entry point for running live retrieval evaluation."""
    arguments = parse_arguments(argv)
    queries = json.loads(arguments.queries.read_text(encoding="utf-8"))
    if arguments.limit is not None:
        queries = queries[: arguments.limit]

    endpoint = resolve_search_endpoint(arguments.backend_url, arguments.endpoint)
    run_name = arguments.run_name or f"{arguments.mode}_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    run_directory = DEFAULT_RESULTS_DIR / run_name
    run_directory.mkdir(parents=True, exist_ok=True)

    print(
        f"Running {len(queries)} queries against {endpoint} "
        f"(mode={arguments.mode}, top_k={arguments.top_k})..."
    )
    records = evaluate_queries(queries, endpoint, arguments.top_k, arguments.timeout, retrieval_mode=arguments.mode)
    summary = summarize_results(records, endpoint, run_name, arguments.top_k, retrieval_mode=arguments.mode)

    (run_directory / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (run_directory / "per_query.json").write_text(
        json.dumps(records, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    csv_rows = write_judgments_template(records, run_directory / "judgments_template.csv")

    print("\n=== Retrieval Evaluation Summary ===")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"\nSaved run artifacts to: {run_directory}")
    print(f"Judgment template ({csv_rows} rows): {run_directory / 'judgments_template.csv'}")
    return 0 if summary["queries_failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

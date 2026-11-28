"""Score one or more retrieval runs against graded 0/1/2 relevance judgments.

Supports single-run reporting and side-by-side ablation studies (for example,
comparing FTS-only, Vector-only, Hybrid, and Hybrid+Graph runs over the same
pooled judgment CSV).

Usage:
    python evaluation/score_judgments.py \\
        --judgments evaluation/results/pooled_judgments.csv \\
        --runs evaluation/results/fts_only evaluation/results/hybrid_full
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from metrics import mrr_at_k, ndcg_at_k, pool_recall_at_k, precision_at_k  # noqa: E402


def load_judgments(csv_path: Path) -> Dict[str, Dict[int, int]]:
    """Read graded 0/1/2 relevance judgments from a CSV file.

    Rows with an empty ``relevance_0_1_2`` cell are skipped so partial labeling works.

    Args:
        csv_path: Path to the filled CSV file (derived from ``judgments_template.csv``).

    Returns:
        Nested mapping ``{query_id: {listing_id: grade}}``.

    Raises:
        ValueError: When a non-empty relevance value is not an integer in {0, 1, 2}.
    """
    judgments: Dict[str, Dict[int, int]] = defaultdict(dict)
    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row_number, row in enumerate(csv.DictReader(handle), start=2):
            raw_grade = (row.get("relevance_0_1_2") or "").strip()
            if not raw_grade:
                continue
            try:
                grade = int(raw_grade)
            except ValueError as error:
                raise ValueError(f"Line {row_number}: invalid relevance grade {raw_grade!r}") from error
            if grade not in (0, 1, 2):
                raise ValueError(f"Line {row_number}: relevance grade must be 0, 1, or 2 (got {grade})")
            query_id = (row.get("query_id") or "").strip()
            listing_id = int(row["listing_id"])
            judgments[query_id][listing_id] = grade
    return dict(judgments)


def load_run_records(run_path: Path) -> Tuple[str, List[Dict[str, Any]]]:
    """Load per-query retrieval outputs from a run directory or per_query.json path.

    Args:
        run_path: Either a run directory containing ``per_query.json`` or the JSON file itself.

    Returns:
        Tuple of ``(run_label, per_query_records)``.
    """
    if run_path.is_dir():
        label = run_path.name
        json_path = run_path / "per_query.json"
    else:
        label = run_path.parent.name
        json_path = run_path
    records = json.loads(json_path.read_text(encoding="utf-8"))
    return label, records


def score_run(
    run_label: str,
    records: List[Dict[str, Any]],
    judgments: Dict[str, Dict[int, int]],
) -> Dict[str, Any]:
    """Compute Precision@K, nDCG@K, MRR@10, and Pool Recall@10 for one retrieval run.

    Args:
        run_label: Display name of the run.
        records: Per-query retrieval records from ``per_query.json``.
        judgments: Graded relevance lookup ``{query_id: {listing_id: grade}}``.

    Returns:
        Dictionary of averaged IR metrics across all judged queries.
    """
    metrics_accumulator: Dict[str, List[float]] = defaultdict(list)
    judged_queries = 0

    for record in records:
        query_id = record["query_id"]
        query_judgments = judgments.get(query_id)
        if not query_judgments:
            continue
        judged_queries += 1

        retrieved_ids = [int(item["listing_id"]) for item in record.get("results") or []]
        grades = [query_judgments.get(listing_id, 0) for listing_id in retrieved_ids]
        ideal_pool_grades = list(query_judgments.values())
        relevant_pool_ids = [listing_id for listing_id, grade in query_judgments.items() if grade >= 1]

        top_10_ids = retrieved_ids[:10]
        judged_in_top_10 = sum(1 for listing_id in top_10_ids if listing_id in query_judgments)
        coverage_at_10 = judged_in_top_10 / len(top_10_ids) if top_10_ids else 0.0

        metrics_accumulator["precision_at_5"].append(precision_at_k(grades, cutoff=5, min_relevant_grade=1))
        metrics_accumulator["precision_at_10"].append(precision_at_k(grades, cutoff=10, min_relevant_grade=1))
        metrics_accumulator["strict_precision_at_5"].append(precision_at_k(grades, cutoff=5, min_relevant_grade=2))
        metrics_accumulator["ndcg_at_5"].append(ndcg_at_k(grades, cutoff=5, ideal_relevances=ideal_pool_grades))
        metrics_accumulator["ndcg_at_10"].append(ndcg_at_k(grades, cutoff=10, ideal_relevances=ideal_pool_grades))
        metrics_accumulator["mrr_at_10"].append(mrr_at_k(grades, cutoff=10, min_relevant_grade=1))
        metrics_accumulator["pool_recall_at_10"].append(pool_recall_at_k(retrieved_ids, relevant_pool_ids, cutoff=10))
        metrics_accumulator["judged_coverage_at_10"].append(coverage_at_10)

    averaged = {
        metric_name: round(sum(values) / len(values), 4) if values else 0.0
        for metric_name, values in metrics_accumulator.items()
    }
    return {"run": run_label, "judged_queries": judged_queries, **averaged}


def format_markdown_table(rows: List[Dict[str, Any]]) -> str:
    """Format multi-run evaluation results as a GitHub-flavored Markdown table."""
    columns = [
        ("run", "Run"),
        ("judged_queries", "Queries"),
        ("precision_at_5", "P@5"),
        ("precision_at_10", "P@10"),
        ("strict_precision_at_5", "P@5 (grade=2)"),
        ("ndcg_at_5", "nDCG@5"),
        ("ndcg_at_10", "nDCG@10"),
        ("mrr_at_10", "MRR@10"),
        ("pool_recall_at_10", "Pool Recall@10"),
        ("judged_coverage_at_10", "Judged@10"),
    ]
    header = "| " + " | ".join(label for _, label in columns) + " |"
    separator = "| " + " | ".join("---" for _ in columns) + " |"
    body_lines = []
    for row in rows:
        cells = []
        for key, _ in columns:
            value = row.get(key, "")
            cells.append(f"{value:.4f}" if isinstance(value, float) else str(value))
        body_lines.append("| " + " | ".join(cells) + " |")
    return "\n".join([header, separator, *body_lines])


def parse_arguments(argv: Optional[List[str]] = None) -> argparse.Namespace:
    """Parse command-line arguments for judgment scoring."""
    parser = argparse.ArgumentParser(description="Score retrieval runs using graded 0/1/2 judgments.")
    parser.add_argument("--judgments", type=Path, required=True, help="Path to filled judgments CSV.")
    parser.add_argument(
        "--runs",
        type=Path,
        nargs="+",
        required=True,
        help="One or more run folders (or per_query.json files) to score and compare.",
    )
    parser.add_argument("--output", type=Path, default=None, help="Optional JSON output path.")
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    """Entry point for scoring runs against graded relevance judgments."""
    arguments = parse_arguments(argv)
    judgments = load_judgments(arguments.judgments)
    if not judgments:
        print("No filled relevance_0_1_2 grades found in the CSV.", file=sys.stderr)
        return 1

    rows = []
    for run_path in arguments.runs:
        label, records = load_run_records(run_path)
        rows.append(score_run(label, records, judgments))

    print(format_markdown_table(rows))
    if arguments.output is not None:
        arguments.output.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nSaved comparison JSON to: {arguments.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

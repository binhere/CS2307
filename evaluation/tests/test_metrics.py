"""Unit tests for retrieval evaluation metrics, constraint checks, and judgment scoring."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

EVALUATION_DIR = Path(__file__).resolve().parent.parent
if str(EVALUATION_DIR) not in sys.path:
    sys.path.insert(0, str(EVALUATION_DIR))

from metrics import (  # noqa: E402
    check_listing_constraints,
    constraint_match_at_k,
    mrr_at_k,
    ndcg_at_k,
    percentile,
    pool_recall_at_k,
    precision_at_k,
)
from score_judgments import load_judgments, score_run  # noqa: E402


class RetrievalMetricsTests(unittest.TestCase):
    def test_precision_penalizes_single_hit_in_top_ten(self) -> None:
        relevances = [2, 0, 0, 0, 0, 0, 0, 0, 0, 0]
        self.assertAlmostEqual(precision_at_k(relevances, cutoff=10), 0.1)
        self.assertAlmostEqual(mrr_at_k(relevances, cutoff=10), 1.0)

    def test_ndcg_rewards_higher_grade_at_top(self) -> None:
        ideal_pool = [2, 2, 1, 0, 0]
        good_order = [2, 2, 1, 0, 0]
        weak_order = [0, 0, 1, 2, 2]
        self.assertAlmostEqual(ndcg_at_k(good_order, cutoff=5, ideal_relevances=ideal_pool), 1.0)
        self.assertLess(ndcg_at_k(weak_order, cutoff=5, ideal_relevances=ideal_pool), 0.7)

    def test_pool_recall_measures_fraction_of_pooled_relevant_items(self) -> None:
        retrieved = [101, 102, 103, 104, 105]
        relevant_pool = [101, 105, 200, 201]
        self.assertAlmostEqual(pool_recall_at_k(retrieved, relevant_pool, cutoff=5), 0.5)

    def test_percentile_interpolates_p50_and_p95(self) -> None:
        self.assertAlmostEqual(percentile([10, 20, 30, 40, 50], 50), 30.0)
        self.assertAlmostEqual(percentile([10, 20, 30, 40, 50], 95), 48.0)
        self.assertAlmostEqual(percentile([], 50), 0.0)


class ConstraintCheckerTests(unittest.TestCase):
    def test_checks_all_supported_constraints_against_listing(self) -> None:
        listing = {
            "listing_id": 1,
            "property_type": "Căn hộ chung cư",
            "price_range": 4.2,
            "area": 72.0,
            "bedrooms": 2,
            "bathrooms": 2,
            "district": "Phường Long Trường",
            "former_admin_area": "Thành phố Thủ Đức",
            "address": "Đường Nguyễn Xiển, Phường Long Trường, TP. Thủ Đức",
            "pool": True,
            "near_school": True,
        }
        constraints = {
            "property_types": ["Căn hộ"],
            "max_price": 5.0,
            "min_area": 70.0,
            "min_bedrooms": 2,
            "min_bathrooms": 2,
            "locations": ["Long Trường"],
            "required_features": ["pool", "near_school"],
        }
        checks = check_listing_constraints(listing, constraints)
        self.assertTrue(all(checks.values()))

    def test_constraint_match_at_k_handles_mixed_results_and_empty_constraints(self) -> None:
        listings = [
            {"property_type": "Căn hộ chung cư", "price_range": 4.0},
            {"property_type": "Nhà riêng", "price_range": 6.5},
        ]
        exact, per_key = constraint_match_at_k(
            listings,
            {"property_types": ["Căn hộ"], "max_price": 5.0},
            cutoff=2,
        )
        self.assertAlmostEqual(exact, 0.5)
        self.assertAlmostEqual(per_key["property_type"], 0.5)
        self.assertAlmostEqual(per_key["price"], 0.5)

        empty_exact, empty_per_key = constraint_match_at_k(listings, {}, cutoff=2)
        self.assertIsNone(empty_exact)
        self.assertEqual(empty_per_key, {})


class JudgmentScoringTests(unittest.TestCase):
    def test_load_judgments_and_score_run(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            csv_path = Path(temp_dir) / "judgments.csv"
            csv_path.write_text(
                "query_id,listing_id,relevance_0_1_2\n"
                "Q01,10,2\n"
                "Q01,20,1\n"
                "Q01,30,0\n"
                "Q01,40,\n",
                encoding="utf-8",
            )
            judgments = load_judgments(csv_path)
            self.assertEqual(judgments, {"Q01": {10: 2, 20: 1, 30: 0}})

            records = [{"query_id": "Q01", "results": [{"listing_id": 10}, {"listing_id": 30}, {"listing_id": 20}]}]
            scored = score_run("hybrid", records, judgments)
            self.assertEqual(scored["judged_queries"], 1)
            self.assertAlmostEqual(scored["mrr_at_10"], 1.0)
            self.assertAlmostEqual(scored["pool_recall_at_10"], 1.0)
            self.assertAlmostEqual(scored["judged_coverage_at_10"], 1.0)

    def test_queries_json_has_valid_unique_ids(self) -> None:
        queries = json.loads((EVALUATION_DIR / "queries.json").read_text(encoding="utf-8"))
        self.assertGreaterEqual(len(queries), 20)
        ids = [entry["id"] for entry in queries]
        self.assertEqual(len(ids), len(set(ids)))


if __name__ == "__main__":
    unittest.main()

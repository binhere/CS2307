"""Unit tests for the in-process retrieval evaluation service."""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from src.services.retrieval_evaluation import service
from src.services.retrieval_evaluation.metrics import check_listing_constraints, constraint_match_at_k


class RetrievalEvaluationServiceTests(unittest.IsolatedAsyncioTestCase):
    def test_bundled_queries_has_150_unique_entries(self) -> None:
        queries = service.load_queries()
        self.assertEqual(len(queries), 150)
        self.assertEqual(len({entry["id"] for entry in queries}), 150)

    def test_constraint_checker_matches_expected_listing(self) -> None:
        listing = {
            "property_type": "Căn hộ chung cư",
            "price_range": 3.8,
            "area": 72.0,
            "bedrooms": 2,
            "district": "Phường Long Bình",
            "pool": True,
        }
        constraints = {
            "property_types": ["Căn hộ"],
            "max_price": 4.0,
            "min_area": 70.0,
            "min_bedrooms": 2,
            "locations": ["Long Bình"],
            "required_features": ["pool"],
        }
        self.assertTrue(all(check_listing_constraints(listing, constraints).values()))
        exact, per_key = constraint_match_at_k([listing], constraints, cutoff=5)
        self.assertAlmostEqual(exact, 1.0)
        self.assertAlmostEqual(per_key["price"], 1.0)

    async def test_execute_run_persists_completed_summary(self) -> None:
        sample_queries = [
            {
                "id": "Q001",
                "category": "budget_apartment",
                "query": "căn hộ 2 phòng ngủ dưới 4 tỷ",
                "expected_constraints": {"property_types": ["Căn hộ"], "min_bedrooms": 2, "max_price": 4.0},
            }
        ]
        mock_search = AsyncMock(
            return_value={
                "results": [{"listing_id": 1, "property_type": "Căn hộ chung cư", "bedrooms": 2, "price_range": 3.5}],
                "semantic_enabled": True,
                "graph_enabled": False,
                "latency_ms": 42.0,
            }
        )
        update_mock = MagicMock()
        finish_mock = MagicMock()

        with (
            patch("src.services.retrieval_evaluation.service.load_queries", return_value=sample_queries),
            patch("src.services.retrieval_evaluation.service.hybrid_search_service.search", mock_search),
            patch("src.services.retrieval_evaluation.service.repository.update_progress", update_mock),
            patch("src.services.retrieval_evaluation.service.repository.finish_run", finish_mock),
        ):
            outcome = await service.execute_run(run_id=7, mode_name="hybrid", top_k=10)

        self.assertEqual(outcome, "completed")
        finish_mock.assert_called_once()
        args = finish_mock.call_args.args
        self.assertEqual(args[0], 7)
        self.assertEqual(args[1], "completed")
        self.assertEqual(args[2], 1)
        hybrid_summary = args[3]["modes"]["hybrid"]
        self.assertAlmostEqual(hybrid_summary["exact_constraint_match_at_10"], 1.0)
        self.assertAlmostEqual(hybrid_summary["latency_ms"]["p50"], 42.0)

    def test_build_latest_by_mode_picks_newest_per_mode(self) -> None:
        runs = [
            {
                "id": 5,
                "finished_at": "2026-11-28T10:00:00Z",
                "summary": {"modes": {"hybrid_graph": {"exact_constraint_match_at_10": 0.88}}},
            },
            {
                "id": 4,
                "finished_at": "2026-11-28T09:00:00Z",
                "summary": {
                    "modes": {
                        "fts": {"exact_constraint_match_at_10": 0.72},
                        "hybrid_graph": {"exact_constraint_match_at_10": 0.80},
                    }
                },
            },
        ]
        latest = service.build_latest_by_mode(runs)
        self.assertEqual(latest["hybrid_graph"]["run_id"], 5)
        self.assertEqual(latest["fts"]["run_id"], 4)


if __name__ == "__main__":
    unittest.main()

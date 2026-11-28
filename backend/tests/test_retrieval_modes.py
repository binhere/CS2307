"""Unit tests for selectable RetrievalMode strategies in HybridSearchService."""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
from pydantic import ValidationError

from src.search.graph_repository import GraphSearchResponse
from src.search.schemas import RetrievalMode, SearchRequest
from src.search.service import HybridSearchService


def _sample_candidates() -> list[dict]:
    """Two listings where candidate 2 wins on vector cosine similarity while candidate 1 wins on criteria."""
    return [
        {
            "id": 1,
            "listing_id": 1001,
            "title": "Căn hộ 2PN giá tốt",
            "property_type": "Căn hộ chung cư",
            "district": "Phường Long Bình",
            "price_range": 3.5,
            "area": 70.0,
            "bedrooms": 2,
            "bathrooms": 2,
            "semantic_score": 0.45,
            "text_score": 0.9,
            "amenity_evidence": [],
        },
        {
            "id": 2,
            "listing_id": 1002,
            "title": "Nhà riêng mô tả gần nghĩa",
            "property_type": "Nhà riêng",
            "district": "Phường Thới An",
            "price_range": 9.5,
            "area": 80.0,
            "bedrooms": 4,
            "bathrooms": 3,
            "semantic_score": 0.92,
            "text_score": 0.1,
            "amenity_evidence": [],
        },
    ]


class SearchRequestRetrievalModeTests(unittest.TestCase):
    def test_default_retrieval_mode_is_hybrid_graph(self) -> None:
        request = SearchRequest(query="căn hộ 2 phòng ngủ dưới 4 tỷ")
        self.assertEqual(request.retrieval_mode, RetrievalMode.HYBRID_GRAPH)

    def test_invalid_retrieval_mode_is_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            SearchRequest(query="căn hộ 2 phòng ngủ", retrieval_mode="bm25")


class HybridSearchRetrievalModeTests(unittest.IsolatedAsyncioTestCase):
    async def _run_with_mode(self, mode: RetrievalMode):
        service = HybridSearchService()
        candidates = _sample_candidates()
        encode_mock = MagicMock(return_value=np.array([0.1, 0.2, 0.3], dtype=float))
        search_mock = MagicMock(return_value=candidates)
        graph_mock = MagicMock(
            return_value=GraphSearchResponse(
                available=True,
                candidates=[{"listing_id": "1001", "graph_score": 0.95, "graph_evidence": {"matched": True}}],
            )
        )
        fetch_graph_mock = MagicMock(return_value=[])
        count_mock = MagicMock(return_value=len(candidates))

        async def _deterministic_parse(_query, deterministic):
            return deterministic

        with (
            patch.object(service.llm_parser, "parse", new=AsyncMock(side_effect=_deterministic_parse)),
            patch("src.search.service.search_repository.embedding_coverage", return_value=(100, 100)),
            patch("src.search.service.embedding_model.encode_query", encode_mock),
            patch("src.search.service.search_repository.search", search_mock),
            patch("src.search.service.search_repository.fetch_graph_candidates", fetch_graph_mock),
            patch("src.search.service.search_repository.count", count_mock),
            patch("src.search.service.graph_repository.search", graph_mock),
        ):
            response = await service.search(
                SearchRequest(query="căn hộ 2 phòng ngủ dưới 4 tỷ", top_k=5, retrieval_mode=mode),
                generate_llm_answer=False,
            )
        return response, encode_mock, search_mock, graph_mock

    async def test_fts_mode_skips_vector_and_graph(self) -> None:
        response, encode_mock, search_mock, graph_mock = await self._run_with_mode(RetrievalMode.FTS)
        encode_mock.assert_not_called()
        graph_mock.assert_not_called()
        self.assertIsNone(search_mock.call_args.args[1])
        self.assertEqual(response["retrieval_mode"], "fts")
        self.assertFalse(response["semantic_enabled"])
        self.assertFalse(response["graph_enabled"])

    async def test_vector_mode_ranks_purely_by_semantic_score_and_skips_graph(self) -> None:
        response, encode_mock, search_mock, graph_mock = await self._run_with_mode(RetrievalMode.VECTOR)
        encode_mock.assert_called_once()
        graph_mock.assert_not_called()
        self.assertIsNotNone(search_mock.call_args.args[1])
        self.assertEqual(response["retrieval_mode"], "vector")
        self.assertTrue(response["semantic_enabled"])
        self.assertFalse(response["graph_enabled"])
        self.assertEqual([item["listing_id"] for item in response["results"]], [1002, 1001])
        self.assertAlmostEqual(response["results"][0]["final_score"], 0.92)

    async def test_hybrid_mode_uses_vector_and_skips_graph(self) -> None:
        response, encode_mock, search_mock, graph_mock = await self._run_with_mode(RetrievalMode.HYBRID)
        encode_mock.assert_called_once()
        graph_mock.assert_not_called()
        self.assertIsNotNone(search_mock.call_args.args[1])
        self.assertEqual(response["retrieval_mode"], "hybrid")
        self.assertTrue(response["semantic_enabled"])
        self.assertFalse(response["graph_enabled"])

    async def test_hybrid_graph_mode_uses_both_vector_and_graph(self) -> None:
        response, encode_mock, search_mock, graph_mock = await self._run_with_mode(RetrievalMode.HYBRID_GRAPH)
        encode_mock.assert_called_once()
        graph_mock.assert_called_once()
        self.assertIsNotNone(search_mock.call_args.args[1])
        self.assertEqual(response["retrieval_mode"], "hybrid_graph")
        self.assertTrue(response["semantic_enabled"])
        self.assertTrue(response["graph_enabled"])


if __name__ == "__main__":
    unittest.main()

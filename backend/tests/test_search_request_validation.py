import unittest
from unittest.mock import patch

from pydantic import ValidationError

from src.api.v1.endpoints.chat.view_chat import ChatRequest
from src.search.schemas import SearchRequest
from src.search.service import HybridSearchService


class ExplicitFilterValidationTests(unittest.TestCase):
    def test_uncastable_numeric_filter_is_rejected(self):
        for filters in ({"min_price": "abc"}, {"bedrooms": "2.5"}, {"max_area": [1]}):
            with self.assertRaises(ValidationError, msg=filters):
                SearchRequest(query="căn hộ", filters=filters)
            with self.assertRaises(ValidationError, msg=filters):
                ChatRequest(message="căn hộ", filters=filters)

    def test_valid_and_unset_values_pass(self):
        request = SearchRequest(
            query="căn hộ",
            filters={"min_price": "2.5", "bedrooms": 2, "max_price": "", "bathrooms": 0, "district": "Quận 7"},
        )
        self.assertEqual(request.filters["min_price"], "2.5")


class SearchRouteValidationTests(unittest.IsolatedAsyncioTestCase):
    async def test_search_route_answers_400_for_a_bad_filter(self):
        import httpx
        from src.main import app

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/v1/search", json={"query": "căn hộ", "filters": {"min_price": "abc"}})
        self.assertEqual(response.status_code, 400)


class ImpressionLoggingTests(unittest.TestCase):
    def test_ranks_are_absolute_across_pages_and_keep_the_conversation(self):
        captured = []
        request = SearchRequest(query="căn hộ", top_k=2, page=2)
        results = [{"listing_id": 11, "final_score": 0.9}, {"listing_id": 12, "final_score": 0.8}]
        with patch(
            "src.services.feedback.repository.insert_recommendation_events",
            side_effect=captured.extend,
        ):
            HybridSearchService._log_impressions(request, results, 7, 42, 2)

        self.assertEqual([row["retrieval_rank"] for row in captured], [3, 4])
        self.assertEqual({row["conversation_id"] for row in captured}, {42})


if __name__ == "__main__":
    unittest.main()

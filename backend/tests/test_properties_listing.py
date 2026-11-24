import asyncio
import json
import unittest
from unittest.mock import patch

from sqlalchemy.exc import OperationalError

from src.api.v1.endpoints.properties.controllers.controller_properties import PropertiesController
from src.api.v1.endpoints.properties.repositories import repo_properties


LISTING_ARGUMENTS = dict(
    query=None, min_price=None, max_price=None, min_area=None, max_area=None,
    bedrooms=None, bathrooms=None, legal_status=None, furniture=None,
    house_direction=None, balcony_direction=None, min_frontage=None,
    min_road_width=None, sort_by="newest", sort_order="desc", page=1, limit=10,
    property_type=None, district=None,
)


class RecordingConnector:
    """Stand-in for postgres_client that records SQL and returns canned rows."""

    def __init__(self, pages):
        self.pages = list(pages)
        self.calls = []

    def fetch_mappings(self, raw_query, **params):
        self.calls.append((raw_query, params))
        return self.pages.pop(0)


class RepoGetPropertiesTests(unittest.TestCase):
    def test_csv_filters_and_amenities_build_parameterized_sql(self):
        connector = RecordingConnector([[{"id": 1, "posted_date": None}], [{"total": 1}]])
        with patch.object(repo_properties, "postgres_client", connector):
            data = repo_properties.repo_get_properties(
                **{
                    **LISTING_ARGUMENTS,
                    "legal_status": "Sổ hồng riêng, Sổ chung",
                    "furniture": "Đầy đủ",
                    "amenity_filters": {"pool": True, "gym": None, "not_a_column": True},
                }
            )

        page_query, page_params = connector.calls[0]
        count_query, count_params = connector.calls[1]
        self.assertIn("LOWER(legal_status) IN (:legal_status_0, :legal_status_1)", page_query)
        self.assertEqual(page_params["legal_status_1"], "sổ chung")
        self.assertIn("LOWER(furnishing) = LOWER(:furnishing)", page_query)
        self.assertEqual(page_params["furnishing"], "Đầy đủ")
        self.assertIn("pool = :pool", page_query)
        self.assertNotIn("gym", page_params)
        self.assertNotIn("not_a_column", page_query)
        self.assertEqual((page_params["limit"], page_params["offset"]), (10, 0))
        self.assertNotIn("limit", count_params)
        self.assertIn("SELECT COUNT(*) AS total", count_query)
        self.assertEqual(data["total"], 1)
        self.assertEqual(data["num_pages"], 1)

    def test_database_error_propagates_instead_of_an_empty_page(self):
        class FailingConnector:
            def fetch_mappings(self, raw_query, **params):
                raise OperationalError("SELECT", {}, Exception("connection refused"))

        with patch.object(repo_properties, "postgres_client", FailingConnector()):
            with self.assertRaises(OperationalError):
                repo_properties.repo_get_properties(**LISTING_ARGUMENTS)


class PropertiesControllerTests(unittest.TestCase):
    def test_database_failure_returns_500_with_a_generic_message(self):
        def fail(**_arguments):
            raise OperationalError("SELECT", {}, Exception("password authentication failed for user x"))

        with patch(
            "src.api.v1.endpoints.properties.controllers.controller_properties.repo_get_properties",
            side_effect=fail,
        ):
            response = asyncio.run(PropertiesController().ctr_get_properties(**LISTING_ARGUMENTS))

        body = json.loads(response.body)
        self.assertEqual(response.status_code, 500)
        self.assertEqual(body["status"], "failed")
        self.assertNotIn("password authentication", response.body.decode())


class PropertiesRouteValidationTests(unittest.IsolatedAsyncioTestCase):
    async def test_page_size_above_100_is_rejected(self):
        # Starlette 0.32's TestClient does not work with httpx 0.28, so drive the
        # ASGI app directly; startup handlers are not run, so no service is touched.
        import httpx
        from src.main import app

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/v1/properties", params={"limit": 500})
        self.assertEqual(response.status_code, 400)


if __name__ == "__main__":
    unittest.main()

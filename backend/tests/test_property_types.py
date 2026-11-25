"""Property-type groups: "Căn hộ" and "Nhà đất" must find the catalog's own types.

The filter UI and the query parser speak two groups, while the catalog (the new crawl) stores
"Căn hộ chung cư", "Nhà riêng", ... Matching the group names literally found no listing: the
listing page showed 0 results for "Tất cả", and search never matched a property type.
"""

import unittest
from unittest.mock import patch

from src.api.v1.endpoints.properties.repositories import repo_properties
from src.search.normalizer import expand_property_types
from src.search.query_parser import RuleBasedQueryParser
from src.search.ranker import rank_candidates
from src.search.repository import SearchRepository

LISTING_ARGUMENTS = dict(
    query=None, min_price=None, max_price=None, min_area=None, max_area=None,
    bedrooms=None, bathrooms=None, legal_status=None, furniture=None,
    house_direction=None, balcony_direction=None, min_frontage=None,
    min_road_width=None, sort_by="newest", sort_order="desc", page=1, limit=10,
    property_type=None, district=None,
)
HOUSE_TYPES = ["Nhà đất", "Nhà riêng", "Nhà mặt phố", "Nhà biệt thự, liền kề", "Shophouse, nhà phố thương mại"]


class RecordingConnector:
    """Stand-in for postgres_client that records SQL and returns an empty page."""

    def __init__(self):
        self.calls = []

    def fetch_mappings(self, raw_query, **params):
        self.calls.append((raw_query, params))
        return [{"total": 0}] if "COUNT(*)" in raw_query else []


def listing_property_type_values(property_type):
    """The property_type values the listing query binds for one filter value."""
    connector = RecordingConnector()
    with patch.object(repo_properties, "postgres_client", connector):
        repo_properties.repo_get_properties(**{**LISTING_ARGUMENTS, "property_type": property_type})
    _, params = connector.calls[0]
    if "property_type" in params:
        return [params["property_type"]]
    return [value for key, value in sorted(params.items()) if key.startswith("property_type_")]


class ExpandPropertyTypesTests(unittest.TestCase):
    def test_group_names_become_the_listing_types_they_cover(self):
        self.assertEqual(expand_property_types(["Căn hộ"]), ["Căn hộ", "Căn hộ chung cư"])
        self.assertEqual(expand_property_types(["nhà đất"]), HOUSE_TYPES)

    def test_exact_types_and_unknown_values_are_kept_once(self):
        self.assertEqual(expand_property_types(["Nhà riêng", "Đất nền", "Nhà đất"]),
                         ["Nhà riêng", "Đất nền", "Nhà đất", "Nhà mặt phố", "Nhà biệt thự, liền kề",
                          "Shophouse, nhà phố thương mại"])


class ListingFilterTests(unittest.TestCase):
    def test_both_groups_cover_every_catalog_type_with_commas_intact(self):
        values = listing_property_type_values("Căn hộ,Nhà đất")
        self.assertIn("căn hộ chung cư", values)
        self.assertIn("nhà biệt thự, liền kề", values)
        self.assertIn("shophouse, nhà phố thương mại", values)
        self.assertEqual(len(values), 7)

    def test_one_group_filters_to_its_own_types(self):
        self.assertEqual(listing_property_type_values("Căn hộ"), ["căn hộ", "căn hộ chung cư"])

    def test_no_property_type_adds_no_condition(self):
        connector = RecordingConnector()
        with patch.object(repo_properties, "postgres_client", connector):
            repo_properties.repo_get_properties(**LISTING_ARGUMENTS)
        query, params = connector.calls[0]
        self.assertNotIn("LOWER(property_type)", query)
        self.assertFalse(any(key.startswith("property_type") for key in params))


class SearchTests(unittest.TestCase):
    def test_explicit_group_filter_matches_the_catalog_types(self):
        parsed = RuleBasedQueryParser().parse("tìm nhà", explicit_filters={"property_type": "Căn hộ"})
        _, params = SearchRepository()._where(parsed)
        self.assertEqual(params["property_type_values"], ["căn hộ", "căn hộ chung cư"])

    def test_a_query_for_an_apartment_ranks_the_catalog_apartment_type_higher(self):
        parsed = RuleBasedQueryParser().parse("căn hộ 2 phòng ngủ")
        items = [
            {"listing_id": 1, "property_type": "Nhà riêng", "bedrooms": 2, "semantic_score": .8},
            {"listing_id": 2, "property_type": "Căn hộ chung cư", "bedrooms": 2, "semantic_score": .8},
        ]
        ranked = rank_candidates(items, parsed)
        self.assertEqual(ranked[0]["listing_id"], 2)
        self.assertGreater(ranked[0]["score_breakdown"]["target"], ranked[1]["score_breakdown"]["target"])


if __name__ == "__main__":
    unittest.main()

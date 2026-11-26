"""Enriching posted listings: nearest amenities with estimated drives, the graph statements, and
when a listing counts as done (a Neo4j outage must leave it for a later pass).
"""

import unittest
from datetime import date
from unittest.mock import patch

from src.services.listing_enrichment import graph, service
from src.services.listing_enrichment.amenities import NEAREST_PER_CATEGORY, amenity_reference

# A point in Phường Cát Lái.
CAT_LAI = (10.7792, 106.7571)


def listing_row(**values):
    """A posted listing's properties row with the columns enrichment reads."""
    row = {
        "id": 9000000001, "listing_id": 9000000001, "listing_type": "Tin người dùng", "property_type": "Căn hộ chung cư",
        "title": "Căn hộ 2 phòng ngủ", "description": "Mô tả", "address": "Đường Số 60, Phường Cát Lái, Hồ Chí Minh",
        "legal_status": "Sổ hồng riêng", "furnishing": None, "district": "Phường Cát Lái",
        "former_admin_area": "Thành phố Thủ Đức", "posted_date": date(2026, 11, 26), "price_range": 4.5, "area": 70.0,
        "bedrooms": 2, "bathrooms": None, "latitude_longitude": f"{CAT_LAI[0]}, {CAT_LAI[1]}",
        "submission": {"metadata": {"street": "Đường Số 60"}},
    }
    row.update(values)
    return row


class AmenityTests(unittest.TestCase):
    def test_every_category_gets_its_nearest_amenities_with_an_estimated_drive(self):
        reference = amenity_reference()
        found = reference.nearest(*CAT_LAI)
        categories = {item.category for item in found}
        self.assertEqual(len(categories), 9)
        self.assertEqual(len(found), 9 * NEAREST_PER_CATEGORY)
        schools = [item for item in found if item.category == "school"]
        self.assertEqual([item.rank for item in schools], [1, 2, 3, 4, 5])
        self.assertEqual(schools, sorted(schools, key=lambda item: item.straight_line_km))
        factor, speed = reference.calibration["school"]["road_factor"], reference.calibration["school"]["speed_kmh"]
        first = schools[0]
        self.assertAlmostEqual(first.driving_distance_km, first.straight_line_km * factor, places=3)
        self.assertAlmostEqual(first.driving_duration_min, first.driving_distance_km / speed * 60, places=1)
        self.assertEqual(first.is_within_threshold, first.driving_distance_km <= reference.near_threshold_km["school"])


class GraphStatementTests(unittest.TestCase):
    def test_a_located_listing_links_to_its_places_cell_and_amenities(self):
        amenities = amenity_reference().nearest(*CAT_LAI)[:2]
        statements = graph.listing_statements(listing_row(), CAT_LAI, amenities)
        queries = [query for query, _ in statements]
        self.assertEqual(queries, [graph.REMOVE_OWN_RELATIONSHIPS, graph.UPSERT_LISTING, graph.LINK_WARD, graph.LINK_FORMER_AREA,
                                   graph.LINK_STREET, graph.LINK_CELL, graph.LINK_AMENITIES])
        parameters = {query: values for query, values in statements}
        self.assertEqual(parameters[graph.LINK_WARD]["ward_id"], "ward_phuong_cat_lai")
        self.assertEqual((parameters[graph.LINK_FORMER_AREA]["former_area_id"], parameters[graph.LINK_FORMER_AREA]["former_type"]),
                         ("faa_thanh_pho_thu_duc", "Thành phố"))
        listing = parameters[graph.UPSERT_LISTING]
        self.assertEqual((listing["properties"]["ward_commune"], listing["properties"]["listing_id"], listing["posted_date"]),
                         ("Phường Cát Lái", "9000000001", "2026-11-26"))
        self.assertEqual(len(parameters[graph.LINK_AMENITIES]["amenities"]), 2)

    def test_a_listing_without_coordinates_still_joins_its_ward(self):
        statements = graph.listing_statements(listing_row(latitude_longitude=None), None, [])
        queries = [query for query, _ in statements]
        self.assertIn(graph.LINK_WARD, queries)
        self.assertNotIn(graph.LINK_CELL, queries)
        self.assertNotIn(graph.LINK_AMENITIES, queries)

    def test_cells_group_points_about_150_metres_apart(self):
        here = graph.location_cell(10.77921, 106.75711)[0]
        self.assertEqual(here, graph.location_cell(10.77931, 106.75721)[0])  # ~15 m away
        self.assertNotEqual(here, graph.location_cell(10.78221, 106.75711)[0])  # ~330 m north


class FakeGraph:
    def __init__(self, enabled, reachable):
        self.enabled, self.reachable, self.writes = enabled, reachable, []

    def write(self, statements):
        if not self.reachable:
            return False
        self.writes.append(statements)
        return True


class FakeDatabase:
    def __init__(self, rows=()):
        self.rows, self.updates = list(rows), []

    def fetch_mappings(self, query, **parameters):
        return self.rows

    def execute_write(self, query, **parameters):
        self.updates.append(parameters)


class EnrichRowTests(unittest.TestCase):
    def enrich(self, fake_graph, row=None, database=None):
        stored = []
        database = database or FakeDatabase()
        with patch.object(service, "graph_repository", fake_graph), patch.object(service, "postgres_client", database), \
                patch.object(service, "store_amenities", lambda listing_id, amenities: stored.append((listing_id, len(amenities)))), \
                patch.dict(service._schema_state, {"is_ready": False}):
            result = service.enrich_row(row or listing_row())
        return result, stored, database

    def test_a_listing_is_done_once_amenities_and_graph_are_written(self):
        fake_graph = FakeGraph(enabled=True, reachable=True)
        result, stored, database = self.enrich(fake_graph)
        self.assertTrue(result)
        self.assertEqual(stored, [("9000000001", 45)])
        self.assertEqual(len(fake_graph.writes), len(graph.SCHEMA_STATEMENTS) + 1)  # schema once, then the listing
        self.assertEqual(database.updates, [{"coordinates": f"{CAT_LAI[0]}, {CAT_LAI[1]}", "id": 9000000001}])

    def test_a_graph_outage_leaves_the_listing_for_a_later_pass(self):
        result, stored, database = self.enrich(FakeGraph(enabled=True, reachable=False))
        self.assertFalse(result)
        self.assertEqual(len(stored), 1)  # search already has its amenities
        self.assertEqual(database.updates, [])

    def test_without_a_graph_and_without_coordinates_the_listing_is_still_done(self):
        result, stored, database = self.enrich(FakeGraph(enabled=False, reachable=False), listing_row(latitude_longitude=None))
        self.assertTrue(result)
        self.assertEqual(stored, [("9000000001", 0)])
        self.assertEqual(database.updates[0]["coordinates"], "")

    def test_a_pass_stops_at_the_first_graph_outage(self):
        database = FakeDatabase([listing_row(id=1, listing_id=1), listing_row(id=2, listing_id=2)])
        with patch.object(service, "postgres_client", database), patch.object(service, "enrich_row", return_value=False) as enrich:
            self.assertEqual(service.enrich_due_listings(), {"enriched": 0, "postponed": 1, "failed": 0})
        enrich.assert_called_once()


class GraphWriteTests(unittest.TestCase):
    def test_writes_run_in_one_transaction_with_the_timeout_on_the_unit_of_work(self):
        from neo4j import Query

        from src.search.graph_repository import Neo4jGraphRepository

        class FakeTransaction:
            """Rejects Query objects inside a transaction, as the real driver does."""

            def __init__(self):
                self.statements = []

            def run(self, query, parameters=None):
                if isinstance(query, Query):
                    raise ValueError("Query object is only supported for session.run")
                self.statements.append((query, parameters))

        class FakeSession:
            def __init__(self, transaction):
                self.transaction, self.timeout = transaction, None

            def __enter__(self):
                return self

            def __exit__(self, *details):
                return False

            def execute_write(self, function):
                self.timeout = getattr(function, "timeout", None)
                return function(self.transaction)

        transaction = FakeTransaction()
        session = FakeSession(transaction)
        repository = Neo4jGraphRepository()
        repository.enabled, repository.query_timeout = True, 7.5
        repository._driver = type("FakeDriver", (), {"session": lambda self, **_kwargs: session})()
        self.assertTrue(repository.write([("RETURN 1", {}), ("RETURN $value", {"value": 2})]))
        self.assertEqual(transaction.statements, [("RETURN 1", {}), ("RETURN $value", {"value": 2})])
        self.assertEqual(session.timeout, 7.5)


if __name__ == "__main__":
    unittest.main()

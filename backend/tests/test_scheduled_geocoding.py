"""The scheduled job that fills in coordinates of posted listings saved without them.

It must store only points in or near the listing's ward, remember addresses it cannot find so they
are not asked again, leave the rest for the next pass when the geocoder is down, and run in one
backend process at a time.
"""

import asyncio
import unittest
from unittest.mock import patch

import requests

from src.services.listing_submission import scheduled_geocoding
from src.services.listing_submission.scheduled_geocoding import (
    address_queries,
    configured_interval_seconds,
    fill_missing_coordinates,
    locate_listing,
    run_locked_pass,
)

# A point in Phường Cát Lái, and one in the old District 1, far from it.
IN_CAT_LAI = (10.77921234567, 106.7571)
IN_DISTRICT_ONE = (10.7769, 106.7009)


def submission(street="Đường Số 60", project="Căn hộ DEFINE", ward="Phường Cát Lái"):
    """A validated listing as stored in properties.submission, with only the fields the job reads."""
    metadata = {key: value for key, value in (("street", street), ("project", project)) if value is not None}
    return {"title": "Bán căn hộ", "ward": ward, "former_district": "Thành phố Thủ Đức", "metadata": metadata}


class FakeGeocoder:
    """Answers queries from a dict; raises a value that is an exception."""

    def __init__(self, answers):
        self.answers, self.queries = answers, []

    def search(self, query):
        self.queries.append(query)
        answer = self.answers.get(query)
        if isinstance(answer, Exception):
            raise answer
        return answer


class FakeDatabase:
    """Returns the given rows for the batch query and records every update."""

    def __init__(self, rows):
        self.rows, self.updates, self.batch_limits = rows, [], []

    def fetch_mappings(self, query, **parameters):
        self.batch_limits.append(parameters["limit"])
        return self.rows

    def execute_write(self, query, **parameters):
        self.updates.append(parameters)


def query(place):
    return f"{place}, Phường Cát Lái, Thành phố Hồ Chí Minh"


class LocateListingTests(unittest.TestCase):
    def test_the_street_is_tried_first_then_the_project(self):
        self.assertEqual(address_queries(submission(), "Phường Cát Lái"), [query("Đường Số 60"), query("Căn hộ DEFINE")])
        self.assertEqual(address_queries(submission(street=" ", project="Căn hộ DEFINE"), "Phường Cát Lái"), [query("Căn hộ DEFINE")])

    def test_a_point_in_the_ward_is_kept_rounded(self):
        geocoder = FakeGeocoder({query("Đường Số 60"): IN_CAT_LAI})
        self.assertEqual(locate_listing(submission(), geocoder), ("found", (10.7792123, 106.7571)))
        self.assertEqual(geocoder.queries, [query("Đường Số 60")])

    def test_a_point_away_from_the_ward_is_ignored_and_the_project_tried(self):
        geocoder = FakeGeocoder({query("Đường Số 60"): IN_DISTRICT_ONE, query("Căn hộ DEFINE"): IN_CAT_LAI})
        self.assertEqual(locate_listing(submission(), geocoder)[0], "found")
        only_away = FakeGeocoder({query("Đường Số 60"): IN_DISTRICT_ONE})
        self.assertEqual(locate_listing(submission(), only_away), ("outside_ward", None))

    def test_listings_that_cannot_be_looked_up_say_why(self):
        self.assertEqual(locate_listing(submission(), FakeGeocoder({})), ("not_found", None))
        self.assertEqual(locate_listing(submission(street=None, project=None), FakeGeocoder({})), ("no_address", None))
        self.assertEqual(locate_listing(submission(ward="Phường Không Có"), FakeGeocoder({})), ("unknown_ward", None))


class FillMissingCoordinatesTests(unittest.TestCase):
    def test_every_outcome_is_stored_so_no_address_is_asked_twice(self):
        database = FakeDatabase([
            {"id": 9000000001, "submission": submission()},
            {"id": 9000000002, "submission": '{"ward": "Phường Cát Lái", "metadata": {"street": "Đường Lạ"}}'},
        ])
        geocoder = FakeGeocoder({query("Đường Số 60"): IN_CAT_LAI})
        with patch.object(scheduled_geocoding, "postgres_client", database):
            outcomes = fill_missing_coordinates(geocoder, batch_size=50)
        self.assertEqual(outcomes, {"found": 1, "not_found": 1})
        self.assertEqual(database.batch_limits, [50])
        self.assertEqual(database.updates, [
            {"id": 9000000001, "status": "found", "coordinates": "10.7792123, 106.7571"},
            {"id": 9000000002, "status": "not_found", "coordinates": None},
        ])

    def test_a_geocoder_failure_leaves_the_rest_for_the_next_pass(self):
        database = FakeDatabase([
            {"id": 9000000001, "submission": submission()},
            {"id": 9000000002, "submission": submission(street="Đường Khác")},
            {"id": 9000000003, "submission": submission()},
        ])
        geocoder = FakeGeocoder({query("Đường Số 60"): IN_CAT_LAI, query("Đường Khác"): requests.ConnectionError("down")})
        with patch.object(scheduled_geocoding, "postgres_client", database):
            outcomes = fill_missing_coordinates(geocoder)
        self.assertEqual(outcomes, {"found": 1, "unavailable": 1})
        self.assertEqual([update["id"] for update in database.updates], [9000000001])


class FakeResult:
    def __init__(self, value):
        self.value = value

    def scalar(self):
        return self.value


class FakeConnection:
    """A SQLAlchemy 1.4 Connection (no commit()): records statements and execution options, and
    answers the lock query with whether the lock is free."""

    def __init__(self, is_lock_free):
        self.is_lock_free, self.statements, self.options = is_lock_free, [], {}

    def __enter__(self):
        return self

    def __exit__(self, *exception_details):
        return False

    def execution_options(self, **options):
        self.options.update(options)
        return self

    def execute(self, statement, parameters):
        self.statements.append(str(statement))
        return FakeResult(self.is_lock_free)


class FakeEngine:
    def __init__(self, connection):
        self.connection = connection

    def connect(self):
        return self.connection


class FakeClient:
    def __init__(self, connection):
        self.engine = FakeEngine(connection)


class LockedPassTests(unittest.TestCase):
    def test_a_pass_runs_only_with_the_lock_and_releases_it(self):
        connection = FakeConnection(is_lock_free=True)
        with patch.object(scheduled_geocoding, "postgres_client", FakeClient(connection)), \
                patch.object(scheduled_geocoding, "fill_missing_coordinates", return_value={"found": 2}) as fill:
            self.assertEqual(run_locked_pass(FakeGeocoder({})), {"found": 2})
        fill.assert_called_once()
        self.assertIn("pg_try_advisory_lock", connection.statements[0])
        self.assertIn("pg_advisory_unlock", connection.statements[-1])
        self.assertEqual(connection.options, {"isolation_level": "AUTOCOMMIT"})

    def test_another_process_holding_the_lock_skips_the_pass(self):
        connection = FakeConnection(is_lock_free=False)
        with patch.object(scheduled_geocoding, "postgres_client", FakeClient(connection)), \
                patch.object(scheduled_geocoding, "fill_missing_coordinates") as fill:
            self.assertIsNone(run_locked_pass(FakeGeocoder({})))
        fill.assert_not_called()
        self.assertEqual(len(connection.statements), 1)


class ScheduleTests(unittest.TestCase):
    def test_the_interval_comes_from_the_setting(self):
        for value, expected in (("", 300.0), ("60", 60.0), ("0", 0.0), ("soon", 300.0)):
            with patch.object(scheduled_geocoding, "config_value", return_value=value):
                self.assertEqual(configured_interval_seconds(), expected)

    def test_passes_repeat_survive_failures_and_stop_when_cancelled(self):
        calls = []

        def run_pass(geocoder):
            calls.append(geocoder)
            if len(calls) == 1:
                raise RuntimeError("database down")
            return {"found": 1}

        async def run_for_a_while():
            with patch.object(scheduled_geocoding, "run_locked_pass", run_pass):
                task = asyncio.create_task(scheduled_geocoding.fill_missing_coordinates_periodically(0.01, "geocoder"))
                while len(calls) < 3:
                    await asyncio.sleep(0.01)
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task

        asyncio.run(run_for_a_while())
        self.assertGreaterEqual(len(calls), 3)

    def test_a_zero_interval_turns_the_job_off(self):
        async def start_and_stop():
            job = scheduled_geocoding.ScheduledGeocoding()
            with patch.object(scheduled_geocoding, "config_value", return_value="0"):
                job.start()
            self.assertIsNone(job._task)
            await job.stop()

        asyncio.run(start_and_stop())


if __name__ == "__main__":
    unittest.main()

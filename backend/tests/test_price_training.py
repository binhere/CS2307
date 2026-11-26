"""Training the price model on the server: preparing listings, judging a new model against the
served one, the daily schedule, and the training API.

A new model must replace the served one only when it is better on the fixed holdout set; missing
bedrooms and coordinates are filled in rather than dropped; the schedule trains only with enough
new data, once per day; and only one run may go at a time.
"""

import asyncio
import io
import json
import random
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

import httpx
import joblib
import numpy as np
import pandas as pd

from src.services.price_training import preparation, service
from src.services.price_training.preparation import feature_frame, feature_names_for, prepare_training_data
from src.services.price_training.service import ServedModel, choose_holdout, execute_run, latest_slot
from src.services.price_training.trainer import build_report, fit_candidates

WARDS = {"Phường Cát Lái": 1.0, "Phường Bến Thành": 2.2, "Phường Tân Hưng": 1.3}


def listing_rows(count=400, seed=7):
    """Plausible listings whose price grows with area and depends on the ward."""
    generator = random.Random(seed)
    rows = []
    for index in range(count):
        ward = generator.choice(list(WARDS))
        is_flat = index % 3 != 0
        area = generator.uniform(45, 120) if is_flat else generator.uniform(40, 200)
        bedrooms = max(1, min(4, round(area / 30)))
        price = area * (0.06 if is_flat else 0.09) * WARDS[ward] * generator.uniform(0.9, 1.1)
        rows.append({
            "listing_id": 1000 + index, "source": "catalog" if index % 5 else "user",
            "property_type": "Căn hộ chung cư" if is_flat else "Nhà riêng", "area": round(area, 1),
            "bedrooms": bedrooms, "latitude_longitude": f"{10.75 + generator.random() / 50}, {106.70 + generator.random() / 50}",
            "district": ward, "legal_status": "Sổ hồng riêng", "price_range": round(price, 2),
        })
    return rows


class PreparationTests(unittest.TestCase):
    def test_rows_that_cannot_be_priced_are_dropped_and_gaps_filled(self):
        good = {"listing_id": 1, "source": "catalog", "property_type": "Căn hộ chung cư", "area": 70, "bedrooms": 2,
                "latitude_longitude": "10.76, 106.77", "district": "Phường Cát Lái", "legal_status": None, "price_range": 4.0}
        rows = [
            good,
            {**good, "listing_id": 1},  # duplicate
            {**good, "listing_id": 2, "price_range": None},
            {**good, "listing_id": 3, "district": ""},
            {**good, "listing_id": 4, "bedrooms": None},
            {**good, "listing_id": 5, "latitude_longitude": None},
            {**good, "listing_id": 6, "latitude_longitude": "21.0, 105.8"},  # Hà Nội: not in the city
            {**good, "listing_id": 7, "latitude_longitude": None, "district": "Huyện Không Có"},
            {**good, "listing_id": 8, "price_range": 90.0},  # outlier for a flat
        ]
        prepared = prepare_training_data(rows)
        frame = prepared.frame.set_index("listing_id")
        self.assertEqual(sorted(frame.index), [1, 4, 5, 6])
        self.assertEqual(dict(prepared.dropped), {"duplicate": 1, "missing_price_or_area": 1, "missing_type_or_ward": 1,
                                                  "no_location": 1, "outlier": 1})
        self.assertEqual(prepared.step_rows, [9, 6, 5, 4])
        self.assertTrue(frame.loc[4, "is_bedrooms_imputed"])
        self.assertEqual(frame.loc[4, "bedrooms"], 2)
        ward_point = preparation.ward_point("Phường Cát Lái")
        self.assertEqual((frame.loc[5, "latitude"], frame.loc[5, "longitude"]), ward_point)
        self.assertEqual((frame.loc[6, "latitude"], frame.loc[6, "longitude"]), ward_point)
        self.assertEqual(frame.loc[1, "property_type"], "Căn hộ")
        self.assertEqual(frame.loc[1, "legal_status"], "Không rõ")

    def test_bedrooms_are_filled_from_listings_of_the_same_group_and_size(self):
        imputation = preparation.learn_imputation([("Căn hộ", 50, 1), ("Căn hộ", 55, 1), ("Căn hộ", 90, 3), ("Nhà đất", 90, 4)])
        self.assertEqual(preparation.impute_bedrooms("Căn hộ", 52, imputation), 1)
        self.assertEqual(preparation.impute_bedrooms("Căn hộ", 95, imputation), 3)
        self.assertEqual(preparation.impute_bedrooms("Nhà đất", 30, imputation), 4)  # no band: the group's median

    def test_rows_are_encoded_with_the_model_columns(self):
        frame = pd.DataFrame([{"property_type": "Căn hộ", "area": 60.0, "bedrooms": 2, "latitude": 10.7, "longitude": 106.7,
                               "district": "Phường Mới", "legal_status": "Sổ chung"}])
        names = ["area", "bedrooms", "area_per_bedroom", "latitude", "longitude", "property_type_Căn hộ",
                 "legal_status_Sổ chung", "district_Phường Cát Lái"]
        features = feature_frame(frame, names)
        self.assertEqual(list(features.columns), names)
        self.assertEqual(features.iloc[0].tolist(), [60.0, 2.0, 30.0, 10.7, 106.7, 1.0, 1.0, 0.0])
        self.assertEqual(feature_names_for(frame)[-1], "district_Phường Mới")


class ConstantModel:
    """A served model that answers one price for every listing."""

    def __init__(self, price):
        self.value = float(np.log1p(price))

    def predict(self, features):
        return np.full(len(features), self.value)


class FakeRepository:
    """In-memory runs, holdout and listing rows."""

    def __init__(self, rows, holdout=()):
        self.rows, self.holdout, self.finished, self.started = rows, list(holdout), [], []
        self.is_busy, self.has_run_today = False, False

    def fetch_training_rows(self):
        return self.rows

    def fetch_holdout_ids(self):
        return self.holdout

    def replace_holdout_ids(self, listing_ids):
        self.holdout = sorted(listing_ids)

    def finish_run(self, run_id, status, reason, summary, report=None, model=None):
        self.finished.append({"id": run_id, "status": status, "reason": reason, "summary": summary, "report": report, "model": model})

    def start_run(self, trigger, user_id, user_name):
        if self.is_busy:
            return None
        self.started.append((trigger, user_id, user_name))
        return len(self.started)

    def fail_stale_runs(self, minutes):
        return 0

    def has_scheduled_run_since(self, moment):
        return self.has_run_today


class ExecuteRunTests(unittest.TestCase):
    def setUp(self):
        self.rows = listing_rows()
        prepared = prepare_training_data(self.rows)
        self.feature_names = feature_names_for(prepared.frame)
        # The served model was tested on the first fifth of the catalog and trained on the rest.
        catalog = prepared.frame[prepared.frame["source"] == "catalog"]["listing_id"].tolist()
        self.report = {"test_predictions": [[listing_id, "Căn hộ", 1.0, 1.0] for listing_id in catalog[:60]],
                       "train_listing_ids": catalog[60:]}

    def run_with(self, champion_price, trigger="manual", report=None):
        repository = FakeRepository(self.rows)
        served = ServedModel({"model_name": "Constant", "model": ConstantModel(champion_price), "feature_names": self.feature_names},
                             report if report is not None else self.report, None)
        with patch.object(service, "repository", repository):
            status = execute_run(1, trigger, served)
        return status, repository

    def test_a_better_model_is_deployed_with_its_bytes_and_report(self):
        status, repository = self.run_with(champion_price=50.0)
        run = repository.finished[0]
        self.assertEqual((status, run["reason"]), ("deployed", None))
        self.assertGreater(run["summary"]["challenger"]["r2"], run["summary"]["champion"]["r2"])
        bundle = joblib.load(io.BytesIO(run["model"]))
        self.assertIn("imputation", bundle)
        self.assertEqual(run["report"]["composition"]["test_rows"], 60)  # the served model's test rows
        self.assertEqual(sorted(repository.holdout), sorted(self.report["test_predictions"][index][0] for index in range(60)))
        self.assertTrue(set(run["report"]["seen_listing_ids"]).isdisjoint(repository.holdout))
        self.assertGreater(run["summary"]["peak_memory_mb"], 0)

    def test_a_model_no_better_than_the_served_one_is_rejected(self):
        class Oracle:
            """Answers each holdout listing's own price: unbeatable."""
            def predict(self, features):
                key = features[["area", "latitude"]].round(6).apply(tuple, axis=1)
                return np.log1p(key.map(prices).to_numpy())
        prepared = prepare_training_data(self.rows).frame
        prices = dict(zip(prepared[["area", "latitude"]].round(6).apply(tuple, axis=1), prepared["price"]))
        repository = FakeRepository(self.rows)
        served = ServedModel({"model_name": "Oracle", "model": Oracle(), "feature_names": self.feature_names}, self.report, 3)
        with patch.object(service, "repository", repository):
            self.assertEqual(execute_run(1, "manual", served), "rejected")
        run = repository.finished[0]
        self.assertEqual((run["reason"], run["model"]), ("not_better", None))
        self.assertIsNotNone(run["report"])  # kept for the history, not served
        self.assertEqual(run["summary"]["champion_run_id"], 3)

    def test_the_schedule_skips_without_enough_new_data_and_the_button_does_not(self):
        everything = {"seen_listing_ids": [row["listing_id"] for row in self.rows], **self.report}
        status, repository = self.run_with(50.0, trigger="schedule", report=everything)
        self.assertEqual((status, repository.finished[0]["reason"]), ("skipped", "not_enough_new_data"))
        self.assertEqual(repository.finished[0]["summary"]["new_rows"], 0)
        self.assertEqual(self.run_with(50.0, trigger="manual", report=everything)[0], "deployed")

    def test_the_holdout_is_chosen_again_when_a_catalog_update_removed_its_listings(self):
        repository = FakeRepository(self.rows, holdout=[999_999])  # listings no longer in the catalog
        served = ServedModel({"model_name": "Constant", "model": ConstantModel(50.0), "feature_names": self.feature_names},
                             self.report, None)
        with patch.object(service, "repository", repository):
            self.assertEqual(execute_run(1, "manual", served), "deployed")
        self.assertEqual(len(repository.holdout), 60)
        self.assertTrue(repository.finished[0]["summary"]["is_holdout_new"])

    def test_a_failure_closes_the_run(self):
        repository = FakeRepository(self.rows)
        repository.fetch_training_rows = lambda: (_ for _ in ()).throw(RuntimeError("boom"))
        with patch.object(service, "repository", repository):
            status = execute_run(1, "manual", ServedModel({}, None, None))
        self.assertEqual((status, repository.finished[0]["reason"], repository.finished[0]["summary"]["error"]),
                         ("failed", "error", "RuntimeError"))


class HoldoutTests(unittest.TestCase):
    def test_the_holdout_prefers_complete_rows_the_served_model_was_tested_on(self):
        frame = prepare_training_data(listing_rows()).frame
        catalog = frame[frame["source"] == "catalog"]["listing_id"].tolist()
        report = {"test_predictions": [[listing_id] for listing_id in catalog[:70]], "train_listing_ids": catalog[70:]}
        self.assertEqual(choose_holdout(frame, report), set(catalog[:70]))
        # Too few tested rows left: a random share of rows it was not trained on.
        fallback = choose_holdout(frame, {"test_predictions": [], "train_listing_ids": catalog[:100]})
        self.assertTrue(fallback.isdisjoint(catalog[:100]))
        self.assertGreaterEqual(len(fallback), service.MINIMUM_HOLDOUT_ROWS)


class ScheduleTests(unittest.TestCase):
    def test_the_slot_is_the_latest_two_oclock_in_vietnam(self):
        before = datetime(2026, 11, 26, 18, 30, tzinfo=timezone.utc)  # 01:30 on 27 November in Vietnam
        after = datetime(2026, 11, 26, 19, 30, tzinfo=timezone.utc)  # 02:30
        self.assertEqual(latest_slot(before).isoformat(), "2026-11-26T02:00:00+07:00")
        self.assertEqual(latest_slot(after).isoformat(), "2026-11-27T02:00:00+07:00")

    def test_the_day_runs_once_and_never_beside_another_run(self):
        repository = FakeRepository(listing_rows())
        with patch.object(service, "repository", repository), patch.object(service, "execute_run", return_value="skipped") as run:
            repository.has_run_today = True
            self.assertIsNone(service.run_scheduled_if_due(lambda: None))
            repository.has_run_today, repository.is_busy = False, True
            self.assertIsNone(service.run_scheduled_if_due(lambda: None))
            repository.is_busy = False
            self.assertEqual(service.run_scheduled_if_due(lambda: "served"), "skipped")
        run.assert_called_once_with(1, "schedule", "served")

    def test_a_zero_interval_turns_the_schedule_off(self):
        async def start_and_stop():
            schedule = service.ScheduledTraining()
            with patch.object(service, "config_value", return_value="0"):
                schedule.start(lambda: None)
            self.assertIsNone(schedule._task)
            await schedule.stop()

        asyncio.run(start_and_stop())


class TrainerTests(unittest.TestCase):
    def test_three_candidates_are_compared_and_the_report_has_the_dashboard_shape(self):
        frame = prepare_training_data(listing_rows()).frame
        holdout, pool = frame.iloc[:50], frame.iloc[50:]
        name, model, feature_names, benchmark, training = fit_candidates(pool)
        self.assertEqual({row["model"] for row in benchmark}, {"Ridge Regression", "Random Forest", "HistGradientBoosting"})
        self.assertEqual(name, max(benchmark, key=lambda row: row["r2"])["model"])
        self.assertEqual(len(training), len(pool))  # the chosen candidate is fitted on the whole pool
        report = build_report({"model_name": name, "model": model, "feature_names": feature_names}, training, holdout, frame, [1, 2, 3, 4], benchmark)
        for key in ("model", "composition", "benchmark", "segments", "test_predictions", "train_listing_ids", "reference"):
            self.assertIn(key, report)
        self.assertEqual(len(report["test_predictions"]), 50)
        self.assertEqual(report["composition"]["train_rows"], len(report["train_listing_ids"]))
        json.dumps(report)


class ApiTests(unittest.TestCase):
    def send(self, method, path, user=None):
        from src.main import app
        from src.services.auth.dependencies import get_current_user

        async def request():
            if user is not None:
                app.dependency_overrides[get_current_user] = lambda: user
            try:
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                    return await client.request(method, path)
            finally:
                app.dependency_overrides.pop(get_current_user, None)

        return asyncio.run(request())

    def test_only_signed_in_users_start_a_run_and_a_second_one_is_told_to_wait(self):
        from src.api.v1.endpoints.models import view_models

        self.assertEqual(self.send("POST", "/v1/models/price/train").status_code, 401)
        user = {"id": 7, "username": "lan", "display_name": "Lan"}
        with patch.object(view_models.service, "start_manual_run", return_value=None):
            busy = self.send("POST", "/v1/models/price/train", user)
        self.assertEqual((busy.status_code, busy.json()["errors"][0]["msg"]), (409, "TRAINING_IN_PROGRESS"))
        with patch.object(view_models.service, "start_manual_run", return_value=12), \
                patch.object(view_models, "run_and_serve") as background:
            started = self.send("POST", "/v1/models/price/train", user)
        self.assertEqual((started.status_code, started.json()["data"]), (202, {"run_id": 12}))
        background.assert_called_once_with(12)

    def test_the_history_is_public(self):
        from src.api.v1.endpoints.models import view_models

        runs = [{"id": 2, "status": "running"}, {"id": 1, "status": "deployed"}]
        with patch.object(view_models.repository, "list_runs", return_value=runs):
            body = self.send("GET", "/v1/models/price/runs?limit=5").json()
        self.assertEqual((body["data"]["runs"], body["data"]["is_running"], body["data"]["schedule_hour"]), (runs, True, 2))


if __name__ == "__main__":
    unittest.main()

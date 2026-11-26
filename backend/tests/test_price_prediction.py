import io
import unittest
from unittest.mock import patch

import joblib
from sqlalchemy.exc import OperationalError

from src.services.inference import price_prediction
from src.services.inference.price_prediction import (
    PriceModelUnavailableError,
    PricePredictionService,
    normalize_legal_status,
)
from src.services.price_training.service import ServedModel
from tests.price_model_fixture import fixture_served_model, price_service_with_fixture

LISTING = {
    "property_type": "Căn hộ chung cư",
    "district": "Phường Tân Hưng",
    "area": 70.0,
    "bedrooms": 2,
    "price_range": 4.0,
    "legal_status": "Sổ đỏ/ Sổ hồng.",
    "latitude_longitude": "q=10.745,106.705",
}


class LegalStatusTests(unittest.TestCase):
    def test_maps_raw_labels(self):
        cases = {
            "Sổ đỏ/ Sổ hồng.": "Sổ hồng riêng",
            "Sổ đỏ/ Sổ hồng ( sử dụng chung)": "Sổ chung",
            "HĐMB.": "Hợp đồng mua bán",
            "Đang chờ sổ, HĐMB": "Đang chờ sổ",
            None: "Không rõ",
        }
        for raw, expected in cases.items():
            self.assertEqual(normalize_legal_status(raw), expected, raw)


class EstimateListingTests(unittest.TestCase):
    def setUp(self):
        self.service = price_service_with_fixture()

    def test_scores_listing_and_classifies_verdict(self):
        result = self.service.estimate_listing(LISTING)
        self.assertEqual(result["property_type"], "Căn hộ")
        self.assertEqual(result["asking_price"], 4.0)
        self.assertIn(result["verdict"], {"below", "within", "above"})
        cheap = self.service.estimate_listing({**LISTING, "price_range": 1.5})
        dear = self.service.estimate_listing({**LISTING, "price_range": 12.0})
        self.assertEqual((cheap["verdict"], dear["verdict"]), ("below", "above"))

    def test_rejects_listing_outside_training_domain(self):
        for change in (
            {"area": 900.0},
            {"bedrooms": 9},
            {"price_range": 32.1},
            {"district": "Phường Không Tồn Tại"},
            # No coordinates and no official ward to take a point from.
            {"latitude_longitude": None, "district": "Phường Không Tồn Tại"},
        ):
            with self.assertRaises(ValueError, msg=change) as ctx:
                self.service.estimate_listing({**LISTING, **change})
            self.assertTrue(str(ctx.exception), change)

    def test_missing_or_far_coordinates_are_taken_from_the_ward(self):
        ward_point = self.service.assess_listing({**LISTING, "latitude_longitude": None}).inputs
        far_away = self.service.assess_listing({**LISTING, "latitude_longitude": "q=21.0,105.8"}).inputs
        self.assertEqual((ward_point["latitude"], ward_point["longitude"]), (far_away["latitude"], far_away["longitude"]))
        self.assertTrue(10.3 <= ward_point["latitude"] <= 11.6)

    def test_missing_bedrooms_are_filled_only_by_a_model_that_learnt_how(self):
        served = fixture_served_model()
        self.assertGreaterEqual(self.service.assess_listing({**LISTING, "bedrooms": None}).inputs["bedrooms"], 1)
        without = {key: value for key, value in served.bundle.items() if key != "imputation"}
        unlearnt = price_service_with_fixture(ServedModel(without, served.report, served.run_id))
        self.assertEqual(unlearnt.assess_listing({**LISTING, "bedrooms": None}).reason_code, "missing_fields")


class FakeTrainingRepository:
    """Answers which run is deployed and returns its bundle; raises when told the database is down."""

    def __init__(self, bundle):
        self.bundle, self.active_id, self.is_down, self.fetches = bundle, None, False, 0

    def active_model_id(self):
        if self.is_down:
            raise OperationalError("SELECT", {}, Exception("down"))
        return self.active_id

    def fetch_model(self, run_id):
        self.fetches += 1
        buffer = io.BytesIO()
        joblib.dump({**self.bundle, "model_name": f"run {run_id}"}, buffer)
        return {"model": buffer.getvalue(), "report": {"run": run_id}}


class ServedModelTests(unittest.TestCase):
    def setUp(self):
        self.repository = FakeTrainingRepository(fixture_served_model().bundle)

    def test_nothing_is_served_until_a_run_deploys_a_model(self):
        service = PricePredictionService(model_check_seconds=0)
        with patch.object(price_prediction, "training_repository", self.repository):
            with self.assertRaises(PriceModelUnavailableError) as raised:
                service.served_model()
            self.assertIn("Chưa có mô hình định giá", str(raised.exception))
            self.assertIsNone(service.served_model_or_none())
            self.repository.active_id = 7
            served = service.served_model()
            self.assertEqual((served.run_id, served.bundle["model_name"], served.report), (7, "run 7", {"run": 7}))
            service.served_model()
            self.assertEqual(self.repository.fetches, 1)  # the same run is not loaded twice
            self.repository.is_down = True
            self.assertEqual(service.served_model().run_id, 7)  # a database error keeps the model held
            self.repository.is_down, self.repository.active_id = False, None
            self.assertIsNone(service.served_model_or_none())  # runs wiped: the model goes too

    def test_a_newer_model_is_looked_for_only_after_the_check_interval(self):
        service = PricePredictionService(model_check_seconds=3600)
        self.repository.active_id = 7
        with patch.object(price_prediction, "training_repository", self.repository):
            service.served_model()
            self.repository.active_id = 8
            self.assertEqual(service.served_model().run_id, 7)
            self.assertEqual(service.served_model(is_forced=True).run_id, 8)

    def test_pricing_without_a_model_says_so(self):
        service = PricePredictionService(model_check_seconds=0)
        with patch.object(price_prediction, "training_repository", self.repository):
            with self.assertRaises(PriceModelUnavailableError):
                service.estimate_listing(LISTING)


class WarmUpTests(unittest.TestCase):
    def test_warm_up_loads_the_model_once(self):
        repository = FakeTrainingRepository(fixture_served_model().bundle)
        repository.active_id = 3
        service = PricePredictionService()
        with patch.object(price_prediction, "training_repository", repository):
            self.assertTrue(service.warm_up())
            served = service._served
            service.warm_up()
        self.assertIs(service._served, served)

    def test_no_model_yet_does_not_break_startup(self):
        service = PricePredictionService()
        with patch.object(price_prediction, "training_repository", FakeTrainingRepository({})):
            self.assertFalse(service.warm_up())


if __name__ == "__main__":
    unittest.main()

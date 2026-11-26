"""Data dashboard: statistics, the snapshot sections and the public API.

The dashboard exists because the price model was trained on rows the site also shows: these
tests pin the numbers that reveal it (training overlap, verdicts per group, drift) as well as
the data-quality checks.
"""

import asyncio
import json
import unittest
from datetime import date
from unittest.mock import patch

import joblib
from sqlalchemy.exc import OperationalError

from src.api.v1.endpoints.monitoring import view_monitoring
from src.services.monitoring import service, statistics


def listing(**values):
    """A catalog row with plausible defaults, overridden by values."""
    row = {
        "id": 1, "listing_id": 1001, "title": "Căn hộ 2 phòng ngủ", "property_type": "Căn hộ chung cư",
        "district": "Phường Thạnh Mỹ Tây", "price_range": 4.0, "area": 70.0, "bedrooms": 2, "bathrooms": 2,
        "latitude_longitude": "10.81, 106.71", "legal_status": "Sổ hồng riêng", "furnishing": "Đầy đủ",
        "house_direction": None, "posted_date": date(2026, 9, 20), "image_count": 8,
        "description_length": 900, "has_embedding": True,
    }
    row.update(values)
    return row


class StatisticsTests(unittest.TestCase):
    def test_identical_distributions_have_no_drift_and_a_shift_has_some(self):
        self.assertEqual(statistics.population_stability_index([0.5, 0.5], [0.5, 0.5]), 0.0)
        shifted = statistics.population_stability_index([0.5, 0.5], [0.9, 0.1])
        self.assertGreater(shifted, statistics.PSI_MAJOR)
        self.assertEqual(statistics.drift_status(shifted), "major")
        self.assertEqual(statistics.drift_status(0.05), "stable")
        self.assertEqual(statistics.drift_status(0.2), "moderate")

    def test_numeric_drift_bins_follow_reference_deciles(self):
        reference = [float(value) for value in range(1, 101)]
        same = statistics.numeric_drift(reference, reference)
        self.assertEqual(len(same["bins"]), 10)
        self.assertAlmostEqual(sum(item["reference"] for item in same["bins"]), 1.0, places=3)
        self.assertEqual(same["status"], "stable")
        higher = statistics.numeric_drift(reference, [value + 60 for value in reference])
        self.assertEqual(higher["status"], "major")

    def test_categorical_drift_adds_other_only_when_needed(self):
        reference = {"Căn hộ": 30, "Nhà đất": 70}
        self.assertEqual([item["label"] for item in statistics.categorical_drift(reference, {"Căn hộ": 50})["bins"]],
                         ["Nhà đất", "Căn hộ"])
        with_unknown = statistics.categorical_drift(reference, {"Căn hộ": 50, "Shophouse": 5})
        self.assertEqual(with_unknown["bins"][-1]["label"], "Khác")
        self.assertGreater(with_unknown["bins"][-1]["current"], 0)

    def test_histogram_box_plot_and_robust_outliers(self):
        histogram = statistics.fixed_histogram([0.5, 1.5, 2.5, 60], [1, 2, 50])
        self.assertEqual([item["count"] for item in histogram], [1, 1, 1, 1])
        self.assertEqual(histogram[0]["label"], "≤ 1")
        self.assertEqual(histogram[-1]["label"], "> 50")
        box = statistics.box_statistics([10, 11, 12, 13, 14, 100])
        self.assertEqual(box["outliers"], 1)
        self.assertEqual(box["high"], 14)
        scores = statistics.modified_z_scores([50, 52, 48, 51, 49, 500])
        self.assertGreater(scores[-1], statistics.MODIFIED_Z_THRESHOLD)
        self.assertLess(abs(scores[0]), statistics.MODIFIED_Z_THRESHOLD)


class SectionTests(unittest.TestCase):
    def test_quality_counts_missing_invalid_duplicate_and_future_values(self):
        rows = [
            listing(),
            listing(id=2, listing_id=1002, price_range=None, latitude_longitude="", posted_date=date(2030, 1, 1)),
            listing(id=3, listing_id=1001, area=-5, image_count=0, has_embedding=False),
        ]
        quality = service.quality_section(rows, today=date(2026, 11, 26))
        fields = {item["field"]: item for item in quality["fields"]}
        self.assertEqual(fields["price_range"]["missing"], 1)
        self.assertEqual(fields["area"]["invalid"], 1)
        self.assertEqual(fields["coordinates"]["missing"], 1)
        self.assertEqual(fields["posted_date"]["invalid"], 1)
        self.assertEqual(fields["images"]["missing"], 1)
        self.assertEqual(fields["embedding"]["missing"], 1)
        self.assertEqual(quality["duplicate_listings"], 1)

    def test_a_listing_priced_like_a_monthly_rent_is_flagged(self):
        rows = [listing(id=index, listing_id=index, price_range=4.0 + index / 10) for index in range(1, 30)]
        rows.append(listing(id=99, listing_id=99, price_range=0.03, title="Cho thuê 30 triệu/tháng"))
        flagged = service.suspicious_prices(rows)
        self.assertEqual(flagged["count"], 1)
        self.assertEqual(flagged["listings"][0]["id"], 99)
        self.assertLess(flagged["listings"][0]["score"], 0)

    def test_drift_compares_the_catalog_with_the_training_reference(self):
        report = {"reference": {
            "rows": 4,
            "numeric": {"price": [3, 4, 5, 6], "area": [60, 70, 80, 90], "bedrooms": [1, 2, 2, 3], "price_per_m2": [50, 57, 62, 66]},
            "categorical": {"property_group": {"Căn hộ": 4}, "legal_status": {"Sổ hồng riêng": 4}, "ward": {"Phường Thạnh Mỹ Tây": 4}},
        }}
        drift = service.drift_section([listing(property_type="Nhà riêng"), listing()], report)
        features = {item["feature"]: item for item in drift["features"]}
        self.assertEqual(set(features), {"price", "area", "bedrooms", "price_per_m2", "property_group", "legal_status", "ward"})
        self.assertEqual(features["property_group"]["status"], "major")
        self.assertIn("Nhà đất", [item["label"] for item in features["property_group"]["bins"]] + ["Nhà đất"])

    def test_model_section_measures_interval_coverage_per_segment(self):
        report = {
            "model": {"name": "HistGradientBoosting"}, "generated_at": "2026-11-26T00:00:00+00:00",
            "composition": {}, "benchmark": [],
            "segments": [{"segment": "all", "count": 3}, {"segment": "Căn hộ", "count": 2}, {"segment": "Nhà đất", "count": 1}],
            "test_predictions": [[1, "Căn hộ", 5.0, 5.2], [2, "Căn hộ", 5.0, 7.0], [3, "Nhà đất", 10.0, 11.5]],
        }
        model = service.model_section(report)
        coverage = {row["segment"]: row["interval_coverage"] for row in model["segments"]}
        self.assertEqual(coverage, {"all": 66.7, "Căn hộ": 50.0, "Nhà đất": 100.0})
        self.assertEqual(len(model["scatter"]), 3)
        self.assertEqual(sum(row["Căn hộ"] for row in model["error_histogram"]), 2)

    def test_predictions_split_verdicts_by_training_membership(self):
        rows = [listing(listing_id=1), listing(listing_id=2, price_range=40.0), listing(listing_id=3, latitude_longitude="")]
        predictions = service.prediction_section(rows, train_ids={1})
        groups = {item["group"]: item for item in predictions["verdicts"]}
        self.assertEqual(groups["in_training"]["total"], 1)
        self.assertEqual(groups["not_in_training"]["total"], 2)
        self.assertEqual(groups["not_in_training"]["refused"], 2)
        self.assertEqual({item["code"] for item in predictions["refused_reasons"]}, {"missing_fields", "price_range"})
        self.assertEqual(predictions["scored"], 1)


class SnapshotTests(unittest.TestCase):
    def test_the_shipped_report_describes_the_shipped_model(self):
        report = service.load_training_report()
        bundle = joblib.load(service.REPORT_FILE.with_name("best_model.joblib"))
        self.assertEqual(report["model"]["name"], bundle["model_name"])
        self.assertAlmostEqual(report["segments"][0]["r2"], float(bundle["metrics_overall"]["R2 Score"]), places=4)
        self.assertEqual(len(report["train_listing_ids"]), report["composition"]["train_rows"])

    def test_snapshot_has_every_section_without_graph_or_image_store(self):
        rows = [listing(id=index, listing_id=index) for index in range(1, 6)]
        with patch.object(service.repository, "fetch_catalog_rows", return_value=rows), \
                patch.object(service.repository, "fetch_catalog_import", return_value=None), \
                patch.object(service.graph_repository, "count_listing_nodes", return_value=None), \
                patch.object(service.minio_client, "count_objects", return_value=None):
            snapshot = service.build_snapshot(today=date(2026, 11, 26))
        self.assertEqual(snapshot["kpis"]["listings"], 5)
        self.assertIsNone(snapshot["kpis"]["graph_listings"])
        for section in ("quality", "distributions", "drift", "model", "predictions"):
            self.assertIsNotNone(snapshot[section], section)
        json.dumps(snapshot)


class ApiTests(unittest.TestCase):
    def test_snapshot_is_returned_in_the_envelope(self):
        with patch.object(view_monitoring.monitoring_service, "snapshot", return_value={"kpis": {"listings": 3}}):
            body = asyncio.run(view_monitoring.view_monitoring())
        self.assertEqual(body["status"], "success")
        self.assertEqual(body["data"]["kpis"]["listings"], 3)

    def test_database_failure_is_a_503_without_details(self):
        failure = OperationalError("SELECT", {}, Exception("connection to db.internal:5432 refused"))
        with patch.object(view_monitoring.monitoring_service, "snapshot", side_effect=failure):
            response = asyncio.run(view_monitoring.view_monitoring())
        self.assertEqual(response.status_code, 503)
        self.assertNotIn(b"db.internal", response.body)


if __name__ == "__main__":
    unittest.main()

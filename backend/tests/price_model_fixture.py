"""A small price model trained on synthetic listings, for tests.

No model ships in the image any more (the backend trains its own from posted listings), so tests
that price listings train this one once per test process: four wards, flats and houses whose price
grows with area and depends on the ward, coordinates inside each ward.
"""

import random
import time
from functools import lru_cache

from src.services.inference.price_prediction import PricePredictionService
from src.services.price_training.preparation import prepare_training_data, ward_point
from src.services.price_training.service import ServedModel
from src.services.price_training.trainer import build_report, fit_candidates

WARD_PRICE_FACTORS = {"Phường Tân Hưng": 1.0, "Phường Thạnh Mỹ Tây": 1.4, "Phường Cát Lái": 0.9, "Phường Bến Thành": 2.0}
HOLDOUT_ROWS = 60


def synthetic_rows(count: int = 400, seed: int = 11) -> list:
    """Plausible properties rows: about 4.2 billion VND for a 70 m² two-bedroom flat in Tân Hưng.

    Args:
        count: How many listings.
        seed: Random seed, so every test process trains the same model.

    Returns:
        list: Dicts with the columns training reads.
    """
    generator = random.Random(seed)
    rows = []
    for index in range(count):
        ward = list(WARD_PRICE_FACTORS)[index % len(WARD_PRICE_FACTORS)]
        is_flat = index % 3 != 0
        area = generator.uniform(45, 120) if is_flat else generator.uniform(40, 200)
        price = area * (0.06 if is_flat else 0.09) * WARD_PRICE_FACTORS[ward] * generator.uniform(0.92, 1.08)
        latitude, longitude = ward_point(ward)
        rows.append({
            "listing_id": 1000 + index, "source": "user", "property_type": "Căn hộ chung cư" if is_flat else "Nhà riêng",
            "area": round(area, 1), "bedrooms": max(1, min(4, round(area / 30))),
            "latitude_longitude": f"{latitude + generator.uniform(-0.002, 0.002)}, {longitude + generator.uniform(-0.002, 0.002)}",
            "district": ward, "legal_status": "Sổ hồng riêng", "price_range": round(price, 2),
        })
    return rows


@lru_cache(maxsize=1)
def fixture_served_model() -> ServedModel:
    """The trained model with its report, as a deployed training run would serve it (run 1)."""
    prepared = prepare_training_data(synthetic_rows())
    holdout, pool = prepared.frame.iloc[:HOLDOUT_ROWS], prepared.frame.iloc[HOLDOUT_ROWS:]
    name, model, feature_names, benchmark, training = fit_candidates(pool)
    bundle = {"model_name": name, "model": model, "feature_names": feature_names, "imputation": prepared.imputation}
    report = build_report(bundle, training, holdout, prepared.frame, prepared.step_rows, benchmark)
    return ServedModel(bundle, report, 1)


def price_service_with_fixture(served: ServedModel = None) -> PricePredictionService:
    """A pricing service already holding a model, which it will not look to replace during a test."""
    service = PricePredictionService(model_check_seconds=3600)
    service._served = served or fixture_served_model()
    service._checked_at = time.monotonic()
    return service

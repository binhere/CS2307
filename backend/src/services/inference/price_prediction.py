"""Price prediction inference service.

Serves the latest model a training run deployed (price_model_runs, see
src/services/price_training), checking for a newer one every MODEL_CHECK_SECONDS. No model ships in
the image: listings come only from users, so until the first training run on their listings
deploys a model, pricing answers PriceModelUnavailableError. Listings are encoded by the same code
that prepares training data, and missing bedrooms and coordinates are filled in the way training
filled them. Estimates come with a confidence band per property group.
"""

from __future__ import annotations

import io
import time
from dataclasses import dataclass
from threading import Lock
from typing import Any, Dict, List, Optional, Tuple

import joblib
import numpy as np
import pandas as pd
from loguru import logger

from src.services.price_training import repository as training_repository
from src.services.price_training.preparation import (
    OUTLIER_RULES,
    feature_frame,
    impute_bedrooms,
    is_inside_city,
    normalize_legal_status,
    parse_coordinates,
    property_group,
    ward_point,
)
from src.services.price_training.service import ServedModel

MODEL_CHECK_SECONDS = 60.0
NO_MODEL_MESSAGE = "Chưa có mô hình định giá. Mô hình đầu tiên sẽ được huấn luyện khi có đủ tin đăng."
# Re-exported for the dashboard, which reads these from here.
__all__ = ["is_inside_city", "normalize_legal_status", "parse_coordinates", "price_bounds", "price_verdict",
           "price_prediction_service"]


class PriceModelUnavailableError(ValueError):
    """No model has been deployed yet (or the database holding it cannot be read).

    A ValueError on purpose: every caller already treats ValueError as "this listing cannot be
    priced" and shows its Vietnamese message (the listing page, the chat's price tool).
    """

    def __init__(self) -> None:
        super().__init__(NO_MODEL_MESSAGE)


@dataclass(frozen=True)
class ListingAssessment:
    """Whether a stored listing can be priced, and with which model inputs.

    Attributes:
        inputs: Keyword arguments for PricePredictionService.predict, or None when refused.
        reason_code: Why the listing is refused ("missing_fields", "unknown_ward", "area_range",
            "bedrooms_range", "price_range", "price_per_m2_range"), or None.
        message: The user-facing Vietnamese reason, or None.
        asking_price: The listing's asking price in billion VND, when it has one.
    """

    inputs: Optional[Dict[str, Any]]
    reason_code: Optional[str]
    message: Optional[str]
    asking_price: Optional[float]


def price_bounds(estimated_price: float, property_type: str) -> Tuple[float, float]:
    """Confidence band around an estimate.

    Empirical test results: Căn hộ MAPE ~14.9% (use +/-12%), Nhà đất MAPE ~21.0% (use +/-20%).

    Args:
        estimated_price: Model estimate in billion VND.
        property_type: "Căn hộ" or "Nhà đất".

    Returns:
        tuple: (lower bound, upper bound) in billion VND, rounded to 2 decimals; the lower bound
        is at least 0.1.
    """
    margin_pct = 12.0 if "Căn hộ" in property_type else 20.0
    lower_bound = round(estimated_price * (1.0 - margin_pct / 100.0), 2)
    upper_bound = round(estimated_price * (1.0 + margin_pct / 100.0), 2)
    return max(lower_bound, 0.1), upper_bound


def price_verdict(asking_price: float, lower_bound: float, upper_bound: float) -> str:
    """Compare an asking price with the confidence band: "below", "within" or "above"."""
    return "below" if asking_price < lower_bound else "above" if asking_price > upper_bound else "within"


class PricePredictionService:
    """Prices listings with the served model, reloading it when a training run deploys a new one."""

    def __init__(self, model_check_seconds: float = MODEL_CHECK_SECONDS) -> None:
        """Start without a model; the first use loads one.

        Args:
            model_check_seconds: How often to look for a newly deployed model.
        """
        self.model_check_seconds = model_check_seconds
        self._served: Optional[ServedModel] = None
        self._checked_at = 0.0
        # Predictions run in worker threads; the lock makes concurrent calls load a model once.
        self._lock = Lock()

    def _refresh(self) -> None:
        """Load the latest deployed model when it differs from the one held; keep the held one on a database error.

        When the database lists no deployed model any more (its runs were wiped), the held model is
        dropped too: a model the history no longer shows must not keep pricing.
        """
        self._checked_at = time.monotonic()
        try:
            run_id = training_repository.active_model_id()
            stored = training_repository.fetch_model(run_id) if run_id is not None and (
                self._served is None or self._served.run_id != run_id) else None
        except Exception as error:  # noqa: BLE001 - pricing must not fail harder than "no model"
            if self._served is None:
                logger.warning("Deployed price model unavailable: {}", type(error).__name__)
            return
        if stored is not None:
            # Written by this server's own training runs, never uploaded: loading it is safe.
            self._served = ServedModel(joblib.load(io.BytesIO(stored["model"])), stored["report"], run_id)
            logger.info("Serving price model of training run {}", run_id)
        elif run_id is None:
            self._served = None

    def served_model(self, is_forced: bool = False) -> ServedModel:
        """The model being served, looking for a newer one at most every model_check_seconds.

        Args:
            is_forced: Look now (after a run deployed a model).

        Returns:
            ServedModel: The bundle, its report and its run id.

        Raises:
            PriceModelUnavailableError: No training run has deployed a model yet.
        """
        with self._lock:
            if is_forced or self._served is None or time.monotonic() - self._checked_at > self.model_check_seconds:
                self._refresh()
            if self._served is None:
                raise PriceModelUnavailableError()
            return self._served

    def served_model_or_none(self) -> Optional[ServedModel]:
        """The model being served, or None before the first deployed model (the champion a training run must beat)."""
        try:
            return self.served_model()
        except PriceModelUnavailableError:
            return None

    def _get_bundle(self) -> Dict[str, Any]:
        """The served model's bundle: model_name, model, feature_names and, for trained-here models, imputation."""
        return self.served_model().bundle

    def warm_up(self) -> bool:
        """Load the model ahead of the first request.

        The first load takes about a second (joblib plus scikit-learn imports); doing it at
        startup keeps that delay out of a user's request.

        Returns:
            bool: True when a model is loaded; False before the first deployed model, in which case
            predictions raise PriceModelUnavailableError.
        """
        try:
            self.served_model()
            return True
        except PriceModelUnavailableError:
            logger.info("No price model deployed yet; pricing waits for the first training run")
            return False
        except Exception as error:  # noqa: BLE001 - startup must not fail on a broken model
            logger.warning("Price model warm-up failed; predictions are unavailable: {}", error)
            return False

    def predict_many(self, inputs: List[Dict[str, Any]]) -> List[float]:
        """Estimate many listings in one model call.

        Args:
            inputs: Keyword arguments of predict, one dict per listing.

        Returns:
            list: Estimated prices in billion VND, rounded to 2 decimals, in input order.
        """
        if not inputs:
            return []
        bundle = self._get_bundle()
        frame = pd.DataFrame([{**values, "legal_status": values.get("legal_status", "Sổ hồng riêng")} for values in inputs])
        return [round(float(value), 2) for value in np.expm1(bundle["model"].predict(feature_frame(frame, bundle["feature_names"])))]

    def predict(
        self,
        property_type: str,
        area: float,
        bedrooms: int,
        latitude: float,
        longitude: float,
        district: str,
        legal_status: str = "Sổ hồng riêng",
    ) -> Dict[str, Any]:
        """Predict estimated price and confidence interval in billion VND."""
        bundle = self._get_bundle()
        estimated_price = self.predict_many([{
            "property_type": property_type, "area": area, "bedrooms": bedrooms, "latitude": latitude,
            "longitude": longitude, "district": district, "legal_status": legal_status,
        }])[0]
        lower_bound, upper_bound = price_bounds(estimated_price, property_type)
        return {
            "estimated_price": estimated_price,
            "lower_bound": lower_bound,
            "upper_bound": upper_bound,
            "unit": "tỷ VND",
            "property_type": property_type,
            "district": district,
            "model_name": bundle.get("model_name", "HistGradientBoosting"),
        }

    def assess_listing(self, item: Dict[str, Any]) -> ListingAssessment:
        """Check whether a stored listing lies inside the model's training domain.

        Missing bedrooms and missing or out-of-city coordinates are filled in as training fills
        them (the bedroom median of the served model, a point inside the listing's ward); a model
        shipped without bedroom medians refuses listings without bedrooms.

        Args:
            item: A properties row with latitude_longitude, area, bedrooms, price_range,
                property_type, district and legal_status.

        Returns:
            ListingAssessment: the model inputs when every check passes, otherwise the first
            failing check's code and Vietnamese message.
        """
        bundle = self._get_bundle()
        area, asking = item.get("area"), item.get("price_range")
        asking_price = float(asking) if asking else None
        group = property_group(item.get("property_type")) or "Nhà đất"
        district = item.get("district")
        coordinates = parse_coordinates(item.get("latitude_longitude"))
        if not (coordinates and is_inside_city(*coordinates)):
            coordinates = ward_point(district)
        bedrooms = item.get("bedrooms")
        if not bedrooms and area and bundle.get("imputation"):
            bedrooms = impute_bedrooms(group, float(area), bundle["imputation"])
        if not (coordinates and area and bedrooms and asking):
            return ListingAssessment(None, "missing_fields",
                                     "Tin đăng thiếu diện tích, số phòng ngủ, giá hoặc tọa độ để định giá.", asking_price)

        latitude, longitude = coordinates
        rule = OUTLIER_RULES[group]
        per_m2 = float(asking) * 1000 / float(area)
        # First failing check wins; the message is shown to users, so say which limit was hit.
        checks = [
            (f"district_{district}" in bundle["feature_names"],
             "unknown_ward", "Khu vực (phường/xã) của tin chưa có trong dữ liệu huấn luyện."),
            (rule.minimum_area <= float(area) <= rule.maximum_area,
             "area_range", f"Diện tích {float(area):g} m² nằm ngoài khoảng {rule.minimum_area:g}–{rule.maximum_area:g} m² mô hình được huấn luyện."),
            (1 <= int(bedrooms) <= rule.maximum_bedrooms,
             "bedrooms_range", f"Số phòng ngủ ({int(bedrooms)}) nằm ngoài khoảng 1–{rule.maximum_bedrooms} mô hình được huấn luyện."),
            # Asking price outside the trained price band: the comparison would be meaningless.
            (rule.minimum_price <= float(asking) <= rule.maximum_price,
             "price_range", f"Giá rao nằm ngoài khoảng {rule.minimum_price:g}–{rule.maximum_price:g} tỷ mô hình được huấn luyện."),
            (rule.minimum_price_per_m2 <= per_m2 <= rule.maximum_price_per_m2,
             "price_per_m2_range",
             f"Đơn giá {per_m2:.0f} triệu/m² nằm ngoài khoảng {rule.minimum_price_per_m2:g}–{rule.maximum_price_per_m2:g} triệu/m² mô hình được huấn luyện."),
        ]
        for ok, code, reason in checks:
            if not ok:
                return ListingAssessment(None, code, reason, asking_price)
        inputs = {
            "property_type": group,
            "area": float(area),
            "bedrooms": int(bedrooms),
            "latitude": latitude,
            "longitude": longitude,
            "district": district,
            "legal_status": normalize_legal_status(item.get("legal_status")),
        }
        return ListingAssessment(inputs, None, None, asking_price)

    def estimate_listing(self, item: Dict[str, Any]) -> Dict[str, Any]:
        """Compare a stored listing's asking price with the model estimate.

        Raises ValueError (user-facing, Vietnamese) when the listing cannot be scored reliably.
        """
        assessment = self.assess_listing(item)
        if assessment.inputs is None:
            raise ValueError(assessment.message)
        result = self.predict(**assessment.inputs)
        asking = float(assessment.asking_price)
        result["asking_price"] = asking
        result["diff_pct"] = round((asking / result["estimated_price"] - 1.0) * 100.0, 1)
        result["verdict"] = price_verdict(asking, result["lower_bound"], result["upper_bound"])
        return result


# Singleton service instance
price_prediction_service = PricePredictionService()

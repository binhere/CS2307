"""Price prediction inference service.

Loads the trained machine learning model and computes real estate valuations
with confidence bounds based on property type segment.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Any, Dict, List, Optional, Tuple

import joblib
import numpy as np
import pandas as pd
from loguru import logger


# shortcut: label rules duplicated from price_prediction/data_preparation/step-1/remove-unreliable-features.py
# (DB stores the raw v3 labels, the model was trained on the normalized ones); keep in sync on retrain.
_APARTMENT_TYPES = {"Căn hộ", "Căn hộ chung cư"}
_COORDINATES = re.compile(r"(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)")
# Training domain: step-2 coordinate box and step-3 outlier rules.
_LAT_RANGE, _LON_RANGE = (10.3, 11.6), (106.3, 107.6)
# Per type: (min area, max area, max bedrooms, min price tỷ, max price tỷ, min tr/m2, max tr/m2).
_DOMAIN = {"Căn hộ": (25, 150, 4, 1.0, 20.0, 20, 180), "Nhà đất": (20, 350, 8, 1.5, 40.0, 30, 350)}


@dataclass(frozen=True)
class ListingAssessment:
    """Whether a stored listing can be priced, and with which model inputs.

    Attributes:
        inputs: Keyword arguments for PricePredictionService.predict, or None when refused.
        reason_code: Why the listing is refused ("missing_fields", "outside_city", "unknown_ward",
            "area_range", "bedrooms_range", "price_range", "price_per_m2_range"), or None.
        message: The user-facing Vietnamese reason, or None.
        asking_price: The listing's asking price in billion VND, when it has one.
    """

    inputs: Optional[Dict[str, Any]]
    reason_code: Optional[str]
    message: Optional[str]
    asking_price: Optional[float]


def parse_coordinates(value: Optional[str]) -> Optional[Tuple[float, float]]:
    """Read "latitude, longitude" from a listing's latitude_longitude text.

    Returns:
        tuple | None: (latitude, longitude), or None when the text holds no coordinate pair.
    """
    match = _COORDINATES.search(value or "")
    return (float(match.group(1)), float(match.group(2))) if match else None


def is_inside_city(latitude: float, longitude: float) -> bool:
    """Whether a point lies in the Ho Chi Minh City box the model was trained on."""
    return _LAT_RANGE[0] <= latitude <= _LAT_RANGE[1] and _LON_RANGE[0] <= longitude <= _LON_RANGE[1]


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


def normalize_legal_status(value: Optional[str]) -> str:
    text = (value or "").lower()
    if re.search(r"chờ|làm sổ|nộp hồ sơ", text):
        return "Đang chờ sổ"
    if re.search(r"chung|sdc", text):
        return "Sổ chung"
    if re.search(r"hợp đồng|hđmb|hdmb", text):
        return "Hợp đồng mua bán"
    if re.search(r"sổ|hồng|đỏ|shr", text):
        return "Sổ hồng riêng"
    return "Không rõ"


class PricePredictionService:
    def __init__(self) -> None:
        self._bundle: Optional[Dict[str, Any]] = None
        # Predictions run in worker threads; the lock makes concurrent first calls
        # load the bundle once instead of once per thread.
        self._bundle_lock = Lock()

    def _resolve_model_path(self) -> Path:
        """Return the trained model bundle written by price_prediction/train-models.py."""
        model_path = Path(__file__).resolve().parent / "artifacts" / "best_model.joblib"
        if model_path.exists():
            return model_path
        raise FileNotFoundError(f"Trained price model not found: {model_path}")

    def _get_bundle(self) -> Dict[str, Any]:
        """Lazy-load and cache the model bundle."""
        if self._bundle is None:
            with self._bundle_lock:
                if self._bundle is None:
                    self._bundle = joblib.load(self._resolve_model_path())
        return self._bundle

    def warm_up(self) -> bool:
        """Load the model bundle ahead of the first request.

        The first load takes about a second (joblib plus scikit-learn imports);
        doing it at startup keeps that delay out of a user's request.

        Returns:
            bool: True when the bundle is loaded; False when it could not be
            loaded, in which case predictions raise on use as before.
        """
        try:
            self._get_bundle()
            return True
        except Exception as error:  # noqa: BLE001 - startup must not fail on a missing model
            logger.warning("Price model warm-up failed; predictions are unavailable: {}", error)
            return False

    @staticmethod
    def _feature_row(
        feature_names: List[str],
        property_type: str,
        area: float,
        bedrooms: int,
        latitude: float,
        longitude: float,
        district: str,
        legal_status: str = "Sổ hồng riêng",
    ) -> Dict[str, Any]:
        """Build one model input row, as price_prediction/data_preparation step 4 encoded it.

        Returns:
            dict: A value for every feature name; one-hot columns the model does not know stay 0.
        """
        row = {col: 0 for col in feature_names}
        row["area"] = float(area)
        row["bedrooms"] = int(bedrooms)
        row["area_per_bedroom"] = round(float(area) / max(int(bedrooms), 1), 2)
        row["latitude"] = float(latitude)
        row["longitude"] = float(longitude)

        # One-hot encoded flags
        pt_col = f"property_type_{property_type}"
        if pt_col in row:
            row[pt_col] = 1

        ls_col = f"legal_status_{legal_status}"
        if ls_col in row:
            row[ls_col] = 1

        dist_col = f"district_{district}"
        if dist_col in row:
            row[dist_col] = 1
        return row

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
        feature_names = bundle["feature_names"]
        frame = pd.DataFrame([self._feature_row(feature_names, **values) for values in inputs])[feature_names]
        return [round(float(value), 2) for value in np.expm1(bundle["model"].predict(frame))]

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
        model = bundle["model"]
        feature_names = bundle["feature_names"]
        row = self._feature_row(feature_names, property_type, area, bedrooms, latitude, longitude, district, legal_status)

        # DataFrame with exact column order expected by scikit-learn
        input_df = pd.DataFrame([row])[feature_names]

        # Inference in log space, convert to billion VND
        pred_log = model.predict(input_df)[0]
        estimated_price = round(float(np.expm1(pred_log)), 2)
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

        Args:
            item: A properties row with latitude_longitude, area, bedrooms, price_range,
                property_type, district and legal_status.

        Returns:
            ListingAssessment: the model inputs when every check passes, otherwise the first
            failing check's code and Vietnamese message.
        """
        coords = parse_coordinates(item.get("latitude_longitude"))
        area, bedrooms, asking = item.get("area"), item.get("bedrooms"), item.get("price_range")
        asking_price = float(asking) if asking else None
        if not (coords and area and bedrooms and asking):
            return ListingAssessment(None, "missing_fields",
                                     "Tin đăng thiếu diện tích, số phòng ngủ, giá hoặc tọa độ để định giá.", asking_price)

        latitude, longitude = coords
        property_type = "Căn hộ" if item.get("property_type") in _APARTMENT_TYPES else "Nhà đất"
        min_area, max_area, max_bedrooms, min_price, max_price, min_m2, max_m2 = _DOMAIN[property_type]
        per_m2 = float(asking) * 1000 / float(area)
        district = item.get("district")
        # First failing check wins; the message is shown to users, so say which limit was hit.
        checks = [
            (is_inside_city(latitude, longitude),
             "outside_city", "Toạ độ của tin nằm ngoài khu vực TP.HCM."),
            (f"district_{district}" in self._get_bundle()["feature_names"],
             "unknown_ward", "Khu vực (phường/xã) của tin chưa có trong dữ liệu huấn luyện."),
            (min_area <= float(area) <= max_area,
             "area_range", f"Diện tích {float(area):g} m² nằm ngoài khoảng {min_area}–{max_area} m² mô hình được huấn luyện."),
            (1 <= int(bedrooms) <= max_bedrooms,
             "bedrooms_range", f"Số phòng ngủ ({int(bedrooms)}) nằm ngoài khoảng 1–{max_bedrooms} mô hình được huấn luyện."),
            # Asking price outside the trained price band: the comparison would be meaningless.
            (min_price <= float(asking) <= max_price,
             "price_range", f"Giá rao nằm ngoài khoảng {min_price:g}–{max_price:g} tỷ mô hình được huấn luyện."),
            (min_m2 <= per_m2 <= max_m2,
             "price_per_m2_range",
             f"Đơn giá {per_m2:.0f} triệu/m² nằm ngoài khoảng {min_m2}–{max_m2} triệu/m² mô hình được huấn luyện."),
        ]
        for ok, code, reason in checks:
            if not ok:
                return ListingAssessment(None, code, reason, asking_price)
        inputs = {
            "property_type": property_type,
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

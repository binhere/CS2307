"""Fit the price model candidates, score models, and describe a trained model for the dashboard.

The candidates, the 80/20 split stratified by property group, the selection by R² on log price and
the metrics are those of price_prediction/train-models.py, so a model trained here is comparable
with the one shipped in the image. Unlike that script, the chosen candidate is then fitted again
on the whole pool: the fixed holdout set, not the validation split, judges it against the served
model, so the validation rows are better spent on training. The report has the same shape as the
script's training_report.json (report_version 1), which the data dashboard reads, with the
holdout set in place of the script's test split.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Sequence, Tuple

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor, RandomForestRegressor
from sklearn.linear_model import Ridge
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.model_selection import train_test_split
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from threadpoolctl import threadpool_limits

from src.services.price_training.preparation import feature_frame, feature_names_for, log_price

RANDOM_STATE = 42
VALIDATION_SIZE = 0.2
# The node is shared with the live site and other tenants: two threads keep training from starving
# requests. OpenMP (HistGradientBoosting) and BLAS default to one thread per core, and on a busy
# machine those threads wait on each other: measured 11.5 s with 8 threads against 1.4 s with 2.
TRAINING_JOBS = 2


def candidate_models() -> Dict[str, Any]:
    """The three candidates of train-models.py, unfitted."""
    return {
        "Ridge Regression": make_pipeline(StandardScaler(), Ridge(alpha=1.0)),
        "Random Forest": RandomForestRegressor(n_estimators=150, random_state=RANDOM_STATE, n_jobs=TRAINING_JOBS),
        "HistGradientBoosting": HistGradientBoostingRegressor(max_iter=150, random_state=RANDOM_STATE),
    }


def regression_metrics(true_log: np.ndarray, predicted_log: np.ndarray) -> Dict[str, float]:
    """Metrics of train-models.py, in the report's keys.

    Args:
        true_log: log1p of the real prices.
        predicted_log: log1p of the predicted prices.

    Returns:
        dict: r2 and rmse_log on log prices; mae and median_ae in billion VND; mape in percent.
    """
    true_price, predicted_price = np.expm1(true_log), np.expm1(predicted_log)
    return {
        "r2": round(float(r2_score(true_log, predicted_log)), 4),
        "rmse_log": round(float(np.sqrt(mean_squared_error(true_log, predicted_log))), 4),
        "mae": round(float(mean_absolute_error(true_price, predicted_price)), 3),
        "median_ae": round(float(np.median(np.abs(true_price - predicted_price))), 3),
        "mape": round(float(np.mean(np.abs((true_price - predicted_price) / true_price)) * 100), 2),
    }


def segment_metrics(frame: pd.DataFrame, predicted_log: np.ndarray) -> List[Dict[str, Any]]:
    """Metrics on all rows and per property group, as the report's "segments".

    Args:
        frame: Prepared rows (property_type and price).
        predicted_log: The model's log1p predictions for them, in order.

    Returns:
        list: {"segment", "count", r2, rmse_log, mae, median_ae, mape} for "all", "Căn hộ", "Nhà đất".
    """
    true_log = log_price(frame).to_numpy()
    rows = []
    for segment in ("all", "Căn hộ", "Nhà đất"):
        mask = np.ones(len(frame), dtype=bool) if segment == "all" else (frame["property_type"] == segment).to_numpy()
        if mask.sum() < 2:
            continue
        rows.append({"segment": segment, "count": int(mask.sum()), **regression_metrics(true_log[mask], predicted_log[mask])})
    return rows


def predict_log(bundle: Dict[str, Any], frame: pd.DataFrame) -> np.ndarray:
    """A bundle's log1p price predictions for prepared rows, encoded with the bundle's own columns."""
    return bundle["model"].predict(feature_frame(frame, bundle["feature_names"]))


def fit_candidates(pool: pd.DataFrame) -> Tuple[str, Any, List[str], List[Dict[str, Any]], pd.DataFrame]:
    """Choose the best candidate on an 80/20 split of the pool, then fit it on the whole pool.

    Args:
        pool: Prepared training rows (holdout excluded).

    Returns:
        tuple: (best model name, the best candidate fitted on the whole pool, its feature names,
        the benchmark rows of every candidate on the validation rows, the rows it was fitted on).
    """
    feature_names = feature_names_for(pool)
    stratify = pool["property_type"] if pool["property_type"].nunique() > 1 else None
    training, validation = train_test_split(pool, test_size=VALIDATION_SIZE, random_state=RANDOM_STATE, stratify=stratify)
    training_features, validation_features = feature_frame(training, feature_names), feature_frame(validation, feature_names)
    training_target, validation_true = log_price(training), log_price(validation).to_numpy()
    benchmark: List[Dict[str, Any]] = []
    with threadpool_limits(limits=TRAINING_JOBS):
        for name, model in candidate_models().items():
            model.fit(training_features, training_target)
            benchmark.append({"model": name, **regression_metrics(validation_true, model.predict(validation_features))})
        best = max(benchmark, key=lambda row: row["r2"])["model"]
        final_model = candidate_models()[best]
        final_model.fit(feature_frame(pool, feature_names), log_price(pool))
    return best, final_model, feature_names, benchmark, pool


def build_report(bundle: Dict[str, Any], training: pd.DataFrame, holdout: pd.DataFrame, input_rows: pd.DataFrame,
                 step_rows: Sequence[int], benchmark: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Describe a trained model in the shape of train-models.py's training_report.json.

    Args:
        bundle: The trained bundle (model_name, model, feature_names).
        training: The rows it was fitted on.
        holdout: The holdout rows it is judged on (they play the test split's part).
        input_rows: Every prepared row of the run, for the composition by source.
        step_rows: Rows after each preparation step.
        benchmark: Every candidate's metrics on the validation rows.

    Returns:
        dict: JSON-ready report; prices in billion VND, price per m² in million VND.
    """
    predicted = np.expm1(predict_log(bundle, holdout))
    price_per_m2 = training["price"] * 1000 / training["area"]
    return {
        "report_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "model": {"name": bundle["model_name"], "random_state": RANDOM_STATE, "test_size": VALIDATION_SIZE,
                  "feature_count": len(bundle["feature_names"])},
        "composition": {
            "source_file": "PostgreSQL: properties",
            "by_source": {str(key): int(value) for key, value in input_rows["source"].value_counts().items()},
            "step_rows": [int(value) for value in step_rows],
            "train_rows": int(len(training)),
            "test_rows": int(len(holdout)),
            "train_by_source": {str(key): int(value) for key, value in training["source"].value_counts().items()},
            "imputed": {"bedrooms": int(training["is_bedrooms_imputed"].sum()),
                        "coordinates": int(training["is_coordinates_imputed"].sum())},
        },
        "benchmark": benchmark,
        "segments": segment_metrics(holdout, np.log1p(predicted)),
        "test_predictions": [
            [int(listing_id), group, round(float(actual), 3), round(float(estimate), 3)]
            for listing_id, group, actual, estimate in zip(holdout["listing_id"], holdout["property_type"], holdout["price"], predicted)
        ],
        "train_listing_ids": sorted(int(value) for value in training["listing_id"]),
        "reference": {
            "rows": int(len(training)),
            "numeric": {
                "price": [round(float(value), 3) for value in training["price"]],
                "area": [round(float(value), 1) for value in training["area"]],
                "bedrooms": [int(value) for value in training["bedrooms"]],
                "price_per_m2": [round(float(value), 2) for value in price_per_m2],
            },
            "categorical": {
                "property_group": {str(key): int(value) for key, value in training["property_type"].value_counts().items()},
                "legal_status": {str(key): int(value) for key, value in training["legal_status"].value_counts().items()},
                "ward": {str(key): int(value) for key, value in training["district"].value_counts().items()},
            },
        },
    }

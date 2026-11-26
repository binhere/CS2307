"""Train, benchmark, and save price prediction models.

Dataset source: price_prediction/data_preparation/step-4/encoded_data.xlsx

Steps:
1. Load clean encoded dataset.
2. Split into 80% train and 20% test sets (stratified by property type).
3. Train candidate models:
   - Ridge Regression (linear baseline)
   - Random Forest Regressor (bagging ensemble)
   - HistGradientBoosting Regressor (boosting ensemble)
4. Evaluate performance overall and separately by segment:
   - Overall (Combined)
   - Căn hộ (Apartments)
   - Nhà đất (Houses)
5. Save the best model artifact and multi-sheet evaluation report.
6. Save training_report.json next to the model: what the data dashboard (/du-lieu) shows about
   this model (data composition, metrics, test predictions, training ids, reference distributions).

Run `python train-models.py --report-only` to write the report for the model already saved,
without training again: the split is fixed (random_state=42), so it reproduces the same test set.
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor, RandomForestRegressor
from sklearn.linear_model import Ridge
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.model_selection import train_test_split
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler


SCRIPT_DIR = Path(__file__).resolve().parent
INPUT_FILE = SCRIPT_DIR / "data_preparation" / "step-4" / "encoded_data.xlsx"
EVALUATION_FILE = SCRIPT_DIR / "model_evaluation.xlsx"
# The backend serves this exact file; training overwrites it in place.
MODEL_FILE = SCRIPT_DIR.parent / "backend" / "src" / "services" / "inference" / "artifacts" / "best_model.joblib"
# Read by the backend's monitoring API; written with the model so the two always describe each other.
REPORT_FILE = MODEL_FILE.with_name("training_report.json")
MASTER_FILE = SCRIPT_DIR.parent / "data" / "v3" / "master_v3.csv"
STEP_FILES = [
    SCRIPT_DIR / "data_preparation" / "step-1" / "reliable_features.xlsx",
    SCRIPT_DIR / "data_preparation" / "step-2" / "cleaned_data.xlsx",
    SCRIPT_DIR / "data_preparation" / "step-3" / "outliers_removed.xlsx",
    INPUT_FILE,
]
RANDOM_STATE = 42
TEST_SIZE = 0.2


def load_and_split_data(file_path: Path):
    """Load the encoded dataset and split it into train/test sets.

    Args:
        file_path: encoded_data.xlsx from data_preparation step 4.

    Returns:
        tuple: X_train, X_test, y_train, y_test, ids_train, ids_test (listing ids of each row)
        and the feature names. The split is stratified by property type and fixed by
        RANDOM_STATE, so the same file always gives the same split.
    """
    df = pd.read_excel(file_path)

    # Features X and target y
    X = df.drop(columns=["listing_id", "log_price"])
    y = df["log_price"]
    ids = df["listing_id"]

    # Stratify by property type to keep balanced ratios
    stratify_col = (
        df["property_type_Nhà đất"] if "property_type_Nhà đất" in df.columns else None
    )

    # Splitting the ids alongside X and y does not change the permutation.
    X_train, X_test, y_train, y_test, ids_train, ids_test = train_test_split(
        X, y, ids, test_size=TEST_SIZE, random_state=RANDOM_STATE, stratify=stratify_col
    )
    return X_train, X_test, y_train, y_test, ids_train, ids_test, list(X.columns)


def compute_metrics(y_true_log: pd.Series, y_pred_log: np.ndarray) -> dict:
    """Compute regression metrics in log and real price space."""
    y_true_real = np.expm1(y_true_log)
    y_pred_real = np.expm1(y_pred_log)

    r2 = r2_score(y_true_log, y_pred_log)
    rmse_log = float(np.sqrt(mean_squared_error(y_true_log, y_pred_log)))
    mae_billion = float(mean_absolute_error(y_true_real, y_pred_real))
    median_ae_billion = float(np.median(np.abs(y_true_real - y_pred_real)))
    mape_pct = float(np.mean(np.abs((y_true_real - y_pred_real) / y_true_real)) * 100)

    return {
        "R2 Score": round(r2, 4),
        "RMSE (log)": round(rmse_log, 4),
        "MAE (billion VND)": round(mae_billion, 3),
        "Median AE (billion VND)": round(median_ae_billion, 3),
        "MAPE (%)": round(mape_pct, 2),
    }


def train_and_evaluate(X_train, X_test, y_train, y_test):
    """Train candidates and compute overall + segment benchmark metrics."""
    candidate_models = {
        "Ridge Regression": make_pipeline(StandardScaler(), Ridge(alpha=1.0)),
        "Random Forest": RandomForestRegressor(
            n_estimators=150, random_state=42, n_jobs=-1
        ),
        "HistGradientBoosting": HistGradientBoostingRegressor(
            max_iter=150, random_state=42
        ),
    }

    # Segment masks
    is_house = X_test["property_type_Nhà đất"] == 1
    is_apt = ~is_house
    segments = [
        ("Combined (All)", slice(None), len(y_test)),
        ("Căn hộ", is_apt, int(is_apt.sum())),
        ("Nhà đất", is_house, int(is_house.sum())),
    ]

    overall_results = []
    segment_results = []
    trained_models = {}

    for name, model in candidate_models.items():
        # Train model on all training data
        model.fit(X_train, y_train)
        trained_models[name] = model

        # Predict on entire test set
        y_pred_log = model.predict(X_test)

        # 1. Overall metrics
        overall_m = compute_metrics(y_test, y_pred_log)
        overall_results.append({"Model": name, **overall_m})

        # 2. Per-segment metrics
        for seg_name, mask, count in segments:
            seg_metrics = compute_metrics(y_test[mask], y_pred_log[mask])
            segment_results.append(
                {
                    "Model": name,
                    "Segment": seg_name,
                    "Sample Count": count,
                    **seg_metrics,
                }
            )

    overall_df = pd.DataFrame(overall_results)
    segment_df = pd.DataFrame(segment_results)
    return overall_df, segment_df, trained_models


def segment_metrics(model, X_test, y_test) -> list:
    """Metrics of one model on the whole test set and on each property-type segment.

    Returns:
        list: one dict per segment ("all", "Căn hộ", "Nhà đất") with count, r2, rmse_log,
        mae, median_ae and mape (prices in billion VND).
    """
    y_pred_log = model.predict(X_test)
    is_house = (X_test["property_type_Nhà đất"] == 1).to_numpy()
    rows = []
    for segment, mask in (("all", np.ones(len(y_test), dtype=bool)), ("Căn hộ", ~is_house), ("Nhà đất", is_house)):
        metrics = compute_metrics(y_test[mask], y_pred_log[mask])
        rows.append({
            "segment": segment,
            "count": int(mask.sum()),
            "r2": metrics["R2 Score"],
            "rmse_log": metrics["RMSE (log)"],
            "mae": metrics["MAE (billion VND)"],
            "median_ae": metrics["Median AE (billion VND)"],
            "mape": metrics["MAPE (%)"],
        })
    return rows


def benchmark_rows(overall_df: pd.DataFrame) -> list:
    """Overall metrics of every candidate model, renamed to the report's keys."""
    return [
        {
            "model": row["Model"],
            "r2": float(row["R2 Score"]),
            "rmse_log": float(row["RMSE (log)"]),
            "mae": float(row["MAE (billion VND)"]),
            "median_ae": float(row["Median AE (billion VND)"]),
            "mape": float(row["MAPE (%)"]),
        }
        for _, row in overall_df.iterrows()
    ]


def build_training_report(model_name, model, X_test, y_test, ids_train, ids_test, overall_df) -> dict:
    """Describe the saved model for the data dashboard.

    Args:
        model_name: name of the saved model.
        model: the fitted model.
        X_test, y_test: test features and log prices.
        ids_train, ids_test: listing ids of the training and test rows.
        overall_df: overall benchmark of every candidate model.

    Returns:
        dict: JSON-ready report. Prices are in billion VND, price per m² in million VND.
        test_predictions holds [listing_id, segment, actual, predicted] rows; reference holds
        the raw training values used as the reference distribution for drift.
    """
    master = pd.read_csv(MASTER_FILE, usecols=["listing_id", "source"])
    source_by_id = dict(zip(master["listing_id"], master["source"]))
    train_ids = [int(value) for value in ids_train]
    train_sources = pd.Series([source_by_id.get(listing_id, "unknown") for listing_id in train_ids]).value_counts()

    predicted = np.expm1(model.predict(X_test))
    actual = np.expm1(y_test.to_numpy())
    is_house = (X_test["property_type_Nhà đất"] == 1).to_numpy()
    test_predictions = [
        [int(listing_id), "Nhà đất" if house else "Căn hộ", round(float(truth), 3), round(float(estimate), 3)]
        for listing_id, house, truth, estimate in zip(ids_test, is_house, actual, predicted)
    ]

    raw = pd.read_excel(STEP_FILES[2])
    reference = raw[raw["listing_id"].isin(set(train_ids))]
    price_per_m2 = reference["price_range"] * 1000 / reference["area"]
    return {
        "report_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "model": {
            "name": model_name,
            "random_state": RANDOM_STATE,
            "test_size": TEST_SIZE,
            "feature_count": int(X_test.shape[1]),
        },
        "composition": {
            "source_file": "data/v3/master_v3.csv",
            "by_source": {str(key): int(value) for key, value in master["source"].value_counts().items()},
            "step_rows": [int(len(pd.read_excel(path))) for path in STEP_FILES],
            "train_rows": len(train_ids),
            "test_rows": len(test_predictions),
            "train_by_source": {str(key): int(value) for key, value in train_sources.items()},
        },
        "benchmark": benchmark_rows(overall_df),
        "segments": segment_metrics(model, X_test, y_test),
        "test_predictions": test_predictions,
        "train_listing_ids": sorted(train_ids),
        "reference": {
            "rows": int(len(reference)),
            "numeric": {
                "price": [round(float(value), 3) for value in reference["price_range"]],
                "area": [round(float(value), 1) for value in reference["area"]],
                "bedrooms": [int(value) for value in reference["bedrooms"]],
                "price_per_m2": [round(float(value), 2) for value in price_per_m2],
            },
            "categorical": {
                "property_group": {str(key): int(value) for key, value in reference["property_type"].value_counts().items()},
                "legal_status": {str(key): int(value) for key, value in reference["legal_status"].value_counts().items()},
                "ward": {str(key): int(value) for key, value in reference["district"].value_counts().items()},
            },
        },
    }


def write_training_report(report: dict) -> None:
    """Write the report next to the model, compactly (it ships inside the backend image)."""
    REPORT_FILE.write_text(json.dumps(report, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"Saved training report to: {REPORT_FILE}")


def write_report_for_saved_model() -> None:
    """Write the report for the model already saved, without training: same split, same test set."""
    X_train, X_test, y_train, y_test, ids_train, ids_test, _ = load_and_split_data(INPUT_FILE)
    bundle = joblib.load(MODEL_FILE)
    overall_df = pd.read_excel(EVALUATION_FILE, sheet_name="Overall")
    report = build_training_report(bundle["model_name"], bundle["model"], X_test, y_test, ids_train, ids_test, overall_df)
    saved_r2 = float(bundle["metrics_overall"]["R2 Score"])
    if abs(report["segments"][0]["r2"] - saved_r2) > 1e-4:
        raise SystemExit(f"The saved model scores R2 {report['segments'][0]['r2']} on this split, not {saved_r2}: "
                         "the data changed since it was trained; train again instead.")
    write_training_report(report)


def main() -> None:
    if "--report-only" in sys.argv[1:]:
        write_report_for_saved_model()
        return
    print(f"Loading dataset from: {INPUT_FILE}")
    X_train, X_test, y_train, y_test, ids_train, ids_test, feature_names = load_and_split_data(INPUT_FILE)

    print(f"Train records: {len(X_train):,}; Test records: {len(X_test):,}")
    print(f"Number of features: {len(feature_names)}")

    print("\nTraining and evaluating candidate models...")
    overall_df, segment_df, trained_models = train_and_evaluate(
        X_train, X_test, y_train, y_test
    )

    # Display overall benchmark
    print("\n=== OVERALL BENCHMARK RESULTS ===")
    print(overall_df.to_string(index=False))

    # Display segment breakdown
    print("\n=== SEGMENT BREAKDOWN (Căn hộ vs Nhà đất) ===")
    print(segment_df.to_string(index=False))

    # Save to Excel with two sheets
    with pd.ExcelWriter(EVALUATION_FILE, engine="openpyxl") as writer:
        overall_df.to_excel(writer, sheet_name="Overall", index=False)
        segment_df.to_excel(writer, sheet_name="By Segment", index=False)
    print(f"\nSaved multi-sheet evaluation results to: {EVALUATION_FILE}")

    # Determine best model by Overall R2 Score
    best_row = overall_df.sort_values(by="R2 Score", ascending=False).iloc[0]
    best_name = best_row["Model"]
    best_model = trained_models[best_name]

    # Best model segment metrics
    best_segment_metrics = segment_df[segment_df["Model"] == best_name].to_dict(
        orient="records"
    )

    # Save best model along with metadata
    model_bundle = {
        "model_name": best_name,
        "model": best_model,
        "feature_names": feature_names,
        "metrics_overall": best_row.to_dict(),
        "metrics_by_segment": best_segment_metrics,
    }
    joblib.dump(model_bundle, MODEL_FILE)
    print(f"Saved best model ({best_name}) to: {MODEL_FILE}")
    write_training_report(build_training_report(best_name, best_model, X_test, y_test, ids_train, ids_test, overall_df))


if __name__ == "__main__":
    main()

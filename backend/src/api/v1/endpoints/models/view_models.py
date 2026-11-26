"""Price model training API: the training history and the "Huấn luyện ngay" button.

GET /v1/models/price/runs is public, like the data dashboard it feeds; POST /v1/models/price/train
needs a signed-in user and starts a run in the background, or answers 409 while one is going.
"""

from typing import Any, Dict

from fastapi import APIRouter, BackgroundTasks, Depends, Query, status
from fastapi.responses import JSONResponse
from loguru import logger
from sqlalchemy.exc import SQLAlchemyError
from starlette.concurrency import run_in_threadpool

from src.services.auth.dependencies import get_current_user
from src.services.inference.price_prediction import price_prediction_service
from src.services.price_training import repository, service

router = APIRouter()


def error_response(status_code: int, location: str, code: str, detail: str) -> JSONResponse:
    """An error in the API envelope.

    Args:
        status_code: HTTP status.
        location: What failed, e.g. "postgres".
        code: Machine-readable code, e.g. "TRAINING_IN_PROGRESS".
        detail: Vietnamese message for the user.

    Returns:
        JSONResponse: {"data": None, "errors": [{"loc", "msg", "detail"}], "status": "error"}.
    """
    return JSONResponse(status_code=status_code,
                        content={"data": None, "status": "error", "errors": [{"loc": location, "msg": code, "detail": detail}]})


def run_and_serve(run_id: int) -> None:
    """Carry out a "Huấn luyện ngay" run, then serve its model at once when it was deployed.

    Args:
        run_id: The run started by the request.
    """
    if service.execute_run(run_id, "manual", price_prediction_service.served_model_or_none()) == "deployed":
        price_prediction_service.served_model(is_forced=True)


@router.get("/price/runs", summary="History of price model training runs")
async def view_price_model_runs(limit: int = Query(30, ge=1, le=100)):
    """List the latest training runs.

    Args:
        limit: How many runs, newest first.

    Returns:
        dict | JSONResponse: Envelope whose data is {"runs": [{id, trigger, requested_by_name,
        status, reason, started_at, finished_at, summary}], "schedule_hour", "new_data_share",
        "is_running"}; 503 when the database is unavailable.
    """
    try:
        runs = await run_in_threadpool(repository.list_runs, limit)
    except SQLAlchemyError:
        logger.exception("Listing price model runs failed")
        return error_response(503, "postgres", "DATABASE_UNAVAILABLE", "Cơ sở dữ liệu chưa sẵn sàng.")
    data: Dict[str, Any] = {
        "runs": runs, "schedule_hour": service.SCHEDULE_HOUR, "new_data_share": service.NEW_DATA_SHARE,
        "is_running": any(run["status"] == "running" for run in runs),
    }
    return {"data": data, "errors": [], "status": "success"}


@router.post("/price/train", status_code=status.HTTP_202_ACCEPTED, summary="Train the price model now")
async def view_train_price_model(background_tasks: BackgroundTasks, user: dict = Depends(get_current_user)):
    """Start a training run for a signed-in user.

    Returns:
        dict | JSONResponse: 202 with data {"run_id"}; 409 TRAINING_IN_PROGRESS while another run
        is going; 401 for anonymous callers; 503 when the database is unavailable.
    """
    try:
        run_id = await run_in_threadpool(service.start_manual_run, user)
    except SQLAlchemyError:
        logger.exception("Starting a price model training run failed")
        return error_response(503, "postgres", "DATABASE_UNAVAILABLE", "Cơ sở dữ liệu chưa sẵn sàng.")
    if run_id is None:
        return error_response(409, "training", "TRAINING_IN_PROGRESS", "Máy chủ đang huấn luyện mô hình. Vui lòng thử lại sau.")
    background_tasks.add_task(run_and_serve, run_id)
    return {"data": {"run_id": run_id}, "errors": [], "status": "success"}

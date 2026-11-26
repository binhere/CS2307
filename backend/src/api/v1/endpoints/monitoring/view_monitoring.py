"""Public data dashboard API: aggregate numbers about the catalog and the price model."""

from fastapi import APIRouter, status
from fastapi.responses import JSONResponse
from loguru import logger
from sqlalchemy.exc import SQLAlchemyError
from starlette.concurrency import run_in_threadpool

from src.services.monitoring.service import monitoring_service

router = APIRouter()


@router.get("", summary="Data and model monitoring for the data dashboard")
async def view_monitoring():
    """Return the dashboard snapshot (cached for ten minutes).

    Returns:
        dict: the envelope {"data", "errors", "status"} with the sections kpis, quality,
        distributions, drift, model and predictions. Aggregates only: no user data, no keys.
        503 when the database is unavailable.
    """
    try:
        snapshot = await run_in_threadpool(monitoring_service.snapshot)
    except SQLAlchemyError:
        logger.exception("Monitoring snapshot failed")
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"data": None, "status": "error",
                     "errors": [{"loc": "postgres", "msg": "DATABASE_UNAVAILABLE", "detail": "Cơ sở dữ liệu chưa sẵn sàng."}]},
        )
    return {"data": snapshot, "errors": [], "status": "success"}

"""Retrieval evaluation API: run history, latest 4-mode comparison, and background trigger.

GET /v1/evaluation/retrieval/runs is public, feeding the "Đánh giá tìm kiếm" tab on /du-lieu.
POST /v1/evaluation/retrieval/run requires a signed-in user and starts an asynchronous
evaluation run in the background, or answers 409 while another run is in progress.
"""

from __future__ import annotations

from typing import Any, Dict, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, Query, status
from fastapi.responses import JSONResponse
from loguru import logger
from pydantic import BaseModel, ConfigDict
from sqlalchemy.exc import SQLAlchemyError
from starlette.concurrency import run_in_threadpool

from src.services.auth.dependencies import get_current_user
from src.services.retrieval_evaluation import repository, service

router = APIRouter()


class RunRetrievalEvaluationRequest(BaseModel):
    """Request body for starting a retrieval evaluation run."""

    model_config = ConfigDict(extra="forbid")

    retrieval_mode: Literal["all", "fts", "vector", "hybrid", "hybrid_graph"] = "all"


def error_response(status_code: int, location: str, code: str, detail: str) -> JSONResponse:
    """Build a standard API error envelope.

    Args:
        status_code: HTTP status code.
        location: Component that failed (e.g. 'postgres', 'evaluation').
        code: Machine-readable error code.
        detail: Vietnamese message for the user.

    Returns:
        JSONResponse with ``{"data": None, "status": "error", "errors": [...]}``.
    """
    return JSONResponse(
        status_code=status_code,
        content={
            "data": None,
            "status": "error",
            "errors": [{"loc": location, "msg": code, "detail": detail}],
        },
    )


@router.get("/retrieval/runs", summary="History and latest 4-mode retrieval evaluation results")
async def view_retrieval_eval_runs(limit: int = Query(20, ge=1, le=100)):
    """Return recent retrieval evaluation runs and the latest metrics per mode.

    Args:
        limit: Maximum number of runs to return, newest first.

    Returns:
        dict | JSONResponse: Envelope with ``runs``, ``latest_by_mode``, ``is_running``,
        and ``query_count``; 503 when PostgreSQL is unavailable.
    """
    try:
        runs = await run_in_threadpool(repository.list_runs, limit)
    except SQLAlchemyError:
        logger.exception("Listing retrieval evaluation runs failed")
        return error_response(503, "postgres", "DATABASE_UNAVAILABLE", "Cơ sở dữ liệu chưa sẵn sàng.")

    data: Dict[str, Any] = {
        "runs": runs,
        "latest_by_mode": service.build_latest_by_mode(runs),
        "is_running": any(run["status"] == "running" for run in runs),
        "query_count": len(service.load_queries()),
    }
    return {"data": data, "errors": [], "status": "success"}


@router.post(
    "/retrieval/run",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Start an in-process retrieval evaluation run",
)
async def view_start_retrieval_eval(
    background_tasks: BackgroundTasks,
    payload: RunRetrievalEvaluationRequest = RunRetrievalEvaluationRequest(),
    user: dict = Depends(get_current_user),
):
    """Start a retrieval evaluation run in the background for a signed-in user.

    Args:
        background_tasks: FastAPI background task runner.
        payload: Target retrieval mode ('all' or one of the 4 modes).
        user: Signed-in user from ``get_current_user``.

    Returns:
        dict | JSONResponse: 202 with ``{"run_id", "retrieval_mode"}``; 409 when another
        evaluation is already running; 503 when PostgreSQL is unavailable.
    """
    try:
        run_id = await run_in_threadpool(service.start_manual_run, user, payload.retrieval_mode)
    except SQLAlchemyError:
        logger.exception("Starting retrieval evaluation run failed")
        return error_response(503, "postgres", "DATABASE_UNAVAILABLE", "Cơ sở dữ liệu chưa sẵn sàng.")
    if run_id is None:
        return error_response(
            409,
            "evaluation",
            "EVALUATION_IN_PROGRESS",
            "Máy chủ đang chạy đánh giá tìm kiếm. Vui lòng chờ lượt hiện tại hoàn tất.",
        )

    background_tasks.add_task(service.execute_run, run_id, payload.retrieval_mode)
    return {
        "data": {"run_id": run_id, "retrieval_mode": payload.retrieval_mode},
        "errors": [],
        "status": "success",
    }

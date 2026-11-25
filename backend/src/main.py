from fastapi import FastAPI, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import HTTPException, RequestValidationError
from fastapi.openapi import utils
from fastapi.responses import ORJSONResponse
from loguru import logger
from starlette.concurrency import run_in_threadpool
from starlette.middleware.cors import CORSMiddleware
from starlette.responses import JSONResponse

from src.api.base.http_exception import http_except_custom
from src.api.v1.endpoints import router as v1_router

from src.settings.config import APPLICATION
from src.settings.event import (
    create_start_app_handler, create_stop_app_handler, postgres_client,
)
from src.utils.error_messages import VALIDATE_ERROR


app = FastAPI(
    title=APPLICATION["project_name"],
    debug=APPLICATION["debug"],
    version=APPLICATION["version"],
    docs_url="/",
    default_response_class=ORJSONResponse,
    openapi_url="/openapi.json" if APPLICATION["debug"] else None
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# run after start server
app.add_event_handler("startup", create_start_app_handler(app))
# run before shutdown server
app.add_event_handler("shutdown", create_stop_app_handler(app))

app.include_router(router=v1_router.router, prefix="/v1")


@app.get("/health", include_in_schema=False)
async def report_health() -> dict:
    """Liveness probe for the Docker Compose healthcheck and the cluster.

    The Swagger page at "/" is disabled when DEBUG is false, so the healthcheck
    cannot rely on it.

    Returns:
        dict: {"status": "ok"} with HTTP 200 while the process serves requests.
    """
    return {"status": "ok"}


@app.get("/ready", include_in_schema=False)
async def report_readiness():
    """Readiness probe: the API can serve requests only while PostgreSQL answers.

    Neo4j, MinIO and the embedding service are optional (search falls back to
    PostgreSQL, images to the frontend illustration), so they are not checked here.

    Returns:
        dict | JSONResponse: 200 envelope {"data": {"status": "ready"}} when a trivial
        query succeeds; 503 envelope with DATABASE_UNAVAILABLE otherwise.
    """
    try:
        await run_in_threadpool(postgres_client.fetch_mappings, "SELECT 1 AS ready")
    except Exception as error:  # noqa: BLE001 - any failure means not ready
        # Probes repeat every few seconds; one line without a traceback is enough.
        logger.warning("Readiness check failed: PostgreSQL is unavailable ({})", type(error).__name__)
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={
                "data": None,
                "errors": [{"loc": "postgres", "msg": "DATABASE_UNAVAILABLE", "detail": "Cơ sở dữ liệu chưa sẵn sàng."}],
            },
        )
    return {"data": {"status": "ready"}, "errors": [], "status": "success"}

# handler exception
@app.exception_handler(RequestValidationError)
async def request_validation_except_custom(request: Request, exc: RequestValidationError) -> JSONResponse:
    errors = []
    if exc.errors():
        for temp in exc.errors():
            errors.append(
                {
                    "loc": " -> ".join([str(err) for err in temp["loc"]]) if len(temp["loc"]) != 0 else None,
                    "msg": VALIDATE_ERROR,
                    "detail": f"{temp['msg']}",
                }
            )

    response_body = jsonable_encoder({"data": None, "errors": errors})

    return JSONResponse(
        content=response_body,
        status_code=status.HTTP_400_BAD_REQUEST,
    )


app.add_exception_handler(HTTPException, http_except_custom)


utils.HTTP_422_UNPROCESSABLE_ENTITY = status.HTTP_400_BAD_REQUEST

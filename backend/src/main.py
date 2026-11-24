from fastapi import FastAPI, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import HTTPException, RequestValidationError
from fastapi.openapi import utils
from fastapi.responses import ORJSONResponse
from starlette.middleware.cors import CORSMiddleware
from starlette.responses import JSONResponse

from src.api.base.http_exception import http_except_custom
from src.api.v1.endpoints import router as v1_router

from src.settings.config import APPLICATION
from src.settings.event import (
    create_start_app_handler, create_stop_app_handler,
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
    """Liveness probe for the Docker Compose healthcheck.

    The Swagger page at "/" is disabled when DEBUG is false, so the healthcheck
    cannot rely on it.

    Returns:
        dict: {"status": "ok"} with HTTP 200 while the process serves requests.
    """
    return {"status": "ok"}

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

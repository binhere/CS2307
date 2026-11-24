from fastapi import Request
from fastapi.exceptions import HTTPException
from fastapi.responses import JSONResponse


async def http_except_custom(request: Request, exc: HTTPException) -> JSONResponse:
    """Wrap HTTPException in the API envelope while keeping its real status code."""
    errors = [
        {
            "loc": None,
            "msg": None,
            "detail": f"{exc.detail}",
        }
    ]

    return JSONResponse(
        content={"data": None, "errors": errors},
        status_code=exc.status_code,
        headers=getattr(exc, "headers", None),
    )


def credential_error_response(error) -> JSONResponse:
    """Answer a missing, malformed or rejected LLM key in the API envelope.

    Args:
        error: LlmCredentialError with a stable ``code`` and a user-facing ``detail``.

    Returns:
        JSONResponse: HTTP 400 with ``errors[0] = {"loc": "llm", "msg": code, "detail": detail}``,
        which the frontend uses to send the user to the API key settings.
    """
    return JSONResponse(
        content={"data": None, "errors": [{"loc": "llm", "msg": error.code, "detail": error.detail}]},
        status_code=400,
    )

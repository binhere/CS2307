"""Listings posted by signed-in users: the form's choices and map lookup, checking, and posting.

The browser checks a listing here first (validate) and shows the user which fields fail and why;
posting (multipart: the listing as JSON plus each photo in two sizes the browser made) checks it
again, because the server never trusts the browser, then stores it. Only signed-in users may
check or post; the form's choices are public.
"""

import json
from typing import Any, List

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, Query, UploadFile, status
from fastapi.responses import JSONResponse
from loguru import logger
from minio.error import S3Error
from pydantic import BaseModel, Field
from sqlalchemy.exc import SQLAlchemyError
from starlette.concurrency import run_in_threadpool

from src.services.auth.dependencies import get_current_user
from src.services.listing_submission import storage
from src.services.listing_submission.coercion import today_in_vietnam
from src.services.listing_submission.places import location_directory
from src.services.listing_submission.validation import validate_listing, validate_listings
from src.services.minio.minio_client import ImageStorageUnavailableError

router = APIRouter()

MAXIMUM_LISTINGS_PER_REQUEST = 100


class ListingValidationRequest(BaseModel):
    """Body of POST /v1/listings/validate.

    Attributes:
        listings: 1 to 100 listings as JSON objects in the shape of docs/plan.html; anything
            else is reported per listing rather than rejected as a whole.
    """

    listings: List[Any] = Field(..., min_length=1, max_length=MAXIMUM_LISTINGS_PER_REQUEST)


def error_response(status_code: int, location: str, code: str, detail: str, data: Any = None) -> JSONResponse:
    """An error in the API envelope.

    Args:
        status_code: HTTP status.
        location: What failed: "listing", "photos", "storage" or "postgres".
        code: Stable error code.
        detail: Vietnamese message for the user, never raw exception text.
        data: Optional payload (the validation report of a refused listing).

    Returns:
        JSONResponse: {"data", "errors": [{"loc", "msg", "detail"}], "status": "error"}.
    """
    return JSONResponse(status_code=status_code, content={
        "data": data, "status": "error", "errors": [{"loc": location, "msg": code, "detail": detail}]})


@router.get("/reference", summary="Choices of the listing form: wards, former districts, categories, photo limits")
async def view_listing_reference():
    """Return the form's choices.

    Returns:
        dict: Envelope whose data is {"wards", "former_districts", "property_types",
        "legal_statuses", "furnishings", "limits"}.
    """
    return {"data": storage.listing_reference(), "errors": [], "status": "success"}


@router.get("/locate", summary="The ward and former districts of a point, for the form's map")
async def view_locate(latitude: float = Query(..., ge=-90, le=90), longitude: float = Query(..., ge=-180, le=180)):
    """Name the place a user picked on the map, from the official ward boundaries.

    Args:
        latitude: Decimal degrees.
        longitude: Decimal degrees.

    Returns:
        dict: Envelope whose data is {"is_in_city": bool, "ward": official name or null,
        "former_districts": [districts the ward's area belonged to before 1 July 2025]}.
    """
    reference = location_directory().reference
    ward = reference.ward_near(latitude, longitude) if reference.is_in_city(latitude, longitude) else None
    data = {"is_in_city": ward is not None, "ward": ward.name if ward else None,
            "former_districts": list(ward.former_districts) if ward else []}
    return {"data": data, "errors": [], "status": "success"}


@router.post("/validate", summary="Check listings before posting: converted values, errors and warnings")
async def view_validate_listings(payload: ListingValidationRequest, user: dict = Depends(get_current_user)):
    """Validate listings without saving them.

    Args:
        payload: The listings to check.
        user: The signed-in user; anonymous callers get 401.

    Returns:
        dict: Envelope whose data is {"results": [{index, reference, is_valid, listing, errors,
        warnings, conversions}], "valid_count", "invalid_count"}.
    """
    # Conversion is pure Python but a batch of 100 long descriptions is not free; keep it off the event loop.
    data = await run_in_threadpool(validate_listings, payload.listings)
    return {"data": data, "errors": [], "status": "success"}


@router.post("", status_code=status.HTTP_201_CREATED, summary="Post one listing with its photos")
async def view_post_listing(
    background_tasks: BackgroundTasks,
    listing: str = Form(..., description="The listing as a JSON object"),
    photos: List[UploadFile] = File(..., description="Gallery-size photos (longest side 1280 px), in order"),
    thumbnails: List[UploadFile] = File(..., description="Thumbnails (longest side 480 px), in the same order"),
    user: dict = Depends(get_current_user),
):
    """Check and store a listing; its search vector is computed afterwards in the background.

    Returns:
        dict | JSONResponse: 201 with data {"id", "listing_id", "report"}; 400 when the listing or
        a photo is refused (data holds the report); 503 when the image store or database is down.
    """
    try:
        document = json.loads(listing)
    except ValueError:
        return error_response(400, "listing", "INVALID_JSON", "Tin phải là một JSON object {…}.")
    report = await run_in_threadpool(validate_listing, document, 0, today_in_vietnam())
    if not report.is_valid:
        return error_response(400, "listing", "INVALID_LISTING", "Tin chưa hợp lệ.", {"report": report.as_dict()})
    # Read at most one byte past each limit, so an oversized upload is refused without reading it all.
    galleries = [await photo.read(storage.GALLERY_MAXIMUM_BYTES + 1) for photo in photos]
    small_photos = [await photo.read(storage.THUMBNAIL_MAXIMUM_BYTES + 1) for photo in thumbnails]
    try:
        checked = await run_in_threadpool(storage.check_photos, galleries, small_photos)
    except storage.PhotoError as error:
        return error_response(400, "photos", "INVALID_PHOTO", str(error))
    try:
        saved = await run_in_threadpool(storage.save_listing, report.listing, checked, int(user["id"]))
    except ImageStorageUnavailableError:
        return error_response(503, "storage", "IMAGE_STORAGE_UNAVAILABLE", "Không đăng được tin vì kho ảnh chưa sẵn sàng.")
    except (S3Error, SQLAlchemyError, OSError):
        logger.exception("Saving a posted listing failed")
        return error_response(503, "storage", "STORAGE_UNAVAILABLE", "Chưa lưu được tin. Vui lòng thử lại sau.")
    background_tasks.add_task(storage.embed_listing, saved["id"])
    return {"data": {**saved, "report": report.as_dict()}, "errors": [], "status": "success"}

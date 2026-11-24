from fastapi import APIRouter, HTTPException, Request
from loguru import logger

from src.search.schemas import ParseRequest, SearchRequest, SimilarRequest
from src.api.base.http_exception import credential_error_response
from src.search.service import hybrid_search_service
from src.services.llm.credentials import LlmCredentialError, resolve_credentials, use_credentials


router = APIRouter()


@router.post("/parse", summary="Parse a Vietnamese real-estate query")
async def parse_search(data: ParseRequest):
    parsed = await hybrid_search_service.parse(data.query, data.top_k)
    return {"data": parsed.model_dump(mode="json"), "errors": [], "status": "success"}


# Failures are logged in full but answered with a generic message: driver errors
# carry database host and user names that clients must not see.
@router.post("", summary="Hybrid PostgreSQL/pgvector real-estate search")
async def hybrid_search(data: SearchRequest, request: Request):
    # The LLM answer is optional here: without a key the search still returns ranked
    # listings with deterministic explanations.
    try:
        credentials = resolve_credentials(request.headers)
    except LlmCredentialError as error:
        return credential_error_response(error)
    try:
        async with use_credentials(credentials):
            result = await hybrid_search_service.search(data)
        return {"data": result, "errors": [], "status": "success"}
    except Exception as error:
        logger.exception("Hybrid search failed")
        raise HTTPException(status_code=500, detail="Search failed") from error


@router.post("/similar/{listing_id}", summary="Find semantically similar listings")
async def similar_listings(listing_id: str, data: SimilarRequest):
    try:
        result = await hybrid_search_service.similar(listing_id, data.top_k)
        return {"data": result, "errors": [], "status": "success"}
    except Exception as error:
        logger.exception("Similar-listing search failed for {}", listing_id)
        raise HTTPException(status_code=500, detail="Similar search failed") from error

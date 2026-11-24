from typing import Dict, Optional

from loguru import logger
from starlette import status
from starlette.concurrency import run_in_threadpool

from src.api.base.controller import BaseController
from src.api.v1.endpoints.properties.repositories.repo_properties import (
    repo_get_properties,
)


class PropertiesController(BaseController):
    async def ctr_get_properties(
        self,
        query: str,
        min_price: float,
        max_price: float,
        min_area: float,
        max_area: float,
        bedrooms: int,
        bathrooms: int,
        legal_status: str,
        furniture: str,
        house_direction: str,
        balcony_direction: str,
        min_frontage: float,
        min_road_width: float,
        sort_by: str,
        sort_order: str,
        page: int,
        limit: int,
        property_type: str,
        district: Optional[str] = None,
        amenity_filters: Optional[Dict[str, Optional[bool]]] = None,
    ):
        """Return one page of the property listing with its total count.

        Args:
            query: Keyword matched against title, description, address, city, district.
            min_price, max_price: Price bounds in billion VND, or None.
            min_area, max_area: Area bounds in m², or None.
            bedrooms, bathrooms: Minimum room counts, or None.
            legal_status, furniture, house_direction, balcony_direction,
                property_type, district: Comma-separated values matched
                case-insensitively, or None.
            min_frontage, min_road_width: Minimum widths in metres, or None.
            sort_by: newest | oldest | price_asc | price_desc | area_asc | area_desc.
            sort_order: asc | desc (used by newest and the created_at fallback).
            page: 1-based page number (validated >= 1 by the view).
            limit: Page size (validated 1..100 by the view).
            amenity_filters: Feature column -> True/False to require, None to ignore.

        Returns:
            JSONResponse: 200 with {"properties", "total", "page", "limit",
            "num_pages"} in "data"; 500 with a generic error when the database
            query fails (details are logged, never sent to the client).
        """
        try:
            # The repository is synchronous SQL; run it off the event loop so one
            # listing query does not stall every other request in the worker.
            data = await run_in_threadpool(
                repo_get_properties,
                query=query,
                min_price=min_price,
                max_price=max_price,
                min_area=min_area,
                max_area=max_area,
                bedrooms=bedrooms,
                bathrooms=bathrooms,
                legal_status=legal_status,
                furniture=furniture,
                house_direction=house_direction,
                balcony_direction=balcony_direction,
                min_frontage=min_frontage,
                min_road_width=min_road_width,
                sort_by=sort_by,
                sort_order=sort_order,
                page=page,
                limit=limit,
                property_type=property_type,
                district=district,
                amenity_filters=amenity_filters or {},
            )
        except Exception:
            logger.exception("Property listing query failed")
            self.errors.append(
                {
                    "loc": "PropertiesController -> ctr_get_properties",
                    "msg": "Internal server error",
                    "detail": "The property listing is temporarily unavailable.",
                }
            )
            return self.response(
                data=None, status_code=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
        return self.response(data=data, status_code=status.HTTP_200_OK)

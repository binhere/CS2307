import math
from datetime import date, datetime
from typing import Dict, List, Optional

from src.settings.event import postgres_client


# Boolean feature columns the listing page can filter on. The names are
# interpolated into SQL, so only this fixed list is ever accepted.
AMENITY_FIELDS = (
    "pool",
    "gym",
    "park",
    "bbq",
    "kids_playground",
    "sports_court",
    "security_24h",
    "reception",
    "elevator",
    "parking",
    "near_metro",
    "near_bus",
    "near_highway",
    "near_school",
    "near_hospital",
    "near_mall",
    "near_market",
    "near_park",
    "river_view",
    "park_view",
    "city_view",
    "balcony",
    "garden",
    "garage",
    "terrace",
)

SELECT_COLUMNS = """
    id,
    listing_id,
    listing_type,
    posted_date,
    expiry_date,
    property_type,
    city_province,
    district,
    images,
    folder,
    title,
    address,
    old_address,
    location_link,
    latitude_longitude,
    description,
    area_price_history,
    price_range,
    area,
    bedrooms,
    bathrooms,
    legal_status,
    furnishing,
    balcony_direction,
    house_direction,
    pool,
    gym,
    park,
    bbq,
    kids_playground,
    sports_court,
    security_24h,
    reception,
    elevator,
    parking,
    near_metro,
    near_bus,
    near_highway,
    near_school,
    near_hospital,
    near_mall,
    near_market,
    near_park,
    river_view,
    park_view,
    city_view,
    balcony,
    garden,
    garage,
    terrace,
    frontage,
    access_road,
    metadata,
    is_active,
    is_deleted,
    updated_at,
    created_at
"""


def _is_blank(value) -> bool:
    return value is None or (isinstance(value, str) and value.strip() == "")


def _add_csv_filter(where_clauses: List[str], params: Dict, column: str, raw_value: Optional[str]) -> None:
    """Add a case-insensitive match on one column for a comma-separated filter.

    Args:
        where_clauses: SQL conditions collected so far; one is appended when the
            filter has at least one value.
        params: Bound parameters collected so far; this filter's values are added
            under ``column`` (one value) or ``column_0``, ``column_1``, ... (several).
        column: Column name from a fixed list in this module, never user input.
        raw_value: The query-string value, e.g. "Sổ hồng riêng,Sổ chung", or None.

    Returns:
        None. ``where_clauses`` and ``params`` are updated in place.
    """
    if _is_blank(raw_value):
        return
    values = [part.strip() for part in str(raw_value).split(",") if not _is_blank(part)]
    if len(values) == 1:
        where_clauses.append(f"LOWER({column}) = LOWER(:{column})")
        params[column] = values[0]
    elif len(values) > 1:
        placeholders = []
        for index, value in enumerate(values):
            key = f"{column}_{index}"
            placeholders.append(f":{key}")
            params[key] = value.lower()
        where_clauses.append(f"LOWER({column}) IN ({', '.join(placeholders)})")


def repo_get_properties(
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
) -> Dict:
    """Fetch one page of active properties with filters, sorting and a total count.

    Args:
        query: Keyword matched (ILIKE) against title, description, address, city
            and district, or None.
        min_price, max_price, min_area, max_area: Inclusive numeric bounds, or None.
        bedrooms, bathrooms: Minimum counts, or None.
        legal_status, furniture, house_direction, balcony_direction,
            property_type, district: Comma-separated values matched
            case-insensitively, or None. ``furniture`` filters the
            ``furnishing`` column.
        min_frontage: Minimum frontage in metres, or None.
        min_road_width: Minimum ``access_road`` width in metres, or None.
        sort_by: newest | oldest | price_asc | price_desc | area_asc | area_desc;
            anything else sorts by created_at.
        sort_order: asc | desc, used by "newest" and the created_at fallback.
        page: 1-based page number; values below 1 become 1.
        limit: Page size; values below 1 become 10.
        amenity_filters: Column in AMENITY_FIELDS -> required True/False; None
            values and unknown keys are ignored.

    Returns:
        Dict: {"properties": [row dicts with dates as ISO strings], "total": int,
        "page": int, "limit": int, "num_pages": int}.

    Raises:
        sqlalchemy.exc.SQLAlchemyError: When the database query fails. Errors
        propagate so the caller can answer 5xx instead of an empty page.
    """
    if page is None or page < 1:
        page = 1
    if limit is None or limit < 1:
        limit = 10

    where_clauses = ["is_deleted = false", "is_active = true"]
    params: Dict = {}

    if not _is_blank(query):
        params["q"] = f"%{query.strip()}%"
        where_clauses.append(
            "("
            "title ILIKE :q OR "
            "description ILIKE :q OR "
            "address ILIKE :q OR "
            "city_province ILIKE :q OR "
            "district ILIKE :q"
            ")"
        )

    for column, key, value, operator in (
        ("price_range", "min_price", min_price, ">="),
        ("price_range", "max_price", max_price, "<="),
        ("area", "min_area", min_area, ">="),
        ("area", "max_area", max_area, "<="),
        ("bedrooms", "bedrooms", bedrooms, ">="),
        ("bathrooms", "bathrooms", bathrooms, ">="),
    ):
        if value is not None:
            where_clauses.append(f"{column} {operator} :{key}")
            params[key] = value

    _add_csv_filter(where_clauses, params, "legal_status", legal_status)
    _add_csv_filter(where_clauses, params, "furnishing", furniture)
    _add_csv_filter(where_clauses, params, "house_direction", house_direction)
    _add_csv_filter(where_clauses, params, "balcony_direction", balcony_direction)

    if min_frontage is not None:
        where_clauses.append("frontage >= :min_frontage")
        params["min_frontage"] = min_frontage
    if min_road_width is not None:
        where_clauses.append("access_road >= :min_road_width")
        params["min_road_width"] = min_road_width

    _add_csv_filter(where_clauses, params, "property_type", property_type)
    _add_csv_filter(where_clauses, params, "district", district)

    amenity_filters = amenity_filters or {}
    for field in AMENITY_FIELDS:
        value = amenity_filters.get(field)
        if value is not None:
            where_clauses.append(f"{field} = :{field}")
            params[field] = value

    where_sql = " AND ".join(where_clauses)

    sort_by = (sort_by or "newest").strip().lower()
    sort_order = (sort_order or "desc").strip().lower()
    sort_order_sql = "ASC" if sort_order == "asc" else "DESC"
    sort_map = {
        "newest": f"id {sort_order_sql}",
        "price_asc": "price_range ASC NULLS LAST",
        "price_desc": "price_range DESC NULLS LAST",
        "area_asc": "area ASC NULLS LAST",
        "area_desc": "area DESC NULLS LAST",
    }
    if sort_by == "oldest":
        order_by_sql = "created_at ASC"
    else:
        order_by_sql = sort_map.get(sort_by, f"created_at {sort_order_sql}")

    properties = postgres_client.fetch_mappings(
        f"""
            SELECT {SELECT_COLUMNS}
            FROM properties
            WHERE {where_sql}
            ORDER BY {order_by_sql}
            LIMIT :limit OFFSET :offset
        """,
        **params,
        limit=limit,
        offset=(page - 1) * limit,
    )
    total_rows = postgres_client.fetch_mappings(
        f"SELECT COUNT(*) AS total FROM properties WHERE {where_sql}",
        **params,
    )

    total = int(total_rows[0]["total"]) if total_rows else 0
    num_pages = int(math.ceil(total / limit))

    for item in properties:
        for key, value in list(item.items()):
            if isinstance(value, (datetime, date)):
                item[key] = value.isoformat()

    return {
        "properties": properties,
        "total": total,
        "page": page,
        "limit": limit,
        "num_pages": num_pages,
    }

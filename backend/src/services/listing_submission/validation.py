"""Validate posted listings against the listing schema and report what was converted or refused.

A listing is a JSON object in the shape of docs/plan.html: required fields at the top level,
optional fields inside "metadata". People get the shape wrong as often as the values, so a
field is found wherever it is (top level or metadata) and under its common other names. A
missing or unusable required field makes the listing invalid; an unusable optional field is
dropped with a warning and the listing stays valid.

Location fields are checked together against the official 2025 wards (see places.py and
geography.py): an old ward name becomes its new ward, a missing former district is filled in when
the ward came from one district only, the pair must agree, and coordinates must lie in the city and
near the ward. A geocoder, when given (zip uploads, processed on the server), fills missing
coordinates from the street.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Callable, Dict, List, Optional, Protocol, Tuple

from loguru import logger

from src.services.listing_submission import coercion, vocabulary
from src.services.listing_submission.coercion import CoercionError, Problem, describe
from src.services.listing_submission.geography import Ward
from src.services.listing_submission.places import WardMatch, location_directory
from src.services.listing_submission.vocabulary import FIELD_LABELS


class Geocoder(Protocol):
    """Finds coordinates for an address; see geocoding.NominatimGeocoder."""

    def search(self, query: str) -> Optional[Tuple[float, float]]:
        """(latitude, longitude) of the best match, or None when nothing is found."""

# Rough bounds that pick the reading of a typed coordinate; the city boundary decides the rest.
LATITUDE_RANGE = (10.3, 11.6)
LONGITUDE_RANGE = (106.3, 107.6)
MAXIMUM_AMENITIES = len(vocabulary.AMENITY_CATEGORIES) * vocabulary.AMENITIES_PER_CATEGORY


@dataclass
class ListingReport:
    """Outcome of validating one listing.

    Attributes:
        index: Position of the listing in the request.
        reference: The listing_id the user gave, to find the listing in their file; the server
            assigns its own id when the listing is saved.
        listing: The listing in canonical form (required fields, then "metadata"); fields that
            failed are absent.
        errors: Problems that make the listing invalid.
        warnings: Problems with optional fields, which were dropped.
        conversions: Values and field names that were rewritten, so the user can check them.
    """

    index: int
    reference: Optional[str] = None
    listing: Dict[str, Any] = field(default_factory=dict)
    errors: List[Dict[str, Any]] = field(default_factory=list)
    warnings: List[Dict[str, Any]] = field(default_factory=list)
    conversions: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def is_valid(self) -> bool:
        """Whether the listing can be saved: no errors."""
        return not self.errors

    def add_problem(self, is_error: bool, path: str, value: Any, problem: Problem) -> None:
        """Record a problem as an error or a warning.

        Args:
            is_error: True for an error, False for a warning.
            path: Field path, e.g. "price_billion_vnd" or "metadata.nearby_amenities[2].name".
            value: The value as typed.
            problem: Code, message and suggestion.
        """
        entry = {"field": path, "label": _label(path), "value": describe(value), "code": problem.code,
                 "message": problem.message}
        if problem.suggestion:
            entry["suggestion"] = problem.suggestion
        (self.errors if is_error else self.warnings).append(entry)

    def add_conversion(self, path: str, kind: str, before: Any, after: Any, note: Optional[str] = None) -> None:
        """Record a rewrite.

        Args:
            path: Field path.
            kind: "value" for a converted value, "field" for a renamed field, "derived" for a value
                filled in from other fields, "geocoded" for coordinates found from the address.
            before: The value or field name as typed (None when the field was absent).
            after: The canonical value or field name.
            note: Optional Vietnamese explanation, e.g. which old ward was merged into which.
        """
        entry = {"field": path, "kind": kind, "from": describe(before), "to": describe(after)}
        if note:
            entry["note"] = note
        self.conversions.append(entry)

    def as_dict(self) -> Dict[str, Any]:
        """The report as JSON: index, reference, is_valid, listing, errors, warnings, conversions."""
        return {"index": self.index, "reference": self.reference, "is_valid": self.is_valid, "listing": self.listing,
                "errors": self.errors, "warnings": self.warnings, "conversions": self.conversions}


def _label(path: str) -> str:
    """Vietnamese name of a field path, for people reading the report."""
    leaf = path.split(".")[-1].split("[")[0]
    return FIELD_LABELS.get(leaf, leaf)


def _problem(code: str, message: str, suggestion: Optional[str] = None) -> Problem:
    """Shorthand for a Problem built in this module."""
    return Problem(code, message, suggestion)


def _is_blank(value: Any) -> bool:
    """Whether a typed value counts as absent: None, an empty or blank string, or "null"-like text."""
    return value is None or (isinstance(value, str) and value.strip().lower() in ("", "null", "none", "nan", "-"))


def _collect_fields(document: Dict[str, Any], report: ListingReport) -> Dict[str, Tuple[str, Any]]:
    """Find every schema field in a document, at the top level or in metadata, under any name.

    Args:
        document: The listing as posted.
        report: Receives renamed fields as conversions and unknown or repeated fields as warnings.

    Returns:
        dict: Schema field -> (path where it was found, value as typed).
    """
    found: Dict[str, Tuple[str, Any]] = {}
    sources: List[Tuple[str, Dict[str, Any]]] = [("", document)]
    for key, value in document.items():
        if vocabulary.resolve_field(key) == "metadata":
            try:
                sources.append((f"{key}.", coercion.parse_embedded_json(value, dict)))
            except CoercionError as error:
                report.add_problem(False, key, value, error.problem)
    for prefix, source in sources:
        for key, value in source.items():
            path = f"{prefix}{key}"
            name = vocabulary.resolve_field(key)
            if name == "metadata" and not prefix:
                continue
            if name is None:
                report.add_problem(False, path, value, _problem("UNKNOWN_FIELD", f"Trường «{key}» không có trong định dạng chuẩn, đã bỏ qua."))
                continue
            if name in found:
                report.add_problem(False, path, value, _problem(
                    "DUPLICATE_FIELD", f"Trường «{key}» trùng với trường «{found[name][0]}»; chỉ giữ giá trị đầu tiên."))
                continue
            if key != name:
                report.add_conversion(name, "field", path, name)
            found[name] = (path, value)
    return found


def _convert(report: ListingReport, found: Dict[str, Tuple[str, Any]], name: str, is_required: bool,
             converter: Callable[[Any], Any]) -> Any:
    """Convert one field, recording a conversion when the value changed and a problem when it failed.

    Args:
        report: Receives the outcome.
        found: Fields collected by _collect_fields.
        name: Schema field.
        is_required: Missing or failing values are errors when True, warnings when False.
        converter: Turns the typed value into the canonical one; raises CoercionError.

    Returns:
        Any: The canonical value, or None when the field is absent or failed.
    """
    path, value = found.get(name, (name, None))
    if _is_blank(value):
        if is_required:
            report.add_problem(True, name, value, _problem(
                "MISSING_REQUIRED", f"Thiếu trường bắt buộc «{FIELD_LABELS[name]}» ({name})."))
        return None
    try:
        converted = converter(value)
    except CoercionError as error:
        report.add_problem(is_required, path, value, error.problem)
        return None
    canonical = converted.isoformat() if isinstance(converted, date) else converted
    if canonical != value:
        report.add_conversion(name, "value", value, canonical)
    return canonical


def _convert_coordinates(report: ListingReport, found: Dict[str, Tuple[str, Any]]) -> Optional[Tuple[float, float]]:
    """Latitude and longitude from separate fields or one combined text; both or neither.

    Swapped values (longitude in the latitude field) are put back with a conversion note.
    Coordinates are optional, so every problem is a warning.

    Returns:
        tuple | None: (latitude, longitude) inside Ho Chi Minh City, or None.
    """
    latitude_value = found.get("latitude", ("latitude", None))[1]
    longitude_value = found.get("longitude", ("longitude", None))[1]
    combined_path, combined_value = found.get("coordinates", ("coordinates", None))
    if _is_blank(latitude_value) and _is_blank(longitude_value) and not _is_blank(combined_value):
        try:
            latitude_value, longitude_value = coercion.coordinate_pair_tokens(combined_value)
        except CoercionError as error:
            report.add_problem(False, combined_path, combined_value, error.problem)
            return None
    elif _is_blank(latitude_value) and _is_blank(longitude_value):
        return None
    if _is_blank(latitude_value) or _is_blank(longitude_value):
        missing = "longitude" if _is_blank(longitude_value) else "latitude"
        report.add_problem(False, missing, None, _problem(
            "INCOMPLETE_COORDINATES", "Cần cả vĩ độ và kinh độ; đã bỏ qua tọa độ."))
        return None
    try:
        latitude = coercion.to_decimal_degrees(latitude_value, *LATITUDE_RANGE)
        longitude = coercion.to_decimal_degrees(longitude_value, *LONGITUDE_RANGE)
    except CoercionError as first_error:
        try:
            latitude = coercion.to_decimal_degrees(longitude_value, *LATITUDE_RANGE)
            longitude = coercion.to_decimal_degrees(latitude_value, *LONGITUDE_RANGE)
        except CoercionError:
            report.add_problem(False, "latitude", latitude_value, _problem(
                first_error.problem.code, f"Không dùng được tọa độ ({describe(latitude_value)}, {describe(longitude_value)}); "
                f"đã bỏ qua. {first_error.problem.message}",
                "Vĩ độ khoảng 10,3–11,6; kinh độ khoảng 106,3–107,6."))
            return None
        report.add_conversion("latitude", "value", latitude_value, latitude)
        report.add_conversion("longitude", "value", longitude_value, longitude)
        report.add_problem(False, "latitude", latitude_value, _problem(
            "SWAPPED_COORDINATES", "Vĩ độ và kinh độ bị đảo chỗ; đã đổi lại."))
        return latitude, longitude
    for name, typed, canonical in (("latitude", latitude_value, latitude), ("longitude", longitude_value, longitude)):
        if typed != canonical:
            report.add_conversion(name, "value", typed, canonical)
    return latitude, longitude


def _coordinates_in_city(report: ListingReport, coordinates: Optional[Tuple[float, float]]) -> Optional[Tuple[float, float]]:
    """Drop coordinates outside Ho Chi Minh City's boundary, with a warning."""
    if coordinates and not location_directory().reference.is_in_city(*coordinates):
        report.add_problem(False, "latitude", list(coordinates), _problem(
            "OUTSIDE_CITY", "Tọa độ nằm ngoài TP.HCM; đã bỏ qua."))
        return None
    return coordinates


# Every amenity needs all four fields; a broken one is dropped and named in the warning.
AMENITY_CONVERTERS: Dict[str, Callable[[Any], Any]] = {
    "category": lambda value: coercion.to_category(value, vocabulary.AMENITY_CATEGORIES, "Nhóm tiện ích"),
    "name": lambda value: coercion.to_text(value, 1, 200),
    "driving_distance_km": coercion.to_distance_km,
    "driving_duration_minutes": coercion.to_duration_minutes,
}
AMENITY_FIELD_LABELS = {
    "category": "Nhóm tiện ích", "name": "Tên tiện ích",
    "driving_distance_km": "Quãng đường (km)", "driving_duration_minutes": "Thời gian (phút)",
}


def _convert_amenities(report: ListingReport, found: Dict[str, Tuple[str, Any]]) -> Optional[List[Dict[str, Any]]]:
    """Nearby amenities: a list of {category, name, driving_distance_km, driving_duration_minutes}.

    A broken item is dropped with a warning; at most 5 items per category are kept.

    Returns:
        list | None: The valid items, or None when there are none.
    """
    path, value = found.get("nearby_amenities", ("nearby_amenities", None))
    if _is_blank(value):
        return None
    try:
        items = coercion.parse_embedded_json(value, list)
    except CoercionError as error:
        report.add_problem(False, path, value, error.problem)
        return None
    kept: List[Dict[str, Any]] = []
    per_category: Dict[str, int] = {}
    for position, item in enumerate(items):
        item_path = f"{path}[{position}]"
        if not isinstance(item, dict):
            report.add_problem(False, item_path, item, _problem("INVALID_JSON", "Mỗi tiện ích phải là một JSON object {…}."))
            continue
        fields = {vocabulary.resolve_amenity_field(key): item[key] for key in item}
        amenity: Dict[str, Any] = {}
        for name, converter in AMENITY_CONVERTERS.items():
            value = fields.get(name)
            label = AMENITY_FIELD_LABELS[name]
            if _is_blank(value):
                report.add_problem(False, f"{item_path}.{name}", value, _problem(
                    "MISSING_REQUIRED", f"Tiện ích thiếu «{label}»; đã bỏ tiện ích này."))
                break
            try:
                amenity[name] = converter(value)
            except CoercionError as error:
                report.add_problem(False, f"{item_path}.{name}", value, Problem(
                    error.problem.code, f"{label}: {error.problem.message} Đã bỏ tiện ích này.", error.problem.suggestion))
                break
        if len(amenity) < len(AMENITY_CONVERTERS):
            continue
        per_category[amenity["category"]] = per_category.get(amenity["category"], 0) + 1
        if per_category[amenity["category"]] > vocabulary.AMENITIES_PER_CATEGORY:
            report.add_problem(False, item_path, item, _problem(
                "TOO_MANY_AMENITIES", f"Tối đa {vocabulary.AMENITIES_PER_CATEGORY} tiện ích mỗi nhóm; đã bỏ tiện ích này."))
            continue
        kept.append(amenity)
    return kept or None


def _resolve_location(report: ListingReport, found: Dict[str, Tuple[str, Any]],
                      coordinates: Optional[Tuple[float, float]], geocoder: Optional[Geocoder],
                      street: Optional[str]) -> Tuple[Optional[str], Optional[str], Optional[Tuple[float, float]]]:
    """Resolve ward, former district and coordinates together.

    Args:
        report: Receives errors, warnings and conversions.
        found: Fields collected by _collect_fields.
        coordinates: Parsed coordinates inside the city, or None.
        geocoder: Finds coordinates from the street when none were given; None to skip.
        street: The listing's street, used for geocoding.

    Returns:
        tuple: (ward name, former district, coordinates); a part that failed is None.
    """
    directory = location_directory()
    reference = directory.reference
    point_ward: Optional[Ward] = reference.ward_at(*coordinates) if coordinates else None

    former_path, former_value = found.get("former_district", ("former_district", None))
    former_district: Optional[str] = None
    if not _is_blank(former_value):
        try:
            former_district = directory.match_former_district(former_value)
        except CoercionError as error:
            report.add_problem(True, former_path, former_value, error.problem)
        else:
            if former_district != former_value:
                report.add_conversion("former_district", "value", former_value, former_district)

    ward_path, ward_value = found.get("ward", ("ward", None))
    match: Optional[WardMatch] = None
    if not _is_blank(ward_value):
        try:
            match = directory.match_ward(ward_value, former_district, point_ward)
        except CoercionError as error:
            report.add_problem(True, ward_path, ward_value, error.problem)
        else:
            if match.former_ward is not None:
                report.add_conversion("ward", "value", ward_value, match.ward.name,
                                      f"{match.former_ward.ward} ({match.former_ward.district}) thuộc {match.ward.name} "
                                      "từ 1/7/2025.")
            elif match.ward.name != ward_value:
                report.add_conversion("ward", "value", ward_value, match.ward.name)
    elif point_ward is not None:
        match = WardMatch(point_ward)
        report.add_conversion("ward", "derived", None, point_ward.name, "Lấy từ tọa độ.")
    else:
        report.add_problem(True, "ward", ward_value, _problem(
            "MISSING_REQUIRED", f"Thiếu trường bắt buộc «{FIELD_LABELS['ward']}» (ward)."))

    ward = match.ward if match else None
    if ward is not None and _is_blank(former_value):
        candidates = [match.former_ward.district] if match.former_ward else list(ward.former_districts)
        if len(candidates) == 1:
            former_district = candidates[0]
            report.add_conversion("former_district", "derived", None, former_district,
                                  f"{ward.name} trước 1/7/2025 thuộc {former_district}.")
        else:
            report.add_problem(True, "former_district", former_value, _problem(
                "MISSING_REQUIRED", f"Thiếu trường bắt buộc «{FIELD_LABELS['former_district']}» (former_district).",
                f"Trước 1/7/2025, địa bàn {ward.name} thuộc nhiều quận/huyện: {', '.join(candidates)}."))
    elif ward is not None and former_district is not None and not directory.was_in(ward, former_district):
        report.add_problem(True, former_path, former_value, _problem(
            "MISMATCHED_FORMER_DISTRICT", f"{ward.name} trước 1/7/2025 không thuộc «{former_district}».",
            f"{ward.name} trước đây thuộc: {', '.join(ward.former_districts)}."))
    elif _is_blank(former_value) and ward is None:
        report.add_problem(True, "former_district", former_value, _problem(
            "MISSING_REQUIRED", f"Thiếu trường bắt buộc «{FIELD_LABELS['former_district']}» (former_district)."))

    if ward is not None and coordinates and not reference.is_near_ward(ward, *coordinates):
        distance = reference.distance_to_ward_metres(ward, *coordinates)
        where = point_ward.name if point_ward else "ngoài ranh giới các phường/xã"
        report.add_problem(False, "latitude", list(coordinates), _problem(
            "COORDINATES_OUTSIDE_WARD", f"Tọa độ nằm ở {where}, cách {ward.name} khoảng {round(distance, -1):.0f} m.",
            "Vui lòng kiểm tra lại tọa độ hoặc phường/xã."))
    if ward is not None and coordinates is None and geocoder is not None and street:
        coordinates = _geocode(report, geocoder, street, ward)
    return (ward.name if ward else None), former_district, coordinates


def _geocode(report: ListingReport, geocoder: Geocoder, street: str, ward: Ward) -> Optional[Tuple[float, float]]:
    """Find coordinates from the street, kept only when they fall in the listing's ward.

    A geocoder failure is a warning: coordinates are optional and the listing stays valid.
    """
    query = f"{street}, {ward.name}, Thành phố Hồ Chí Minh"
    try:
        result = geocoder.search(query)
    except Exception:
        logger.exception("Geocoding failed")
        report.add_problem(False, "latitude", None, _problem("GEOCODER_UNAVAILABLE", "Dịch vụ tìm tọa độ đang lỗi, chưa lấy được tọa độ từ địa chỉ."))
        return None
    if result is None:
        report.add_problem(False, "latitude", query, _problem("ADDRESS_NOT_FOUND", "Không tìm thấy tọa độ cho địa chỉ."))
        return None
    reference = location_directory().reference
    if not reference.is_near_ward(ward, *result):
        report.add_problem(False, "latitude", list(result), _problem(
            "ADDRESS_OUTSIDE_WARD", f"Tọa độ tìm được từ địa chỉ không nằm trong {ward.name}; đã bỏ qua."))
        return None
    latitude, longitude = round(result[0], 7), round(result[1], 7)
    report.add_conversion("latitude", "geocoded", None, latitude, "Tìm từ địa chỉ qua OpenStreetMap.")
    report.add_conversion("longitude", "geocoded", None, longitude, "Tìm từ địa chỉ qua OpenStreetMap.")
    return latitude, longitude


def validate_listing(document: Any, index: int, today: date, geocoder: Optional[Geocoder] = None) -> ListingReport:
    """Validate one posted listing.

    Args:
        document: The listing as posted: a JSON object.
        index: Its position in the request, echoed in the report.
        today: Today's date in Vietnam; a missing posted_date becomes today, a later one is refused.
        geocoder: Finds coordinates from the street when none were given; None to skip (the form
            geocodes in the browser, zip uploads on the server).

    Returns:
        ListingReport: The canonical listing with every error, warning and conversion.
    """
    report = ListingReport(index=index)
    if not isinstance(document, dict):
        report.add_problem(True, "", document, _problem("NOT_AN_OBJECT", "Mỗi tin phải là một JSON object {…}."))
        return report
    found = _collect_fields(document, report)
    reference = found.get(vocabulary.REFERENCE_FIELD, (None, None))[1]
    if not _is_blank(reference) and isinstance(reference, (str, int)) and not isinstance(reference, bool):
        report.reference = str(reference).strip()

    if _is_blank(found.get("posted_date", (None, None))[1]):
        found["posted_date"] = ("posted_date", today.isoformat())
        report.add_conversion("posted_date", "derived", None, today.isoformat(), "Không có ngày đăng; đã dùng ngày hôm nay.")
    required_converters: Dict[str, Callable[[Any], Any]] = {
        "title": lambda value: coercion.to_text(value, 10, 300),
        "description": lambda value: coercion.to_text(value, 30, 20000, is_multiline=True),
        "property_type": lambda value: coercion.to_category(value, vocabulary.PROPERTY_TYPES, "Loại bất động sản"),
        "price_billion_vnd": coercion.to_price_billion,
        "area_m2": coercion.to_area_m2,
        "posted_date": lambda value: coercion.to_posted_date(value, today),
    }
    values: Dict[str, Any] = {name: _convert(report, found, name, True, converter)
                              for name, converter in required_converters.items()}

    optional_converters: Dict[str, Callable[[Any], Any]] = {
        "bedroom_count": coercion.to_room_count,
        "bathroom_count": coercion.to_room_count,
        "legal_status": lambda value: coercion.to_category(
            value, vocabulary.LEGAL_STATUSES, "Pháp lý", vocabulary.LEGAL_STATUS_RULES),
        "furnishing": lambda value: coercion.to_category(
            value, vocabulary.FURNISHINGS, "Nội thất", vocabulary.FURNISHING_RULES),
        "street": lambda value: coercion.to_text(value, 2, 200),
        "project": lambda value: coercion.to_text(value, 2, 200),
    }
    optional_values = {name: _convert(report, found, name, False, converter)
                       for name, converter in optional_converters.items()}
    coordinates = _coordinates_in_city(report, _convert_coordinates(report, found))
    values["ward"], values["former_district"], coordinates = _resolve_location(
        report, found, coordinates, geocoder, optional_values["street"])

    listing: Dict[str, Any] = {name: values[name] for name in vocabulary.REQUIRED_FIELDS if values.get(name) is not None}
    metadata: Dict[str, Any] = {name: optional_values[name] for name in ("bedroom_count", "bathroom_count", "legal_status", "furnishing")
                                if optional_values[name] is not None}
    if coordinates:
        metadata["latitude"], metadata["longitude"] = coordinates
    metadata.update({name: optional_values[name] for name in ("street", "project") if optional_values[name] is not None})
    amenities = _convert_amenities(report, found)
    if amenities:
        metadata["nearby_amenities"] = amenities
    listing["metadata"] = metadata
    report.listing = listing
    return report


def validate_listings(documents: List[Any], today: Optional[date] = None,
                      geocoder: Optional[Geocoder] = None) -> Dict[str, Any]:
    """Validate a batch of posted listings.

    Args:
        documents: Listings as posted.
        today: Today's date in Vietnam; defaults to the current one.
        geocoder: Passed to validate_listing; None to skip geocoding.

    Returns:
        dict: {"results": one report per listing in order, "valid_count", "invalid_count"}.
    """
    today = today or coercion.today_in_vietnam()
    results = [validate_listing(document, index, today, geocoder).as_dict() for index, document in enumerate(documents)]
    valid_count = sum(result["is_valid"] for result in results)
    return {"results": results, "valid_count": valid_count, "invalid_count": len(results) - valid_count}

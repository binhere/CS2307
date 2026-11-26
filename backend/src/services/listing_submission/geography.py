"""Ho Chi Minh City's official wards, their former districts and their boundaries.

Loads the reference files written by backend/scripts/export_address_reference.py and answers the
questions listing validation asks: which ward is this name, which new ward did this old ward
become, which ward contains this point, and how far is a point from a ward. Pure Python: point in
polygon by ray casting over at most a few thousand vertices per ward, after a bounding-box check.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

REFERENCE_DIRECTORY = Path(__file__).resolve().parent / "reference"
METRES_PER_DEGREE = 111_320.0
# Boundaries are simplified to about 5 m, so a point this close to a ward counts as inside it.
BOUNDARY_TOLERANCE_METRES = 100.0

Ring = List[List[float]]            # [[longitude, latitude], ...], closed
Polygon = List[Ring]                # outer ring, then holes
BoundingBox = Tuple[float, float, float, float]  # minimum longitude, minimum latitude, maximum longitude, maximum latitude


@dataclass(frozen=True)
class FormerWard:
    """A ward as it was before 1 July 2025.

    Attributes:
        district: Its district then, e.g. "Thành phố Thủ Đức".
        ward: Its name then, e.g. "Phường Thạnh Mỹ Lợi".
        is_divided: Whether it was split between several new wards.
    """

    district: str
    ward: str
    is_divided: bool


@dataclass(frozen=True)
class Ward:
    """An official ward (phường, xã or đặc khu) of Ho Chi Minh City.

    Attributes:
        code: Official code, e.g. "26734".
        name: Official name, e.g. "Phường Cát Lái".
        keyword: Accent-free administrative word: "phuong", "xa" or "dac khu".
        bare_name: Accent-free name without the keyword, e.g. "cat lai".
        former_districts: Districts its area belonged to before 1 July 2025.
        former_wards: The old wards it was made from.
    """

    code: str
    name: str
    keyword: str
    bare_name: str
    former_districts: Tuple[str, ...]
    former_wards: Tuple[FormerWard, ...]


def bounding_box(polygons: Sequence[Polygon]) -> BoundingBox:
    """Smallest box around the outer rings of some polygons."""
    longitudes = [point[0] for polygon in polygons for point in polygon[0]]
    latitudes = [point[1] for polygon in polygons for point in polygon[0]]
    return min(longitudes), min(latitudes), max(longitudes), max(latitudes)


def is_in_ring(longitude: float, latitude: float, ring: Ring) -> bool:
    """Ray casting: whether a point lies inside a closed ring."""
    is_inside = False
    previous_longitude, previous_latitude = ring[-1]
    for current_longitude, current_latitude in ring:
        if (current_latitude > latitude) != (previous_latitude > latitude):
            crossing = (previous_longitude - current_longitude) * (latitude - current_latitude) / (
                previous_latitude - current_latitude) + current_longitude
            if longitude < crossing:
                is_inside = not is_inside
        previous_longitude, previous_latitude = current_longitude, current_latitude
    return is_inside


def is_in_polygons(longitude: float, latitude: float, polygons: Sequence[Polygon]) -> bool:
    """Whether a point lies inside any polygon: inside its outer ring and outside all its holes."""
    for polygon in polygons:
        if is_in_ring(longitude, latitude, polygon[0]) and not any(
                is_in_ring(longitude, latitude, hole) for hole in polygon[1:]):
            return True
    return False


def distance_to_polygons_metres(longitude: float, latitude: float, polygons: Sequence[Polygon]) -> float:
    """Distance from a point to the nearest edge of some polygons, 0 when inside.

    Uses an equirectangular projection around the point, accurate to well under 1% at city scale.
    """
    if is_in_polygons(longitude, latitude, polygons):
        return 0.0
    scale_x = METRES_PER_DEGREE * math.cos(math.radians(latitude))
    nearest = math.inf
    for polygon in polygons:
        for ring in polygon:
            for (start_longitude, start_latitude), (end_longitude, end_latitude) in zip(ring, ring[1:]):
                start_x, start_y = (start_longitude - longitude) * scale_x, (start_latitude - latitude) * METRES_PER_DEGREE
                end_x, end_y = (end_longitude - longitude) * scale_x, (end_latitude - latitude) * METRES_PER_DEGREE
                delta_x, delta_y = end_x - start_x, end_y - start_y
                length_squared = delta_x ** 2 + delta_y ** 2
                position = 0.0 if length_squared == 0 else max(0.0, min(1.0, -(start_x * delta_x + start_y * delta_y) / length_squared))
                nearest = min(nearest, math.hypot(start_x + position * delta_x, start_y + position * delta_y))
    return nearest


class AddressReference:
    """The official wards, former districts and boundaries, indexed for validation.

    Attributes:
        wards: All 168 wards, by code order.
        ward_by_name: Ward by official name.
        former_districts: Accepted former district names.
        former_district_group: Former district -> the unit it counts as when checking a ward
            ("Quận 2" -> "Thành phố Thủ Đức"); a name maps to itself when it has no group.
    """

    def __init__(self, reference: dict, boundaries: dict):
        """Index the two reference files.

        Args:
            reference: Content of hcmc_wards.json.
            boundaries: Content of hcmc_boundaries.json.
        """
        self.wards: List[Ward] = [
            Ward(item["code"], item["name"], item["keyword"], item["bare_name"], tuple(item["former_districts"]),
                 tuple(FormerWard(former["district"], former["ward"], former["is_divided"]) for former in item["former_wards"]))
            for item in reference["wards"]
        ]
        self.ward_by_name: Dict[str, Ward] = {ward.name: ward for ward in self.wards}
        self.former_districts: List[str] = [item["name"] for item in reference["former_districts"]]
        self.former_district_group: Dict[str, str] = {
            item["name"]: item["same_as"] or item["name"] for item in reference["former_districts"]}
        self._polygons: Dict[str, List[Polygon]] = boundaries["wards"]
        self._boxes: Dict[str, BoundingBox] = {code: bounding_box(polygons) for code, polygons in self._polygons.items()}
        self._city: List[Polygon] = boundaries["city"]
        self._city_box = bounding_box(self._city)
        self._ward_by_code: Dict[str, Ward] = {ward.code: ward for ward in self.wards}

    def group_of(self, former_district: str) -> str:
        """The unit a former district counts as when checking a ward ("Quận 9" -> "Thành phố Thủ Đức")."""
        return self.former_district_group.get(former_district, former_district)

    def is_in_city(self, latitude: float, longitude: float) -> bool:
        """Whether a point lies in Ho Chi Minh City (or within the boundary tolerance of it)."""
        minimum_longitude, minimum_latitude, maximum_longitude, maximum_latitude = self._city_box
        margin = BOUNDARY_TOLERANCE_METRES / METRES_PER_DEGREE * 2
        if not (minimum_longitude - margin <= longitude <= maximum_longitude + margin
                and minimum_latitude - margin <= latitude <= maximum_latitude + margin):
            return False
        return distance_to_polygons_metres(longitude, latitude, self._city) <= BOUNDARY_TOLERANCE_METRES

    def ward_at(self, latitude: float, longitude: float) -> Optional[Ward]:
        """The ward containing a point, or None (outside the city or in a gap between simplified edges)."""
        for code, (minimum_longitude, minimum_latitude, maximum_longitude, maximum_latitude) in self._boxes.items():
            if minimum_longitude <= longitude <= maximum_longitude and minimum_latitude <= latitude <= maximum_latitude:
                if is_in_polygons(longitude, latitude, self._polygons[code]):
                    return self._ward_by_code[code]
        return None

    def distance_to_ward_metres(self, ward: Ward, latitude: float, longitude: float) -> float:
        """Distance from a point to a ward's boundary in metres, 0 when inside."""
        return distance_to_polygons_metres(longitude, latitude, self._polygons[ward.code])

    def is_near_ward(self, ward: Ward, latitude: float, longitude: float) -> bool:
        """Whether a point lies in a ward or within the boundary tolerance of it."""
        minimum_longitude, minimum_latitude, maximum_longitude, maximum_latitude = self._boxes[ward.code]
        margin = BOUNDARY_TOLERANCE_METRES / METRES_PER_DEGREE * 2
        if not (minimum_longitude - margin <= longitude <= maximum_longitude + margin
                and minimum_latitude - margin <= latitude <= maximum_latitude + margin):
            return False
        return self.distance_to_ward_metres(ward, latitude, longitude) <= BOUNDARY_TOLERANCE_METRES


@lru_cache(maxsize=1)
def address_reference() -> AddressReference:
    """The address reference, loaded once from the reference directory."""
    reference = json.loads((REFERENCE_DIRECTORY / "hcmc_wards.json").read_text(encoding="utf-8"))
    boundaries = json.loads((REFERENCE_DIRECTORY / "hcmc_boundaries.json").read_text(encoding="utf-8"))
    return AddressReference(reference, boundaries)

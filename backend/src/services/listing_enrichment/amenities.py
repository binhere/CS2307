"""The amenities nearest to a point, per category, with an estimated drive to each.

Reads reference/amenities.json (written by scripts/export_amenity_reference.py): 10,775
OpenStreetMap points of interest in 9 categories, and per category the median road factor and
driving speed the offline pipeline measured with OSRM. The server has no road network, so a
drive is estimated: straight-line distance x road_factor, at speed_kmh. Search filters ("trong
vòng 1 km") and ranking read these estimates exactly as they read routed distances.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Dict, List

import numpy as np

REFERENCE_FILE = Path(__file__).resolve().parent / "reference" / "amenities.json"
NEAREST_PER_CATEGORY = 5
EARTH_RADIUS_KM = 6371.0088


@dataclass(frozen=True)
class NearbyAmenity:
    """One of the nearest amenities of a category, as the search tables and the graph store it.

    Attributes:
        amenity_id: OpenStreetMap id, e.g. "node/1001114451".
        name: Its name.
        category: One of the 9 categories, e.g. "school".
        latitude: Decimal degrees.
        longitude: Decimal degrees.
        street: Its street, or "".
        rank: 1 for the nearest of its category, up to NEAREST_PER_CATEGORY.
        straight_line_km: Great-circle distance.
        driving_distance_km: Estimated drive.
        driving_duration_min: Estimated drive time.
        threshold_km: Drive within which the category counts as "near" (near_school, ...).
    """

    amenity_id: str
    name: str
    category: str
    latitude: float
    longitude: float
    street: str
    rank: int
    straight_line_km: float
    driving_distance_km: float
    driving_duration_min: float
    threshold_km: float

    @property
    def is_within_threshold(self) -> bool:
        """Whether the drive is short enough for the category to count as near."""
        return self.driving_distance_km <= self.threshold_km


class AmenityReference:
    """The amenities grouped by category, with coordinates in radians for fast distances."""

    def __init__(self, reference: Dict):
        """Index the reference file.

        Args:
            reference: Content of amenities.json.
        """
        self.calibration: Dict[str, Dict[str, float]] = reference["calibration"]
        self.near_threshold_km: Dict[str, float] = reference["near_threshold_km"]
        self._by_category: Dict[str, Dict[str, np.ndarray]] = {}
        rows_by_category: Dict[str, List[list]] = {}
        for row in reference["amenities"]:
            rows_by_category.setdefault(row[2], []).append(row)
        for category, rows in rows_by_category.items():
            self._by_category[category] = {
                "rows": np.array(rows, dtype=object),
                "latitude": np.radians(np.array([row[3] for row in rows], dtype=float)),
                "longitude": np.radians(np.array([row[4] for row in rows], dtype=float)),
            }

    def nearest(self, latitude: float, longitude: float, per_category: int = NEAREST_PER_CATEGORY) -> List[NearbyAmenity]:
        """The nearest amenities of every category to a point.

        Args:
            latitude: Decimal degrees.
            longitude: Decimal degrees.
            per_category: How many per category.

        Returns:
            list: NearbyAmenity items, by category then rank.
        """
        point_latitude, point_longitude = np.radians(latitude), np.radians(longitude)
        found: List[NearbyAmenity] = []
        for category in sorted(self._by_category):
            index = self._by_category[category]
            # Haversine distance to every amenity of the category: a few thousand at most.
            half_chord = (np.sin((index["latitude"] - point_latitude) / 2) ** 2
                          + np.cos(point_latitude) * np.cos(index["latitude"]) * np.sin((index["longitude"] - point_longitude) / 2) ** 2)
            distances = 2 * EARTH_RADIUS_KM * np.arcsin(np.sqrt(np.clip(half_chord, 0, 1)))
            order = np.argsort(distances)[:per_category]
            road_factor = self.calibration[category]["road_factor"]
            speed_kmh = self.calibration[category]["speed_kmh"]
            for rank, position in enumerate(order, start=1):
                amenity_id, name, _, amenity_latitude, amenity_longitude, street = index["rows"][position]
                straight = float(distances[position])
                driving = straight * road_factor
                found.append(NearbyAmenity(
                    amenity_id=str(amenity_id), name=str(name), category=category, latitude=float(amenity_latitude),
                    longitude=float(amenity_longitude), street=str(street or ""), rank=rank,
                    straight_line_km=round(straight, 4), driving_distance_km=round(driving, 4),
                    driving_duration_min=round(driving / speed_kmh * 60, 2),
                    threshold_km=float(self.near_threshold_km[category]),
                ))
        return found


@lru_cache(maxsize=1)
def amenity_reference() -> AmenityReference:
    """The amenity reference, loaded once from the reference directory."""
    return AmenityReference(json.loads(REFERENCE_FILE.read_text(encoding="utf-8")))

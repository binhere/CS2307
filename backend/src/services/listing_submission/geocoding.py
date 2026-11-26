"""Server-side geocoding of posted listings' streets.

The form's map finds a point in the user's browser; listings saved without coordinates (most zip
uploads) are looked up afterwards by scheduled_geocoding.py, which asks Nominatim
(OpenStreetMap) through this class. The public Nominatim service allows at most one request per
second, an identifying User-Agent and no heavy bulk use
(operations.osmfoundation.org/policies/nominatim), so requests are serialised one second apart
across threads and repeated addresses are answered from a cache. Point GEOCODER_URL at a
self-hosted Nominatim to lift the limit.
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from typing import Callable, Optional, Tuple

import requests

from src.settings.config import config_value

DEFAULT_GEOCODER_URL = "https://nominatim.openstreetmap.org"
DEFAULT_USER_AGENT = "real-estate-listing-geocoder/1.0"
# Ho Chi Minh City since 1 July 2025 (with Bình Dương and Bà Rịa - Vũng Tàu): west, north, east, south.
CITY_VIEWBOX = "106.3,11.6,107.6,10.3"
CACHE_SIZE = 2048


class NominatimGeocoder:
    """Finds the coordinates of an address in Ho Chi Minh City through a Nominatim server."""

    def __init__(self, base_url: Optional[str] = None, user_agent: Optional[str] = None,
                 minimum_interval_seconds: float = 1.0, timeout_seconds: float = 10.0,
                 session: Optional[requests.Session] = None, clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep):
        """Configure the geocoder.

        Args:
            base_url: Nominatim server; defaults to GEOCODER_URL, then the public server.
            user_agent: Identifies this application to the server; defaults to GEOCODER_USER_AGENT.
            minimum_interval_seconds: Shortest time between two requests (1 s on the public server).
            timeout_seconds: Per-request timeout.
            session: HTTP session; one is created when omitted (tests pass a fake).
            clock: Monotonic clock in seconds (tests pass a fake).
            sleep: Waits a number of seconds (tests pass a fake).
        """
        self.base_url = (base_url or config_value("GEOCODER_URL", "") or DEFAULT_GEOCODER_URL).rstrip("/")
        self.user_agent = user_agent or config_value("GEOCODER_USER_AGENT", "") or DEFAULT_USER_AGENT
        self.minimum_interval_seconds = minimum_interval_seconds
        self.timeout_seconds = timeout_seconds
        self.session = session or requests.Session()
        self.clock = clock
        self.sleep = sleep
        self._lock = threading.Lock()
        self._last_request_at: Optional[float] = None
        self._cache: "OrderedDict[str, Optional[Tuple[float, float]]]" = OrderedDict()

    def search(self, query: str) -> Optional[Tuple[float, float]]:
        """Coordinates of the best match for an address.

        Args:
            query: Free-text address, e.g. "Đường Số 60, Phường Cát Lái, Thành phố Hồ Chí Minh".

        Returns:
            tuple | None: (latitude, longitude), or None when nothing in the city matches.

        Raises:
            requests.RequestException: The server could not be reached or answered an error;
                failures are not cached, so a later call retries.
        """
        key = " ".join(query.lower().split())
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
                return self._cache[key]
            if self._last_request_at is not None:
                wait = self.minimum_interval_seconds - (self.clock() - self._last_request_at)
                if wait > 0:
                    self.sleep(wait)
            try:
                response = self.session.get(
                    f"{self.base_url}/search",
                    params={"q": query, "format": "jsonv2", "limit": 1, "countrycodes": "vn",
                            "viewbox": CITY_VIEWBOX, "bounded": 1, "accept-language": "vi"},
                    headers={"User-Agent": self.user_agent},
                    timeout=self.timeout_seconds,
                )
            finally:
                self._last_request_at = self.clock()
            response.raise_for_status()
            results = response.json()
            result = (float(results[0]["lat"]), float(results[0]["lon"])) if results else None
            self._cache[key] = result
            if len(self._cache) > CACHE_SIZE:
                self._cache.popitem(last=False)
            return result

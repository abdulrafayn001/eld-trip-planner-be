from __future__ import annotations

import hashlib
import threading
import time
from typing import Final

import httpx
from django.conf import settings
from django.core.cache import cache

CACHE_PREFIX: Final = "geocoding:nominatim:"
CACHE_TIMEOUT_SECONDS: Final = 60 * 60 * 24 * 30  # 30 days
MIN_REQUEST_INTERVAL_SECONDS: Final = 1.0
REQUEST_TIMEOUT_SECONDS: Final = 10.0

_throttle_lock = threading.Lock()
_last_request_time: float = 0.0


class GeocodingError(RuntimeError):
    """Raised when Nominatim returns no result or fails to respond."""


def _cache_key(query: str) -> str:
    digest = hashlib.sha1(query.strip().lower().encode("utf-8")).hexdigest()
    return CACHE_PREFIX + digest


def _wait_for_rate_limit() -> None:
    global _last_request_time
    with _throttle_lock:
        elapsed = time.monotonic() - _last_request_time
        if 0 < elapsed < MIN_REQUEST_INTERVAL_SECONDS:
            time.sleep(MIN_REQUEST_INTERVAL_SECONDS - elapsed)
        _last_request_time = time.monotonic()


def geocode(query: str) -> tuple[float, float, str]:
    """Resolve `query` to ``(lat, lng, display_label)``.

    Results are cached in the Django cache for `CACHE_TIMEOUT_SECONDS`.
    Outbound requests are throttled to one per second per process per
    Nominatim TOS. Raises `GeocodingError` if no match is found.
    """
    if not query or not query.strip():
        raise GeocodingError("empty query")

    key = _cache_key(query)
    cached = cache.get(key)
    if cached is not None:
        return cached

    _wait_for_rate_limit()
    response = httpx.get(
        settings.NOMINATIM_URL,
        params={"q": query, "format": "json", "limit": 1},
        headers={"User-Agent": settings.NOMINATIM_USER_AGENT},
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    payload = response.json()
    if not payload:
        raise GeocodingError(f"no results for {query!r}")

    top = payload[0]
    result = (float(top["lat"]), float(top["lon"]), top["display_name"])
    cache.set(key, result, CACHE_TIMEOUT_SECONDS)
    return result

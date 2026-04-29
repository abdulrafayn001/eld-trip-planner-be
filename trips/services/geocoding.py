"""Nominatim geocoding service.

Single responsibility: talk to Nominatim. Two callers:

- ``search_locations(q, limit)`` — returns up to ``limit`` candidates for the
  autocomplete dropdown. Per-query results cached and per-process throttled
  to honour Nominatim's 1 req/s usage policy.
- ``geocode(q)`` — convenience wrapper that returns the top match as
  ``(lat, lng, label)`` (kept for ad-hoc backfill / tests).
"""
from __future__ import annotations

import hashlib
import threading
import time
from typing import Final, TypedDict

import httpx
from django.conf import settings
from django.core.cache import cache

CACHE_PREFIX: Final = "geocoding:nominatim:"
CACHE_TIMEOUT_SECONDS: Final = 60 * 60 * 24 * 30  # 30 days
MIN_REQUEST_INTERVAL_SECONDS: Final = 1.0
REQUEST_TIMEOUT_SECONDS: Final = 10.0
SEARCH_LIMIT_MAX: Final = 8

_throttle_lock = threading.Lock()
_last_request_time: float = 0.0


class LocationCandidate(TypedDict):
    label: str
    lat: float
    lng: float
    place_id: str


class GeocodingError(RuntimeError):
    """Raised when Nominatim returns no result or fails to respond."""


def _cache_key(query: str, limit: int) -> str:
    digest = hashlib.sha1(
        f"{limit}:{query.strip().lower()}".encode("utf-8")
    ).hexdigest()
    return CACHE_PREFIX + digest


def _wait_for_rate_limit() -> None:
    global _last_request_time
    with _throttle_lock:
        elapsed = time.monotonic() - _last_request_time
        if 0 < elapsed < MIN_REQUEST_INTERVAL_SECONDS:
            time.sleep(MIN_REQUEST_INTERVAL_SECONDS - elapsed)
        _last_request_time = time.monotonic()


def search_locations(query: str, limit: int = 5) -> list[LocationCandidate]:
    """Return up to ``limit`` Nominatim matches for ``query``.

    Empty / blank queries short-circuit to ``[]`` so the autocomplete UI
    can call this on every keystroke without provoking 400s. Successful
    responses (including "no matches") are cached.
    """
    if not query or not query.strip():
        return []

    bounded = max(1, min(limit, SEARCH_LIMIT_MAX))
    key = _cache_key(query, bounded)
    cached = cache.get(key)
    if cached is not None:
        return cached

    _wait_for_rate_limit()
    response = httpx.get(
        settings.NOMINATIM_URL,
        params={"q": query, "format": "json", "limit": bounded},
        headers={"User-Agent": settings.NOMINATIM_USER_AGENT},
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    payload = response.json() or []

    results: list[LocationCandidate] = [
        {
            "label": item["display_name"],
            "lat": float(item["lat"]),
            "lng": float(item["lon"]),
            "place_id": str(item.get("place_id", "")),
        }
        for item in payload
        if "lat" in item and "lon" in item and "display_name" in item
    ]
    cache.set(key, results, CACHE_TIMEOUT_SECONDS)
    return results


def geocode(query: str) -> tuple[float, float, str]:
    """Resolve ``query`` to ``(lat, lng, display_label)`` (top match).

    Raises :class:`GeocodingError` if no match is found. Useful for
    server-side enrichment paths that don't have a UI picker.
    """
    matches = search_locations(query, limit=1)
    if not matches:
        raise GeocodingError(f"no results for {query!r}")
    top = matches[0]
    return top["lat"], top["lng"], top["label"]

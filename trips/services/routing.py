"""Routing client: OSRM primary with OpenRouteService fallback.

OSRM (the public demo server by default) is queried first. On rate-limit
(HTTP 429), server error (HTTP 5xx), or transport-level failure, the request
is retried against OpenRouteService when ``ORS_API_KEY`` is configured.
A 4xx response other than 429 is treated as a permanent rejection (bad
coordinates, malformed request) and is *not* retried.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Final

import httpx
from django.conf import settings

METERS_PER_MILE: Final = 1609.344
SECONDS_PER_HOUR: Final = 3600.0
REQUEST_TIMEOUT_SECONDS: Final = 30.0
OSRM_RETRYABLE_STATUSES: Final = frozenset({429, 500, 502, 503, 504})
ORS_PROFILE: Final = "driving-hgv"  # heavy goods vehicle — appropriate for property-carrying CMV

Coord = tuple[float, float]  # (lat, lng) — matches the geocoding service output


@dataclass(frozen=True)
class RouteLeg:
    distance_mi: float
    duration_hr: float
    geometry: dict  # GeoJSON LineString


class RoutingError(RuntimeError):
    """Raised when no provider can compute a route."""


def route(from_coord: Coord, to_coord: Coord) -> RouteLeg:
    """Compute a driving route from ``from_coord`` to ``to_coord``.

    Tries OSRM first. On 429/5xx or transport failure, falls back to
    OpenRouteService when ``ORS_API_KEY`` is set. Raises ``RoutingError``
    if neither provider yields a usable route.
    """
    try:
        return _route_osrm(from_coord, to_coord)
    except httpx.HTTPError as exc:
        if not _is_retryable(exc):
            raise RoutingError(f"OSRM rejected request: {exc}") from exc
        if not settings.ORS_API_KEY:
            raise RoutingError(
                f"OSRM unavailable and no ORS_API_KEY configured: {exc}"
            ) from exc
        osrm_failure = exc

    try:
        return _route_ors(from_coord, to_coord)
    except httpx.HTTPError as exc:
        raise RoutingError(
            f"both providers failed (osrm: {osrm_failure}; ors: {exc})"
        ) from exc


def _is_retryable(exc: httpx.HTTPError) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in OSRM_RETRYABLE_STATUSES
    # Transport-level failures (timeouts, connection errors): worth retrying.
    return True


def _route_osrm(from_coord: Coord, to_coord: Coord) -> RouteLeg:
    coords = (
        f"{from_coord[1]},{from_coord[0]};"  # OSRM expects lng,lat
        f"{to_coord[1]},{to_coord[0]}"
    )
    url = f"{settings.OSRM_BASE_URL.rstrip('/')}/route/v1/driving/{coords}"
    response = httpx.get(
        url,
        params={"overview": "full", "geometries": "geojson"},
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    payload = response.json()
    routes = payload.get("routes") or []
    if not routes:
        raise RoutingError("OSRM returned no routes for the given coordinates")
    top = routes[0]
    return RouteLeg(
        distance_mi=top["distance"] / METERS_PER_MILE,
        duration_hr=top["duration"] / SECONDS_PER_HOUR,
        geometry=top["geometry"],
    )


def _route_ors(from_coord: Coord, to_coord: Coord) -> RouteLeg:
    url = f"{settings.ORS_BASE_URL.rstrip('/')}/v2/directions/{ORS_PROFILE}/geojson"
    response = httpx.post(
        url,
        json={
            "coordinates": [
                [from_coord[1], from_coord[0]],
                [to_coord[1], to_coord[0]],
            ],
        },
        headers={
            "Authorization": settings.ORS_API_KEY,
            "Accept": "application/geo+json, application/json",
            "Content-Type": "application/json",
        },
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    payload = response.json()
    features = payload.get("features") or []
    if not features:
        raise RoutingError("ORS returned no features for the given coordinates")
    feature = features[0]
    summary = feature["properties"]["summary"]
    return RouteLeg(
        distance_mi=summary["distance"] / METERS_PER_MILE,
        duration_hr=summary["duration"] / SECONDS_PER_HOUR,
        geometry=feature["geometry"],
    )

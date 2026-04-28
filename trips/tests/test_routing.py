from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest

from trips.services import routing

LA = (34.0537, -118.2428)
DALLAS = (32.7767, -96.7970)


def _osrm_ok(distance_m: float = 1_609_344.0, duration_s: float = 36_000.0) -> MagicMock:
    response = MagicMock(spec=httpx.Response)
    response.status_code = 200
    response.raise_for_status.return_value = None
    response.json.return_value = {
        "routes": [
            {
                "distance": distance_m,
                "duration": duration_s,
                "geometry": {
                    "type": "LineString",
                    "coordinates": [[-118.2428, 34.0537], [-96.7970, 32.7767]],
                },
            }
        ]
    }
    return response


def _ors_ok(distance_m: float = 2_575_000.0, duration_s: float = 92_000.0) -> MagicMock:
    response = MagicMock(spec=httpx.Response)
    response.status_code = 200
    response.raise_for_status.return_value = None
    response.json.return_value = {
        "features": [
            {
                "properties": {"summary": {"distance": distance_m, "duration": duration_s}},
                "geometry": {
                    "type": "LineString",
                    "coordinates": [[-118.2428, 34.0537], [-96.7970, 32.7767]],
                },
            }
        ]
    }
    return response


def _http_failure(status_code: int) -> MagicMock:
    response = MagicMock(spec=httpx.Response)
    response.status_code = status_code
    response.raise_for_status.side_effect = httpx.HTTPStatusError(
        f"HTTP {status_code}",
        request=MagicMock(spec=httpx.Request),
        response=response,
    )
    return response


def test_route_osrm_happy_path_does_not_call_ors():
    with (
        patch("trips.services.routing.httpx.get", return_value=_osrm_ok()) as mock_get,
        patch("trips.services.routing.httpx.post") as mock_post,
    ):
        leg = routing.route(LA, DALLAS)

    assert leg.distance_mi == pytest.approx(1000.0, rel=1e-3)  # 1,609,344 m / 1609.344
    assert leg.duration_hr == pytest.approx(10.0)  # 36,000 s / 3600
    assert leg.geometry["type"] == "LineString"
    assert mock_get.call_count == 1
    assert mock_post.call_count == 0
    # OSRM coordinate order is lng,lat (not lat,lng)
    url = mock_get.call_args.args[0]
    assert "-118.2428,34.0537;-96.797,32.7767" in url


def test_route_falls_back_to_ors_on_osrm_429(settings):
    settings.ORS_API_KEY = "test-ors-key"
    with (
        patch("trips.services.routing.httpx.get", return_value=_http_failure(429)) as mock_get,
        patch("trips.services.routing.httpx.post", return_value=_ors_ok()) as mock_post,
    ):
        leg = routing.route(LA, DALLAS)

    assert mock_get.call_count == 1
    assert mock_post.call_count == 1
    assert leg.distance_mi == pytest.approx(2_575_000.0 / 1609.344)
    assert leg.duration_hr == pytest.approx(92_000.0 / 3600.0)
    headers = mock_post.call_args.kwargs["headers"]
    assert headers["Authorization"] == "test-ors-key"
    body = mock_post.call_args.kwargs["json"]
    assert body["coordinates"] == [[-118.2428, 34.0537], [-96.7970, 32.7767]]


def test_route_falls_back_to_ors_on_osrm_5xx(settings):
    settings.ORS_API_KEY = "test-ors-key"
    with (
        patch("trips.services.routing.httpx.get", return_value=_http_failure(503)),
        patch("trips.services.routing.httpx.post", return_value=_ors_ok()) as mock_post,
    ):
        routing.route(LA, DALLAS)

    assert mock_post.call_count == 1


def test_route_raises_when_both_providers_fail(settings):
    settings.ORS_API_KEY = "test-ors-key"
    with (
        patch("trips.services.routing.httpx.get", return_value=_http_failure(503)),
        patch("trips.services.routing.httpx.post", return_value=_http_failure(503)) as mock_post,
    ):
        with pytest.raises(routing.RoutingError):
            routing.route(LA, DALLAS)
    assert mock_post.call_count == 1


def test_route_raises_when_osrm_fails_and_no_ors_key(settings):
    settings.ORS_API_KEY = ""
    with (
        patch("trips.services.routing.httpx.get", return_value=_http_failure(500)),
        patch("trips.services.routing.httpx.post") as mock_post,
    ):
        with pytest.raises(routing.RoutingError):
            routing.route(LA, DALLAS)
    assert mock_post.call_count == 0


def test_route_does_not_fall_back_on_osrm_4xx(settings):
    """4xx (other than 429) is a permanent rejection — ORS would not help."""
    settings.ORS_API_KEY = "test-ors-key"
    with (
        patch("trips.services.routing.httpx.get", return_value=_http_failure(400)),
        patch("trips.services.routing.httpx.post") as mock_post,
    ):
        with pytest.raises(routing.RoutingError):
            routing.route(LA, DALLAS)
    assert mock_post.call_count == 0

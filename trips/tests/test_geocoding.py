from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest
from django.core.cache import cache

from trips.services import geocoding


@pytest.fixture(autouse=True)
def _reset_geocoding_state():
    cache.clear()
    geocoding._last_request_time = 0.0
    yield
    cache.clear()


def _ok_response(payload):
    response = MagicMock(spec=httpx.Response)
    response.json.return_value = payload
    response.raise_for_status.return_value = None
    return response


def test_geocode_returns_lat_lng_and_label():
    payload = [{"lat": "34.0537", "lon": "-118.2428", "display_name": "Los Angeles, CA, USA"}]
    with (
        patch("trips.services.geocoding.httpx.get", return_value=_ok_response(payload)) as mock_get,
        patch("trips.services.geocoding.time.sleep"),
    ):
        lat, lng, label = geocoding.geocode("Los Angeles, CA")

    assert lat == pytest.approx(34.0537)
    assert lng == pytest.approx(-118.2428)
    assert label == "Los Angeles, CA, USA"
    assert mock_get.call_count == 1
    kwargs = mock_get.call_args.kwargs
    assert kwargs["headers"]["User-Agent"]
    assert kwargs["params"] == {"q": "Los Angeles, CA", "format": "json", "limit": 1}


def test_geocode_caches_repeat_queries():
    payload = [{"lat": "32.7767", "lon": "-96.7970", "display_name": "Dallas, TX, USA"}]
    with (
        patch("trips.services.geocoding.httpx.get", return_value=_ok_response(payload)) as mock_get,
        patch("trips.services.geocoding.time.sleep"),
    ):
        first = geocoding.geocode("Dallas, TX")
        second = geocoding.geocode("Dallas, TX")

    assert first == second
    assert mock_get.call_count == 1


def test_geocode_normalizes_query_for_cache_key():
    payload = [{"lat": "33.7490", "lon": "-84.3880", "display_name": "Atlanta, GA, USA"}]
    with (
        patch("trips.services.geocoding.httpx.get", return_value=_ok_response(payload)) as mock_get,
        patch("trips.services.geocoding.time.sleep"),
    ):
        geocoding.geocode("Atlanta, GA")
        geocoding.geocode("  ATLANTA, GA  ")

    assert mock_get.call_count == 1


def test_geocode_raises_when_no_results():
    with (
        patch("trips.services.geocoding.httpx.get", return_value=_ok_response([])),
        patch("trips.services.geocoding.time.sleep"),
    ):
        with pytest.raises(geocoding.GeocodingError):
            geocoding.geocode("zzzzzzzz unmappable place")


def test_geocode_rejects_empty_query():
    with pytest.raises(geocoding.GeocodingError):
        geocoding.geocode("   ")

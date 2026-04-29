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


def test_search_locations_returns_candidates():
    payload = [
        {
            "lat": "34.0537",
            "lon": "-118.2428",
            "display_name": "Los Angeles, CA, USA",
            "place_id": 1,
        },
        {
            "lat": "34.0500",
            "lon": "-118.2500",
            "display_name": "Downtown LA",
            "place_id": 2,
        },
    ]
    with (
        patch("trips.services.geocoding.httpx.get", return_value=_ok_response(payload)) as mock_get,
        patch("trips.services.geocoding.time.sleep"),
    ):
        results = geocoding.search_locations("Los Angeles", limit=5)

    assert len(results) == 2
    assert results[0] == {
        "label": "Los Angeles, CA, USA",
        "lat": pytest.approx(34.0537),
        "lng": pytest.approx(-118.2428),
        "place_id": "1",
    }
    kwargs = mock_get.call_args.kwargs
    assert kwargs["headers"]["User-Agent"]
    assert kwargs["params"] == {"q": "Los Angeles", "format": "json", "limit": 5}


def test_search_locations_caches_repeat_queries():
    payload = [{"lat": "32.7767", "lon": "-96.7970", "display_name": "Dallas, TX, USA"}]
    with (
        patch("trips.services.geocoding.httpx.get", return_value=_ok_response(payload)) as mock_get,
        patch("trips.services.geocoding.time.sleep"),
    ):
        first = geocoding.search_locations("Dallas, TX")
        second = geocoding.search_locations("Dallas, TX")

    assert first == second
    assert mock_get.call_count == 1


def test_search_locations_normalizes_query_for_cache_key():
    payload = [{"lat": "33.7490", "lon": "-84.3880", "display_name": "Atlanta, GA, USA"}]
    with (
        patch("trips.services.geocoding.httpx.get", return_value=_ok_response(payload)) as mock_get,
        patch("trips.services.geocoding.time.sleep"),
    ):
        geocoding.search_locations("Atlanta, GA")
        geocoding.search_locations("  ATLANTA, GA  ")

    assert mock_get.call_count == 1


def test_search_locations_empty_query_short_circuits():
    with patch("trips.services.geocoding.httpx.get") as mock_get:
        assert geocoding.search_locations("   ") == []
        assert geocoding.search_locations("") == []
    assert mock_get.call_count == 0


def test_search_locations_caps_limit_to_max():
    payload = [{"lat": "0", "lon": "0", "display_name": "x"}]
    with (
        patch("trips.services.geocoding.httpx.get", return_value=_ok_response(payload)) as mock_get,
        patch("trips.services.geocoding.time.sleep"),
    ):
        geocoding.search_locations("anywhere", limit=99)

    assert mock_get.call_args.kwargs["params"]["limit"] == geocoding.SEARCH_LIMIT_MAX


def test_geocode_returns_top_match():
    payload = [{"lat": "34.0537", "lon": "-118.2428", "display_name": "Los Angeles, CA, USA"}]
    with (
        patch("trips.services.geocoding.httpx.get", return_value=_ok_response(payload)),
        patch("trips.services.geocoding.time.sleep"),
    ):
        lat, lng, label = geocoding.geocode("Los Angeles, CA")

    assert lat == pytest.approx(34.0537)
    assert lng == pytest.approx(-118.2428)
    assert label == "Los Angeles, CA, USA"


def test_geocode_raises_when_no_results():
    with (
        patch("trips.services.geocoding.httpx.get", return_value=_ok_response([])),
        patch("trips.services.geocoding.time.sleep"),
    ):
        with pytest.raises(geocoding.GeocodingError):
            geocoding.geocode("zzzzzzzz unmappable place")

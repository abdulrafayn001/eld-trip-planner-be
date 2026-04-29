from __future__ import annotations

from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from trips.models import Trip
from trips.services.routing import RouteLeg

User = get_user_model()

LA = (34.0537, -118.2428, "Los Angeles, CA, USA")
DALLAS = (32.7767, -96.7970, "Dallas, TX, USA")
ATLANTA = (33.7490, -84.3880, "Atlanta, GA, USA")


def _leg(distance_mi: float, duration_hr: float, start, end) -> RouteLeg:
    return RouteLeg(
        distance_mi=distance_mi,
        duration_hr=duration_hr,
        geometry={
            "type": "LineString",
            "coordinates": [[start[1], start[0]], [end[1], end[0]]],
        },
    )


def _geocode_side_effect(query: str):
    q = query.lower()
    if "los angeles" in q:
        return LA
    if "dallas" in q:
        return DALLAS
    if "atlanta" in q:
        return ATLANTA
    raise AssertionError(f"unexpected geocode call: {query!r}")


@pytest.fixture
def planner_stubs():
    """Mock geocoding + routing so the API test never hits the network."""

    def _route_side_effect(from_coord, to_coord):
        if from_coord == (LA[0], LA[1]) and to_coord == (DALLAS[0], DALLAS[1]):
            return _leg(1400, 22.0, LA, DALLAS)  # forces multi-day + restart-eligible
        if from_coord == (DALLAS[0], DALLAS[1]) and to_coord == (ATLANTA[0], ATLANTA[1]):
            return _leg(800, 13.0, DALLAS, ATLANTA)
        raise AssertionError(f"unexpected route call: {from_coord} → {to_coord}")

    with (
        patch("trips.views.geocode", side_effect=_geocode_side_effect) as mock_geocode,
        patch("trips.views.route", side_effect=_route_side_effect) as mock_route,
    ):
        yield mock_geocode, mock_route


# ---------------------------------------------------------------------------
# Spec §7.5 — POST happy path returns 201 with a UUID.
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_post_trips_returns_201_and_uuid(planner_stubs):
    client = APIClient()
    response = client.post(
        "/api/trips/",
        data={
            "current": "Los Angeles, CA",
            "pickup": "Dallas, TX",
            "dropoff": "Atlanta, GA",
            "cycle_used_hrs": "20.0",
        },
    )

    assert response.status_code == 201
    assert "id" in response.data
    trip_id = response.data["id"]
    # UUID parses cleanly and the row was actually persisted.
    import uuid
    uuid.UUID(trip_id)
    assert Trip.objects.filter(id=trip_id).exists()


# ---------------------------------------------------------------------------
# Spec §7.5 — GET /trips/{id}/logs/ returns N entries where each totals 24h
# (and N matches the wall-clock day span of the planned events).
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_get_trip_logs_returns_24h_entries_per_day(planner_stubs):
    client = APIClient()
    create = client.post(
        "/api/trips/",
        data={
            "current": "Los Angeles, CA",
            "pickup": "Dallas, TX",
            "dropoff": "Atlanta, GA",
            "cycle_used_hrs": "20.0",
        },
    )
    trip_id = create.data["id"]

    response = client.get(f"/api/trips/{trip_id}/logs/")
    assert response.status_code == 200

    logs = response.data
    assert len(logs) >= 2  # multi-day trip per the stubbed legs

    for log in logs:
        total = (
            log["total_off_duty"]
            + log["total_sleeper"]
            + log["total_driving"]
            + log["total_on_duty"]
        )
        assert total == pytest.approx(24.0, abs=0.01), f"{log['date']} != 24h"


# ---------------------------------------------------------------------------
# Spec §7.5 — Invalid cycle_used_hrs (negative or >70) returns 400.
# ---------------------------------------------------------------------------
@pytest.mark.django_db
@pytest.mark.parametrize("bad_value", ["-1.0", "70.1", "100.0"])
def test_post_trips_rejects_invalid_cycle_used_hrs(bad_value, planner_stubs):
    client = APIClient()
    response = client.post(
        "/api/trips/",
        data={
            "current": "Los Angeles, CA",
            "pickup": "Dallas, TX",
            "dropoff": "Atlanta, GA",
            "cycle_used_hrs": bad_value,
        },
    )
    assert response.status_code == 400
    assert "cycle_used_hrs" in response.data


# ---------------------------------------------------------------------------
# GET /api/trips/{id}/ returns the full trip with nested events and logs.
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_get_trip_detail_includes_events_and_logs(planner_stubs):
    client = APIClient()
    create = client.post(
        "/api/trips/",
        data={
            "current": "Los Angeles, CA",
            "pickup": "Dallas, TX",
            "dropoff": "Atlanta, GA",
            "cycle_used_hrs": "20.0",
        },
    )
    trip_id = create.data["id"]

    response = client.get(f"/api/trips/{trip_id}/")
    assert response.status_code == 200
    body = response.data
    assert body["id"] == trip_id
    assert body["current_location"] == LA[2]
    assert body["pickup_location"] == DALLAS[2]
    assert body["dropoff_location"] == ATLANTA[2]
    assert len(body["events"]) > 0
    assert len(body["logs"]) > 0
    assert body["route_geometry"]["type"] == "LineString"


# ---------------------------------------------------------------------------
# GET /api/trips/{id}/route/ returns geometry + non-driving event markers.
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_get_trip_route_returns_geometry_and_markers(planner_stubs):
    client = APIClient()
    create = client.post(
        "/api/trips/",
        data={
            "current": "Los Angeles, CA",
            "pickup": "Dallas, TX",
            "dropoff": "Atlanta, GA",
            "cycle_used_hrs": "20.0",
        },
    )
    trip_id = create.data["id"]

    response = client.get(f"/api/trips/{trip_id}/route/")
    assert response.status_code == 200
    assert response.data["geometry"]["type"] == "LineString"
    markers = response.data["markers"]
    assert len(markers) > 0
    types = {m["type"] for m in markers}
    # Expect the bookend inspections + pickup/drop-off at minimum.
    assert "Pre-trip inspection" in types
    assert "Post-trip inspection" in types
    assert "Pickup" in types
    assert "Drop-off" in types
    # Every marker has lat/lng — pure-driving events are excluded.
    for m in markers:
        assert m["lat"] is not None and m["lng"] is not None


# ---------------------------------------------------------------------------
# GET /api/trips/ returns LimitOffsetPagination shape with PAGE_SIZE=10.
# Backs the frontend useInfiniteQuery hook on the trips list page.
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_list_trips_is_paginated():
    user = User.objects.create_user(username="judy", password="longpassword123")
    token = Token.objects.create(user=user)

    # Create 12 trips directly via the ORM — bypasses the planner so the test
    # stays fast and isolated from geocoding/routing.
    for i in range(12):
        Trip.objects.create(
            user=user,
            current_location=f"Origin {i}",
            current_lat=0.0,
            current_lng=0.0,
            pickup_location=f"Pickup {i}",
            pickup_lat=0.0,
            pickup_lng=0.0,
            dropoff_location=f"Dropoff {i}",
            dropoff_lat=0.0,
            dropoff_lng=0.0,
            cycle_used_hrs=10.0,
            total_distance_mi=100.0,
            total_duration_hr=2.0,
            route_geometry={"type": "LineString", "coordinates": []},
        )

    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Token {token.key}")

    page1 = client.get("/api/trips/")
    assert page1.status_code == 200
    body1 = page1.data
    assert body1["count"] == 12
    assert len(body1["results"]) == 10
    assert body1["next"] is not None
    assert body1["previous"] is None

    page2 = client.get("/api/trips/?limit=10&offset=10")
    assert page2.status_code == 200
    body2 = page2.data
    assert len(body2["results"]) == 2
    assert body2["next"] is None


# ---------------------------------------------------------------------------
# DELETE /api/trips/{id}/ — owner-only authorization.
# Anonymous callers are rejected before object lookup; non-owners get 404
# (existence not leaked); the owner gets 204 and cascade removes events/logs.
# ---------------------------------------------------------------------------
@pytest.fixture
def make_trip():
    def _make(user):
        return Trip.objects.create(
            user=user,
            current_location="Origin",
            current_lat=0.0,
            current_lng=0.0,
            pickup_location="Pickup",
            pickup_lat=0.0,
            pickup_lng=0.0,
            dropoff_location="Dropoff",
            dropoff_lat=0.0,
            dropoff_lng=0.0,
            cycle_used_hrs=10.0,
            total_distance_mi=100.0,
            total_duration_hr=2.0,
            route_geometry={"type": "LineString", "coordinates": []},
        )
    return _make


@pytest.mark.django_db
def test_owner_can_delete_trip(make_trip):
    user = User.objects.create_user(username="alice", password="longpassword123")
    token = Token.objects.create(user=user)
    trip = make_trip(user)

    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Token {token.key}")
    response = client.delete(f"/api/trips/{trip.id}/")

    assert response.status_code == 204
    assert not Trip.objects.filter(id=trip.id).exists()


@pytest.mark.django_db
def test_non_owner_delete_returns_404(make_trip):
    owner = User.objects.create_user(username="alice", password="longpassword123")
    other = User.objects.create_user(username="bob", password="longpassword123")
    other_token = Token.objects.create(user=other)
    trip = make_trip(owner)

    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Token {other_token.key}")
    response = client.delete(f"/api/trips/{trip.id}/")

    assert response.status_code == 404
    assert Trip.objects.filter(id=trip.id).exists()


@pytest.mark.django_db
def test_anonymous_delete_is_rejected(make_trip):
    owner = User.objects.create_user(username="alice", password="longpassword123")
    trip = make_trip(owner)

    client = APIClient()
    response = client.delete(f"/api/trips/{trip.id}/")

    assert response.status_code in (401, 403)
    assert Trip.objects.filter(id=trip.id).exists()

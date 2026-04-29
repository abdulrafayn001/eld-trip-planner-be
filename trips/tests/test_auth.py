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


def _items(payload):
    """Normalize DRF list payloads — paginated dict or bare list."""
    if isinstance(payload, dict) and "results" in payload:
        return payload["results"]
    return payload


def _route_side_effect(from_coord, to_coord):
    if from_coord == (LA[0], LA[1]) and to_coord == (DALLAS[0], DALLAS[1]):
        return _leg(150, 3.0, LA, DALLAS)
    if from_coord == (DALLAS[0], DALLAS[1]) and to_coord == (ATLANTA[0], ATLANTA[1]):
        return _leg(100, 2.0, DALLAS, ATLANTA)
    raise AssertionError(f"unexpected route call: {from_coord} → {to_coord}")


@pytest.fixture
def planner_stubs():
    """Mock routing only — geocoding is no longer invoked from the view."""
    with patch("trips.views.route", side_effect=_route_side_effect):
        yield


def _location(point):
    return {"label": point[2], "lat": point[0], "lng": point[1]}


@pytest.fixture
def trip_payload() -> dict:
    return {
        "current": _location(LA),
        "pickup": _location(DALLAS),
        "dropoff": _location(ATLANTA),
        "cycle_used_hrs": "10.0",
    }


# ---------------------------------------------------------------------------
# Register + login round-trip.
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_register_creates_user_and_returns_token():
    client = APIClient()
    response = client.post(
        "/api/auth/register/",
        data={"username": "alice", "email": "alice@example.com", "password": "longpassword123"},
    )
    assert response.status_code == 201
    assert response.data["username"] == "alice"
    assert "token" in response.data and len(response.data["token"]) > 0
    user = User.objects.get(username="alice")
    assert Token.objects.filter(user=user).exists()


@pytest.mark.django_db
def test_register_rejects_duplicate_username():
    User.objects.create_user(username="bob", password="longpassword123")
    client = APIClient()
    response = client.post(
        "/api/auth/register/",
        data={"username": "bob", "password": "longpassword123"},
    )
    assert response.status_code == 400
    assert "username" in response.data


@pytest.mark.django_db
def test_register_rejects_short_password():
    client = APIClient()
    response = client.post(
        "/api/auth/register/",
        data={"username": "carol", "password": "short"},
    )
    assert response.status_code == 400
    assert "password" in response.data


@pytest.mark.django_db
def test_login_returns_token_for_valid_credentials():
    User.objects.create_user(username="dave", password="longpassword123")
    client = APIClient()
    response = client.post(
        "/api/auth/login/",
        data={"username": "dave", "password": "longpassword123"},
    )
    assert response.status_code == 200
    assert len(response.data["token"]) > 0
    assert response.data["username"] == "dave"


@pytest.mark.django_db
def test_login_rejects_invalid_credentials():
    User.objects.create_user(username="eve", password="longpassword123")
    client = APIClient()
    response = client.post(
        "/api/auth/login/",
        data={"username": "eve", "password": "wrong-password"},
    )
    assert response.status_code == 400


# ---------------------------------------------------------------------------
# Trip stamping: authenticated request stamps Trip.user; anonymous still 201.
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_authenticated_post_trip_stamps_user(planner_stubs, trip_payload):
    user = User.objects.create_user(username="frank", password="longpassword123")
    token = Token.objects.create(user=user)

    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Token {token.key}")
    response = client.post("/api/trips/", data=trip_payload, format="json")

    assert response.status_code == 201
    trip = Trip.objects.get(id=response.data["id"])
    assert trip.user_id == user.pk


@pytest.mark.django_db
def test_anonymous_post_trip_succeeds_with_null_user(planner_stubs, trip_payload):
    client = APIClient()
    response = client.post("/api/trips/", data=trip_payload, format="json")

    assert response.status_code == 201
    trip = Trip.objects.get(id=response.data["id"])
    assert trip.user is None


# ---------------------------------------------------------------------------
# List filtering: GET /api/trips/ returns only the requesting user's trips;
# anonymous list is empty (single-trip retrieve by UUID stays open).
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_list_trips_returns_only_own_trips(planner_stubs, trip_payload):
    grace = User.objects.create_user(username="grace", password="longpassword123")
    heidi = User.objects.create_user(username="heidi", password="longpassword123")
    grace_token = Token.objects.create(user=grace)
    heidi_token = Token.objects.create(user=heidi)

    client = APIClient()

    client.credentials(HTTP_AUTHORIZATION=f"Token {grace_token.key}")
    grace_create = client.post("/api/trips/", data=trip_payload, format="json")
    grace_trip_id = grace_create.data["id"]

    client.credentials(HTTP_AUTHORIZATION=f"Token {heidi_token.key}")
    heidi_create = client.post("/api/trips/", data=trip_payload, format="json")
    heidi_trip_id = heidi_create.data["id"]

    # Anonymous trip should appear in nobody's list.
    client.credentials()
    client.post("/api/trips/", data=trip_payload, format="json")

    client.credentials(HTTP_AUTHORIZATION=f"Token {grace_token.key}")
    grace_list = client.get("/api/trips/")
    items = _items(grace_list.data)
    grace_ids = {t["id"] for t in items}
    assert grace_trip_id in grace_ids
    assert heidi_trip_id not in grace_ids


@pytest.mark.django_db
def test_anonymous_list_trips_is_empty(planner_stubs, trip_payload):
    user = User.objects.create_user(username="ivan", password="longpassword123")
    token = Token.objects.create(user=user)
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Token {token.key}")
    client.post("/api/trips/", data=trip_payload, format="json")
    # Anonymous trip — also persisted, but still excluded from anon list.
    client.credentials()
    client.post("/api/trips/", data=trip_payload, format="json")

    client.credentials()
    response = client.get("/api/trips/")
    assert response.status_code == 200
    payload = response.data
    assert _items(payload) == []


# ---------------------------------------------------------------------------
# Anonymous retrieve-by-UUID still works (matches spec §7.3 retrieve).
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_anonymous_can_retrieve_anonymous_trip_by_id(planner_stubs, trip_payload):
    client = APIClient()
    create = client.post("/api/trips/", data=trip_payload, format="json")
    trip_id = create.data["id"]

    response = client.get(f"/api/trips/{trip_id}/")
    assert response.status_code == 200
    assert response.data["id"] == trip_id
    assert response.data["user"] is None

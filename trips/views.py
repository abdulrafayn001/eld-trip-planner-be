"""Trips API views.
- ``POST /api/trips/``       → orchestrates route → plan → build logs →
                              persist using locations pre-picked by the
                              frontend autocomplete; returns ``{"id": ...}``.
- ``GET  /api/trips/{id}/``  → full trip with nested events and logs.
- ``GET  /api/trips/{id}/logs/``  → array of daily-log structures for the
                                    SVG renderer.
- ``GET  /api/trips/{id}/route/`` → ``{geometry, markers}`` for the map.
- ``GET  /api/trips/?user_id=...`` → recent trips, optionally filtered.
- ``GET  /api/geocode/?q=...`` → autocomplete candidates from Nominatim.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Final

import httpx
from django.contrib.auth import get_user_model
from django.db import transaction
from rest_framework import mixins, status, viewsets
from rest_framework.authtoken.models import Token
from rest_framework.authtoken.views import ObtainAuthToken
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from trips.models import DailyLog, Trip, TripEvent
from trips.serializers import (
    DailyLogSerializer,
    GeocodeSearchSerializer,
    RegisterSerializer,
    TripCreateSerializer,
    TripEventSerializer,
    TripSerializer,
)

User = get_user_model()
from trips.services import log_builder, planner
from trips.services.geocoding import search_locations
from trips.services.routing import RouteLeg, RoutingError, route

NON_DRIVING_MARKER_ACTIVITIES: Final = frozenset(
    {
        "Pre-trip inspection",
        "Post-trip inspection",
        "Pickup",
        "Drop-off",
        "Fueling",
        "30-min break",
        "10-hr daily reset",
        "34-hr restart",
    }
)


class TripViewSet(
    mixins.CreateModelMixin,
    mixins.RetrieveModelMixin,
    mixins.ListModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    queryset = Trip.objects.all().prefetch_related("events", "logs")
    serializer_class = TripSerializer

    def get_permissions(self):
        # Destroy is owner-only; require auth before object lookup so anonymous
        # callers see 401 rather than 404. The other actions keep the global
        # AllowAny default (anonymous create/retrieve are intentional).
        if self.action == "destroy":
            return [IsAuthenticated()]
        return super().get_permissions()

    def get_queryset(self):
        qs = super().get_queryset()
        if self.action in ("list", "destroy"):
            # Listing returns only the requesting user's trips (anonymous list
            # is empty). Destroy is scoped the same way so non-owners get a
            # 404 from get_object() rather than leaking trip existence.
            # Retrieve-by-UUID stays open so a freshly-created anonymous trip
            # can still be fetched by id.
            user = self.request.user
            if user.is_authenticated:
                return qs.filter(user=user)
            return qs.none()
        return qs

    def create(self, request: Request, *args, **kwargs) -> Response:
        serializer = TripCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        # Locations arrive pre-resolved from the autocomplete picker, so the
        # view skips geocoding entirely. Normalise to (lat, lng, label) for
        # the rest of the pipeline.
        current = (data["current"]["lat"], data["current"]["lng"], data["current"]["label"])
        pickup = (data["pickup"]["lat"], data["pickup"]["lng"], data["pickup"]["label"])
        dropoff = (data["dropoff"]["lat"], data["dropoff"]["lng"], data["dropoff"]["label"])

        try:
            leg_a = route((current[0], current[1]), (pickup[0], pickup[1]))
            leg_b = route((pickup[0], pickup[1]), (dropoff[0], dropoff[1]))
        except RoutingError as exc:
            raise ValidationError({"detail": f"could not compute route: {exc}"}) from exc

        cycle_used_hrs = float(data["cycle_used_hrs"])
        home_timezone = data.get("home_timezone") or "America/Chicago"
        start_dt = datetime.now(tz=timezone.utc)

        result = planner.plan_trip(
            (current[0], current[1]),
            (pickup[0], pickup[1]),
            (dropoff[0], dropoff[1]),
            leg_a,
            leg_b,
            cycle_used_hrs=cycle_used_hrs,
            start_dt=start_dt,
            current_label=current[2],
            pickup_label=pickup[2],
            dropoff_label=dropoff[2],
        )

        total_distance_mi = leg_a.distance_mi + leg_b.distance_mi
        total_duration_hr = (
            result.events[-1].end_time - result.events[0].start_time
        ).total_seconds() / 3600.0

        logs = log_builder.build_daily_logs(
            result.events,
            home_timezone=home_timezone,
            total_distance_mi=total_distance_mi,
        )

        trip = self._persist(
            user=request.user if request.user.is_authenticated else None,
            current=current,
            pickup=pickup,
            dropoff=dropoff,
            cycle_used_hrs=cycle_used_hrs,
            home_timezone=home_timezone,
            total_distance_mi=total_distance_mi,
            total_duration_hr=total_duration_hr,
            route_geometry=_combine_geometries(leg_a, leg_b),
            requires_34h_restart=result.requires_34h_restart,
            events=result.events,
            logs=logs,
        )
        return Response({"id": str(trip.id)}, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["get"])
    def logs(self, request: Request, pk: str | None = None) -> Response:
        trip = self.get_object()
        return Response(DailyLogSerializer(trip.logs.all(), many=True).data)

    @action(detail=True, methods=["get"])
    def route(self, request: Request, pk: str | None = None) -> Response:
        trip = self.get_object()
        markers = [
            {
                "type": e.activity,
                "lat": e.lat,
                "lng": e.lng,
                "time": e.start_time.isoformat(),
                "label": e.location_label,
            }
            for e in trip.events.all()
            if e.activity in NON_DRIVING_MARKER_ACTIVITIES
            and e.lat is not None
            and e.lng is not None
        ]
        return Response({"geometry": trip.route_geometry, "markers": markers})

    @staticmethod
    @transaction.atomic
    def _persist(
        *,
        user,
        current: tuple[float, float, str],
        pickup: tuple[float, float, str],
        dropoff: tuple[float, float, str],
        cycle_used_hrs: float,
        home_timezone: str,
        total_distance_mi: float,
        total_duration_hr: float,
        route_geometry: dict,
        requires_34h_restart: bool,
        events: list[TripEvent],
        logs: list[DailyLog],
    ) -> Trip:
        trip = Trip.objects.create(
            user=user,
            current_location=current[2],
            current_lat=current[0],
            current_lng=current[1],
            pickup_location=pickup[2],
            pickup_lat=pickup[0],
            pickup_lng=pickup[1],
            dropoff_location=dropoff[2],
            dropoff_lat=dropoff[0],
            dropoff_lng=dropoff[1],
            cycle_used_hrs=cycle_used_hrs,
            home_timezone=home_timezone,
            total_distance_mi=total_distance_mi,
            total_duration_hr=total_duration_hr,
            route_geometry=route_geometry,
            requires_34h_restart=requires_34h_restart,
        )
        for event in events:
            event.trip = trip
        TripEvent.objects.bulk_create(events)
        for log in logs:
            log.trip = trip
        DailyLog.objects.bulk_create(logs)
        return trip


class RegisterView(APIView):
    """``POST /api/auth/register/`` — create a user and return an auth token."""

    authentication_classes: list = []
    permission_classes: list = []

    def post(self, request: Request) -> Response:
        serializer = RegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        token, _ = Token.objects.get_or_create(user=user)
        return Response(
            {"token": token.key, "user_id": user.pk, "username": user.username},
            status=status.HTTP_201_CREATED,
        )


class LoginView(ObtainAuthToken):
    """``POST /api/auth/login/`` — return the user's auth token.

    Wraps DRF's :class:`ObtainAuthToken` to also return ``user_id`` and
    ``username`` so the frontend doesn't need a second round-trip.
    """

    authentication_classes: list = []
    permission_classes: list = []

    def post(self, request: Request, *args, **kwargs) -> Response:
        serializer = self.serializer_class(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        user = serializer.validated_data["user"]
        token, _ = Token.objects.get_or_create(user=user)
        return Response({"token": token.key, "user_id": user.pk, "username": user.username})


class GeocodeSearchView(APIView):
    """``GET /api/geocode/?q=...`` — proxy to Nominatim for autocomplete.

    The frontend never hits Nominatim directly: this proxy enforces the
    project's User-Agent, applies the per-process 1 req/s throttle, and
    caches results so repeated keystrokes don't fan out to upstream. The
    response is a flat list — caching, filtering, and ranking are
    Nominatim's job, not the API's.
    """

    authentication_classes: list = []
    permission_classes: list = []

    def get(self, request: Request) -> Response:
        params = GeocodeSearchSerializer(data=request.query_params)
        params.is_valid(raise_exception=True)
        q = params.validated_data["q"]
        limit = params.validated_data.get("limit") or 5

        try:
            matches = search_locations(q, limit=limit)
        except httpx.HTTPError as exc:
            return Response(
                {"detail": f"geocoder unavailable: {exc}"},
                status=status.HTTP_502_BAD_GATEWAY,
            )
        return Response({"results": matches})


def _combine_geometries(leg_a: RouteLeg, leg_b: RouteLeg) -> dict:
    """Concatenate two LineString legs, dropping the duplicate join point."""
    coords_a = (leg_a.geometry or {}).get("coordinates") or []
    coords_b = (leg_b.geometry or {}).get("coordinates") or []
    if coords_a and coords_b and coords_a[-1] == coords_b[0]:
        combined = coords_a + coords_b[1:]
    else:
        combined = coords_a + coords_b
    return {"type": "LineString", "coordinates": combined}

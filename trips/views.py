"""Trips API views.
- ``POST /api/trips/``       → orchestrates geocode → route → plan →
                              build logs → persist; returns ``{"id": ...}``.
- ``GET  /api/trips/{id}/``  → full trip with nested events and logs.
- ``GET  /api/trips/{id}/logs/``  → array of daily-log structures for the
                                    SVG renderer.
- ``GET  /api/trips/{id}/route/`` → ``{geometry, markers}`` for the map.
- ``GET  /api/trips/?user_id=...`` → recent trips, optionally filtered.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Final

from django.db import transaction
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.request import Request
from rest_framework.response import Response

from trips.models import DailyLog, Trip, TripEvent
from trips.serializers import (
    DailyLogSerializer,
    TripCreateSerializer,
    TripEventSerializer,
    TripSerializer,
)
from trips.services import log_builder, planner
from trips.services.geocoding import GeocodingError, geocode
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
    viewsets.GenericViewSet,
):
    queryset = Trip.objects.all().prefetch_related("events", "logs")
    serializer_class = TripSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        user_id = self.request.query_params.get("user_id")
        if user_id:
            qs = qs.filter(user_id=user_id)
        return qs

    def create(self, request: Request, *args, **kwargs) -> Response:
        serializer = TripCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        try:
            current = geocode(data["current"])
            pickup = geocode(data["pickup"])
            dropoff = geocode(data["dropoff"])
        except GeocodingError as exc:
            raise ValidationError({"detail": f"could not geocode: {exc}"}) from exc

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
            user_id=getattr(request, "user_id", None),
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
        user_id,
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
            user_id=user_id,
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


def _combine_geometries(leg_a: RouteLeg, leg_b: RouteLeg) -> dict:
    """Concatenate two LineString legs, dropping the duplicate join point."""
    coords_a = (leg_a.geometry or {}).get("coordinates") or []
    coords_b = (leg_b.geometry or {}).get("coordinates") or []
    if coords_a and coords_b and coords_a[-1] == coords_b[0]:
        combined = coords_a + coords_b[1:]
    else:
        combined = coords_a + coords_b
    return {"type": "LineString", "coordinates": combined}

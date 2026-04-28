"""DRF serializers for the trips API.

The input serializer (:class:`TripCreateSerializer`) validates the four
fields from spec §3 — ``current``, ``pickup``, ``dropoff`` (free-text
locations the geocoder will resolve) and ``cycle_used_hrs`` (0-70, one
decimal). Output serializers expose the persisted Trip with its events
and per-day logs. Trip orchestration (geocode → route → plan → build
logs → persist) lives in the view, not here.
"""
from __future__ import annotations

from decimal import Decimal

from rest_framework import serializers

from trips.models import DailyLog, Trip, TripEvent

CYCLE_HOURS_MAX = 70.0


class TripCreateSerializer(serializers.Serializer):
    """Validates trip-input from the frontend form (spec §3, §8.4)."""

    current = serializers.CharField(max_length=255)
    pickup = serializers.CharField(max_length=255)
    dropoff = serializers.CharField(max_length=255)
    cycle_used_hrs = serializers.DecimalField(
        max_digits=4,
        decimal_places=1,
        min_value=Decimal("0.0"),
        max_value=Decimal(str(CYCLE_HOURS_MAX)),
    )
    home_timezone = serializers.CharField(
        max_length=64, required=False, default="America/Chicago"
    )


class TripEventSerializer(serializers.ModelSerializer):
    class Meta:
        model = TripEvent
        fields = (
            "sequence",
            "start_time",
            "end_time",
            "duty_status",
            "activity",
            "location_label",
            "lat",
            "lng",
        )


class DailyLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = DailyLog
        fields = (
            "date",
            "from_label",
            "to_label",
            "total_miles",
            "total_off_duty",
            "total_sleeper",
            "total_driving",
            "total_on_duty",
            "segments",
        )


class TripSerializer(serializers.ModelSerializer):
    events = TripEventSerializer(many=True, read_only=True)
    logs = DailyLogSerializer(many=True, read_only=True)

    class Meta:
        model = Trip
        fields = (
            "id",
            "user_id",
            "current_location",
            "current_lat",
            "current_lng",
            "pickup_location",
            "pickup_lat",
            "pickup_lng",
            "dropoff_location",
            "dropoff_lat",
            "dropoff_lng",
            "cycle_used_hrs",
            "home_timezone",
            "total_distance_mi",
            "total_duration_hr",
            "route_geometry",
            "requires_34h_restart",
            "created_at",
            "events",
            "logs",
        )
        read_only_fields = fields

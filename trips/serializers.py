"""DRF serializers for the trips API.

The input serializer (:class:`TripCreateSerializer`) validates the four
fields from spec §3 — ``current``, ``pickup``, ``dropoff`` (each a
``{label, lat, lng}`` object pre-resolved by the frontend autocomplete
against ``GET /api/geocode/``) and ``cycle_used_hrs`` (0-70, one
decimal). Output serializers expose the persisted Trip with its events
and per-day logs. Trip orchestration (route → plan → build logs →
persist) lives in the view, not here.
"""
from __future__ import annotations

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from rest_framework import serializers

from trips.models import DailyLog, Trip, TripEvent

User = get_user_model()

CYCLE_HOURS_MAX = 70.0


class LocationSerializer(serializers.Serializer):
    """A picked location from the autocomplete: label + coordinates."""

    label = serializers.CharField(max_length=255)
    lat = serializers.FloatField(min_value=-90.0, max_value=90.0)
    lng = serializers.FloatField(min_value=-180.0, max_value=180.0)


class TripCreateSerializer(serializers.Serializer):
    """Validates trip-input from the frontend form (spec §3, §8.4)."""

    current = LocationSerializer()
    pickup = LocationSerializer()
    dropoff = LocationSerializer()
    cycle_used_hrs = serializers.DecimalField(
        max_digits=4,
        decimal_places=1,
        min_value=Decimal("0.0"),
        max_value=Decimal(str(CYCLE_HOURS_MAX)),
    )
    home_timezone = serializers.CharField(
        max_length=64, required=False, default="America/Chicago"
    )


class GeocodeSearchSerializer(serializers.Serializer):
    """Validates the ``GET /api/geocode/`` query string."""

    q = serializers.CharField(max_length=255, required=True)
    limit = serializers.IntegerField(min_value=1, max_value=8, required=False, default=5)


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
            "user",
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


class RegisterSerializer(serializers.Serializer):
    """Validates new-user input for ``POST /api/auth/register/``."""

    username = serializers.CharField(max_length=150)
    email = serializers.EmailField(required=False, allow_blank=True)
    password = serializers.CharField(write_only=True, min_length=8)

    def validate_username(self, value: str) -> str:
        if User.objects.filter(username=value).exists():
            raise serializers.ValidationError("username already taken")
        return value

    def validate_password(self, value: str) -> str:
        validate_password(value)
        return value

    def create(self, validated_data: dict) -> "User":
        return User.objects.create_user(
            username=validated_data["username"],
            email=validated_data.get("email", "") or "",
            password=validated_data["password"],
        )

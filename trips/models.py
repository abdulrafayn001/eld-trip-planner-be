import uuid

from django.conf import settings
from django.db import models


DUTY_STATUS = [
    ("OFF", "Off Duty"),
    ("SB", "Sleeper Berth"),
    ("D", "Driving"),
    ("ON", "On Duty Not Driving"),
]


class Trip(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        related_name="trips",
        on_delete=models.SET_NULL,
        db_index=True,
    )
    current_location = models.CharField(max_length=255)
    current_lat = models.FloatField()
    current_lng = models.FloatField()
    pickup_location = models.CharField(max_length=255)
    pickup_lat = models.FloatField()
    pickup_lng = models.FloatField()
    dropoff_location = models.CharField(max_length=255)
    dropoff_lat = models.FloatField()
    dropoff_lng = models.FloatField()
    cycle_used_hrs = models.FloatField()
    home_timezone = models.CharField(max_length=64, default="America/Chicago")
    total_distance_mi = models.FloatField()
    total_duration_hr = models.FloatField()
    route_geometry = models.JSONField()
    requires_34h_restart = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.current_location} → {self.pickup_location} → {self.dropoff_location}"


class TripEvent(models.Model):
    trip = models.ForeignKey(Trip, related_name="events", on_delete=models.CASCADE)
    sequence = models.PositiveIntegerField()
    start_time = models.DateTimeField()
    end_time = models.DateTimeField()
    duty_status = models.CharField(max_length=3, choices=DUTY_STATUS)
    activity = models.CharField(max_length=64)
    location_label = models.CharField(max_length=255)
    lat = models.FloatField(null=True, blank=True)
    lng = models.FloatField(null=True, blank=True)

    class Meta:
        ordering = ["sequence"]
        indexes = [models.Index(fields=["trip", "sequence"])]

    def __str__(self) -> str:
        return f"#{self.sequence} {self.duty_status} {self.activity}"


class DailyLog(models.Model):
    trip = models.ForeignKey(Trip, related_name="logs", on_delete=models.CASCADE)
    date = models.DateField()
    from_label = models.CharField(max_length=255)
    to_label = models.CharField(max_length=255)
    total_miles = models.FloatField()
    total_off_duty = models.FloatField()
    total_sleeper = models.FloatField()
    total_driving = models.FloatField()
    total_on_duty = models.FloatField()
    segments = models.JSONField()

    class Meta:
        ordering = ["date"]
        constraints = [
            models.UniqueConstraint(fields=["trip", "date"], name="uniq_trip_date"),
        ]

    def __str__(self) -> str:
        return f"{self.date} ({self.from_label} → {self.to_label})"

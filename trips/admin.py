from django.contrib import admin

from trips.models import DailyLog, Trip, TripEvent


class TripEventInline(admin.TabularInline):
    model = TripEvent
    extra = 0
    fields = ("sequence", "start_time", "end_time", "duty_status", "activity", "location_label")
    ordering = ("sequence",)


class DailyLogInline(admin.TabularInline):
    model = DailyLog
    extra = 0
    fields = ("date", "from_label", "to_label", "total_miles", "total_driving", "total_on_duty")
    ordering = ("date",)


@admin.register(Trip)
class TripAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "current_location",
        "pickup_location",
        "dropoff_location",
        "cycle_used_hrs",
        "total_distance_mi",
        "requires_34h_restart",
        "created_at",
    )
    list_filter = ("requires_34h_restart", "home_timezone")
    search_fields = (
        "current_location",
        "pickup_location",
        "dropoff_location",
        "user__username",
    )
    readonly_fields = ("id", "created_at")
    inlines = (TripEventInline, DailyLogInline)


@admin.register(TripEvent)
class TripEventAdmin(admin.ModelAdmin):
    list_display = ("trip", "sequence", "start_time", "end_time", "duty_status", "activity")
    list_filter = ("duty_status",)
    search_fields = ("activity", "location_label")
    ordering = ("trip", "sequence")


@admin.register(DailyLog)
class DailyLogAdmin(admin.ModelAdmin):
    list_display = ("trip", "date", "from_label", "to_label", "total_miles", "total_driving")
    search_fields = ("from_label", "to_label")
    ordering = ("trip", "date")

"""HOS-compliant trip planner.

Pure-function service that takes geocoded coordinates and OSRM/ORS route
legs and produces an ordered list of unsaved :class:`trips.models.TripEvent`
instances together with a flag indicating whether a 34-hr restart had to be
inserted to complete the trip. Persistence is the caller's responsibility.

Implements FMCSA Part 395 daily and weekly limits per spec §6:

- 11-hr drive limit / 14-hr duty window (reset by 10 consecutive hr off duty)
- 30-min break required after 8 cumulative driving hrs since the last
  ≥30-min non-driving period (off-duty, sleeper, or on-duty-not-driving)
- 70-hr / 8-day cycle, with a 34-hr restart inserted only when the remaining
  cycle is insufficient to complete the trip
- Fueling at every ≤1,000 mi of cumulative driving
- 15-min pre/post-trip inspections, 60-min pickup, 60-min drop-off
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final

from trips.models import TripEvent
from trips.services.routing import RouteLeg

PRE_TRIP_HR: Final = 0.25
POST_TRIP_HR: Final = 0.25
PICKUP_HR: Final = 1.0
DROPOFF_HR: Final = 1.0
FUELING_HR: Final = 0.25
BREAK_HR: Final = 0.5
DAILY_RESET_HR: Final = 10.0
RESTART_HR: Final = 34.0

MAX_DRIVE_PER_DAY: Final = 11.0
MAX_DUTY_WINDOW: Final = 14.0
MAX_DRIVE_BEFORE_BREAK: Final = 8.0
MAX_CYCLE_HR: Final = 70.0
FUEL_INTERVAL_MI: Final = 1000.0
EARTH_RADIUS_MI: Final = 3958.7613

_EPS: Final = 1e-9

Coord = tuple[float, float]  # (lat, lng) — matches geocoding service output


@dataclass(frozen=True)
class PlannerResult:
    events: list[TripEvent]
    requires_34h_restart: bool


def plan_trip(
    current: Coord,
    pickup: Coord,
    dropoff: Coord,
    leg_a: RouteLeg,
    leg_b: RouteLeg,
    cycle_used_hrs: float,
    start_dt: datetime,
    *,
    current_label: str = "Origin",
    pickup_label: str = "Pickup",
    dropoff_label: str = "Drop-off",
) -> PlannerResult:
    """Plan a HOS-compliant trip current → pickup → dropoff.

    ``leg_a`` is the route from ``current`` to ``pickup``; ``leg_b`` is from
    ``pickup`` to ``dropoff``. Returns events covering the entire wall-clock
    duration of the trip (continuous: each event's ``start_time`` equals the
    previous event's ``end_time``).
    """
    state = _Clocks(
        cycle_used=cycle_used_hrs,
        drive_today=0.0,
        window_used=0.0,
        since_break=0.0,
    )
    events: list[TripEvent] = []
    requires_34h = False
    now = start_dt

    def emit(duty: str, hours: float, activity: str, lat: float, lng: float, label: str) -> None:
        nonlocal now
        end = now + timedelta(hours=hours)
        events.append(
            TripEvent(
                sequence=len(events),
                start_time=now,
                end_time=end,
                duty_status=duty,
                activity=activity,
                location_label=label,
                lat=lat,
                lng=lng,
            )
        )
        state.advance(duty, hours)
        now = end

    emit("ON", PRE_TRIP_HR, "Pre-trip inspection", current[0], current[1], current_label)

    legs = (
        (leg_a, "PICKUP", pickup, pickup_label),
        (leg_b, "DROPOFF", dropoff, dropoff_label),
    )
    miles_since_fuel = 0.0

    for leg, end_action, end_coord, end_label in legs:
        remaining_hours = leg.duration_hr
        avg_speed_mph = (
            leg.distance_mi / leg.duration_hr if leg.duration_hr > _EPS else 0.0
        )
        miles_into_leg = 0.0

        while remaining_hours > _EPS:
            available = min(
                MAX_DRIVE_PER_DAY - state.drive_today,
                MAX_DUTY_WINDOW - state.window_used,
                MAX_CYCLE_HR - state.cycle_used,
                MAX_DRIVE_BEFORE_BREAK - state.since_break,
            )

            if available <= _EPS:
                lat, lng = interp_position(leg.geometry, miles_into_leg, leg.distance_mi)
                if state.since_break >= MAX_DRIVE_BEFORE_BREAK - _EPS:
                    emit("OFF", BREAK_HR, "30-min break", lat, lng, "En route")
                elif state.cycle_used + POST_TRIP_HR > MAX_CYCLE_HR:
                    requires_34h = True
                    emit("ON", POST_TRIP_HR, "Post-trip inspection", lat, lng, "En route")
                    emit("OFF", RESTART_HR, "34-hr restart", lat, lng, "En route")
                    emit("ON", PRE_TRIP_HR, "Pre-trip inspection", lat, lng, "En route")
                else:
                    emit("ON", POST_TRIP_HR, "Post-trip inspection", lat, lng, "En route")
                    emit("OFF", DAILY_RESET_HR, "10-hr daily reset", lat, lng, "En route")
                    emit("ON", PRE_TRIP_HR, "Pre-trip inspection", lat, lng, "En route")
                continue

            hours_to_fuel = (
                (FUEL_INTERVAL_MI - miles_since_fuel) / avg_speed_mph
                if avg_speed_mph > _EPS
                else math.inf
            )
            chunk = min(available, hours_to_fuel, remaining_hours)
            if chunk <= _EPS:
                break  # defensive; available > _EPS guarantees forward progress

            lat, lng = interp_position(leg.geometry, miles_into_leg, leg.distance_mi)
            emit("D", chunk, "Driving", lat, lng, "En route")

            remaining_hours -= chunk
            miles_in_chunk = chunk * avg_speed_mph
            miles_since_fuel += miles_in_chunk
            miles_into_leg += miles_in_chunk

            if miles_since_fuel >= FUEL_INTERVAL_MI - 1e-6 and remaining_hours > _EPS:
                lat, lng = interp_position(leg.geometry, miles_into_leg, leg.distance_mi)
                emit("ON", FUELING_HR, "Fueling", lat, lng, "Fuel stop")
                miles_since_fuel = 0.0

        if end_action == "PICKUP":
            emit("ON", PICKUP_HR, "Pickup", end_coord[0], end_coord[1], end_label)
        else:
            emit("ON", DROPOFF_HR, "Drop-off", end_coord[0], end_coord[1], end_label)

    emit("ON", POST_TRIP_HR, "Post-trip inspection", dropoff[0], dropoff[1], dropoff_label)
    return PlannerResult(events=events, requires_34h_restart=requires_34h)


@dataclass
class _Clocks:
    cycle_used: float
    drive_today: float
    window_used: float
    since_break: float

    def advance(self, duty: str, hours: float) -> None:
        if duty == "D":
            self.drive_today += hours
            self.window_used += hours
            self.cycle_used += hours
            self.since_break += hours
        elif duty == "ON":
            self.window_used += hours
            self.cycle_used += hours
            if hours >= BREAK_HR - _EPS:
                self.since_break = 0.0
        elif duty in ("OFF", "SB"):
            if hours >= BREAK_HR - _EPS:
                self.since_break = 0.0
            if hours >= DAILY_RESET_HR - _EPS:
                self.drive_today = 0.0
                self.window_used = 0.0
            else:
                # FMCSA § 395.3(a)(2): 14-hr window is consecutive wall-clock
                # from start of work; only a ≥10-hr off-duty period resets it.
                # Sub-reset off-duty (e.g. 30-min break) still consumes window.
                self.window_used += hours
            if hours >= RESTART_HR - _EPS:
                self.cycle_used = 0.0


def interp_position(geometry: dict, miles_into_leg: float, leg_distance_mi: float) -> Coord:
    """Return ``(lat, lng)`` at ``miles_into_leg`` along the leg's polyline.

    OSRM/ORS GeoJSON LineStrings store coordinates as ``[lng, lat]``. We sum
    haversine distances between consecutive points, then rescale by
    ``leg_distance_mi`` (the road distance the routing provider reports —
    typically larger than the great-circle sum of polyline samples) so a
    given ``miles_into_leg`` maps to a proportional point along the polyline.
    """
    coords = (geometry or {}).get("coordinates") or []
    if not coords:
        return (0.0, 0.0)
    if len(coords) == 1 or miles_into_leg <= 0:
        return _flip(coords[0])
    if leg_distance_mi <= _EPS or miles_into_leg >= leg_distance_mi - _EPS:
        return _flip(coords[-1])

    cumulative = [0.0]
    for i in range(1, len(coords)):
        cumulative.append(cumulative[-1] + _haversine_mi(coords[i - 1], coords[i]))
    polyline_total = cumulative[-1]
    if polyline_total <= _EPS:
        return _flip(coords[0])

    target = polyline_total * (miles_into_leg / leg_distance_mi)
    for i in range(1, len(cumulative)):
        if cumulative[i] >= target - _EPS:
            seg_len = cumulative[i] - cumulative[i - 1]
            if seg_len <= _EPS:
                return _flip(coords[i])
            t = (target - cumulative[i - 1]) / seg_len
            lng1, lat1 = coords[i - 1]
            lng2, lat2 = coords[i]
            return (lat1 + t * (lat2 - lat1), lng1 + t * (lng2 - lng1))
    return _flip(coords[-1])


def _flip(coord: list) -> Coord:
    lng, lat = coord
    return (float(lat), float(lng))


def _haversine_mi(a: list, b: list) -> float:
    lng1, lat1 = a
    lng2, lat2 = b
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_MI * math.asin(math.sqrt(h))

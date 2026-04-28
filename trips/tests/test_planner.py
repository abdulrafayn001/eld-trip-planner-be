from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from trips.services import planner
from trips.services.routing import RouteLeg

LA: tuple[float, float] = (34.0537, -118.2428)
DALLAS: tuple[float, float] = (32.7767, -96.7970)
ATLANTA: tuple[float, float] = (33.7490, -84.3880)
START = datetime(2025, 6, 1, 6, 0, tzinfo=timezone.utc)


def _leg(distance_mi: float, duration_hr: float, start=LA, end=DALLAS) -> RouteLeg:
    """Synthetic RouteLeg with a 2-point GeoJSON polyline (lng,lat order)."""
    return RouteLeg(
        distance_mi=distance_mi,
        duration_hr=duration_hr,
        geometry={
            "type": "LineString",
            "coordinates": [[start[1], start[0]], [end[1], end[0]]],
        },
    )


def _hours(event) -> float:
    return (event.end_time - event.start_time).total_seconds() / 3600


# ---------------------------------------------------------------------------
# Spec §7.5 test 1 — Short trip (<11 hrs total driving): exactly one log day,
# no 30-min break when cumulative driving < 8 hrs.
# ---------------------------------------------------------------------------
def test_short_trip_under_8_driving_hours_has_no_break_or_reset():
    leg_a = _leg(150, 3.0, start=LA, end=DALLAS)
    leg_b = _leg(100, 2.0, start=DALLAS, end=ATLANTA)

    result = planner.plan_trip(LA, DALLAS, ATLANTA, leg_a, leg_b,
                               cycle_used_hrs=10.0, start_dt=START)

    activities = [e.activity for e in result.events]
    assert "30-min break" not in activities
    assert "10-hr daily reset" not in activities
    assert "34-hr restart" not in activities
    # One driving day → one pre-trip and one post-trip total.
    assert activities.count("Pre-trip inspection") == 1
    assert activities.count("Post-trip inspection") == 1
    assert result.requires_34h_restart is False


# ---------------------------------------------------------------------------
# Spec §7.5 test 2 — 30-min break inserted exactly at the 8-hr cumulative
# driving boundary.
# ---------------------------------------------------------------------------
def test_30_min_break_inserted_at_8_hour_cumulative_driving_mark():
    # 9-hr first leg with no mid-leg pickup keeps `since_break` running until
    # the planner forces a break.
    leg_a = _leg(500, 9.0, start=LA, end=DALLAS)
    leg_b = _leg(10, 0.2, start=DALLAS, end=ATLANTA)

    result = planner.plan_trip(LA, DALLAS, ATLANTA, leg_a, leg_b,
                               cycle_used_hrs=0.0, start_dt=START)

    drive_before_break = 0.0
    found_break = False
    for e in result.events:
        if e.activity == "30-min break":
            found_break = True
            break
        if e.duty_status == "D":
            drive_before_break += _hours(e)
    assert found_break, "expected a 30-min break to be inserted"
    assert drive_before_break == pytest.approx(planner.MAX_DRIVE_BEFORE_BREAK, abs=0.01)


# ---------------------------------------------------------------------------
# Spec §7.5 test 3 — 11-hr cap forces a 10-hr daily reset.
# ---------------------------------------------------------------------------
def test_11_hour_driving_cap_forces_10_hour_daily_reset():
    leg_a = _leg(700, 12.0, start=LA, end=DALLAS)  # 12 hrs > 11-hr daily cap
    leg_b = _leg(100, 2.0, start=DALLAS, end=ATLANTA)

    result = planner.plan_trip(LA, DALLAS, ATLANTA, leg_a, leg_b,
                               cycle_used_hrs=0.0, start_dt=START)

    activities = [e.activity for e in result.events]
    assert "10-hr daily reset" in activities

    # No driving event ever pushes drive_today above 11 hrs cumulatively
    # since the most recent 10-hr reset.
    drive_today = 0.0
    for e in result.events:
        if e.duty_status == "D":
            drive_today += _hours(e)
            assert drive_today <= planner.MAX_DRIVE_PER_DAY + 0.01
        elif e.duty_status in ("OFF", "SB") and _hours(e) >= planner.DAILY_RESET_HR - 0.01:
            drive_today = 0.0


# ---------------------------------------------------------------------------
# Spec §7.5 test 4 — Trip ≥1,000 mi has at least one fueling event, placed
# at the correct interpolated position.
# ---------------------------------------------------------------------------
def test_trip_over_1000_miles_emits_fueling_at_interpolated_position():
    leg_a = _leg(1100, 18.0, start=LA, end=DALLAS)  # crosses 1000-mi mark
    leg_b = _leg(50, 1.0, start=DALLAS, end=ATLANTA)

    result = planner.plan_trip(LA, DALLAS, ATLANTA, leg_a, leg_b,
                               cycle_used_hrs=0.0, start_dt=START)

    fuelings = [e for e in result.events if e.activity == "Fueling"]
    assert len(fuelings) >= 1
    first = fuelings[0]
    assert first.duty_status == "ON"
    assert first.lat is not None and first.lng is not None
    # Interpolated position must lie on the LA→Dallas segment, not at either
    # endpoint (~mile 1000 of an 1100-mi leg → ratio ≈ 0.91).
    lat_lo, lat_hi = sorted((LA[0], DALLAS[0]))
    lng_lo, lng_hi = sorted((LA[1], DALLAS[1]))
    assert lat_lo < first.lat < lat_hi
    assert lng_lo < first.lng < lng_hi
    # Closer to Dallas than LA at ~91% along the leg.
    assert abs(first.lat - DALLAS[0]) < abs(first.lat - LA[0])
    assert abs(first.lng - DALLAS[1]) < abs(first.lng - LA[1])


# ---------------------------------------------------------------------------
# Spec §7.5 test 5 — High cycle_used (=68) forces a 34-hr restart and the
# requires_34h_restart flag.
# ---------------------------------------------------------------------------
def test_high_cycle_used_inserts_34_hr_restart():
    leg_a = _leg(280, 5.0, start=LA, end=DALLAS)
    leg_b = _leg(50, 1.0, start=DALLAS, end=ATLANTA)

    result = planner.plan_trip(LA, DALLAS, ATLANTA, leg_a, leg_b,
                               cycle_used_hrs=68.0, start_dt=START)

    activities = [e.activity for e in result.events]
    assert result.requires_34h_restart is True
    assert "34-hr restart" in activities
    restart = next(e for e in result.events if e.activity == "34-hr restart")
    assert restart.duty_status == "OFF"
    assert _hours(restart) == pytest.approx(planner.RESTART_HR, abs=0.01)


# ---------------------------------------------------------------------------
# Spec §7.5 test 6 — Day-sum invariant: every full 24-hr window of events
# sums to 24.0 hrs (events form a continuous timeline).
# ---------------------------------------------------------------------------
def test_day_sum_invariant_24h_windows_total_24_hours():
    # Multi-day trip (12 hrs driving forces a daily reset → wall-clock > 24 hr).
    leg_a = _leg(700, 12.0, start=LA, end=DALLAS)
    leg_b = _leg(100, 2.0, start=DALLAS, end=ATLANTA)

    result = planner.plan_trip(LA, DALLAS, ATLANTA, leg_a, leg_b,
                               cycle_used_hrs=0.0, start_dt=START)

    trip_start = result.events[0].start_time
    trip_end = result.events[-1].end_time
    full_days = int((trip_end - trip_start).total_seconds() // 86400)
    assert full_days >= 1, "test fixture should produce at least one full 24-hr window"

    for day in range(full_days):
        window_start = trip_start + timedelta(hours=24 * day)
        window_end = trip_start + timedelta(hours=24 * (day + 1))
        total_hours = 0.0
        for e in result.events:
            overlap_start = max(e.start_time, window_start)
            overlap_end = min(e.end_time, window_end)
            if overlap_end > overlap_start:
                total_hours += (overlap_end - overlap_start).total_seconds() / 3600
        assert total_hours == pytest.approx(24.0, abs=0.01)


# ---------------------------------------------------------------------------
# Spec §7.5 test 7 — Cycle invariant: cumulative on-duty (D+ON) hours,
# starting at the offset cycle_used_hrs and resetting at every 34-hr restart,
# never exceeds 70 (with a small bracket for the post-trip that immediately
# precedes a restart, per spec §6 algorithm).
# ---------------------------------------------------------------------------
def test_cycle_invariant_on_duty_with_restart_resets_never_exceeds_70():
    leg_a = _leg(280, 5.0, start=LA, end=DALLAS)
    leg_b = _leg(50, 1.0, start=DALLAS, end=ATLANTA)

    result = planner.plan_trip(LA, DALLAS, ATLANTA, leg_a, leg_b,
                               cycle_used_hrs=68.0, start_dt=START)

    cumulative = 68.0
    events = result.events
    for i, e in enumerate(events):
        if e.activity == "34-hr restart":
            cumulative = 0.0
            continue
        if e.duty_status in ("D", "ON"):
            cumulative += _hours(e)
        # The post-trip immediately preceding a 34-hr restart can briefly
        # tip the cycle past 70 (per spec §6 pseudocode); the restart
        # follows on the next event and resets the cycle to zero.
        next_is_restart = (
            i + 1 < len(events) and events[i + 1].activity == "34-hr restart"
        )
        bracket = planner.POST_TRIP_HR if next_is_restart else 0.0
        assert cumulative <= planner.MAX_CYCLE_HR + bracket + 0.01


# ---------------------------------------------------------------------------
# FMCSA § 395.3(a)(2) 14-hr window + § 395.3(a)(3)(ii) 30-min break invariants.
# Walk the events of a multi-day trip and verify the daily limits hold across
# every reset boundary.
# ---------------------------------------------------------------------------
def test_daily_limits_invariants_across_multi_day_trip():
    leg_a = _leg(700, 12.0, start=LA, end=DALLAS)
    leg_b = _leg(100, 2.0, start=DALLAS, end=ATLANTA)

    result = planner.plan_trip(LA, DALLAS, ATLANTA, leg_a, leg_b,
                               cycle_used_hrs=0.0, start_dt=START)

    drive_today = 0.0
    window_used = 0.0
    since_break = 0.0

    for e in result.events:
        hours = _hours(e)
        if e.duty_status == "D":
            # Invariant 4: cannot drive when since_break ≥ 8.
            assert since_break < planner.MAX_DRIVE_BEFORE_BREAK + 0.01
            drive_today += hours
            window_used += hours
            since_break += hours
            # Invariants 2 & 3.
            assert drive_today <= planner.MAX_DRIVE_PER_DAY + 0.01
            assert window_used <= planner.MAX_DUTY_WINDOW + 0.01
        elif e.duty_status == "ON":
            window_used += hours
            if hours >= planner.BREAK_HR - 0.01:
                since_break = 0.0
            assert window_used <= planner.MAX_DUTY_WINDOW + 0.01
        else:  # OFF or SB
            if hours >= planner.BREAK_HR - 0.01:
                since_break = 0.0
            if hours >= planner.DAILY_RESET_HR - 0.01:
                drive_today = 0.0
                window_used = 0.0
            else:
                window_used += hours

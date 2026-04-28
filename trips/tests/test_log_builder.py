from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from trips.models import TripEvent
from trips.services import log_builder, planner
from trips.services.routing import RouteLeg

LA: tuple[float, float] = (34.0537, -118.2428)
DALLAS: tuple[float, float] = (32.7767, -96.7970)
ATLANTA: tuple[float, float] = (33.7490, -84.3880)
START = datetime(2025, 6, 1, 6, 0, tzinfo=timezone.utc)


def _leg(distance_mi: float, duration_hr: float, start=LA, end=DALLAS) -> RouteLeg:
    return RouteLeg(
        distance_mi=distance_mi,
        duration_hr=duration_hr,
        geometry={
            "type": "LineString",
            "coordinates": [[start[1], start[0]], [end[1], end[0]]],
        },
    )


def _ev(start: datetime, hours: float, duty: str, activity: str = "x", label: str = "x") -> TripEvent:
    return TripEvent(
        sequence=0,
        start_time=start,
        end_time=start + timedelta(hours=hours),
        duty_status=duty,
        activity=activity,
        location_label=label,
    )


# ---------------------------------------------------------------------------
# Spec §5.2 — "The four totals must sum to 24.0".
# Single-day trip → exactly one DailyLog whose totals sum to 24.
# ---------------------------------------------------------------------------
def test_single_day_trip_produces_one_log_summing_to_24h():
    leg_a = _leg(150, 3.0, start=LA, end=DALLAS)
    leg_b = _leg(100, 2.0, start=DALLAS, end=ATLANTA)
    result = planner.plan_trip(LA, DALLAS, ATLANTA, leg_a, leg_b,
                               cycle_used_hrs=10.0, start_dt=START)

    logs = log_builder.build_daily_logs(
        result.events,
        home_timezone="America/Chicago",
        total_distance_mi=leg_a.distance_mi + leg_b.distance_mi,
    )

    assert len(logs) == 1
    log = logs[0]
    total = log.total_off_duty + log.total_sleeper + log.total_driving + log.total_on_duty
    assert total == pytest.approx(24.0, abs=0.01)
    assert log.total_miles == pytest.approx(leg_a.distance_mi + leg_b.distance_mi, abs=0.01)


# ---------------------------------------------------------------------------
# Spec §5.2 — events that cross local-tz midnight must be split across two
# daily sheets; the sum of both halves must equal the original duration.
# ---------------------------------------------------------------------------
def test_event_crossing_local_midnight_is_split_into_two_days():
    chicago = ZoneInfo("America/Chicago")
    # Jun 1 22:00 Chicago → Jun 2 02:00 Chicago = 4-hr driving event across midnight.
    start = datetime(2025, 6, 1, 22, 0, tzinfo=chicago)
    drive = _ev(start, 4.0, "D", activity="Driving", label="En route")

    logs = log_builder.build_daily_logs(
        [drive], home_timezone="America/Chicago", total_distance_mi=200.0,
    )

    assert len(logs) == 2

    day1_drive_segs = [s for s in logs[0].segments if s["duty_status"] == "D"]
    day2_drive_segs = [s for s in logs[1].segments if s["duty_status"] == "D"]
    assert len(day1_drive_segs) == 1
    assert len(day2_drive_segs) == 1

    # Day 1 driving runs 22→24, Day 2 driving runs 0→2; halves sum to 4.0.
    assert day1_drive_segs[0]["start_hr"] == pytest.approx(22.0, abs=0.01)
    assert day1_drive_segs[0]["end_hr"] == pytest.approx(24.0, abs=0.01)
    assert day2_drive_segs[0]["start_hr"] == pytest.approx(0.0, abs=0.01)
    assert day2_drive_segs[0]["end_hr"] == pytest.approx(2.0, abs=0.01)

    assert logs[0].total_driving + logs[1].total_driving == pytest.approx(4.0, abs=0.01)
    # Miles allocated proportionally to driving hours: 50/50 since each day has 2hr.
    assert logs[0].total_miles == pytest.approx(100.0, abs=0.5)
    assert logs[1].total_miles == pytest.approx(100.0, abs=0.5)


# ---------------------------------------------------------------------------
# Spec §5.2 — multi-day trip: every sheet sums to 24.0 and segments form a
# contiguous, non-overlapping cover of the day [0, 24].
# ---------------------------------------------------------------------------
def test_multi_day_trip_each_day_sums_to_24h_with_contiguous_segments():
    # 12-hr leg forces a 10-hr daily reset → trip spans multiple days.
    leg_a = _leg(700, 12.0, start=LA, end=DALLAS)
    leg_b = _leg(100, 2.0, start=DALLAS, end=ATLANTA)
    result = planner.plan_trip(LA, DALLAS, ATLANTA, leg_a, leg_b,
                               cycle_used_hrs=0.0, start_dt=START)

    logs = log_builder.build_daily_logs(
        result.events,
        home_timezone="America/Chicago",
        total_distance_mi=leg_a.distance_mi + leg_b.distance_mi,
    )

    assert len(logs) >= 2

    for log in logs:
        total = log.total_off_duty + log.total_sleeper + log.total_driving + log.total_on_duty
        assert total == pytest.approx(24.0, abs=0.01), f"{log.date} totals != 24"

        # Segments must cover [0, 24] exactly, in order, with no gaps or overlaps.
        assert log.segments[0]["start_hr"] == pytest.approx(0.0, abs=0.01)
        assert log.segments[-1]["end_hr"] == pytest.approx(24.0, abs=0.01)
        for prev, nxt in zip(log.segments, log.segments[1:]):
            assert prev["end_hr"] == pytest.approx(nxt["start_hr"], abs=0.01)

    # Per-day driving miles allocated proportionally to driving hours summing
    # back to the trip total (within float tolerance).
    sum_miles = sum(log.total_miles for log in logs)
    assert sum_miles == pytest.approx(leg_a.distance_mi + leg_b.distance_mi, abs=0.5)


# ---------------------------------------------------------------------------
# Per-day duty totals derived from the segments must equal the values
# stored on the DailyLog (sanity check that segments and totals agree).
# ---------------------------------------------------------------------------
def test_segment_durations_match_daily_log_totals():
    leg_a = _leg(700, 12.0, start=LA, end=DALLAS)
    leg_b = _leg(100, 2.0, start=DALLAS, end=ATLANTA)
    result = planner.plan_trip(LA, DALLAS, ATLANTA, leg_a, leg_b,
                               cycle_used_hrs=0.0, start_dt=START)

    logs = log_builder.build_daily_logs(
        result.events,
        home_timezone="America/Chicago",
        total_distance_mi=leg_a.distance_mi + leg_b.distance_mi,
    )

    for log in logs:
        agg = {"OFF": 0.0, "SB": 0.0, "D": 0.0, "ON": 0.0}
        for s in log.segments:
            agg[s["duty_status"]] += s["end_hr"] - s["start_hr"]
        assert agg["OFF"] == pytest.approx(log.total_off_duty, abs=0.01)
        assert agg["SB"] == pytest.approx(log.total_sleeper, abs=0.01)
        assert agg["D"] == pytest.approx(log.total_driving, abs=0.01)
        assert agg["ON"] == pytest.approx(log.total_on_duty, abs=0.01)

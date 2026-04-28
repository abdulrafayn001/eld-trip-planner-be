"""Per-day log builder.

Pure-function service that converts an ordered list of unsaved
:class:`trips.models.TripEvent` instances (as produced by
:func:`trips.services.planner.plan_trip`) into per-calendar-day unsaved
:class:`trips.models.DailyLog` instances in the home-terminal time zone
(FMCSA § 395.8).

Events that cross local-tz midnight are split. Gaps between events are
filled with Off-Duty so the four duty-status totals on each sheet sum to
exactly 24.0 hours (per spec §5.2). Trip miles are allocated to each day
proportionally to that day's driving hours.

Persistence — assigning ``DailyLog.trip`` and saving — is the caller's
responsibility.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Final
from zoneinfo import ZoneInfo

from trips.models import DailyLog, TripEvent

HOURS_PER_DAY: Final = 24.0
SECONDS_PER_HOUR: Final = 3600.0
_EPS: Final = 1e-9


@dataclass(frozen=True)
class _Segment:
    start_hr: float
    end_hr: float
    duty_status: str
    activity: str
    location_label: str


def build_daily_logs(
    events: list[TripEvent],
    *,
    home_timezone: str,
    total_distance_mi: float,
) -> list[DailyLog]:
    """Build one unsaved ``DailyLog`` per calendar day spanned by ``events``.

    ``events`` must be ordered and continuous (each event's ``start_time``
    equal to the previous event's ``end_time``). All times are interpreted
    in ``home_timezone``. Returned ``DailyLog`` instances have no FK to a
    ``Trip`` yet; the caller assigns ``log.trip = trip`` before saving.
    """
    if not events:
        return []

    tz = ZoneInfo(home_timezone)

    total_driving_hr = sum(
        _hours(e) for e in events if e.duty_status == "D"
    )

    first_local = events[0].start_time.astimezone(tz)
    last_local = events[-1].end_time.astimezone(tz)
    first_date = first_local.date()
    # An event ending exactly at local midnight belongs to the previous day.
    if last_local.time() == time(0, 0):
        last_date = (last_local - timedelta(microseconds=1)).date()
    else:
        last_date = last_local.date()

    logs: list[DailyLog] = []
    current_date = first_date
    while current_date <= last_date:
        day_start = datetime.combine(current_date, time(0, 0), tzinfo=tz)
        day_end = day_start + timedelta(days=1)

        segments: list[_Segment] = []
        driving_hr_today = 0.0
        first_label: str | None = None
        last_label: str | None = None

        for e in events:
            e_start = e.start_time.astimezone(tz)
            e_end = e.end_time.astimezone(tz)
            overlap_start = max(e_start, day_start)
            overlap_end = min(e_end, day_end)
            if (overlap_end - overlap_start).total_seconds() <= _EPS:
                continue

            start_hr = (overlap_start - day_start).total_seconds() / SECONDS_PER_HOUR
            end_hr = (overlap_end - day_start).total_seconds() / SECONDS_PER_HOUR
            segments.append(
                _Segment(
                    start_hr=start_hr,
                    end_hr=end_hr,
                    duty_status=e.duty_status,
                    activity=e.activity,
                    location_label=e.location_label,
                )
            )

            if e.duty_status == "D":
                driving_hr_today += (overlap_end - overlap_start).total_seconds() / SECONDS_PER_HOUR

            if first_label is None:
                first_label = e.location_label
            last_label = e.location_label

        filled = _fill_gaps_with_off_duty(segments, fallback_label=first_label or "Off duty")

        totals = {"OFF": 0.0, "SB": 0.0, "D": 0.0, "ON": 0.0}
        for s in filled:
            totals[s.duty_status] += s.end_hr - s.start_hr

        miles_today = (
            (driving_hr_today / total_driving_hr) * total_distance_mi
            if total_driving_hr > _EPS
            else 0.0
        )

        logs.append(
            DailyLog(
                date=current_date,
                from_label=first_label or "",
                to_label=last_label or first_label or "",
                total_miles=miles_today,
                total_off_duty=totals["OFF"],
                total_sleeper=totals["SB"],
                total_driving=totals["D"],
                total_on_duty=totals["ON"],
                segments=[
                    {
                        "start_hr": round(s.start_hr, 4),
                        "end_hr": round(s.end_hr, 4),
                        "duty_status": s.duty_status,
                        "activity": s.activity,
                        "location_label": s.location_label,
                    }
                    for s in filled
                ],
            )
        )
        current_date += timedelta(days=1)

    return logs


def _fill_gaps_with_off_duty(
    segments: list[_Segment], *, fallback_label: str
) -> list[_Segment]:
    """Insert Off-Duty segments so the day spans [0, 24] with no gaps."""
    segments = sorted(segments, key=lambda s: s.start_hr)
    filled: list[_Segment] = []
    cursor = 0.0
    last_label = fallback_label
    for s in segments:
        if s.start_hr > cursor + _EPS:
            filled.append(
                _Segment(
                    start_hr=cursor,
                    end_hr=s.start_hr,
                    duty_status="OFF",
                    activity="Off duty",
                    location_label=last_label,
                )
            )
        filled.append(s)
        cursor = s.end_hr
        last_label = s.location_label
    if cursor < HOURS_PER_DAY - _EPS:
        filled.append(
            _Segment(
                start_hr=cursor,
                end_hr=HOURS_PER_DAY,
                duty_status="OFF",
                activity="Off duty",
                location_label=last_label,
            )
        )
    return filled


def _hours(event: TripEvent) -> float:
    return (event.end_time - event.start_time).total_seconds() / SECONDS_PER_HOUR

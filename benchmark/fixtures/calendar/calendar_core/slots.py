"""Tiny day-planning helpers. Times are minutes after midnight (0-1440).

Rules (the contract every change must keep):

* An Event covers the half-open interval [start, end): it includes its start
  minute and excludes its end minute. start < end is required.
* Two events overlap only if they share at least one minute. Back-to-back
  events (one ends exactly when the next starts) do not overlap.
* free_slots(events, day_start, day_end, min_minutes) returns the gaps inside
  [day_start, day_end) not covered by any event, as (start, end) tuples sorted
  by start. Events may arrive in any order and may overlap each other. Only
  gaps of at least min_minutes are returned. Events partly outside the day
  are clipped to it.
* parse_time turns "HH:MM" (24-hour) into minutes. Anything it cannot parse
  raises ValueError.
* fmt_duration(minutes) renders "Xh YYm" (e.g. 65 -> "1h 05m", 45 -> "0h 45m").
"""

from __future__ import annotations

from dataclasses import dataclass

MINUTES_PER_DAY = 1440


@dataclass(frozen=True)
class Event:
    start: int
    end: int
    title: str = ""

    def __post_init__(self):
        if not (0 <= self.start < self.end <= MINUTES_PER_DAY):
            raise ValueError(f"bad event times {self.start}-{self.end}")


def parse_time(text: str) -> int:
    """Parse "HH:MM" (24-hour) into minutes after midnight."""
    hours, minutes = text.strip().split(":")
    h, m = int(hours), int(minutes)
    if not (0 <= h < 24 and 0 <= m < 60):
        raise ValueError(f"bad time {text!r}")
    return h * 60 + m


def overlaps(a: Event, b: Event) -> bool:
    """True if the two events share at least one minute."""
    return a.start <= b.end and b.start <= a.end


def free_slots(events: list[Event], day_start: int, day_end: int, min_minutes: int = 1) -> list[tuple[int, int]]:
    """Gaps of at least min_minutes inside [day_start, day_end) not covered by any event."""
    slots = []
    cursor = day_start
    for ev in events:
        if ev.start > cursor:
            slots.append((cursor, ev.start))
        cursor = ev.end
    if cursor < day_end:
        slots.append((cursor, day_end))
    return [(s, e) for s, e in slots if e - s >= min_minutes]


def fmt_duration(minutes: int) -> str:
    """Render minutes as "Xh YYm"."""
    return f"{minutes // 60}h {minutes % 60:02d}m"

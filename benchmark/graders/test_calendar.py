"""Independent acceptance checks for receipts-demo-calendar, written before the agents ran.
Never shown to the system. Used only to grade the PRs it opens."""
import pytest

from calendar_core import Event, free_slots, fmt_duration, overlaps, parse_time


# ---- issue 1
def test_i1_back_to_back_not_overlap():
    assert not overlaps(Event(540, 600), Event(600, 660))
    assert not overlaps(Event(600, 660), Event(540, 600))


def test_i1_real_overlaps_still_detected():
    assert overlaps(Event(540, 600), Event(599, 660))
    assert overlaps(Event(540, 700), Event(600, 650))   # contained
    assert overlaps(Event(540, 600), Event(540, 600))   # identical
    assert overlaps(Event(540, 541), Event(540, 600))   # one-minute overlap


# ---- issue 2
def test_i2_unsorted():
    assert free_slots([Event(720, 780), Event(540, 600)], 480, 1020) == [(480, 540), (600, 720), (780, 1020)]


def test_i2_overlapping_events():
    assert free_slots([Event(540, 700), Event(600, 650)], 480, 800) == [(480, 540), (700, 800)]


def test_i2_chain_of_overlaps_and_touching():
    evs = [Event(600, 660), Event(540, 610), Event(660, 700)]
    assert free_slots(evs, 480, 800) == [(480, 540), (700, 800)]


def test_i2_clipping_and_outside_events():
    evs = [Event(300, 360), Event(450, 500), Event(1000, 1100), Event(1200, 1300)]
    assert free_slots(evs, 480, 1020) == [(500, 1000)]


def test_i2_min_minutes_after_merge():
    evs = [Event(540, 600), Event(610, 700)]
    assert free_slots(evs, 540, 760, min_minutes=30) == [(700, 760)]
    assert free_slots(evs, 540, 760, min_minutes=10) == [(600, 610), (700, 760)]


def test_i2_no_events_and_fully_booked():
    assert free_slots([], 480, 540) == [(480, 540)]
    assert free_slots([Event(400, 600)], 480, 540) == []


# ---- issue 3
@pytest.mark.parametrize("text,expected", [
    ("9am", 540), ("9 AM", 540), ("2:30pm", 870), ("12pm", 720), ("12am", 0), ("12:15am", 15),
    ("11:59pm", 1439), ("1PM", 780), ("09:30", 570), ("00:00", 0), ("23:59", 1439),
])
def test_i3_parses(text, expected):
    assert parse_time(text) == expected


@pytest.mark.parametrize("text", ["13pm", "0am", "9:60am", "9", "pm", "", "24:00", "12:60", "abc"])
def test_i3_rejects(text):
    with pytest.raises(ValueError):
        parse_time(text)


# ---- contract that must keep working
def test_contract_fmt_duration():
    assert fmt_duration(65) == "1h 05m"
    assert fmt_duration(45) == "0h 45m"


def test_contract_event_validation():
    with pytest.raises(ValueError):
        Event(600, 600)

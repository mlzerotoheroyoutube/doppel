"""Hidden acceptance tests for horizon issues #4-#6 (calendar). Never shown to any agent."""
import pytest

import calendar_core as cc
from calendar_core import Event


# ---- issue 4: find_meeting_time
def test_i4_example():
    assert cc.find_meeting_time([[Event(540, 600)], [Event(600, 660)]], 30, 540, 720) == (660, 690)


def test_i4_back_to_back_start_allowed():
    assert cc.find_meeting_time([[Event(540, 600)]], 60, 540, 720) == (600, 660)


def test_i4_unsorted_overlapping_and_three_people():
    cals = [[Event(700, 760), Event(540, 620)], [Event(600, 700)], [Event(780, 800)]]
    assert cc.find_meeting_time(cals, 20, 540, 900) == (760, 780)


def test_i4_none_when_no_room():
    assert cc.find_meeting_time([[Event(540, 700)], [Event(690, 720)]], 30, 540, 720) is None


def test_i4_clipping_and_empty_calendars():
    assert cc.find_meeting_time([[Event(300, 560)], []], 30, 540, 600) == (560, 590)
    assert cc.find_meeting_time([[], []], 45, 600, 700) == (600, 645)


def test_i4_bad_duration():
    for bad in (0, -5):
        with pytest.raises(ValueError):
            cc.find_meeting_time([[]], bad, 540, 600)


# ---- issue 5: parse_event
@pytest.mark.parametrize("text,start,end,title", [
    ("09:30-10:15 Standup", 570, 615, "Standup"),
    ("9am - 10:30am Design review", 540, 630, "Design review"),
    ("2pm-3pm", 840, 900, ""),
    ("12pm-1pm   Lunch  ", 720, 780, "Lunch"),
    ("11:30am-1:15pm Offsite prep", 690, 795, "Offsite prep"),
])
def test_i5_parses(text, start, end, title):
    ev = cc.parse_event(text)
    assert (ev.start, ev.end, ev.title) == (start, end, title)


@pytest.mark.parametrize("text", ["10:00 Standup", "10:00-09:00 Backwards", "10:00-10:00 Zero",
                                  "25:00-26:00 Bad", "9am-13pm Bad", ""])
def test_i5_rejects(text):
    with pytest.raises(ValueError):
        cc.parse_event(text)


# ---- issue 6: day_summary
def test_i6_example():
    evs = [Event(540, 600), Event(570, 660)]
    assert cc.day_summary(evs, 480, 720) == "2 events | busy 2h 00m | longest free 1h 00m"


def test_i6_singular_and_clipping():
    assert cc.day_summary([Event(400, 500)], 480, 600) == "1 event | busy 0h 20m | longest free 1h 40m"


def test_i6_empty_day_and_fully_booked():
    assert cc.day_summary([], 480, 540) == "0 events | busy 0h 00m | longest free 1h 00m"
    assert cc.day_summary([Event(400, 700)], 480, 540) == "1 event | busy 1h 00m | longest free 0h 00m"


def test_i6_ignores_events_outside_day_and_merges_unsorted():
    evs = [Event(900, 960), Event(100, 200), Event(600, 630), Event(540, 610)]
    assert cc.day_summary(evs, 480, 720) == "2 events | busy 1h 30m | longest free 1h 30m"

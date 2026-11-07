# Open issues

## #1 Back-to-back meetings are reported as overlapping

A meeting from 09:00 to 10:00 and another from 10:00 to 11:00 are reported as overlapping:

```python
overlaps(Event(540, 600), Event(600, 660))  # True, should be False
```

Events are half-open intervals [start, end), so back-to-back events share no minute and must not overlap. Events that do share at least one minute (including one event fully inside another, or identical events) must still overlap.

## #2 free_slots is wrong for unsorted, overlapping or out-of-day events

`free_slots` assumes events arrive sorted and never overlap. Real calendars break both:

```python
free_slots([Event(720, 780), Event(540, 600)], 480, 1020)
# returns [(480, 720), (600, 1020)]  -- should be [(480, 540), (600, 720), (780, 1020)]

free_slots([Event(540, 700), Event(600, 650)], 480, 800)
# returns [(480, 540), (650, 800)]   -- should be [(480, 540), (700, 800)]
```

Events may come in any order and may overlap each other. Events that start before the day or end after it should be clipped to the day, and events entirely outside the day ignored. The result must be sorted, cover only time inside [day_start, day_end), and respect min_minutes.

## #3 parse_time should accept 12-hour times like 9am and 2:30pm

People type times the way they say them. `parse_time` should also accept 12-hour times:

- `"9am"` -> 540, `"9 AM"` -> 540, `"2:30pm"` -> 870, `"12pm"` -> 720 (noon), `"12am"` -> 0 (midnight), `"12:15am"` -> 15
- case-insensitive, optional space before am/pm
- 24-hour `"HH:MM"` must keep working exactly as today
- invalid input still raises `ValueError`, e.g. `"13pm"`, `"0am"`, `"9:60am"`, `"9"`, `"pm"`, `""`

## #4 Find a meeting time across several calendars

Add `find_meeting_time(calendars, duration, day_start, day_end)` to `calendar_core` (exported from the package).

- `calendars` is a list of calendars; each calendar is a list of `Event` in any order, possibly overlapping.
- Return the earliest `(start, start + duration)` inside `[day_start, day_end)` where nobody has an event, or `None` if there is no such time.
- Events are half-open, so a meeting may start exactly when someone's event ends. Events partly outside the day are clipped.
- `duration` must be a positive integer, otherwise raise `ValueError`.

Example: `find_meeting_time([[Event(540, 600)], [Event(600, 660)]], 30, 540, 720)` returns `(660, 690)`.

## #5 Parse an event from text

Add `parse_event(text)` to `calendar_core` (exported from the package) that returns an `Event`.

- Format: `"<start>-<end> <title>"`, e.g. `"09:30-10:15 Standup"`, `"9am - 10:30am Design review"`, `"2pm-3pm"`.
- Start and end accept anything `parse_time` accepts (24-hour and 12-hour). Spaces around `-` are allowed.
- The title is the rest of the text with surrounding spaces removed; it may be empty.
- Raise `ValueError` if the dash is missing, a time is invalid, or the end is not after the start.

## #6 One-line day summary

Add `day_summary(events, day_start, day_end)` to `calendar_core` (exported from the package) returning a string:

`"<N> events | busy <D1> | longest free <D2>"`

- N is the number of events that overlap `[day_start, day_end)`; write `1 event` (singular) when N is 1.
- D1 is the total time inside the day covered by at least one event (overlapping events count once), formatted with `fmt_duration`.
- D2 is the length of the longest free gap inside the day (as `free_slots` with min_minutes=1 would find), formatted with `fmt_duration`; `0h 00m` if there is none.

Example: events 09:00-10:00 and 09:30-11:00 in an 08:00-12:00 day give `"2 events | busy 2h 00m | longest free 1h 00m"`.

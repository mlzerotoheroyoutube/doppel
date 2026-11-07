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

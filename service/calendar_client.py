"""Fetch and parse the Google Calendar ICS feed.

The ICS URL lives in service/config.json (gitignored) — it's a "secret address"
from Google Calendar → Settings and sharing → Integrate calendar.

Public API: get_events(start, end) → list[Event] for events overlapping the range,
including expansions of recurring events.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

import recurring_ical_events
import requests
from icalendar import Calendar

SERVICE_DIR = Path(__file__).parent
CONFIG_PATH = SERVICE_DIR / "config.json"


@dataclass
class Event:
    start: date
    end: date
    summary: str
    all_day: bool


def _load_ics_url() -> str:
    with CONFIG_PATH.open() as f:
        return json.load(f)["ics_url"]


def _fetch_ics(url: str) -> bytes:
    # Google's ICS endpoint sometimes serves gzip; requests handles that.
    r = requests.get(url, timeout=15)
    r.raise_for_status()
    return r.content


def _to_date(value) -> date:
    # icalendar returns either a datetime (timed events) or a date (all-day).
    return value.date() if isinstance(value, datetime) else value


def _parse_events(ics_bytes: bytes, start: date, end: date) -> list[Event]:
    cal = Calendar.from_ical(ics_bytes)
    # recurring_ical_events.of(cal).between(a, b) expands RRULEs and returns
    # concrete VEVENT instances that fall in [a, b].
    raw = recurring_ical_events.of(cal).between(start, end)

    events: list[Event] = []
    for comp in raw:
        dtstart = comp["DTSTART"].dt
        dtend_prop = comp.get("DTEND")
        dtend = dtend_prop.dt if dtend_prop else dtstart

        all_day = not isinstance(dtstart, datetime)
        events.append(
            Event(
                start=_to_date(dtstart),
                # ICS all-day DTEND is exclusive (next day). Timed DTEND is the
                # actual end. For rendering we just care what day it lives on,
                # so subtract a day for all-day events so a 1-day event's
                # `end` equals its `start`.
                end=_to_date(dtend) - timedelta(days=1) if all_day else _to_date(dtend),
                summary=str(comp.get("SUMMARY", "(no title)")),
                all_day=all_day,
            )
        )

    # Sort so all-day events float to the top of a day, then by start time.
    events.sort(key=lambda e: (not e.all_day, e.start))
    return events


def get_events(start: date, end: date) -> list[Event]:
    """Return events overlapping [start, end). Network + parse. Raises on error."""
    ics = _fetch_ics(_load_ics_url())
    return _parse_events(ics, start, end)


if __name__ == "__main__":
    # Quick smoke test: python calendar_client.py
    today = date.today()
    month_start = today.replace(day=1)
    next_month = (month_start + timedelta(days=32)).replace(day=1)
    for e in get_events(month_start, next_month):
        tag = "[all-day]" if e.all_day else "         "
        print(f"{e.start} {tag} {e.summary}")

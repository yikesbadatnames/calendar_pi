"""Fetch and parse one-or-more Google Calendar ICS feeds.

Config lives in service/config.json (gitignored). Two accepted shapes:

    {"ics_url": "https://.../basic.ics"}        # single-calendar shorthand

    {"calendars": [                              # multi-calendar
        {"name": "Personal", "url": "...", "prefix": "P", "email": "me@x.com"},
        {"name": "Family",   "url": "...", "prefix": "F", "email": "fam@x.com",
                             "color": "#ffcccb"}
    ]}

Public API: get_events(start, end) → list[Event] merged across all calendars.
Events are attributed to the calendar whose configured email matches the
ORGANIZER property, so a shared event (both people invited) always shows in
the inviter's color/prefix regardless of which feed it arrived on. Duplicates
across feeds are dropped after attribution.
"""

from __future__ import annotations

import json
import sys
import traceback
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

import recurring_ical_events
import requests
from icalendar import Calendar

SERVICE_DIR = Path(__file__).parent
CONFIG_PATH = SERVICE_DIR / "config.json"

# Default text color for events whose calendar didn't specify one.
# Kept in sync with renderer.TEXT_EVENT so single-calendar configs look
# identical to how they did before multi-calendar support landed.
DEFAULT_EVENT_COLOR = "#c8d4ff"


@dataclass
class Event:
    start: date
    end: date
    summary: str
    all_day: bool
    color: str = DEFAULT_EVENT_COLOR
    prefix: str = ""
    # Preserves the original DTSTART datetime for timed events so we can sort
    # by time-of-day within a day. None for all-day events (which have no time).
    start_dt: datetime | None = None


def _load_calendars() -> list[dict]:
    """Return a normalized list of {'name', 'url', 'color'} dicts."""
    with CONFIG_PATH.open() as f:
        cfg = json.load(f)

    if "calendars" in cfg:
        raw = cfg["calendars"]
    elif "ics_url" in cfg:
        # Back-compat with the single-URL shorthand.
        raw = [{"name": "Calendar", "url": cfg["ics_url"]}]
    else:
        raise ValueError(
            "config.json must contain either 'calendars' (list) or 'ics_url' (string)"
        )

    normalized = []
    for cal in raw:
        normalized.append({
            "name": cal.get("name", "Calendar"),
            "url": cal["url"],
            "color": cal.get("color", DEFAULT_EVENT_COLOR),
            "prefix": cal.get("prefix", ""),
            "email": cal.get("email", "").lower(),
            # If set, only keep events where this email is in the ATTENDEE list.
            # Useful for a partner's calendar where you only want events they
            # invited you to (skips their personal time-blocking).
            "require_attendee": cal.get("require_attendee", "").lower(),
        })
    return normalized


def _fetch_ics(url: str) -> bytes:
    # Google's ICS endpoint sometimes serves gzip; requests handles that.
    r = requests.get(url, timeout=15)
    r.raise_for_status()
    return r.content


def _to_date(value) -> date:
    # icalendar returns either a datetime (timed events) or a date (all-day).
    return value.date() if isinstance(value, datetime) else value


def _organizer_email(comp) -> str:
    """Return the ORGANIZER's email in lowercase, or '' if none. ICS stores it
    as a vCalAddress that stringifies to 'mailto:foo@bar.com' (case may vary)."""
    organizer = comp.get("ORGANIZER")
    if organizer is None:
        return ""
    addr = str(organizer).strip().lower()
    if addr.startswith("mailto:"):
        addr = addr[len("mailto:"):]
    return addr


def _attendee_emails(comp) -> set[str]:
    """Return the set of ATTENDEE emails (lowercase). icalendar returns a
    single vCalAddress when there's one attendee, a list when there are many —
    we normalize both cases."""
    raw = comp.get("ATTENDEE", [])
    if not isinstance(raw, list):
        raw = [raw]
    result: set[str] = set()
    for a in raw:
        addr = str(a).strip().lower()
        if addr.startswith("mailto:"):
            addr = addr[len("mailto:"):]
        if addr:
            result.add(addr)
    return result


def _parse_events(
    ics_bytes: bytes,
    start: date,
    end: date,
    feed_cal: dict,
    email_to_cal: dict,
) -> list[Event]:
    cal = Calendar.from_ical(ics_bytes)
    # recurring_ical_events.of(cal).between(a, b) expands RRULEs and returns
    # concrete VEVENT instances that fall in [a, b].
    raw = recurring_ical_events.of(cal).between(start, end)

    required_attendee = feed_cal.get("require_attendee", "")
    events: list[Event] = []
    filtered = 0
    for comp in raw:
        # Attendee filter — for partner-style calendars where we only want
        # events they explicitly invited us to.
        if required_attendee and required_attendee not in _attendee_emails(comp):
            filtered += 1
            continue

        dtstart = comp["DTSTART"].dt
        dtend_prop = comp.get("DTEND")
        dtend = dtend_prop.dt if dtend_prop else dtstart

        # Attribute to the ORGANIZER's calendar (the inviter). If we can't
        # identify them, fall back to the feed this event came in on.
        attribution = email_to_cal.get(_organizer_email(comp), feed_cal)

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
                color=attribution["color"],
                prefix=attribution["prefix"],
                start_dt=dtstart if isinstance(dtstart, datetime) else None,
            )
        )
    if required_attendee and filtered:
        print(
            f"    (filtered {filtered} events not addressed to {required_attendee})",
            file=sys.stderr,
        )
    return events


def get_events(start: date, end: date) -> list[Event]:
    """Return events overlapping [start, end) merged across all configured
    calendars. Per-calendar fetch failures are logged and skipped so one bad
    URL doesn't blank the whole display. Duplicates across feeds (shared
    events both people were invited to) are deduped after organizer-based
    attribution — both copies resolve to the same inviter, so any copy wins."""
    calendars = _load_calendars()
    email_to_cal = {c["email"]: c for c in calendars if c["email"]}

    print(f"fetching {len(calendars)} calendar(s) for {start}..{end}", file=sys.stderr)
    all_events: list[Event] = []
    for cal in calendars:
        print(f"  → {cal['name']}...", end="", file=sys.stderr, flush=True)
        try:
            ics = _fetch_ics(cal["url"])
            parsed = _parse_events(ics, start, end, cal, email_to_cal)
            all_events.extend(parsed)
            print(f" {len(parsed)} events", file=sys.stderr)
        except Exception:
            print(" FAILED", file=sys.stderr)
            traceback.print_exc()

    # Dedupe on (summary, start, end, all_day). Google sometimes assigns
    # separate UIDs per invitee for the same underlying event, so we key on
    # the visible content instead.
    seen: dict[tuple, Event] = {}
    for e in all_events:
        key = (e.summary, e.start, e.end, e.all_day)
        seen.setdefault(key, e)

    deduped = list(seen.values())
    print(
        f"  {len(all_events)} raw → {len(deduped)} after dedupe",
        file=sys.stderr,
    )
    # Sort chronologically by day. Within a day, all-day events float to the
    # top, then timed events by their time-of-day. Using (hour, minute) as a
    # tuple sidesteps any tz-aware/naive datetime comparison issues.
    def _sort_key(e: Event) -> tuple:
        if e.start_dt is not None:
            return (e.start, True, e.start_dt.hour, e.start_dt.minute)
        return (e.start, False, 0, 0)

    deduped.sort(key=_sort_key)
    return deduped


if __name__ == "__main__":
    # Quick smoke test: python calendar_client.py
    today = date.today()
    month_start = today.replace(day=1)
    next_month = (month_start + timedelta(days=32)).replace(day=1)
    for e in get_events(month_start, next_month):
        tag = "[all-day]" if e.all_day else (e.start_dt.strftime("[%H:%M]  ") if e.start_dt else "         ")
        who = f"[{e.prefix}]" if e.prefix else "   "
        print(f"{e.start} {tag} {who} {e.summary}")

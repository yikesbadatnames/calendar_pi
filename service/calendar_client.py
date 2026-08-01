"""Fetch and parse one-or-more Google Calendar ICS feeds.

Config lives in service/config.json (gitignored). Two accepted shapes:

    {"ics_url": "https://.../basic.ics"}        # single-calendar shorthand

    {"calendars": [                              # multi-calendar
        {"name": "Personal", "url": "...", "prefix": "P", "email": "me@x.com"},
        {"name": "Family",   "url": "...", "prefix": "F", "email": "fam@x.com",
                             "color": "#ffcccb"}
    ]}

Public API: get_events(start, end) → FetchResult, whose .events are merged
across all calendars. Events are attributed to the calendar whose configured
email matches the ORGANIZER property, so a shared event (both people invited)
always shows in the inviter's color/prefix regardless of which feed it arrived
on. Duplicates across feeds are dropped after attribution.

Each feed's last successful result is kept in memory, so a feed that fails on
one refresh is served from cache (reported as *stale*) instead of vanishing
from the display. Only a feed that fails with no usable cache is a *failure*.
"""

from __future__ import annotations

import json
import sys
import time
import traceback
from dataclasses import dataclass, field
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


# How many times to try a single feed before giving up on it this refresh.
FETCH_ATTEMPTS = 3

# Sleep after attempt 1 and attempt 2 respectively. Short, because this runs on
# the UI thread (see RETRY_BUDGET_S).
RETRY_BACKOFF_S = (2.0, 5.0)

# 429 means Google is asking us to slow down; hammering it makes that worse, so
# throttling gets a longer pause than an ordinary transport error.
THROTTLED_BACKOFF_S = 10.0

# Total extra wall-clock we'll spend sleeping on retries across ALL feeds in one
# refresh. main.py calls get_events on the tkinter main thread, so every second
# spent here is a second of frozen kiosk and unresponsive buttons — the budget
# keeps a flapping network from locking the display up for minutes.
RETRY_BUDGET_S = 20.0


class CalendarFetchError(Exception):
    """Raised for config-level problems — missing or malformed config.json.

    Deliberately NOT raised for a feed that failed to fetch: that's normal
    operation on a Pi over wifi and is reported per-feed via FetchResult, so
    callers can tell "Zoe's feed is a few minutes stale" apart from "config.json
    is broken and no amount of retrying will help".
    """


@dataclass
class FetchResult:
    """Outcome of one refresh across all configured feeds.

    `events` is always the best available merge: fresh data where we got it,
    last-known-good data where we didn't. The three name lists say how much to
    trust it — a caller with a populated display should keep what it has when
    `failed_feeds` is non-empty, since that merge is genuinely missing someone's
    calendar rather than merely being a little behind.
    """

    events: list[Event] = field(default_factory=list)
    fresh_feeds: list[str] = field(default_factory=list)
    stale_feeds: list[str] = field(default_factory=list)   # served from cache
    failed_feeds: list[str] = field(default_factory=list)  # no usable cache

    @property
    def ok(self) -> bool:
        return not self.stale_feeds and not self.failed_feeds


@dataclass
class _FeedCache:
    """One feed's last successful fetch, plus the window it was fetched for."""

    events: list[Event]
    window_start: date
    window_end: date
    fetched_at: datetime

    def covers(self, start: date, end: date) -> bool:
        """True if this cache was fetched over a window containing [start, end).

        Checked before serving stale data: a cache fetched for June–August can't
        answer a request for December. Without this, navigating past the cached
        span during an outage would render a confidently empty month instead of
        admitting the feed is unavailable.
        """
        return self.window_start <= start and end <= self.window_end


# Per-feed last-known-good results, keyed by calendar name. Module-level so it
# survives across get_events calls for the life of the process. Bounded by the
# number of configured calendars, so it can't grow without limit.
_last_good: dict[str, _FeedCache] = {}


@dataclass
class Event:
    start: date
    end: date
    summary: str
    all_day: bool
    color: str = DEFAULT_EVENT_COLOR
    prefix: str = ""
    # Preserves the original DTSTART/DTEND datetimes for timed events so we can
    # sort by time-of-day and size week-view event blocks by duration. None for
    # all-day events (which have no time).
    start_dt: datetime | None = None
    end_dt: datetime | None = None


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


def _is_retryable(exc: Exception) -> bool:
    """Whether retrying this failure could plausibly succeed.

    Transport errors (DNS, TLS, connection reset, read timeout) and 5xx are
    transient — exactly the wifi-blip case we want to ride out. A 4xx is not:
    a revoked or mistyped secret ICS URL returns 404 forever, and retrying it
    just burns the shared retry budget that a genuinely flaky feed needs. 429
    is the exception — it means "later", not "never".
    """
    if isinstance(exc, requests.HTTPError) and exc.response is not None:
        status = exc.response.status_code
        return status == 429 or status >= 500
    return isinstance(exc, requests.RequestException)


def _fetch_ics(url: str, deadline: float) -> bytes:
    """Fetch one ICS payload, retrying transient failures until `deadline`
    (a time.monotonic() value shared across all feeds in this refresh)."""
    last_exc: Exception | None = None
    for attempt in range(FETCH_ATTEMPTS):
        try:
            # Google's ICS endpoint sometimes serves gzip; requests handles that.
            r = requests.get(url, timeout=15)
            r.raise_for_status()
            return r.content
        except Exception as e:
            if not _is_retryable(e):
                raise
            last_exc = e

        if attempt == FETCH_ATTEMPTS - 1:
            break
        throttled = (
            isinstance(last_exc, requests.HTTPError)
            and last_exc.response is not None
            and last_exc.response.status_code == 429
        )
        delay = THROTTLED_BACKOFF_S if throttled else RETRY_BACKOFF_S[attempt]
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            print(" retry budget spent;", end="", file=sys.stderr)
            break
        time.sleep(min(delay, remaining))
        print(f" retry {attempt + 2}/{FETCH_ATTEMPTS}...", end="", file=sys.stderr, flush=True)

    assert last_exc is not None
    raise last_exc


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
                end_dt=dtend if isinstance(dtend, datetime) else None,
            )
        )
    if required_attendee and filtered:
        print(
            f"    (filtered {filtered} events not addressed to {required_attendee})",
            file=sys.stderr,
        )
    return events


def _dedupe_and_sort(all_events: list[Event]) -> list[Event]:
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


def get_events(start: date, end: date) -> FetchResult:
    """Return events overlapping [start, end) merged across all configured
    calendars. Duplicates across feeds (shared events both people were invited
    to) are deduped after organizer-based attribution — both copies resolve to
    the same inviter, so any copy wins.

    A feed that fails falls back to its last successful result when that result
    covers the requested window, and is reported in `stale_feeds`. This is the
    whole point of the cache: previously a single dropped request made that
    person's events disappear from the display entirely until the next
    successful refresh, which on a flaky connection could be a long time.

    Raises CalendarFetchError only if config.json itself is unusable.
    """
    try:
        calendars = _load_calendars()
    except Exception as e:
        # Not a network problem — surfaced separately so the caller can say so.
        raise CalendarFetchError(f"could not load {CONFIG_PATH.name}: {e}") from e

    email_to_cal = {c["email"]: c for c in calendars if c["email"]}

    # One budget shared by every feed, started before the first request, so the
    # worst case for a whole refresh is bounded no matter how many feeds flap.
    deadline = time.monotonic() + RETRY_BUDGET_S

    print(f"fetching {len(calendars)} calendar(s) for {start}..{end}", file=sys.stderr)
    all_events: list[Event] = []
    result = FetchResult()
    for cal in calendars:
        name = cal["name"]
        print(f"  → {name}...", end="", file=sys.stderr, flush=True)
        try:
            ics = _fetch_ics(cal["url"], deadline)
            parsed = _parse_events(ics, start, end, cal, email_to_cal)
        except Exception:
            print(" FAILED", file=sys.stderr)
            traceback.print_exc()
            cached = _last_good.get(name)
            if cached is not None and cached.covers(start, end):
                # Narrow the cached window down to what was actually asked for,
                # so a stale feed contributes exactly what a fresh one would.
                usable = [e for e in cached.events if e.start < end and e.end >= start]
                all_events.extend(usable)
                result.stale_feeds.append(name)
                age = datetime.now() - cached.fetched_at
                print(
                    f"    using cached {name}: {len(usable)} events, "
                    f"{int(age.total_seconds() // 60)}m old",
                    file=sys.stderr,
                )
            else:
                result.failed_feeds.append(name)
                why = "no cached data" if cached is None else "cache doesn't cover this window"
                print(f"    {name} unavailable ({why})", file=sys.stderr)
            continue

        _last_good[name] = _FeedCache(parsed, start, end, datetime.now())
        all_events.extend(parsed)
        result.fresh_feeds.append(name)
        print(f" {len(parsed)} events", file=sys.stderr)

    result.events = _dedupe_and_sort(all_events)
    return result


if __name__ == "__main__":
    # Quick smoke test: python calendar_client.py
    today = date.today()
    month_start = today.replace(day=1)
    next_month = (month_start + timedelta(days=32)).replace(day=1)
    try:
        res = get_events(month_start, next_month)
    except CalendarFetchError as err:
        raise SystemExit(f"!! {err}")
    print(
        f"\nfresh={res.fresh_feeds or '-'} stale={res.stale_feeds or '-'} "
        f"failed={res.failed_feeds or '-'}\n",
        file=sys.stderr,
    )
    for e in res.events:
        tag = "[all-day]" if e.all_day else (e.start_dt.strftime("[%H:%M]  ") if e.start_dt else "         ")
        who = f"[{e.prefix}]" if e.prefix else "   "
        print(f"{e.start} {tag} {who} {e.summary}")

"""tkinter renderers for calendar_pi.

MonthView.draw(anchor, events) — 6-row month grid, one label per event per day.
WeekView.draw(anchor, events)  — Google-Calendar-style time-of-day grid: 7 day
    columns, hour rows, timed events as colored blocks positioned by start
    time and sized by duration; all-day (and multi-day) events pin to a
    header strip.

Both views re-render fully on every draw() — no incremental updates.
"""

from __future__ import annotations

import calendar as pycal
import tkinter as tk
from datetime import date, datetime, timedelta

from calendar_client import Event

DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

# Palette. Simple and readable on a small monitor.
BG = "#000000"
CELL_BG = "#0e0e0e"
CELL_BG_OTHER_MONTH = "#050505"
CELL_BG_TODAY = "#1e2a3a"
TEXT = "#ffffff"
TEXT_DIM = "#555555"
TEXT_EVENT = "#c8d4ff"
HEADER_BG = "#1a1a1a"
GRID_LINE = "#222222"

# WeekView-specific. Fixed hour range (below/above are dropped from the timed
# grid); multi-day timed events fall back to the all-day strip.
HOUR_START = 6                    # first hour shown (6am)
HOUR_END = 23                     # grid ends at HOUR_END:00 (i.e. shows 6–23)
PX_PER_HOUR = 40
TIME_LABEL_WIDTH = 60
ALLDAY_STRIP_ROW_HEIGHT = 18
ALLDAY_STRIP_ROWS = 3             # visible rows in the header strip
MIN_EVENT_HEIGHT = 13             # floor so zero-duration events stay visible


class MonthView:
    def __init__(self, root: tk.Tk, visible: bool = True):
        self.root = root
        self.frame = tk.Frame(root, bg=BG)
        if visible:
            self.frame.pack(fill="both", expand=True)

    def draw(self, anchor: date, events: list[Event]) -> None:
        for w in self.frame.winfo_children():
            w.destroy()

        # Month/year header spanning all 7 columns.
        tk.Label(
            self.frame,
            text=anchor.strftime("%B %Y"),
            bg=BG,
            fg=TEXT,
            font=("Helvetica", 22, "bold"),
        ).grid(row=0, column=0, columnspan=7, pady=(6, 4))

        # Day-of-week row.
        for i, name in enumerate(DAY_NAMES):
            tk.Label(
                self.frame,
                text=name,
                bg=HEADER_BG,
                fg=TEXT,
                font=("Helvetica", 11, "bold"),
                pady=4,
            ).grid(row=1, column=i, sticky="nsew")

        # Bucket events by day. A multi-day event shows on every covered day.
        events_by_day: dict[date, list[Event]] = {}
        for e in events:
            span_days = (e.end - e.start).days + 1
            for offset in range(max(1, span_days)):
                d = e.start + timedelta(days=offset)
                events_by_day.setdefault(d, []).append(e)

        # Get the 6-row Mon-Sun grid covering the month.
        weeks = pycal.Calendar(firstweekday=0).monthdatescalendar(
            anchor.year, anchor.month
        )
        today = date.today()

        for week_idx, week in enumerate(weeks):
            for day_idx, d in enumerate(week):
                in_month = d.month == anchor.month
                is_today = d == today
                bg = (
                    CELL_BG_TODAY
                    if is_today
                    else (CELL_BG if in_month else CELL_BG_OTHER_MONTH)
                )
                num_fg = TEXT if in_month else TEXT_DIM

                cell = tk.Frame(self.frame, bg=bg, highlightthickness=1,
                                highlightbackground="#222")
                cell.grid(row=2 + week_idx, column=day_idx, sticky="nsew")

                tk.Label(
                    cell,
                    text=str(d.day),
                    bg=bg,
                    fg=num_fg,
                    font=("Helvetica", 11, "bold"),
                ).pack(anchor="nw", padx=3, pady=(2, 0))

                for ev in events_by_day.get(d, []):
                    label = f"{ev.prefix}: {ev.summary}" if ev.prefix else ev.summary
                    # Prefix timed events with start time on their start day
                    # only (Google Calendar-style). Compact "9am" / "9:30am" so
                    # the title still fits in a narrow cell.
                    if ev.start_dt is not None and d == ev.start:
                        t = ev.start_dt
                        hour12 = t.hour % 12 or 12
                        suffix = "am" if t.hour < 12 else "pm"
                        time_str = f"{hour12}{suffix}" if t.minute == 0 else f"{hour12}:{t.minute:02d}{suffix}"
                        label = f"{time_str} {label}"
                    tk.Label(
                        cell,
                        text=label,
                        bg=bg,
                        # In-month days use the calendar's own color so mixed
                        # calendars are visually distinguishable. Non-month
                        # days stay dim regardless.
                        fg=ev.color if in_month else TEXT_DIM,
                        font=("Helvetica", 9),
                        anchor="w",
                        justify="left",
                        wraplength=140,
                    ).pack(anchor="w", fill="x", padx=3)

        # Stretchy grid so cells fill the window.
        for col in range(7):
            self.frame.grid_columnconfigure(col, weight=1)
        # Row 0 (month header) and row 1 (day names) fixed height;
        # week rows share remaining space.
        self.frame.grid_rowconfigure(0, weight=0)
        self.frame.grid_rowconfigure(1, weight=0)
        for row in range(2, 2 + len(weeks)):
            self.frame.grid_rowconfigure(row, weight=1)


class WeekView:
    """Google-Calendar-style week view.

    Layout (top to bottom):
      - "Jun 29 – Jul 5, 2026" header label
      - 7-column day header canvas (weekday abbr + day number)
      - all-day strip canvas (multi-day + all-day events as bars)
      - main time-grid canvas (hour lines + timed events as colored blocks)

    Full redraw on every draw() call. Also re-renders on canvas resize so the
    initial layout settles once tkinter computes the fullscreen size.
    """

    def __init__(self, root: tk.Tk, visible: bool = True):
        self.root = root
        self.frame = tk.Frame(root, bg=BG)
        if visible:
            self.frame.pack(fill="both", expand=True)

        self.header = tk.Label(
            self.frame, text="", bg=BG, fg=TEXT,
            font=("Helvetica", 22, "bold"),
        )
        self.header.pack(pady=(6, 4))

        self.day_header_canvas = tk.Canvas(
            self.frame, bg=HEADER_BG, height=32, highlightthickness=0,
        )
        self.day_header_canvas.pack(fill="x")

        self.allday_canvas = tk.Canvas(
            self.frame, bg=CELL_BG,
            height=ALLDAY_STRIP_ROW_HEIGHT * ALLDAY_STRIP_ROWS + 4,
            highlightthickness=0,
        )
        self.allday_canvas.pack(fill="x")

        self.grid_canvas = tk.Canvas(
            self.frame, bg=CELL_BG, highlightthickness=0,
        )
        self.grid_canvas.pack(fill="both", expand=True)

        # Cached state so we can re-render on <Configure> events without the
        # caller having to know about resize.
        self._anchor: date | None = None
        self._events: list[Event] = []
        for c in (self.day_header_canvas, self.allday_canvas, self.grid_canvas):
            c.bind("<Configure>", lambda _e: self._render())

    def draw(self, anchor: date, events: list[Event]) -> None:
        self._anchor = anchor
        self._events = events
        self._render()

    # ---- internal ---------------------------------------------------------

    def _render(self) -> None:
        if self._anchor is None:
            return
        # winfo_width() returns 1 before tkinter has laid the widget out.
        # Skip; the Configure event that follows will fire us again.
        total_width = self.grid_canvas.winfo_width()
        if total_width < 100:
            return

        week_start = self._anchor - timedelta(days=self._anchor.weekday())
        week_end = week_start + timedelta(days=7)
        last_day = week_start + timedelta(days=6)

        # Header text: same month → "Jul 6 – Jul 12, 2026"; spanning months →
        # both months spelled out. Uses week_start.day directly to sidestep
        # platform strftime differences ("%-d" is glibc-only).
        if week_start.month == last_day.month:
            header_text = (
                f"{week_start.strftime('%b')} {week_start.day} – "
                f"{last_day.day}, {last_day.year}"
            )
        else:
            header_text = (
                f"{week_start.strftime('%b')} {week_start.day} – "
                f"{last_day.strftime('%b')} {last_day.day}, {last_day.year}"
            )
        self.header.config(text=header_text)

        self.day_header_canvas.delete("all")
        self.allday_canvas.delete("all")
        self.grid_canvas.delete("all")

        day_col_width = (total_width - TIME_LABEL_WIDTH) / 7
        today = date.today()

        # Day column headers.
        for i in range(7):
            d = week_start + timedelta(days=i)
            x = TIME_LABEL_WIDTH + i * day_col_width
            bg = CELL_BG_TODAY if d == today else HEADER_BG
            self.day_header_canvas.create_rectangle(
                x, 0, x + day_col_width, 32, fill=bg, outline="",
            )
            self.day_header_canvas.create_text(
                x + day_col_width / 2, 16,
                text=f"{d.strftime('%a')} {d.day}",
                fill=TEXT, font=("Helvetica", 11, "bold"),
            )

        # Filter to events overlapping the week window.
        week_events = [
            e for e in self._events
            if e.start < week_end and e.end >= week_start
        ]

        # Split: all-day + multi-day-timed → strip; single-day timed with both
        # datetimes → time grid; anything malformed → strip as a safe fallback.
        strip_events: list[Event] = []
        timed_events: list[Event] = []
        for e in week_events:
            if e.all_day or e.start != e.end:
                strip_events.append(e)
            elif e.start_dt is not None and e.end_dt is not None:
                timed_events.append(e)
            else:
                strip_events.append(e)

        self._draw_allday_strip(strip_events, week_start, day_col_width)
        self._draw_time_grid(timed_events, week_start, day_col_width, today)

    def _draw_allday_strip(
        self, events: list[Event], week_start: date, day_col_width: float,
    ) -> None:
        """Stack multi-day/all-day bars into rows. Greedy: prefer longer events
        first, place each in the lowest row where every day it covers is free."""
        # day_rows[col] is a list where index = row, value = True if occupied.
        day_rows: list[list[bool]] = [[] for _ in range(7)]

        def by_length_then_start(e: Event) -> tuple:
            span = (e.end - e.start).days
            return (-span, e.start)

        for e in sorted(events, key=by_length_then_start):
            start_col = max(0, (e.start - week_start).days)
            end_col = min(6, (e.end - week_start).days)
            if start_col > 6 or end_col < 0:
                continue

            row = 0
            while True:
                free = all(
                    len(day_rows[c]) <= row or not day_rows[c][row]
                    for c in range(start_col, end_col + 1)
                )
                if free:
                    break
                row += 1
            for c in range(start_col, end_col + 1):
                while len(day_rows[c]) <= row:
                    day_rows[c].append(False)
                day_rows[c][row] = True

            if row >= ALLDAY_STRIP_ROWS:
                # Out of room in the visible strip; drop silently. Rare.
                continue

            y_top = 2 + row * ALLDAY_STRIP_ROW_HEIGHT
            y_bot = y_top + ALLDAY_STRIP_ROW_HEIGHT - 2
            x_left = TIME_LABEL_WIDTH + start_col * day_col_width + 1
            x_right = TIME_LABEL_WIDTH + (end_col + 1) * day_col_width - 1

            # Multi-day-timed events retain their start time in the label so we
            # don't lose the "when" info by demoting them to the strip.
            label = e.summary
            if not e.all_day and e.start_dt is not None:
                label = f"{e.start_dt.strftime('%H:%M')}→ {label}"
            if e.prefix:
                label = f"{e.prefix}: {label}"

            self.allday_canvas.create_rectangle(
                x_left, y_top, x_right, y_bot, fill=e.color, outline="",
            )
            self.allday_canvas.create_text(
                x_left + 4, (y_top + y_bot) / 2,
                text=label, fill=BG, anchor="w",
                font=("Helvetica", 9, "bold"),
            )

    def _draw_time_grid(
        self,
        events: list[Event],
        week_start: date,
        day_col_width: float,
        today: date,
    ) -> None:
        hours_shown = HOUR_END - HOUR_START
        total_height = hours_shown * PX_PER_HOUR

        # Column backgrounds (today's column highlighted).
        for i in range(7):
            d = week_start + timedelta(days=i)
            x = TIME_LABEL_WIDTH + i * day_col_width
            bg = CELL_BG_TODAY if d == today else CELL_BG
            self.grid_canvas.create_rectangle(
                x, 0, x + day_col_width, total_height, fill=bg, outline="",
            )

        # Horizontal hour lines + left-column time labels.
        for h in range(hours_shown + 1):
            y = h * PX_PER_HOUR
            self.grid_canvas.create_line(
                TIME_LABEL_WIDTH, y,
                TIME_LABEL_WIDTH + 7 * day_col_width, y,
                fill=GRID_LINE,
            )
            if h < hours_shown:
                self.grid_canvas.create_text(
                    TIME_LABEL_WIDTH - 6, y + 2,
                    text=f"{HOUR_START + h:02d}:00",
                    fill=TEXT_DIM, anchor="ne",
                    font=("Helvetica", 8),
                )

        # Vertical day-column dividers.
        for i in range(8):
            x = TIME_LABEL_WIDTH + i * day_col_width
            self.grid_canvas.create_line(x, 0, x, total_height, fill=GRID_LINE)

        # Group timed events by day-column, then run cluster-aware lane
        # assignment per column so a solo event doesn't share width with an
        # unrelated overlap elsewhere in the day.
        events_by_col: dict[int, list[Event]] = {i: [] for i in range(7)}
        for e in events:
            col = (e.start - week_start).days
            if 0 <= col <= 6:
                events_by_col[col].append(e)

        for col, day_events in events_by_col.items():
            self._draw_day_column(day_events, col, day_col_width, total_height)

    def _draw_day_column(
        self,
        events: list[Event],
        day_col: int,
        day_col_width: float,
        total_height: float,
    ) -> None:
        events_sorted = sorted(events, key=lambda e: (e.start_dt, e.end_dt))

        # Split into clusters: an event starts a new cluster if it begins at or
        # after every prior end time in the current cluster.
        clusters: list[list[Event]] = []
        current: list[Event] = []
        cluster_max_end: datetime | None = None
        for e in events_sorted:
            if not current:
                current = [e]
                cluster_max_end = e.end_dt
            elif e.start_dt < cluster_max_end:
                current.append(e)
                if e.end_dt > cluster_max_end:
                    cluster_max_end = e.end_dt
            else:
                clusters.append(current)
                current = [e]
                cluster_max_end = e.end_dt
        if current:
            clusters.append(current)

        # Within each cluster, greedy sweep-line lane assignment.
        for cluster in clusters:
            lane_ends: list[datetime] = []
            assignments: list[tuple[Event, int]] = []
            for e in cluster:
                placed = False
                for i, end in enumerate(lane_ends):
                    if end <= e.start_dt:
                        lane_ends[i] = e.end_dt
                        assignments.append((e, i))
                        placed = True
                        break
                if not placed:
                    lane_ends.append(e.end_dt)
                    assignments.append((e, len(lane_ends) - 1))
            total_lanes = len(lane_ends)
            for e, lane_idx in assignments:
                self._draw_event_block(
                    e, day_col, day_col_width, lane_idx, total_lanes,
                    total_height,
                )

    def _draw_event_block(
        self,
        e: Event,
        day_col: int,
        day_col_width: float,
        lane_idx: int,
        total_lanes: int,
        total_height: float,
    ) -> None:
        assert e.start_dt is not None and e.end_dt is not None

        # Y: convert to minutes-from-HOUR_START, clamp into the visible range.
        start_min = (e.start_dt.hour - HOUR_START) * 60 + e.start_dt.minute
        duration_min = (e.end_dt - e.start_dt).total_seconds() / 60
        y_top = start_min * PX_PER_HOUR / 60
        y_bottom = y_top + max(MIN_EVENT_HEIGHT, duration_min * PX_PER_HOUR / 60)
        y_top = max(0.0, min(y_top, total_height - MIN_EVENT_HEIGHT))
        y_bottom = max(y_top + MIN_EVENT_HEIGHT, min(y_bottom, total_height))

        lane_width = day_col_width / total_lanes
        x_left = TIME_LABEL_WIDTH + day_col * day_col_width + lane_idx * lane_width + 1
        x_right = x_left + lane_width - 2

        self.grid_canvas.create_rectangle(
            x_left, y_top, x_right, y_bottom, fill=e.color, outline="",
        )
        label = e.summary
        if e.prefix:
            label = f"{e.prefix}: {label}"
        label = f"{e.start_dt.strftime('%H:%M')} {label}"
        self.grid_canvas.create_text(
            x_left + 3, y_top + 2,
            text=label, fill=BG, anchor="nw",
            font=("Helvetica", 8, "bold"),
            width=max(1, x_right - x_left - 6),
        )

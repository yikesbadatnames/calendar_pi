"""tkinter month-grid renderer for calendar_pi.

MonthView.draw(anchor, events) fully re-renders the grid for the month
containing `anchor`, placing each event as a text label in the cell(s)
of the day(s) it covers. Multi-day events show on every day they span.
Non-current-month cells are dimmed. Today's cell is highlighted.
"""

from __future__ import annotations

import calendar as pycal
import tkinter as tk
from datetime import date, timedelta

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


class MonthView:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.frame = tk.Frame(root, bg=BG)
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

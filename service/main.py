"""Main loop for calendar_pi.

Owns view state (anchor date + month/week mode), schedules 60s ICS refetches,
and wires the three kiosk buttons to state mutations. tkinter runs the UI on
the main thread; gpiozero callbacks fire on background threads and hop back
onto the main thread via root.after(0, ...).
"""

from __future__ import annotations

import sys
import traceback
from datetime import date, timedelta

import tkinter as tk

from calendar_client import Event, get_events
from renderer import MonthView, WeekView

REFRESH_MS = 60_000

# Rolling fetch window around the anchor month. Navigation within this window
# is instant (no network). Bigger = smoother, at the cost of a slightly longer
# first fetch and marginally more memory. 3+3 lets you swipe half a year in
# either direction before hitting a refetch.
WINDOW_MONTHS_BACK = 3
WINDOW_MONTHS_FORWARD = 3

# BCM pin numbers — match the README hardware table and button_test.py.
PIN_TOGGLE = 17
PIN_PREV = 27
PIN_NEXT = 22


def _advance_months(anchor: date, months: int) -> date:
    """Return the first day of the month `months` away from anchor's month.
    Positive advances forward; negative rewinds. Uses the day-1 + 32-day trick
    to sidestep month-length edge cases (Feb 28 → Mar 28 → Feb 28 issues)."""
    d = anchor.replace(day=1)
    if months >= 0:
        for _ in range(months):
            d = (d + timedelta(days=32)).replace(day=1)
    else:
        for _ in range(-months):
            d = (d - timedelta(days=1)).replace(day=1)
    return d


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.configure(bg="black", cursor="none")
        self.root.attributes("-fullscreen", True)
        # Escape exits — convenient during dev over VNC.
        self.root.bind("<Escape>", lambda _e: self.root.destroy())
        # Keyboard stand-ins for the physical buttons so navigation is
        # testable over VNC before the GPIO wiring exists. Mapping matches the
        # button roles in the README hardware table. "Period" here means the
        # currently visible span — a month in month mode, a week in week mode.
        self.root.bind("<Left>",  lambda _e: self.prev_period())
        self.root.bind("<Right>", lambda _e: self.next_period())
        self.root.bind("<space>", lambda _e: self.toggle_view())

        self.anchor = date.today().replace(day=1)
        self.events: list[Event] = []
        # (inclusive_start, exclusive_end) of the events we currently hold.
        # None until the first successful fetch.
        self.window_start: date | None = None
        self.window_end: date | None = None

        # Both views live for the whole app lifetime; toggle_view swaps which
        # one is packed. This is cheaper than destroy/rebuild and preserves
        # per-view state (e.g. the WeekView's cached canvas contents).
        self.view_mode: str = "month"
        self.month_view = MonthView(root, visible=True)
        self.week_view = WeekView(root, visible=False)

        # First fetch is synchronous so the initial render has data;
        # subsequent refreshes are timer-driven.
        self._refetch_window_and_redraw()
        self.root.after(REFRESH_MS, self._periodic_refresh)

    @property
    def view(self):
        return self.month_view if self.view_mode == "month" else self.week_view

    def _window_for(self, anchor: date) -> tuple[date, date]:
        start = _advance_months(anchor, -WINDOW_MONTHS_BACK)
        end = _advance_months(anchor, WINDOW_MONTHS_FORWARD + 1)
        return start, end

    def _visible_end(self) -> date:
        """Exclusive upper bound of what the current view will draw. Used to
        decide whether the cached window covers the whole visible span."""
        if self.view_mode == "month":
            return _advance_months(self.anchor, 1)
        return self.anchor + timedelta(days=7)

    def _refetch_window_and_redraw(self) -> None:
        window_start, window_end = self._window_for(self.anchor)
        try:
            self.events = get_events(window_start, window_end)
            self.window_start = window_start
            self.window_end = window_end
        except Exception:
            # Keep whatever we had on screen; log and move on.
            print("calendar fetch failed:", file=sys.stderr)
            traceback.print_exc()
        self.view.draw(self.anchor, self.events)

    def _periodic_refresh(self) -> None:
        self._refetch_window_and_redraw()
        self.root.after(REFRESH_MS, self._periodic_refresh)

    def _navigate_to(self, new_anchor: date) -> None:
        """Change the anchor. Refetches only if the visible span extends
        outside the cached window — the common case (arrow-key nav within the
        window) is a pure redraw with no network I/O."""
        self.anchor = new_anchor
        end_of_visible = self._visible_end()
        if (self.window_start is not None
                and self.window_start <= self.anchor
                and end_of_visible <= self.window_end):
            self.view.draw(self.anchor, self.events)
        else:
            self._refetch_window_and_redraw()

    def next_period(self) -> None:
        if self.view_mode == "month":
            new_anchor = _advance_months(self.anchor, 1)
        else:
            new_anchor = self.anchor + timedelta(days=7)
        self._navigate_to(new_anchor)

    def prev_period(self) -> None:
        if self.view_mode == "month":
            new_anchor = _advance_months(self.anchor, -1)
        else:
            new_anchor = self.anchor - timedelta(days=7)
        self._navigate_to(new_anchor)

    def toggle_view(self) -> None:
        """Flip month ↔ week and preserve the anchor semantically. Month → week
        snaps to today's week when the anchor is on the current month, else to
        the Monday of the anchor's month's first week — so browsing a future
        month and toggling stays in that month instead of jumping to today.
        Week → month re-anchors to the first of the anchor Monday's month."""
        old_view = self.view
        if self.view_mode == "month":
            self.view_mode = "week"
            today = date.today()
            ref = today if (self.anchor.year, self.anchor.month) == (today.year, today.month) else self.anchor
            self.anchor = ref - timedelta(days=ref.weekday())
        else:
            self.view_mode = "month"
            self.anchor = self.anchor.replace(day=1)
        old_view.frame.pack_forget()
        self.view.frame.pack(fill="both", expand=True)
        self.view.draw(self.anchor, self.events)


def wire_buttons(app: App) -> list:
    """Attach gpiozero callbacks. Returns the Button objects so the caller
    can keep them alive (gpiozero relies on Python refs to keep GPIO watchers
    active)."""
    try:
        from gpiozero import Button
    except Exception as e:
        # Running on the Mac for dev, or gpiozero missing. Non-fatal.
        print(f"gpiozero not available ({e}); button wiring skipped")
        return []

    def on_main(fn):
        # Marshal onto tkinter's main thread.
        return lambda: app.root.after(0, fn)

    buttons = [
        Button(PIN_TOGGLE, pull_up=True, bounce_time=0.05),
        Button(PIN_PREV, pull_up=True, bounce_time=0.05),
        Button(PIN_NEXT, pull_up=True, bounce_time=0.05),
    ]
    buttons[0].when_pressed = on_main(app.toggle_view)
    buttons[1].when_pressed = on_main(app.prev_period)
    buttons[2].when_pressed = on_main(app.next_period)
    return buttons


def main() -> None:
    root = tk.Tk()
    root.title("calendar_pi")
    app = App(root)
    _buttons = wire_buttons(app)  # noqa: F841 — keep refs alive
    root.mainloop()


if __name__ == "__main__":
    main()

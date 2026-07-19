"""Main loop for calendar_pi.

Owns view state (anchor month), schedules 60s ICS refetches, and wires the
three kiosk buttons to state mutations. tkinter runs the UI on the main
thread; gpiozero callbacks fire on background threads and hop back onto
the main thread via root.after(0, ...).
"""

from __future__ import annotations

import sys
import traceback
from datetime import date, timedelta

import tkinter as tk

from calendar_client import Event, get_events
from renderer import MonthView

REFRESH_MS = 60_000

# BCM pin numbers — match the README hardware table and button_test.py.
PIN_TOGGLE = 17
PIN_PREV = 27
PIN_NEXT = 22


def month_bounds(anchor: date) -> tuple[date, date]:
    """Return (first_day_of_month, first_day_of_next_month) for anchor."""
    start = anchor.replace(day=1)
    # Jump ~32 days then snap to day 1 to handle any month length.
    end = (start + timedelta(days=32)).replace(day=1)
    return start, end


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.configure(bg="black", cursor="none")
        self.root.attributes("-fullscreen", True)
        # Escape exits — convenient during dev over VNC.
        self.root.bind("<Escape>", lambda _e: self.root.destroy())
        # Keyboard stand-ins for the physical buttons so month navigation is
        # testable over VNC before the GPIO wiring exists. Mapping matches the
        # button roles in the README hardware table.
        self.root.bind("<Left>",  lambda _e: self.prev_month())
        self.root.bind("<Right>", lambda _e: self.next_month())
        self.root.bind("<space>", lambda _e: self.toggle_view())

        self.anchor = date.today().replace(day=1)
        self.events: list[Event] = []
        self.view = MonthView(root)

        # First fetch is synchronous so the initial render has data;
        # subsequent refreshes are timer-driven.
        self._refetch_and_redraw()
        self.root.after(REFRESH_MS, self._periodic_refresh)

    def _fetch_current_month(self) -> None:
        start, end = month_bounds(self.anchor)
        try:
            self.events = get_events(start, end)
        except Exception:
            # Network hiccup, parse error, whatever — keep the previous
            # events on screen and log. Redraw still happens.
            print("calendar fetch failed:", file=sys.stderr)
            traceback.print_exc()

    def _refetch_and_redraw(self) -> None:
        self._fetch_current_month()
        self.view.draw(self.anchor, self.events)

    def _periodic_refresh(self) -> None:
        self._refetch_and_redraw()
        self.root.after(REFRESH_MS, self._periodic_refresh)

    def next_month(self) -> None:
        _, next_start = month_bounds(self.anchor)
        self.anchor = next_start
        self._refetch_and_redraw()

    def prev_month(self) -> None:
        if self.anchor.month == 1:
            self.anchor = self.anchor.replace(year=self.anchor.year - 1, month=12)
        else:
            self.anchor = self.anchor.replace(month=self.anchor.month - 1)
        self._refetch_and_redraw()

    def toggle_view(self) -> None:
        # Month-only for the MVP; toggle no-ops until week view exists.
        print("toggle pressed — no-op (week view not implemented yet)")


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
    buttons[1].when_pressed = on_main(app.prev_month)
    buttons[2].when_pressed = on_main(app.next_month)
    return buttons


def main() -> None:
    root = tk.Tk()
    root.title("calendar_pi")
    app = App(root)
    _buttons = wire_buttons(app)  # noqa: F841 — keep refs alive
    root.mainloop()


if __name__ == "__main__":
    main()

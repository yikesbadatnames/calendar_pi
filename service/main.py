"""Main loop for calendar_pi.

Owns view state (anchor date + month/week mode), schedules 60s ICS refetches,
and wires the three kiosk buttons to state mutations. tkinter runs the UI on
the main thread; gpiozero callbacks fire on background threads and hop back
onto the main thread via root.after(0, ...).
"""

from __future__ import annotations

import socket
import sys
import time
import traceback
from datetime import date, timedelta

import tkinter as tk

from calendar_client import Event, get_events
from renderer import MonthView, WeekView

REFRESH_MS = 60_000

# Minimum time between accepted key actions. Key autorepeat (holding a key down)
# and mashing both fire far faster than this; anything sooner than the interval
# is dropped, so a held/spammed key can't chain into a burst of redraws and,
# worse, back-to-back network refetches. 0.15s still allows brisk intentional
# taps while cutting autorepeat (~30/s) down to a handful per second.
MIN_ACTION_INTERVAL_S = 0.15

# Rolling fetch window around the anchor month. Navigation within this window
# is instant (no network); only stepping outside it triggers a refetch. Skewed
# forward because you look ahead far more than behind: 2 back + 6 forward means
# half a year of look-ahead before a refetch, at the cost of a slightly longer
# first fetch and marginally more memory.
WINDOW_MONTHS_BACK = 2
WINDOW_MONTHS_FORWARD = 6

# BCM pin numbers — match the README hardware table and button_test.py.
PIN_TOGGLE = 17
PIN_PREV = 27
PIN_NEXT = 22

# Leading NUL puts this in Linux's abstract socket namespace: no file on disk,
# and the kernel drops the name when the process dies. That's the whole appeal
# over a pidfile — a killed or crashed kiosk leaves nothing stale to clean up.
_INSTANCE_LOCK_ADDR = "\0calendar_pi-kiosk"

# Module-level so the socket outlives acquire_instance_lock(); if it were
# garbage collected the name would be released and the guard would do nothing.
_instance_lock: socket.socket | None = None


def acquire_instance_lock() -> bool:
    """True if we're the only kiosk running, False if another holds the lock.

    Two instances can't share the GPIO buttons — the second one's pin claims
    fail with 'GPIO busy' — so we'd rather refuse to start than half-run.
    Abstract sockets are Linux-only; on the Mac we skip the check entirely
    since dev instances there have no pins to fight over.
    """
    global _instance_lock
    if not sys.platform.startswith("linux"):
        return True
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    try:
        sock.bind(_INSTANCE_LOCK_ADDR)
    except OSError:
        sock.close()
        return False
    _instance_lock = sock
    return True


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
        # A USB arrow pad is a HID keyboard, so these bindings ARE the kiosk's
        # input — not stand-ins. "Period" means the currently visible span: a
        # month in month mode, a week in week mode.
        #   Left / Right : previous / next period
        #   Up / Down    : toggle month <-> week — either key flips, so no key
        #                  is ever a dead no-op regardless of the current view
        # Space toggles too, a convenient stand-in during dev over VNC.
        self.root.bind("<Left>",  lambda _e: self._on_key(self.prev_period))
        self.root.bind("<Right>", lambda _e: self._on_key(self.next_period))
        self.root.bind("<Up>",    lambda _e: self._on_key(self.toggle_view))
        self.root.bind("<Down>",  lambda _e: self._on_key(self.toggle_view))
        self.root.bind("<space>", lambda _e: self._on_key(self.toggle_view))

        # Timestamp of the last accepted key action, for input throttling.
        self._last_action_t = 0.0

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

        # Overlay shown while a network refetch is in flight so a blocking fetch
        # reads as "working" not "frozen". Uses place() (not pack/grid) so it
        # floats over whichever view is active without touching their layout.
        self.status = tk.Label(
            root, text="", bg="#1e2a3a", fg="#ffffff",
            font=("Helvetica", 11, "bold"), padx=10, pady=4,
        )

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

    def _show_status(self, text: str) -> None:
        self.status.config(text=text)
        self.status.place(relx=1.0, rely=0.0, x=-12, y=12, anchor="ne")
        self.status.lift()

    def _hide_status(self) -> None:
        self.status.place_forget()

    def _refetch_window_and_redraw(self) -> None:
        window_start, window_end = self._window_for(self.anchor)
        # Paint the badge BEFORE the blocking fetch. get_events runs on this (UI)
        # thread, so nothing repaints until it returns; update_idletasks flushes
        # just the redraw without processing key events (so a mid-fetch button
        # press can't re-enter this method).
        self._show_status("Updating…")
        self.root.update_idletasks()
        ok = True
        try:
            self.events = get_events(window_start, window_end)
            self.window_start = window_start
            self.window_end = window_end
        except Exception:
            # Keep whatever we had on screen; log and move on.
            ok = False
            print("calendar fetch failed:", file=sys.stderr)
            traceback.print_exc()
        self.view.draw(self.anchor, self.events)
        if ok:
            self._hide_status()
        else:
            # Distinct message so a failed refresh doesn't masquerade as a good
            # one; auto-clears, and the 60s timer will retry on its own.
            self._show_status("⚠ refresh failed — will retry")
            self.root.after(4000, self._hide_status)

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

    def _on_key(self, action) -> None:
        """Throttle keyboard input. Drops any press arriving within
        MIN_ACTION_INTERVAL_S of the last accepted one, so key autorepeat (from
        holding a key) or fast mashing can't spam redraws / network refetches.
        Gating here at the binding layer keeps the throttle in one place and off
        the semantic methods, so callers other than key events aren't affected."""
        now = time.monotonic()
        if now - self._last_action_t < MIN_ACTION_INTERVAL_S:
            return
        self._last_action_t = now
        action()


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

    # Claim pins one at a time so a single unavailable line costs us that one
    # button rather than the whole calendar. 'GPIO busy' means something else
    # already owns the line — usually a second kiosk instance.
    buttons = []
    for pin, name, action in (
        (PIN_TOGGLE, "toggle", app.toggle_view),
        (PIN_PREV, "prev", app.prev_period),
        (PIN_NEXT, "next", app.next_period),
    ):
        try:
            button = Button(pin, pull_up=True, bounce_time=0.05)
        except Exception as e:
            print(f"button {name} (BCM{pin}) unavailable: {e}", file=sys.stderr)
            continue
        button.when_pressed = on_main(action)
        buttons.append(button)
    return buttons


def main() -> None:
    if not acquire_instance_lock():
        # Exit 0, not 1: a duplicate launch is a no-op we handled, not a
        # failure. Keeps `Restart=on-failure` supervisors from spinning.
        print("calendar_pi is already running; exiting.", file=sys.stderr)
        return
    root = tk.Tk()
    root.title("calendar_pi")
    app = App(root)
    _buttons = wire_buttons(app)  # noqa: F841 — keep refs alive
    root.mainloop()


if __name__ == "__main__":
    main()

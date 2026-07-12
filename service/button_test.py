"""Print a line whenever one of the three kiosk buttons is pressed.

Wiring: each button bridges its GPIO pin to a ground pin. Internal pull-ups
are enabled, so no external resistors are needed. See README hardware table.
"""

import signal
from datetime import datetime

from gpiozero import Button

BUTTONS = {
    17: "toggle",
    27: "prev",
    22: "next",
}


def on_press(pin: int, label: str) -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {label} pressed (GPIO {pin})", flush=True)


def main() -> None:
    print("button_test: watching " + ", ".join(
        f"GPIO {pin} ({label})" for pin, label in BUTTONS.items()
    ))
    print("press Ctrl-C to exit")

    # Keep references so the Button objects aren't garbage-collected.
    buttons = []
    for pin, label in BUTTONS.items():
        b = Button(pin, pull_up=True, bounce_time=0.05)
        b.when_pressed = lambda pin=pin, label=label: on_press(pin, label)
        buttons.append(b)

    signal.pause()


if __name__ == "__main__":
    main()

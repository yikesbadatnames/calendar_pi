# calendar_pi

A Google Calendar kiosk for a Raspberry Pi Zero 2 WH driving a portable monitor. Three physical buttons control the view: toggle between month/week, jump to the next period, and jump to the previous period.

## Hardware

- Raspberry Pi Zero 2 WH (quad-core ARMv8, 512 MB, soldered 40-pin header)
- Portable HDMI monitor
- Micro SD card + Mac micro SD adapter (for imaging)
- Breadboard
- 3 momentary push buttons
- Female-to-male jumper wires
- Soldering kit (for header/button leads if needed)

### Buttons

| Button   | Function                     | GPIO (BCM) | Physical Pin |
| -------- | ---------------------------- | ---------- | ------------ |
| Toggle   | Switch between month / week  | GPIO 17    | 11           |
| Previous | Previous month/week          | GPIO 27    | 13           |
| Next     | Next month/week              | GPIO 22    | 15           |

Each button wires one leg to its GPIO pin and the other leg to a ground pin (e.g. pin 9). Internal pull-ups are enabled in software, so no external resistors are needed.

## Software Stack

- **OS**: Raspberry Pi OS Lite (Bookworm, 64-bit)
- **Display**: Chromium in kiosk mode, launched via a lightweight X session (or Wayland via `cage`/`labwc`)
- **Calendar view**: Google Calendar embedded view (`calendar.google.com/calendar/embed`) with view + date query params
- **Button handling**: Python service using `gpiozero`, driving the browser via keyboard shortcuts or a small local control page

## High-Level Flow

1. Pi boots straight into Chromium kiosk pointed at Google Calendar (or a local page that iframes it).
2. A background Python service watches the three GPIO pins.
3. Each press sends a corresponding action to the browser:
   - Toggle → switch `mode=MONTH` ↔ `mode=WEEK`
   - Prev / Next → adjust the current date and reload with new `dates=` param
4. State (current view + anchor date) is kept in the Python service so button presses stay consistent across reloads.

## Setup Outline

1. **Flash SD card**
   - Use Raspberry Pi Imager on the Mac to write Raspberry Pi OS Lite.
   - Pre-configure Wi-Fi, hostname (`cal-pi`), SSH, and user in Imager's advanced settings.
2. **First boot**
   - SSH in, run `sudo apt update && sudo apt full-upgrade`.
   - Install the desktop bits needed for kiosk (`xserver-xorg`, `x11-xserver-utils`, `xinit`, `chromium-browser`, `unclutter`).
3. **Google Calendar access**
   - Simplest path: use the public embed URL for the calendar (works read-only, no auth).
   - Optional: sign in once in Chromium and persist the profile so private calendars render.
4. **Kiosk autostart**
   - `~/.xinitrc` launches Chromium with `--kiosk --noerrdialogs --disable-infobars --incognito=false`.
   - `systemd` unit starts `startx` on boot.
   - Interim (before the final HDMI display is sorted): run the kiosk under a virtual X display over VNC — see "Kiosk dev mode (VNC)" below.
5. **Button service**
   - Python venv with `gpiozero`.
   - `systemd` unit runs the button daemon; it talks to the kiosk page over a local WebSocket (or fires xdotool key events).
6. **Wiring**
   - Prototype on the breadboard with jumpers first, then solder to a small perfboard once the layout is confirmed.

## Kiosk dev mode (VNC)

Prove the kiosk software stack (Chromium + Google Calendar embed) without
needing the final HDMI display. The Pi runs a virtual X session; the Mac
views it over VNC.

On the Pi (once, after `pi_bootstrap.sh`):

```
bash scripts/kiosk_bootstrap.sh   # installs X + openbox + chromium + tigervnc + unclutter
                                  # prompts once for a VNC password
```

On the Pi (each time you want to (re)start the kiosk):

```
bash scripts/kiosk_start.sh                       # defaults to US Holidays public calendar
bash scripts/kiosk_start.sh 'https://.../embed?…' # or pass your own URL
```

On the Mac: Finder → Go → Connect to Server → `vnc://cal-pi.local:5901`.

Stop with `vncserver -kill :1`. Logs land in `~/.vnc/*.log`.

## Repo Layout (planned)

```
calendar_pi/
├── README.md
├── kiosk/              # HTML/JS page that iframes Google Calendar and receives button events
├── service/            # Python button daemon (gpiozero)
├── systemd/            # unit files for kiosk + button service
└── scripts/            # setup / provisioning helpers
```

## Status

- ✅ Pi Zero 2 WH flashed with Raspberry Pi OS Lite (64-bit, Bookworm)
- ✅ Pi on Wi-Fi, SSH working from the Mac over the home network
- ✅ OS updated (`apt update && apt full-upgrade`), Python 3 confirmed available
- ✅ Repo scaffolded: `service/button_test.py`, `scripts/pi_bootstrap.sh`, empty `kiosk/` and `systemd/`
- ✅ `pi_bootstrap.sh` run on the Pi (venv + gpiozero ready)
- ✅ Kiosk dev-mode scaffolded: `scripts/kiosk_bootstrap.sh`, `scripts/kiosk_start.sh`
- ⏳ Next: run the kiosk bootstrap + start scripts on the Pi, connect over VNC from the Mac, confirm Google Calendar's embed renders in Chromium
- ⏳ In parallel: wire the three buttons on the breadboard and run `python service/button_test.py`
- ⏳ Blocked on **final** kiosk display (USB-C-only monitor mismatch — see Ongoing Issues); dev work is not blocked

## Ongoing Issues

Running log of hardware / setup blockers and gotchas. Newest at top.

### 🟡 Portable monitor is USB-C-only (no HDMI input)

- **Problem**: All the portable monitors on hand accept USB-C only, both for power and video. Pi Zero 2 WH outputs mini HDMI.
- **Why it's tricky**: USB-C video is DisplayPort Alt Mode, not HDMI — a passive cable can't bridge the two.
- **Options being considered**:
  1. Active HDMI → USB-C DP converter (~$40–60, e.g. j5create JVA02) — needs its own USB power.
  2. Buy a cheap dedicated HDMI display for the kiosk (~$50–80, 7–10" Pi-oriented panels).
  3. Return one of the USB-C-only monitors for a dual-input model.
- **Status**: open. Not blocking dev work (SSH is enough for now); only blocks the final kiosk display.

### ✅ Solved: SSH failing on first-boot race

- **Symptom**: Ping worked, but SSH to `cal-pi.local:22` hung indefinitely at "Connecting to port 22." `nc -zv` timed out. Looked like classic device-to-device isolation on Eero.
- **Actual cause**: unclear — probably a first-boot race between SSH keygen / userconf / mDNS registration on Bookworm's first boot, combined with a marginal SD flash. Not Eero.
- **Fix**: Re-flashed **Raspberry Pi OS Lite (64-bit)** cleanly, watched Verify complete, gave the Pi more time to settle after boot. SSH came up on its own over Wi-Fi.
- **Lesson**: on a fresh Bookworm Pi, first boot can take longer than the flickering LED implies. If ping works but SSH refuses for a few minutes, wait and retry before assuming network isolation.

### ✅ Solved: Solid green LED = wrong OS image / corrupted flash

- **Symptom**: Pi powered on but green LED stayed solid on, never flickered — bootloader stuck.
- **Cause**: Flashed the wrong OS variant for the chip (or flash didn't verify cleanly).
- **Fix**: Re-flashed with **Raspberry Pi OS Lite (64-bit, Bookworm)** — the Zero 2 WH is 64-bit-capable. Watched the Verify step complete cleanly.
- **Lesson**: original Pi Zero W/WH is 32-bit ARMv6; Pi Zero **2** W/WH is 64-bit ARMv8. Same form factor, very different chip. Always pick 64-bit for the Zero 2.

### ✅ Solved: Pi wouldn't boot on USB hub power

- **Symptom**: Solid green LED, no boot, using a micro USB cable from a shared USB hub.
- **Cause**: The hub was underpowered (mouse-charging cable + other peripherals sharing current from the Mac). Marginal voltage → Pi browns out mid-boot.
- **Fix**: Powered directly from a wall charger via USB-C brick + USB-C-to-micro-USB cable.
- **Lesson**: Pi Zero needs a clean 5V supply. Skip hubs, skip flimsy cables (especially ones that ship with mice, LED strips, etc.). Any decent phone-class wall charger works.

### 📋 Known future gotchas

- **2.4 GHz Wi-Fi only**: Pi Zero 2 W/WH's radio doesn't see 5 GHz networks. If a network broadcasts split SSIDs, must target the 2.4 GHz one.
- **No red LED on Pi Zero**: only one green ACT LED. Tutorials showing red PWR LEDs are for Pi 3/4/5.
- **Two micro USB ports**: only `PWR IN` is for power. Plugging into the `USB` port gives inconsistent boot behavior.
- **Micro USB vs mini HDMI**: Pi Zero uses **mini HDMI** (Type C), not micro HDMI (Type D — that's Pi 4/5).

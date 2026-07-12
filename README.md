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

- **OS**: Raspberry Pi OS Lite (Trixie, 64-bit) — Debian 13-based, not Bookworm as originally planned.
- **Display**: minimal X session (TigerVNC virtual display for dev, physical HDMI when the display situation is sorted) + openbox as the window manager.
- **App**: single Python program that talks to the Google Calendar API and renders the calendar itself with `tkinter` (or `pygame` — TBD). No browser involved.
- **Auth**: OAuth 2.0 installed-app flow. `credentials.json` from Google Cloud Console, `token.json` generated once and refreshed automatically thereafter.
- **Button handling**: same Python program (or a sibling module) using `gpiozero` on GPIO 17/27/22. Buttons call `state.next_month()` / `state.toggle_view()` etc. directly — no browser to drive.

## Why not a browser?

Original plan was Chromium in kiosk mode pointed at Google Calendar's embed. Ruled out empirically this session:

- **Chromium** OOMs on the Pi Zero 2 W's 512 MB before the calendar even finishes loading. Even with light flags.
- **Epiphany** (WebKit, ~1/3 Chromium's RAM) launches but hits "page unresponsive" on Google Calendar's embed JS because there's no GPU acceleration under VNC, and its `--application-mode` requires a full "installed web app" ceremony (matching profile prefix + `.desktop` file + icons) that fails hard without every piece.

The pragmatic pivot: skip the browser entirely, own the rendering pipeline in Python. Way lighter (~20 MB vs ~300 MB), cleaner button integration (direct function calls, no fake keyboard events), and total UI control.

## Why the API instead of the ICS feed?

We considered the ICS feed (public/secret URL that returns the calendar in iCalendar format) — simpler setup, no OAuth. Chose the full Google Calendar API instead because:

- We want auto-refresh in near-real-time (push notifications via webhooks, or short-interval polling that's rate-limit-friendly). ICS is poll-only and Google doesn't guarantee freshness on the feed.
- Room to grow later — writing events back, reading multiple calendars with distinct colors, richer event metadata.
- The migration cost from ICS → API later would be small in code (fetch layer is ~30 lines), but the setup churn is annoying to do twice.

## High-Level Flow

1. Pi boots straight into a virtual X session (dev: viewed over VNC from the Mac; final: driven to HDMI).
2. Openbox launches; our Python program launches fullscreen inside it.
3. Python program:
   - Loads `token.json`, refreshes access token as needed.
   - Fetches events from the Google Calendar API for the currently-anchored month/week.
   - Draws the calendar (month grid or week view).
   - Watches GPIO 17/27/22 via `gpiozero`.
4. Button presses mutate view state (`view = "week"`, `anchor += one_month`, etc.) → trigger a redraw.
5. Background task re-fetches events on a schedule (or via push webhooks later) so the display stays fresh without user action.

## Setup Outline

1. **Flash SD card** ✅ done
   - Raspberry Pi Imager → Raspberry Pi OS Lite (64-bit).
   - Pre-configured Wi-Fi, hostname (`cal-pi`), SSH, and user in Imager's advanced settings.
2. **First boot** ✅ done
   - SSH in, `sudo apt update && sudo apt full-upgrade`.
3. **Base Python + GPIO** ✅ done
   - `bash scripts/pi_bootstrap.sh` on the Pi. Installs `python3-venv python3-gpiozero python3-lgpio`, creates `~/calendar_pi/.venv` with `--system-site-packages`.
4. **X + VNC dev environment** ✅ done
   - `bash scripts/kiosk_bootstrap.sh` on the Pi. Installs `xserver-xorg xinit x11-xserver-utils openbox tigervnc-standalone-server unclutter wmctrl` (and `epiphany-browser`, which is now unused but harmless).
   - `bash scripts/kiosk_start.sh` starts a virtual X session on display `:1`, port `5901`. See "Kiosk dev mode (VNC)" below. This currently still launches Epiphany because we haven't swapped it for the Python renderer yet — that's the next step.
5. **Google Cloud + OAuth (next up)** ⏳
   - Create a Google Cloud project, enable Google Calendar API, create OAuth 2.0 client credentials (type: "Desktop app"), download `credentials.json`.
   - Note: if you leave the OAuth consent screen in "Testing" mode, refresh tokens expire every 7 days. Publish the app (mostly a formality for personal use) to avoid that.
   - Run a one-time auth helper on the Mac that opens a browser, does the consent flow, and writes `token.json`. `scp token.json cal-pi:~/calendar_pi/service/`.
6. **Python calendar app (next up)** ⏳
   - `service/calendar_client.py` — loads `credentials.json` + `token.json`, calls the Calendar API, returns events for a date range. Auto-refreshes access tokens.
   - `service/renderer.py` — draws a month/week view of the events. `tkinter` first (already in the stdlib), consider `pygame` only if we hit rendering limits.
   - `service/main.py` — main loop. Holds view state (`view`, `anchor_date`). Kicks off periodic re-fetches. Wires GPIO buttons to state mutations + redraws.
7. **Swap kiosk_start.sh** ⏳
   - Change xstartup to launch the Python app instead of Epiphany (drop `wmctrl` fullscreen dance; Tk/pygame can go fullscreen natively).
8. **Physical display + autostart** ⏳ (blocked on HDMI cable/adapter — see Ongoing Issues)
   - Add `~/.xinitrc` + `systemd` unit that starts the X session on boot without VNC.
9. **Wiring the buttons** ⏳
   - Breadboard first — verify each button prints its label using `service/button_test.py`.
   - Move to perfboard once the layout's confirmed.

## Kiosk dev mode (VNC)

Iterate on the kiosk without needing an HDMI display connected. The Pi
runs a virtual X session; the Mac views it over VNC.

On the Pi (once, after `pi_bootstrap.sh`):

```
bash scripts/kiosk_bootstrap.sh   # installs X + openbox + tigervnc + wmctrl (+ vestigial epiphany)
                                  # prompts once for a VNC password
```

On the Pi (each time you want to (re)start the session):

```
bash scripts/kiosk_start.sh       # currently launches Epiphany — will be swapped
                                  # for the Python calendar app once it exists
```

On the Mac: Finder → Go → Connect to Server → `vnc://cal-pi.local:5901`.
Include the `:5901` — macOS defaults to 5900 (display `:0`) and will fail.

Stop with `vncserver -kill :1`. Logs land in `~/.config/tigervnc/*.log`
(newer tigervnc uses XDG paths; older versions used `~/.vnc/*.log`).

## Repo Layout

```
calendar_pi/
├── README.md
├── service/            # Python: calendar_client.py, renderer.py, main.py, button_test.py
├── scripts/            # setup + start scripts (pi_bootstrap.sh, kiosk_bootstrap.sh, kiosk_start.sh)
├── systemd/            # unit files for kiosk + button service (empty for now)
└── kiosk/              # LEGACY — was going to be an HTML wrapper for the browser-embed
                        # plan. Empty; likely deleted once the browser-free plan lands.
```

Auth secrets — `service/credentials.json` and `service/token.json` — will
live in `service/` locally. Both are gitignored (or should be) before any
commits go up. Never commit either.

## Status

- ✅ Pi Zero 2 WH flashed with Raspberry Pi OS Lite (64-bit, Trixie — note: not Bookworm)
- ✅ Pi on Wi-Fi, SSH working from the Mac over the home network (`cal-pi.local`)
- ✅ OS updated, Python 3 confirmed available
- ✅ Repo scaffolded: `service/button_test.py`, `scripts/pi_bootstrap.sh`
- ✅ `pi_bootstrap.sh` run on the Pi (venv + gpiozero ready)
- ✅ Kiosk dev-mode scripts: `scripts/kiosk_bootstrap.sh`, `scripts/kiosk_start.sh`
- ✅ VNC dev environment working — Mac can connect to `vnc://cal-pi.local:5901` and see the Pi's virtual X session
- ❌ **Ruled out**: Chromium in kiosk mode — OOMs on 512 MB
- ❌ **Ruled out**: Epiphany as a browser kiosk — `--application-mode` is a dead end without the full web-app ceremony; runtime "page unresponsive" on the calendar embed under VNC (no GPU accel)
- ✅ **Decision made**: build a native Python renderer using the Google Calendar API instead of any browser
- ⏳ Next: Google Cloud project + OAuth `credentials.json` + first-time auth to produce `token.json`
- ⏳ Then: write `service/calendar_client.py`, `service/renderer.py`, `service/main.py`
- ⏳ Then: swap `kiosk_start.sh` xstartup to launch the Python app instead of Epiphany
- ⏳ In parallel: wire the three buttons on the breadboard and run `python service/button_test.py`
- ⏳ Blocked on **final** kiosk display (USB-C-only monitor mismatch — see Ongoing Issues); dev work is not blocked

## Ongoing Issues

Running log of hardware / setup blockers and gotchas. Newest at top.

### ✅ Solved: Chromium and Epiphany both fail as kiosk browsers on 512 MB

- **Symptom (Chromium)**: kernel OOM-killed the whole process mid-load, took the VNC session and (once) the SSH session down with it.
- **Symptom (Epiphany)**: `--application-mode` triggers `g_error()` unless the profile directory has the `org.gnome.Epiphany.WebApp_` prefix *and* a matching `.desktop` file (i.e., the full "Install as Web App" ceremony). Running plain-mode + `wmctrl` fullscreen works, but the calendar embed then hits "page unresponsive" because VNC provides no GPU acceleration for WebKit.
- **Fix**: pivoted off browser-embed entirely. See "Why not a browser?" above. Building a native Python + `tkinter`/`pygame` renderer that talks to the Calendar API directly.
- **Lesson**: browsers are heavy. On the Pi Zero 2 W, treat any browser-based kiosk plan as needing at least a Pi 3B (1 GB + GPU). For 512 MB, own the rendering pipeline.

### 📋 Trixie name gotchas (documented, not fatal)

Debian 13 (Trixie) renamed / relocated several things vs Bookworm:

- **Chromium binary**: `chromium`, not `chromium-browser`. Apt package is still called `chromium-browser` (or maybe transitional) but installs `/usr/bin/chromium`.
- **tigervnc config path**: `~/.config/tigervnc/` (XDG), not `~/.vnc/`. Both password and log files live there. Old scripts referencing `~/.vnc/` still mostly work due to backward-compat but are misleading.
- **Epiphany's private-mode flag**: `--incognito-mode`, not `--incognito` (Chrome's spelling). GNOME conventions.

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

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
- **App**: single Python program that fetches the calendar's secret ICS feed and renders it with `tkinter`. No browser involved.
- **Auth**: secret ICS URL from Google Calendar (Settings → Integrate calendar → "Secret address in iCal format"), stored in `service/config.json` (gitignored). No OAuth.
- **Button handling**: same Python program using `gpiozero` on GPIO 17/27/22. Buttons call `app.next_period()` / `app.prev_period()` / `app.toggle_view()` directly — no browser to drive. Keyboard bindings (`←` / `→` / `Space`) mirror the buttons so navigation is testable over VNC before the GPIO wiring exists.

## Why not a browser?

Original plan was Chromium in kiosk mode pointed at Google Calendar's embed. Ruled out empirically this session:

- **Chromium** OOMs on the Pi Zero 2 W's 512 MB before the calendar even finishes loading. Even with light flags.
- **Epiphany** (WebKit, ~1/3 Chromium's RAM) launches but hits "page unresponsive" on Google Calendar's embed JS because there's no GPU acceleration under VNC, and its `--application-mode` requires a full "installed web app" ceremony (matching profile prefix + `.desktop` file + icons) that fails hard without every piece.

The pragmatic pivot: skip the browser entirely, own the rendering pipeline in Python. Way lighter (~20 MB vs ~300 MB), cleaner button integration (direct function calls, no fake keyboard events), and total UI control.

## Why the ICS feed (for now) instead of the API?

Original plan was the full Google Calendar API. Started the OAuth setup (Google Cloud project, Calendar API enable, consent screen, Desktop OAuth client) and pivoted mid-flow. Reasons for backing out:

- **Setup pain is disproportionate** to what a personal kiosk needs. Consent screens, publishing status, testing-mode token expiry, downloading `credentials.json`, running a browser-based auth helper, `scp`'ing `token.json`. A lot of ceremony for "show me my events."
- **A poll-only ICS feed is fine here.** Google's ICS endpoint can lag some minutes-to-hours behind the source, but for a glanceable kiosk that's plenty fresh at a 60-second refetch cadence.
- **Fetch layer is ~30 lines either way.** If we outgrow ICS (e.g. want multi-color merged calendars written back), swapping in the API is a contained change, not a rewrite.

What we lose vs the API:
- Near-real-time push notifications via webhooks.
- Writing events back / multi-calendar with distinct colors from a single auth (multi-calendar over ICS is possible — just add another URL to the config).

## High-Level Flow

1. Pi boots straight into a virtual X session (dev: viewed over VNC from the Mac; final: driven to HDMI).
2. Openbox launches; our Python program launches fullscreen inside it.
3. Python program:
   - Loads calendar(s) from `service/config.json` — one or many ICS URLs, each with optional color, prefix, and email for organizer-based attribution.
   - Fetches each ICS payload, expands recurring events, attributes shared events to the ORGANIZER's calendar, dedupes across feeds, sorts chronologically.
   - Draws either the month grid (`MonthView`) or a Google-Calendar-style week view (`WeekView` — 7 day columns × hour rows, timed events as blocks sized by duration, all-day / multi-day events pinned to a header strip). Toggle button flips between them.
   - Watches GPIO 17/27/22 via `gpiozero`; `←` / `→` / `Space` keyboard bindings mirror those for VNC dev.
4. Button presses mutate view state (advance the anchor by one period — a month or a week depending on mode — or toggle the mode) → trigger a redraw. A ±3 month event cache means nav within the window is a pure redraw (no network).
5. Background timer re-fetches every 60 s so the display stays fresh without user action.

## Setup Outline

1. **Flash SD card** ✅ done
   - Raspberry Pi Imager → Raspberry Pi OS Lite (64-bit).
   - Pre-configured Wi-Fi, hostname (`cal-pi`), SSH, and user in Imager's advanced settings.
2. **First boot** ✅ done
   - SSH in, `sudo apt update && sudo apt full-upgrade`.
3. **Base Python + GPIO** ✅ done
   - `bash scripts/pi_bootstrap.sh` on the Pi. Installs `python3-venv python3-gpiozero python3-lgpio python3-tk`, creates `~/calendar_pi/.venv` with `--system-site-packages`, pip-installs `requests icalendar recurring-ical-events` into it.
   - **Re-run this** if you set the Pi up before the ICS pivot — it now installs the new deps.
4. **X + VNC dev environment** ✅ done
   - `bash scripts/kiosk_bootstrap.sh` on the Pi. Installs `xserver-xorg xinit x11-xserver-utils openbox tigervnc-standalone-server unclutter wmctrl` (and `epiphany-browser`, which is now unused but harmless).
   - `bash scripts/kiosk_start.sh` starts a virtual X session on display `:1`, port `5901`. See "Kiosk dev mode (VNC)" below. Now launches the Python app directly.
5. **Get the secret ICS URL(s)** ✅
   - In Google Calendar (web): sidebar → hover a calendar → ⋮ → **Settings and sharing** → **Integrate calendar** → copy **Secret address in iCal format**. Use the *copy* button next to the field — clicking the link itself triggers a download in most browsers.
   - Guard the URL like a password (anyone with it can read that calendar).
   - Save it in `service/config.json`. Multi-calendar form (preferred — colors + prefixes let you tell events apart at a glance):
     ```json
     {
       "calendars": [
         { "name": "Griffin", "prefix": "G", "email": "griffin@x.com",
           "url": "https://calendar.google.com/calendar/ical/.../basic.ics",
           "color": "#c8d4ff" },
         { "name": "Zoe",     "prefix": "Z", "email": "zoe@x.com",
           "url": "https://calendar.google.com/calendar/ical/.../basic.ics",
           "color": "#ffcccb" }
       ]
     }
     ```
     Single-calendar shorthand also works: `{ "ics_url": "https://..." }`.
   - `color` is optional; unspecified events render in the default light-blue.
   - `prefix` (optional) is prepended to each event title (`G: dentist`) so multi-person calendars are readable even if the colors get muddy on the Pi's monitor.
   - `email` (optional but recommended when two people share a calendar) drives **organizer-based attribution**: when both people are invited to the same event, both feeds carry a copy. The parser reads the ICS `ORGANIZER` property, matches it against the configured `email` fields, and attributes the event to the *inviter's* calendar — so a dinner Griffin invited Zoe to shows in Griffin's color/prefix regardless of which feed delivered it. Duplicate copies from the other feed are then deduped away by `(summary, start, end)`.
   - The file is gitignored.
6. **Python calendar app** ✅ built
   - `service/calendar_client.py` — reads `config.json`, fetches each configured ICS URL, expands recurring events, attributes shared events to the ORGANIZER's calendar, dedupes across feeds, sorts chronologically (all-day first, then timed by time-of-day). Supports per-calendar `require_attendee` filter for partner-style calendars.
   - `service/renderer.py` — `MonthView` (6-row grid, event titles per day) and `WeekView` (7 day columns × hour rows, timed events as blocks positioned by start time and sized by duration; all-day / multi-day events pinned to a header strip).
   - `service/main.py` — main loop. Holds view state (anchor + `month`/`week` mode). Rolling ±3 month event cache; nav within the window is a pure redraw (no network). 60 s background refetch via `root.after`. GPIO buttons (via `gpiozero`) and `←` / `→` / `Space` keyboard bindings both drive the same state mutations.
7. **Swap kiosk_start.sh** ✅ done
   - xstartup now execs the Python app directly. Dropped browser detection + `wmctrl` fullscreen dance (tkinter goes fullscreen natively).
8. **Physical display + autostart** ⏳
   - Wire the Pi to the chosen HDMI monitor via a mini-HDMI → HDMI cable.
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
bash scripts/kiosk_start.sh       # launches the Python calendar app fullscreen
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

Auth material — `service/config.json` (the secret ICS URL) — lives in
`service/` locally. Gitignored. Never commit.

`service/credentials.json` and `service/token.json` are also in `.gitignore`
in case the API route ever comes back on the menu; currently unused.

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
- ✅ **Decision made**: build a native Python renderer, fetching the calendar as an ICS feed
- ✅ Started but pivoted away from OAuth: Google Cloud project + Calendar API + Desktop OAuth client exist and are inert. Cheaper to just consume the secret ICS URL.
- ✅ Python app built: month view + week view (time-of-day grid), ±3 month event cache, multi-calendar with ORGANIZER-based attribution + cross-feed dedup + optional attendee filter, chronological sort, keyboard stand-ins for GPIO buttons
- ✅ `scripts/kiosk_start.sh` swapped to launch the Python app directly
- ✅ `service/config.json` populated with the calendar URL(s); running over VNC
- ⏳ Next: wire the three buttons on the breadboard and run `python service/button_test.py`, then swap keyboard stand-ins for real GPIO input
- ⏳ Final kiosk display: mini-HDMI → HDMI cable to the chosen monitor; add `~/.xinitrc` + `systemd` unit for boot-time autostart

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

### ✅ Solved: Portable monitor was USB-C-only (no HDMI input)

- **Problem**: The portable monitors on hand accepted USB-C only, both for power and video. Pi Zero 2 WH outputs mini HDMI.
- **Why it was tricky**: USB-C video is DisplayPort Alt Mode, not HDMI — a passive cable can't bridge the two. Active converters exist (~$40–60, e.g. j5create JVA02) but need their own USB power.
- **Fix**: sidestepped by using a different monitor with an HDMI input; wired via a mini-HDMI → HDMI cable directly to the Pi.

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

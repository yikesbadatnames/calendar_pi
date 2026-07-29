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

Each button wires one leg to its GPIO pin and the other leg to a ground pin (e.g. pin 9). Internal pull-ups are enabled in software (`pull_up=True`), so no external resistors are needed — the line idles high and the button pulls it to ground.

Run `pinout` on the Pi for an ASCII diagram of the actual header rather than counting pins off a web image. 11/13/15 are three consecutive pins on the same row, with ground at pin 9 two down from 11.

**Tactile-switch gotcha**: a 4-leg button is two pairs, and each pair is permanently bridged *inside* the switch. Wire GPIO and ground to two legs of the same pair and you have a dead short — the pin reads pressed forever. Straddle the breadboard's center channel so the bridged pairs land on opposite sides; then any left-leg/right-leg combination is across the switch.

Test wiring with no code at all: `pinctrl get 17` should read `hi` released and `lo` pressed. That isolates hardware from software before `button_test.py` enters the picture.

**Only one instance may hold the pins.** GPIO lines are exclusive to a single process. Stop the kiosk (`pkill -f service/main.py`) before running `button_test.py`, or the claims fail with `GPIO busy`.

## Software Stack

- **OS**: Raspberry Pi OS Lite (Trixie, 64-bit) — Debian 13-based, not Bookworm as originally planned.
- **Display**: minimal X session on the physical HDMI output (`:0`) + openbox as the window manager. TigerVNC on `:1` was the dev path and is now superseded — see "Boot & autostart chain".
- **App**: single Python program that fetches the calendar's secret ICS feed and renders it with `tkinter`. No browser involved.
- **Auth**: secret ICS URL from Google Calendar (Settings → Integrate calendar → "Secret address in iCal format"), stored in `service/config.json` (gitignored). No OAuth.
- **Button handling**: same Python program using `gpiozero` on GPIO 17/27/22. Buttons call `app.next_period()` / `app.prev_period()` / `app.toggle_view()` directly — no browser to drive. Keyboard bindings (`←` / `→` / `Space`) mirror the buttons so navigation is testable over VNC before the GPIO wiring exists.

## Boot & autostart chain

The kiosk starts itself at power-on with no keyboard and no login. Four links,
none of them systemd — worth knowing because a break anywhere shows the same
symptom (black screen), and none of these files are in this repo:

```
power on
  └─ systemd autologin on tty1        /etc/systemd/system/getty@tty1.service.d/autologin.conf
      └─ ~/.bash_profile              exec startx   (guarded to tty1 only)
          └─ ~/.xinitrc               exec > /tmp/kiosk.log 2>&1
              ├─ xset s off / -dpms / s noblank    (never blank the screen)
              ├─ xsetroot -solid black
              ├─ openbox-session &
              ├─ unclutter -idle 0.1 &
              └─ exec .venv/bin/python service/main.py
```

- **Autologin is what makes it headless.** `.bash_profile` runs on *console
  login*; without autologin the Pi sits at a login prompt forever. Enable with
  `sudo raspi-config nonint do_boot_behaviour B2`.
- **The tty1 guard matters.** `exec startx` must be wrapped in a
  `[ "$(tty)" = "/dev/tty1" ]` test, or every SSH login tries to start a second
  X session — which means two app instances fighting over the GPIO pins.
- **`exec` must come last in `.xinitrc`.** It replaces the shell, so anything
  written after it never runs. Background helpers (`openbox`, `unclutter`) go
  before, with `&`.
- **All app output goes to `/tmp/kiosk.log`** via the redirect on line 2. That's
  the first place to look for anything. It's `/tmp`, so it does not survive a
  reboot.

Originally planned as a systemd unit (see `systemd/`, still empty). The
autologin + `startx` chain got there first and works fine; systemd would mainly
buy ordering guarantees like `network-online.target` (see the blank-first-minute
note under Ongoing Issues).

### Viewing the kiosk remotely

`scripts/kiosk_start.sh` is **superseded**. TigerVNC's `vncserver` creates a
*separate* virtual display — it doesn't show you `:0`, it starts a second copy
of the app, and the two then fight over the GPIO pins. To see the real kiosk,
mirror the existing display instead:

```
sudo apt install x11vnc
x11vnc -display :0 -localhost no -forever
```

Same `vnc://cal-pi.local:5900` from the Mac, one app, one owner of the pins.

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
5. Background timer re-fetches every 5 min (60 s after a failed fetch) so the display stays fresh without user action. A second, network-free 10 s timer watches the system clock and re-anchors the view if the date moves under it — see "Stuck on a boot-time date".

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
   - `service/main.py` — main loop. Holds view state (anchor + `month`/`week` mode). Rolling ±3 month event cache; nav within the window is a pure redraw (no network). 5 min background refetch via `root.after`, backing off to a 60 s retry while fetches are failing. GPIO buttons (via `gpiozero`) and `←` / `→` / `Space` keyboard bindings both drive the same state mutations.
7. **Swap kiosk_start.sh** ✅ done
   - xstartup now execs the Python app directly. Dropped browser detection + `wmctrl` fullscreen dance (tkinter goes fullscreen natively).
8. **Physical display + autostart** ✅ done
   - Pi wired to the HDMI monitor via mini-HDMI → HDMI.
   - Boots unattended into the kiosk via autologin → `.bash_profile` → `startx` → `.xinitrc`. No systemd unit in the end — see "Boot & autostart chain".
9. **Wiring the buttons** ⏳
   - Power the Pi down first (`sudo shutdown -h now`, wait for the LED). Never move jumpers on a live header.
   - Breadboard first, **one button at a time** — one variable to debug instead of three.
   - Verify with `pinctrl get 17` (no code), then `service/button_test.py` (kiosk stopped).
   - Move to perfboard once the layout's confirmed.

## Kiosk dev mode (VNC) — SUPERSEDED

Kept for reference. This was how the kiosk ran before the HDMI monitor
existed. **Do not run this alongside the HDMI kiosk** — it starts a second app
instance on a second display, and they fight over the GPIO pins. Use
`x11vnc -display :0` instead (see "Viewing the kiosk remotely").

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
├── systemd/            # EMPTY — autostart ended up as autologin + .xinitrc instead;
                        # see "Boot & autostart chain". Kept in case boot ordering
                        # (network-online.target) ever justifies a real unit.
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
- ✅ `service/config.json` populated with the calendar URL(s)
- ✅ **Physical HDMI display working**; kiosk autostarts on boot with no keyboard (autologin → `.bash_profile` → `startx` → `.xinitrc`)
- ✅ Kiosk survives being unplugged and moved to another room — comes back on its own
- ✅ `main.py` hardened: single-instance guard (abstract unix socket) + per-button GPIO fallback so one busy pin can't take down the calendar
- ⏳ Next: wire the three buttons on the breadboard and run `python service/button_test.py`, then swap keyboard stand-ins for real GPIO input

## Ongoing Issues

Running log of hardware / setup blockers and gotchas. Newest at top.

### 🔴 OPEN: Pi drops off the network while still running — calendar renders but stays empty

**Status**: unresolved as of 2026-07-19. Two theories tested and disproved. Instrumentation not yet in place.

- **Symptom**: Pi becomes unreachable from the Mac — `ping cal-pi.local` fails to resolve, and pinging the last known IP directly also gets 100% loss. Meanwhile **the kiosk keeps rendering on the HDMI monitor**, with the calendar grid drawn but no events in it. A power cycle restores network access for a while (long enough to SSH in), then it drops again.
- **Trigger (suspected, unconfirmed)**: first noticed after the Pi was unplugged without a clean shutdown. That framing turned out to be a red herring — see "Lessons" below.

**Ruled out:**

- **SD card corruption.** Card read cleanly in a USB reader: `fsck_msdos -n` on `bootfs` exited 0, every boot file present and correctly sized (`bootcode.bin`, `start.elf`, `kernel8.img`, `bcm2710-rpi-zero-2-w.dtb`), ext4 root partition intact at full 30.75 GB, cloud-init trio (`user-data` / `meta-data` / `network-config`) all present with valid Wi-Fi config.
- **Power supply.** Ruled out by the display staying lit and the app continuing to render. A browning-out Pi doesn't draw frames. Same outlet, same cable as when it worked.
- **Wi-Fi power management.** `iw wlan0 get power_save` → `off`. Already disabled by default; was never the cause. A `/etc/NetworkManager/conf.d/wifi-powersave-off.conf` drop-in (`wifi.powersave = 2`) was added anyway — harmless no-op that pins the setting.
- **Weak signal.** `iw wlan0 link` → **−43 dBm**, 72.2 Mbit/s both directions, 2.4 GHz ch 1 (2412 MHz), BSSID `64:d9:c2:e2:30:c6`. That's a strong, healthy link.

**Not yet checked — this is where to resume:**

- **Layer 3.** Everything verified so far is layer 2 (radio association). `ip addr show wlan0`, `ip route`, and `ping -c 3 1.1.1.1` were never run. Leading hypothesis: the Pi keeps a good radio link but loses its **DHCP lease** or default route, leaving it associated and unreachable. That would also explain the empty calendar — no route means no ICS fetch.
- **Possible second subnet.** An ARP sweep from the Mac (`192.168.4.115`) showed a stray `192.168.7.255` broadcast on the `192.168.4.x` network. If the Pi ever lands on `192.168.7.x` it's unreachable from the Mac regardless of Wi-Fi health. Possibly an Eero guest network. Loose thread, not a conclusion.
- **Eero mesh roaming** between nodes (`64:d9:c2:e2:30:xx` and `64:d9:c2:d4:86:d2` both seen).

**Blocker on diagnosis — no persistent journal:**

`journalctl -b -1` fails with *"Specifying boot ID or boot offset has no effect, no persistent journal was found."* Pi OS Lite logs to `/run/log/journal` (tmpfs), so **every reboot destroys the evidence from the session that failed**. Since recovering the Pi requires a reboot, the logs are always gone by the time it's reachable. Fix this first next session:

- `sudo mkdir -p /var/log/journal`
- `sudo systemd-tmpfiles --create --prefix /var/log/journal`
- `sudo systemctl restart systemd-journald`
- Cap it — journald on SD is real flash wear: set `SystemMaxUse=50M` in `/etc/systemd/journald.conf`.

Then add a logger recording IP, route, and reachability every 30 s to a file on disk, so the next failure records itself instead of being reconstructed afterward.

**Lessons:**

- **The green LED was read as a boot failure and it wasn't.** Solid green sent the whole investigation toward SD corruption and power — matching the two prior entries below that *did* have those causes. What settled it was the display: the kiosk was rendering the calendar the entire time, which proves the Pi booted, X started, and the app ran. **Check whether the screen is drawing before diagnosing anything as a boot failure.** Worth resolving what solid-green actually means on this board, given the contradiction with the entries below.
- **`S.M.A.R.T. status: Verified` on an SD card means nothing.** That status comes from the USB reader's bridge chip (Genesys Logic), not the card — SD cards don't implement SMART. The clean `fsck` was real evidence; the SMART line was not.
- **Layer 2 health says nothing about layer 3.** "Associated at −43 dBm" and "completely unreachable" are entirely compatible. `iw` answers a different question than `ip addr`.
- **An intermittent fault needs instrumentation, not theories.** Every hypothesis here was tested after the fact, against evidence that had already been wiped by the reboot needed to regain access.
- Related: **"Blank calendar for the first minute after boot"** below is the benign version of this same symptom — kiosk renders before Wi-Fi associates. This looks like that failure mode stuck permanently.

### ✅ Solved: `lgpio.error: 'GPIO busy'` — two kiosk instances fighting over the pins

- **Symptom**: HDMI kiosk crash-looped at startup. Calendar fetch succeeded (`141 raw → 75 after dedupe`), then `Button(PIN_TOGGLE, ...)` died with `lgpio.error: 'GPIO busy'`, taking the whole app down. `/tmp/kiosk.log` kept truncating as the session relaunched.
- **Cause**: a `kiosk_start.sh` VNC instance from an earlier dev session was still alive on display `:1` (up 90 minutes, parented to `vncserver`). GPIO lines are **exclusive to one process** — it owned 17/27/22, so the HDMI instance on `:0` could never claim them.
- **Fix**: `vncserver -kill :1`. Then retired the VNC path in favor of `x11vnc -display :0`, which mirrors the real display instead of creating a second one.
- **Debugging note**: `pgrep -af 'service/main.py'` typed across two lines silently searches for a pattern *containing a newline* and matches nothing — it looked like no process was running and sent the investigation toward a phantom device-tree overlay. Watch for the `>` continuation prompt.
- **Useful commands**: `pgrep -af main.py` (who's running), `pinctrl get 17` (line state), `sudo cat /sys/kernel/debug/gpio` (who *owns* a line — `pinctrl` shows state, not owner), `ps -o pid,ppid,lstart,etime,cmd -p <pid>` + `tr '\0' '\n' < /proc/<pid>/environ | grep DISPLAY` (which session a process belongs to).
- **Lesson**: an exclusive kernel resource makes "is exactly one copy running?" a correctness question, not a tidiness one. `main.py` now takes a single-instance lock at startup and refuses to start twice, and wires buttons one at a time so a busy pin costs one button instead of the whole display.

### 📋 Blank calendar for the first minute after boot

- **Symptom**: after a cold boot the grid renders but is empty; populates ~60 s later with no intervention.
- **Cause**: the kiosk launches before Wi-Fi finishes associating, so the first synchronous ICS fetch fails. `_refetch_window_and_redraw` catches it, logs to stderr, and draws whatever it has (nothing). The refresh timer then succeeds.
- **Not a bug** — this is the error handling working. Cosmetic only.
- **Now**: a failed fetch schedules the next attempt at `RETRY_MS` (60 s) instead of the full `REFRESH_MS` (5 min), so the blank window doesn't get longer as the normal cadence slows down. Moving autostart to a systemd unit ordered `After=network-online.target` would remove it entirely.

### ✅ Solved: display blanked at random on a good network

- **Symptom**: a fully populated calendar would occasionally drop to an empty grid, then repopulate a minute later. Looked like the render "losing" its data.
- **Cause**: `get_events()` caught each feed's exception internally and returned whatever it had collected — possibly `[]`. So `main.py`'s `except` never fired, and `self.events` was overwritten with an empty list while the UI reported success. One dropped request per minute over Pi wifi made this frequent.
- **Fix**: `get_events()` now raises `CalendarFetchError` if *any* feed failed, carrying the successful feeds' events as `.partial`. `_refetch_window_and_redraw` keeps the data already on screen and shows the ⚠ badge; only a cold start (nothing cached yet) falls back to the partial set.
- **Lesson**: "handled the error" and "the caller can tell something went wrong" are different things. Swallowing a failure and returning a plausible-looking empty result is worse than raising — the caller silently believes it.

### ✅ Solved: stuck on a boot-time date

- **Symptom**: kiosk displaying a month years in the past (2018) and never correcting itself.
- **Cause**: the Pi Zero has no battery-backed RTC. At boot the clock is whatever `fake-hwclock` last saved, and it only jumps to real time once wifi is up and NTP syncs — typically *after* the app has started. `App.__init__` read `date.today()` exactly once, so the anchor froze on the bogus date permanently. (The same bug meant the view never followed midnight rollover either.)
- **Fix**: `_clock_tick` re-reads the clock every 10 s. If the date moved and the user hasn't navigated away, it re-anchors to the new date and lets `_navigate_to` refetch when the corrected window falls outside the cached one. Purely local — no network cost.
- **Check the clock itself** if this recurs: `timedatectl` on the Pi should report `System clock synchronized: yes` and `NTP service: active`. The app follows the clock; it can't fix a clock that never syncs.

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

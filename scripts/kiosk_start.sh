#!/usr/bin/env bash
# Start (or restart) the VNC-hosted kiosk on display :1.
# Usage:
#   bash scripts/kiosk_start.sh                    # defaults to US Holidays public calendar
#   bash scripts/kiosk_start.sh 'https://...'      # pass any URL

set -euo pipefail

# US Holidays is a well-known public Google Calendar — a reliable "does the
# embed pipeline work" placeholder. Swap for the real calendar URL later.
DEFAULT_URL='https://calendar.google.com/calendar/embed?src=en.usa%23holiday%40group.v.calendar.google.com&ctz=America%2FLos_Angeles&mode=MONTH'
KIOSK_URL="${1:-$DEFAULT_URL}"

# Prefer Epiphany (WebKit, ~1/3 Chromium's RAM) since the Pi Zero 2 W only has
# 512 MB. Fall back to Chromium if Epiphany isn't installed (e.g. running on a
# beefier Pi where Chromium fits).
BROWSER_BIN=""
BROWSER_ARGS=()
if command -v epiphany-browser >/dev/null; then
  BROWSER_BIN=$(command -v epiphany-browser)
  # Epiphany has no --kiosk flag. --application-mode looks promising but
  # requires a full "web app" ceremony (matching profile dir prefix + a
  # .desktop file + icons); missing pieces cause hard g_error() aborts.
  # Simpler and more reliable: run --incognito-mode (no profile), then use
  # wmctrl after launch to send the window fullscreen. See xstartup below.
  # NB: it's --incognito-mode, not --incognito (that's Chrome's name).
  BROWSER_ARGS=(--incognito-mode)
elif command -v chromium-browser >/dev/null; then
  BROWSER_BIN=$(command -v chromium-browser)
  BROWSER_ARGS=(--kiosk --noerrdialogs --disable-infobars --incognito=false --no-first-run)
elif command -v chromium >/dev/null; then
  BROWSER_BIN=$(command -v chromium)
  BROWSER_ARGS=(--kiosk --noerrdialogs --disable-infobars --incognito=false --no-first-run)
else
  echo "error: no supported browser found (epiphany-browser, chromium-browser, or chromium)" >&2
  echo "did kiosk_bootstrap.sh finish successfully?" >&2
  exit 1
fi
echo "==> browser: $BROWSER_BIN"

echo "==> stopping any existing vncserver on :1"
vncserver -kill :1 >/dev/null 2>&1 || true

# Newer tigervnc reads xstartup from ~/.config/tigervnc/; older reads ~/.vnc/.
# Write both so we're covered either way.
echo "==> writing xstartup pointed at:"
echo "    $KIOSK_URL"
mkdir -p "$HOME/.vnc" "$HOME/.config/tigervnc"

# Build the browser command line, quoting each arg safely for the shell script.
BROWSER_CMD="$BROWSER_BIN"
for arg in "${BROWSER_ARGS[@]}"; do
  BROWSER_CMD="$BROWSER_CMD '$arg'"
done
BROWSER_CMD="$BROWSER_CMD '$KIOSK_URL'"

XSTARTUP_BODY=$(cat <<EOF
#!/bin/sh
xsetroot -solid black &
openbox-session &
unclutter -idle 0.1 &
# Give the browser a few seconds to open its window, then fullscreen it.
# Loop for a while in case initial GTK/WebKit startup is slow on the Pi Zero.
(
  for i in \$(seq 1 20); do
    sleep 1
    if wmctrl -l 2>/dev/null | grep -qi epiphany; then
      wmctrl -r Epiphany -b add,fullscreen
      break
    fi
  done
) &
exec $BROWSER_CMD
EOF
)
printf '%s\n' "$XSTARTUP_BODY" > "$HOME/.vnc/xstartup"
printf '%s\n' "$XSTARTUP_BODY" > "$HOME/.config/tigervnc/xstartup"
chmod +x "$HOME/.vnc/xstartup" "$HOME/.config/tigervnc/xstartup"

echo "==> starting vncserver :1 (1280x800)"
vncserver :1 -geometry 1280x800 -depth 24 -localhost no

cat <<EOF

kiosk is up on display :1.

connect from the Mac:
  Finder -> Go -> Connect to Server -> vnc://cal-pi.local:5901
  (enter the VNC password from bootstrap)

logs: ~/.config/tigervnc/*.log (or ~/.vnc/*.log on older Pi OS)
stop: vncserver -kill :1
EOF

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

# Older Raspberry Pi OS (Bookworm) ships /usr/bin/chromium-browser; newer
# (Trixie) ships /usr/bin/chromium. Detect whichever exists.
CHROMIUM=$(command -v chromium-browser || command -v chromium || true)
if [ -z "$CHROMIUM" ]; then
  echo "error: neither 'chromium-browser' nor 'chromium' found in PATH" >&2
  echo "did kiosk_bootstrap.sh finish successfully?" >&2
  exit 1
fi
echo "==> chromium binary: $CHROMIUM"

echo "==> stopping any existing vncserver on :1"
vncserver -kill :1 >/dev/null 2>&1 || true

# Newer tigervnc reads xstartup from ~/.config/tigervnc/; older reads ~/.vnc/.
# Write both so we're covered either way.
echo "==> writing xstartup pointed at:"
echo "    $KIOSK_URL"
mkdir -p "$HOME/.vnc" "$HOME/.config/tigervnc"
XSTARTUP_BODY=$(cat <<EOF
#!/bin/sh
xsetroot -solid black &
openbox-session &
unclutter -idle 0.1 &
exec $CHROMIUM \\
  --kiosk \\
  --noerrdialogs \\
  --disable-infobars \\
  --incognito=false \\
  --no-first-run \\
  "$KIOSK_URL"
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

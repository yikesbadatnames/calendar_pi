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

echo "==> stopping any existing vncserver on :1"
vncserver -kill :1 >/dev/null 2>&1 || true

echo "==> writing ~/.vnc/xstartup pointed at:"
echo "    $KIOSK_URL"
mkdir -p "$HOME/.vnc"
cat > "$HOME/.vnc/xstartup" <<EOF
#!/bin/sh
xsetroot -solid black &
openbox-session &
unclutter -idle 0.1 &
exec chromium-browser \\
  --kiosk \\
  --noerrdialogs \\
  --disable-infobars \\
  --incognito=false \\
  --no-first-run \\
  "$KIOSK_URL"
EOF
chmod +x "$HOME/.vnc/xstartup"

echo "==> starting vncserver :1 (1280x800)"
vncserver :1 -geometry 1280x800 -depth 24 -localhost no

cat <<EOF

kiosk is up on display :1.

connect from the Mac:
  Finder -> Go -> Connect to Server -> vnc://cal-pi.local:5901
  (enter the VNC password from bootstrap)

logs: ~/.vnc/*.log
stop: vncserver -kill :1
EOF

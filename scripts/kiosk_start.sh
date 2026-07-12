#!/usr/bin/env bash
# Start (or restart) the VNC-hosted kiosk on display :1.
# Launches the calendar_pi Python app fullscreen inside a minimal X session.
# Usage:
#   bash scripts/kiosk_start.sh

set -euo pipefail

if [ ! -f README.md ] || [ ! -d service ]; then
  echo "error: run this from the repo root (expected README.md and service/ here)" >&2
  exit 1
fi

APP_DIR="$(pwd)"
VENV_PY="$APP_DIR/.venv/bin/python"
APP_ENTRY="$APP_DIR/service/main.py"

if [ ! -x "$VENV_PY" ]; then
  echo "error: $VENV_PY not found. Did pi_bootstrap.sh finish?" >&2
  exit 1
fi
if [ ! -f "$APP_ENTRY" ]; then
  echo "error: $APP_ENTRY not found." >&2
  exit 1
fi

echo "==> stopping any existing vncserver on :1"
vncserver -kill :1 >/dev/null 2>&1 || true

# Newer tigervnc reads xstartup from ~/.config/tigervnc/; older reads ~/.vnc/.
# Write both so we're covered either way.
echo "==> writing xstartup"
mkdir -p "$HOME/.vnc" "$HOME/.config/tigervnc"

XSTARTUP_BODY=$(cat <<EOF
#!/bin/sh
xsetroot -solid black &
openbox-session &
unclutter -idle 0.1 &
exec "$VENV_PY" "$APP_ENTRY"
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
     app stdout/stderr goes to the same log.
stop: vncserver -kill :1
EOF

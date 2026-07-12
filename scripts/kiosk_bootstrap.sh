#!/usr/bin/env bash
# Install the display stack (X + openbox + Chromium + TigerVNC + unclutter)
# so the kiosk can be exercised over VNC from the Mac before we plug in a
# physical monitor. Idempotent — safe to re-run.
#
# Run from the repo root: bash scripts/kiosk_bootstrap.sh

set -euo pipefail

if [ ! -f README.md ] || [ ! -d service ]; then
  echo "error: run this from the repo root (expected README.md and service/ here)" >&2
  exit 1
fi

echo "==> apt update"
sudo apt update

echo "==> installing display + VNC + browser stack"
# openbox: minimal window manager (Chromium --kiosk still needs one).
# tigervnc-standalone-server: runs a virtual X display (not RealVNC's shared-desktop model).
# unclutter: hides the mouse cursor when idle.
sudo apt install -y \
  xserver-xorg \
  xinit \
  x11-xserver-utils \
  openbox \
  chromium-browser \
  tigervnc-standalone-server \
  unclutter

if [ ! -f "$HOME/.vnc/passwd" ]; then
  echo "==> no VNC password set yet — running vncpasswd"
  echo "    (pick something you'll remember; view-only password is optional)"
  mkdir -p "$HOME/.vnc"
  vncpasswd
else
  echo "==> VNC password already exists at ~/.vnc/passwd, skipping"
fi

cat <<EOF

done. next step:
  bash scripts/kiosk_start.sh

that starts the virtual X session + Chromium in kiosk mode. connect from the
Mac with Finder -> Go -> Connect to Server -> vnc://cal-pi.local:5901
EOF

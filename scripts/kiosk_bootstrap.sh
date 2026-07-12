#!/usr/bin/env bash
# Install the display stack (X + openbox + Epiphany + TigerVNC + unclutter)
# so the kiosk can be exercised over VNC from the Mac before we plug in a
# physical monitor. Idempotent — safe to re-run.
#
# We use Epiphany (GNOME Web) instead of Chromium because the Pi Zero 2 W
# only has 512 MB of RAM — Chromium OOMs before it even finishes loading a
# tab. Epiphany is WebKit-based and much lighter.
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
# openbox: minimal window manager (browsers in kiosk-ish modes still want one).
# tigervnc-standalone-server: runs a virtual X display (not RealVNC's shared-desktop model).
# unclutter: hides the mouse cursor when idle.
# epiphany-browser: WebKit-based, ~1/3 the memory of Chromium.
# wmctrl: sends fullscreen command to the browser window after it opens
#        (Epiphany has no --kiosk equivalent, so we drive it externally).
sudo apt install -y \
  xserver-xorg \
  xinit \
  x11-xserver-utils \
  openbox \
  epiphany-browser \
  tigervnc-standalone-server \
  unclutter \
  wmctrl

# Old tigervnc writes to ~/.vnc/passwd; newer (Trixie) writes to
# ~/.config/tigervnc/passwd (XDG). Accept either.
if [ ! -f "$HOME/.vnc/passwd" ] && [ ! -f "$HOME/.config/tigervnc/passwd" ]; then
  echo "==> no VNC password set yet — running vncpasswd"
  echo "    (pick something you'll remember; view-only password is optional)"
  mkdir -p "$HOME/.vnc" "$HOME/.config/tigervnc"
  vncpasswd
else
  echo "==> VNC password already set, skipping"
fi

cat <<EOF

done. next step:
  bash scripts/kiosk_start.sh

that starts the virtual X session + Epiphany in application mode. connect
from the Mac with Finder -> Go -> Connect to Server -> vnc://cal-pi.local:5901
EOF

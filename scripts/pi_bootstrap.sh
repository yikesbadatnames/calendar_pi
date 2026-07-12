#!/usr/bin/env bash
# Bootstrap the Pi for running the button test. Idempotent — safe to re-run.
# Run from the repo root:  bash scripts/pi_bootstrap.sh

set -euo pipefail

if [ ! -f README.md ] || [ ! -d service ]; then
  echo "error: run this from the repo root (expected README.md and service/ here)" >&2
  exit 1
fi

echo "==> apt update"
sudo apt update

echo "==> installing python + gpiozero + lgpio via apt"
# On Bookworm, gpiozero uses lgpio as its default pin factory.
# Installing via apt sidesteps PEP 668 (externally-managed-environment) headaches.
sudo apt install -y python3-venv python3-gpiozero python3-lgpio

VENV_DIR=".venv"
if [ ! -d "$VENV_DIR" ]; then
  echo "==> creating venv at $VENV_DIR (with system site-packages)"
  # --system-site-packages so the venv can import the apt-installed gpiozero/lgpio
  # without needing pip + C build tools.
  python3 -m venv --system-site-packages "$VENV_DIR"
else
  echo "==> venv already exists at $VENV_DIR, skipping create"
fi

cat <<EOF

done. next step:
  source .venv/bin/activate
  python service/button_test.py

wire buttons per README (GPIO 17 = toggle, 27 = prev, 22 = next; other leg to any GND pin),
then press each and watch for the labeled lines.
EOF

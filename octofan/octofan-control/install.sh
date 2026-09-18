#!/bin/bash
# Install octofan-control on an Octominer X12 rig.
# Usage: sudo ./install.sh   (from the octofan/octofan-control directory)
set -euo pipefail
cd "$(dirname "$0")"

[ "$(id -u)" -eq 0 ] || { echo "run as root"; exit 1; }
command -v python3 >/dev/null || { echo "python3 required"; exit 1; }
[ -x /usr/local/bin/fan_controller_cli ] || {
  echo "fan_controller_cli not found at /usr/local/bin/fan_controller_cli"
  echo "install it first (see ../README.md)"; exit 1; }

python3 -m py_compile octofan-control

install -m 0755 octofan-control /usr/local/sbin/octofan-control
if [ ! -f /etc/octofan-control.conf ]; then
  install -m 0644 octofan-control.conf /etc/octofan-control.conf
  echo "installed /etc/octofan-control.conf (EDIT fan/slot/card mapping!)"
else
  echo "kept existing /etc/octofan-control.conf"
fi
install -m 0644 octofan-control.service /etc/systemd/system/octofan-control.service
systemctl daemon-reload
systemctl enable octofan-control.service

echo
echo "Next steps:"
echo " 1. verify wiring per FAN-MAPPING.md (blip pass), fix [fans] if needed"
echo " 2. set [cards] slots for this rig"
echo " 3. sudo systemctl restart octofan-control && journalctl -u octofan-control -n 5"

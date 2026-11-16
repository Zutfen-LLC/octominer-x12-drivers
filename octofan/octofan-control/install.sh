#!/bin/bash
# Install without restarting the controller. DESTDIR stages an offline image.
# Usage: sudo bash install.sh  OR  DESTDIR=/absolute/image bash install.sh
set -euo pipefail
cd "$(dirname "$0")"
command -v python3 >/dev/null || { echo "python3 required"; exit 1; }

dest="${DESTDIR:-}"
if [ -n "$dest" ]; then
  case "$dest" in /*) ;; *) echo "DESTDIR must be absolute"; exit 1;; esac
  dest=$(python3 -c 'import os,sys; print(os.path.realpath(sys.argv[1]))' "$dest")
  [ "$dest" != / ] || { echo "DESTDIR must not resolve to the host root"; exit 1; }
else
  [ "$(id -u)" -eq 0 ] || { echo "run as root"; exit 1; }
  [ -x /usr/local/bin/fan_controller_cli ] || {
    echo "fan_controller_cli not found at /usr/local/bin/fan_controller_cli"
    echo "install it first (see ../README.md)"; exit 1; }
fi

# Validate every source payload before installing anything. Do not produce
# bytecode, touch the controller, load the host config or restart its service.
python3 -B - <<'PY'
from pathlib import Path
import runpy
import octofan_topology
for name in ("octofan-control", "octofan_topology.py"):
    compile(Path(name).read_bytes(), name, "exec")
ns = runpy.run_path("octofan-control", run_name="installer_preflight")
ns["load_config"]("octofan-control.conf")
octofan_topology.load_profile("profiles/minerdude-12xtreme.json")
PY

# Reject image paths redirected outside the staging root before any copy.
if [ -n "$dest" ]; then
  python3 -B - "$dest" <<'PY'
from pathlib import Path
import sys
root = Path(sys.argv[1]).resolve()
for relative in (
    "usr/local/sbin", "usr/local/sbin/octofan-control",
    "usr/local/sbin/octofan_topology.py",
    "usr/local/share/octofan-control/profiles/minerdude-12xtreme.json",
    "etc/octofan-control.conf", "etc/systemd/system/octofan-control.service",
):
    resolved = (root / relative).resolve()
    if not resolved.is_relative_to(root):
        raise SystemExit("staging destination escapes DESTDIR: " + relative)
PY
fi

install -d -m 0755 "$dest/usr/local/sbin" \
  "$dest/usr/local/share/octofan-control/profiles" "$dest/etc/systemd/system"
install -m 0755 octofan-control "$dest/usr/local/sbin/octofan-control"
install -m 0644 octofan_topology.py "$dest/usr/local/sbin/octofan_topology.py"
install -m 0644 profiles/minerdude-12xtreme.json \
  "$dest/usr/local/share/octofan-control/profiles/minerdude-12xtreme.json"
if [ ! -e "$dest/etc/octofan-control.conf" ] && [ ! -L "$dest/etc/octofan-control.conf" ]; then
  install -m 0644 octofan-control.conf "$dest/etc/octofan-control.conf"
  echo "installed manual example config (EDIT fan/slot/card/sensor mapping!)"
else
  echo "kept existing $dest/etc/octofan-control.conf"
fi
install -m 0644 octofan-control.service "$dest/etc/systemd/system/octofan-control.service"
if [ -n "$dest" ]; then
  echo "staged offline image in $dest; no systemctl or controller calls made"
else
  systemctl daemon-reload
  systemctl enable octofan-control.service
  echo "installed without restarting octofan-control"
  echo "verify wiring, back up config, and inspect an explicit auto candidate per README.md"
  echo "restart octofan-control only after commissioning and review"
fi

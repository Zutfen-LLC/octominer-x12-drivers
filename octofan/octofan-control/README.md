# octofan-control

Case-fan control through the Octominer `fan_controller_cli`, with an explicit
manual placement mode and an opt-in PCIe-discovered, per-slot temperature mode.
Python standard library only. No GPU fan, voltage, PCI register, or EEPROM writes
are made by this daemon; its actuator is the case-fan controller's runtime PWM.

## Physical conventions and scope

On the calibrated MINERDUDE 12XTREME:

- Fans count left to right: FAN1..FAN5, channels 8,6,4,2,0.
- Slots count right to left: MB1, MB2, DB1..DB10.
- Wiring and coverage must be verified per chassis; see `FAN-MAPPING.md`.
- The shipped profile records all twelve independently operator-labelled slots.
  It is single-card discovery evidence, NOT qualification of simultaneous card
  populations, every BIOS/PCB revision, another chassis, or loaded thermals.
- DMI/model identity alone never enables automatic control. Explicitly select a
  profile, compare its wiring with the rig, and inspect the actual topology.

## Modes and temperature ownership

Manual mode remains the default. `[cards] slots` selects occupied zones, all
using the legacy global maximum of configured GPU temperatures.

Automatic mode discovers each display endpoint under its motherboard PCIe root
port, including devices behind card-internal switches. It matches root vendor,
root device and the PCIe LinkCap hardware port number—not mutable BDF/bus numbers
or `hwmonN` indices. Two dies/endpoints behind the same slot are grouped. Each
endpoint must have its own attributable GPU hwmon temperature; a healthy die
cannot hide another die with no readable temperature.

Each slot uses its own hottest sensor/die and independent curve hysteresis. A fan
covering several slots gets the maximum of their requests, never the average.
Bridge-slot coverage retains the configured 0.75 scaling and minimum PWM floor.
The background curve still uses the globally hottest GPU as an intentional
high-temperature chassis ventilation policy, not as the occupied zones' input.

The exact onboard Intel `8086:1902` is excluded only when its actual topology
proves it is a direct host-root integrated display. Other display endpoints are
not silently ignored. Proprietary NVIDIA tools are not required when usable
`nouveau`/`nvidia` hwmon temperatures exist; a GPU without an attributable usable
hwmon is a fault, not an invented reading or an empty slot.

## Preflight before automatic operation

First preserve the existing installed source, configuration, unit and any helper
or selected profile needed for rollback. Installing the new files must be an
explicit maintenance action; this README is not permission to update a live rig.

Run `sudo bash install.sh` from this directory when installation is authorized.
It validates the source/example/profile, preserves an existing per-host config,
installs the helper and profile, and enables the unit without restarting it.
An installation is NOT a successful live commissioning check.

Prepare a separate candidate config rather than changing the running service's
configuration during evaluation:

```sh
sudo cp -p /etc/octofan-control.conf /root/octofan-control.before-auto.conf
sudo cp -p /etc/octofan-control.conf /root/octofan-control.candidate.conf
```

In that candidate, retain the rig's verified `[fans]`, `[slots]`, floor and safe
curves. Change/add:

```ini
[controller]
mode = auto

[automatic]
profile = /usr/local/share/octofan-control/profiles/minerdude-12xtreme.json

[sensors]
hwmon_names = amdgpu, nouveau, nvidia
sensors = temp1_input, temp2_input, temp3_input
```

The snippet is NOT a complete standalone config; merge these options into the
existing sections rather than adding duplicate INI sections. `[cards] slots`
is used by manual mode, not by automatic physical placement discovery.

```sh
sudo /usr/local/sbin/octofan-control --inspect /root/octofan-control.candidate.conf
```

Inspection is read-only: it never invokes the fan CLI, including for status,
and never touches systemd, EEPROM, config or GPU settings. It emits JSON showing
profile, endpoints/slots, per-endpoint temperatures, exclusions, targets and
faults. Healthy inspection exits zero; runtime discovery faults exit nonzero
and report an all-fans-full-speed plan. Configuration validation errors are
refused before actuation. Inspection is a stateless snapshot, not proof of
controller reachability, loaded cooling or recovery transitions.

A synthetic `[automatic] sysfs_root` is permitted for read-only inspection and
tests only. Ordinary daemon invocation must use real `/sys`.

Only after reviewing the inspection result and the rig-specific thermal policy,
publish the candidate to `/etc/octofan-control.conf`, explicitly restart
`octofan-control`, and verify its journal, temperatures, target/readback RPMs and
service health. Prove each physical population and load case before calling the
rig thermally qualified. To roll back, restore the saved source/helper/profile
and manual configuration as applicable, explicitly restart, and read back actual
state; a restart exit code alone is not verification.

## Failure policy

- Unknown, ambiguous or unreadable placement, duplicate physical root identities,
  and disappearance/change of an enrolled endpoint require full-speed cooling.
- Each enrolled endpoint is checked independently; malformed or out-of-range
  present temperatures are faults. Configured optional sensor files may be
  absent if the endpoint still has another usable configured temperature.
- Sensor faults after healthy operation may use a short grace period with no
  downward fan changes; healthy hotter zones can still ramp up. Three consecutive
  sensor-fault polls require every physical fan at PWM255, reasserted every poll.
- Startup without complete healthy temperatures is full-speed immediately.
  Recovery from fail-safe requires two complete healthy snapshots and resets
  hysteresis; it is not triggered by one unrelated healthy GPU.
- A failed fan write does not skip the remaining channels. Full-speed retries
  remain latched until control is confirmed; normal curves cannot immediately
  clear an unconfirmed actuator fault.
- Lifetime enrollment is not a cryptographic hardware identity. Identical cards
  at identical reported IDs cannot be distinguished without independent hardware
  identity evidence. Intentional placement/card changes require a powered-down
  move and a new commissioning/startup, not forgetting a missing device in-place.

Software cannot guarantee cooling if the daemon, controller, fan power or fans
are unavailable. The controller's EEPROM startup airflow and a suitable watchdog
policy remain separate hardware/operator responsibilities. This feature does not
enable a watchdog or change boot defaults.

## Active versus passive GPUs and quiet floors

The calibration used a self-cooled Quadro in eleven slots and a passive dual-die
V340L in MB2. Its root-port map is not a license to reuse the Quadro's relaxed
curve for passive GPUs. Before reinstalling a passive V340L, restore the proper
occupied-card curve/coverage and EEPROM startup PWM100 while the safe existing
hardware is still installed. Automatic placement does not infer cooling design
or choose a card-specific thermal policy.

PWM38/255 is about 15% duty, not 15% RPM. Measured tach floors differ among fans;
forcing a lower duty can stop one fan without slowing the others. This feature
does not retune the accepted floor or controller startup defaults.

## Offline installation and tests

`DESTDIR` stages an image without root or live fan-controller dependencies:

```sh
DESTDIR=/absolute/path/to/staged-image bash install.sh
python3 -m unittest discover -v
python3 -m py_compile octofan-control octofan_topology.py
bash -n install.sh
```

Staging never invokes `systemctl` or the fan CLI. Existing staged host configs
are preserved byte-for-byte. Tests use disposable sysfs trees and synthetic
writers; the retained calibration fixture is provenance-linked to the twelve
observations. Simulated multi-card tests are not live multi-card qualification.

# Fan and physical-slot mapping (Octominer X12 / MINERDUDE 12XTREME)

Verify the electrical wiring and physical coverage before trusting a profile.
The calibrated profile is evidence from one rig, not proof that all chassis,
backplanes, BIOS revisions or fan replacements behave identically.

## Conventions

- Case fans count left to right facing the rig: FAN1 is leftmost.
- Slots count right to left: MB1, MB2, then DB1..DB10.
- Confirmed wiring: FAN1=ch8, FAN2=ch6, FAN3=ch4, FAN4=ch2, FAN5=ch0.
  Odd channels are physically empty on the calibrated rig.

## Controller quirks and safety boundaries

- An even-channel PWM write also changes its odd partner. A mirrored curPWM is
  not proof of a second physical fan; verify tach and operator observations.
- Runtime PWM is not RPM percentage. The five measured low-duty responses differ;
  lowering a number can stop one fan without slowing the others appreciably.
- The daemon reasserts targets every poll, so an ad-hoc write during normal
  operation is transient. Do not fight the daemon with a separate controller.
- Keep adequate EEPROM startup airflow before any GPU boots. The accepted quiet
  startup PWM38 was specific to the self-cooled Quadro enumeration exercise;
  restore startup PWM100 and the appropriate card curve before passive V340L use.
- Software temperature-loss fail-safe does not replace hardware watchdog coverage
  for a dead daemon or unavailable controller. This feature does not arm or
  disable a watchdog. Do not disable an existing watchdog as routine commissioning.
- PSU fans and the GPU's own fan controller are separate from these case-fan zones.

## 1. Electrical and physical fan verification

Use a planned, idle commissioning window and an operator at the rig. Preserve a
known safe airflow baseline, monitor actual GPU temperatures, use a bounded test
with an independent restore guard, and restore/verify the daemon after testing.
Prefer briefly boosting one channel over that safe baseline rather than stopping
all fans. Do not run an unattended zero-all-channel sweep on installed GPUs;
particularly do not remove case airflow from passive cards.

Record channel tach responses, mirrored partner channels and the operator's
physical fan label. Electrical tach alone does not establish left/right position
or prove a zero-RPM reading is physical standstill.

If fan wiring has not changed since its operator-confirmed mapping, retain that
mapping rather than re-running disruptive blips for every software update.

## 2. Slot coverage and calibrated root identity

The versioned profile `profiles/minerdude-12xtreme.json` contains the twelve
operator-labelled root-port identities and the following physical coverage:

| Slot | Covering fan(s) |
|---|---|
| MB1, MB2 | FAN5 |
| DB1, DB2 | FAN4 |
| DB3 | FAN3 + FAN4, each at 0.75 scale |
| DB4, DB5 | FAN3 |
| DB6 | FAN2 + FAN3, each at 0.75 scale |
| DB7, DB8 | FAN2 |
| DB9 | FAN1 + FAN2, each at 0.75 scale |
| DB10 | FAN1 |

Manual mode needs an explicit `[cards] slots` list. Automatic mode instead maps
all observed GPU endpoints through their actual motherboard root port. It uses
root vendor/device plus PCIe LinkCap hardware Port#, validated as a Root Port.

Do not use BDF alone: DB1 and DB2 exposed the same root BDF and complete GPU bus
paths in separate boots while having different hardware root IDs/port numbers.
Keep the full root tuple; distinguish a card-internal switch from the motherboard
root and group multiple GPU dies behind that root as one occupied slot.

The retained twelve samples prove single-card positional discovery. Validate
simultaneous populations separately, since bus enumeration and operational
cooling may change with several cards installed. Before moving a physical card,
power down, verify it has stopped and disconnect mains; a remotely accepted
shutdown command is not proof of electrical isolation.

## 3. Read-only commissioning and controlled activation

Follow the candidate-config workflow in `README.md`. Select automatic mode and
the exact profile explicitly; profile wiring/coverage must agree with the INI.
Inspect before touching the running controller:

```sh
sudo /usr/local/sbin/octofan-control --inspect /root/octofan-control.candidate.conf
```

Check every GPU endpoint/die, root/physical slot, hwmon association, exclusions,
slot temperature and predicted channel target. A missing die is not satisfied by
its healthy sibling. Unknown or ambiguous placement must produce a fault and a
full-speed plan, never background-only cooling.

After an authorized activation, verify service health and journal, then read
`fan_controller_cli -h`: actual curPWM/tachs must agree with requested targets.
Under ordinary loads, zones should follow their own GPUs; shared fans take the
maximum request. Background fans can still ramp deliberately at the configured
high-temperature thresholds. Verify real loaded temperatures and fault recovery
before declaring any GPU population qualified.

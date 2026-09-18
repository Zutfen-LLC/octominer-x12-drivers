# Fan mapping procedure (Octominer X12 / MINERDUDE 12XTREME)

Rigs have arrived with fans on non-stock headers, and the controller has
quirks that make guessing expensive. Verify every rig once with this
procedure before trusting any [fans]/[slots] config.

## Conventions (the numbering landmine)

* Case fans count **left to right** facing the rig: FAN1 leftmost.
* PCIe slots count **right to left**: slot 1 is rightmost. Motherboard
  slots are MB1/MB2 (under FAN5's end); daughterboard slots DB1-DB10
  continue leftward (DB1 rightmost, FAN4's zone).
* Stock 12XTREME fan headers: FAN1=ch8, FAN2=ch6, FAN3=ch4, FAN4=ch2,
  FAN5=ch0; odd channels empty.

## Controller quirks

* Writing PWM to an even channel mirrors the value to its odd pair
  (write ch4 -> ch4+ch5). A second fan spinning during a blip is usually
  this, not a Y-splitter; check the adjacent odd channel's tach.
* curPWM is what the controller holds; a manual write persists until
  something overwrites it. The daemon re-asserts every poll.
* EEPROM default PWM (defPWM) covers pre-daemon boot airflow: keep ~100.
* Hardware watchdog should be disabled (`-w 0 -v 0`); the daemon's
  no-temperature fail-safe replaces it.

## Step 1: electrical blip sweep (no human needed)

Stop the daemon, zero all channels, then blip each channel to 255 alone
and read tach. Channels with RPM have a fan; channels without are empty.

```bash
sudo systemctl stop octofan-control
for i in $(seq 0 11); do sudo fan_controller_cli -f $i -v 0; done
sleep 5
for c in $(seq 0 11); do
  sudo fan_controller_cli -f $c -v 255; sleep 4
  rpm=$(sudo fan_controller_cli -h | awk -v ch=$c '$1==ch {print $4}')
  echo "ch$c -> $rpm RPM"
  sudo fan_controller_cli -f $c -v 0; sleep 2
done
sudo systemctl start octofan-control
```

## Step 2: physical position pass (human at the rig)

Blip each occupied channel one at a time and have the operator name the
physical fan that spins. This binds channel -> physical position. With
fans on stock headers the expected order (left to right) is ch8, ch6,
ch4, ch2, ch0; a blip run in that order should spin the fans smoothly
left to right.

## Step 3: slot coverage

Use the coverage table above (it is chassis geometry, identical across
stock 12XTREME builds) and record which slots hold cards. Write both
into /etc/octofan-control.conf ([fans], [slots] if non-stock, [cards]).

## Step 4: verify

```bash
sudo systemctl restart octofan-control
journalctl -u octofan-control -n 5   # expect one line per target change
sudo fan_controller_cli -h           # curPWM matches journal targets
```

Under load, only fans covering occupied slots should ramp; the rest hold
the background floor.

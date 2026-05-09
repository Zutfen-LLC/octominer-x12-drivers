# Octominer Fan Controller (Ubuntu Quick Reference)

## Version

* CLI: **3.1**
* Firmware: **3.0**
* Hardware: **1.2**
* Bootloader: **2.0**

---

## Command Overview

### Show status

```bash
sudo ./fan_controller_cli -h
```

### Machine-readable status

```bash
sudo ./fan_controller_cli -r
```

---

## Fan Control (IMPORTANT)

### Set one fan (PWM 0–255)

```bash
sudo ./fan_controller_cli -f <fan_id> -v <pwm>
```

---

## Useful Examples

My system has 12 (0..11) "fans" (4 physical). Yours may be different

### Set all fans to ~50%

```bash
for i in {0..11}; do
  sudo ./fan_controller_cli -f $i -v 128
done
```

### Quiet mode (~30%)

```bash
for i in {0..11}; do
  sudo ./fan_controller_cli -f $i -v 80
done
```

### Very quiet / idle test (~15%)

```bash
for i in {0..11}; do
  sudo ./fan_controller_cli -f $i -v 40
done
```

### Full speed restore

```bash
for i in {0..11}; do
  sudo ./fan_controller_cli -f $i -v 255
done
```

---

## Notes

* Range: **0–255 PWM**
* Some fans may not respond (hardware-dependent)
* Watchdog is enabled and may override low speeds
* Avoid low PWM under GPU load

---

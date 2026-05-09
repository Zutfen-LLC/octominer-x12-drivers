\# Octominer Fan Controller (Ubuntu Quick Reference)



\## Version



\* CLI: \*\*3.1\*\*

\* Firmware: \*\*3.0\*\*

\* Hardware: \*\*1.2\*\*

\* Bootloader: \*\*2.0\*\*



\---



\## Command Overview



\### Show status



```bash

sudo ./fan\_controller\_cli -h

```



\### Machine-readable status



```bash

sudo ./fan\_controller\_cli -r

```



\---



\## Fan Control (IMPORTANT)



\### Set one fan (PWM 0–255)



```bash

sudo ./fan\_controller\_cli -f <fan\_id> -v <pwm>

```



\---



\## Useful Examples



My system has 12 (0..11) "fans" (4 physical). Yours may be different



\### Set all fans to \~50%



```bash

for i in {0..11}; do

&#x20; sudo ./fan\_controller\_cli -f $i -v 128

done

```



\### Quiet mode (\~30%)



```bash

for i in {0..11}; do

&#x20; sudo ./fan\_controller\_cli -f $i -v 80

done

```



\### Very quiet / idle test (\~15%)



```bash

for i in {0..11}; do

&#x20; sudo ./fan\_controller\_cli -f $i -v 40

done

```



\### Full speed restore



```bash

for i in {0..11}; do

&#x20; sudo ./fan\_controller\_cli -f $i -v 255

done

```



\---



\## Notes



\* Range: \*\*0–255 PWM\*\*

\* Some fans may not respond (hardware-dependent)

\* Watchdog is enabled and may override low speeds

\* Avoid low PWM under GPU load



\---



This driver was ripped from my as-shipped Octominer X12 Ultra. I never updated the HiveOS installation before doing this

so it may or may not be the latest driver. 



Extra note: I have not played with the Watchdog yet, but after a few minutes it will auto-ramp the fans back to max.

I might eventually come back with an auto fan override that watches the environment sensors, but as is this is

just the raw drivers with no changes


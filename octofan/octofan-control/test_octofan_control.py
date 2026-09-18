#!/usr/bin/env python3
"""Unit tests for octofan-control (run from octofan/octofan-control):
python3 test_octofan_control.py"""
import importlib.util
import os
import sys
import tempfile
import unittest
from importlib.machinery import SourceFileLoader

HERE = os.path.dirname(os.path.abspath(__file__))
oc = SourceFileLoader(
    "octofan_control", os.path.join(HERE, "octofan-control")).load_module()

CONF = """
[controller]
cli = /bin/true
poll_seconds = 0.01
step_hysteresis = 2
floor_pwm = 38

[fans]
FAN1 = 8
FAN2 = 6
FAN3 = 4
FAN4 = 2
FAN5 = 0

[slots]
MB1 = FAN5
MB2 = FAN5
DB1 = FAN4
DB2 = FAN4
DB3 = FAN3@0.75, FAN4@0.75
DB4 = FAN3
DB5 = FAN3
DB6 = FAN2@0.75, FAN3@0.75
DB7 = FAN2
DB8 = FAN2
DB9 = FAN1@0.75, FAN2@0.75
DB10 = FAN1

[cards]
slots = DB1

[sensors]
hwmon_names = amdgpu
sensors = temp1_input, temp2_input

[curves]
card_steps = 40:38, 50:64, 60:102, 70:153, 80:204, 90:230
card_max_pwm = 255
background_steps = 70:38, 80:64, 90:102
background_max_pwm = 153
"""


def make_cfg(extra=""):
    with tempfile.NamedTemporaryFile("w", suffix=".conf",
                                     delete=False) as f:
        f.write(CONF + extra)
        path = f.name
    try:
        return oc.load_config(path)
    finally:
        os.unlink(path)


class TestConfig(unittest.TestCase):
    def test_shipped_example_parses(self):
        cfg = oc.load_config(os.path.join(HERE, "octofan-control.conf"))
        self.assertEqual(cfg["fans"], {"FAN1": 8, "FAN2": 6, "FAN3": 4,
                                       "FAN4": 2, "FAN5": 0})
        self.assertEqual(len(cfg["all_channels"]), 5)
        self.assertIn("DB1", cfg["slots"])

    def test_unknown_fan_in_slot_rejected(self):
        bad = CONF.replace("DB1 = FAN4", "DB1 = FAN9")
        with tempfile.NamedTemporaryFile("w", delete=False) as f:
            f.write(bad)
            p = f.name
        try:
            with self.assertRaises(SystemExit):
                oc.load_config(p)
        finally:
            os.unlink(p)

    def test_card_slot_must_exist(self):
        bad = CONF.replace("slots = DB1", "slots = DB99")
        with tempfile.NamedTemporaryFile("w", delete=False) as f:
            f.write(bad)
            p = f.name
        try:
            with self.assertRaises(SystemExit):
                oc.load_config(p)
        finally:
            os.unlink(p)


class TestCurves(unittest.TestCase):
    def setUp(self):
        self.cfg = make_cfg()

    def test_card_table(self):
        c = self.cfg["card_curve"]
        for t, pwm in {36: 38, 45: 64, 55: 102, 65: 153, 75: 204,
                       85: 230, 95: 255}.items():
            self.assertEqual(oc.target_pwm(c, float(t), None, 2), pwm)

    def test_background_table(self):
        c = self.cfg["bg_curve"]
        for t, pwm in {36: 38, 65: 38, 75: 64, 85: 102, 95: 153}.items():
            self.assertEqual(oc.target_pwm(c, float(t), None, 2), pwm)

    def test_hysteresis(self):
        c = self.cfg["card_curve"]
        self.assertEqual(oc.target_pwm(c, 58.0, 153, 2), 102)  # 60-2 -> drop
        self.assertEqual(oc.target_pwm(c, 59.0, 153, 2), 153)  # hold
        self.assertEqual(oc.target_pwm(c, 85.0, 102, 2), 230)  # jump up

    def test_stray_current_recovers(self):
        c = self.cfg["card_curve"]
        self.assertEqual(oc.target_pwm(c, 55.0, 77, 2), 102)


class TestTargets(unittest.TestCase):
    def setUp(self):
        self.cfg = make_cfg()

    def test_db1_card_only_fan4_channel(self):
        self.assertEqual(oc.occupied_fans(self.cfg), {"FAN4"})
        t = oc.compute_targets(self.cfg, 153, 38)
        self.assertEqual(t, {8: 38, 6: 38, 4: 38, 2: 153, 0: 38})

    def test_bridge_slot_two_fans_scaled(self):
        cfg = make_cfg()
        cfg["card_slots"] = ["DB3"]
        t = oc.compute_targets(cfg, 153, 38)
        self.assertEqual(t, {8: 38, 6: 38, 4: 115, 2: 115, 0: 38})

    def test_bridge_floor_respected(self):
        cfg = make_cfg()
        cfg["card_slots"] = ["DB9"]
        t = oc.compute_targets(cfg, 38, 38)
        self.assertEqual(t, {8: 38, 6: 38, 4: 38, 2: 38, 0: 38})

    def test_bg_ramp_capped(self):
        t = oc.compute_targets(self.cfg, 255, 153)
        self.assertEqual(t, {8: 153, 6: 153, 4: 153, 2: 255, 0: 153})


class TestLoop(unittest.TestCase):
    def test_reassert_and_ramp(self):
        cfg = make_cfg()
        writes = []
        temps = iter([39.0, 55.0, 55.0])
        oc.run_loop(cfg,
                    read_temp=lambda: next(temps),
                    set_fan_fn=lambda ch, pwm: writes.append((ch, pwm)),
                    sleep_fn=lambda s: None,
                    iterations=3)
        ch2 = [p for ch, p in writes if ch == 2]
        self.assertEqual(ch2[:2], [38, 102])       # ramp with temp
        self.assertEqual(len(ch2), 3)               # re-asserted every poll
        for ch in (8, 6, 4, 0):
            vals = [p for c, p in writes if c == ch]
            self.assertEqual(vals, [38, 38, 38])    # bg floor throughout

    def test_failsafe_no_temp(self):
        cfg = make_cfg()
        writes = []
        oc.run_loop(cfg,
                    read_temp=lambda: None,
                    set_fan_fn=lambda ch, pwm: writes.append((ch, pwm)),
                    sleep_fn=lambda s: None,
                    iterations=4)
        # 3rd poll trips fail-safe: everything to 255 exactly once
        full = [p for ch, p in writes if p == 255]
        self.assertEqual(len(full), 5)

    def test_failsafe_recovery(self):
        cfg = make_cfg()
        writes = []
        temps = iter([None, None, 40.0])
        oc.run_loop(cfg,
                    read_temp=lambda: next(temps, 40.0),
                    set_fan_fn=lambda ch, pwm: writes.append((ch, pwm)),
                    sleep_fn=lambda s: None,
                    iterations=5)
        # after recovery, curve outputs are re-asserted again
        ch2 = [p for ch, p in writes if ch == 2]
        self.assertIn(38, ch2)


if __name__ == "__main__":
    unittest.main(verbosity=2)

#!/usr/bin/env python3
"""Unit tests for octofan-control (run from octofan/octofan-control):
python3 test_octofan_control.py"""
import importlib.util
import os
import sys
import tempfile
import unittest
from importlib.machinery import SourceFileLoader
from unittest import mock

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

    def test_terminal_curve_pwm_out_of_range_is_rejected(self):
        bad = CONF.replace("card_max_pwm = 255", "card_max_pwm = 256")
        with tempfile.NamedTemporaryFile("w", suffix=".conf", delete=False,
                                         dir=os.environ.get("TMPDIR")) as f:
            f.write(bad)
            path = f.name
        try:
            with self.assertRaises(SystemExit):
                oc.load_config(path)
        finally:
            os.unlink(path)

    def test_normal_auto_dispatch_rejects_synthetic_sysfs_root(self):
        cfg = make_cfg()
        cfg.update({"mode": "auto", "sysfs_root": "/synthetic/sys"})
        with mock.patch.object(oc, "load_config", return_value=cfg), \
             mock.patch.object(oc, "run_auto_loop") as loop:
            with self.assertRaises(SystemExit):
                oc.main(["octofan-control", "config.ini"])
            loop.assert_not_called()

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


    def test_auto_slot_targets_are_independent_and_shared_fans_take_max(self):
        cfg = make_cfg()
        snapshot = {"slot_temps": {"DB1": 55.0, "DB10": 45.0}, "global_temp": 55.0}
        targets, slot_pwms, _ = oc.auto_targets(cfg, snapshot, {}, None)
        self.assertEqual(slot_pwms["DB1"], 102)
        self.assertEqual(slot_pwms["DB10"], 64)
        self.assertEqual(targets[2], 102)
        self.assertEqual(targets[8], 64)

    def test_writer_nonzero_is_reported_after_trying_every_channel(self):
        cfg = make_cfg()
        attempted = []
        def writer(ch, pwm):
            attempted.append(ch)
            return ch != 4
        self.assertFalse(oc._write_round(cfg, {ch: 100 for ch in cfg["all_channels"]}, writer))
        self.assertEqual(sorted(attempted), cfg["all_channels"])


class TestAutoStateMachine(unittest.TestCase):
    def setUp(self):
        self.cfg = make_cfg()
        self.cfg.update({"mode": "auto", "poll": 0.01})

    @staticmethod
    def snapshot(temps, fault=None, topology=None, endpoints=None):
        endpoints = endpoints or [
            {"bdf": "0000:01:00.0", "vendor": 1, "device": 2,
             "subsystem_vendor": 3, "subsystem_device": 4,
             "root": ("0x1234", "0x5678", 1), "slot": "DB1"},
            {"bdf": "0000:02:00.0", "vendor": 1, "device": 2,
             "subsystem_vendor": 3, "subsystem_device": 4,
             "root": ("0x1234", "0x5678", 2), "slot": "DB10"},
        ]
        sensors = [fault] if fault else []
        top = [topology] if topology else []
        vals = list(temps.values())
        return {"endpoints": endpoints, "excluded": [], "slot_temps": temps,
                "global_temp": max(vals) if vals else None,
                "faults": top + sensors, "topology_faults": top,
                "sensor_faults": sensors}

    def run_trace(self, snapshots, write_results=None):
        seq = iter(snapshots)
        writes = []
        results = iter(write_results or [])
        def writer(ch, pwm):
            writes.append((ch, pwm))
            result = next(results, True)
            if result is False:
                return False
            return True
        original = oc.auto_snapshot
        oc.auto_snapshot = lambda cfg: next(seq)
        try:
            oc.run_auto_loop(self.cfg, writer=writer, iterations=len(snapshots),
                             sleep_fn=lambda _: None)
        finally:
            oc.auto_snapshot = original
        return [dict(writes[i:i + 5]) for i in range(0, len(writes), 5)]

    def test_sensor_fault_grace_holds_downward_targets_then_reasserts_full_speed(self):
        traces = self.run_trace([
            self.snapshot({"DB1": 45, "DB10": 45}),
            self.snapshot({"DB1": 45, "DB10": 45}),
            self.snapshot({"DB1": 45, "DB10": 45}),
            self.snapshot({"DB1": 85}, fault="DB10 sensor missing"),
            self.snapshot({"DB1": 85}, fault="DB10 sensor missing"),
            self.snapshot({"DB1": 45}, fault="DB10 sensor missing"),
            self.snapshot({"DB1": 45}, fault="DB10 sensor missing"),
        ])
        # DB1 is FAN4/channel 2; healthy DB1 may ramp upward, but not down.
        self.assertEqual(traces[3][2], 230)
        self.assertEqual(traces[4][2], 230)
        self.assertTrue(all(p == 255 for row in traces[5:] for p in row.values()))

    def test_startup_requires_two_complete_snapshots_before_reduced_control(self):
        traces = self.run_trace([self.snapshot({"DB1": 45, "DB10": 45})] * 3)
        self.assertTrue(all(p == 255 for p in traces[0].values()))
        self.assertTrue(all(p == 255 for p in traces[1].values()))
        self.assertLess(max(traces[2].values()), 255)

    def test_topology_fault_is_immediate_and_reasserted(self):
        traces = self.run_trace([
            self.snapshot({"DB1": 45, "DB10": 45}),
            self.snapshot({"DB1": 45, "DB10": 45}),
            self.snapshot({"DB1": 45, "DB10": 45}, topology="unknown topology"),
            self.snapshot({"DB1": 45, "DB10": 45}, topology="unknown topology"),
        ])
        self.assertTrue(all(p == 255 for row in traces[2:] for p in row.values()))

    def test_changed_or_missing_enrolled_identity_is_fatal(self):
        good = self.snapshot({"DB1": 45, "DB10": 45})
        changed = self.snapshot({"DB1": 45}, endpoints=[good["endpoints"][0]])
        traces = self.run_trace([good, good, changed, good, good, good])
        self.assertTrue(all(p == 255 for p in traces[2].values()))
        self.assertTrue(all(p == 255 for p in traces[3].values()))
        self.assertTrue(all(p == 255 for p in traces[4].values()))
        self.assertLess(max(traces[5].values()), 255)

    def test_inspection_converts_reader_exception_to_safe_plan(self):
        self.cfg["mode"] = "auto"
        self.cfg["all_channels"] = [0, 2]
        with mock.patch.object(oc, "auto_snapshot", side_effect=OSError("reader offline")):
            report = oc.inspect(self.cfg)
        self.assertIn("reader offline", report["faults"][0])
        self.assertEqual(report["fan_targets"], {"0": 255, "2": 255})

    def test_writer_failure_falls_back_and_stays_latched_until_confirmed(self):
        good = self.snapshot({"DB1": 45, "DB10": 45})
        sequence = iter([False] + [True] * 100)
        writes = []
        def writer(ch, pwm):
            writes.append((ch, pwm))
            return next(sequence)
        original = oc.auto_snapshot
        oc.auto_snapshot = lambda cfg: good
        try:
            oc.run_auto_loop(self.cfg, writer=writer, iterations=5,
                             sleep_fn=lambda _: None)
        finally:
            oc.auto_snapshot = original
        rows = [writes[i:i + 5] for i in range(0, len(writes), 5)]
        self.assertEqual(len(rows), 6)  # failed initial round + immediate fallback + 4 polls
        self.assertEqual([p for _, p in rows[0]], [255] * 5)
        self.assertTrue(all(p == 255 for row in rows[:3] for _, p in row))
        self.assertTrue(any(p < 255 for row in rows[3:] for _, p in row))

    def test_set_fan_checks_command_failure_and_timeout_is_caught_by_round(self):
        cfg = self.cfg
        attempted = []
        def writer(ch, pwm):
            attempted.append(ch)
            if ch == 8:
                raise oc.subprocess.TimeoutExpired("fan", 10)
            if ch == 6:
                raise OSError("offline")
            return True
        self.assertFalse(oc._write_round(cfg, {ch: 100 for ch in cfg["all_channels"]}, writer))
        self.assertEqual(sorted(attempted), cfg["all_channels"])

    def test_manual_missing_temperature_reasserts_full_speed_each_poll(self):
        cfg = make_cfg()
        writes = []
        oc.run_loop(cfg, read_temp=lambda: None,
                    set_fan_fn=lambda ch, pwm: writes.append((ch, pwm)),
                    sleep_fn=lambda _: None, iterations=6)
        self.assertEqual(sum(p == 255 for _, p in writes), 20)


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
        # Once the third miss trips fail-safe, every later miss reasserts it.
        full = [p for ch, p in writes if p == 255]
        self.assertEqual(len(full), 10)

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

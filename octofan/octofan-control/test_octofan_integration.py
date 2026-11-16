"""Production-entrypoint and cross-module integration regressions; no hardware."""
import configparser
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from test_octofan_control import CONF, HERE, make_cfg, oc
from test_octofan_topology import SysfsTree


class IntegratedControlTests(unittest.TestCase):
    def test_unreadable_pci_ancestor_cannot_enable_integrated_exclusion(self):
        with tempfile.TemporaryDirectory() as td:
            tree = SysfsTree(td)
            _, root = tree.endpoint("0000:91:00.0", "DB1", vendor=0x8086, device=0x1902)
            (root / "class").write_text("unreadable-class")
            found = oc.octofan_topology.discover_endpoints(td, oc.octofan_topology.load_profile(
                Path(HERE) / "profiles/minerdude-12xtreme.json"))
            self.assertTrue(found["faults"])
            self.assertFalse(found["excluded"])

    def test_unknown_outer_root_cannot_fall_back_to_a_card_internal_rootport(self):
        from test_octofan_topology import config_bytes
        with tempfile.TemporaryDirectory() as td:
            tree = SysfsTree(td)
            ep, root = tree.endpoint("0000:91:00.0", "DB1")
            (root / "config").write_bytes(bytes(256))
            inner = ep.parent
            for name, value in [("class", "0x060400"), ("vendor", "0x8086"), ("device", "0xa29a")]:
                (inner / name).write_text(value)
            (inner / "config").write_bytes(config_bytes(11))
            profile = oc.octofan_topology.load_profile(Path(HERE) / "profiles/minerdude-12xtreme.json")
            self.assertTrue(oc.octofan_topology.discover_endpoints(td, profile)["faults"])

    def test_exact_igpu_id_requires_proven_direct_host_root(self):
        with tempfile.TemporaryDirectory() as td:
            tree = SysfsTree(td)
            ep = Path(td) / "devices/unverified-parent/0000:00:02.0"
            ep.mkdir(parents=True)
            for name, value in [("class", "0x030000"), ("vendor", "0x8086"), ("device", "0x1902")]:
                (ep / name).write_text(value)
            (tree.devices / ep.name).symlink_to(ep)
            profile = oc.octofan_topology.load_profile(Path(HERE) / "profiles/minerdude-12xtreme.json")
            found = oc.octofan_topology.discover_endpoints(td, profile)
            self.assertFalse(found["excluded"])
            self.assertTrue(found["faults"])

    def test_hwmon_driver_names_are_exact_not_prefixes(self):
        with tempfile.TemporaryDirectory() as td:
            tree = SysfsTree(td)
            ep, _ = tree.endpoint("0000:91:00.0", "DB1")
            tree.hwmon("amdgpu", ep, {"temp1_input": "45000"})
            unrelated = Path(td) / "devices/platform/not-a-gpu"
            unrelated.mkdir(parents=True)
            tree.hwmon("amdgpu_fake", unrelated, {"temp1_input": "149000"})
            cfg = make_cfg()
            cfg.update(sysfs_root=td, profile_data=oc.octofan_topology.load_profile(
                Path(HERE) / "profiles/minerdude-12xtreme.json"))
            snapshot = oc.auto_snapshot(cfg)
            self.assertFalse(snapshot["faults"])
            self.assertEqual(snapshot["slot_temps"], {"DB1": 45.0})

    def test_profile_rejects_hardware_channel_overflow_and_duplicate_coverage(self):
        original = json.loads((Path(HERE) / "profiles/minerdude-12xtreme.json").read_text())
        for problem in ["channel", "coverage"]:
            with self.subTest(problem=problem), tempfile.TemporaryDirectory() as td:
                value = json.loads(json.dumps(original))
                if problem == "channel":
                    value["fans"]["FAN1"] = 12
                    for slot in value["slots"]:
                        for coverage in slot["coverage"]:
                            if coverage["fan"] == "FAN1":
                                coverage["channel"] = 12
                else:
                    value["slots"][0]["coverage"].append(dict(value["slots"][0]["coverage"][0]))
                path = Path(td) / "bad.json"
                path.write_text(json.dumps(value))
                with self.assertRaises(oc.octofan_topology.TopologyError):
                    oc.octofan_topology.load_profile(path)

    def test_duplicate_pcie_capabilities_are_not_silently_overridden(self):
        from test_octofan_topology import config_bytes
        data = bytearray(config_bytes(11))
        data[0x41] = 0x60
        data[0x60:0x64] = bytes([0x10, 0, 0x40, 0])
        data[0x6c:0x70] = (11 << 24).to_bytes(4, "little")
        with self.assertRaises(oc.octofan_topology.TopologyError):
            oc.octofan_topology.parse_root_port_config(data)

    def test_actual_inspection_cli_binds_loaded_configuration_to_each_gpu(self):
        with tempfile.TemporaryDirectory(prefix="octofan-cli-") as td:
            base = Path(td)
            tree = SysfsTree(base / "sys")
            first, _ = tree.endpoint("0000:91:00.0", "DB1", "0000:00:1b.0",
                                     vendor=0x1002, device=0x6864)
            second, _ = tree.endpoint("0000:92:00.0", "DB10", "0000:00:1c.0")
            tree.hwmon("amdgpu", first, {"temp1_input": "45000"})
            second_hwmon = tree.hwmon("nouveau", second, {"temp1_input": "85000"})
            marker = base / "FAN_CLI_WAS_CALLED"
            sentinel = base / "refuse-fan-cli"
            sentinel.write_text("#!" + sys.executable + "\nfrom pathlib import Path\n"
                                + "Path(" + repr(str(marker)) + ").write_text('called')\n"
                                + "raise SystemExit(97)\n")
            sentinel.chmod(0o755)
            cp = configparser.ConfigParser()
            cp.optionxform = lambda optionstr: optionstr
            cp.read_string(CONF)
            cp["controller"]["mode"] = "auto"
            cp["controller"]["cli"] = str(sentinel)
            cp["sensors"]["hwmon_names"] = "amdgpu,nouveau,nvidia"
            cp["curves"]["background_steps"] = "85:38,90:64"
            cp["automatic"] = {
                "profile": str(Path(HERE) / "profiles/minerdude-12xtreme.json"),
                "sysfs_root": str(base / "sys"),
            }
            candidate = base / "candidate.conf"
            with candidate.open("w") as f:
                cp.write(f)
            def inspect():
                return subprocess.run([sys.executable, "-B", str(Path(HERE) / "octofan-control"),
                                       "--inspect", str(candidate)], capture_output=True,
                                      text=True, timeout=15)
            result = inspect()
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            report = json.loads(result.stdout)
            self.assertFalse(report["faults"])
            self.assertEqual(report["slot_temps"], {"DB1": 45.0, "DB10": 85.0})
            self.assertEqual(report["fan_targets"], {"0": 38, "2": 64, "4": 38, "6": 38, "8": 230})
            self.assertEqual({e["bdf"]: e["temperature"] for e in report["endpoints"]},
                             {"0000:91:00.0": 45.0, "0000:92:00.0": 85.0})
            self.assertFalse(marker.exists())
            (second_hwmon / "temp1_input").unlink()
            result = inspect()
            self.assertNotEqual(result.returncode, 0)
            report = json.loads(result.stdout)
            self.assertTrue(report["sensor_faults"])
            self.assertTrue(all(p == 255 for p in report["fan_targets"].values()))
            self.assertFalse(marker.exists())

    def test_first_snapshot_reader_failure_writes_full_speed_instead_of_crashing(self):
        cfg = make_cfg()
        writes = []
        with mock.patch.object(oc, "auto_snapshot", side_effect=OSError("PCI unavailable")):
            oc.run_auto_loop(cfg, iterations=1, sleep_fn=lambda _: None,
                             writer=lambda ch, pwm: writes.append((ch, pwm)))
        self.assertEqual(writes, [(ch, 255) for ch in cfg["all_channels"]])

    def test_manual_failed_fullspeed_round_cannot_resume_curve_immediately(self):
        cfg = make_cfg()
        sequence = iter([None, None, None, 45.0, 45.0, 45.0])
        writes = []
        failed = False
        def writer(ch, pwm):
            nonlocal failed
            writes.append((ch, pwm))
            if ch == 0 and pwm == 255 and not failed:
                failed = True
                return False
            return True
        oc.run_loop(cfg, read_temp=lambda: next(sequence), set_fan_fn=writer,
                    iterations=6, sleep_fn=lambda _: None)
        rows = [dict(writes[i:i + 5]) for i in range(0, len(writes), 5)]
        self.assertGreaterEqual(len(rows), 4)
        self.assertTrue(all(p == 255 for row in rows[:3] for p in row.values()), rows)
        self.assertTrue(any(p < 255 for p in rows[-1].values()))

    def test_slot_hysteresis_does_not_use_other_slots_curve_state(self):
        cfg = make_cfg()
        cfg["bg_curve"] = [(100.0, 38), (float("inf"), 153)]
        snapshot = {"slot_temps": {"DB1": 59.0, "DB10": 59.0}, "global_temp": 59.0}
        targets, card, _ = oc.auto_targets(cfg, snapshot,
                                          {"DB1": 153, "DB10": 38}, None)
        self.assertEqual(card, {"DB1": 153, "DB10": 102})
        self.assertEqual(targets[2], 153)
        self.assertEqual(targets[8], 102)


if __name__ == "__main__":
    unittest.main()

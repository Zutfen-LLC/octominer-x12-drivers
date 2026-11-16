import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("octofan_topology", HERE / "octofan_topology.py")
TOPOLOGY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(TOPOLOGY)
PROFILE_PATH = HERE / "profiles" / "minerdude-12xtreme.json"
PROFILE = TOPOLOGY.load_profile(PROFILE_PATH)
# Frozen root identities from the operator-labelled, hash-verified sweep.
CALIBRATED_ROOT_KEYS = {
    "MB1": ("0x8086", "0x1901", 2), "MB2": ("0x8086", "0xa295", 6),
    "DB1": ("0x8086", "0xa29a", 11), "DB2": ("0x8086", "0xa29b", 12),
    "DB3": ("0x8086", "0xa299", 10), "DB4": ("0x8086", "0xa2ee", 24),
    "DB5": ("0x8086", "0xa298", 9), "DB6": ("0x8086", "0xa2ed", 23),
    "DB7": ("0x8086", "0xa297", 8), "DB8": ("0x8086", "0xa2ec", 22),
    "DB9": ("0x8086", "0xa296", 7), "DB10": ("0x8086", "0xa2eb", 21),
}


def put(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(value, bytes):
        path.write_bytes(value)
    else:
        path.write_text(str(value))


def config_bytes(port, ptype=4):
    data = bytearray(256)
    data[6:8] = (0x10).to_bytes(2, "little")
    data[0x34] = 0x40
    data[0x40:0x44] = bytes((0x10, 0, ptype << 4, 0))
    data[0x4c:0x50] = (port << 24).to_bytes(4, "little")
    return bytes(data)


class SysfsTree:
    def __init__(self, root):
        self.root = Path(root)
        self.devices = self.root / "bus/pci/devices"
        self.devices.mkdir(parents=True)
        self.paths = {}

    def endpoint(self, bdf, slot="DB1", root_object="0000:00:1b.0", vendor=0x10de, device=0x1abc, cls=0x030000):
        slot_data = next(s for s in PROFILE["slots"] if s["slot"] == slot)
        tup = slot_data["root"]
        # Separate stable physical root object from mutable BDF lookup symlink.
        rp = self.root / "devices" / root_object
        put(rp / "class", "0x060400")
        put(rp / "vendor", tup["vendor"])
        put(rp / "device", tup["device"])
        put(rp / "config", config_bytes(tup["port"]))
        ep = self.root / "devices" / (root_object + "/0000:01:00.0/" + bdf)
        # Model the real internal PCIe switch ancestor, not an empty directory.
        put(ep.parent / "class", "0x060400")
        put(ep.parent / "vendor", "0x10de")
        put(ep.parent / "device", "0x05be")
        put(ep.parent / "config", config_bytes(0, ptype=5))
        put(ep / "class", f"0x{cls:06x}")
        put(ep / "vendor", f"0x{vendor:04x}")
        put(ep / "device", f"0x{device:04x}")
        put(ep / "subsystem_vendor", "0x10de")
        put(ep / "subsystem_device", "0x0001")
        link = self.devices / bdf
        link.symlink_to(ep, target_is_directory=True)
        self.paths[bdf] = ep
        return ep, rp

    def hwmon(self, name, owner, readings):
        base = self.root / "class/hwmon"
        hm = base / f"hwmon{len(list(base.glob('hwmon*')))}"
        hm.mkdir(parents=True)
        put(hm / "name", name)
        (hm / "device").symlink_to(owner, target_is_directory=True)
        for key, value in readings.items():
            put(hm / key, value)
        return hm


class ProfileContractTests(unittest.TestCase):
    def test_profile_maps_all_slots_and_authenticated_provenance(self):
        fixture = json.loads((HERE / "fixtures/calibration-slots.json").read_text())
        self.assertIn("synthetic config-space bytes", fixture["provenance"])
        self.assertEqual(len(fixture["observations"]), 12)
        self.assertEqual({s["slot"] for s in fixture["slots"]}, set(PROFILE["root_keys_by_slot"]))
        self.assertEqual(PROFILE["root_keys_by_slot"], CALIBRATED_ROOT_KEYS)
        fixture_roots = {s["slot"]: (s["root"]["vendor"], s["root"]["device"], s["root"]["port"]) for s in fixture["slots"]}
        self.assertEqual(fixture_roots, CALIBRATED_ROOT_KEYS)
        self.assertEqual({o["file"]: o["sha256"] for o in fixture["observations"]},
                         {o["file"]: o["sha256"] for o in PROFILE["source_observations"]})

    def test_duplicate_root_tuple_rejected(self):
        p = json.loads(PROFILE_PATH.read_text())
        p["slots"][1]["root"] = dict(p["slots"][0]["root"])
        self.assert_invalid(p)

    def test_malformed_nested_types_duplicates_and_hashes_are_topology_errors(self):
        for mutate in (
            lambda p: p["slots"][0].update(coverage=None),
            lambda p: p["slots"][0]["root"].update(port=True),
            lambda p: p["source_observations"][0].update(sha256="z" * 64),
            lambda p: p.update(roots="12"),
            lambda p: p["slots"][0]["coverage"][0].update(scale=float("nan")),
        ):
            p = json.loads(PROFILE_PATH.read_text()); mutate(p); self.assert_invalid(p)

    def test_duplicate_json_keys_rejected(self):
        text = PROFILE_PATH.read_text().replace('"schema": "octominer-automatic-slot-profile-v1"', '"schema": "octominer-automatic-slot-profile-v1", "schema": "octominer-automatic-slot-profile-v1"', 1)
        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / "duplicate.json"; f.write_text(text)
            with self.assertRaises(TOPOLOGY.TopologyError): TOPOLOGY.load_profile(f)

    def assert_invalid(self, obj):
        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / "bad.json"; f.write_text(json.dumps(obj, allow_nan=True))
            with self.assertRaises(TOPOLOGY.TopologyError): TOPOLOGY.load_profile(f)


class CapabilityParserTests(unittest.TestCase):
    def test_capability_root_port_and_invalid_chains(self):
        self.assertEqual(TOPOLOGY.parse_root_port_config(config_bytes(21)), {"port": 21, "type": 4})
        bad = bytearray(config_bytes(21)); bad[0x34] = 0x41
        with self.assertRaises(TOPOLOGY.TopologyError): TOPOLOGY.parse_root_port_config(bad)
        bad = bytearray(config_bytes(21)); bad[0x41] = 0x40
        with self.assertRaises(TOPOLOGY.TopologyError): TOPOLOGY.parse_root_port_config(bad)
        with self.assertRaises(TOPOLOGY.TopologyError): TOPOLOGY.parse_root_port_config(b"\0" * 63)

    def test_real_captured_db10_root_port_bytes_are_type4_port21(self):
        captured = json.loads((HERE / "fixtures/db10-real-pci-config.json").read_text())
        bridge = next(b for b in captured["bridges"] if b["device"] == "0xa2eb" and b["class"] == "0x060400")
        self.assertEqual(TOPOLOGY.parse_root_port_config(bytes(bridge["config_first256_bytes"])), {"port": 21, "type": 4})


class FilesystemDiscoveryTests(unittest.TestCase):
    def test_real_sysfs_ancestry_class_masks_and_root_object_identity(self):
        with tempfile.TemporaryDirectory() as td:
            t = SysfsTree(td)
            _, rp = t.endpoint("0000:91:00.0", "DB1")
            result = TOPOLOGY.discover_endpoints(td, PROFILE)
            self.assertFalse(result["faults"])
            self.assertEqual(result["endpoints"][0]["slot"], "DB1")
            self.assertEqual(result["endpoints"][0]["root_object"], str(rp.resolve()))

    def test_all_twelve_authenticated_slots_replay_two_endpoints_each(self):
        with tempfile.TemporaryDirectory() as td:
            t = SysfsTree(td)
            for index, slot in enumerate(sorted(PROFILE["root_keys_by_slot"])):
                root_bdf = f"0000:{index:02x}:1b.0"
                t.endpoint(f"0000:{index:02x}:00.0", slot, root_bdf)
                t.endpoint(f"0000:{index:02x}:01.0", slot, root_bdf, device=0x2000 + index)
            result = TOPOLOGY.discover_endpoints(td, PROFILE)
            self.assertEqual(len(result["endpoints"]), 24)
            self.assertFalse(result["faults"])
            self.assertEqual({e["slot"] for e in result["endpoints"]}, set(PROFILE["root_keys_by_slot"]))

    def test_same_root_two_dies_valid_distinct_roots_same_slot_fault(self):
        with tempfile.TemporaryDirectory() as td:
            t = SysfsTree(td)
            t.endpoint("0000:91:00.0", "DB1", "0000:00:1b.0")
            t.endpoint("0000:92:00.0", "DB1", "0000:00:1b.0")
            self.assertEqual(len(TOPOLOGY.discover_endpoints(td, PROFILE)["faults"]), 0)
        with tempfile.TemporaryDirectory() as td:
            t = SysfsTree(td)
            t.endpoint("0000:91:00.0", "DB1", "0000:00:1b.0")
            t.endpoint("0000:92:00.0", "DB1", "0000:00:1c.0")
            self.assertTrue(any("ambiguous physical root" in x for x in TOPOLOGY.discover_endpoints(td, PROFILE)["faults"]))

    def test_narrow_integrated_exclusion_and_external_same_id(self):
        with tempfile.TemporaryDirectory() as td:
            t = SysfsTree(td)
            ep = t.root / "devices/pci0000:00/0000:00:02.0"
            for field, val in (("class", "0x030000"), ("vendor", "0x8086"), ("device", "0x1902"), ("subsystem_vendor", "0x8086"), ("subsystem_device", "0x0001")): put(ep / field, val)
            (t.devices / "0000:00:02.0").symlink_to(ep)
            external, _ = t.endpoint("0000:99:00.0", "DB1", "0000:00:1b.0", 0x8086, 0x1902)
            result = TOPOLOGY.discover_endpoints(td, PROFILE)
            self.assertEqual(len(result["excluded"]), 1)
            self.assertIn("0000:99:00.0", [e["bdf"] for e in result["endpoints"]])

    def test_snapshot_requires_each_endpoint_and_attributes_hwmon(self):
        with tempfile.TemporaryDirectory() as td:
            t = SysfsTree(td)
            a, _ = t.endpoint("0000:91:00.0", "DB1")
            b, _ = t.endpoint("0000:92:00.0", "DB1", "0000:00:1b.0", device=0x1abd)
            t.hwmon("amdgpu", a, {"temp1_input": "65000"})
            t.hwmon("amdgpu", b, {"temp1_input": "71000"})
            cfg = {"sysfs_root": td, "profile_data": PROFILE, "hwmon_names": ["amdgpu"], "sensors": ["temp1_input"]}
            snap = TOPOLOGY.read_snapshot(cfg)
            self.assertEqual(snap["slot_temps"], {"DB1": 71.0})
            self.assertEqual(snap["global_temp"], 71.0)
            t.hwmon("amdgpu", a, {"temp1_input": "80000"})
            cfg["sensors"] = ["temp1_input", "temp2_input"]
            snap = TOPOLOGY.read_snapshot(cfg)
            self.assertEqual(snap["slot_temps"], {"DB1": 80.0})
            self.assertIn("temperatures", snap["endpoints"][0])
            (Path(td) / "class/hwmon/hwmon1/temp1_input").unlink()
            snap = TOPOLOGY.read_snapshot(cfg)
            self.assertTrue(snap["sensor_faults"], "healthy die must not mask the missing die")
            put(Path(td) / "class/hwmon/hwmon0/temp1_input", "NaN")
            snap = TOPOLOGY.read_snapshot(cfg)
            self.assertTrue(any("invalid sensor value" in reason for reason in snap["sensor_faults"]))

    def snapshot_with_one_hotspot(self, td):
        tree = SysfsTree(td)
        endpoint, _ = tree.endpoint("0000:91:00.0", "DB1")
        hm = tree.hwmon("amdgpu", endpoint, {"temp1_input": "45000", "temp2_input": "140000"})
        cfg = {"sysfs_root": td, "profile_data": PROFILE, "hwmon_names": ["amdgpu"],
               "sensors": ["temp1_input", "temp2_input"]}
        return hm, cfg

    def assert_inspection_safe_fault(self, snapshot):
        self.assertTrue(snapshot["sensor_faults"])
        self.assertTrue(snapshot["faults"])
        # A valid cool sibling remains available for grace-policy decisions,
        # but the nonempty fault set prevents inspection from calling it healthy.
        self.assertEqual(snapshot["slot_temps"], {"DB1": 45.0})

    def test_filesystem_stat_denied_hotspot_faults_with_cool_sibling(self):
        with tempfile.TemporaryDirectory() as td:
            hm, cfg = self.snapshot_with_one_hotspot(td)
            hotspot = hm / "temp2_input"
            original_stat = Path.stat
            original_lstat = Path.lstat

            def denied_stat(path, *args, **kwargs):
                if path == hotspot:
                    raise PermissionError("injected stat denial")
                return original_stat(path, *args, **kwargs)

            def denied_lstat(path):
                if path == hotspot:
                    raise PermissionError("injected lstat denial")
                return original_lstat(path)

            # Python 3.13 lstat delegates to Path.stat, while 3.14 uses
            # os.lstat directly. Deny the intended path through either API.
            with mock.patch.object(Path, "stat", denied_stat), \
                    mock.patch.object(Path, "lstat", denied_lstat):
                snapshot = TOPOLOGY.read_snapshot(cfg)
            self.assert_inspection_safe_fault(snapshot)

    def test_filesystem_read_eio_hotspot_faults_with_cool_sibling(self):
        with tempfile.TemporaryDirectory() as td:
            hm, cfg = self.snapshot_with_one_hotspot(td)
            hotspot = hm / "temp2_input"
            original_read_text = Path.read_text

            def failed_read(path, *args, **kwargs):
                if path == hotspot:
                    raise OSError(5, "injected I/O error")
                return original_read_text(path, *args, **kwargs)

            with mock.patch.object(Path, "read_text", failed_read):
                snapshot = TOPOLOGY.read_snapshot(cfg)
            self.assert_inspection_safe_fault(snapshot)

    def test_dangling_configured_hotspot_symlink_faults(self):
        with tempfile.TemporaryDirectory() as td:
            hm, cfg = self.snapshot_with_one_hotspot(td)
            hotspot = hm / "temp2_input"
            hotspot.unlink()
            hotspot.symlink_to(hm / "missing-hotspot-target")
            snapshot = TOPOLOGY.read_snapshot(cfg)
            self.assert_inspection_safe_fault(snapshot)

    def test_truly_absent_optional_input_remains_allowed(self):
        with tempfile.TemporaryDirectory() as td:
            tree = SysfsTree(td)
            endpoint, _ = tree.endpoint("0000:91:00.0", "DB1")
            tree.hwmon("amdgpu", endpoint, {"temp1_input": "45000"})
            cfg = {"sysfs_root": td, "profile_data": PROFILE, "hwmon_names": ["amdgpu"],
                   "sensors": ["temp1_input", "temp2_input"]}
            snapshot = TOPOLOGY.read_snapshot(cfg)
            self.assertEqual(snapshot["sensor_faults"], [])
            self.assertEqual(snapshot["slot_temps"], {"DB1": 45.0})

    def test_filesystem_temperature_boundaries_are_inclusive_and_enforced(self):
        for value, valid in (("-20000", True), ("150000", True), ("-20001", False),
                             ("150001", False), ("abc", False), ("NaN", False),
                             ("inf", False), ("9" * 10000, False)):
            with self.subTest(value=value[:20], valid=valid), tempfile.TemporaryDirectory() as td:
                tree = SysfsTree(td)
                endpoint, _ = tree.endpoint("0000:91:00.0", "DB1")
                tree.hwmon("amdgpu", endpoint, {"temp1_input": value})
                cfg = {"sysfs_root": td, "profile_data": PROFILE, "hwmon_names": ["amdgpu"],
                       "sensors": ["temp1_input"]}
                snapshot = TOPOLOGY.read_snapshot(cfg)
                if valid:
                    self.assertEqual(snapshot["sensor_faults"], [])
                    self.assertEqual(snapshot["slot_temps"], {"DB1": int(value) / 1000.0})
                else:
                    self.assertTrue(snapshot["sensor_faults"])
                    self.assertTrue(snapshot["faults"])


if __name__ == "__main__":
    unittest.main()

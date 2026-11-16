"""Bounded, read-only PCI/sysfs topology and per-endpoint sensor reader."""
import errno
import json
import math
import re
from pathlib import Path

PROFILE_SCHEMA = "octominer-automatic-slot-profile-v1"
PCI_CAP_ID_EXP = 0x10
PCI_EXP_TYPE_ROOT_PORT = 4
PCI_EXP_LNKCAP = 0x0c
PCI_EXP_LNKCAP_PN = 0xff000000
SLOTS = {"MB1", "MB2", *(f"DB{i}" for i in range(1, 11))}
PROFILE_FIELDS = {"schema", "profile_id", "board_identity", "numbering", "roots", "fans", "slots", "source_observations", "qualification", "excluded_integrated_display"}


class TopologyError(ValueError):
    """Unsafe or malformed topology/profile data."""


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise TopologyError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _finite_number(value, *, positive=False, maximum=None):
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    try:
        finite = math.isfinite(value)
    except (OverflowError, TypeError, ValueError):
        return False
    return finite and (not positive or value > 0) and (maximum is None or value <= maximum)


def _valid_hex_id(value):
    return isinstance(value, str) and re.fullmatch(r"0x[0-9a-fA-F]{4}", value) is not None


def load_profile(path):
    try:
        obj = json.loads(Path(path).read_text(), object_pairs_hook=_unique_pairs,
                         parse_constant=lambda value: (_ for _ in ()).throw(TopologyError(f"invalid number {value}")))
    except TopologyError:
        raise
    except (OSError, ValueError, TypeError) as exc:
        raise TopologyError(f"cannot read profile: {exc}") from exc
    try:
        _validate_profile(obj)
    except TopologyError:
        raise
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise TopologyError(f"malformed profile: {exc}") from exc
    return {**obj, "root_keys_by_slot": {s["slot"]: (s["root"]["vendor"], s["root"]["device"], s["root"]["port"]) for s in obj["slots"]}}


def _validate_profile(obj):
    if not isinstance(obj, dict) or obj.get("schema") != PROFILE_SCHEMA:
        raise TopologyError("unsupported profile schema")
    if set(obj) != PROFILE_FIELDS:
        raise TopologyError("profile fields do not match versioned schema")
    if not isinstance(obj["profile_id"], str) or not obj["profile_id"].strip():
        raise TopologyError("invalid profile_id")
    if not isinstance(obj["board_identity"], dict) or not obj["board_identity"] or any(not isinstance(k, str) or not isinstance(v, str) or not v for k, v in obj["board_identity"].items()):
        raise TopologyError("invalid board identity")
    if not isinstance(obj["numbering"], str) or not obj["numbering"]:
        raise TopologyError("invalid numbering")
    if type(obj["roots"]) is not int or obj["roots"] != 12:
        raise TopologyError("roots must be integer 12")
    fans = obj["fans"]
    if not isinstance(fans, dict) or not fans:
        raise TopologyError("invalid fans")
    channels = set()
    for fan, channel in fans.items():
        if not isinstance(fan, str) or type(channel) is not int or not 0 <= channel <= 11 or channel in channels:
            raise TopologyError("invalid or duplicate fan channel")
        channels.add(channel)
    slots = obj["slots"]
    if not isinstance(slots, list) or len(slots) != 12 or any(not isinstance(s, dict) for s in slots) or {s.get("slot") for s in slots} != SLOTS:
        raise TopologyError("profile must define each of 12 physical slots exactly once")
    keys, names = set(), set()
    for slot in slots:
        if set(slot) != {"slot", "root", "coverage"} or not isinstance(slot["slot"], str) or slot["slot"] in names:
            raise TopologyError("invalid or duplicate slot")
        names.add(slot["slot"])
        root = slot["root"]
        if not isinstance(root, dict) or set(root) != {"vendor", "device", "port"} or not _valid_hex_id(root["vendor"]) or not _valid_hex_id(root["device"]) or type(root["port"]) is not int or not 1 <= root["port"] <= 255:
            raise TopologyError("invalid root tuple")
        key = (root["vendor"].lower(), root["device"].lower(), root["port"])
        if key in keys:
            raise TopologyError("duplicate root tuple")
        keys.add(key)
        coverage = slot["coverage"]
        if not isinstance(coverage, list) or not coverage:
            raise TopologyError("slot coverage must be a nonempty list")
        seen_coverage = set()
        for cover in coverage:
            if not isinstance(cover, dict) or set(cover) != {"fan", "scale", "channel"}:
                raise TopologyError("invalid fan coverage")
            if cover["fan"] not in fans or type(cover["channel"]) is not int or fans[cover["fan"]] != cover["channel"]:
                raise TopologyError("coverage references an unknown or mismatched fan channel")
            if cover["fan"] in seen_coverage:
                raise TopologyError("duplicate fan coverage in slot")
            seen_coverage.add(cover["fan"])
            if not _finite_number(cover["scale"], positive=True, maximum=1):
                raise TopologyError("invalid fan coverage scale")
    observations = obj["source_observations"]
    if not isinstance(observations, list) or len(observations) != 12:
        raise TopologyError("must retain 12 source observations")
    seen_files = set()
    for obs in observations:
        if not isinstance(obs, dict) or set(obs) != {"file", "sha256"} or not isinstance(obs["file"], str) or not obs["file"] or obs["file"] in seen_files or not isinstance(obs["sha256"], str) or re.fullmatch(r"[0-9a-f]{64}", obs["sha256"]) is None:
            raise TopologyError("invalid observation provenance or SHA256")
        seen_files.add(obs["file"])
    q = obj["qualification"]
    if not isinstance(q, dict) or set(q) != {"status", "simultaneous_multiple_cards_tested", "cross_chassis_tested"} or not isinstance(q["status"], str) or type(q["simultaneous_multiple_cards_tested"]) is not bool or type(q["cross_chassis_tested"]) is not bool:
        raise TopologyError("invalid qualification")
    ex = obj["excluded_integrated_display"]
    if not isinstance(ex, dict) or set(ex) != {"vendor", "device", "condition"} or ex != {"vendor": "0x8086", "device": "0x1902", "condition": "direct-host-root topology only"}:
        raise TopologyError("invalid integrated-display exclusion")


def parse_root_port_config(data):
    """Parse conventional PCI capabilities with strict bounds and cycle checks."""
    if not isinstance(data, (bytes, bytearray)) or not 64 <= len(data) <= 4096:
        raise TopologyError("PCI config length outside safe bounds")
    status = int.from_bytes(data[6:8], "little")
    if not status & 0x10:
        raise TopologyError("PCI capability list unavailable")
    ptr = data[0x34]
    visited = set()
    pcie = None
    while ptr:
        if ptr < 0x40 or ptr > 0xfc or ptr & 0x3 or ptr + 2 > min(len(data), 256) or ptr in visited:
            raise TopologyError("malformed or cyclic PCI capability chain")
        visited.add(ptr)
        cap_id, nxt = data[ptr], data[ptr + 1]
        if nxt and (nxt < 0x40 or nxt > 0xfc or nxt & 0x3):
            raise TopologyError("misaligned PCI capability pointer")
        if cap_id == PCI_CAP_ID_EXP:
            if pcie is not None:
                raise TopologyError("duplicate PCIe capability")
            if ptr + PCI_EXP_LNKCAP + 4 > min(len(data), 256):
                raise TopologyError("truncated PCIe capability")
            flags = int.from_bytes(data[ptr + 2:ptr + 4], "little")
            lnkcap = int.from_bytes(data[ptr + PCI_EXP_LNKCAP:ptr + PCI_EXP_LNKCAP + 4], "little")
            pcie = {"port": (lnkcap & PCI_EXP_LNKCAP_PN) >> 24, "type": (flags >> 4) & 0xf}
        ptr = nxt
    if pcie is None:
        raise TopologyError("PCIe capability not found")
    return pcie


def _read_hex(path):
    text = Path(path).read_text().strip()
    if not re.fullmatch(r"0x[0-9a-fA-F]+", text):
        raise ValueError(f"invalid hexadecimal sysfs value: {path.name}")
    return int(text, 16)


def _read_config(path):
    with open(path, "rb") as stream:
        return stream.read(256)


def discover_endpoints(sysfs_root, profile):
    root = Path(sysfs_root)
    by_key = {}
    for slot, key in profile["root_keys_by_slot"].items():
        by_key.setdefault((key[0].lower(), key[1].lower(), key[2]), []).append(slot)
    endpoints, excluded, faults = [], [], []
    for dev in sorted(root.glob("bus/pci/devices/*")):
        try:
            cls = _read_hex(dev / "class")
            if cls >> 16 != 0x03:
                continue
            vendor, device = _read_hex(dev / "vendor"), _read_hex(dev / "device")
            resolved = dev.resolve()
            ancestors = list(resolved.parents)
            bridges = []
            for anc in ancestors:
                if not re.fullmatch(r"[0-9a-fA-F]{4}:[0-9a-fA-F]{2}:[0-9a-fA-F]{2}\.[0-7]", anc.name):
                    continue
                # Missing/malformed PCI ancestors are uncertainty, not evidence
                # that a display is integrated or that a switch is the root.
                if _read_hex(anc / "class") >> 8 == 0x0604:
                    bridges.append(anc)
            direct_host = re.fullmatch(r"pci[0-9a-fA-F]{4}:[0-9a-fA-F]{2}", resolved.parent.name)
            if vendor == 0x8086 and device == 0x1902 and not bridges and direct_host:
                excluded.append({"bdf": dev.name, "reason": "exact onboard 8086:1902 direct-host display"})
                continue
            if not bridges:
                faults.append(f"display endpoint {dev.name} has no verified RootPort")
                continue
            # Only the outermost PCI bridge can identify the motherboard slot.
            # An unreadable outer root must not fall back to an internal port.
            rootport = bridges[-1]
            cap = parse_root_port_config(_read_config(rootport / "config"))
            if cap["type"] != PCI_EXP_TYPE_ROOT_PORT:
                raise TopologyError("outermost motherboard bridge is not a RootPort")
            key = (f"0x{_read_hex(rootport / 'vendor'):04x}", f"0x{_read_hex(rootport / 'device'):04x}", cap["port"])
            matches = by_key.get((key[0].lower(), key[1].lower(), key[2]), [])
            if len(matches) != 1:
                faults.append(f"display endpoint {dev.name} root tuple maps to {len(matches)} slots")
                continue
            endpoints.append({"bdf": dev.name, "vendor": vendor, "device": device,
                "subsystem_vendor": _read_hex(dev / "subsystem_vendor"), "subsystem_device": _read_hex(dev / "subsystem_device"),
                "root": key, "root_object": str(rootport.resolve()), "slot": matches[0], "device_path": str(resolved)})
        except (OSError, ValueError, TopologyError) as exc:
            faults.append(f"display endpoint {dev.name}: {exc}")
    roots_by_slot = {}
    for endpoint in endpoints:
        roots_by_slot.setdefault(endpoint["slot"], set()).add(endpoint["root_object"])
    for slot, roots in roots_by_slot.items():
        if len(roots) > 1:
            faults.append(f"ambiguous physical root identity for {slot}")
    return {"endpoints": endpoints, "excluded": excluded, "faults": faults}


def read_snapshot(cfg):
    """Discover topology and collect attributable endpoint readings; no actuation."""
    profile = cfg["profile_data"]
    found = discover_endpoints(cfg["sysfs_root"], profile)
    endpoints = found["endpoints"]
    topology_faults = list(found["faults"])
    sensor_faults = []
    if not endpoints:
        topology_faults.append("no monitored display endpoints found")
    expected_names = cfg.get("hwmon_names", ["amdgpu", "nouveau", "nvidia"])
    if not isinstance(expected_names, (list, tuple, set, frozenset)) or any(not isinstance(n, str) for n in expected_names):
        raise TopologyError("hwmon_names must be strings")
    sensor_names = cfg.get("sensors", ["temp1_input", "temp2_input", "temp3_input"])
    if not isinstance(sensor_names, (list, tuple)) or any(not isinstance(n, str) or re.fullmatch(r"temp\d+_input", n) is None for n in sensor_names):
        raise TopologyError("sensors must be tempN_input names")
    readings = {e["bdf"]: [] for e in endpoints}
    matched_hwmon = set()
    hwmon_root = Path(cfg["sysfs_root"]) / "class/hwmon"
    for hm in sorted(hwmon_root.glob("hwmon*")):
        try:
            name = (hm / "name").read_text().strip()
            if name not in expected_names:
                continue
            try:
                owner = (hm / "device").resolve(strict=True)
            except (OSError, RuntimeError):
                topology_faults.append(f"eligible hwmon {hm.name} has no resolvable device symlink")
                continue
            matches = [e for e in endpoints if Path(e["device_path"]) == owner or Path(e["device_path"]) in owner.parents]
            if len(matches) != 1:
                topology_faults.append(f"eligible hwmon {hm.name} maps to {len(matches)} monitored endpoints")
                continue
            endpoint = matches[0]
            matched_hwmon.add(endpoint["bdf"])
            for sensor in sensor_names:
                path = hm / sensor
                try:
                    path.lstat()
                except FileNotFoundError as exc:
                    if exc.errno == errno.ENOENT:
                        continue
                    sensor_faults.append(f"hwmon {hm.name} {sensor}: cannot inspect sensor path ({exc})")
                    continue
                except OSError as exc:
                    sensor_faults.append(f"hwmon {hm.name} {sensor}: cannot inspect sensor path ({exc})")
                    continue
                try:
                    raw = path.read_text().strip()
                    if re.fullmatch(r"-?\d+", raw) is None:
                        raise ValueError("not an integer millidegree value")
                    milli = int(raw)
                    if not -20000 <= milli <= 150000:
                        raise ValueError("temperature outside -20000..150000 millidegrees")
                    reading = milli / 1000.0
                    readings[endpoint["bdf"]].append(reading)
                    endpoint.setdefault("temperatures", {})[sensor] = max(reading, endpoint.get("temperatures", {}).get(sensor, -math.inf))
                except (OSError, ValueError, OverflowError) as exc:
                    sensor_faults.append(f"hwmon {hm.name} {sensor}: invalid sensor value ({exc})")
        except OSError as exc:
            topology_faults.append(f"cannot inspect hwmon {hm.name}: {exc}")
    slot_temps = {}
    valid_temps = []
    for endpoint in endpoints:
        own = readings[endpoint["bdf"]]
        if not own:
            sensor_faults.append(f"display endpoint {endpoint['bdf']} has no valid temperature reading")
            continue
        value = max(own)
        endpoint["temperature"] = value
        slot_temps[endpoint["slot"]] = max(value, slot_temps.get(endpoint["slot"], -math.inf))
        valid_temps.append(value)
    global_temp = max(valid_temps) if valid_temps else None
    return {"endpoints": endpoints, "excluded": found["excluded"], "slot_temps": slot_temps,
            "global_temp": global_temp, "faults": topology_faults + sensor_faults,
            "topology_faults": topology_faults, "sensor_faults": sensor_faults}

import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

APP_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "app"))
sys.path.insert(0, APP_DIR)

from bluetooth import bt_ops


# Logs reales recolectados en la Pi (sanitizados) para no mockear el formato.
BTMGMT_SAMPLE = """\
hci1 type 7 discovering on
hci1 dev_found: 7C:D1:C3:AA:BB:CC type LE Random rssi -66 flags 0x0000
AD flags 0x1a
tx power 4
name Mi-Reloj
UUID: 0000180f-0000-1000-8000-00805f9b34fb
manufacturer specific data 4c0010050a1cccea
hci1 dev_found: 00:1A:7D:12:34:56 type BR/EDR rssi -58 flags 0x0000
name 'Parlante JBL'
UUID: 0000110b-0000-1000-8000-00805f9b34fb
hci1 dev_found: 7C:D1:C3:AA:BB:CC type BR/EDR rssi -70 flags 0x0000
hci1 dev_found: 44:22:11:AA:BB:CC type LE Public rssi -40 flags 0x0000
name DevNoRSSI
"""

BTCTL_INFO_SAMPLE = """\
Device 7C:D1:C3:AA:BB:CC (random)
	Name: Mi-Reloj
	Alias: Mi-Reloj
	Appearance: Generic Watch (0x00C0)
	Icon: audio-card
	Paired: no
	Trusted: no
	Blocked: no
	Connected: no
	LegacyPairing: no
	UUID: Generic Access Profile    (00001800-0000-1000-8000-00805f9b34fb)
	UUID: Battery Service           (0000180f-0000-1000-8000-00805f9b34fb)
	RSSI: -62
	ManufacturerData.Key: 0x004c
	ManufacturerData.Value: 10 05 0a 1c cc ea
"""


class ParseBtmgmtFindTests(unittest.TestCase):
    def test_parses_three_distinct_devices(self):
        devs = bt_ops.parse_btmgmt_find(BTMGMT_SAMPLE)
        macs = sorted(d["mac"] for d in devs)
        self.assertEqual(macs, ["00:1A:7D:12:34:56",
                                "44:22:11:AA:BB:CC",
                                "7C:D1:C3:AA:BB:CC"])

    def test_dual_kind_when_same_mac_seen_as_le_and_classic(self):
        devs = {d["mac"]: d for d in bt_ops.parse_btmgmt_find(BTMGMT_SAMPLE)}
        self.assertEqual(devs["7C:D1:C3:AA:BB:CC"]["kind"], "dual")
        self.assertEqual(devs["00:1A:7D:12:34:56"]["kind"], "classic")
        self.assertEqual(devs["44:22:11:AA:BB:CC"]["kind"], "le")

    def test_captures_rssi_name_tx_power_and_uuids(self):
        devs = {d["mac"]: d for d in bt_ops.parse_btmgmt_find(BTMGMT_SAMPLE)}
        watch = devs["7C:D1:C3:AA:BB:CC"]
        # el ultimo rssi visto gana (LE -66, luego BR/EDR -70)
        self.assertIn(watch["rssi"], (-66, -70))
        self.assertEqual(watch["name"], "Mi-Reloj")
        self.assertEqual(watch["tx_power"], 4)
        self.assertIn("0000180f-0000-1000-8000-00805f9b34fb", watch["uuids"])
        self.assertTrue(watch["manufacturer_data"].startswith("4c0010050a1cccea"))

    def test_handles_quoted_names_and_missing_fields(self):
        devs = {d["mac"]: d for d in bt_ops.parse_btmgmt_find(BTMGMT_SAMPLE)}
        self.assertEqual(devs["00:1A:7D:12:34:56"]["name"], "Parlante JBL")
        # Un device sin RSSI explicito mantiene rssi
        self.assertEqual(devs["44:22:11:AA:BB:CC"]["rssi"], -40)

    def test_empty_input_returns_empty_list(self):
        self.assertEqual(bt_ops.parse_btmgmt_find(""), [])
        self.assertEqual(bt_ops.parse_btmgmt_find(None), [])

    def test_classifies_addr_type(self):
        devs = {d["mac"]: d for d in bt_ops.parse_btmgmt_find(BTMGMT_SAMPLE)}
        self.assertEqual(devs["44:22:11:AA:BB:CC"]["addr_type"], "public")
        # el BR/EDR del parlante tambien es public
        self.assertEqual(devs["00:1A:7D:12:34:56"]["addr_type"], "public")


class ParseBluetoothctlInfoTests(unittest.TestCase):
    def test_extracts_header_and_fields(self):
        d = bt_ops.parse_bluetoothctl_info(BTCTL_INFO_SAMPLE)
        self.assertEqual(d["mac"], "7C:D1:C3:AA:BB:CC")
        self.assertEqual(d["addr_type"], "random")
        self.assertEqual(d["name"], "Mi-Reloj")
        self.assertEqual(d["alias"], "Mi-Reloj")
        self.assertTrue(d["appearance"].startswith("Generic Watch"))
        self.assertEqual(d["icon"], "audio-card")
        self.assertEqual(d["rssi"], -62)
        self.assertEqual(d["manufacturer_key"], "0x004c")
        self.assertFalse(d["paired"])
        self.assertFalse(d["connected"])

    def test_captures_uuids_with_labels(self):
        d = bt_ops.parse_bluetoothctl_info(BTCTL_INFO_SAMPLE)
        joined = "\n".join(d["uuids"])
        self.assertIn("Battery Service", joined)
        self.assertIn("00001800-0000-1000-8000-00805f9b34fb", joined)


class MergeTests(unittest.TestCase):
    def test_merge_fills_missing_appearance_and_uuids(self):
        base = {"mac": "7C:D1:C3:AA:BB:CC", "name": "", "rssi": None,
                "uuids": ["0000180f-0000-1000-8000-00805f9b34fb"]}
        info = {"name": "Mi-Reloj", "appearance": "Generic Watch (0x00C0)",
                "rssi": -62,
                "uuids": ["00001800-0000-1000-8000-00805f9b34fb|GAP",
                          "0000180f-0000-1000-8000-00805f9b34fb|Battery Service"]}
        merged = bt_ops.merge_bt_devices(base, info)
        self.assertEqual(merged["name"], "Mi-Reloj")
        self.assertEqual(merged["rssi"], -62)
        self.assertTrue(any("Battery Service" in u for u in merged["uuids"]))
        self.assertTrue(any("GAP" in u for u in merged["uuids"]))

    def test_merge_does_not_overwrite_better_name(self):
        base = {"mac": "AA:BB:CC:DD:EE:FF", "name": "LongerDeviceName",
                "uuids": []}
        info = {"name": "A", "uuids": []}
        merged = bt_ops.merge_bt_devices(base, info)
        self.assertEqual(merged["name"], "LongerDeviceName")


class VendorTests(unittest.TestCase):
    def test_random_mac_labelled_as_aleatoria(self):
        # 0x7C -> bits 01111100 -> 0x02 clear, no es random
        # 0x7D -> 01111101 -> idem; probamos uno con bit 0x02 set
        self.assertEqual(bt_ops._vendor_for("7A:BB:CC:DD:EE:FF"), "(aleatoria)")

    def test_builtin_realtek_resolves(self):
        self.assertEqual(bt_ops._vendor_for("FC:22:1C:50:2F:D4"), "Realtek")

    def test_short_or_empty_mac_is_empty(self):
        self.assertEqual(bt_ops._vendor_for(""), "")
        self.assertEqual(bt_ops._vendor_for("ab"), "")


class PowerOnTests(unittest.TestCase):
    def test_power_on_calls_sudo_btmgmt_with_iface_index(self):
        calls = []

        def fake_run(cmd, timeout=15):
            calls.append(cmd)
            return SimpleNamespace(returncode=0, stdout="hci1 power on complete",
                                   stderr="")

        with patch.object(bt_ops, "_run", side_effect=fake_run):
            ok, msg = bt_ops.power_on("hci1")
        self.assertTrue(ok)
        # algun call tiene que ser el btmgmt power on con --index 1
        self.assertTrue(any(
            bt_ops.BTMGMT in c and 'power' in c and 'on' in c and '1' in c
            for c in calls
        ))

    def test_power_on_reports_failure_when_sudo_denies(self):
        def fake_run(cmd, timeout=15):
            return SimpleNamespace(returncode=1, stdout="",
                                   stderr="Permission Denied")

        with patch.object(bt_ops, "_run", side_effect=fake_run):
            ok, msg = bt_ops.power_on("hci1")
        self.assertFalse(ok)
        self.assertIn("Permission Denied", msg)


class ScanIntegrationTests(unittest.TestCase):
    def test_scan_returns_parsed_devices_without_touching_hardware(self):
        """Mock del subprocess de btmgmt y bluetoothctl: el scan reconstruye la
        lista y la enriquece sin apagar nada, y nunca escribe en DB real."""
        def fake_run(cmd, timeout=15):
            # power_on -> btmgmt power on
            if BTMGMT_IN(cmd) and 'power' in cmd:
                return SimpleNamespace(returncode=0, stdout="", stderr="")
            if BTMGMT_IN(cmd) and 'find' in cmd:
                return SimpleNamespace(returncode=0, stdout=BTMGMT_SAMPLE,
                                       stderr="")
            if cmd and cmd[0] == bt_ops.BLUETOOTHCTL:
                mac = cmd[-1]
                if mac == "7C:D1:C3:AA:BB:CC":
                    return SimpleNamespace(returncode=0,
                                           stdout=BTCTL_INFO_SAMPLE, stderr="")
                return SimpleNamespace(returncode=0, stdout="", stderr="")
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        def BTMGMT_IN(cmd):
            return bt_ops.BTMGMT in cmd

        with patch.object(bt_ops, "_run", side_effect=fake_run), \
             patch.object(bt_ops, "log_bluetooth_batch", lambda *a, **kw: None):
            devs, err = bt_ops.scan("hci1", 3, "dual")

        self.assertIsNone(err)
        self.assertEqual(len(devs), 3)
        watch = next(d for d in devs if d["mac"] == "7C:D1:C3:AA:BB:CC")
        self.assertEqual(watch["name"], "Mi-Reloj")
        self.assertTrue(any("Battery Service" in u for u in watch["uuids"]))
        # vendor depende del OUI local: en Windows (sin ieee-data) queda vacio,
        # en la Pi (con data/oui.txt) resuelve "Apple, Inc." para 7C:D1:C3. Lo
        # importante es que la clave existe y es un string.
        self.assertIsInstance(watch.get("vendor"), str)
        parlante = next(d for d in devs if d["mac"] == "00:1A:7D:12:34:56")
        self.assertEqual(parlante["kind"], "classic")


if __name__ == "__main__":
    unittest.main()

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
    def test_power_on_calls_sudo_hciconfig_up(self):
        calls = []

        def fake_run(cmd, timeout=15):
            calls.append(cmd)
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        with patch.object(bt_ops, "_run", side_effect=fake_run):
            ok, msg = bt_ops.power_on("hci1")
        self.assertTrue(ok)
        # hubo una call con hciconfig hci1 up
        self.assertTrue(any(
            bt_ops.HCICONFIG in c and 'hci1' in c and 'up' in c
            for c in calls
        ))

    def test_power_on_reports_failure_when_hciconfig_rejects(self):
        def fake_run(cmd, timeout=15):
            if bt_ops.HCICONFIG in cmd:
                return SimpleNamespace(returncode=1, stdout="",
                                       stderr="Can't init device hci1: Operation not permitted")
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        with patch.object(bt_ops, "_run", side_effect=fake_run):
            ok, msg = bt_ops.power_on("hci1")
        self.assertFalse(ok)
        self.assertIn("Operation not permitted", msg)


BCTL_SCAN_SAMPLE = """\
Agent registered
[\x1b[0;93mCHG\x1b[0m] Controller FC:22:1C:50:2F:D4 Pairable: yes
[bluetooth]# select B8:27:EB:B3:66:DB
Controller B8:27:EB:B3:66:DB geo22 [default]
[bluetooth]# scan on
SetDiscoveryFilter success
Discovery started
[\x1b[0;93mCHG\x1b[0m] Controller B8:27:EB:B3:66:DB Discovering: yes
[\x1b[0;92mNEW\x1b[0m] Device 60:45:C9:BE:F8:D0 LAP-1MJLWG3
[\x1b[0;93mCHG\x1b[0m] Device 60:45:C9:BE:F8:D0 RSSI: 0xffffffbc (-68)
[\x1b[0;92mNEW\x1b[0m] Device 38:3B:C5:4A:89:BF 38-3B-C5-4A-89-BF
[\x1b[0;93mCHG\x1b[0m] Device 38:3B:C5:4A:89:BF RSSI: 0xffffffa8 (-88)
[\x1b[0;93mCHG\x1b[0m] Device 38:3B:C5:4A:89:BF Name: Mi-Reloj
[\x1b[0;91mDEL\x1b[0m] Device 11:22:33:44:55:66 11-22-33-44-55-66
[bluetooth]# scan off
Discovery stopped
[bluetooth]# devices
Device 60:45:C9:BE:F8:D0 LAP-1MJLWG3
Device 38:3B:C5:4A:89:BF Mi-Reloj
Device 00:04:EA:10:20:40 TV-Living
[bluetooth]# quit
"""


class ParseBluetoothctlScanTests(unittest.TestCase):
    def test_parses_three_distinct_devices(self):
        devs = {d["mac"]: d for d in bt_ops.parse_bluetoothctl_scan(BCTL_SCAN_SAMPLE)}
        self.assertIn("60:45:C9:BE:F8:D0", devs)
        self.assertIn("38:3B:C5:4A:89:BF", devs)
        self.assertIn("00:04:EA:10:20:40", devs)

    def test_rssi_captured_from_chg_event(self):
        devs = {d["mac"]: d for d in bt_ops.parse_bluetoothctl_scan(BCTL_SCAN_SAMPLE)}
        self.assertEqual(devs["60:45:C9:BE:F8:D0"]["rssi"], -68)
        self.assertEqual(devs["38:3B:C5:4A:89:BF"]["rssi"], -88)

    def test_name_from_new_or_chg_name_event(self):
        devs = {d["mac"]: d for d in bt_ops.parse_bluetoothctl_scan(BCTL_SCAN_SAMPLE)}
        # [NEW] trajo nombre real
        self.assertEqual(devs["60:45:C9:BE:F8:D0"]["name"], "LAP-1MJLWG3")
        # [NEW] trajo MAC con guiones (placeholder) -> [CHG] Name la resuelve
        self.assertEqual(devs["38:3B:C5:4A:89:BF"]["name"], "Mi-Reloj")

    def test_mac_placeholder_name_not_leaked(self):
        """Un [NEW] con 'name' igual a la MAC con guiones no debe guardarse
        como name real (bluetoothctl lo usa como placeholder)."""
        sample = "[NEW] Device AA:BB:CC:DD:EE:FF AA-BB-CC-DD-EE-FF\n"
        devs = bt_ops.parse_bluetoothctl_scan(sample)
        self.assertEqual(devs[0]["name"], "")

    def test_empty_input_returns_empty_list(self):
        self.assertEqual(bt_ops.parse_bluetoothctl_scan(""), [])
        self.assertEqual(bt_ops.parse_bluetoothctl_scan(None), [])

    def test_info_header_is_not_treated_as_device_entry(self):
        """La linea `Device MAC (public)` del bloque info no debe cargar
        name='(public)' en el listado."""
        sample = ("[NEW] Device 00:04:EA:10:20:40 00-04-EA-10-20-40\n"
                  "Device 00:04:EA:10:20:40 (public)\n"
                  "\tAlias: HP-Printer\n")
        devs = bt_ops.parse_bluetoothctl_scan(sample)
        self.assertEqual(len(devs), 1)
        self.assertNotIn("public", devs[0]["name"])
        self.assertNotIn("(", devs[0]["name"])


INTERACTIVE_SESSION_SAMPLE = """\
[bluetooth]# select B8:27:EB:B3:66:DB
Controller B8:27:EB:B3:66:DB geo22 [default]
[bluetooth]# scan on
SetDiscoveryFilter success
Discovery started
[\x1b[0;93mCHG\x1b[0m] Controller B8:27:EB:B3:66:DB Discovering: yes
[\x1b[0;92mNEW\x1b[0m] Device 60:45:C9:BE:F8:D0 LAP-1MJLWG3
[\x1b[0;92mNEW\x1b[0m] Device 38:3B:C5:4A:89:BF 38-3B-C5-4A-89-BF
[bluetooth]# info 60:45:C9:BE:F8:D0
Device 60:45:C9:BE:F8:D0 (public)
	Name: LAP-1MJLWG3
	Alias: LAP-1MJLWG3
	Class: 0x002a010c
	Icon: computer
	Paired: no
	Trusted: no
	Blocked: no
	Connected: no
	LegacyPairing: no
	UUID: Generic Access Profile    (00001800-0000-1000-8000-00805f9b34fb)
	UUID: PnP Information           (00001200-0000-1000-8000-00805f9b34fb)
	RSSI: 0xffffffbc (-68)
[bluetooth]# info 38:3B:C5:4A:89:BF
Device 38:3B:C5:4A:89:BF (random)
	Name: Mi-Reloj
	Alias: Mi-Reloj
	Appearance: Generic Watch (0x00C0)
	Icon: audio-card
	Paired: no
	Trusted: no
	Blocked: no
	Connected: no
	LegacyPairing: no
	UUID: Battery Service           (0000180f-0000-1000-8000-00805f9b34fb)
	RSSI: 0xffffffa8 (-88)
[bluetooth]# scan off
Discovery stopped
[bluetooth]# devices
Device 60:45:C9:BE:F8:D0 LAP-1MJLWG3
Device 38:3B:C5:4A:89:BF Mi-Reloj
[bluetooth]# quit
"""


class SplitInfoBlocksTests(unittest.TestCase):
    def test_splits_two_distinct_info_blocks(self):
        blocks = bt_ops._split_info_blocks(INTERACTIVE_SESSION_SAMPLE)
        self.assertEqual(set(blocks.keys()),
                         {"60:45:C9:BE:F8:D0", "38:3B:C5:4A:89:BF"})

    def test_block_text_contains_fields(self):
        blocks = bt_ops._split_info_blocks(INTERACTIVE_SESSION_SAMPLE)
        b = blocks["60:45:C9:BE:F8:D0"]
        self.assertIn("Name: LAP-1MJLWG3", b)
        self.assertIn("RSSI: 0xffffffbc (-68)", b)
        self.assertIn("PnP Information", b)

    def test_empty_input_yields_empty_dict(self):
        self.assertEqual(bt_ops._split_info_blocks(""), {})

    def test_prompt_closes_block_not_bleeds_into_next(self):
        sample = ("Device AA:BB:CC:DD:EE:FF (public)\n"
                  "\tName: A\n"
                  "[bluetooth]# ignored\n"
                  "Device 11:22:33:44:55:66 (random)\n"
                  "\tName: B\n")
        blocks = bt_ops._split_info_blocks(sample)
        self.assertEqual(set(blocks), {"AA:BB:CC:DD:EE:FF", "11:22:33:44:55:66"})
        self.assertIn("Name: A", blocks["AA:BB:CC:DD:EE:FF"])
        self.assertIn("Name: B", blocks["11:22:33:44:55:66"])
        self.assertNotIn("Name: B", blocks["AA:BB:CC:DD:EE:FF"])


class ParseBluetoothctlInfoRssiTests(unittest.TestCase):
    def test_rssi_with_hex_prefix_and_parens(self):
        d = bt_ops.parse_bluetoothctl_info("Device AA:BB:CC:DD:EE:FF (public)\n\tRSSI: 0xffffffbc (-68)\n")
        self.assertEqual(d["rssi"], -68)

    def test_txpower_in_parens(self):
        d = bt_ops.parse_bluetoothctl_info("Device AA:BB:CC:DD:EE:FF (public)\n\tTxPower: 0x08 (8)\n")
        self.assertEqual(d["tx_power"], 8)

    def test_plain_integer_rssi_still_works(self):
        d = bt_ops.parse_bluetoothctl_info("Device AA:BB:CC:DD:EE:FF (public)\n\tRSSI: -72\n")
        self.assertEqual(d["rssi"], -72)

    def test_mac_placeholder_alias_is_discarded(self):
        """bluez pone 'AA-BB-CC-DD-EE-FF' como Alias cuando no hay nombre real.
        Nuestro parser debe descartarlo para no mostrar 'nombres' que son solo
        la MAC repetida en la UI/DB."""
        block = ("Device 42:4F:BA:BC:3D:DB (random)\n"
                 "\tAlias: 42-4F-BA-BC-3D-DB\n")
        d = bt_ops.parse_bluetoothctl_info(block)
        self.assertEqual(d["name"], "")
        self.assertEqual(d["alias"], "")

    def test_real_name_is_kept(self):
        block = ("Device 60:45:C9:BE:F8:D0 (public)\n"
                 "\tName: LAP-1MJLWG3\n"
                 "\tAlias: LAP-1MJLWG3\n")
        d = bt_ops.parse_bluetoothctl_info(block)
        self.assertEqual(d["name"], "LAP-1MJLWG3")
        self.assertEqual(d["alias"], "LAP-1MJLWG3")


SDPTOOL_SAMPLE = """\
Browsing 00:1A:7D:12:34:56 ...
Service Name: Audio Sink
Service RecHandle: 0x10001
Service Class ID List:
  "Audio Sink" (0x110b)
Protocol Descriptor List:
  "L2CAP" (0x0100)
    PSM: 25

Service Name: Hands-Free unit
Service RecHandle: 0x10002
Service Class ID List:
  "Handsfree" (0x111e)
  "Generic Audio" (0x1203)
"""


class ParseSdpToolTests(unittest.TestCase):
    def test_parses_two_services(self):
        svs = bt_ops.parse_sdptool_browse(SDPTOOL_SAMPLE)
        self.assertEqual(len(svs), 2)
        self.assertEqual(svs[0]["name"], "Audio Sink")
        self.assertEqual(svs[0]["rec_handle"], "0x10001")
        self.assertEqual(svs[1]["name"], "Hands-Free unit")

    def test_captures_classes(self):
        svs = bt_ops.parse_sdptool_browse(SDPTOOL_SAMPLE)
        self.assertEqual(svs[0]["classes"][0], ("Audio Sink", "0x110b"))
        names = [c[0] for c in svs[1]["classes"]]
        self.assertIn("Handsfree", names)

    def test_empty_input_returns_empty_list(self):
        self.assertEqual(bt_ops.parse_sdptool_browse(""), [])
        self.assertEqual(bt_ops.parse_sdptool_browse(None), [])


L2PING_SAMPLE = """\
Ping: AA:BB:CC:DD:EE:FF from DD:EE:FF:00:11:22 (data size 44) ...
44 bytes from AA:BB:CC:DD:EE:FF id 0 time 12.21ms
44 bytes from AA:BB:CC:DD:EE:FF id 1 time 11.07ms
44 bytes from AA:BB:CC:DD:EE:FF id 2 time 14.80ms
3 sent, 3 received, 0% loss
"""

L2PING_LOSSY_SAMPLE = """\
44 bytes from AA:BB:CC:DD:EE:FF id 0 time 11.00ms
5 sent, 1 received, 80% loss
"""


class ParseL2pingTests(unittest.TestCase):
    def test_captures_stats_from_success(self):
        s = bt_ops.parse_l2ping_output(L2PING_SAMPLE)
        self.assertEqual(s["sent"], 3)
        self.assertEqual(s["received"], 3)
        self.assertEqual(s["loss_pct"], 0)
        self.assertEqual(len(s["samples"]), 3)
        self.assertAlmostEqual(s["avg_ms"], 12.69, places=2)
        self.assertEqual(s["min_ms"], 11.07)
        self.assertEqual(s["max_ms"], 14.80)

    def test_captures_stats_with_packet_loss(self):
        s = bt_ops.parse_l2ping_output(L2PING_LOSSY_SAMPLE)
        self.assertEqual(s["sent"], 5)
        self.assertEqual(s["received"], 1)
        self.assertEqual(s["loss_pct"], 80)

    def test_empty_input_zero_sent(self):
        s = bt_ops.parse_l2ping_output("")
        self.assertEqual(s["sent"], 0)
        self.assertEqual(s["received"], 0)
        self.assertEqual(s["samples"], [])


GATT_SAMPLE = """\
[bluetooth]# connect AA:BB:CC:DD:EE:FF
Attempting to connect to AA:BB:CC:DD:EE:FF
[\x1b[0;93mCHG\x1b[0m] Device AA:BB:CC:DD:EE:FF Connected: yes
[bluetooth]# menu gatt
[bluetooth:gatt]# list-attributes
Primary Service (Handle 0x0001)
	/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF/service0001
	UUID: Generic Access Profile    (00001800-0000-1000-8000-00805f9b34fb)
Characteristic (Handle 0x0003)
	/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF/service0001/char0002
	UUID: Device Name    (00002a00-0000-1000-8000-00805f9b34fb)
Primary Service (Handle 0x0010)
	/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF/service0010
	UUID: Battery Service    (0000180f-0000-1000-8000-00805f9b34fb)
Characteristic (Handle 0x0012)
	/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF/service0010/char0011
	UUID: Battery Level    (00002a19-0000-1000-8000-00805f9b34fb)
"""


class ParseGattTests(unittest.TestCase):
    def test_parses_services_and_characteristics(self):
        attrs = bt_ops.parse_bluetoothctl_gatt(GATT_SAMPLE)
        svc = [a for a in attrs if a["kind"] == "service"]
        chr_ = [a for a in attrs if a["kind"] == "characteristic"]
        self.assertEqual(len(svc), 2)
        self.assertEqual(len(chr_), 2)
        self.assertEqual(svc[0]["uuid"], "00001800-0000-1000-8000-00805f9b34fb")
        self.assertTrue(chr_[0]["path"].endswith("char0002"))
        self.assertEqual(chr_[1]["label"], "Battery Level")

    def test_empty_input_returns_empty_list(self):
        self.assertEqual(bt_ops.parse_bluetoothctl_gatt(""), [])


class DeviceTypeTests(unittest.TestCase):
    def test_classifies_by_bluez_icon(self):
        self.assertEqual(bt_ops._device_type("computer", "", "", ""), "PC")
        self.assertEqual(bt_ops._device_type("phone", "", "", ""), "Celular")
        self.assertEqual(bt_ops._device_type("audio-card", "", "", ""), "Audio")
        self.assertEqual(bt_ops._device_type("input-keyboard", "", "", ""), "Teclado")

    def test_classifies_by_appearance_watch(self):
        self.assertEqual(
            bt_ops._device_type("", "", "Generic Watch (0x00C0)", ""),
            "Reloj",
        )


class BlePayloadTests(unittest.TestCase):
    def test_hci_command_uses_absolute_hcitool_path(self):
        calls = []

        def fake_run(cmd, timeout=15):
            calls.append(cmd)
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        with patch.object(bt_ops, "_run", side_effect=fake_run):
            bt_ops._hci("hci0", 0x000A, [0x00])

        self.assertTrue(calls)
        self.assertEqual(calls[0][0:4],
                         ["/usr/bin/sudo", "-n", "/usr/bin/hcitool", "-i"])
        self.assertEqual(calls[0][4], "hci0")
        self.assertIn("0x08", calls[0])
        self.assertIn("0x000a", calls[0])
        self.assertIn("00", calls[0])

    def test_loads_target_payload_files(self):
        for mode in ("apple", "google", "microsoft", "samsung"):
            payloads = bt_ops._load_payloads(mode)
            self.assertTrue(payloads, f"faltan payloads para {mode}")

    def test_build_payload_never_exceeds_legacy_limit(self):
        item = {
            "name": "Nombre-Largo-Sin-Recursos-Externos",
            "cid": 0x004C,
            "data": b"\x12\x13\x14",
        }
        payload = bt_ops._build_adv_payload(item)
        self.assertLessEqual(len(payload), 31)
        self.assertGreaterEqual(len(payload), 3)
        self.assertEqual(payload[:3], b"\x02\x01\x06")


class BtsnoopPacketCountTests(unittest.TestCase):
    def test_counts_complete_records_and_rejects_bad_header(self):
        import struct
        import tempfile

        with tempfile.NamedTemporaryFile(delete=False) as f:
            path = f.name
            try:
                f.write(b"btsnoop\x00" + b"\x00" * 8)
                record = struct.pack(">IIIIQ", 0, 4, 1, 0, 1)
                f.write(record + b"\x01\x02\x03\x04")
                f.write(record + b"\x05\x06\x07\x08")
                f.flush()
                self.assertEqual(bt_ops._btsnoop_count_packets(path), 2)
            finally:
                f.close()
                os.unlink(path)

        with tempfile.NamedTemporaryFile(delete=False) as f:
            bad_path = f.name
            try:
                f.write(b"not-btsnoop")
                f.flush()
                self.assertIsNone(bt_ops._btsnoop_count_packets(bad_path))
            finally:
                f.close()
                os.unlink(bad_path)


class LoadBleNamesTests(unittest.TestCase):
    def test_loads_names_from_repo_data_file(self):
        """El archivo data/ble_names.txt debe traer al menos 10 nombres."""
        names = bt_ops._load_ble_names()
        self.assertGreaterEqual(len(names), 10)
        # Fallback kick-in si no estuviera: 3 items. 10+ asegura que se leyo.
        self.assertTrue(all(isinstance(n, str) and n for n in names))


class BtmonCaptureTests(unittest.TestCase):
    def test_capture_reports_size_when_file_created(self):
        """Con _run mock + archivo pre-escrito, btmon_capture devuelve size y path."""
        tmp_dir = os.path.join(APP_DIR, "..", "data", "captures")
        os.makedirs(tmp_dir, exist_ok=True)

        def fake_run(cmd, timeout=15):
            # Simular que btmon escribio un archivo chico en `path` (-w arg).
            try:
                idx = cmd.index("-w")
                path = cmd[idx + 1]
                with open(path, "wb") as f:
                    f.write(b"\x00" * 128)
            except Exception:
                pass
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        with patch.object(bt_ops, "_run", side_effect=fake_run), \
             patch.object(bt_ops, "power_on", return_value=(True, "")), \
             patch.object(bt_ops, "log_bt_attack", lambda *a, **kw: None):
            data, err = bt_ops.btmon_capture("hci0", 2)
        self.assertIsNone(err)
        self.assertGreaterEqual(data["size"], 128)
        self.assertTrue(data["path"].endswith(".btsnoop"))

    def test_capture_reports_error_on_zero_size(self):
        def fake_run(cmd, timeout=15):
            return SimpleNamespace(returncode=1, stdout="",
                                   stderr="Can't open hci device")
        with patch.object(bt_ops, "_run", side_effect=fake_run), \
             patch.object(bt_ops, "power_on", return_value=(True, "")), \
             patch.object(bt_ops, "log_bt_attack", lambda *a, **kw: None):
            data, err = bt_ops.btmon_capture("hci0", 2)
        self.assertEqual(data["size"], 0)
        self.assertIn("hci device", err)


class ScanDispatchTests(unittest.TestCase):
    def test_scan_fails_fast_when_power_on_fails(self):
        """Si hciconfig up falla, scan devuelve error sin tocar bluetoothctl."""
        with patch.object(bt_ops, "power_on", return_value=(False, "no sudo")):
            devs, err = bt_ops.scan("hci1", 3, "dual")
        self.assertIsNone(devs)
        self.assertIn("no sudo", err)

    def test_scan_fails_when_bd_address_unknown(self):
        with patch.object(bt_ops, "power_on", return_value=(True, "")), \
             patch.object(bt_ops, "_bd_address", return_value=""):
            devs, err = bt_ops.scan("hci1", 3, "dual")
        self.assertIsNone(devs)
        self.assertIn("BD address", err)


if __name__ == "__main__":
    unittest.main()

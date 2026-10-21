import os
import sys
import unittest

APP_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "app"))
sys.path.insert(0, APP_DIR)

from wifi import wifi_ops


class WashParsingTests(unittest.TestCase):
    def test_parses_aps_and_skips_headers(self):
        text = (
            "BSSID               Ch  dBm  WPS  Lck  ESSID\n"
            "--------------------------------------------------\n"
            "AA:BB:CC:DD:EE:FF    1  -60  2.0  No   MiRed\n"
            "11:22:33:44:55:66    6  -72  1.0  Yes  Red Con Espacios\n"
        )
        aps = wifi_ops.parse_wash_output(text)
        self.assertEqual(len(aps), 2)
        self.assertEqual(aps[0]["bssid"], "AA:BB:CC:DD:EE:FF")
        self.assertEqual(aps[0]["channel"], 1)
        self.assertFalse(aps[0]["locked"])
        self.assertEqual(aps[1]["channel"], 6)
        self.assertTrue(aps[1]["locked"])
        self.assertEqual(aps[1]["essid"], "Red Con Espacios")

    def test_empty_output_is_empty_list(self):
        self.assertEqual(wifi_ops.parse_wash_output(""), [])
        self.assertEqual(wifi_ops.parse_wash_output(None), [])


class ReaverParsingTests(unittest.TestCase):
    def test_extracts_pin_and_psk(self):
        out = (
            "[+] Running reaver...\n"
            "[+] WPS PIN: '12345670'\n"
            "[+] WPA PSK: 'mi-clave-secreta'\n"
            "[+] AP SSID: 'MiRed'\n"
        )
        parsed = wifi_ops.parse_reaver_output(out)
        self.assertEqual(parsed["pin"], "12345670")
        self.assertEqual(parsed["psk"], "mi-clave-secreta")

    def test_pixiedust_lowercase_pin(self):
        out = "[Pixie-Dust] [+] WPS pin: 01234567\n"
        self.assertEqual(wifi_ops.parse_reaver_output(out)["pin"], "01234567")

    def test_no_match_returns_none(self):
        parsed = wifi_ops.parse_reaver_output("no encontro nada util aqui")
        self.assertIsNone(parsed["pin"])
        self.assertIsNone(parsed["psk"])


if __name__ == "__main__":
    unittest.main()

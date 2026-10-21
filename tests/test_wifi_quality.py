import os
import sys
import io
import unittest
from contextlib import redirect_stderr
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

APP_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "app"))
sys.path.insert(0, APP_DIR)

from wifi.wifi_scan import _parse_scan_output, log_scan_results
from wifi import wifi_ops


class WifiScanParsingTests(unittest.TestCase):
    def test_keeps_access_points_with_duplicate_ssids(self):
        networks = {}
        scan_output = """\
BSS 00:11:22:33:44:55(on wlan1)
\tfreq: 2412.0
\tsignal: -45.00 dBm
\tcapability: ESS Privacy
\tSSID: Lab
\tRSN:
\t\t * Authentication suites: PSK
BSS 00:11:22:33:44:66(on wlan1)
\tfreq: 5180.0
\tsignal: -51.00 dBm
\tcapability: ESS Privacy
\tSSID: Lab
\tRSN:
\t\t * Authentication suites: PSK
"""

        _parse_scan_output(scan_output, networks)

        self.assertEqual(set(networks), {"00:11:22:33:44:55", "00:11:22:33:44:66"})
        self.assertEqual(networks["00:11:22:33:44:55"]["channel"], 1)
        self.assertEqual(networks["00:11:22:33:44:66"]["channel"], 36)

    def test_keeps_strongest_observation_for_same_bssid(self):
        networks = {}
        _parse_scan_output(
            "BSS 00:11:22:33:44:55(on wlan1)\n"
            "\tfreq: 2412.0\n\tsignal: -70.0 dBm\n\tSSID: Lab\n"
            "BSS 00:11:22:33:44:55(on wlan1)\n"
            "\tfreq: 2412.0\n\tsignal: -40.0 dBm\n\tSSID: Lab\n",
            networks,
        )

        self.assertEqual(len(networks), 1)
        self.assertEqual(networks["00:11:22:33:44:55"]["signal"], -40.0)

    def test_classifies_open_wep_wpa2_wpa3_and_transition(self):
        networks = {}
        scan_output = """\
BSS 00:00:00:00:00:01(on wlan1)
\tfreq: 2412.0
\tSSID: Open
BSS 00:00:00:00:00:02(on wlan1)
\tfreq: 2412.0
\tcapability: ESS Privacy
\tSSID: Legacy
BSS 00:00:00:00:00:03(on wlan1)
\tfreq: 2412.0
\tcapability: ESS Privacy
\tSSID: WPA2
\tRSN:
\t\t * Authentication suites: PSK
BSS 00:00:00:00:00:04(on wlan1)
\tfreq: 2412.0
\tcapability: ESS Privacy
\tSSID: WPA3
\tRSN:
\t\t * Authentication suites: SAE
BSS 00:00:00:00:00:05(on wlan1)
\tfreq: 2412.0
\tcapability: ESS Privacy
\tSSID: Transition
\tRSN:
\t\t * Authentication suites: PSK
\t\t * Authentication suites: SAE
"""

        _parse_scan_output(scan_output, networks)

        self.assertEqual(networks["00:00:00:00:00:01"]["security"], "OPEN")
        self.assertEqual(networks["00:00:00:00:00:02"]["security"], "WEP")
        self.assertEqual(networks["00:00:00:00:00:03"]["security"], "WPA2")
        self.assertEqual(networks["00:00:00:00:00:04"]["security"], "WPA3")
        self.assertEqual(networks["00:00:00:00:00:05"]["security"], "WPA2/3")

    def test_scan_results_are_committed_and_session_closed(self):
        session = MagicMock()
        session.__enter__.return_value = session
        db_models = SimpleNamespace(
            get_session=MagicMock(return_value=session),
            WifiScanLog=MagicMock(),
        )
        networks = [{
            "ssid": "Lab",
            "bssid": "00:11:22:33:44:55",
            "channel": 1,
            "security": "WPA2",
            "signal": -45.0,
        }]

        with patch.dict(sys.modules, {"db.models": db_models}):
            self.assertTrue(log_scan_results(networks))

        db_models.WifiScanLog.assert_called_once_with(
            ssid="Lab",
            bssid="00:11:22:33:44:55",
            channel=1,
            security="WPA2",
            signal=-45.0,
        )
        session.add.assert_called_once_with(db_models.WifiScanLog.return_value)
        session.commit.assert_called_once_with()
        session.__exit__.assert_called_once()

    def test_db_failure_is_reported_without_raising(self):
        session = MagicMock()
        session.__enter__.return_value = session
        session.commit.side_effect = RuntimeError("disk unavailable")
        db_models = SimpleNamespace(
            get_session=MagicMock(return_value=session),
            WifiScanLog=MagicMock(),
        )
        stderr = io.StringIO()

        with patch.dict(sys.modules, {"db.models": db_models}), redirect_stderr(stderr):
            self.assertFalse(log_scan_results([{"ssid": "Lab"}]))

        self.assertIn("no se pudieron guardar resultados en la DB", stderr.getvalue())
        self.assertIn("disk unavailable", stderr.getvalue())
        session.__exit__.assert_called_once()


class HandshakeDetectionTests(unittest.TestCase):
    def check_output(self, output):
        with patch.object(
            wifi_ops,
            "_run",
            return_value=SimpleNamespace(stdout=output, stderr=""),
        ):
            return wifi_ops.check_handshake("sample.cap")[0]

    def test_zero_handshakes_is_not_valid(self):
        self.assertFalse(self.check_output("1 AP, 0 handshake"))

    def test_positive_handshake_is_valid(self):
        self.assertTrue(self.check_output("1 AP, 1 handshake"))
        self.assertTrue(self.check_output("1 AP, 2 handshakes"))

    def test_unrelated_handshake_text_is_not_valid(self):
        self.assertFalse(self.check_output("handshake detection unavailable"))


if __name__ == "__main__":
    unittest.main()

import json
import os
import sys
import unittest
from contextlib import redirect_stdout
from io import StringIO
from types import SimpleNamespace
from unittest.mock import patch

APP_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "app"))
sys.path.insert(0, APP_DIR)

from wifi import net_ops


class NetScanTests(unittest.TestCase):
    def test_successful_empty_scan_returns_empty_list(self):
        with patch.object(
            net_ops,
            "_nmcli",
            side_effect=[
                SimpleNamespace(returncode=0, stdout="", stderr=""),
                SimpleNamespace(returncode=0, stdout="", stderr=""),
            ],
        ):
            self.assertEqual(net_ops.scan(), [])

    def test_scan_list_error_is_reported_in_json(self):
        with patch.object(
            net_ops,
            "_nmcli",
            side_effect=[
                SimpleNamespace(returncode=0, stdout="", stderr=""),
                SimpleNamespace(returncode=10, stdout="", stderr="radio unavailable"),
            ],
        ), patch.object(sys, "argv", ["net_ops.py", "scan"]):
            stdout = StringIO()
            with redirect_stdout(stdout), self.assertRaises(SystemExit) as exit_context:
                net_ops.main()

        self.assertEqual(exit_context.exception.code, 1)
        result = json.loads(stdout.getvalue())
        self.assertFalse(result["ok"])
        self.assertIn("radio unavailable", result["error"])

    def test_rescan_error_does_not_look_like_no_networks(self):
        with patch.object(
            net_ops,
            "_nmcli",
            return_value=SimpleNamespace(
                returncode=1,
                stdout="",
                stderr="device unavailable",
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "device unavailable"):
                net_ops.scan()

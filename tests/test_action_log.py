import io
import os
import sys
import json
import unittest
from unittest.mock import patch

APP_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "app"))
sys.path.insert(0, APP_DIR)

from db import action_log


class ExtractToolAndErrorTests(unittest.TestCase):
    def test_tool_from_argv_first(self):
        tool, err = action_log._extract_tool_and_error(
            ["scan", "hci1"], '{"ok": true, "count": 3}', 0
        )
        self.assertEqual(tool, "scan")
        self.assertIsNone(err)

    def test_error_pulled_from_ok_false_json(self):
        payload = json.dumps({"ok": False, "error": "sudo: password required"})
        tool, err = action_log._extract_tool_and_error(
            ["scan"], payload, 1
        )
        self.assertEqual(err, "sudo: password required")

    def test_non_zero_exit_falls_back_to_stdout_tail(self):
        tool, err = action_log._extract_tool_and_error(
            ["noop"], "boom\nalgo salio mal", 2
        )
        self.assertIn("algo salio mal", err)

    def test_empty_argv_returns_empty_tool(self):
        tool, err = action_log._extract_tool_and_error([], "", 0)
        self.assertEqual(tool, "")
        self.assertIsNone(err)


class LogActionTests(unittest.TestCase):
    def test_log_action_calls_add_commit_close_on_session(self):
        """log_action debe insertar una fila via get_session y silenciar fallos."""
        calls = []

        class FakeSession:
            def add(self, row):     calls.append(("add", row))
            def commit(self):       calls.append(("commit",))
            def close(self):        calls.append(("close",))

        class FakeActionLog:
            def __init__(self, **kw): self.__dict__.update(kw); calls.append(("ActionLog", kw))

        fake_models = type(sys)("fake_models")
        fake_models.get_session = lambda: FakeSession()
        fake_models.ActionLog = FakeActionLog

        with patch.dict(sys.modules, {"db.models": fake_models}):
            action_log.log_action("bluetooth", "scan",
                                  inputs="hci1 10", outputs='{"ok":true}',
                                  error=None, result="ok", duration_ms=1234)
        # verificamos add + commit + close
        kinds = [c[0] for c in calls]
        self.assertEqual(kinds, ["ActionLog", "add", "commit", "close"])
        kw = calls[0][1]
        self.assertEqual(kw["section"], "bluetooth")
        self.assertEqual(kw["tool"], "scan")
        self.assertEqual(kw["result"], "ok")
        self.assertEqual(kw["duration_ms"], 1234)

    def test_log_action_truncates_oversized_fields(self):
        payload = {}

        class FakeSession:
            def add(self, row):   payload.update(row.__dict__)
            def commit(self):     pass
            def close(self):      pass

        class FakeActionLog:
            def __init__(self, **kw): self.__dict__.update(kw)

        fake_models = type(sys)("fake_models")
        fake_models.get_session = lambda: FakeSession()
        fake_models.ActionLog = FakeActionLog

        huge = "x" * 5000
        with patch.dict(sys.modules, {"db.models": fake_models}):
            action_log.log_action("wifi", "deauth",
                                  inputs=huge, outputs=huge, error=huge)
        self.assertEqual(len(payload["inputs"]), action_log._IN_LIMIT)
        self.assertEqual(len(payload["outputs"]), action_log._OUT_LIMIT)
        self.assertEqual(len(payload["error"]), action_log._ERR_LIMIT)

    def test_log_action_swallows_db_errors(self):
        """Si get_session levanta, no se propaga."""
        fake_models = type(sys)("fake_models")
        def boom(): raise RuntimeError("db offline")
        fake_models.get_session = boom
        fake_models.ActionLog = object
        with patch.dict(sys.modules, {"db.models": fake_models}):
            # NO debe lanzar
            action_log.log_action("x", "y")


class RunCliWithLogTests(unittest.TestCase):
    def test_wraps_main_captures_stdout_and_exit(self):
        """El envoltorio debe reproducir stdout + exit_code exactos y logear."""
        logged = []

        def fake_log(section, tool, inputs=None, outputs=None, error=None,
                     result="ok", duration_ms=None):
            logged.append({"section": section, "tool": tool, "inputs": inputs,
                           "outputs": outputs, "error": error, "result": result})

        def fake_main():
            print(json.dumps({"ok": True, "count": 2}))
            sys.exit(0)

        with patch.object(action_log, "log_action", side_effect=fake_log), \
             patch.object(sys, "argv", ["bt_ops.py", "scan", "hci1"]), \
             patch.object(sys, "stdout", io.StringIO()):
            with self.assertRaises(SystemExit) as cm:
                action_log.run_cli_with_log("bluetooth", fake_main)
        self.assertEqual(cm.exception.code, 0)
        self.assertEqual(len(logged), 1)
        self.assertEqual(logged[0]["section"], "bluetooth")
        self.assertEqual(logged[0]["tool"], "scan")
        self.assertEqual(logged[0]["result"], "ok")
        self.assertIn("count", logged[0]["outputs"])

    def test_marks_error_when_main_raises_system_exit_nonzero(self):
        logged = []

        def fake_log(**kw): logged.append(kw)

        def fake_main():
            print(json.dumps({"ok": False, "error": "boom"}))
            sys.exit(1)

        with patch.object(action_log, "log_action", side_effect=lambda *a, **k: fake_log(**k)), \
             patch.object(sys, "argv", ["net_ops.py", "scan"]), \
             patch.object(sys, "stdout", io.StringIO()):
            with self.assertRaises(SystemExit) as cm:
                action_log.run_cli_with_log("net", fake_main)
        self.assertEqual(cm.exception.code, 1)
        self.assertEqual(logged[0]["result"], "error")
        self.assertEqual(logged[0]["error"], "boom")


if __name__ == "__main__":
    unittest.main()

"""Tests del refactor de wifi_ops.py (dispatcher + monitor_mode + _run).

Cargan el modulo por path (no depende de hardware WiFi) y mockean las ops
pesadas. Verifican:
  * monitor_mode garantiza disable_monitor (happy / excepcion / enable-fail),
  * el dispatcher rutea al handler correcto y valida argumentos sin tirar
    traceback (int() con basura -> JSON ok:false),
  * _run delega en el helper compartido.
"""
import io
import os
import sys
import json
import importlib.util
import contextlib
import unittest

APP_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "app"))
WIFI_OPS = os.path.join(APP_DIR, "wifi", "wifi_ops.py")


def _load():
    sys.path.insert(0, APP_DIR)
    spec = importlib.util.spec_from_file_location("wifi_ops_under_test", WIFI_OPS)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class MonitorModeTests(unittest.TestCase):
    def setUp(self):
        self.m = _load()
        self.calls = []
        self.m.enable_monitor = lambda i: (self.calls.append(("en", i)) or True)
        self.m.disable_monitor = lambda i: self.calls.append(("dis", i))
        self.m.get_status = lambda i: "monitor"

    def test_happy_path_disables(self):
        with self.m.monitor_mode("wlan1"):
            self.calls.append(("body",))
        self.assertEqual(self.calls, [("en", "wlan1"), ("body",), ("dis", "wlan1")])

    def test_exception_still_disables(self):
        with self.assertRaises(RuntimeError):
            with self.m.monitor_mode("wlan1"):
                raise RuntimeError("boom")
        self.assertIn(("dis", "wlan1"), self.calls)

    def test_enable_fail_raises_and_skips_body(self):
        self.m.enable_monitor = lambda i: (self.calls.append(("en", i)) or False)
        ran_body = []
        with self.assertRaises(self.m.MonitorError):
            with self.m.monitor_mode("wlan1"):
                ran_body.append(True)
        self.assertEqual(ran_body, [])                 # cuerpo no corrio
        self.assertNotIn(("dis", "wlan1"), self.calls) # no se desactiva lo que no se activo


class DispatcherTests(unittest.TestCase):
    def setUp(self):
        self.m = _load()
        self.m.ensure_up = lambda i: None   # no tocar hardware

    def _run_main(self, argv):
        """Corre main() con argv dado, captura stdout y el exit code."""
        buf = io.StringIO()
        code = 0
        old = sys.argv
        sys.argv = argv
        try:
            with contextlib.redirect_stdout(buf):
                self.m.main()
        except SystemExit as e:
            code = e.code if isinstance(e.code, int) else 1
        finally:
            sys.argv = old
        out = buf.getvalue().strip()
        return code, (json.loads(out) if out else None)

    def test_unknown_command(self):
        code, obj = self._run_main(["wifi_ops.py", "nope"])
        self.assertEqual(code, 1)
        self.assertFalse(obj["ok"])
        self.assertIn("desconocido", obj["error"])

    def test_no_command(self):
        code, obj = self._run_main(["wifi_ops.py"])
        self.assertEqual(code, 1)
        self.assertFalse(obj["ok"])

    def test_status_routes_to_handler(self):
        self.m.get_status = lambda i: "managed"
        code, obj = self._run_main(["wifi_ops.py", "status", "wlan1"])
        self.assertTrue(obj["ok"])
        self.assertEqual(obj["mode"], "managed")

    def test_bad_int_arg_is_clean_json_not_traceback(self):
        # channel = "abc" -> int() falla; antes tiraba traceback (rompia el
        # contrato JSON). Ahora debe salir un JSON ok:false limpio.
        code, obj = self._run_main(["wifi_ops.py", "deauth", "AA:BB:CC:DD:EE:FF", "abc", "5"])
        self.assertEqual(code, 1)
        self.assertFalse(obj["ok"])
        self.assertIn("argumento invalido", obj["error"])

    def test_deauth_missing_args_usage(self):
        code, obj = self._run_main(["wifi_ops.py", "deauth", "AA:BB:CC:DD:EE:FF"])
        self.assertEqual(code, 1)
        self.assertFalse(obj["ok"])
        self.assertIn("uso:", obj["error"])

    def test_deauth_routes_and_emits(self):
        # Mock de la op para no tocar hardware; verifica ruteo + shape de salida.
        self.m.deauth = lambda *a, **k: ("salida aireplay", None)
        self.m.get_status = lambda i: "managed"
        self.m.log_attack = lambda *a, **k: None
        code, obj = self._run_main(
            ["wifi_ops.py", "deauth", "aa:bb:cc:dd:ee:ff", "6", "5", "wlan1", "0", "11:22:33:44:55:66"])
        self.assertTrue(obj["ok"])
        self.assertEqual(obj["output"], "salida aireplay")


class RunDelegationTests(unittest.TestCase):
    def test_run_delegates_to_shared_helper(self):
        m = _load()
        r = m._run([sys.executable, "-c", "print('hola')"], timeout=5)
        self.assertIsNotNone(r)
        self.assertEqual(r.stdout.strip(), "hola")


if __name__ == "__main__":
    unittest.main()

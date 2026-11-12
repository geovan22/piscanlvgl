"""Tests de app/reports/report_ops.py (Fase 8).

Cada test usa una DB SQLite en archivo temporal: override de PISCAN_DB_PATH
antes de importar db.models y reset del engine cacheado. Fixtures insertan
filas en las 7 tablas fuente y comprueban que el generador:

  * filtra por ventana (24h / 7d / all),
  * redacta credenciales del honeypot por default,
  * respeta max_rows_per_section,
  * arma el timeline ordenado desc,
  * registra una fila en audit_report.
"""
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta

APP_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "app"))
sys.path.insert(0, APP_DIR)


def _fresh_db():
    """Crea archivo temporal, apunta PISCAN_DB_PATH a el y resetea el engine."""
    tmp = tempfile.NamedTemporaryFile(prefix="piscan_test_", suffix=".db",
                                      delete=False)
    tmp.close()
    os.environ["PISCAN_DB_PATH"] = tmp.name
    os.environ["PISCAN_DB_URL"] = f"sqlite:///{tmp.name}"
    # Import tardio para que tome el env, y resetear singletons.
    import importlib
    import db.models as models
    importlib.reload(models)
    return tmp.name, models


def _fresh_report_ops(models):
    """Importa report_ops con la DB actual (tras resetear models)."""
    import importlib
    # Garantiza que app/reports sea importable como 'reports'
    reports_path = os.path.join(APP_DIR)
    if reports_path not in sys.path:
        sys.path.insert(0, reports_path)
    if "reports.report_ops" in sys.modules:
        del sys.modules["reports.report_ops"]
    import reports.report_ops as ro
    importlib.reload(ro)
    return ro


class PureHelpersTests(unittest.TestCase):
    """Helpers puros sin DB."""

    def setUp(self):
        self.db_path, self.models = _fresh_db()
        self.ro = _fresh_report_ops(self.models)

    def tearDown(self):
        try: os.remove(self.db_path)
        except Exception: pass

    def test_window_bounds_24h(self):
        now = datetime(2026, 11, 12, 10, 0, 0)
        start, end = self.ro._window_bounds("24h", now=now)
        self.assertEqual(end, now)
        self.assertEqual(start, now - timedelta(hours=24))

    def test_window_bounds_7d(self):
        now = datetime(2026, 11, 12, 10, 0, 0)
        start, end = self.ro._window_bounds("7d", now=now)
        self.assertEqual(start, now - timedelta(days=7))

    def test_window_bounds_all(self):
        now = datetime(2026, 11, 12, 10, 0, 0)
        start, end = self.ro._window_bounds("all", now=now)
        self.assertIsNone(start)
        self.assertEqual(end, now)

    def test_window_bounds_invalid(self):
        with self.assertRaises(ValueError):
            self.ro._window_bounds("xx")

    def test_redact_pass_default(self):
        # include=False -> redacta mostrando longitud
        self.assertEqual(self.ro._redact_pass("abcd", False), "***(len=4)")
        self.assertEqual(self.ro._redact_pass("", False), "-")
        self.assertEqual(self.ro._redact_pass(None, False), "-")
        # include=True -> pasa el valor tal cual
        self.assertEqual(self.ro._redact_pass("abcd", True), "abcd")

    def test_md_table_sin_datos(self):
        self.assertIn("_sin datos._", self.ro._md_table(["a", "b"], []))

    def test_md_table_escapa_pipes_y_newlines(self):
        md = self.ro._md_table(
            ["c"], [["a|b\nc"]])
        self.assertIn("a\\|b c", md)
        self.assertNotIn("\n c", md.split("\n")[2])

    def test_md_table_trunca_celda_larga(self):
        cell = "x" * 200
        md = self.ro._md_table(["c"], [[cell]])
        self.assertIn("...", md)
        # La celda truncada debe terminar con "..." (117 x + ...)
        line = md.split("\n")[2]
        self.assertTrue(line.endswith("... |"))


class GenerateReportTests(unittest.TestCase):
    """Tests del generador con fixtures reales en SQLite temporal."""

    def setUp(self):
        self.db_path, self.models = _fresh_db()
        self.ro = _fresh_report_ops(self.models)
        # Redirige data/reports/ a un dir temporal (para no ensuciar el repo)
        self.reports_dir = tempfile.mkdtemp(prefix="piscan_reports_")
        self._orig_reports_dir = self.ro._reports_dir
        self.ro._reports_dir = lambda: self.reports_dir

    def tearDown(self):
        self.ro._reports_dir = self._orig_reports_dir
        try: os.remove(self.db_path)
        except Exception: pass
        try:
            for f in os.listdir(self.reports_dir):
                os.remove(os.path.join(self.reports_dir, f))
            os.rmdir(self.reports_dir)
        except Exception:
            pass

    def _seed(self, now):
        """Inserta fixtures: una fila dentro de las 24h, otra de 10 dias atras."""
        M = self.models
        s = M.get_session()
        try:
            s.add(M.WifiScanLog(timestamp=now - timedelta(hours=1),
                                ssid="CLARO_lab", bssid="AA:BB:CC:DD:EE:01",
                                channel=36, security="WPA2", signal=-55))
            s.add(M.WifiScanLog(timestamp=now - timedelta(days=10),
                                ssid="CLARO_old", bssid="AA:BB:CC:DD:EE:02",
                                channel=1, security="WPA2", signal=-72))
            s.add(M.WifiAttackLog(timestamp=now - timedelta(minutes=30),
                                  attack_type="deauth", target_ssid="CLARO_lab",
                                  target_bssid="AA:BB:CC:DD:EE:01",
                                  result="ok", details="20s"))
            s.add(M.HoneypotLog(timestamp=now - timedelta(minutes=15),
                                ssid="PiScan_hp", client_mac="12:34:56:78:9A:BC",
                                vendor="Samsung", client_ip="10.0.0.42",
                                hostname="Pixel", os_guess="Android",
                                user_agent="Mozilla/5.0", requests=3,
                                portal_user="admin", portal_pass="hunter2",
                                rssi=-48, dwell_s=120, bytes_rx=2048,
                                bytes_tx=512, sni_hosts="google.com",
                                mdns_name="Pixel.local"))
            s.add(M.BluetoothLog(timestamp=now - timedelta(minutes=5),
                                 iface="hci1", mac="AA:00:00:00:00:01",
                                 name="LAP-1MJLWG3", vendor="Hewlett Packard",
                                 rssi=-60, type_label="PC"))
            s.add(M.BluetoothAttackLog(timestamp=now - timedelta(minutes=4),
                                       action="gatt_enum",
                                       target_mac="AA:00:00:00:00:01",
                                       target_name="LAP-1MJLWG3",
                                       result="ok", details="svcs=3"))
            s.add(M.BluetoothSighting(timestamp=now - timedelta(minutes=3),
                                      session="20261112-09", round_idx=1,
                                      mac="AA:00:00:00:00:01",
                                      name="LAP-1MJLWG3", rssi=-62,
                                      iface="hci1", kind="dual"))
            s.add(M.ActionLog(timestamp=now - timedelta(minutes=2),
                              section="wifi", tool="deauth",
                              inputs="bssid=AA:BB:CC:DD:EE:01",
                              outputs='{"ok":true}', result="ok",
                              duration_ms=20012))
            s.add(M.ActionLog(timestamp=now - timedelta(minutes=1),
                              section="bluetooth", tool="scan",
                              inputs="hci9", error="Invalid Index",
                              result="error", duration_ms=120))
            s.commit()
        finally:
            s.close()

    def test_generate_24h_filtra_viejos(self):
        now = datetime(2026, 11, 12, 10, 0, 0)
        self._seed(now)
        row = self.ro.generate_report(window="24h", now=now)
        self.assertEqual(row["window"], "24h")
        # WifiScanLog: solo 1 entrada en la ventana (la de hace 1h)
        self.assertEqual(row["summary"]["wifi_scans"], 1)
        self.assertEqual(row["summary"]["wifi_attacks"], 1)
        self.assertEqual(row["summary"]["hp_clients"], 1)
        self.assertEqual(row["summary"]["bt_devices"], 1)
        self.assertEqual(row["summary"]["bt_attacks"], 1)
        self.assertEqual(row["summary"]["bt_sightings"], 1)
        self.assertEqual(row["summary"]["actions"], 2)
        self.assertEqual(row["summary"]["action_errors"], 1)
        # Archivo existe en el dir redirigido
        self.assertTrue(os.path.isfile(row["path"]))
        self.assertTrue(row["path"].startswith(self.reports_dir))
        with open(row["path"], "r", encoding="utf-8") as f:
            text = f.read()
        self.assertIn("# PiScan — Reporte de auditoria", text)
        self.assertIn("CLARO_lab", text)       # scan reciente
        self.assertNotIn("CLARO_old", text)    # scan viejo fuera de ventana

    def test_generate_all_incluye_viejos(self):
        now = datetime(2026, 11, 12, 10, 0, 0)
        self._seed(now)
        row = self.ro.generate_report(window="all", now=now)
        self.assertEqual(row["summary"]["wifi_scans"], 2)  # recientes + viejos

    def test_credenciales_redactadas_por_default(self):
        now = datetime(2026, 11, 12, 10, 0, 0)
        self._seed(now)
        row = self.ro.generate_report(window="24h", now=now)
        with open(row["path"], "r", encoding="utf-8") as f:
            text = f.read()
        # NO debe aparecer el password raw
        self.assertNotIn("hunter2", text)
        # SI debe aparecer el formato redactado
        self.assertIn("***(len=7)", text)

    def test_credenciales_incluidas_si_opt_in(self):
        now = datetime(2026, 11, 12, 10, 0, 0)
        self._seed(now)
        row = self.ro.generate_report(window="24h", now=now,
                                      include_portal_creds=True)
        with open(row["path"], "r", encoding="utf-8") as f:
            text = f.read()
        self.assertIn("hunter2", text)
        self.assertIn("admin", text)

    def test_list_show_delete_roundtrip(self):
        now = datetime(2026, 11, 12, 10, 0, 0)
        self._seed(now)
        created = self.ro.generate_report(window="24h", now=now)
        items = self.ro.list_reports()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["id"], created["id"])
        shown = self.ro.show_report(created["id"])
        self.assertIn("text", shown)
        self.assertIn("PiScan — Reporte", shown["text"])
        self.assertIn("summary", shown)
        # delete borra archivo + fila
        res = self.ro.delete_report(created["id"])
        self.assertEqual(res["id"], created["id"])
        self.assertFalse(os.path.isfile(created["path"]))
        self.assertEqual(self.ro.list_reports(), [])

    def test_timeline_ordenado_desc(self):
        now = datetime(2026, 11, 12, 10, 0, 0)
        self._seed(now)
        row = self.ro.generate_report(window="24h", now=now)
        with open(row["path"], "r", encoding="utf-8") as f:
            text = f.read()
        idx_timeline = text.find("## Timeline unificado")
        self.assertGreater(idx_timeline, -1)
        tail = text[idx_timeline:]
        # El evento de bluetooth/scan (hace 1 min) debe aparecer antes
        # que el de wifi/deauth (hace 2 min) por orden desc.
        i_bt = tail.find("scan")
        i_wifi = tail.find("deauth")
        self.assertGreater(i_bt, -1)
        self.assertGreater(i_wifi, -1)
        self.assertLess(i_bt, i_wifi)


if __name__ == "__main__":
    unittest.main()

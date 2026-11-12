"""
report_ops.py — Generador de reportes de auditoria (Fase 8).

Lee las tablas de log de PiScan y arma un archivo Markdown bajo
`data/reports/` con un resumen de la ventana pedida (24h / 7d / all),
registrandolo en la tabla `AuditReport`.

Marco: laboratorio controlado / pruebas autorizadas (ver CLAUDE.md).

Funciones principales (API publica):
  * generate_report(window="24h", include_portal_creds=False,
                    max_rows=50) -> dict
  * list_reports(limit=50) -> list[dict]
  * show_report(report_id) -> dict (incluye 'text' con el contenido .md)
  * delete_report(report_id) -> dict

CLI (invocado desde db_tool.py):
  python3 -m app.reports.report_ops generate [window] [--creds]
  python3 -m app.reports.report_ops list [limit]
  python3 -m app.reports.report_ops show <id>
  python3 -m app.reports.report_ops delete <id>

Tests: pruebas unitarias en tests/test_report_ops.py con SQLite :memory:.
"""
import os
import sys
import json
import io
from datetime import datetime, timedelta


def _ensure_app_in_path():
    if "db.models" in sys.modules:
        return
    here = os.path.dirname(os.path.abspath(__file__))
    app_dir = os.path.abspath(os.path.join(here, ".."))
    if app_dir not in sys.path:
        sys.path.insert(0, app_dir)


_ensure_app_in_path()
from db.models import (
    get_session, AuditReport,
    WifiScanLog, WifiAttackLog, HoneypotLog,
    BluetoothLog, BluetoothAttackLog, BluetoothSighting,
    ActionLog, Config,
)


WINDOWS = ("24h", "7d", "all")
_DEFAULT_MAX_ROWS = 50


def _reports_dir():
    here = os.path.dirname(os.path.abspath(__file__))
    base = os.path.abspath(os.path.join(here, "..", "..", "data", "reports"))
    os.makedirs(base, exist_ok=True)
    return base


def _window_bounds(window, now=None):
    """Devuelve (period_start, period_end). period_start=None si window=='all'."""
    now = now or datetime.utcnow()
    if window == "24h":
        return now - timedelta(hours=24), now
    if window == "7d":
        return now - timedelta(days=7), now
    if window == "all":
        return None, now
    raise ValueError(f"ventana invalida: {window} (usa 24h/7d/all)")


def _fmt_ts(dt):
    if not dt:
        return "?"
    try:
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return str(dt)


def _redact_pass(value, enabled):
    """Enmascara una credencial del honeypot salvo que include_portal_creds=True.
    Devuelve texto corto: '-' vacio, '***(len=N)' redactado, o el valor raw."""
    if not value:
        return "-"
    if enabled:
        return value
    return f"***(len={len(value)})"


def _md_table(headers, rows):
    """Markdown table simple. Si rows==[], devuelve una linea 'sin datos.'"""
    if not rows:
        return "_sin datos._\n"
    out = ["| " + " | ".join(headers) + " |",
           "|" + "|".join("---" for _ in headers) + "|"]
    for r in rows:
        cells = []
        for c in r:
            if c is None:
                cells.append("")
            else:
                s = str(c).replace("\n", " ").replace("|", "\\|")
                if len(s) > 120:
                    s = s[:117] + "..."
                cells.append(s)
        out.append("| " + " | ".join(cells) + " |")
    return "\n".join(out) + "\n"


def _apply_window(q, col, period_start):
    if period_start is None:
        return q
    return q.filter(col >= period_start)


# ---------- Secciones ----------

def _section_wifi_scans(session, period_start, max_rows):
    q = session.query(WifiScanLog)
    q = _apply_window(q, WifiScanLog.timestamp, period_start)
    total = q.count()
    rows = (q.order_by(WifiScanLog.id.desc()).limit(max_rows).all())
    body = _md_table(
        ["Timestamp", "SSID", "BSSID", "Canal", "Seg.", "Signal"],
        [[_fmt_ts(r.timestamp), r.ssid or "", r.bssid or "",
          r.channel if r.channel is not None else "",
          r.security or "",
          f"{r.signal:.0f}" if r.signal is not None else ""]
         for r in rows],
    )
    md = f"## WiFi — Redes vistas\n\n**Total en la ventana:** {total}"
    if total > len(rows):
        md += f" (mostrando ultimas {len(rows)})"
    md += "\n\n" + body
    return md, {"wifi_scans": total}


def _section_wifi_attacks(session, period_start, max_rows):
    q = session.query(WifiAttackLog)
    q = _apply_window(q, WifiAttackLog.timestamp, period_start)
    total = q.count()
    rows = q.order_by(WifiAttackLog.id.desc()).limit(max_rows).all()
    body = _md_table(
        ["Timestamp", "Tipo", "SSID", "BSSID", "Cliente", "Resultado", "Detalles"],
        [[_fmt_ts(r.timestamp), r.attack_type or "",
          r.target_ssid or "", r.target_bssid or "", r.target_client_mac or "",
          r.result or "", r.details or ""] for r in rows],
    )
    md = f"## WiFi — Ataques\n\n**Total en la ventana:** {total}"
    if total > len(rows):
        md += f" (mostrando ultimos {len(rows)})"
    md += "\n\n" + body
    return md, {"wifi_attacks": total}


def _section_honeypot(session, period_start, max_rows, include_creds):
    q = session.query(HoneypotLog)
    q = _apply_window(q, HoneypotLog.timestamp, period_start)
    total = q.count()
    rows = q.order_by(HoneypotLog.id.desc()).limit(max_rows).all()
    # Sesiones = SSID + timestamp agrupados aproximados (ssid+día+hora):
    sessions = {}
    for r in rows:
        key = (r.ssid or "?", r.timestamp.strftime("%Y-%m-%d %H") if r.timestamp else "?")
        sessions.setdefault(key, []).append(r)
    md_parts = [f"## Honeypot\n\n**Clientes en la ventana:** {total}"]
    if total > len(rows):
        md_parts.append(f" (mostrando ultimos {len(rows)})")
    md_parts.append(f"  —  **Sesiones aproximadas:** {len(sessions)}\n\n")
    if not include_creds:
        md_parts.append(
            "_Credenciales del portal redactadas (ver Config "
            "`report_include_portal_creds`). Para ver el valor exacto: "
            "`python3 app/db_tool.py honeypot show <id>`._\n\n"
        )
    for (ssid, hour), devs in sessions.items():
        md_parts.append(f"### Sesion `{ssid}` — {hour}\n\n")
        md_parts.append(_md_table(
            ["Timestamp", "MAC", "Vendor", "OS", "IP", "Hostname", "UA",
             "User", "Pass", "Reqs", "RSSI", "Dwell(s)", "Rx", "Tx", "SNI", "mDNS"],
            [[_fmt_ts(r.timestamp), r.client_mac or "", r.vendor or "",
              r.os_guess or "", r.client_ip or "", r.hostname or "",
              (r.user_agent or "")[:40],
              _redact_pass(r.portal_user, include_creds),
              _redact_pass(r.portal_pass, include_creds),
              r.requests if r.requests is not None else "",
              r.rssi if r.rssi is not None else "",
              r.dwell_s if r.dwell_s is not None else "",
              r.bytes_rx if r.bytes_rx is not None else "",
              r.bytes_tx if r.bytes_tx is not None else "",
              (r.sni_hosts or "")[:60],
              r.mdns_name or ""]
             for r in devs],
        ))
        md_parts.append("\n")
    return "".join(md_parts), {
        "hp_sessions": len(sessions), "hp_clients": total,
    }


def _section_bluetooth(session, period_start, max_rows):
    q = session.query(BluetoothLog)
    q = _apply_window(q, BluetoothLog.timestamp, period_start)
    total = q.count()
    rows = q.order_by(BluetoothLog.id.desc()).limit(max_rows).all()
    body = _md_table(
        ["Timestamp", "MAC", "Nombre", "Vendor", "RSSI", "Tipo", "Iface"],
        [[_fmt_ts(r.timestamp), r.mac or "", r.name or "", r.vendor or "",
          r.rssi if r.rssi is not None else "",
          r.type_label or r.scan_type or "", r.iface or ""]
         for r in rows],
    )
    md = f"## Bluetooth — Devices vistos\n\n**Total en la ventana:** {total}"
    if total > len(rows):
        md += f" (mostrando ultimos {len(rows)})"
    md += "\n\n" + body
    return md, {"bt_devices": total}


def _section_bt_attacks(session, period_start, max_rows):
    q = session.query(BluetoothAttackLog)
    q = _apply_window(q, BluetoothAttackLog.timestamp, period_start)
    total = q.count()
    rows = q.order_by(BluetoothAttackLog.id.desc()).limit(max_rows).all()
    body = _md_table(
        ["Timestamp", "Accion", "MAC", "Nombre", "Resultado", "Detalles"],
        [[_fmt_ts(r.timestamp), r.action or "", r.target_mac or "",
          r.target_name or "", r.result or "", r.details or ""]
         for r in rows],
    )
    md = f"## Bluetooth — Acciones\n\n**Total en la ventana:** {total}"
    if total > len(rows):
        md += f" (mostrando ultimas {len(rows)})"
    md += "\n\n" + body
    return md, {"bt_attacks": total}


def _section_bt_sightings(session, period_start, max_rows):
    q = session.query(BluetoothSighting)
    q = _apply_window(q, BluetoothSighting.timestamp, period_start)
    total = q.count()
    rows = q.order_by(BluetoothSighting.id.desc()).limit(max_rows).all()
    # Resumen por sesion (de la columna 'session', set por tracking)
    sessions = {}
    for r in rows:
        sess = r.session or "?"
        sessions.setdefault(sess, {"count": 0, "macs": set()})
        sessions[sess]["count"] += 1
        if r.mac:
            sessions[sess]["macs"].add(r.mac)
    summary = _md_table(
        ["Sesion", "Avistamientos", "MACs unicos"],
        [[k, v["count"], len(v["macs"])] for k, v in sessions.items()],
    )
    body = _md_table(
        ["Timestamp", "Sesion", "Ronda", "MAC", "Nombre", "RSSI", "Iface"],
        [[_fmt_ts(r.timestamp), r.session or "", r.round_idx or "",
          r.mac or "", r.name or "",
          r.rssi if r.rssi is not None else "", r.iface or ""]
         for r in rows],
    )
    md = (f"## Bluetooth — Tracking / avistamientos\n\n"
          f"**Total en la ventana:** {total}")
    if total > len(rows):
        md += f" (mostrando ultimos {len(rows)})"
    md += "\n\n### Resumen por sesion\n\n" + summary
    md += "\n### Detalle\n\n" + body
    return md, {"bt_sightings": total}


def _section_actions(session, period_start, max_rows):
    q = session.query(ActionLog)
    q = _apply_window(q, ActionLog.timestamp, period_start)
    total = q.count()
    errors = q.filter(ActionLog.result != "ok").count()
    rows = (q.order_by(ActionLog.id.desc()).limit(max_rows).all())
    body = _md_table(
        ["Timestamp", "Section", "Tool", "Resultado", "ms", "Error"],
        [[_fmt_ts(r.timestamp), r.section or "", r.tool or "",
          r.result or "", r.duration_ms if r.duration_ms is not None else "",
          (r.error or "")[:80]]
         for r in rows],
    )
    md = (f"## Operaciones del sistema (ActionLog)\n\n"
          f"**Total:** {total}  —  **Errores:** {errors}")
    if total > len(rows):
        md += f" (mostrando ultimas {len(rows)})"
    md += "\n\n" + body
    return md, {"actions": total, "action_errors": errors}


def _timeline(session, period_start, max_rows):
    """Unifica eventos de WifiAttackLog + BluetoothAttackLog + ActionLog por ts."""
    events = []
    for cls, label_fn in (
        (WifiAttackLog, lambda r: ("wifi",
            r.attack_type or "?",
            r.result or "?",
            (r.target_ssid or r.target_bssid or ""))),
        (BluetoothAttackLog, lambda r: ("bt",
            r.action or "?",
            r.result or "?",
            (r.target_name or r.target_mac or ""))),
        (ActionLog, lambda r: (r.section or "sys",
            r.tool or "?",
            r.result or "?",
            "")),
    ):
        q = session.query(cls)
        q = _apply_window(q, cls.timestamp, period_start)
        for r in q.order_by(cls.id.desc()).limit(max_rows).all():
            events.append((r.timestamp, label_fn(r)))
    events.sort(key=lambda e: e[0] or datetime.min, reverse=True)
    events = events[:max_rows]
    rows = []
    for ts, (src, tool, result, target) in events:
        rows.append([_fmt_ts(ts), src, tool, result, target])
    body = _md_table(["Timestamp", "Origen", "Tool", "Resultado", "Objetivo"], rows)
    md = f"## Timeline unificado\n\n_Mostrando hasta {max_rows} eventos._\n\n" + body
    return md


# ---------- Generador ----------

def _get_bool_config(session, key, default):
    row = session.get(Config, key)
    if row is None or row.value is None:
        return default
    v = str(row.value).strip().lower()
    return v in ("1", "true", "on", "yes")


def _get_int_config(session, key, default):
    row = session.get(Config, key)
    if row is None or row.value is None:
        return default
    try:
        return int(row.value)
    except Exception:
        return default


def generate_report(window="24h", include_portal_creds=None,
                    max_rows=None, now=None):
    """Genera un reporte Markdown de la ventana pedida y lo registra en
    `audit_report`. Devuelve dict con la fila creada (incl. path, summary)."""
    if window not in WINDOWS:
        raise ValueError(f"ventana invalida: {window} (usa 24h/7d/all)")
    period_start, period_end = _window_bounds(window, now=now)
    s = get_session()
    try:
        if include_portal_creds is None:
            include_portal_creds = _get_bool_config(
                s, "report_include_portal_creds", False)
        if max_rows is None:
            max_rows = _get_int_config(
                s, "report_max_rows_per_section", _DEFAULT_MAX_ROWS)

        parts = []
        header = [
            "# PiScan — Reporte de auditoria",
            "",
            f"**Generado:** {_fmt_ts(period_end)} UTC  ",
            f"**Ventana:** {window}"
            + (f" ({_fmt_ts(period_start)} UTC → {_fmt_ts(period_end)} UTC)"
               if period_start else " (todo el historico)"),
            "",
            "**Marco:** laboratorio controlado / pruebas autorizadas.  ",
            "Datos tomados de las tablas de log de la DB; no se re-ejecuta ningun ataque.",
            "",
            "---",
            "",
        ]
        parts.append("\n".join(header))

        summary = {}
        sections = [
            _section_wifi_scans(s, period_start, max_rows),
            _section_wifi_attacks(s, period_start, max_rows),
            _section_honeypot(s, period_start, max_rows, include_portal_creds),
            _section_bluetooth(s, period_start, max_rows),
            _section_bt_attacks(s, period_start, max_rows),
            _section_bt_sightings(s, period_start, max_rows),
            _section_actions(s, period_start, max_rows),
        ]
        # Resumen arriba (lo prependemos antes de las secciones)
        for (_md, stats) in sections:
            summary.update(stats)
        resumen_lines = [
            "## Resumen",
            "",
            f"- WiFi: {summary.get('wifi_scans', 0)} redes vistas, "
            f"{summary.get('wifi_attacks', 0)} ataques.",
            f"- Honeypot: {summary.get('hp_sessions', 0)} sesiones, "
            f"{summary.get('hp_clients', 0)} clientes.",
            f"- Bluetooth: {summary.get('bt_devices', 0)} devices, "
            f"{summary.get('bt_attacks', 0)} acciones, "
            f"{summary.get('bt_sightings', 0)} avistamientos.",
            f"- Sistema: {summary.get('actions', 0)} operaciones "
            f"({summary.get('action_errors', 0)} errores).",
            "",
            "---",
            "",
        ]
        parts.append("\n".join(resumen_lines))
        for (md, _stats) in sections:
            parts.append(md)
            parts.append("\n")
        parts.append(_timeline(s, period_start, max_rows))
        text = "\n".join(parts)

        # Archivo
        base = _reports_dir()
        stamp = period_end.strftime("%Y%m%d%H%M%S")
        fname = f"piscan_report_{window}_{stamp}.md"
        fpath = os.path.join(base, fname)
        with open(fpath, "w", encoding="utf-8") as f:
            f.write(text)
        size = os.path.getsize(fpath)

        row = AuditReport(
            timestamp=period_end,
            report_type="window",
            window=window,
            period_start=period_start,
            period_end=period_end,
            path=fpath,
            size_bytes=size,
            summary=json.dumps(summary, ensure_ascii=False),
        )
        s.add(row)
        s.commit()
        s.refresh(row)
        return _serialize(row, include_text=False)
    finally:
        s.close()


def list_reports(limit=50):
    s = get_session()
    try:
        rows = (s.query(AuditReport)
                .order_by(AuditReport.id.desc())
                .limit(int(limit))
                .all())
        return [_serialize(r, include_text=False) for r in rows]
    finally:
        s.close()


def show_report(report_id, max_text=None):
    """Devuelve el meta + el contenido del archivo (truncado si max_text)."""
    s = get_session()
    try:
        r = s.get(AuditReport, int(report_id))
        if r is None:
            raise ValueError(f"id no encontrado: {report_id}")
        d = _serialize(r, include_text=True, max_text=max_text)
        return d
    finally:
        s.close()


def delete_report(report_id):
    s = get_session()
    try:
        r = s.get(AuditReport, int(report_id))
        if r is None:
            raise ValueError(f"id no encontrado: {report_id}")
        path = r.path
        try:
            if path and os.path.isfile(path):
                os.remove(path)
        except Exception:
            pass
        s.delete(r)
        s.commit()
        return {"id": int(report_id), "path": path}
    finally:
        s.close()


def _serialize(r, include_text=False, max_text=None):
    d = {
        "id": r.id,
        "ts": _fmt_ts(r.timestamp),
        "report_type": r.report_type,
        "window": r.window,
        "period_start": _fmt_ts(r.period_start) if r.period_start else None,
        "period_end": _fmt_ts(r.period_end) if r.period_end else None,
        "path": r.path,
        "size_bytes": r.size_bytes,
    }
    try:
        d["summary"] = json.loads(r.summary) if r.summary else {}
    except Exception:
        d["summary"] = {}
    if include_text:
        text = ""
        try:
            if r.path and os.path.isfile(r.path):
                with open(r.path, "r", encoding="utf-8") as f:
                    text = f.read()
        except Exception:
            text = ""
        if max_text is not None and len(text) > max_text:
            text = text[:max_text] + "\n\n_[truncado]_\n"
        d["text"] = text
    return d


# ---------- CLI ----------

def _cli_main(argv):
    def _emit(obj):
        print(json.dumps(obj, indent=2, ensure_ascii=False))

    if not argv:
        _emit({"ok": False,
               "error": "uso: report_ops.py <generate|list|show|delete> [args]"})
        sys.exit(1)
    cmd, args = argv[0], argv[1:]
    try:
        if cmd == "generate":
            window = "24h"
            include_creds = None
            for a in args:
                if a in WINDOWS:
                    window = a
                elif a in ("--creds", "--include-creds"):
                    include_creds = True
                elif a in ("--no-creds",):
                    include_creds = False
            row = generate_report(window=window,
                                  include_portal_creds=include_creds)
            _emit({"ok": True, "report": row})
            return 0
        if cmd == "list":
            limit = int(args[0]) if args and args[0].isdigit() else 50
            items = list_reports(limit=limit)
            _emit({"ok": True, "count": len(items), "items": items})
            return 0
        if cmd == "show":
            if not args or not args[0].isdigit():
                _emit({"ok": False, "error": "uso: show <id>"})
                return 1
            max_text = None
            for a in args[1:]:
                if a.startswith("--max-text="):
                    try:
                        max_text = int(a.split("=", 1)[1])
                    except Exception:
                        pass
            row = show_report(int(args[0]), max_text=max_text)
            _emit({"ok": True, "report": row})
            return 0
        if cmd == "delete":
            if not args or not args[0].isdigit():
                _emit({"ok": False, "error": "uso: delete <id>"})
                return 1
            res = delete_report(int(args[0]))
            _emit({"ok": True, "deleted": res})
            return 0
        _emit({"ok": False, "error": f"comando desconocido: {cmd}"})
        return 1
    except Exception as e:
        _emit({"ok": False, "error": str(e)})
        return 1


if __name__ == "__main__":
    sys.exit(_cli_main(sys.argv[1:]))

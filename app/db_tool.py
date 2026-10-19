#!/usr/bin/env python3
"""
db_tool.py — CLI de acceso a la DB de PiScan. Invocado desde C via popen().
Convencion: SIEMPRE responde un unico JSON por stdout, exit 0 si ok=true.
"""
import sys, os, json, hashlib, secrets

# Bajo systemd el servicio corre con locale minimo (C/POSIX) sin LANG; forzar
# UTF-8 en stdout evita que el print del JSON (o un mensaje de error con
# caracteres raros) tire UnicodeEncodeError.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
except Exception:
    pass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from db.models import get_session, Config, SecurityCredential, HoneypotLog

def ok(data=None, pretty=False):
    out = {"ok": True}
    if data:
        out.update(data)
    # pretty=True: salida indentada para lectura humana (comandos honeypot).
    # Los comandos que invoca C (config/credential) usan una sola linea (popen).
    print(json.dumps(out, indent=2, ensure_ascii=False) if pretty else json.dumps(out))
    sys.exit(0)

def err(msg):
    try:
        msg = str(msg)
    except Exception:
        msg = "error"
    print(json.dumps({"ok": False, "error": msg}))
    sys.exit(1)

def hash_value(plaintext, salt=None):
    if salt is None:
        salt = secrets.token_hex(16)
    h = hashlib.sha256((salt + plaintext).encode()).hexdigest()
    return h, salt

def cmd_config_get(args):
    if not args: err("uso: config get <key>")
    s = get_session()
    row = s.get(Config, args[0])
    if row is None: err(f"clave no encontrada: {args[0]}")
    ok({"key": row.key, "value": row.value, "category": row.category})

def cmd_config_set(args):
    if len(args) < 2: err("uso: config set <key> <value> [category]")
    key, value = args[0], args[1]
    category = args[2] if len(args) > 2 else None
    s = get_session()
    row = s.get(Config, key)
    if row is None:
        row = Config(key=key, value=value, category=category)
        s.add(row)
    else:
        row.value = value
        if category: row.category = category
    s.commit()
    ok({"key": key, "value": value})

def cmd_config_list(args):
    s = get_session()
    q = s.query(Config)
    if args: q = q.filter(Config.category == args[0])
    ok({"items": [{"key": r.key, "value": r.value, "category": r.category} for r in q.all()]})

def cmd_config_setmany(args):
    """config setmany <category> <key> <value> [<key> <value> ...] — escribe
    varios parametros en UNA sola invocacion (evita forkear Python por cada tap
    en la pantalla Config). category="" -> None. value puede ser "" (vacio)."""
    if len(args) < 3:
        err("uso: config setmany <category> <key> <value> [<key> <value> ...]")
    category = args[0] or None
    pairs = args[1:]
    if len(pairs) % 2 != 0:
        err("setmany: numero impar de key/value")
    s = get_session()
    n = 0
    for i in range(0, len(pairs), 2):
        key, value = pairs[i], pairs[i + 1]
        row = s.get(Config, key)
        if row is None:
            s.add(Config(key=key, value=value, category=category))
        else:
            row.value = value
            if category:
                row.category = category
        n += 1
    s.commit()
    ok({"saved": n})

def cmd_credential_set(args):
    if len(args) < 2: err("uso: credential set <cred_type> <plaintext>")
    cred_type, plaintext = args[0], args[1]
    h, salt = hash_value(plaintext)
    s = get_session()
    row = s.query(SecurityCredential).filter(SecurityCredential.cred_type == cred_type).first()
    if row is None:
        row = SecurityCredential(cred_type=cred_type, hash=h, salt=salt)
        s.add(row)
    else:
        row.hash, row.salt = h, salt
    s.commit()
    ok({"cred_type": cred_type})

def cmd_credential_verify(args):
    if len(args) < 2: err("uso: credential verify <cred_type> <plaintext>")
    cred_type, plaintext = args[0], args[1]
    s = get_session()
    row = s.query(SecurityCredential).filter(SecurityCredential.cred_type == cred_type).first()
    if row is None: ok({"valid": False, "reason": "no_credential_set"})
    h, _ = hash_value(plaintext, salt=row.salt)
    ok({"valid": (h == row.hash)})

def cmd_credential_exists(args):
    if not args: err("uso: credential exists <cred_type>")
    s = get_session()
    row = s.query(SecurityCredential).filter(SecurityCredential.cred_type == args[0]).first()
    ok({"exists": row is not None})

def _hp_row(r, full=False):
    """Serializa una fila de honeypot_log a dict JSON-safe."""
    d = {
        "id": r.id,
        "timestamp": r.timestamp.isoformat() if r.timestamp else None,
        "ssid": r.ssid, "client_mac": r.client_mac, "vendor": r.vendor,
        "hostname": r.hostname, "os": r.os_guess,
        "portal_user": r.portal_user, "portal_pass": r.portal_pass,
        "requests": r.requests,
    }
    if full:
        d.update({"client_ip": r.client_ip, "user_agent": r.user_agent,
                  "domains": r.domains, "details": r.details,
                  "rssi": r.rssi, "bytes_rx": r.bytes_rx, "bytes_tx": r.bytes_tx,
                  "dwell_s": r.dwell_s, "dhcp_vendor": r.dhcp_vendor,
                  "dhcp_fingerprint": r.dhcp_fingerprint, "sni_hosts": r.sni_hosts,
                  "mdns_name": r.mdns_name, "probe_ssids": r.probe_ssids,
                  "pcap_path": r.pcap_path})
    return d

def cmd_honeypot_list(args):
    """honeypot list [limit] [ssid] — ultimas filas, mas recientes primero."""
    limit = 20
    ssid = None
    for a in args:
        if a.isdigit():
            limit = int(a)
        else:
            ssid = a
    s = get_session()
    q = s.query(HoneypotLog)
    if ssid:
        q = q.filter(HoneypotLog.ssid == ssid)
    rows = q.order_by(HoneypotLog.id.desc()).limit(limit).all()
    ok({"count": len(rows), "items": [_hp_row(r) for r in rows]}, pretty=True)

def cmd_honeypot_show(args):
    """honeypot show <id> — detalle completo de una fila (incl. dominios/UA)."""
    if not args or not args[0].isdigit():
        err("uso: honeypot show <id>")
    s = get_session()
    row = s.get(HoneypotLog, int(args[0]))
    if row is None:
        err(f"id no encontrado: {args[0]}")
    ok(_hp_row(row, full=True), pretty=True)

def cmd_portals_list(args):
    """portals list — plantillas disponibles en data/portals/ y cual esta activa
    (Config honeypot_portal). Para que la UI/Config pueda listarlas y elegir."""
    base = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "portals")
    base = os.path.abspath(base)
    files = []
    try:
        files = sorted(f for f in os.listdir(base) if f.endswith(".html"))
    except Exception:
        files = []
    s = get_session()
    row = s.get(Config, "honeypot_portal")
    active = row.value if row else None
    ok({"active": active, "count": len(files), "items": files}, pretty=True)

COMMANDS = {
    ("config", "get"): cmd_config_get,
    ("config", "set"): cmd_config_set,
    ("config", "list"): cmd_config_list,
    ("config", "setmany"): cmd_config_setmany,
    ("credential", "set"): cmd_credential_set,
    ("credential", "verify"): cmd_credential_verify,
    ("credential", "exists"): cmd_credential_exists,
    ("honeypot", "list"): cmd_honeypot_list,
    ("honeypot", "show"): cmd_honeypot_show,
    ("portals", "list"): cmd_portals_list,
}

def main():
    if len(sys.argv) < 3:
        err("uso: db_tool.py <tabla> <accion> [args...]")
    fn = COMMANDS.get((sys.argv[1], sys.argv[2]))
    if fn is None:
        err(f"comando desconocido: {sys.argv[1]} {sys.argv[2]}")
    try:
        fn(sys.argv[3:])
    except Exception as e:
        err(str(e))

if __name__ == "__main__":
    main()

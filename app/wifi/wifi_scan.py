#!/usr/bin/env python3
"""
wifi_scan.py — Escaneo de redes via `iw scan`. Salida JSON, mismo
patron que db_tool.py: {"ok": true, "networks": [...]} o {"ok": false, "error": ...}
"""
import sys, json, subprocess

def ensure_interface_up(iface):
    """wlan1 esta 'unmanaged' en NetworkManager a proposito (para
    modo monitor/ataque), asi que nada la levanta sola tras un reinicio."""
    subprocess.run(['sudo', 'ip', 'link', 'set', iface, 'up'],
                    capture_output=True, timeout=5)

def scan_networks(iface='wlan1'):
    ensure_interface_up(iface)
    try:
        proc = subprocess.run(
            ['sudo', 'iw', 'dev', iface, 'scan'],
            capture_output=True, text=True, timeout=15
        )
        out = proc.stdout
        if proc.returncode != 0:
            return None, f"iw scan fallo: {proc.stderr.strip() or out.strip()}"
    except Exception as e:
        return None, str(e)

    networks = {}
    cur = None

    def commit(c):
        if not c or not c.get('ssid'):
            return
        key = c['ssid']
        if key not in networks or c['signal'] > networks[key]['signal']:
            networks[key] = {k: v for k, v in c.items() if not k.startswith('_')}

    for raw in out.splitlines():
        line = raw.strip()
        if raw.startswith('BSS '):
            commit(cur)
            bssid = raw.split('BSS ')[1].split('(')[0].strip()
            cur = {'ssid': '', 'bssid': bssid, 'signal': -100.0, 'channel': 0, 'security': 'OPEN', '_privacy': False}
            continue
        if cur is None:
            continue
        if line.startswith('signal:'):
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
    result = list(networks.values())
    result.sort(key=lambda n: n['signal'], reverse=True)
    return result, None

def main():
    iface = sys.argv[2] if len(sys.argv) > 2 else 'wlan1'
    if len(sys.argv) < 2 or sys.argv[1] != 'scan':
        print(json.dumps({"ok": False, "error": "uso: wifi_scan.py scan [iface]"}))
        sys.exit(1)

    networks, err = scan_networks(iface)
    if err is not None:
        print(json.dumps({"ok": False, "error": err}))
        sys.exit(1)

    print(json.dumps({"ok": True, "networks": networks}))
    sys.exit(0)

if __name__ == "__main__":
    main()

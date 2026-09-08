#!/usr/bin/env python3
"""
net_ops.py — Gestion de la red de GESTION sobre wlan0 (el adaptador
onboard, cliente/managed para SSH). Separado de wifi_ops.py, que maneja
wlan1 en modo ataque. Salida JSON, mismo patron que el resto.

Comandos:
  scan            escanea redes disponibles en wlan0
  list_saved      lista perfiles WiFi guardados en NetworkManager
  status          estado actual de wlan0 (conectada, IP)
  connect <ssid> [password]   conecta (crea/actualiza perfil, autoconnect)
  activate <ssid>             activa un perfil ya guardado (sin password)
  forget <ssid>               borra un perfil guardado
"""
import sys, os, json, subprocess

IFACE = "wlan0"

def _run(cmd, timeout=30):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except Exception:
        return None

def _nmcli(args, timeout=30):
    return _run(['sudo', '/usr/bin/nmcli'] + args, timeout=timeout)

def scan():
    """Redes disponibles en wlan0. Descarta SSID vacios (ocultas) y
    deduplica por SSID quedandose con la senal mas fuerte."""
    _nmcli(['device', 'wifi', 'rescan', 'ifname', IFACE], timeout=15)
    r = _nmcli(['-t', '-f', 'SSID,SIGNAL,SECURITY,IN-USE',
                'device', 'wifi', 'list', 'ifname', IFACE], timeout=20)
    nets = {}
    if r and r.stdout:
        for line in r.stdout.splitlines():
            # nmcli -t escapa ':' internos como '\:'; separamos con cuidado
            parts = line.replace('\\:', '\x00').split(':')
            parts = [p.replace('\x00', ':') for p in parts]
            if len(parts) < 4:
                continue
            ssid, signal, security, in_use = parts[0], parts[1], parts[2], parts[3]
            if not ssid:
                continue
            try:
                sig = int(signal)
            except ValueError:
                sig = 0
            cur = nets.get(ssid)
            if not cur or sig > cur['signal']:
                nets[ssid] = {
                    'ssid': ssid,
                    'signal': sig,
                    'security': security or 'OPEN',
                    'in_use': in_use.strip() == '*',
                }
    result = sorted(nets.values(), key=lambda x: x['signal'], reverse=True)
    return result

def list_saved():
    """Perfiles WiFi guardados en NetworkManager (802-11-wireless)."""
    r = _nmcli(['-t', '-f', 'NAME,TYPE,AUTOCONNECT', 'connection', 'show'])
    saved = []
    active = _active_ssid()
    if r and r.stdout:
        for line in r.stdout.splitlines():
            parts = line.replace('\\:', '\x00').split(':')
            parts = [p.replace('\x00', ':') for p in parts]
            if len(parts) < 2:
                continue
            name, ctype = parts[0], parts[1]
            if ctype != '802-11-wireless':
                continue
            autoconnect = (len(parts) > 2 and parts[2] == 'yes')
            saved.append({
                'name': name,
                'autoconnect': autoconnect,
                'active': (name == active),
            })
    return saved

def _active_ssid():
    r = _nmcli(['-t', '-f', 'NAME,DEVICE', 'connection', 'show', '--active'])
    if r and r.stdout:
        for line in r.stdout.splitlines():
            parts = line.rsplit(':', 1)
            if len(parts) == 2 and parts[1] == IFACE:
                return parts[0]
    return None

def status():
    """Estado de wlan0: conectada?, a que SSID, con que IP."""
    connected = False
    ssid = _active_ssid()
    r = _run(['/usr/bin/nmcli', '-t', '-f', 'DEVICE,STATE', 'device', 'status'])
    if r and r.stdout:
        for line in r.stdout.splitlines():
            p = line.split(':')
            if len(p) >= 2 and p[0] == IFACE and p[1] == 'connected':
                connected = True
    ip = None
    r2 = _run(['/usr/bin/ip', '-4', '-o', 'addr', 'show', IFACE])
    if r2 and r2.stdout:
        for tok in r2.stdout.split():
            if '.' in tok and '/' in tok:
                ip = tok.split('/')[0]
                break
    return {'connected': connected, 'ssid': ssid, 'ip': ip}

def connect(ssid, password=None):
    """Conecta a una red. Si tiene perfil guardado, lo activa; si no,
    crea uno nuevo (con o sin password). NM lo guarda con autoconnect."""
    # Si ya existe el perfil, activarlo directo
    existing = [s['name'] for s in list_saved()]
    if ssid in existing and not password:
        r = _nmcli(['connection', 'up', ssid], timeout=45)
        ok = bool(r and r.returncode == 0)
        return {'ok': ok, 'ssid': ssid, 'detail': (r.stderr or r.stdout or '')[:200] if r else 'sin salida'}
    # Conectar (crea perfil). --ask no aplica aqui; pasamos password si hay.
    args = ['device', 'wifi', 'connect', ssid, 'ifname', IFACE]
    if password:
        args += ['password', password]
    r = _nmcli(args, timeout=45)
    ok = bool(r and r.returncode == 0)
    detail = (r.stderr or r.stdout or '')[:200] if r else 'sin salida'
    return {'ok': ok, 'ssid': ssid, 'detail': detail}

def forget(ssid):
    r = _nmcli(['connection', 'delete', ssid], timeout=15)
    ok = bool(r and r.returncode == 0)
    return {'ok': ok, 'ssid': ssid}

def main():
    if len(sys.argv) < 2:
        print(json.dumps({"ok": False, "error": "falta comando"}))
        sys.exit(1)
    cmd = sys.argv[1]
    if cmd == 'scan':
        print(json.dumps({"ok": True, "networks": scan()}))
    elif cmd == 'list_saved':
        print(json.dumps({"ok": True, "saved": list_saved()}))
    elif cmd == 'status':
        print(json.dumps({"ok": True, "status": status()}))
    elif cmd == 'connect':
        if len(sys.argv) < 3:
            print(json.dumps({"ok": False, "error": "uso: connect <ssid> [password]"}))
            sys.exit(1)
        ssid = sys.argv[2]
        password = sys.argv[3] if len(sys.argv) > 3 else None
        print(json.dumps(connect(ssid, password)))
    elif cmd == 'activate':
        if len(sys.argv) < 3:
            print(json.dumps({"ok": False, "error": "uso: activate <ssid>"}))
            sys.exit(1)
        print(json.dumps(connect(sys.argv[2], None)))
    elif cmd == 'forget':
        if len(sys.argv) < 3:
            print(json.dumps({"ok": False, "error": "uso: forget <ssid>"}))
            sys.exit(1)
        print(json.dumps(forget(sys.argv[2])))
    else:
        print(json.dumps({"ok": False, "error": f"comando desconocido: {cmd}"}))
        sys.exit(1)

if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
wifi_scan.py — Escaneo de redes via `iw scan`. Salida JSON, mismo
patron que db_tool.py: {"ok": true, "networks": [...]} o {"ok": false, "error": ...}
"""
import sys, os, json, subprocess, time

def ensure_interface_up(iface):
    """wlan1 esta 'unmanaged' en NetworkManager a proposito (para
    modo monitor/ataque), asi que nada la levanta sola tras un reinicio."""
    subprocess.run(['sudo', '/usr/bin/ip', 'link', 'set', iface, 'up'],
                    capture_output=True, timeout=5)

def _db_config_get(key):
    """Lee un valor de la tabla Config (silencioso si falla)."""
    try:
        sys.path.insert(0, os.path.expanduser("~/piscanlvgl/app"))
        from db.models import get_session, Config
        row = get_session().get(Config, key)
        return row.value if row and row.value else None
    except Exception:
        return None

def ensure_reg_domain():
    """El dominio regulatorio en 99/00 (world) DESHABILITA muchos canales
    (sobre todo 5GHz) -> el scan no los barre y se ven menos redes. Ademas el
    pais puede volver a 99 tras recargas de modulo. Si hay un pais configurado
    (Config key 'wifi_country', ej 'GT'/'HN'/'MX'), lo fijamos antes de
    escanear para habilitar todos los canales. No-op si no esta seteado."""
    cc = _db_config_get('wifi_country')
    if cc and len(cc.strip()) == 2:
        subprocess.run(['sudo', '/usr/sbin/iw', 'reg', 'set', cc.strip().upper()],
                       capture_output=True, timeout=5)
        time.sleep(0.5)

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

def _pct_to_dbm(pct):
    """nmcli da la senal en % (0-100); la convertimos a dBm aprox para que
    sea comparable con la de `iw` (dBm). 100%->-50, 0%->-100."""
    try:
        return float(int(pct)) / 2.0 - 100.0
    except (ValueError, TypeError):
        return -100.0

def _scan_nmcli(networks, iface='wlan0'):
    """Suma al dict las redes que ve el ONBOARD (wlan0) via NetworkManager.
    La antena onboard capta bastante mas que el dongle USB (wlan1), asi el
    listado de la seccion WiFi queda completo. El ataque sigue usando wlan1
    con el BSSID+canal obtenidos aca. rescan en background = no corta el SSH."""
    try:
        subprocess.run(['sudo', '/usr/bin/nmcli', 'device', 'wifi', 'rescan', 'ifname', iface],
                       capture_output=True, timeout=15)
        r = subprocess.run(['sudo', '/usr/bin/nmcli', '-t', '-f',
                            'BSSID,SSID,CHAN,SIGNAL,SECURITY', 'device', 'wifi', 'list', 'ifname', iface],
                           capture_output=True, text=True, timeout=20)
    except Exception:
        return
    if not r or r.returncode != 0 or not r.stdout:
        return
    for line in r.stdout.splitlines():
        # nmcli -t escapa ':' internos como '\:' (el BSSID los tiene); los
        # protegemos, separamos por el ':' de campos, y los restauramos.
        parts = line.replace('\\:', '\x00').split(':')
        parts = [p.replace('\x00', ':') for p in parts]
        if len(parts) < 5:
            continue
        bssid, ssid, chan, signal, security = parts[0], parts[1], parts[2], parts[3], parts[4]
        try:
            ch = int(chan)
        except ValueError:
            ch = 0
        sig = _pct_to_dbm(signal)
        key = ssid if ssid else ('\x00' + bssid)
        if not key:
            continue
        if key not in networks or sig > networks[key]['signal']:
            networks[key] = {
                'ssid': ssid if ssid else '(oculta)',
                'bssid': bssid.upper(),
                'signal': sig,
                'channel': ch,
                'security': security if (security and security != '--') else 'OPEN',
                'band': '5G' if ch >= 32 else ('2.4G' if ch >= 1 else '?'),
            }

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


def scan_networks(iface='wlan1', passes=2):
    """Escanea en VARIAS pasadas y fusiona (por BSSID/SSID). Una sola pasada
    de `iw scan` suele quedarse corta en este adaptador: no todos los APs
    emiten beacon durante el barrido y los 5GHz DFS son escaneo pasivo. Con
    2-3 pasadas se captan bastantes mas."""
    ensure_interface_up(iface)
    ensure_reg_domain()   # habilita todos los canales si hay pais configurado
    networks = {}
    last_err = None
    for i in range(max(1, passes)):
        # Pausa entre pasadas: sin ella, `iw scan` seguido devuelve -EBUSY
        # (la pasada previa sigue corriendo) y se descarta -> en la practica
        # quedaba 1 sola pasada. La pausa tambien deja variar los beacons.
        if i > 0:
            time.sleep(3)
        try:
            proc = subprocess.run(
                ['sudo', '/usr/sbin/iw', 'dev', iface, 'scan'],
                capture_output=True, text=True, timeout=20
            )
            if proc.returncode != 0:
                last_err = f"iw scan fallo: {proc.stderr.strip() or proc.stdout.strip()}"
                continue
            _parse_scan_output(proc.stdout, networks)
        except Exception as e:
            last_err = str(e)
            continue

    # Sumar lo que ve el onboard (wlan0): capta mas que el dongle wlan1.
    _scan_nmcli(networks)

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

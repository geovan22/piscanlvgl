#!/usr/bin/env python3
"""
bt_ops.py - Operaciones Bluetooth (BR/EDR + LE) para PiScan.

Patron: salida JSON por stdout (1 solo JSON, nunca prints de debug).
Marco: laboratorio controlado / pruebas autorizadas. Mismo contrato
C <-> Python que wifi_ops.py.

Comandos:
  up    [iface]                      -> rfkill unblock + btmgmt power on
  down  [iface]                      -> btmgmt power off
  info  [iface]                      -> datos del controller (btmgmt info)
  scan  [iface] [seconds] [mode]     -> mode: dual (default) | le | classic
                                       devuelve lista de dispositivos vistos
  show  [iface] <mac>                -> bluetoothctl info <mac>, enriquecido

iface default: hci1 (USB RTL8821CU).
"""
import sys, os, re, json, subprocess, time, threading

# Rutas absolutas de los binarios usados (coherente con wifi_ops.py / sudoers).
BTMGMT       = "/usr/bin/btmgmt"
BLUETOOTHCTL = "/usr/bin/bluetoothctl"
HCICONFIG    = "/usr/bin/hciconfig"
SDPTOOL      = "/usr/bin/sdptool"
L2PING       = "/usr/bin/l2ping"
BTMON        = "/usr/bin/btmon"
TIMEOUT_BIN  = "/usr/bin/timeout"
STDBUF_BIN   = "/usr/bin/stdbuf"
RFKILL_BIN   = "/usr/sbin/rfkill"
SUDO         = "/usr/bin/sudo"

DEFAULT_IFACE = "hci1"


def _run(cmd, timeout=15):
    """Importante: pasamos stdin=DEVNULL siempre. Bajo systemd el servicio
    corre con stdin cerrado y `btmgmt` (bluez 5.82) tiene un bug donde se
    cuelga indefinidamente si no detecta un stdin TTY o un pipe abierto.
    Confirmado 2026-10-27 reproduciendo `btmgmt info` con DEVNULL -> timeout.
    hciconfig no sufre ese bug, por eso power_on/off usan hciconfig."""
    try:
        return subprocess.run(cmd, capture_output=True, text=True,
                              timeout=timeout, stdin=subprocess.DEVNULL)
    except Exception:
        return None


def _iface_index(iface):
    """hci0 -> 0, hci1 -> 1. Devuelve str para pasarlo a btmgmt --index."""
    m = re.match(r'^hci(\d+)$', (iface or '').strip().lower())
    if not m:
        raise ValueError(f"iface invalido: {iface!r} (esperado hci<N>)")
    return m.group(1)


# ---------------------------------------------------------------------------
# OUI -> fabricante (duplicado minimal del helper de wifi_ops.py). Se podria
# refactorizar a app/oui.py mas adelante; por ahora, autonomia completa.
# ---------------------------------------------------------------------------

_OUI_BUILTIN_BT = {
    "B827EB": "Raspberry Pi",
    "DCA632": "Raspberry Pi",
    "E45F01": "Raspberry Pi",
    "FC221C": "Realtek",       # dongle USB BT que usa PiScan (hci1)
    "001A7D": "Realtek",
    "D83ADD": "Raspberry Pi",
}


def _oui_file():
    for p in (os.path.expanduser("~/piscanlvgl/data/oui.txt"),
              "/usr/share/ieee-data/oui.txt", "/var/lib/ieee-data/oui.txt",
              "/usr/share/aircrack-ng/airodump-ng-oui.txt",
              "/usr/share/wireshark/manuf", "/usr/share/misc/oui.txt"):
        if os.path.exists(p):
            return p
    return None


def _mac_is_random(mac):
    """True si el 1er octeto tiene el bit 0x02 (locally administered). Las
    MAC BLE "random" tipicas (iOS/Android por privacidad) caen aqui. Ningun
    OUI las resuelve, asi que las etiquetamos aparte."""
    try:
        o1 = int(mac.split(':')[0], 16)
        return bool(o1 & 0x02)
    except Exception:
        return False


def _resolve_vendors(prefixes):
    """prefixes: set de prefijos 6-hex en mayuscula sin separador.
    Devuelve {prefijo: fabricante}, best-effort."""
    result = {}
    want = set(p.upper() for p in prefixes if p)
    for p in want:
        if p in _OUI_BUILTIN_BT:
            result[p] = _OUI_BUILTIN_BT[p]
    path = _oui_file()
    if path and want:
        remaining = want - set(result.keys())
        if remaining:
            try:
                with open(path, "r", errors="replace") as f:
                    for line in f:
                        m = re.match(r'^\s*([0-9A-Fa-f]{2})[:-]([0-9A-Fa-f]{2})[:-]([0-9A-Fa-f]{2})', line)
                        if not m:
                            continue
                        pref = (m.group(1) + m.group(2) + m.group(3)).upper()
                        if pref in remaining and pref not in result:
                            rest = line[m.end():].replace('(hex)', '').strip().strip('\t').strip()
                            vendor = re.split(r'\t|  +', rest)[0].strip() if rest else ''
                            if vendor:
                                result[pref] = vendor[:24]
                        if len(result) == len(want):
                            break
            except Exception:
                pass
    return result


def _vendor_for(mac):
    """Devuelve el fabricante por OUI o una etiqueta especial para MAC
    aleatorias. '' si no se puede resolver."""
    if not mac or len(mac) < 8:
        return ""
    if _mac_is_random(mac):
        return "(aleatoria)"
    prefix = mac.replace(":", "").upper()[:6]
    v = _resolve_vendors({prefix}).get(prefix, "")
    return v


# ---------------------------------------------------------------------------
# Power / rfkill
# ---------------------------------------------------------------------------

def power_on(iface=DEFAULT_IFACE):
    """Enciende el controller: rfkill unblock + hciconfig <iface> up.
    Usamos hciconfig en vez de btmgmt porque btmgmt (bluez 5.82) se cuelga
    con stdin cerrado (ver _run). hciconfig es idempotente y responde en ms.
    Devuelve (ok, msg)."""
    _iface_index(iface)  # valida formato
    # 1) rfkill unblock (soft block). Si no esta instalado, se omite.
    if os.path.exists(RFKILL_BIN):
        _run([SUDO, '-n', RFKILL_BIN, 'unblock', 'bluetooth'], timeout=5)
    # 2) up via hciconfig (requiere sudo con NOPASSWD).
    r = _run([SUDO, '-n', HCICONFIG, iface, 'up'], timeout=5)
    if r is None:
        return False, "hciconfig up: sin respuesta (timeout)"
    out = (r.stdout or '') + (r.stderr or '')
    if r.returncode != 0 or 'Operation not permitted' in out or 'Can\'t' in out:
        return False, f"hciconfig {iface} up fallo: {out.strip()[:200]}"
    return True, "powered on"


def power_off(iface=DEFAULT_IFACE):
    _iface_index(iface)
    r = _run([SUDO, '-n', HCICONFIG, iface, 'down'], timeout=5)
    if r is None:
        return False, "hciconfig down: sin respuesta (timeout)"
    return (r.returncode == 0), (r.stdout or r.stderr or '').strip()[:200]


def ctl_info(iface=DEFAULT_IFACE):
    """Datos del controller: address, UP/DOWN, flags. hciconfig -a es
    estable con stdin=DEVNULL (btmgmt info se cuelga ahi). Mantiene las
    mismas claves del return (iface, address, powered, raw) por compat."""
    _iface_index(iface)
    r = _run([SUDO, '-n', HCICONFIG, iface, '-a'], timeout=5)
    if r is None or r.returncode != 0:
        return None
    text = r.stdout or ''
    data = {"iface": iface, "raw": text.strip()[:600]}
    m = re.search(r'BD Address:\s+([0-9A-Fa-f:]{17})', text)
    if m: data["address"] = m.group(1)
    data["powered"] = ('UP RUNNING' in text) or ('\n\tUP ' in text)
    return data


# ---------------------------------------------------------------------------
# Parsers (pura logica, testeables sin tocar hardware)
# ---------------------------------------------------------------------------

_RX_ANSI = re.compile(r'\x1b\[[0-9;]*[A-Za-z]')

_RX_DEV_FOUND = re.compile(
    r'^hci\d+\s+dev_found:\s+'
    r'(?P<mac>[0-9A-Fa-f:]{17})\s+'
    r'type\s+(?P<type>\S+(?:\s+\S+)?)'    # \S+ para que "BR/EDR" entre entero
    r'(?:\s+rssi\s+(?P<rssi>-?\d+))?'
    r'(?:\s+flags\s+0x[0-9A-Fa-f]+)?'
)
_RX_AD_NAME        = re.compile(r'\bname\s+(?:complete\s+)?[\'"]?([^\'"\r\n]+?)[\'"]?\s*$')
_RX_AD_TX_POWER    = re.compile(r'\btx\s*power\s+(-?\d+)', re.I)
_RX_AD_UUID        = re.compile(r'UUID[^0-9A-Fa-f]+([0-9A-Fa-f]{4,8}(?:-[0-9A-Fa-f]{4}){0,3}-?[0-9A-Fa-f]{0,12})')
_RX_AD_MANUF       = re.compile(r'manufacturer(?:\s+specific)?\s+data\s+([0-9A-Fa-f ]+)', re.I)
_RX_AD_APPEARANCE  = re.compile(r'\bappearance\s+(?:0x)?([0-9A-Fa-f]+)', re.I)

# Eventos de bluetoothctl interactivo (btmgmt se cuelga con stdin=DEVNULL, por
# eso scan() usa bluetoothctl en lugar de btmgmt find).
_RX_BCTL_NEW   = re.compile(r'\[NEW\]\s+Device\s+([0-9A-F:]{17})\s+(.*)$')
_RX_BCTL_DEL   = re.compile(r'\[DEL\]\s+Device\s+([0-9A-F:]{17})')
_RX_BCTL_CHG_R = re.compile(r'\[CHG\]\s+Device\s+([0-9A-F:]{17})\s+RSSI(?:\s+is\s+now)?:[^\(]*\((-?\d+)\)')
_RX_BCTL_CHG_N = re.compile(r'\[CHG\]\s+Device\s+([0-9A-F:]{17})\s+Name:\s+(.+?)\s*$')
_RX_BCTL_LIST  = re.compile(r'^Device\s+([0-9A-F:]{17})\s+(.*)$')


def parse_btmgmt_find(text):
    """Parsea el stdout de `btmgmt --index N find [-l|-b]`. Devuelve una lista
    de dicts con las claves: mac, addr_type, rssi, name, tx_power, uuids,
    manufacturer_data, appearance. Nunca lanza; devuelve [] si no hay nada."""
    devices = {}
    cur = None
    for raw in (text or '').splitlines():
        line = raw.rstrip()
        m = _RX_DEV_FOUND.match(line)
        if m:
            mac = m.group('mac').upper()
            type_raw = (m.group('type') or '').strip()
            # type puede ser "BR/EDR", "LE Public", "LE Random", "LE"
            if 'BR/EDR' in type_raw:
                addr_type = 'public'  # BR/EDR siempre publica
                kind = 'classic'
            elif 'Random' in type_raw:
                addr_type = 'random'
                kind = 'le'
            elif 'LE' in type_raw:
                addr_type = 'public'
                kind = 'le'
            else:
                addr_type = ''
                kind = ''
            rssi = m.group('rssi')
            dev = devices.get(mac) or {
                "mac": mac, "addr_type": addr_type, "kind": kind,
                "rssi": None, "name": "", "tx_power": None,
                "uuids": [], "manufacturer_data": "", "appearance": ""
            }
            if rssi is not None:
                try: dev["rssi"] = int(rssi)
                except Exception: pass
            # si lo visto antes era classic y ahora llega LE (o viceversa), marcamos dual
            if dev.get("kind") and kind and dev["kind"] != kind:
                dev["kind"] = "dual"
            else:
                dev["kind"] = dev.get("kind") or kind
            devices[mac] = dev
            cur = dev
            continue
        if cur is None:
            continue
        # Lineas de datos del ultimo dispositivo:
        mn = _RX_AD_NAME.search(line)
        if mn and 'name' in line.lower() and 'uuid' not in line.lower():
            # evitamos pisar un nombre bueno con uno peor
            new_name = mn.group(1).strip()
            if new_name and (not cur["name"] or len(new_name) > len(cur["name"])):
                cur["name"] = new_name[:96]
        mtx = _RX_AD_TX_POWER.search(line)
        if mtx:
            try: cur["tx_power"] = int(mtx.group(1))
            except Exception: pass
        ma = _RX_AD_APPEARANCE.search(line)
        if ma:
            try:
                cur["appearance"] = f"0x{int(ma.group(1), 16):04X}"
            except Exception:
                pass
        mu = _RX_AD_UUID.search(line)
        if mu:
            uu = mu.group(1).lower()
            if uu and uu not in cur["uuids"]:
                cur["uuids"].append(uu)
        md = _RX_AD_MANUF.search(line)
        if md:
            hexd = re.sub(r'\s+', '', md.group(1))
            if hexd:
                cur["manufacturer_data"] = hexd[:200]
    return list(devices.values())


_RX_BCTL_HEADER       = re.compile(r'^Device\s+([0-9A-Fa-f:]{17})\s*(?:\(([^)]+)\))?')
_RX_BCTL_KV           = re.compile(r'^\s*([A-Za-z][A-Za-z\s./]+?):\s*(.+?)\s*$')
_RX_BCTL_UUID         = re.compile(r'^\s*UUID:\s+([^\s(]+(?:\s+[^\s(]+)*)\s*\(([0-9A-Fa-f-]+)\)')
_RX_BCTL_MANUF_KEY    = re.compile(r'^\s*ManufacturerData\.Key:\s*0x([0-9A-Fa-f]+)')


def _is_mac_placeholder(s, mac):
    """bluez usa la MAC con guiones ('AA-BB-CC-DD-EE-FF') como Name/Alias cuando
    no hay nombre real. No es un nombre util; lo descartamos para que no quede
    guardado en la DB ni mostrado en la UI."""
    if not s:
        return False
    if re.fullmatch(r'[0-9A-Fa-f]{2}(?:[-:][0-9A-Fa-f]{2}){5}', s):
        return True
    if mac and s.upper() == mac.upper().replace(':', '-'):
        return True
    return False


def parse_bluetoothctl_info(text):
    """Parsea la salida de `bluetoothctl info <mac>`. Devuelve dict con
    name, alias, appearance, icon, rssi, uuids, manufacturer_key, class, etc."""
    d = {"mac": "", "addr_type": "", "name": "", "alias": "",
         "appearance": "", "icon": "", "rssi": None, "tx_power": None,
         "class": "", "paired": False, "trusted": False, "connected": False,
         "uuids": [], "manufacturer_key": ""}
    for raw in (text or '').splitlines():
        mh = _RX_BCTL_HEADER.match(raw)
        if mh:
            d["mac"] = mh.group(1).upper()
            d["addr_type"] = (mh.group(2) or '').strip().lower()
            continue
        mu = _RX_BCTL_UUID.match(raw)
        if mu:
            label = mu.group(1).strip()
            uuid  = mu.group(2).strip().lower()
            if uuid not in [x.split('|', 1)[0] for x in d["uuids"]]:
                d["uuids"].append(f"{uuid}|{label}" if label else uuid)
            continue
        mk = _RX_BCTL_MANUF_KEY.match(raw)
        if mk:
            d["manufacturer_key"] = f"0x{mk.group(1).lower()}"
            continue
        mv = _RX_BCTL_KV.match(raw)
        if mv:
            k = mv.group(1).strip().lower()
            v = mv.group(2).strip()
            if k == 'name':
                if not _is_mac_placeholder(v, d.get("mac")):
                    d["name"] = v[:96]
            elif k == 'alias':
                if not _is_mac_placeholder(v, d.get("mac")):
                    d["alias"] = v[:96]
            elif k == 'appearance':
                d["appearance"] = v[:48]
            elif k == 'icon':
                d["icon"] = v[:32]
            elif k == 'class':
                d["class"] = v[:16]
            elif k == 'rssi':
                # Formato bluez: "0xffffffbc (-68)". Preferimos el valor en
                # parentesis (int con signo). Fallback a parseo directo.
                m2 = re.search(r'\((-?\d+)\)', v)
                try:
                    d["rssi"] = int(m2.group(1)) if m2 else int(v)
                except Exception:
                    pass
            elif k == 'txpower':
                m2 = re.search(r'\((-?\d+)\)', v)
                try:
                    d["tx_power"] = int(m2.group(1)) if m2 else int(v)
                except Exception:
                    pass
            elif k == 'paired':
                d["paired"] = (v.lower() == 'yes')
            elif k == 'trusted':
                d["trusted"] = (v.lower() == 'yes')
            elif k == 'connected':
                d["connected"] = (v.lower() == 'yes')
    return d


def merge_bt_devices(base, info):
    """Enriquece un device descubierto por btmgmt con los datos de
    bluetoothctl info (nombre completo, UUIDs resueltos, class, icon,
    appearance textual). Nunca pisa valores no vacios de base con vacios
    de info, salvo que info traiga algo nuevo."""
    out = dict(base)
    if not info:
        return out
    name = info.get("name") or info.get("alias") or ""
    if name and (not out.get("name") or len(name) > len(out.get("name") or "")):
        out["name"] = name
    for k in ("appearance", "icon", "class", "manufacturer_key"):
        if info.get(k) and not out.get(k):
            out[k] = info[k]
    if info.get("rssi") is not None and out.get("rssi") is None:
        out["rssi"] = info["rssi"]
    # UUIDs: fusionamos (bluetoothctl los trae con etiqueta humana). Si la
    # misma UUID existe sin label en base y en info viene con label, la
    # version con label gana (es mas util en la UI/DB).
    extra = info.get("uuids") or []
    if extra:
        out_uuids = list(out.get("uuids", []))
        by_key = {u.split('|', 1)[0]: i for i, u in enumerate(out_uuids)}
        for u in extra:
            key = u.split('|', 1)[0]
            if key in by_key:
                existing = out_uuids[by_key[key]]
                if '|' not in existing and '|' in u:
                    out_uuids[by_key[key]] = u
            else:
                out_uuids.append(u)
                by_key[key] = len(out_uuids) - 1
        out["uuids"] = out_uuids
    return out


# ---------------------------------------------------------------------------
# Scan
# ---------------------------------------------------------------------------

def parse_bluetoothctl_scan(text):
    """Parsea el stdout de una sesion interactiva de bluetoothctl mientras
    corre `scan on`. Reconoce eventos `[NEW] Device MAC [name]`, `[CHG] Device
    MAC RSSI: 0x.. (-N)`, `[CHG] Device MAC Name: X`, `[DEL] Device MAC`, y
    el listado final del comando `devices` (`Device MAC Name`). Devuelve lista
    de dicts; nunca lanza."""
    devs = {}
    for raw_line in (text or '').splitlines():
        line = _RX_ANSI.sub('', raw_line).strip()

        m = _RX_BCTL_NEW.match(line)
        if m:
            mac = m.group(1).upper()
            name = (m.group(2) or '').strip()
            d = devs.setdefault(mac, {
                "mac": mac, "addr_type": "", "kind": "le",
                "rssi": None, "name": "", "tx_power": None,
                "uuids": [], "manufacturer_data": "", "appearance": "",
            })
            # Si el "name" viene como MAC con guiones (bluez placeholder), descartar.
            if name and name.upper() != mac.replace(':', '-') and not re.match(r'^[0-9A-F:-]+$', name):
                d["name"] = name[:96]
            continue

        m = _RX_BCTL_CHG_R.match(line)
        if m:
            mac = m.group(1).upper()
            try:
                rssi = int(m.group(2))
            except Exception:
                rssi = None
            d = devs.setdefault(mac, {"mac": mac, "addr_type": "", "kind": "le",
                                      "rssi": None, "name": "", "tx_power": None,
                                      "uuids": [], "manufacturer_data": "",
                                      "appearance": ""})
            if rssi is not None:
                d["rssi"] = rssi
            continue

        m = _RX_BCTL_CHG_N.match(line)
        if m:
            mac = m.group(1).upper()
            name = m.group(2).strip()
            d = devs.setdefault(mac, {"mac": mac, "addr_type": "", "kind": "le",
                                      "rssi": None, "name": "", "tx_power": None,
                                      "uuids": [], "manufacturer_data": "",
                                      "appearance": ""})
            if name and not re.match(r'^[0-9A-F:-]+$', name):
                d["name"] = name[:96]
            continue

        m = _RX_BCTL_DEL.match(line)
        if m:
            # un peer salio de rango; lo dejamos (ya lo vimos). no-op.
            continue

        m = _RX_BCTL_LIST.match(line)
        if m:
            mac = m.group(1).upper()
            name = (m.group(2) or '').strip()
            # Si lo que sigue a la MAC es '(public)' / '(random)' etc., es el
            # header de un bloque `info`, no una entrada del comando `devices`;
            # el bloque lo procesa _split_info_blocks y no debemos tomar name aqui.
            if name.startswith('(') and name.endswith(')'):
                continue
            d = devs.setdefault(mac, {"mac": mac, "addr_type": "", "kind": "le",
                                      "rssi": None, "name": "", "tx_power": None,
                                      "uuids": [], "manufacturer_data": "",
                                      "appearance": ""})
            if not d["name"] and name and name.upper() != mac.replace(':', '-'):
                if not re.match(r'^[0-9A-F:-]+$', name):
                    d["name"] = name[:96]
            continue
    return list(devs.values())


def _bd_address(iface):
    """iface -> BD address (ej 'B8:27:EB:B3:66:DB'), o '' si no se puede."""
    info = ctl_info(iface)
    return info.get("address", "") if info else ""


def _split_info_blocks(text):
    """Separa el stream de bluetoothctl interactivo en bloques `info <mac>`.
    Cada vez que vemos una linea "Device MAC (type)" abrimos bloque; cerramos
    al proximo header o al prompt "[bluetooth]#". Retorna {MAC: texto_bloque}.
    Util para enriquecer el scan con los datos que vienen de `info <mac>`
    (RSSI, UUIDs, appearance, icon, class) mientras el scan sigue activo."""
    blocks = {}
    cur_mac = None
    cur_lines = []
    for raw in (text or '').splitlines():
        line = _RX_ANSI.sub('', raw)
        m = re.match(r'^Device\s+([0-9A-F:]{17})\s*\(', line)
        if m:
            if cur_mac:
                blocks[cur_mac] = '\n'.join(cur_lines)
            cur_mac = m.group(1).upper()
            cur_lines = [line]
            continue
        # cierre si llegamos a un prompt o a un nuevo comando
        if line.lstrip().startswith('[bluetooth]#') or line.lstrip().startswith('[CHG]') or line.lstrip().startswith('[NEW]'):
            if cur_mac:
                blocks[cur_mac] = '\n'.join(cur_lines)
                cur_mac = None
                cur_lines = []
            continue
        if cur_mac:
            cur_lines.append(line)
    if cur_mac:
        blocks[cur_mac] = '\n'.join(cur_lines)
    return blocks


def scan(iface=DEFAULT_IFACE, seconds=10, mode='dual'):
    """Barrido de dispositivos BT con bluetoothctl interactivo. Devuelve
    (devices, err).

    Importante: NO usamos `btmgmt find` porque btmgmt 5.82 se cuelga cuando
    stdin es /dev/null (confirmado el 2026-10-27 bajo systemd). bluetoothctl
    por D-Bus no sufre ese bug y ademas da RSSI + nombres resueltos cuando
    el scan sigue activo."""
    try:
        _iface_index(iface)
    except ValueError as e:
        return None, str(e)
    seconds = max(2, min(int(seconds or 10), 120))
    mode = (mode or 'dual').lower()

    ok, msg = power_on(iface)
    if not ok:
        return None, f"No se pudo encender el controller: {msg}"

    bd = _bd_address(iface)
    if not bd:
        return None, f"No se pudo obtener BD address de {iface}"

    # bluetoothctl interactivo con reader thread para capturar el stream mientras
    # inyectamos comandos. Flow:
    #   select <bd> + scan on + sleep (seconds-2)
    #   -> snapshot de MACs vistas ([NEW] events)
    #   -> info <mac> por cada (asi vienen RSSI/UUIDs/appearance/class/addr_type)
    #   sleep (0.1 por mac, floor 1s)
    #   scan off + devices + quit
    proc = subprocess.Popen(
        [BLUETOOTHCTL],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, text=True, bufsize=1,
    )
    lines = []
    stop = threading.Event()

    def _reader():
        try:
            for line in iter(proc.stdout.readline, ''):
                if stop.is_set():
                    break
                lines.append(line)
        except Exception:
            pass

    t = threading.Thread(target=_reader, daemon=True)
    t.start()

    try:
        proc.stdin.write(f"select {bd}\n")
        proc.stdin.write("scan on\n")
        proc.stdin.flush()
        pre = max(1, seconds - 2)
        time.sleep(pre)

        # Snapshot de MACs vistas por [NEW] events hasta el momento.
        snapshot = ''.join(lines)
        clean = _RX_ANSI.sub('', snapshot)
        macs = []
        seen = set()
        for m in re.finditer(r'\[NEW\]\s+Device\s+([0-9A-F:]{17})', clean):
            mm = m.group(1).upper()
            if mm not in seen:
                seen.add(mm); macs.append(mm)
        macs = macs[:48]   # tope duro para no abusar

        # Pedir info de cada MAC mientras el scan sigue activo (RSSI fresca).
        for m in macs:
            proc.stdin.write(f"info {m}\n")
        proc.stdin.flush()
        time.sleep(max(1.0, 0.12 * len(macs)))

        proc.stdin.write("scan off\n")
        proc.stdin.write("devices\n")
        proc.stdin.flush()
        time.sleep(0.4)
        proc.stdin.write("quit\n")
        proc.stdin.flush()
        try:
            proc.wait(timeout=seconds + 10)
        except subprocess.TimeoutExpired:
            proc.kill()
    except Exception as e:
        try: proc.kill()
        except Exception: pass
        stop.set(); t.join(timeout=1)
        return None, f"bluetoothctl fallo: {e}"

    stop.set()
    t.join(timeout=1)
    out = ''.join(lines)

    # 1) MACs + name + eventos del scan.
    found = parse_bluetoothctl_scan(out)
    by_mac = {d["mac"]: d for d in found}

    # 2) Enriquecer con los bloques `info` (RSSI/UUIDs/appearance/class/icon/addr_type).
    for mac, block in _split_info_blocks(out).items():
        info = parse_bluetoothctl_info(block)
        if mac in by_mac:
            by_mac[mac] = merge_bt_devices(by_mac[mac], info)
            # addr_type del info: si venia vacio en el scan, lo tomamos.
            if info.get("addr_type") and not by_mac[mac].get("addr_type"):
                by_mac[mac]["addr_type"] = info["addr_type"]
            # kind: si es "random" o "public" tipico BLE, kind=le; "br/edr" -> classic
            at = (info.get("addr_type") or '').lower()
            if 'br' in at:
                by_mac[mac]["kind"] = "classic"
            elif at in ("public", "random"):
                by_mac[mac]["kind"] = "le"
        else:
            # Device solo visto en info (raro); lo agregamos.
            base = {"mac": mac, "addr_type": "", "kind": "le", "rssi": None,
                    "name": "", "tx_power": None, "uuids": [],
                    "manufacturer_data": "", "appearance": ""}
            by_mac[mac] = merge_bt_devices(base, info)

    devices = list(by_mac.values())
    for d in devices:
        d["vendor"] = _vendor_for(d["mac"])

    # Log en DB (best-effort; nunca tumba el scan).
    log_bluetooth_batch(iface, mode, devices)
    return devices, None


# ---------------------------------------------------------------------------
# Logging a DB (best-effort)
# ---------------------------------------------------------------------------

def _db_import():
    """Patron del repo: inyectar app/ en sys.path y usar SQLAlchemy."""
    try:
        base = os.path.expanduser("~/piscanlvgl/app")
        if base not in sys.path:
            sys.path.insert(0, base)
        from db.models import get_session, BluetoothLog
        return get_session, BluetoothLog
    except Exception:
        return None, None


def log_bluetooth_batch(iface, mode, devices):
    if not devices:
        return
    get_session, BluetoothLog = _db_import()
    if get_session is None:
        return
    try:
        s = get_session()
        for d in devices:
            uuids_str = ",".join(u for u in d.get("uuids") or [])[:1800]
            services_label = []
            # bluetoothctl UUIDs vienen como "uuid|Label"; preferimos labels
            for u in d.get("uuids") or []:
                parts = u.split('|', 1)
                services_label.append(parts[1] if len(parts) == 2 else parts[0])
            svc = ", ".join(services_label)[:1800]
            s.add(BluetoothLog(
                scan_type=mode,
                iface=iface,
                mac=d.get("mac"),
                addr_type=d.get("addr_type") or None,
                name=(d.get("name") or None),
                vendor=d.get("vendor") or None,
                rssi=d.get("rssi"),
                tx_power=d.get("tx_power"),
                appearance=(d.get("appearance") or None),
                cls=(d.get("class") or None),
                icon=(d.get("icon") or None),
                services=svc or uuids_str or None,
                manufacturer_data=(d.get("manufacturer_data")
                                   or d.get("manufacturer_key") or None),
                details=None,
            ))
        s.commit(); s.close()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Pentesting / auditoria: SDP (BR/EDR) + GATT (LE) + L2ping (BR/EDR)
# Marco: SOLO redes y dispositivos propios o con autorizacion explicita.
# ---------------------------------------------------------------------------

def log_bt_attack(action, target_mac, result, details=None, target_name=None):
    """Inserta una fila en bluetooth_attack_log. Silencia fallos de DB."""
    try:
        base = os.path.expanduser("~/piscanlvgl/app")
        if base not in sys.path:
            sys.path.insert(0, base)
        from db.models import get_session, BluetoothAttackLog
        s = get_session()
        s.add(BluetoothAttackLog(
            action=(action or "")[:32],
            target_mac=(target_mac or "")[:24],
            target_name=(target_name or "")[:96] if target_name else None,
            result=(result or "")[:16],
            details=(details or "")[:1800] if details else None,
        ))
        s.commit(); s.close()
    except Exception:
        pass


_RX_SDP_RECORD = re.compile(r'^Service Name:\s*(.+?)\s*$')
_RX_SDP_RHANDLE = re.compile(r'^Service RecHandle:\s*(0x[0-9A-Fa-f]+)')
_RX_SDP_CLASS   = re.compile(r'^"?([^"]+)"?\s*\((0x[0-9A-Fa-f]+)\)')


def parse_sdptool_browse(text):
    """Parsea `sdptool browse <mac>`. Devuelve lista de servicios BR/EDR:
    [{name, rec_handle, classes:[(name,uuid16)]}]. Nunca lanza."""
    services = []
    cur = None
    in_classes = False
    for raw in (text or '').splitlines():
        line = raw.rstrip()
        m = _RX_SDP_RECORD.match(line.strip())
        if m:
            if cur: services.append(cur)
            cur = {"name": m.group(1).strip(), "rec_handle": "", "classes": []}
            in_classes = False
            continue
        if cur is None:
            continue
        mh = _RX_SDP_RHANDLE.match(line.strip())
        if mh:
            cur["rec_handle"] = mh.group(1)
            continue
        if line.strip().startswith('Service Class ID List'):
            in_classes = True
            continue
        if in_classes:
            mc = _RX_SDP_CLASS.match(line.strip())
            if mc:
                cur["classes"].append((mc.group(1).strip(), mc.group(2)))
            elif line.strip() == "" or (line and not line.startswith((' ', '\t'))):
                in_classes = False
    if cur: services.append(cur)
    return services


def sdp_browse(mac, timeout=15):
    """Enumera servicios BR/EDR del target via SDP (sin pairing). Util para
    identificar perfil (A2DP/HFP/HID/OBEX...) y evaluar superficie de ataque.
    Nota: solo funciona con dispositivos BR/EDR; muchos LE-only responden vacio."""
    mac = (mac or '').strip().upper()
    if not re.fullmatch(r'[0-9A-F:]{17}', mac):
        return None, "MAC invalida"
    r = _run([SUDO, '-n', SDPTOOL, 'browse', mac], timeout=timeout)
    text = ""
    if r:
        text = (r.stdout or '') + (r.stderr or '')
    services = parse_sdptool_browse(text)
    err = None
    if (r is None) or (r.returncode != 0 and not services):
        err = (text.strip() or "sin respuesta")[:200]
    result = "ok" if services else ("fail" if err else "empty")
    log_bt_attack("sdp_browse", mac, result,
                  details=f"services={len(services)}" + (f" err={err}" if err else ""))
    return {"services": services, "raw": text[-800:]}, err


_RX_GATT_SERVICE = re.compile(r'Service\s+(?:Attribute\s+)?/org/bluez/[^\s]+/service([0-9a-f]+)\s*(?:$|\s)', re.I)
# UUID 128-bit canonical dentro de parentesis (bluetoothctl format):
#   "UUID: Generic Access Profile    (00001800-0000-1000-8000-00805f9b34fb)"
_RX_GATT_UUID    = re.compile(r'\(([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})\)')
_RX_GATT_LABEL   = re.compile(r'UUID:\s+(.+?)\s*\([0-9a-fA-F-]+\)\s*$')


def parse_bluetoothctl_gatt(text):
    """Parsea el stream interactivo de `menu gatt` + `list-attributes`:
       Primary Service (Handle 0x????)
            /org/bluez/hci0/dev_XX/serviceNN
            UUID: 00001800-0000-1000-8000-00805f9b34fb
       Characteristic (Handle 0x????)
            /org/bluez/hci0/dev_XX/serviceNN/charMM
            UUID: ...
    Devuelve lista de dicts {kind:'service'|'characteristic', path, uuid, label}."""
    attrs = []
    cur = None
    for raw in (text or '').splitlines():
        line = _RX_ANSI.sub('', raw).strip()
        low = line.lower()
        if 'primary service' in low:
            if cur: attrs.append(cur)
            cur = {"kind": "service", "path": "", "uuid": "", "label": ""}
            continue
        if low.startswith('characteristic'):
            if cur: attrs.append(cur)
            cur = {"kind": "characteristic", "path": "", "uuid": "", "label": ""}
            continue
        if low.startswith('descriptor'):
            if cur: attrs.append(cur)
            cur = {"kind": "descriptor", "path": "", "uuid": "", "label": ""}
            continue
        if cur is None:
            continue
        if line.startswith('/org/bluez/'):
            cur["path"] = line
            continue
        if 'UUID:' in line or line.lstrip().startswith('UUID'):
            mu = _RX_GATT_UUID.search(line)
            if mu:
                cur["uuid"] = mu.group(1).lower()
            ml = _RX_GATT_LABEL.search(line)
            if ml:
                cur["label"] = ml.group(1).strip()[:48]
            continue
    if cur: attrs.append(cur)
    return attrs


def gatt_enum(mac, iface=DEFAULT_IFACE, timeout=30):
    """Enumera services + characteristics GATT de un device LE. Conecta via
    bluetoothctl interactivo (sin pairing para servicios publicos), lista los
    atributos, desconecta. Si el device requiere pairing o rechaza, devuelve
    error con detalle. SOLO lab."""
    mac = (mac or '').strip().upper()
    if not re.fullmatch(r'[0-9A-F:]{17}', mac):
        return None, "MAC invalida"
    try:
        _iface_index(iface)
    except ValueError as e:
        return None, str(e)
    ok, msg = power_on(iface)
    if not ok:
        return None, f"No se pudo encender {iface}: {msg}"
    bd = _bd_address(iface)
    if not bd:
        return None, f"No se pudo obtener BD de {iface}"

    proc = subprocess.Popen(
        [BLUETOOTHCTL],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, text=True, bufsize=1,
    )
    lines = []
    stop = threading.Event()
    def _rd():
        try:
            for line in iter(proc.stdout.readline, ''):
                if stop.is_set(): break
                lines.append(line)
        except Exception:
            pass
    t = threading.Thread(target=_rd, daemon=True); t.start()

    err = None
    try:
        proc.stdin.write(f"select {bd}\n")
        proc.stdin.write(f"connect {mac}\n")
        proc.stdin.flush()
        # Esperamos a Connected: yes o fallo (hasta 10s)
        t0 = time.time()
        connected = False
        while time.time() - t0 < 10:
            if any('Connected: yes' in l for l in lines):
                connected = True; break
            if any('Failed to connect' in l or 'org.bluez.Error' in l for l in lines):
                err = "connect fallo"; break
            time.sleep(0.3)
        if connected:
            # Dar tiempo al GATT discovery
            time.sleep(3)
            proc.stdin.write("menu gatt\n")
            proc.stdin.write("list-attributes\n")
            proc.stdin.flush()
            time.sleep(2)
            proc.stdin.write("back\n")
            proc.stdin.write(f"disconnect {mac}\n")
            proc.stdin.flush()
            time.sleep(1)
        proc.stdin.write("quit\n")
        proc.stdin.flush()
        try: proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired: proc.kill()
    except Exception as e:
        try: proc.kill()
        except Exception: pass
        stop.set(); t.join(timeout=1)
        return None, f"bluetoothctl fallo: {e}"

    stop.set(); t.join(timeout=1)
    out = ''.join(lines)
    if err is None and 'Failed to connect' in out:
        err = "connect fallo"
    attrs = parse_bluetoothctl_gatt(out)
    services = [a for a in attrs if a["kind"] == "service"]
    chars    = [a for a in attrs if a["kind"] == "characteristic"]

    result = "ok" if attrs else ("fail" if err else "empty")
    log_bt_attack("gatt_enum", mac, result,
                  details=f"services={len(services)} chars={len(chars)}" +
                          (f" err={err}" if err else ""))
    return {"services": services, "characteristics": chars,
            "raw": out[-1500:]}, err


_RX_L2PING_SENT = re.compile(r'^(\d+)\s+bytes\s+from\s+([0-9A-F:]{17})\s+id\s+(\d+)\s+time\s+([\d.]+)\s*ms', re.I)
_RX_L2PING_STAT = re.compile(r'^(\d+)\s+sent,\s+(\d+)\s+received,\s+(\d+)%\s+loss', re.I)


def parse_l2ping_output(text):
    """Parsea `l2ping` output. Devuelve stats: {sent, received, loss_pct,
    avg_ms, min_ms, max_ms, samples:[{id, time_ms}]}."""
    samples = []
    for raw in (text or '').splitlines():
        m = _RX_L2PING_SENT.match(raw.strip())
        if m:
            try:
                samples.append({"id": int(m.group(3)),
                                "time_ms": float(m.group(4))})
            except Exception:
                pass
    stats = {"sent": 0, "received": 0, "loss_pct": None,
             "avg_ms": None, "min_ms": None, "max_ms": None,
             "samples": samples}
    for raw in (text or '').splitlines():
        m = _RX_L2PING_STAT.match(raw.strip())
        if m:
            try:
                stats["sent"] = int(m.group(1))
                stats["received"] = int(m.group(2))
                stats["loss_pct"] = int(m.group(3))
            except Exception:
                pass
    if samples:
        times = [s["time_ms"] for s in samples]
        stats["avg_ms"] = round(sum(times) / len(times), 2)
        stats["min_ms"] = round(min(times), 2)
        stats["max_ms"] = round(max(times), 2)
    return stats


def l2ping(mac, count=5, size=44, timeout=20):
    """L2CAP ping (BR/EDR). Prueba de alcance/latencia y stress leve. SOLO
    funciona con BR/EDR classic; BLE-only responde 'Host is down' u otro error.
    SOLO lab."""
    mac = (mac or '').strip().upper()
    if not re.fullmatch(r'[0-9A-F:]{17}', mac):
        return None, "MAC invalida"
    count = max(1, min(int(count or 5), 50))
    size = max(1, min(int(size or 44), 600))
    r = _run([SUDO, '-n', L2PING, '-c', str(count), '-s', str(size), mac],
             timeout=timeout)
    text = ""
    if r:
        text = (r.stdout or '') + (r.stderr or '')
    err = None
    if r is None:
        err = "l2ping: sin respuesta"
    elif r.returncode != 0 and 'bytes from' not in text:
        err = text.strip()[-200:] or "l2ping fallo"
    stats = parse_l2ping_output(text)
    result = "ok" if stats["received"] > 0 else ("fail" if err else "empty")
    log_bt_attack("l2ping", mac, result,
                  details=f"sent={stats['sent']} recv={stats['received']} loss={stats['loss_pct']}% avg={stats['avg_ms']}ms")
    return {"stats": stats, "raw": text[-600:]}, err


# ---------------------------------------------------------------------------
# Show (bluetoothctl info enriquecido, para una MAC concreta)
# ---------------------------------------------------------------------------

def show(mac, iface=DEFAULT_IFACE):
    rr = _run([BLUETOOTHCTL, '--timeout', '3', 'info', mac], timeout=10)
    if not rr or not rr.stdout:
        return None, "sin respuesta de bluetoothctl"
    d = parse_bluetoothctl_info(rr.stdout)
    d["vendor"] = _vendor_for(d.get("mac") or mac)
    return d, None


# ---------------------------------------------------------------------------
# CLI dispatcher
# ---------------------------------------------------------------------------

def _emit(obj):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass
    try:
        print(json.dumps(obj, ensure_ascii=True))
    except Exception as e:
        print(json.dumps({"ok": False, "error": f"json: {e}"}))


def main():
    if len(sys.argv) < 2:
        _emit({"ok": False, "error": "uso: bt_ops.py <up|down|info|scan|show|sdp|gatt|l2ping> [args]"})
        sys.exit(1)
    cmd = sys.argv[1]
    iface = DEFAULT_IFACE
    if len(sys.argv) > 2 and re.match(r'^hci\d+$', sys.argv[2]):
        iface = sys.argv[2]

    try:
        if cmd == 'up':
            ok, msg = power_on(iface)
            _emit({"ok": ok, "iface": iface, "msg": msg})
        elif cmd == 'down':
            ok, msg = power_off(iface)
            _emit({"ok": ok, "iface": iface, "msg": msg})
        elif cmd == 'info':
            info = ctl_info(iface)
            if info is None:
                _emit({"ok": False, "error": "no se pudo obtener info del controller"})
                sys.exit(1)
            _emit({"ok": True, "controller": info})
        elif cmd == 'scan':
            # scan [iface] [seconds] [mode]
            args = sys.argv[2:]
            if args and re.match(r'^hci\d+$', args[0]):
                args = args[1:]
            seconds = int(args[0]) if args and args[0].isdigit() else 10
            mode = args[1] if len(args) > 1 else 'dual'
            devs, err = scan(iface, seconds, mode)
            if err:
                _emit({"ok": False, "error": err, "iface": iface})
                sys.exit(1)
            _emit({"ok": True, "iface": iface, "mode": mode,
                   "seconds": seconds, "count": len(devs or []),
                   "devices": devs or []})
        elif cmd == 'show':
            args = sys.argv[2:]
            if args and re.match(r'^hci\d+$', args[0]):
                args = args[1:]
            if not args:
                _emit({"ok": False, "error": "uso: bt_ops.py show [iface] <mac>"})
                sys.exit(1)
            mac = args[0].upper()
            data, err = show(mac, iface)
            if err:
                _emit({"ok": False, "error": err})
                sys.exit(1)
            _emit({"ok": True, "device": data})
        elif cmd == 'sdp':
            # bt_ops.py sdp <mac>
            args = sys.argv[2:]
            if args and re.match(r'^hci\d+$', args[0]):
                args = args[1:]
            if not args:
                _emit({"ok": False, "error": "uso: bt_ops.py sdp <mac>"})
                sys.exit(1)
            data, err = sdp_browse(args[0].upper())
            if err and not data:
                _emit({"ok": False, "error": err})
                sys.exit(1)
            _emit({"ok": True, "mac": args[0].upper(),
                   "services": data.get("services", []),
                   "raw": data.get("raw", ""), "warn": err})
        elif cmd == 'gatt':
            # bt_ops.py gatt [iface] <mac>
            args = sys.argv[2:]
            if args and re.match(r'^hci\d+$', args[0]):
                args = args[1:]
            if not args:
                _emit({"ok": False, "error": "uso: bt_ops.py gatt [iface] <mac>"})
                sys.exit(1)
            data, err = gatt_enum(args[0].upper(), iface)
            if err and not data:
                _emit({"ok": False, "error": err})
                sys.exit(1)
            _emit({"ok": True, "mac": args[0].upper(),
                   "services": data.get("services", []),
                   "characteristics": data.get("characteristics", []),
                   "raw": data.get("raw", ""), "warn": err})
        elif cmd == 'l2ping':
            # bt_ops.py l2ping <mac> [count] [size]
            args = sys.argv[2:]
            if args and re.match(r'^hci\d+$', args[0]):
                args = args[1:]
            if not args:
                _emit({"ok": False, "error": "uso: bt_ops.py l2ping <mac> [count] [size]"})
                sys.exit(1)
            mac = args[0].upper()
            count = int(args[1]) if len(args) > 1 and args[1].isdigit() else 5
            size  = int(args[2]) if len(args) > 2 and args[2].isdigit() else 44
            data, err = l2ping(mac, count, size)
            if err and not (data and data.get("stats", {}).get("received", 0) > 0):
                _emit({"ok": False, "error": err, "stats": (data or {}).get("stats")})
                sys.exit(1)
            _emit({"ok": True, "mac": mac,
                   "stats": data.get("stats"), "raw": data.get("raw", ""),
                   "warn": err})
        else:
            _emit({"ok": False, "error": f"comando desconocido: {cmd}"})
            sys.exit(1)
    except Exception as e:
        _emit({"ok": False, "error": f"excepcion: {e}"})
        sys.exit(1)


if __name__ == "__main__":
    # Envoltorio con log centralizado (ActionLog): captura stdout + exit code.
    try:
        _here = os.path.dirname(os.path.abspath(__file__))
        _app  = os.path.abspath(os.path.join(_here, ".."))
        if _app not in sys.path:
            sys.path.insert(0, _app)
        from db.action_log import run_cli_with_log
        run_cli_with_log("bluetooth", main)
    except Exception:
        # Si el logging no esta disponible, no interrumpimos el funcionamiento.
        main()

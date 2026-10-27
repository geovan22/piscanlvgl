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
import sys, os, re, json, subprocess, time

# Rutas absolutas de los binarios usados (coherente con wifi_ops.py / sudoers).
BTMGMT       = "/usr/bin/btmgmt"
BLUETOOTHCTL = "/usr/bin/bluetoothctl"
TIMEOUT_BIN  = "/usr/bin/timeout"
STDBUF_BIN   = "/usr/bin/stdbuf"
RFKILL_BIN   = "/usr/sbin/rfkill"
SUDO         = "/usr/bin/sudo"

DEFAULT_IFACE = "hci1"


def _run(cmd, timeout=15):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
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
    """Enciende el controller: rfkill unblock (si rfkill esta) + btmgmt power on.
    Devuelve (ok, msg)."""
    idx = _iface_index(iface)
    # 1) rfkill unblock (soft block). Si no esta instalado, intentamos tocar /sys.
    if os.path.exists(RFKILL_BIN):
        _run([SUDO, '-n', RFKILL_BIN, 'unblock', 'bluetooth'])
    else:
        # Fallback best-effort: a veces /sys/class/rfkill/<N>/soft es escribible via sudo tee.
        pass
    # 2) power on via btmgmt (requiere sudo con NOPASSWD).
    r = _run([SUDO, '-n', BTMGMT, '--index', idx, 'power', 'on'], timeout=10)
    if r is None:
        return False, "btmgmt power on: sin respuesta"
    out = (r.stdout or '') + (r.stderr or '')
    if r.returncode != 0 or 'Permission Denied' in out or 'failed' in out.lower():
        return False, f"power on fallo: {out.strip()[:200]}"
    return True, "powered on"


def power_off(iface=DEFAULT_IFACE):
    idx = _iface_index(iface)
    r = _run([SUDO, '-n', BTMGMT, '--index', idx, 'power', 'off'], timeout=10)
    if r is None:
        return False, "btmgmt power off: sin respuesta"
    return (r.returncode == 0), (r.stdout or r.stderr or '').strip()[:200]


def ctl_info(iface=DEFAULT_IFACE):
    """Datos del controller: version, address, powered, etc."""
    idx = _iface_index(iface)
    r = _run([SUDO, '-n', BTMGMT, '--index', idx, 'info'], timeout=5)
    if r is None or r.returncode != 0:
        return None
    text = r.stdout or ''
    data = {"iface": iface, "raw": text.strip()[:600]}
    m = re.search(r'addr\s+([0-9A-F:]{17})', text)
    if m: data["address"] = m.group(1)
    m = re.search(r'version\s+(\d+)', text)
    if m: data["version"] = int(m.group(1))
    m = re.search(r'manufacturer\s+(\d+)', text)
    if m: data["manufacturer"] = int(m.group(1))
    m = re.search(r'name\s+([^\r\n]+)', text)
    if m: data["name"] = m.group(1).strip()
    m = re.search(r'current settings:\s*([^\r\n]+)', text)
    if m: data["settings"] = m.group(1).strip()
    data["powered"] = bool(data.get("settings") and 'powered' in data["settings"])
    return data


# ---------------------------------------------------------------------------
# Parsers (pura logica, testeables sin tocar hardware)
# ---------------------------------------------------------------------------

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
                d["name"] = v[:96]
            elif k == 'alias':
                d["alias"] = v[:96]
            elif k == 'appearance':
                d["appearance"] = v[:48]
            elif k == 'icon':
                d["icon"] = v[:32]
            elif k == 'class':
                d["class"] = v[:16]
            elif k == 'rssi':
                try: d["rssi"] = int(v)
                except Exception: pass
            elif k == 'txpower':
                try: d["tx_power"] = int(v)
                except Exception: pass
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

def scan(iface=DEFAULT_IFACE, seconds=10, mode='dual'):
    """Barrido de dispositivos BT. Enciende el controller, corre btmgmt find
    durante `seconds` y enriquece con bluetoothctl info. Devuelve
    (devices, err)."""
    try:
        idx = _iface_index(iface)
    except ValueError as e:
        return None, str(e)
    seconds = max(2, min(int(seconds or 10), 120))
    mode = (mode or 'dual').lower()

    ok, msg = power_on(iface)
    if not ok:
        return None, f"No se pudo encender el controller: {msg}"

    args = [SUDO, '-n', TIMEOUT_BIN, str(seconds + 1),
            STDBUF_BIN, '-oL', '-eL',
            BTMGMT, '--index', idx, 'find']
    if mode == 'le':
        args.append('-l')
    elif mode == 'classic':
        args.append('-b')
    # dual: sin flag (default es BR/EDR + LE)

    r = _run(args, timeout=seconds + 10)
    raw = ((r.stdout if r else '') or '') + ((r.stderr if r else '') or '')
    found = parse_btmgmt_find(raw)

    # Enriquecer con bluetoothctl info (sin sudo; habla con bluez por D-Bus).
    enriched = []
    for d in found:
        info_text = ""
        rr = _run([BLUETOOTHCTL, '--timeout', '2', 'info', d["mac"]], timeout=8)
        if rr and rr.stdout:
            info_text = rr.stdout
        info = parse_bluetoothctl_info(info_text) if info_text else {}
        merged = merge_bt_devices(d, info)
        # vendor (OUI o 'aleatoria')
        merged["vendor"] = _vendor_for(merged["mac"])
        enriched.append(merged)

    # Log en DB (best-effort; nunca tumba el scan)
    log_bluetooth_batch(iface, mode, enriched)
    return enriched, None


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
        _emit({"ok": False, "error": "uso: bt_ops.py <up|down|info|scan|show> [args]"})
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

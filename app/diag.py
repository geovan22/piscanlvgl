#!/usr/bin/env python3
"""
diag.py — Self-check / "doctor" de PiScan. Verifica que la Pi tenga todo lo
necesario para operar en campo SIN SSH: binarios de las herramientas, entradas
sudoers, adaptadores WiFi (wlan0 gestion / wlan1 ataque RTL8821CU) y la DB.

Salida: UN SOLO JSON por stdout, mismo contrato que wifi_ops.py / db_tool.py.
  {"ok": bool, "summary": {"ok": n, "warn": n, "fail": n}, "checks": [ ... ]}
Cada check: {"category", "name", "status": ok|warn|fail, "detail"}.
`ok` global = no hay ningun check en estado `fail` (los `warn` no tumban).

La logica pura (parseo de sudoers/iw, resolucion de binarios, decision global)
esta separada de la inspeccion del entorno para poder testearla sin hardware.
"""
import sys, os, json, shutil, subprocess

# (nombre, [rutas absolutas candidatas], feature, critico)
REQUIRED_BINARIES = [
    ("iw",            ["/usr/sbin/iw"],                         "core",     True),
    ("ip",            ["/usr/bin/ip", "/sbin/ip"],              "core",     True),
    ("nmcli",         ["/usr/bin/nmcli"],                       "core",     True),
    ("timeout",       ["/usr/bin/timeout"],                     "core",     True),
    ("pkill",         ["/usr/bin/pkill"],                       "core",     False),
    ("airodump-ng",   ["/usr/sbin/airodump-ng"],                "handshake", True),
    ("aireplay-ng",   ["/usr/sbin/aireplay-ng"],                "deauth",    True),
    ("aircrack-ng",   ["/usr/bin/aircrack-ng"],                 "audit",     True),
    ("hcxdumptool",   ["/usr/bin/hcxdumptool"],                 "pmkid",    False),
    ("hcxpcapngtool", ["/usr/bin/hcxpcapngtool"],               "pmkid",    False),
    ("mdk4",          ["/usr/sbin/mdk4"],                       "beacon",   False),
    ("hostapd",       ["/usr/sbin/hostapd"],                    "honeypot", False),
    ("dnsmasq",       ["/usr/sbin/dnsmasq"],                    "honeypot", False),
    ("nft",           ["/usr/sbin/nft", "/sbin/nft"],           "honeypot", False),
    ("tcpdump",       ["/usr/bin/tcpdump", "/usr/sbin/tcpdump"], "honeypot", False),
    ("tshark",        ["/usr/bin/tshark", "/usr/sbin/tshark"],  "honeypot", False),
    # WPS (WiFi ampliado) — opcionales hasta instalarlos (apt install reaver bully)
    ("wash",          ["/usr/bin/wash", "/usr/sbin/wash"],      "wps",      False),
    ("reaver",        ["/usr/bin/reaver", "/usr/sbin/reaver"],  "wps",      False),
    ("bully",         ["/usr/bin/bully", "/usr/sbin/bully"],    "wps",      False),
    # Bluetooth (Fase 3) — bluetoothctl viene con bluez; hciconfig es el que
    # usamos para power on (btmgmt se cuelga con stdin=/dev/null). rfkill
    # desbloquea el soft-block del controller.
    ("bluetoothctl",  ["/usr/bin/bluetoothctl"],                "bluetooth", False),
    ("hciconfig",     ["/usr/bin/hciconfig", "/bin/hciconfig"], "bluetooth", False),
    ("btmgmt",        ["/usr/bin/btmgmt"],                      "bluetooth", False),
    ("hcitool",       ["/usr/bin/hcitool"],                     "bluetooth", False),
    ("rfkill",        ["/usr/sbin/rfkill", "/usr/bin/rfkill"],  "bluetooth", False),
]

EXPECTED_WLAN1_DRIVER = "rtw88_8821cu"
SUDOERS_INSTALLED = "/etc/sudoers.d/piscan-wifi"


# ───────────────────────── logica pura (testeable) ─────────────────────────

def resolve_binary(name, candidates):
    """Devuelve la ruta del binario (PATH o alguna candidata ejecutable) o None."""
    found = shutil.which(name)
    if found:
        return found
    for c in candidates:
        if os.path.isfile(c) and os.access(c, os.X_OK):
            return c
    return None


def check_binaries(spec=REQUIRED_BINARIES, resolver=resolve_binary):
    checks = []
    for name, candidates, feature, critical in spec:
        path = resolver(name, candidates)
        if path:
            status, detail = "ok", path
        elif critical:
            status, detail = "fail", "FALTA (critico)"
        else:
            status, detail = "warn", "no instalado (opcional)"
        checks.append({"category": "bin/" + feature, "name": name,
                       "status": status, "detail": detail})
    return checks


def parse_sudoers(text):
    """Extrae el conjunto de rutas absolutas con NOPASSWD de un sudoers."""
    paths = set()
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or line.startswith('#') or 'NOPASSWD:' not in line:
            continue
        tail = line.split('NOPASSWD:', 1)[1].strip()
        for item in tail.split(','):
            item = item.strip()
            if not item:
                continue
            first = item.split()[0]
            if first.startswith('/'):
                paths.add(first)
    return paths


def missing_sudoers(installed_text, required_text):
    """Rutas que el sudoers canonico (del repo) exige y no estan en el instalado."""
    return sorted(parse_sudoers(required_text) - parse_sudoers(installed_text))


def classify_missing_sudoers(missing, exists_fn=None):
    """Parte las rutas faltantes segun si el binario existe en disco:
    'hard' (binario presente -> rompe el uso desatendido, es un fallo real) y
    'soft' (binario ausente -> feature todavia no instalada, solo aviso)."""
    if exists_fn is None:
        exists_fn = lambda p: os.path.isfile(p) or bool(shutil.which(os.path.basename(p)))
    hard, soft = [], []
    for path in missing:
        (hard if exists_fn(path) else soft).append(path)
    return hard, soft


def parse_phy_modes(text):
    """Modos de interfaz soportados por el phy, de `iw phy info`
    (seccion 'Supported interface modes')."""
    modes = set()
    in_section = False
    for raw in (text or "").splitlines():
        line = raw.strip()
        if 'supported interface modes' in line.lower():
            in_section = True
            continue
        if in_section:
            if line.startswith('*'):
                modes.add(line.lstrip('* ').strip())
            elif line:
                break  # otra seccion -> fin de la lista de modos
    return modes


def category_rollup(checks):
    """Resumen por categoria de alto nivel (lo que va antes de '/'):
    'cat: X ok[, Y warn][, Z fail]'. Para que el doctor muestre QUE reviso
    aunque todo pase, no solo 'Todo OK'."""
    order = []
    cats = {}
    for c in checks:
        top = c["category"].split("/", 1)[0]
        if top not in cats:
            cats[top] = {"ok": 0, "warn": 0, "fail": 0}
            order.append(top)
        cats[top][c["status"]] = cats[top].get(c["status"], 0) + 1
    lines = []
    for top in order:
        d = cats[top]
        parts = ["%d ok" % d["ok"]]
        if d["warn"]:
            parts.append("%d warn" % d["warn"])
        if d["fail"]:
            parts.append("%d fail" % d["fail"])
        lines.append("%s: %s" % (top, ", ".join(parts)))
    return lines


def build_report(checks):
    """Texto humano para la UI: resumen por area + los checks NO-ok al final."""
    rollup = category_rollup(checks)
    nonok = ["[%s] %s: %s" % (c["status"], c["name"], c["detail"])
             for c in checks if c["status"] != "ok"]
    if nonok:
        return "\n".join(rollup + ["", "Revisar:"] + nonok)
    return "\n".join(rollup)


def overall_ok(checks):
    return not any(c["status"] == "fail" for c in checks)


def summarize(checks):
    s = {"ok": 0, "warn": 0, "fail": 0}
    for c in checks:
        s[c["status"]] = s.get(c["status"], 0) + 1
    return s


# ──────────────────────── inspeccion del entorno ───────────────────────────

def _run(cmd, timeout=6):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except Exception:
        return None


def iface_present(iface):
    return os.path.isdir("/sys/class/net/" + iface)


def iface_driver(iface):
    try:
        return os.path.basename(os.readlink("/sys/class/net/%s/device/driver" % iface))
    except OSError:
        return None


def iface_phy_index(iface):
    try:
        with open("/sys/class/net/%s/phy80211/index" % iface) as f:
            return int(f.read().strip())
    except (OSError, ValueError):
        return None


def phy_info_text(phy_index):
    """`iw phy phyN info` (sin root; fallback con sudo)."""
    for cmd in (["/usr/sbin/iw", "phy", "phy%d" % phy_index, "info"],
                ["sudo", "/usr/sbin/iw", "phy", "phy%d" % phy_index, "info"]):
        r = _run(cmd)
        if r and r.returncode == 0 and r.stdout:
            return r.stdout
    return ""


def check_interfaces():
    checks = []

    # wlan0 (gestion / SSH)
    if iface_present("wlan0"):
        checks.append({"category": "iface", "name": "wlan0",
                       "status": "ok", "detail": "presente (gestion)"})
    else:
        checks.append({"category": "iface", "name": "wlan0",
                       "status": "warn", "detail": "ausente (sin red de gestion)"})

    # wlan1 (ataque, RTL8821CU)
    if not iface_present("wlan1"):
        checks.append({"category": "iface", "name": "wlan1",
                       "status": "fail", "detail": "ausente (adaptador de ataque)"})
        return checks

    drv = iface_driver("wlan1")
    if drv == EXPECTED_WLAN1_DRIVER:
        checks.append({"category": "iface", "name": "wlan1/driver",
                       "status": "ok", "detail": drv})
    else:
        checks.append({"category": "iface", "name": "wlan1/driver", "status": "warn",
                       "detail": "driver=%s (esperado %s)" % (drv, EXPECTED_WLAN1_DRIVER)})

    idx = iface_phy_index("wlan1")
    if idx is None:
        checks.append({"category": "iface", "name": "wlan1/phy",
                       "status": "warn", "detail": "no se pudo leer el indice del phy"})
    else:
        modes = parse_phy_modes(phy_info_text(idx))
        for mode, crit in (("AP", False), ("monitor", True)):
            if mode in modes:
                checks.append({"category": "iface", "name": "wlan1/%s" % mode,
                               "status": "ok", "detail": "phy%d soporta %s" % (idx, mode)})
            else:
                checks.append({"category": "iface", "name": "wlan1/%s" % mode,
                               "status": "fail" if crit else "warn",
                               "detail": "phy%d NO soporta %s" % (idx, mode)})
    return checks


def installed_sudoers_text():
    """Privilegios NOPASSWD efectivos de la cuenta via `sudo -n -l`. NO depende
    de poder LEER /etc/sudoers.d/piscan-wifi (es root:root 0440, ilegible para
    el usuario) ni pide contrasena (`-n`). Es la forma correcta de verificarlo."""
    r = _run(['sudo', '-n', '-l'])
    if r and r.returncode == 0 and r.stdout:
        return r.stdout
    return None


def check_sudoers(installed_text=None):
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    required_path = os.path.join(repo_root, "systemd", "sudoers-piscan-wifi")
    try:
        with open(required_path) as f:
            required_text = f.read()
    except OSError:
        return [{"category": "sudoers", "name": "canonico", "status": "warn",
                 "detail": "no se encontro %s en el repo" % required_path}]
    if installed_text is None:
        installed_text = installed_sudoers_text()
    if installed_text is None:
        return [{"category": "sudoers", "name": "privilegios", "status": "fail",
                 "detail": "no se pudieron leer los privilegios (sudo -n -l fallo)"}]
    missing = missing_sudoers(installed_text, required_text)
    if not missing:
        return [{"category": "sudoers", "name": "entradas", "status": "ok",
                 "detail": "todas las rutas NOPASSWD presentes"}]
    hard, soft = classify_missing_sudoers(missing)
    checks = []
    if hard:
        checks.append({"category": "sudoers", "name": "entradas", "status": "fail",
                       "detail": "faltan (binario instalado): " + ", ".join(hard)})
    if soft:
        checks.append({"category": "sudoers", "name": "entradas-opcionales",
                       "status": "warn",
                       "detail": "faltan, pero el binario tampoco esta instalado: "
                                 + ", ".join(soft)})
    return checks


def check_db():
    try:
        sys.path.insert(0, os.path.expanduser("~/piscanlvgl/app"))
        from db.models import get_session
        with get_session() as session:
            session.execute  # atributo existe -> sesion viva
        return [{"category": "db", "name": "sqlite", "status": "ok",
                 "detail": "get_session() OK"}]
    except Exception as exc:
        return [{"category": "db", "name": "sqlite", "status": "fail",
                 "detail": "no accesible: %s" % exc}]


def run_all():
    checks = []
    checks += check_binaries()
    checks += check_interfaces()
    checks += check_sudoers()
    checks += check_db()
    return {"ok": overall_ok(checks), "summary": summarize(checks),
            "checks": checks, "report": build_report(checks)}


def main():
    result = run_all()
    print(json.dumps(result))
    sys.exit(0 if result["ok"] else 1)


if __name__ == "__main__":
    try:
        _here = os.path.dirname(os.path.abspath(__file__))
        if _here not in sys.path:
            sys.path.insert(0, _here)
        from db.action_log import run_cli_with_log
        run_cli_with_log("tools", main)
    except Exception:
        main()

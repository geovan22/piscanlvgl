# TODO — PiScan

> Lista de trabajo viva para no perder el hilo entre sesiones. Versionada en el
> repo. Convención: `[x]` hecho, `[ ]` pendiente, `[~]` en progreso/a medias.
> El roadmap grande y el "por qué" de las decisiones viven en `CLAUDE.md`
> (§4 PENDIENTE, §8 Roadmap). Acá va el detalle operativo de lo pendiente.

---

## Estado actual

> **Fase 0 completa.** La Fase 1 está funcional; quedan pruebas de UI y
> comprobaciones puntuales en la Pi. **Fase 3 — Bluetooth: MVP escrito y
> validado en PC/Pi (compila, 41 tests OK). Pendiente de aplicar sudoers +
> instalar rfkill en la Pi y validar el scan en pantalla.**

## Logging centralizado (ActionLog) — [x] NUEVO, por validar en pantalla
> Regla general: **toda operación invocada desde la UI debe quedar registrada**
> en `action_log` para poder depurar sin reproducir. Captura automática en el
> dispatcher Python; funciones específicas (log_attack, log_honeypot,
> log_bluetooth_batch) siguen existiendo con su propia tabla.
- [x] Tabla `ActionLog` (section/tool/inputs/outputs/error/result/duration_ms).
- [x] Helper `app/db/action_log.py`:
      - `log_action(section, tool, inputs, outputs, error, result, duration_ms)`
      - `run_cli_with_log(section, main_fn)` — envoltorio del `if __name__`.
        Captura stdout + exit code **sin cambiar** el contrato con C.
        Fallo del log nunca tumba el script.
- [x] Instrumentados: `bt_ops.py`, `wifi_ops.py`, `net_ops.py`, `diag.py`.
      `db_tool.py` queda sin envoltorio global (sus llamadas son demasiado
      frecuentes: `config get` por cada carga), pero añade comandos de lectura.
- [x] CLI: `python3 app/db_tool.py action list [N] [section] [--errors]`,
      `action show <id>`, `action clear [--older-than-days N]`.
- [x] Bridge C `db_action_recent()` en `db_client` → texto multilínea listo
      para un `lv_label`.
- [x] Pantalla **Herramientas**: 2 botones nuevos **"Ver logs"** (cyan,
      últimas 20) y **"Solo errores"** (rojo, filtrado). Comparten el label
      con Self-check; hilo + poll (`ui_shell_poll_logs`).
- [x] 9 tests nuevos en `tests/test_action_log.py` (extract tool/error,
      log_action truncation + swallow, run_cli_with_log paths ok/error).
- [x] Validado end-to-end en la Pi: `bt_ops scan hci1` registra ok+duration;
      `bt_ops scan hci9` (iface invalida) registra error con el stderr crudo
      de btmgmt (`status 0x11 Invalid Index`); `--errors` filtra solo esas.
- [ ] Validar los botones en pantalla y que el filtrado se lee legible.
- [ ] Follow-up: instrumentar `db_tool.py` para los comandos NO frecuentes
      (config set / setmany, credential set, honeypot) con el mismo envoltorio.

## Fase 3 — Bluetooth (iteración 6): Expansión de herramientas — [~] EN CURSO
> Objetivo del proyecto: **superar a Bruce/Flipper** aprovechando que debajo hay
> Linux/BlueZ completo + **2 radios** (hci0 onboard BCM43438, hci1 USB RTL8821CU,
> seleccionables). Ellos son solo-BLE y enlatados (spam, scan, beacon, BadBLE).
> Nosotros sumamos BR/EDR clásico, GATT interactivo, sniffing pcap y MITM.
> **Regla:** lo configurable va a Config (tabla `CFG_ITEMS`), y **todo resultado
> se persiste a DB** (para estadísticas/análisis). Marco: SOLO lab/autorizado.
>
> Diagnóstico base (2026-10-29, commits 89bc9c4/ae98be9): SDP/GATT/L2ping no
> estaban rotas — apuntan a capas que el equipo moderno no expone sin emparejar;
> ahora reportan el motivo. BLE spam verificado irradiando; popup = límite del
> receptor (ver memoria del proyecto).

### Orden de prioridad
> **A y B HECHOS (2026-11-04, commits c975df3 opción A, 8d5d196 opción B).**
> Compilados en la Pi (exit 0).
>
> **Sesión 2026-11-06 (BT pausado por decisión del usuario):** se completaron
> #5 tracking, #7 OBEX, selector de spam en pantalla, fix del bug de selección
> de la lista BT (siempre abría el 1º), scroll en el overlay de detalle BT
> (ya no se solapa con los botones), auto-emparejamiento (`_ensure_paired`
> en gatt/obex) y `bt_pair_agent.py` + botón **Pair** (modo emparejable).
> **El Geo 22 quedó Paired+Bonded via el botón Pair**. Para el spam se
> reescribió a alta tasa via socket HCI (`ble_adv_fast.py`, ~40 adv/s
> verificados OTA) y se agotaron todas las palancas (tasa 2-40/s, hci0 y hci1,
> con/sin bluetoothd, persistencia 25/200/500 ms) sin lograr popup en el
> Geo 22. Conclusión: la emisión es correcta; falta el set exacto de
> model-IDs de Bruce (fetch bloqueado; usuario no tiene el archivo). Ver
> memoria `project-ble-spam-receiver-limit.md`.
>
> **Pendiente de hardware:** GATT read/write real contra un periférico BLE
> conectable en pairing (pulsera/sensor/bombilla/ESP32), y OBEX push real
> contra un target clásico en modo recepción OPP.

**Single-change (se construyen en esta/próxima iteración, autocontenidas):**
- [x] **1. GATT read/write/subscribe** (diferenciador Linux #1) — HECHO
      (commit 8d5d196). `parse_gatt_value` + `gatt_access(mac, mode, char,
      value_hex, notify_secs)` modos read/write/subscribe via bluetoothctl
      (mismo connect que `gatt_enum`). CLI `gattrw <mode> <mac> <char>
      [valor|secs] [iface]`. Bridge `bt_client_gatt_rw`. UI: 2ª fila en el
      overlay de detalle BT (G.read/G.write/G.notify) que pide el UUID por
      `text_input` (+ valor hex en write). Config: `bt_gatt_notify_secs`. DB:
      `bluetooth_attack_log` (action=`gatt_read`/`gatt_write`/`gatt_notify`,
      con char + valor/notificaciones). Tests +4 (parse_gatt_value). Validado:
      connect-path + contrato JSON + validación de args; **el read/write real
      queda pendiente de un periférico BLE conectable en pairing.**
- [x] **2. BLE beacon (iBeacon/Eddystone)** (paridad) — HECHO y verificado
      on-device (2026-10-29, commit 4e65589). `ble_beacon()` reusa el motor HCI
      adv (trama fija no-conectable ADV_NONCONN_IND). iBeacon (UUID PiScan +
      major/minor) y Eddystone-URL (https://piscan.lab) **irradian** (on_air,
      status 00, vistos desde hci0) y loguean a `bluetooth_attack_log`
      (action=`ble_beacon`). Bridge `bt_client_ble_beacon`; 4º botón "Beacon"
      (teal) en la fila BT (reusa hilo+poll global, kind="beacon"). Config
      (grupo Bluetooth): `bt_beacon_type` (CHOICE), `bt_beacon_major`,
      `bt_beacon_minor`, `bt_beacon_duration`. Tests +2 (iBeacon/Eddystone).
      Binario recompilado en la Pi (exit 0). **UI aparece tras `sudo systemctl
      restart piscan`** (el proceso corriendo aún es el binario viejo; Python/
      CLI ya vivos). `systemctl restart` NO está en NOPASSWD → pendiente decidir
      si se agrega a sudoers.
- [x] **3. L2ping flood / BlueSmack** (diferenciador, lab) — HECHO (commit
      c975df3). `l2ping` count hasta 200, size hasta 600B (BlueSmack), timeout
      escalado. Config: `bt_l2ping_count`, `bt_l2ping_size` (la UI los lee).
      DB: ya loguea sent/recv/loss. Test del cap. Validado on-device (corre y
      da el hint BR/EDR contra BLE).
- [x] **4. Sniffer pcap en UI + decode** (diferenciador) — HECHO (commit
      c975df3). Duración ya era configurable (`bt_capture_duration`). Añadido
      `_tshark_summary` (nombres EIR/AD + nro/opcodes ATT) al leer el .btsnoop;
      el bridge C muestra el decode. DB: ya loguea path/size/packets + decode
      en details. Validado on-device (decodificó "LAP-1MJLWG3").

**Multi-iteración (más grandes, su propia tanda):**
- [x] **5. Tracking/presencia** (diferenciador) — HECHO y verificado on-device
      (2026-11-04, commit 3234b79). `track(iface, rounds, window, mode)` = N
      barridos logueando avistamientos por ronda a **tabla nueva
      `bluetooth_sighting`** (session/round/mac/rssi/ts); resumen agregado
      (seen_rounds, rssi best/last). CLI `track`, bridge `bt_client_track`, 5º
      botón "Track" (verde) en la fila BT. Config: `bt_track_rounds`,
      `bt_track_window`. Verificado: 3 rondas → 6 únicos / 13 avistamientos.
      (Wardriving = esto + GPS, futuro.)
- [~] **6. Bad BLE / BadBT (HID injection)** — **PENDIENTE, iteración propia.**
      Estado al 2026-11-06: (a) `python3-dbus` + `python3-gi` **YA instalados**
      en la Pi (del trabajo del pairing agent); (b) `bt_pair_agent.py` ya
      demuestra el patrón daemon+GLib+Agent1 D-Bus con la Pi descubrible
      (verificado contra el Geo 22); (c) **el Geo 22 ya está emparejado con la
      Pi (Paired+Bonded)** — sirve de host para validar. Falta: servidor GATT
      **HOGP** (HID 0x1812 + report map + bonding + input reports) también
      como daemon dbus (igual patrón que el agente), un formato de script
      estilo Ducky y la UI (botón + carga de script). Config: `badble_layout`.
      DB: `BluetoothAttackLog` action=`badble`.
- [x] **7. OBEX push/pull (clásico)** (diferenciador) — HECHO (commit ebb8c24).
      `obex_push(mac, filepath)` con `obexctl` (BlueZ/obexd); clave:
      `_session_bus_env()` apunta `DBUS_SESSION_BUS_ADDRESS` a
      `/run/user/<uid>/bus` (el servicio system no lo hereda). CLI `obexpush`,
      bridge `bt_client_obex_push`, botón OBEX en el overlay de detalle (envía
      `data/obex_test.txt`; rutas arbitrarias por CLI). DB: action=`obex_push`.
      Verificado: obexctl alcanza a obexd; connect a no-OPP falla limpio. **Push
      real pendiente de un target clásico en modo recepción OPP.** (No hizo
      falta sudoers: obexctl corre como usuario.)
- [ ] **8. A2DP: conectar a parlante propio** (play/capture audio). BlueZ/Pulse.
- [ ] **9. BLE MITM / relay con los 2 radios** 🏆 (ningún Flipper/Bruce puede).
      Radio A conecta al periférico real, radio B anuncia un clon al teléfono;
      proxea/loguea/modifica GATT. DB: **nueva tabla** para el tráfico MITM.
      Lab. Esfuerzo alto; depende de #1 (GATT r/w).

### Mejoras transversales de BT (2026-10-29 → 2026-11-06)
- [x] **Spam de alta tasa** (commit `f0fdef2`+`2d8b2d2`) — reescritura: nuevo
      `app/bluetooth/ble_adv_fast.py` (socket HCI crudo, root via sudoers;
      rota MAC+payload cada ~25 ms). Verificado OTA: **~40 adv/s** (vs ~1.3/s
      del fork-de-hcitool viejo); 220 tramas Fast Pair `0xFE2C` y 173 Samsung
      `0x0075` bien formadas visibles desde hci0. `ble_spam()` delega en el
      helper; bridge muestra la tasa. Sudoers: + helper (root para socket HCI).
- [x] **Selector de modo de spam** en pantalla (commit `d13157b`) — al tocar
      "Spam" abre overlay (Samsung/Google/Apple/Microsoft/names/random). Antes
      quedaba enterrado en Config → se probaba `microsoft` contra un Android
      y no salía nada. Resuelto.
- [x] **Fix selección de la lista BT** (commit `d13157b`) — `bt_row_cb` leía
      `lv_event_get_user_data` (4° arg = `NULL`) → siempre abría el primer
      device. Ahora el índice va como user_data del evento. Afectaba SDP/GATT/
      GATT-rw/L2ping/OBEX por dispositivo.
- [x] **Scroll en el overlay de detalle BT** (commit `feba977`) — la info +
      resultados de acciones ahora viven en contenedor con scroll (442×182)
      que termina sobre las 2 filas de botones → ya no se solapa; auto
      scroll-to-top al llegar un resultado.
- [x] **Auto-emparejamiento antes de acciones** (commit `feba977`) — nuevo
      `pair_device(mac, iface)` y `_ensure_paired()` que se llama antes de
      `gatt_enum`, `gatt_access` (R/W/N) y `obex_push`. CLI `pair <mac>`.
      Mensaje de error guía (una MAC random del scan **no** es emparejable;
      el device debe estar en modo pairing).
- [x] **Pairing agent + botón Pair** (commits `0906af3`+`2ea48af`) —
      `app/bluetooth/bt_pair_agent.py` (python3-dbus + GLib): registra un
      `org.bluez.Agent1` KeyboardDisplay que auto-confirma (passkey y
      authorize), hace la Pi Powered+Pairable+Discoverable (alias "PiScan")
      por N segundos. Bridge `bt_client_pair_agent`, **botón "Pair" (azul)
      6° en la fila BT**. Config: `bt_pair_window`. **Validado end-to-end
      2026-11-06: el Geo 22 (`1C:F8:D0:34:A0:9A`) quedó `Paired: yes,
      Bonded: yes`** (encontrado por hci0 — onboard tiene mejor antena que
      hci1 para este caso).
- [x] **Sudoers: `systemctl restart/stop/start piscan` NOPASSWD** (commit
      `c9f0ba0`) — archivo separado `systemd/sudoers-piscan-restart`,
      validado con `visudo -c`. Ya me permite reiniciar piscan tras cada
      `build_app.sh` sin pedir contraseña.
- [~] **BLE spam popup en Geo 22** — **BLOQUEADO (ver memoria
      `project-ble-spam-receiver-limit.md`).** Sesión 2026-11-06 agotó todas
      las palancas (tasa 2-40/s, hci0/hci1, con/sin bluetoothd, persistencia
      25/200/500 ms); emisión verificada correcta OTA, pero el Geo 22 no
      dispara popup. Lo único que queda es el set exacto de **model-IDs** que
      Bruce usa (Google/Samsung dejaron de gatillar con muchos IDs); el fetch
      automático lo bloqueó el filtro 2 veces, y el usuario no tiene el
      archivo. **No volver a iterar sin el archivo de payloads de Bruce.**

> Al implementar cada una: binario nuevo en sudoers si usa root (ruta absoluta),
> Config si tiene parámetros, y `log_*` a DB siempre. Tests de parsers puros.

## Fase 3 — Bluetooth (MVP iteración 1) — [~] a validar en Pi
> Marco: laboratorio controlado / pruebas autorizadas. Controller elegido:
> hci1 (USB RTL8821CU, mismo chip que wlan1). hci0 (onboard BCM43438, BT 5.0)
> queda de respaldo. Ambos arrancan rfkill soft-blocked; se desbloquean con
> `rfkill unblock bluetooth` + `btmgmt power on`.
- [x] Modelo `BluetoothLog` en `app/db/models.py` (tabla nueva, create_all).
- [x] `app/bluetooth/bt_ops.py` — power on/off, scan dual (BR/EDR + LE) con
      `btmgmt find` + enriquecido con `bluetoothctl info`. Parsers puros
      (btmgmt / bluetoothctl) y merge probados con 16 tests unitarios.
- [x] Vendor por OUI (`data/oui.txt`) y etiqueta `(aleatoria)` para MAC
      locally-administered (BLE privacy).
- [x] Bridge C `app/bluetooth/bt_client.c/h` (`bt_client_scan`, `bt_client_power`).
- [x] UI: sección **Bluetooth** nueva en el menú (entre WiFi y LAN), botón
      "Escanear (10s)", lista con nombre/MAC/vendor/RSSI/tipo (LE/BR/DUAL),
      scroll ▲▼. Hilo+poll (`ui_shell_poll_bluetooth`).
- [x] diag.py: nuevos checks `bin/bluetooth` (bluetoothctl/btmgmt/hcitool/rfkill).
- [x] sudoers versionado: + rfkill, btmgmt, hcitool, sdptool, gatttool.
- [x] Compila en la Pi (HEAD con los nuevos .c/.h), tests 41/41 OK.
- [x] **Aplicado en la Pi (2026-10-27):** `sudo apt install -y rfkill` + sudoers
      copiado y reiniciado el servicio. `btmgmt power on` responde OK con `sudo -n`.
- [x] **Backend validado end-to-end**: `bt_ops scan hci1 6` listó 5 devices
      con RSSI y nombres (`LAP-1MJLWG3` entre ellos); `BluetoothLog` persiste
      (count 11 tras varias corridas); registro en `action_log` también OK.
- [x] **Toggle hci0/hci1 en Config** (CFG_CHOICE estática `bt_iface`): la UI
      lee el valor con `db_config_get("bt_iface")` antes de llamar al scan.
      Default `hci1` por compatibilidad. `cfg_item_t` ahora soporta choices
      estáticas (`BT_IFACE_CHOICES`) o dinámicas (portal). Compila en la Pi.
- [ ] Validar en pantalla: tocar Escanear en la sección Bluetooth, cambiar
      `bt_iface` a hci0 en Config y confirmar que el próximo scan usa el onboard.

### Iteración 4 (2026-10-29) — [x] Acciones globales del controller BT
> Marco: SOLO lab / dispositivos propios o con autorización explícita.
- [x] **BLE spam / fake advertising** (`bt_ops.ble_spam`): rota el nombre
      advertising del controller cada ~1.5s con nombres de `data/ble_names.txt`
      (60 nombres plausibles: AirPods Pro, Mi Band, JBL Flip, AirTag, etc.) o
      aleatorios cortos (`mode="random"`). bluez admite UN solo advertisement
      por controller a la vez → rotamos (off+set-name+on) en loop. Duración
      configurable (`bt_spam_duration` en Config, default 20s).
- [x] **btmon capture** (`bt_ops.btmon_capture`): sniffer HCI a archivo
      `data/captures/btmon_<iface>_<ts>.btsnoop` (formato btsnoop; abrible
      con Wireshark y `btmon --read`). Duración configurable
      (`bt_capture_duration` en Config, default 30s).
- [x] CLI: `bt_ops.py spam [iface] [seconds] [names|random]`,
      `bt_ops.py capture [iface] [seconds]`.
- [x] Bridge C `bt_client_ble_spam` + `bt_client_capture`.
- [x] UI: dos botones nuevos en la fila principal de Bluetooth:
      **"BLE spam"** (naranja) y **"Capturar"** (púrpura). Hilo+poll
      (`ui_shell_poll_bt_global`). Mientras corre, deshabilita todos los
      botones de la fila. Al terminar, muestra resumen en el status label.
- [x] Config: 2 keys nuevas `bt_spam_duration` (5..180s), `bt_capture_duration`
      (5..300s). CFG_INT, paso 5s.
- [x] sudoers (ya aplicado por el usuario): `l2ping` + `btmon` con NOPASSWD.
- [x] 3 tests nuevos (`_load_ble_names`, `btmon_capture` con mock creando
      archivo chico, caso de error zero-size). Suite 77/77 OK.
- [x] Validado end-to-end en la Pi:
      - `capture hci0 3s` → `btmon_hci0_<ts>.btsnoop` 386B, `bluetooth_attack_log`
        action="capture" result="ok".
      - `spam hci0 4s names` → cycles=3 uniq_names=3 last="Ring Doorbell",
        `bluetooth_attack_log` action="ble_spam" result="ok".
- [ ] Validar en pantalla: tocar "BLE spam" (debe aparecer un device con
      nombre rotando cerca) y "Capturar" (archivo .btsnoop en data/captures/).

### Iteración 3 (2026-10-29) — [x] Acciones de pentesting por device
> Marco: SOLO lab / dispositivos propios o con autorización explícita.
- [x] Tabla `BluetoothAttackLog` (action/target_mac/target_name/result/details).
- [x] Backend `bt_ops.py`:
      - `sdp_browse(mac)` — BR/EDR services via `sdptool browse` (sin pairing).
        Útil para identificar perfil (A2DP/HFP/HID/OBEX...).
      - `gatt_enum(mac, iface)` — LE: `bluetoothctl` interactivo con `connect`
        + `menu gatt` + `list-attributes`. Parser `parse_bluetoothctl_gatt`
        extrae services + characteristics + descriptors (uuid 128-bit canonical
        entre paréntesis + label humano antes).
      - `l2ping(mac, count, size)` — L2CAP echo (BR/EDR, test alcance + stress
        leve). Parser `parse_l2ping_output` da sent/recv/loss/avg/min/max.
      - CLI: `bt_ops.py sdp <mac>`, `gatt [iface] <mac>`, `l2ping <mac> [count] [size]`.
- [x] Bridge C `bt_client_{sdp,gatt,l2ping}` → texto multilínea para `lv_label`.
- [x] UI: overlay BT detalle con 3 botones de acción (SDP ámbar, GATT cyan,
      L2ping rojo) + Cerrar. Al tocar, hilo+poll (`ui_shell_poll_bt_action`);
      el resultado reemplaza el body del overlay sin cerrar.
- [x] sudoers versionado: + `l2ping`, `btmon`.
- [x] diag.py: nuevos checks `bin/bluetooth/{sdptool,gatttool,l2ping,btmon}`.
- [x] 11 tests nuevos (parsers SDP, L2ping, GATT, UUID canonical). Suite 74/74.
- [x] Validado end-to-end: SDP contra target → JSON válido con services=[] +
      warn="Host is down" (muchos LE-only no exponen SDP). Fila en
      `bluetooth_attack_log` registra la acción. l2ping sin sudoers aplicado
      falla con "sudo: a password is required" y queda loggeado.
- [ ] **Aplicar en la Pi** (requiere sudo con password):
      `sudo cp ~/piscanlvgl/systemd/sudoers-piscan-wifi /etc/sudoers.d/piscan-wifi && sudo chmod 440 /etc/sudoers.d/piscan-wifi`
- [ ] Validar en pantalla: tocar un device en Bluetooth → overlay → tocar
      SDP/GATT/L2ping → el resultado aparece dentro del overlay.

### Config lento — [~] optimización + telemetría
- [x] **Instrumentar `db_tool.py`** con `run_cli_with_log` SELECTIVO
      (`config list|set|setmany`, `credential set`, `action clear`). Los
      `config get` frecuentes NO se logean (serían spam en action_log).
      Permite medir cuánto tarda abrir Config (= `config list`) y guardar
      al salir (= `config setmany`).
- [x] **Ocultar `g_cfg_box` durante el fill de rows** (`LV_OBJ_FLAG_HIDDEN`
      antes de la carga, `clear_flag` al final). Evita que el SPI KeDei
      repinte parcialmente por cada fila añadida al flex-column — un solo
      flush al final.
- [ ] Medir en la Pi: tras reiniciar, abrir Config y ver `action list db`
      para los `duration_ms` reales. Si `config list` tarda >400ms es un
      candidato a cachear en memoria.

### Iteración 2 (2026-10-29) — [x] CERRADA excepto GATT
- [x] **RSSI por device**: implementado con reader thread sobre Popen de
      bluetoothctl interactivo. Flow: `select <BD>` → `scan on` → sleep
      (seconds-2) → snapshot de MACs de `[NEW]` → `info <mac>` por cada una
      (mientras scan activo, ahí sí aparece `RSSI: 0xff.. (-N)`) → sleep
      (max 1s, 0.12s/mac) → `scan off` → `devices` → `quit`.
- [x] **addr_type + UUIDs resueltos + Appearance + Class + Icon**: todos vienen
      del mismo bloque `info <mac>`. `_split_info_blocks` separa el stream en
      bloques por MAC; `parse_bluetoothctl_info` enriquece cada device.
- [x] **Fix del placeholder MAC-con-guiones**: bluez pone `AA-BB-CC-DD-EE-FF`
      como `Alias` cuando no hay nombre real; `_is_mac_placeholder` lo descarta
      para no mostrar "nombres" que son la MAC repetida.
- [x] **Fix del header `Device MAC (public)` interpretado como entrada del
      listado `devices`**: el handler de `_RX_BCTL_LIST` descarta nombres que
      empiezan y terminan con paréntesis.
- [x] **UI: filas clickables + overlay de detalle** (name, MAC+addr_type, tipo,
      vendor, RSSI, Icon, Class, Appearance, Servicios). `close_bt_detail()`
      se llama en `enter_section` y `show_carousel` para no dejar fugas.
- [x] **bt_client (bridge C)**: `bt_device_t` suma `uuids[480]` serializado
      como CSV de etiquetas humanas (preferido) o UUID corto si no hay label.
- [x] Validado end-to-end: scan hci0 10s devolvió 9 devices con `rssi=7/9`,
      `addr_type=8/9`, `uuids=6/9`. Vendors por OUI resuelven correctamente
      (Hewlett Packard, Samsung, Tuya Smart Inc., etc.).
- [x] 66/66 tests pasan (10 nuevos: split blocks, parse rssi hex, placeholder
      alias, info-header-no-es-listado).
- [ ] Validar en pantalla: tocar una fila BT y comprobar que abre el overlay
      con todos los campos legibles.
- [ ] Pendiente iteración 3: **GATT enumeration** para dispositivos LE
      (`bluetoothctl connect` + listado de services/characteristics).
- [ ] Pendiente: filtros por tipo/nombre; reportes (Fase 8); `bleak` opcional.

### Lecciones del día (2026-10-27, iteración 1.5)
- **btmgmt 5.82 se cuelga con stdin=/dev/null**: `btmgmt info` o `btmgmt find`
  con `stdin=DEVNULL` (igual que bajo systemd) jamás retornan. Con TTY/pipe
  responden en 0.08s. `_run()` ahora pasa `stdin=DEVNULL` explícito para
  reproducir el entorno del servicio y detectar estos cuelgues con timeout.
  Fix: `power_on/off` y `ctl_info` usan **hciconfig** (idempotente, no sufre
  el bug); `scan` usa **bluetoothctl** interactivo con `select <bd>`.
- Al parser que uso ahora, el toggle `bt_iface` (Config) sí llega al scan: el
  wrapper hace `select <BD>` dentro del mismo bluetoothctl antes de `scan on`,
  así que hci0 vs hci1 se honra end-to-end.
- Comprobado en vivo: hci0 (BT 5.0 onboard) ve ~11 dispositivos en 6s;
  hci1 (USB RTL8821CU, BT 4.2) ve ~2. Confirma lo esperado.

## Mejora de precisión WiFi — [~] WPA3 real pendiente
- [x] El parser conserva APs con SSID repetido, diferenciándolos por BSSID, y
      conserva la señal más fuerte observada para el mismo BSSID.
- [x] Clasifica OPEN, WEP, WPA, WPA2, WPA3 y transición WPA2/3 a partir de
      `iw scan`; la lista muestra el final del BSSID para identificar cada AP.
- [x] La detección de handshake exige un conteo positivo; `0 handshake` no
      marca una captura como válida.
- [x] Ocho pruebas unitarias focalizadas pasan localmente y también se
      ejecutaron satisfactoriamente en la Pi, incluidos los casos de escritura
      y error de DB.
- [x] En la Pi se confirmó que aparecen los sufijos BSSID en pantalla y que
      `wifi_scan_log` persiste el BSSID completo.
- [ ] Contrastar la clasificación con WPA3/transición usando una salida real de
      `iw scan` si hay un AP de laboratorio disponible.

## Conectar Red — errores de escaneo explícitos — ✅ VALIDADO
- [x] `net_ops.py` distingue un escaneo sin redes de errores de `nmcli` al
      actualizar o listar redes, y responde el error como JSON.
- [x] El puente C conserva el detalle del error y la UI lo muestra en el footer
      en vez de indicar "No se detectaron redes".
- [x] Tres pruebas unitarias cubren lista vacía válida y fallos de actualización
      y listado.
- [x] En la Pi: suite actualizada ejecutada y compilación completada; Conectar
      Red escanea, conecta a la red seleccionada y muestra la IP en la UI.
- [x] `rescan` endurecido (2026-10-21): un rescan rechazado por `<30s` ya no
      aborta el escaneo; solo se reporta error si la lista además sale vacía
      (adaptador caído/ocupado). Independiente del idioma del nmcli. 12 pruebas
      unitarias pasan (se añadió el caso benigno). Solo `.py` → toma efecto con
      pull+restart, sin `build_app.sh`.
- [ ] Sanity en la Pi: tocar "Escanear" dos veces seguidas (<30s) y confirmar
      que sigue mostrando redes (ya no "Error al escanear").
- No se forzó un fallo de `wlan0` para proteger la conexión de gestión; las
  respuestas de error y el caso de lista vacía están cubiertos por pruebas.

## Self-check / doctor (`app/diag.py`) — ✅ VALIDADO (pantalla + Pi)
- [x] Verifica binarios (core/handshake/deauth/pmkid/beacon/honeypot/wps),
      privilegios sudoers vía `sudo -n -l` (NO lee el archivo root:root 0440;
      distingue "falta y el binario está" = fail de "falta pero no instalado" =
      warn), interfaces wlan0/wlan1 (driver `rtw88_8821cu`, phy soporta
      AP+monitor) y el acceso a la DB. Salida JSON; `ok` = sin checks en `fail`.
- [x] 7 pruebas unitarias de la lógica pura pasan (local y en la Pi).
- [x] Corrido en la Pi (HEAD 58f14ce): `ok: true`, 21 ok / 4 warn / 0 fail.
      Los 4 warn son WPS (wash/reaver/bully + su sudoers), esperado hasta
      instalarlos. Descubrió y se corrigió un falso negativo del sudoers.
- [x] Wire UI: sección **Herramientas** con botón "Self-check (doctor)" (hilo+
      poll, `diag_client_run`). Muestra **resumen por área** (bin/iface/sudoers/
      db) + los checks no-ok (`report` en diag.py; +test de `category_rollup`).
- [x] Confirmado en pantalla y con reaver/sudoers aplicados: `ok=True`,
      **25 ok / 0 warn / 0 fail** con el desglose por área.

## WiFi ampliado — WPS (reaver/bully) — ✅ VALIDADO en hardware, lab-only
> Marco: SOLO redes propias o con permiso explícito.
- [x] `wifi_ops.py wps_scan [iface] [seg]` — `wash` en monitor (pasivo) → lista
      APs con WPS (bssid/canal/locked/essid).
- [x] `wifi_ops.py wps_attack <bssid> <channel> [iface] [seg] [pixie0|pixie1]`
      — `reaver` Pixie-Dust por defecto → parsea PIN/PSK; loguea en WifiAttackLog.
- [x] 5 pruebas unitarias de los parsers (wash/reaver) pasan localmente.
- [x] sudoers versionado: + wash/reaver/bully (`/usr/bin` y `/usr/sbin`).
- [x] En la Pi: instalados reaver 1.6.6 + bully 1.4 + pixiewps; sudoers aplicado.
- [x] Wire UI: acción **"WPS (Pixie)"** en el menú Acciones por AP (hilo+poll,
      `wifi_client_wps_attack`, confirm "solo lab", ~90s).
- [x] Validado en pantalla y por SSH (`wash` vio el AP; reaver asoció, M1/M2):
      el AP de prueba **no es vulnerable** a Pixie-Dust (NACK 0x03 + deauth),
      resultado correcto "sin PIN/PSK". La cadena completa funciona.
- [ ] Pulido menor (opcional): `wps_scan` incluye la columna Vendor de `wash`
      1.6.x en el `essid` (sale "Unknown <SSID>"); cosmético, no afecta el ataque.

## Fase 0 — WiFi / Config — ✅ CERRADA

### 1. text_input.c (teclado/entrada reutilizable) — ✅ CERRADO (validado en pantalla)
- [x] Matar el parpadeo del cursor (reenvío de `FOCUSED` tras `anim_duration=0`).
      *(ya venía del commit anterior)*
- [x] Botón **ver/ocultar contraseña** (ojo): aparece solo en modo password,
      alterna `lv_textarea_set_password_mode` y cambia etiqueta `ver`↔`ocu`.
      Como la pantalla de conectar WiFi usa este mismo componente
      (`text_input_show(..., is_password=1, ...)` en `ui_shell.c`), el botón ya
      aplica ahí — es el único camino de contraseña.
- [x] `password_show_time = 0`: evita el redibujado tardío (~1.5 s) que
      reemplazaba la letra en claro por el bullet y se veía como que el texto
      "se movía"/parpadeaba y pesaba por letra sobre el SPI del KeDei.
- [x] Texto que **subía y bajaba** dentro del campo: la fuente por defecto
      (montserrat_14) no cabía en los 30px → el campo scrolleaba en Y. Fijado
      a **montserrat_10 + pad_ver 2** → el texto entra holgado, `scroll_max_y=0`
      y el bobbing vertical desaparece. (Confirmado por el usuario que el ojo y
      las teclas andan; validar este fix en pantalla.)

### 2. Sección Config (editar config en DB) — ✅ FUNCIONA (UI validada)
- [x] Contrato con DB ya existía: `db_tool.py config get/set/list` +
      `db_client.c` (`db_config_get/set`). Incluido `db_client.h` en ui_shell.c.
- [x] UI de la sección `config` (rama en `enter_section`): título, valor actual,
      descripción, botones **-5s / +5s**.
- [x] Primer parámetro editable: **`deauth_duration`** (0–600 s, paso 5,
      key=`deauth_duration`, cat=`wifi`). 0 = ráfaga; >0 = sostenido.
      Carga al entrar, guarda en DB en cada ajuste, feedback en el footer.
- [x] **Validado en pantalla**: carga el valor, -/+ ajustan y persisten.
- [x] **Validado en red real (2026-10-13)**: el tiempo configurado se respeta
      en el deauth sostenido. (Requirió rebuild del binario C — ver lección abajo.)
- Nota: antes guardaba en cada tap (fork+exec python por tap = lento). Resuelto
  (2026-10-19): la carga trae toda la config en **1 fork** (`db_config_list` ->
  `config list`) y los cambios se persisten **en batch al salir** de Config en 1
  fork (`db_config_set_many` -> `config setmany`). Editar ya no forkea por tap.

### 3. Deauth sostenido en la UI (Patrón A) — ✅ CERRADO (validado en red real)
- [x] Cableado C→Python del `duration`: `wifi_client_deauth` ahora toma
      `int duration` y lo pasa como 6º arg a `wifi_ops.py deauth` (el Python ya
      soportaba sostenido; solo el wrapper C no lo pasaba — mandaba ráfaga fija).
- [x] El botón **Deauth** lee `deauth_duration` de la DB (fresco en cada ataque),
      lo muestra en el diálogo de confirmación ("sostenido Ns" vs "ráfaga") y lo
      usa en el ataque. Mensajes de estado/éxito reflejan el modo.
- [x] Flujo Patrón A (ya existía): escanear → tocar red → Deauth → confirmar.
- [x] **Validado en red real (2026-10-13)**: con N=20 en Config, el deauth
      (red y por cliente) mantiene la red caída ~20s. Antes duraba ~5s porque
      el binario C era viejo (ráfaga); tras `build_app.sh` respeta el tiempo.

---

## Fase 1 — WiFi avanzado (CLAUDE.md §8)

> Backend funcional. Las validaciones pendientes son de UI/telemetría; no
> bloquean continuar con otros pendientes. Pruebas únicamente con dispositivos
> propios o con autorización.

### 1. Escaneo de clientes conectados por AP — ✅ VALIDADO (lista MACs en red real)
- [x] Python `wifi_ops.py clients <bssid> <channel> [iface] [seg]`: `scan_clients`
      corre airodump filtrado por bssid unos seg (SOLO escucha, sin inyección),
      parsea la sección Station del CSV y devuelve los MAC asociados (+power,
      +packets). Limpia los temporales.
- [x] Wrapper C `wifi_client_scan_clients` (+ struct `wifi_client_sta_t`).
- [x] UI: botón **Clientes** en la sección WiFi (celeste). Con un AP seleccionado,
      escanea en hilo (poll `ui_shell_poll_clients`) y lista los clientes en el
      box de resultados (`g_list_mode=3`).
- [x] **Validado en red real**: lista el MAC de los dispositivos conectados.
- [x] Info extra: **fabricante por OUI** (archivo del sistema si existe +
      fallback chico, incluye Raspberry Pi), **orden por actividad** (más
      paquetes primero = mejor objetivo de handshake), columnas MAC/fabricante/
      dBm/pkts.
- [x] MAC **aleatorias/privadas** (bit 0x02 del 1er octeto = localmente
      administrada, ej. 2a/6x/Ax/Ex; iOS/Android por privacidad) se etiquetan
      como `(aleatoria)` en vez de `?` — ninguna base OUI las resuelve.
- [x] **Base OUI completa en el repo** (`data/oui.txt`, ~40k fabricantes IEEE,
      1.3MB, formato `AA:BB:CC\tFabricante`). `_oui_file()` la prioriza sobre
      las del sistema. Llega con `git pull`, funciona offline. Para
      actualizarla: bajar oui.txt de IEEE y recompactar (ver commit).

### 2. Deauth selectivo a un cliente — [~] código listo, a validar en red
- [x] Python: `deauth()` acepta `client_mac` → `aireplay-ng -c <mac>` (selectivo)
      vs broadcast. Dispatcher toma el 7º arg y lo loguea en WifiAttackLog
      (`target_client_mac`, para reportes Fase 8).
- [x] Wrapper C: `wifi_client_deauth` toma `const char *client_mac` (NULL/""=
      broadcast) y lo agrega al argv solo si viene.
- [x] UI: **tocar una fila de cliente** → confirm "Deauth al cliente <MAC>?" →
      deauth SELECTIVO usando `deauth_duration` de la DB (mismo hilo/confirm que
      el deauth de red). El botón Deauth sigue siendo a toda la red (dirigido).
- [x] **Validado en red real (2026-10-13)**: el deauth por cliente funciona y
      respeta el tiempo configurado (tras el fix de canal 0 en 5GHz + rebuild).

### Resto Fase 1 (ver CLAUDE.md §8)
- [~] **Captura PMKID/EAPOL (hcxdumptool 6.3.5)** — backend VALIDADO directo,
      UI cableada, a probar en pantalla.
      - Backend `wifi_ops.pmkid_capture`: hcxdumptool `-F` ~30s (pone monitor él
        mismo, por eso NO usamos enable_monitor) → `.pcapng` → `hcxpcapngtool`
        → `.hc22000`; filtra por BSSID (pmkid_target/eapol_target). Validado en
        red: de CLARO_fCexjw sacó **EAPOL clientless** (no PMKID — el router no
        lo suelta, es normal). hashes=3.
      - UI: botón **PMKID** (morado) en WiFi; `wifi_client_pmkid` + hilo + poll.
      - sudoers: +`/usr/bin/hcxdumptool`. Deps: `hcxdumptool hcxtools` (ya en Pi).
      - [ ] Validar en pantalla contra la red de lab (vía menú Acciones).
      - [x] **PMKID auditable on-device**: aircrack NO lee `.pcapng`, así que
        `pmkid_capture` ahora convierte también a `.cap` clásico (tcpdump). Ese
        `.cap` entra en la lista de "Auditar" (list_captures + _bssid_from_filename
        reconocen el prefijo `pmkid_`) y aircrack lo crackea con wordlist.
        Verificado: tras convertir, aircrack lista CLARO_fCexjw/J74r3f con
        handshake. El `.hc22000` queda para hashcat en PC.
- [x] **UI: menú "Acciones" (overlay)** — la sección WiFi no escalaba con un
      botón por acción (y PMKID chocaba con el botón Menú). Ahora la fila es solo
      **Escanear + Acciones**; "Acciones" (con red seleccionada) abre un overlay
      modal con Clientes / Deauth red / Handshake / PMKID-EAPOL / Auditar /
      Cancelar. Cada uno cierra el overlay y lanza su `trigger_*()`. Reusa el
      patrón del `confirm_dialog`. Escala para Export y lo que venga.
      - [ ] Validar en pantalla (ubicación, que no pise el botón Menú, que cada
        acción abra su confirm/flujo).
- [x] **Export `.cap` / `.hc22000`** — cubierto. Cada captura deja en
      `data/captures/` el `.hc22000` (listo para `hashcat -m 22000`) y el `.cap`.
      Se sacan por scp (hay SSH en .195). + Vista **Capturas** en pantalla:
      botón propio en la fila WiFi (Escanear · Acciones · Capturas), lista
      SSID/BSSID/tipo (HS/PMKID)/tamaño; tocar = auditar on-device. "Auditar"
      salió del menú Acciones (su lugar es Capturas). A validar en pantalla.
- [x] **Beacon flood (mdk4)** — backend VALIDADO directo (mdk4 inyecta beacons
      de SSID/MAC aleatorios, ~80pps) + UI cableada. `beacon_flood()` →
      reset→monitor→`mdk4 <if> b -c <ch> -s <pps>` bajo timeout→managed.
      En el menú **Acciones** ("Beacon flood", naranja); usa el canal del AP
      seleccionado, ~20s fijos (BEACON_SECONDS). mdk4 en /usr/sbin (+sudoers).
      - [ ] Validar el botón en pantalla contra el lab.
- [x] **Evil Twin / Honeypot AP (hostapd+dnsmasq+portal)** — FUNCIONAL y probado
      end-to-end en red real (2026-10-15, modo puente). El 2026-10-21 también
      se comprobó que crea `evil_twin.log` y el PCAP, y que persiste telemetría
      en SQLite. Los cinco registros recientes pasaron comprobaciones de
      formato/rangos y su referencia PCAP existe.
      `evil_twin()`: hostapd AP abierto con el nombre objetivo (2.4, ch fiable)
      → ip 10.0.0.1/24 + dnsmasq → portal cautivo (http.server threading :8080)
      → teardown limpio. Botón **Honeypot** directo en la fila WiFi. Log en
      data/captures/evil_twin.log. sudoers: + hostapd/dnsmasq/nft/iptables/
      sysctl/pkill. Detalle completo del flujo en **CLAUDE.md §3**.
      **Nota de privacidad:** los campos `portal_user`/`portal_pass` se guardan
      actualmente en texto plano; usar solo valores ficticios durante pruebas.
  - [x] **Puente + portal cautivo "walled garden"** (`honeypot_bridge=1`):
        DNS real + nft `piscan_nat` (redirect por cliente, masquerade solo a
        `@authorized`, forward drop a no autorizados). **Sin login = sin internet
        ni LAN**; tras el login `_et_authorize(ip)` abre internet. `do_GET` manda
        **302** a los sondeos del SO → el popup del portal salta solo. Volcado de
        `nft list table piscan_nat` al log para diagnóstico.
  - [x] Validado: conecta → portal automático → sin internet hasta autorizar
        user/pass → se guardan en `honeypot_log` → se abre internet. (Samsung
        Android; con Private DNS desactivado el popup salta solo.)
  - [x] Persistencia comprobada (2026-10-21): existe `evil_twin.log`, el log
        registra dnsmasq/portal/cierre, hay un PCAP de ~4.6 MB y su ruta existe
        en SQLite. Cinco filas recientes pasaron validaciones de formato MAC/IP,
        contadores, RSSI y referencia al PCAP. Los campos extendidos aparecen
        cuando la sesión/dispositivo produce esos datos.
  - [ ] **Baja prioridad — rendimiento del internet compartido:** con puente
        activo, el cliente puede hacer ping, pero la navegación/carga web es muy
        lenta o no termina. No bloquea el uso actual del honeypot. Pendiente
        comparar DNS, HTTPS desde la Pi por `wlan0` y acceso desde el cliente;
        ejecutar el diagnóstico documentado en la conversación cuando haya
        tiempo. No modificar reglas de red antes de recoger esa evidencia.
      Caveats honestos (no bugs): popup automático depende del teléfono (DNS
      cifrado/DoH lo evita → entra por navegador); HTTPS no se intercepta (sin
      TLS MITM); device con la red guardada como WPA puede no auto-unirse al
      gemelo ABIERTO. Limitaciones de todo evil twin.
      - [x] Validado en pantalla: botón Honeypot y toggle del puente en Config
        funcionan.
  - [~] **Telemetría extendida para auditoría (2026-10-15)** — código y
        persistencia comprobados parcialmente en la Pi. Se guarda por dispositivo
        en `honeypot_log` (ver
        CLAUDE.md §7) y se lee con `db_tool.py honeypot list/show`:
    - [x] #1 RSSI + bytes_rx/tx + dwell_s (de `iw station dump` pre-teardown).
    - [x] #2 timeline/permanencia (dwell_s; connect/disconnect en el log hostapd).
    - [x] #3 DHCP fingerprint (dnsmasq `log-dhcp` → vendor class + requested opts).
    - [x] #4 SNI de TLS (sitios HTTPS) — pcap + tshark, pasivo.
    - [x] #6 mDNS name (nombre `.local`) — pcap + tshark.
    - [x] #7 PCAP de sesión (`tcpdump`, Config `honeypot_pcap`, default ON).
    - [x] Código #5 PNL/probe (Config `honeypot_probe_sniff`, default OFF,
          experimental: monitor+AP en rtw88 conviven mal; best-effort).
    - [x] sudoers: + tcpdump/tshark. Migración SQLite (ALTER ADD COLUMN) en
          `models.py` para las columnas nuevas.
    - [x] Validado en la Pi (2026-10-21): PCAP de sesión creado y enlazado desde
          DB; RSSI/bytes/dwell, DHCP, SNI y mDNS aparecen en al menos un registro
          reciente. Los campos son dependientes de actividad y no se esperan en
          todas las filas.
    - [ ] `probe_ssids`: sigue sin validar; opción experimental desactivada por
          defecto y no apareció en las filas revisadas. No habilitar salvo que
          se quiera probar específicamente en laboratorio.
  - [x] **Rediseño pantalla Config + selector de portal** (2026-10-19, validado
        en pantalla). Config pasó de offsets
        fijos (llena/al límite) a **lista scrollable data-driven**: tabla
        `CFG_ITEMS` en `ui_shell.c` + caja flex-column (`g_cfg_box`) con botones
        ▲▼ (touch-scroll del KeDei no fiable). Agregar un ajuste = 1 línea en
        `CFG_ITEMS`. Tres tipos de fila: INT (−/+), BOOL (ON/OFF), CHOICE (◀/▶).
        Items actuales: deauth_duration, beacon_duration, honeypot_portal
        (CHOICE, lista `data/portals/*.html` por `dirent`; "default"=idx0=""),
        honeypot_bridge, honeypot_pcap, honeypot_probe_sniff. Un único callback
        `cfg_ctl_cb` (índice+dir empacados en user_data). Guarda en DB al tocar.
        - [x] Las 6 filas renderizan; el scroll ▲▼, los controles, guardado y
          selector de portal fueron probados por el usuario.
        - [x] El toggle del puente en Config fue probado por el usuario.
  - [ ] **Portal "juego/test"** (idea futura): si el cliente pasa un mini-reto
        se lo autoriza en vez de pedir user/pass. El POST del portal pasaría a
        validar el reto y llamar `_et_authorize(ip)`. Encaja en el walled garden.
  - [x] **Portales con más diseño**: `data/portals/wifi_gratis.html` y
        `operador.html` (login, CSS inline — sin recursos externos porque el
        cliente aún no tiene internet). Elegibles por `honeypot_portal`.

---

## Splash de inicio — [~] rediseño CYBER (terminal), a validar en pantalla
- Descartado el pingüino/frambuesa (muy "infantil", pedido del usuario). Nueva
  dirección: **ciberseguridad / terminal hacker**.
- [x] Imagen regenerada (`assets/splash/current.bin`, RGB565 480×320) con Node:
      **lluvia matrix** de 0/1 (columnas verdes, cabeza brillante, cola que se
      apaga), marco verde, y **banda central oscurecida** para que el texto se
      lea. Generador: scratchpad `make_splash2.js`.
- [x] Texto en **labels LVGL** (nítido) en `splash.c`: `root@piscan:~#` +
      "PiScan 22" (verde) + "> ethical hacking toolkit" (cyan) + estado abajo.
- [ ] Validar en pantalla. Si se quiere "PiScan 22" **más grande/bold**:
      habilitar `montserrat_28` (rebuild liblvgl) o hornear letras de bloque.

## Scanner WiFi — ✅ CERRADO (límite de antena del dongle, no bug)
> **Decisión del usuario: dejar el `iw scan` rápido como está.** Diagnóstico
> completo abajo. El scanner funciona bien; el "pocas redes" es sensibilidad RX.
- Evidencia final: unión de todos los métodos probados (4 iw scans + airodump
  pasivo 2.4 + airodump ambas bandas) = **máx ~5 redes distintas**. El dongle
  oye SIEMPRE los objetivos del usuario (CLARO_fCexjw 5G, CLARO_J74r3f 2.4, su
  hotspot Geo22). Las que faltan vs el teléfono (9) son **vecinos de señal
  débil** (ej. CLARO_DzR99M, solo audible con airodump dedicado a 2.4) que la
  **antena fija (no cambiable)** del módulo no alcanza de forma fiable — y que
  §11 dice no tocar sin confirmar.
- No era país 99 (wlan1=phy2, 76 canales habilitados), no era el código
  (3d9d835 usa el mismo método), no eran pocas pasadas (4 iw scans saturan en
  4). Es RX de hardware. Única palanca física restante: alargador USB (antena
  no se puede cambiar). Se evaluó airodump por-banda (ganancia ±1 red vecina
  inconsistente a cambio de scan de ~25-30s) y el usuario eligió **no** hacerlo.

  - Sobre "antes veía 16 redes": esas 16 venían del **onboard wlan0 vía
    NetworkManager** (escanea en segundo plano y ACUMULA, + mejor antena), no
    del dongle. Era el merge revertido (`20fea87`). wlan1 en un barrido llega a
    ~4-5 reales. El usuario reconfirmó (2026-10-13) dejar **solo wlan1** (lo
    atacable), no mezclar wlan0. Hipótesis vieja del país 99 quedó descartada
    (wlan1=phy2, 76 canales; el 99 es del onboard wlan0).

## Bugs reportados (sesión de prueba en red real)
- [x] **#1 Lista de redes pierde entradas al volver de Clientes**: el box de
      resultados no reseteaba el scroll al mostrar una lista nueva → quedaba
      scrolleado y ocultaba las primeras filas. Fix: `lv_obj_scroll_to_y(box,0)`
      en cada render (redes, clientes, capturas, wordlists).
- [x] **#2 Deauth selectivo (por cliente)**: FUNCIONA. ✅
- [x] **#3 Deauth por red (broadcast) no tumba** — ✅ RESUELTO Y VALIDADO.
      Los clientes modernos ignoran el deauth broadcast; el dirigido (`-c`)
      sí. Ahora "Deauth red" (sin `client_mac`) → `wifi_ops.py` enumera las
      estaciones asociadas (airodump ~8s, solo escucha) y lanza un
      `aireplay --deauth -a <bssid> -c <mac>` DIRIGIDO a CADA cliente **en
      paralelo** durante `deauth_duration`. Si no detecta clientes, cae al
      broadcast clásico. Sin cambios en C/UI (el botón ya llama con
      `client_mac` vacío; el tap por cliente sigue siendo selectivo). Nueva
      función `deauth_network()` + helper `_station_macs()`; se loguea como
      `deauth_red`. El escaneo y la inyección NO corren a la vez (evita el
      problema de concurrencia airodump+aireplay documentado en
      `capture_handshake`).
      - [x] **Validado en red real (2026-10-13)**: "Deauth dirigido a N
        cliente(s), sostenido 20s" — tumba la red el tiempo configurado.
- [x] **#4 Error "utf-8 codec can't encode" al guardar Config** — RESUELTO.
      systemd corría sin locale (C/POSIX) → Python fallaba al codificar salida.
      Fix: `db_tool.py` fuerza `sys.stdout.reconfigure(utf-8)` + err ascii-safe;
      `piscan.service` setea LANG/LC_ALL=C.UTF-8 + PYTHONUTF8. Config guarda OK
      (DB value="20" confirmado).
- [x] **Canal 0 en redes 5GHz** (rompía deauth con `-22 invalid argument`) —
      RESUELTO (2026-10-13). El `iw` de trixie imprime `freq:` como **float**
      (`5180.0`) y `int("5180.0")` reventaba → canal 0; además las 5GHz no traen
      `DS Parameter set`. Fix en `wifi_scan.py`: `int(float(primer_token))` + se
      lee `* primary channel:` (fiable en HT/VHT). Validado: 5180→36, 5805→161.
- [x] **Escaneo canal 0 (origen previo)**: ya se derivaba del `freq:`; el bug que
      quedaba era el float (arriba). Tope de redes 15→20.
- [x] **#1 Lista pierde redes** — reset de scroll + escaneo completo. Validado.

## Por revisar (confirmar en pantalla; revertir si molesta)
- [ ] **Scroll animado HORIZONTAL del textarea** (`lv_obj_scroll_to_x(..., LV_ANIM_ON)`
      en `lv_textarea_scroll_to_cusor_pos`, en cada tecla). Solo aplica a claves
      **largas** que exceden el ancho del campo (~402 px; con montserrat_10 entran
      más chars antes de scrollear). Si molesta: apagarlo sin hackear internals de
      LVGL (evaluar opciones) o aceptar como limitación del SPI por-pixel
      (documentada en CLAUDE.md §2/§6). El bobbing **vertical** ya quedó resuelto.
- [ ] Verificar en la Pi que el botón **ver/ocultar** no pisa el campo ni el
      teclado (campo 402 px + ojo 52 px a la derecha, y=16).

## Por revertir (si una prueba sale mal)
- *(vacío por ahora — anotar aquí cualquier cambio a deshacer si rompe algo)*

---

## Hecho (histórico breve)
> El detalle completo de lo ya en producción está en `CLAUDE.md §3`.
- Fase 0 · ítem 1 (text_input: cursor + ojo + show_time) — ver arriba.

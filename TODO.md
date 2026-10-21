# TODO — PiScan

> Lista de trabajo viva para no perder el hilo entre sesiones. Versionada en el
> repo. Convención: `[x]` hecho, `[ ]` pendiente, `[~]` en progreso/a medias.
> El roadmap grande y el "por qué" de las decisiones viven en `CLAUDE.md`
> (§4 PENDIENTE, §8 Roadmap). Acá va el detalle operativo de lo pendiente.

---

## Estado actual

> **Fase 0 completa.** La Fase 1 está funcional; quedan pruebas de UI y
> comprobaciones puntuales en la Pi. El siguiente paso es cerrar esas
> validaciones y luego elegir el siguiente módulo del roadmap.

## Mejora de precisión WiFi — [~] código listo, pendiente de validar en la Pi
- [x] El parser conserva APs con SSID repetido, diferenciándolos por BSSID, y
      conserva la señal más fuerte observada para el mismo BSSID.
- [x] Clasifica OPEN, WEP, WPA, WPA2, WPA3 y transición WPA2/3 a partir de
      `iw scan`; la lista muestra el final del BSSID para identificar cada AP.
- [x] La detección de handshake exige un conteo positivo; `0 handshake` no
      marca una captura como válida.
- [x] Ocho pruebas unitarias focalizadas pasan localmente. En la Pi pasaron las
      seis pruebas originales antes de añadir los tests de escritura DB.
- [x] En la Pi se confirmó que aparecen los sufijos BSSID en pantalla y que
      `wifi_scan_log` persiste el BSSID completo.
- [ ] Contrastar la clasificación con WPA3/transición usando una salida real de
      `iw scan` si hay un AP de laboratorio disponible.
- [ ] Validar en Pi los cambios recientes del manejo de errores de escritura
      del historial en DB.

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

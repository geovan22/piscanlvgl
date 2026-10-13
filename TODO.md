# TODO — PiScan

> Lista de trabajo viva para no perder el hilo entre sesiones. Versionada en el
> repo. Convención: `[x]` hecho, `[ ]` pendiente, `[~]` en progreso/a medias.
> El roadmap grande y el "por qué" de las decisiones viven en `CLAUDE.md`
> (§4 PENDIENTE, §8 Roadmap). Acá va el detalle operativo de la fase en curso.

---

## Fase 0 — Terminar WiFi / Config (en curso)

> Estado: **código de los 3 ítems completo.** Ítem 1 cerrado. Ítems 2 y 3 a
> validar en red real (tiempo del deauth sostenido). Cuando eso quede OK, se
> cierra la Fase 0 y se arranca la Fase 1 (CLAUDE.md §8).

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
- Nota: guarda en cada tap (fork+exec python ~150ms). Si se siente lento al
  tap repetido, pasar a guardar-al-salir o con botón "Guardar" (anotar en
  "Por revisar").

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

> Marco: siempre laboratorio controlado / redes propias o con permiso.

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
- [ ] Beacon flood / probe spam (mdk4 — NO instalado; resolver instalación).
- [ ] Evil Twin / AP falso (hostapd+dnsmasq) — solo lab.

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

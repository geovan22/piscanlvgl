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
- [ ] Pendiente: confirmar en red real que el tiempo configurado se respeta en
      el deauth sostenido (el usuario lo prueba cuando libere su red de pruebas).
- Nota: guarda en cada tap (fork+exec python ~150ms). Si se siente lento al
  tap repetido, pasar a guardar-al-salir o con botón "Guardar" (anotar en
  "Por revisar").

### 3. Deauth sostenido en la UI (Patrón A) — [~] código listo, a validar en red
- [x] Cableado C→Python del `duration`: `wifi_client_deauth` ahora toma
      `int duration` y lo pasa como 6º arg a `wifi_ops.py deauth` (el Python ya
      soportaba sostenido; solo el wrapper C no lo pasaba — mandaba ráfaga fija).
- [x] El botón **Deauth** lee `deauth_duration` de la DB (fresco en cada ataque),
      lo muestra en el diálogo de confirmación ("sostenido Ns" vs "ráfaga") y lo
      usa en el ataque. Mensajes de estado/éxito reflejan el modo.
- [x] Flujo Patrón A (ya existía): escanear → tocar red → Deauth → confirmar.
- [ ] **Validar en red real**: configurar N en Config, atacar la red de pruebas
      y confirmar que la red queda caída ~N s (sostenido) y vuelve al soltar.

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
      el deauth de red). El botón Deauth sigue siendo a toda la red (broadcast).
- [ ] **Validar en red real**: tocar un cliente y confirmar que solo ESE se cae
      (el resto sigue conectado).

### Resto Fase 1 (pendiente, ver CLAUDE.md §8)
- [ ] Evil Twin / AP falso (hostapd+dnsmasq) — solo lab.
- [ ] Captura PMKID (hcxdumptool).
- [ ] Beacon flood / probe spam (demo).
- [ ] Export .cap / .hccapx para crackeo externo.

---

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

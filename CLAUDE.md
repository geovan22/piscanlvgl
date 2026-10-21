# CLAUDE.md — PiScan

> Documento de contexto para Claude Code. **Léelo entero antes de tocar nada.**
> Describe qué es PiScan, cómo está construido, qué decisiones ya se tomaron
> (y por qué), qué NO se debe hacer, qué está hecho, qué está pendiente, y el
> roadmap completo de herramientas de pentesting por fases.
>
> Regla de oro: **sigue la línea de decisiones ya tomadas.** Si algo aquí
> contradice una ocurrencia nueva, gana lo documentado salvo que el usuario
> diga explícitamente lo contrario.

---

## 1. Qué es PiScan

PiScan es una herramienta portátil de auditoría WiFi / pentesting / laboratorio
de seguridad, pensada para uso de campo **sin SSH** (arranca sola, se opera
desde una pantalla táctil). Corre en una Raspberry Pi 3 B+ con una pantalla
SPI táctil KeDei de 3.5".

El objetivo es convertirla en una herramienta profesional tipo
**Flipper Zero / Bruce / M5 Cardputer**, pero aprovechando que debajo hay un
Linux completo: además de los módulos de RF/NFC/IR, se explota la Pi como
estación de auditoría (nmap, aircrack-ng, tcpdump, bluetooth, etc.).

**Marco de uso (importante y no negociable):** laboratorio controlado y pruebas
autorizadas sobre redes/dispositivos **propios del usuario** o con permiso
explícito. Hacking ético. Todo el código asume este marco.

---

## 2. 🚫 NO HACER (lecciones ya pagadas)

Estas cosas ya se intentaron y fallaron, o rompen el proyecto. **No repetir.**

- **NO tocar LVGL desde un hilo de trabajo.** Patrón obligatorio: el worker
  thread hace el trabajo lento, deja el resultado en variables + un flag
  `done`, y una función `poll_*` llamada desde el main-loop aplica el resultado
  a LVGL. Siempre NULL-guard a los labels (pueden haberse destruido al salir de
  la sección).
- **NO aplicar `ui_apply_press_effect()` a filas `lv_obj_create`** (las filas de
  resultados). Provoca bucle infinito de layout (`lv_obj_set_size` recursivo,
  80% CPU) al abrir un overlay encima. El efecto de presión es solo para
  botones.
- **NO intentar batch SPI en el driver KeDei.** Se intentó 3 veces → pantalla
  negra. El protocolo KeDei exige toggle de CS por cada unidad vía `ioctl`
  (dos `spi_transmit` de 3 bytes por pixel). Es limitación de hardware, no de
  software. El driver quedó en la versión per-pixel original. **No "optimizar"
  esto de nuevo.** (La palanca real de velocidad es la alimentación: ver §6.)
- **NO subir SPI a 42 MHz.** A 42 MHz la pantalla sale en blanco. Máximo
  estable = **34 MHz** (`speed = 34000000`).
- **NO usar `sed -i 'Nd'` / edición por número de línea** para parches. Los
  números cambian entre pasos y corrompe el archivo. Usar reemplazo de archivo
  completo o edición por contenido. Compilar entre pasos.
- **NO transferir código por base64 multilínea con caracteres raros.** Los
  caracteres `\177` (DEL), cirílicos lookalike (А/Б vs A/B), etc. corrompen el
  archivo. Si hay que transferir: base64 en una sola línea, y verificar
  `wc -l` después. (En el flujo VS Code / Remote-SSH esto ya casi no aplica:
  se edita el archivo directo.)
- **NO usar `check_handshake` ingenuo con `'handshake' in output`.** aircrack-ng
  SIEMPRE imprime "(0 handshake)". Hay que extraer N>0 con regex.
- **NO correr aircrack-ng síncrono por cada .cap al listar capturas.** Congela
  la UI. `list_captures()` usa heurística de tamaño (>100 KB) para marcar
  "probable handshake". El aircrack real solo al auditar.
- **NO bajar `LV_MEM_SIZE`.** Está en **256 KB**. A 64 KB el scan colgaba en
  `lv_array_resize` (80% CPU, confirmado con gdb).
- **NO filtrar airodump con `--bssid` en captura amplia** con este driver
  cuando no hace falta; y recordar que BSSID va **en mayúsculas** para
  aireplay-ng, con rutas absolutas a los binarios.
- **NO dejar que una contraseña WiFi equivocada quede guardada** como perfil
  NetworkManager (se auto-reconecta y estorba). `net_ops.connect()` borra el
  perfil si el connect falla.
- **NO usar la red de gestión (wlan0) como objetivo de deauth.** Te
  autodesconectas. Para pruebas de deauth contra el router de casa, gestionar
  por otra red o por cable.
- **NO recargar `rtl8xxxu`** para el adaptador nuevo. El adaptador nuevo es
  **RTL8821CU** (driver `rtw88_8821cu`), no necesita reset de módulo.
  `reset_driver()` quedó como `pass` (no-op). El código viejo de `modprobe -r
  rtl8xxxu` era para el adaptador viejo (RTL8188EUS) que se colgaba.
- **NO perseguir el "país 99 / dominio regulatorio" como causa de que wlan1
  liste pocas redes.** Ya se descartó a fondo (sesión 2026-10-13):
  `wlan1 = phy2` con **76 canales habilitados / 1 deshabilitado** → reg
  COMPLETO. El `phy#0` que aparece en `iw reg get` como **país 99 es el onboard
  (wlan0)**, NO wlan1. `iw reg set GT` + **recargar `rtw88_8821cu` NO cambió
  nada** (probado: seguía en ~4 BSS). El "pocas redes" de wlan1 es **límite de
  RX de su antena fija** (no cambiable), no el reg ni el código. Ve siempre los
  objetivos del usuario; las que faltan vs el teléfono son vecinos débiles
  (§11). Única palanca física: alargador USB. **No volver a tocar módulos ni
  reg por esto.**

---

## 3. ✅ HECHO

Funciona y está en producción. No rehacer salvo mejora pedida.

- **UI base** C + LVGL v9 (RENDER_MODE_PARTIAL), carrusel de menú, header con
  stats, footer como **barra de estado global** (color + prefijo
  `[OK]`/`[...]`/`[X]`) vía `ui_shell_set_status()`. Botones se deshabilitan
  durante operaciones.
- **Efecto de presión** en TODOS los botones (`ui_apply_press_effect` /
  `_danger` en `ui_style.h`), incluido keypad del PIN (borrar/confirmar).
- **PIN lock** con keypad propio.
- **Sección Config** (rediseñada 2026-10-19, **data-driven**): lista scrollable
  manejada por la tabla `CFG_ITEMS` en `ui_shell.c` (caja flex-column `g_cfg_box`
  + botones ▲▼). **Agregar un ajuste nuevo = una línea en `CFG_ITEMS`**, sin
  tocar layout. Tipos de fila: INT (−/+), BOOL (ON/OFF), CHOICE (◀/▶), con un
  único callback `cfg_ctl_cb` (índice+dir empacados en user_data). Guarda en la
  tabla Config al tocar. Items: `deauth_duration`, `beacon_duration`,
  `honeypot_portal` (CHOICE), `honeypot_bridge`, `honeypot_pcap`,
  `honeypot_probe_sniff`. (Antes eran offsets fijos que llenaban la pantalla.)
- **WiFi scan** (`wifi_scan.py` → `iw scan` → JSON), con etiqueta de banda
  **2.4G / 5G** en el listado (5G cyan `0x33CCFF`, 2.4G atenuado). Los APs se
  acumulan por BSSID (no por SSID), conservando la señal más fuerte del mismo
  BSSID; la fila muestra su terminación para distinguir SSIDs duplicados.
  Seguridad: OPEN/WEP/WPA/WPA2/WPA3/WPA2/3. Validado en la Pi: sufijos visibles
  en pantalla y BSSID completo persistido en `wifi_scan_log`; WPA3 real queda
  pendiente de un AP disponible.
- **Monitor mode**: botón Monitor **eliminado**. Se activa solo dentro de
  deauth/handshake. (Patrón confiable: reset→monitor→ataque→managed.)
- **Deauth** (burst y sostenido): `deauth(bssid, channel, count, iface,
  duration)`. `duration>0` → `--deauth 0` bajo `timeout <d>` (sostenido);
  si no, `--deauth <count>` (burst).
- **Handshake capture** end-to-end: airodump + aireplay, guarda `.cap` en
  `data/captures/`, detecta handshake por regex que exige conteo positivo
  (aircrack-ng también imprime `0 handshake`).
- **Audit de handshake** contra wordlists: `audit_handshake(cap, bssid,
  wordlist_key, timeout)` → aircrack-ng, parsea `KEY FOUND! [ ... ]`.
  Wordlists: `common` (comunes) y `full` (rockyou). `_resolve_wordlist(key)`
  resuelve por alias/filename/path. `list_wordlists` / `list_captures`.
- **Conectar Red** (wlan0 / NetworkManager, `net_ops.py`): scan + connect +
  listar/activar/olvidar redes guardadas, con teclado de contraseña. Permite
  SSH desde cualquier red. Borra perfil en connect fallido. El scan distingue
  lista vacía de fallos de `nmcli` y pasa el detalle a la UI; un `rescan`
  rechazado por `<30s` (límite de nmcli) es best-effort y NO se trata como
  error si la lista cacheada trae redes (solo falla si además sale vacía). IP
  estática configurada (.195).
- **Adaptador nuevo RTL8821CU** validado y adaptado en todo el tooling WiFi:
  dual-band 2.4/5 GHz + Bluetooth, driver `rtw88_8821cu` (`0bda:c820`),
  monitor + inyección OK. `reset_driver()` = no-op.
- **Autostart** vía systemd (`systemd/piscan.service`, `User=geo22`,
  `ExecStart=build/piscan_main`, `Restart=on-failure`, `After
  network-online`). Arranca con splash + PIN sin SSH.
- **Teclado de botones propio** (`text_input.c`): QWERTY + capa de símbolos + ñ,
  reemplaza `lv_keyboard` (que tenía lag por redibujado constante). Reutilizable.
- **DB SQLite** vía SQLAlchemy con CLI `db_tool.py` (config/credential
  get/set/verify/exists → JSON, invocado desde C por popen).
- **Sudoers** `/etc/sudoers.d/piscan-wifi` con NOPASSWD para los binarios
  necesarios (ver §7).

### Fase 1 — WiFi avanzado (COMPLETA, 2026-10-13)
- **Menú "Acciones" (overlay)** por AP en la sección WiFi: fila superior
  Escanear · Acciones · Capturas. Acciones abre un modal (patrón
  `confirm_dialog`) con: Clientes / Deauth red / Handshake / PMKID·EAPOL /
  Beacon flood / Evil Twin / Cancelar. Escala sin llenar la pantalla.
- **Clientes por AP** (`scan_clients`, airodump estaciones) + **OUI→fabricante**
  (base IEEE completa `data/oui.txt`) + MAC aleatorias etiquetadas.
- **Deauth**: selectivo por cliente (`-c`) y **"Deauth red" = dirigido a TODOS
  los clientes en paralelo** (el broadcast no tumba modernos). Duración
  configurable (`deauth_duration`, Config).
- **Handshake** + **Auditoría** (aircrack + wordlist común/rockyou).
- **PMKID/EAPOL clientless** (`hcxdumptool` 6.3.x → `.pcapng` → `hcxpcapngtool`
  → `.hc22000`). Se convierte también a `.cap` (tcpdump) para auditar
  on-device; aparece en **Capturas**.
- **Vista Capturas/Export**: lista HS + PMKID (SSID/BSSID/tipo/tamaño); tocar =
  auditar. Export = los `.hc22000`/`.cap` en `data/captures` salen por scp a
  hashcat (`-m 22000`).
- **Beacon flood** (`mdk4`, en `/usr/sbin`): nombres realistas (clon del scan +
  `data/beacon_ssids.txt`), duración configurable (`beacon_duration`, Config).
- **Evil Twin / Honeypot AP** (`evil_twin()` en wifi_ops, botón **Honeypot** en
  la fila WiFi — acción directa, no depende del listado): **AP abierto con
  nombre a elección** (se tipea con el teclado,
  ej. `Sala_Estar_Lopez`) → `ip 10.0.0.1/24` + dnsmasq (DHCP+DNS wildcard) →
  (sin deauth) → **portal de acceso** (http.server threading :8080) con form de
  **usuario + contraseña** ("inicie sesión para navegar"); lo que el cliente
  ingresa se guarda (marco: laboratorio autorizado / datos de prueba). Además
  registra TELEMETRIA por dispositivo (MAC+fabricante, IP, hostname DHCP, SO por
  User-Agent, dominios DNS vía dnsmasq log-queries, nro de hits). Todo va a la
  tabla `honeypot_log` (incl. `portal_user`/`portal_pass`) para análisis/reporte.
  Plantillas en `data/portals/*.html` ({{SSID}}) elegibles por Config
  `honeypot_portal`; el portal por defecto trae el form de login.
  - **Puente de internet + portal cautivo "walled garden"** (Config
    `honeypot_bridge=1`, toggle en pantalla Config; requiere wlan0 con internet;
    el SSH por wlan0 no se cae). Validado end-to-end (2026-10-15). Flujo:
    1. DNS **real** vía dnsmasq (upstream 8.8.8.8/1.1.1.1), para que los dominios
       de chequeo resuelvan y el móvil mande tráfico que interceptamos.
    2. **Redirect por cliente** en `nft` tabla `piscan_nat`: los **no
       autorizados** → su `tcp:80` se redirige al portal (`:8080`) y su `FORWARD`
       se corta; los que están en el set `@authorized` pasan de largo.
    3. **Candado de internet = masquerade condicionado a `@authorized`.** Sin
       login no hay NAT → el paquete sale con origen privado `10.0.0.x` y el
       router lo descarta = **sin internet aunque el forward lo dejara pasar**
       (candado principal). La cadena `fwd` (drop a no autorizados) es el candado
       secundario: aísla al no-logueado también de la **LAN** (`192.168.1.x`).
    4. Tras el **login**, `_et_authorize(ip)` mete la IP en `@authorized` →
       deja de redirigir + masquerade activo → **ahí recién tiene internet**.
    - **302 para gatillar el popup**: `do_GET` responde **302 →
      `http://10.0.0.1:8080/`** a los sondeos del SO (Host = dominio externo:
      connectivitycheck.gstatic.com, captive.apple.com, msftconnecttest.com…);
      Android lo interpreta como portal cautivo y abre el login solo. Cuando el
      navegador cautivo ya entra a `10.0.0.1`, sirve el HTML.
    - **Diagnóstico**: tras montar el puente se vuelca `nft list table ip
      piscan_nat` a `evil_twin.log` (sección `piscan_nat instalada:`) → ahí se ve
      si las 3 cadenas (`post`/`pre`/`fwd`) y el set `authorized` quedaron.
      Lección: `policy accept` en la def. compacta de una cadena nft la hace
      fallar silenciosamente; dejarla como `pre`/`post` (`{ type ... priority N ; }`).
  - **Sin puente** (`honeypot_bridge=0`): DNS wildcard (`address=/#/10.0.0.1`) +
    redirect `:80→:8080` (tabla `piscan_et`). Portal sí, internet no.
  - Teardown a managed (borra `piscan_nat`/`piscan_et`, `ip_forward=0`, flush,
    `pkill tcpdump`); log en `data/captures/evil_twin.log`. La UI muestra
    "N dispositivos".
  - **Estado validado (2026-10-21):** honeypot funcional; el log de sesión y el
    PCAP se crean, la ruta del PCAP queda en SQLite y las filas de telemetría se
    persisten. Cinco filas recientes pasaron controles de formato/rango y la
    referencia PCAP comprobada existe. Los campos extendidos dependen de la
    actividad y pueden estar vacíos en sesiones/dispositivos concretos.
  - **Pendiente de baja prioridad:** con el puente activo, el cliente logra
    hacer ping, pero la carga de páginas web es muy lenta o no termina. El
    puente da conectividad básica, pero la experiencia web queda pendiente de
    diagnóstico (DNS/HTTPS y forwarding desde la Pi y el cliente). No cambiar
    reglas del puente sin recoger antes esa evidencia. No bloquea continuar con
    otras tareas.
  - **Privacidad:** el código actual guarda `portal_user`/`portal_pass` en texto
    plano en SQLite. Usar solo valores ficticios durante pruebas; no consultar
    ni compartir salidas que incluyan estos campos.
  - **Telemetría extendida para auditoría (2026-10-15)** — se guarda por
    dispositivo en `honeypot_log` (campos en §7). Fuentes:
    - `iw station dump` **antes del teardown** → `rssi`, `bytes_rx/tx`, `dwell_s`.
    - dnsmasq con `log-dhcp` → `dhcp_vendor` (vendor class) + `dhcp_fingerprint`.
    - **pcap de la sesión** (`tcpdump`, Config `honeypot_pcap`, default ON) →
      se parsea con **tshark** para `sni_hosts` (sitios HTTPS por SNI, pasivo) y
      `mdns_name` (nombre `.local`). Si falta tcpdump/tshark, se omite sin romper.
    - `probe_ssids` (PNL) → `_probe_sniff()` pre-AP en monitor, Config
      `honeypot_probe_sniff` (default **OFF**: en rtw88 monitor+AP conviven mal;
      es experimental y best-effort, restaura a managed).
    - Todo el extra va en `try/except` silencioso: nunca tumba el honeypot.
  - **Portales**: viven en `data/portals/*.html` (`{{SSID}}` se sustituye).
    Se elige con Config `honeypot_portal` (nombre de archivo). Se cambia de dos
    formas: en **la pantalla Config** (fila CHOICE `Honeypot portal`, cicla con
    ◀/▶ los `.html` de la carpeta; `default` = sin plantilla) o por **CLI**
    (`python3 app/db_tool.py portals list` + `config set honeypot_portal
    <archivo>`). El portal por defecto (sin archivo) trae el form de login.
    Incluidos: `wifi_gratis.html` y `operador.html` (login con más diseño),
    `bienvenida.html`/`terminos.html` (informativos, sin captura). **Los portales
    no deben usar recursos externos** (fuentes/CDN): el cliente aún no tiene
    internet → todo CSS inline.
    - **Idea futura (roadmap)**: portal tipo **"juego/test"** — si el cliente
      pasa un mini-reto (quiz/captcha/jueguito) se lo autoriza (`_et_authorize`)
      en vez de pedir user/pass. Encaja directo en el walled garden (el POST que
      hoy captura credenciales pasaría a validar el reto y autorizar la IP).
  - Caveats (no bugs): el **popup captive automático** depende del teléfono
    (**DNS cifrado / Private DNS / DoH lo evita** → el usuario entra por el
    navegador; para probar, desactivar DNS privado en el móvil). Las sondas por
    **HTTPS no se interceptan** (no hay TLS MITM); Android cae al chequeo HTTP,
    que es el que gatilla el portal. Levanta **una sola** red conectable (hostapd
    multi-BSS no va bien en rtw88); la "variedad" de nombres la da el beacon
    flood (no conectable).
  - **wlan1 soporta AP mode** (`iw phy` lista `* AP`). El índice del phy
    **cambia** entre reboots/recargas → derivarlo de
    `/sys/class/net/wlan1/phy80211/index`, no asumir `phy2`.
- Pendiente de Fase 1 (diferido): captura PMKID ya hecha; lo único no hecho es
  si se quisiera un portal HTTPS/clonado más elaborado (no necesario).

---

## 4. ⏳ PENDIENTE

Estado actualizado 2026-10-21: Fase 0 completa. Fase 1 es funcional; quedan
validaciones puntuales en la Pi y en pantalla. La lentitud del internet
compartido por el honeypot es de **baja prioridad** y no bloquea avanzar.

### Validaciones inmediatas restantes
- Mejora de precisión WiFi validada en pantalla; BSSID completo confirmado en
  la DB. Ocho pruebas unitarias pasan localmente y en la Pi. Solo falta
  contrastar WPA3/transición con una salida real de `iw scan` si hay un AP de
  laboratorio disponible (ver `TODO.md`).
- Conectar Red validado en la Pi: escanea, conecta y muestra la IP. Errores de
  `nmcli` y lista vacía están cubiertos por pruebas unitarias; no se forzó un
  fallo real de `wlan0` para no afectar la red de gestión.
- Confirmar en pantalla el menú Acciones, Capturas y los flujos PMKID/beacon.
- Completar una comprobación controlada de la telemetría extendida del honeypot.
  `probe_ssids` es experimental y está desactivado por defecto.
- La lista Config (seis filas, ▲▼, controles, guardado, selector de portal y
  toggle del puente) ya fue confirmada funcional por el usuario en pantalla;
  queda registrada como validada en `TODO.md`.
- Ver el detalle operativo y las demás comprobaciones en `TODO.md`.

Después de cerrar las validaciones convenientes, continuar con la siguiente fase
del roadmap. Terminal sigue diferida por decisión del usuario. Diagnóstico del
rendimiento del internet compartido: pospuesto y de baja prioridad (§3).

---

## 5. Arquitectura

```
┌─────────────────────────────────────────────────┐
│  C + LVGL v9  (UI, main loop, estado)            │
│   app/ui_shell.c   ← UI principal, secciones     │
│   app/pin_lock.c   ← PIN                          │
│   app/confirm_dialog.c  app/text_input.c          │
│   app/ui_style.h   ← estilos / press-effect       │
│   app/wifi/*_client.c  ← puentes C ↔ Python       │
└───────────────┬─────────────────────────────────┘
                │ fork + exec + pipe, stdout = 1 JSON
                ▼
┌─────────────────────────────────────────────────┐
│  Scripts Python (trabajo de red / sistema)       │
│   app/wifi/wifi_scan.py   ← scan wlan1            │
│   app/wifi/wifi_ops.py    ← monitor/deauth/hs/audit│
│   app/wifi/net_ops.py     ← wlan0 / NetworkManager │
│   app/db_tool.py          ← acceso DB (CLI JSON)   │
│   app/db/models.py        ← modelos SQLAlchemy     │
│   app/diag.py             ← self-check / doctor    │
└─────────────────────────────────────────────────┘
```

**Contrato C ↔ Python:** cada script imprime **un único JSON** por stdout,
`exit 0` si `ok=true`. El lado C lo parsea con **cJSON**. Nunca mezclar prints
de debug con el JSON (rompe el parseo — ya pasó con un `import re` faltante que
tiraba traceback).

**Patrón hilo + poll (obligatorio para operaciones lentas):**
- Worker thread: hace el trabajo, deja resultado en variables globales + flag
  `done`.
- `poll_*()` en el main loop: si `done`, aplica a LVGL y limpia flag.
- Nunca LVGL desde el thread. NULL-guard a labels (sección pudo cerrarse).
- Al salir de una sección: resetear punteros globales a `NULL` (evita dangling
  → segfault/bucle, ej. `g_selected_row = NULL` tras `lv_obj_clean`).

**Secciones del menú:** WiFi / LAN (`lan`) / Herramientas (`tools`) / Config
(`config`) / Conectar Red (`net_connect`). (Bluetooth / Reportes se agregan en
el roadmap.)

---

## 6. Hardware

### Raspberry Pi 3 B+
- BCM2837, Raspberry Pi OS **trixie**, Python **3.13**.
- **Alimentación = palanca #1 de rendimiento.** El under-voltage (`0x50005`)
  estrangula la CPU a 600 MHz y hace que TODO se sienta lento (incluida la
  pantalla). Usar una fuente buena de 5V/2.5A+. Esto importa más que cualquier
  "optimización" de la pantalla.

### Pantalla KeDei 3.5" SPI TFT v5.0 (480×320)
- `/dev/spidev0.1`, **SPI = 34 MHz** (máx estable), `MADCTL = 0x2A`,
  `bufsiz = 4096`.
- Táctil **XPT2046** en `spidev0.0`. Calibración:
  `TCH_X_MIN=260, TCH_X_MAX=1798, TCH_Y_MIN=211, TCH_Y_MAX=1853`.
- Protocolo: `lcd_color(col)` = 2 `spi_transmit` (ioctl) de 3 bytes por pixel,
  con toggle de CS por unidad
  . **No batchear** (ver §2).
- `LV_MEM_SIZE = 256 KB`. `RENDER_MODE_PARTIAL`.

### Adaptadores WiFi
- **wlan0** = onboard `brcmfmac` → gestión / SSH. Es un **phy self-managed**;
  su bug país-99 bloqueaba 5 GHz (se fija país; su `phy` puede volver a 99 tras
  cambios de módulo). **Este 99 es de wlan0, NO de wlan1** — en `iw reg get`
  aparece como un phy propio en país 99. No confundirlo con wlan1 (ver §2: no
  perseguir el reg como causa del "pocas redes" de wlan1).
- **wlan1** = USB dedicado a ataque, `unmanaged` en NetworkManager.
  - **Actual: RTL8821CU** (`rtw88_8821cu`, `0bda:c820`), dual-band 2.4/5 GHz +
    **Bluetooth**, estable, monitor + inyección OK.
  - **Reg: OK y completo.** `wlan1 = phy2`, 76 canales habilitados (verificado
    2026-10-13). NO es self-managed estilo brcmfmac; sigue el `global`. No toca
    el país 99 del onboard. No hace falta fijarle reg ni recargar el módulo.
  - **Techo de scan ~4-5 redes = límite de RX de su antena fija** (no se puede
    cambiar; solo orientar). Oye siempre los objetivos del usuario; las redes
    que el teléfono ve y wlan1 no son vecinos débiles. No es bug del scanner.
    Única palanca física: alargador USB. (Diagnóstico completo en `TODO.md`.)
  - (Viejo: RTL8188EUS / `rtl8xxxu`, se colgaba en RX. Ya no se usa.)

### Módulos para el roadmap (el usuario los tiene)
- **PN532** — NFC/RFID (I2C o SPI).
- **CC1101** — sub-GHz (SPI). 315/433/868/915 MHz.
- **NRF24L01** — 2.4 GHz (SPI).
- **IR** — LED emisor + receptor (GPIO).

Recomendaciones de hardware futuras (opcionales): RTL-SDR (recepción amplia de
RF / análisis de espectro), GPS (geolocalización de wardriving), 2º adaptador
WiFi (para AP + monitor simultáneo), batería/UPS HAT (uso de campo real),
y modo **BadUSB gadget** (la Pi como teclado HID por el puerto USB OTG).

---

## 7. Base de datos (SQLite + SQLAlchemy)

**Decisión:** se mantiene SQLite como está. Documentarla bien aquí. Toda data
útil debe vivir en la DB.

### Acceso
- Modelos: `app/db/models.py` (SQLAlchemy). `get_session()` abre sesión.
- CLI: `app/db_tool.py <tabla> <accion> [args]` → **un JSON** por stdout.
  Invocado desde C vía `popen()`.
- Desde Python de red: `sys.path.insert(...~/piscanlvgl/app)` y
  `from db.models import get_session, WifiAttackLog` (patrón en `log_attack`).
- Credenciales: hash **SHA-256 con salt** (`salt + plaintext`), salt
  `token_hex(16)`. Nunca guardar plaintext.

### Tablas actuales
- **Config** — `key` (PK), `value`, `category`. Config general clave/valor.
  Claves actuales (cat `wifi`): `deauth_duration`, `beacon_duration`,
  `honeypot_bridge` (0/1 puente internet), `honeypot_portal` (archivo de
  `data/portals/` a usar), `honeypot_pcap` (0/1, default 1: pcap de sesión),
  `honeypot_probe_sniff` (0/1, default 0: pre-sniff de PNL, experimental).
- **SecurityCredential** — `cred_type`, `hash`, `salt`. PIN y otras credenciales.
- **PinConfig** — configuración del PIN.
- **Device** — dispositivos conocidos/descubiertos.
- **Action** — registro de acciones del usuario.
- **WifiScanLog** — historial de scans WiFi.
- **WifiAttackLog** — `attack_type`, `target_ssid`, `target_bssid`,
  `target_client_mac`, `result`, `details` (≤500 chars). Deauth/handshake.
  (El honeypot también loguea aquí un resumen por sesión vía `log_attack`:
  `attack_type="honeypot"`, `result=done/fail`, `details="clientes=N bridge=..."`.)
- **HoneypotLog** — telemetría **por dispositivo y por sesión** del honeypot (una
  fila por cliente observado). Llenada por `log_honeypot()` al cerrar la sesión.
  Campos:
  - `timestamp` (UTC), `ssid` (nombre del honeypot usado esa vez).
  - `client_mac` — MAC del cliente (DHCP). Puede ser **aleatoria/privada**
    (bit `0x02` del 1er octeto) → `vendor="(aleatoria)"`.
  - `vendor` — fabricante por OUI (de `data/oui.txt`), o `?`/`(aleatoria)`.
  - `client_ip` — IP asignada (DHCP, `10.0.0.x`).
  - `hostname` — nombre que el dispositivo mandó en el DHCP (ej. `Geo-22`).
  - `os_guess` — SO inferido (User-Agent del portal + dominios): Android/iOS/
    Windows/macOS/Linux/`?`.
  - `user_agent` — UA del navegador que tocó el portal (≤256).
  - `requests` — nº de hits HTTP al portal (interés/insistencia del cliente).
  - `portal_user` / `portal_pass` — **usuario y clave que tipeó en el portal**
    (texto plano, marco: lab autorizado / datos de prueba). Vacío si no completó.
  - `domains` — dominios DNS que consultó (de `dnsmasq log-queries`), separados
    por `, ` (≤2000). Útil para perfilar apps/servicios del dispositivo.
  - **Telemetría extendida (2026-10-15)** — por dispositivo, para auditoría:
    - `rssi` — señal en dBm (de `iw station dump`): proximidad física.
    - `bytes_rx` / `bytes_tx` — datos transferidos (uso real vs. de paso).
    - `dwell_s` — segundos conectado (tiempo de permanencia).
    - `dhcp_vendor` — *vendor class* DHCP (ej. `android-dhcp-14`, `MSFT 5.0`):
      identifica SO/modelo mejor que el UA, aunque no abran el portal.
    - `dhcp_fingerprint` — *requested options* DHCP (huella del stack).
    - `sni_hosts` — **hosts TLS SNI = sitios HTTPS que visitó** (de tshark sobre
      el pcap; pasivo, sin romper TLS). El dato que más perfila al usuario.
    - `mdns_name` — nombre `.local` que anunció por mDNS (suele traer el nombre
      real del equipo/dueño, ej. `iPhone-de-Juan.local`).
    - `probe_ssids` — **PNL**: redes que el equipo recuerda (probe requests).
      Solo si `honeypot_probe_sniff=1` (experimental, ver §3).
    - `pcap_path` — ruta del `.pcap` de la sesión (si `honeypot_pcap≠0`), para
      análisis forense offline (Wireshark).
  - `details` — libre (reservado).
  Cómo leerla (CLI, salida JSON **indentada** para lectura humana):
  - `python3 app/db_tool.py honeypot list [limit] [ssid]` — últimas filas (resumen),
    más recientes primero (default 20; filtra por ssid si se pasa).
  - `python3 app/db_tool.py honeypot show <id>` — detalle completo de una fila
    (incluye `client_ip`, `user_agent`, `domains`).
  (No depende de `sqlite3` CLI — usa SQLAlchemy. A futuro: vista "Reportes" en
  pantalla, Fase 8.) Interpretación: `portal_*` = credenciales capturadas;
  `domains`+`os_guess`+`user_agent` = fingerprint del dispositivo;
  `client_mac`/`vendor`/`hostname` = identidad; `requests` = cuánto interactuó.

### Tablas propuestas (para el roadmap — crear al implementar cada fase)
- **LanScanLog** — resultados nmap/arp-scan (host, MAC, puertos, servicios, OS).
- **BluetoothLog** — dispositivos BT/BLE descubiertos, servicios, acciones.
- **RfCaptureLog** — capturas CC1101/NRF24 (freq, modulación, raw, replay).
- **NfcLog** — tags NFC/RFID leídos/escritos (UID, tipo, dump).
- **IrLog** — señales IR capturadas/enviadas (protocolo, código).
- **AuditReport** — reportes generados (tipo, fecha, ruta del archivo, resumen).

**Convención:** cada módulo nuevo registra en su tabla vía un helper
`log_*()` con try/except silencioso (no debe bloquear el flujo si la DB falla —
patrón de `log_attack`).

---

## 8. Roadmap de pentesting por fases

> **Todo documentado; se implementa por fases.** El marco es siempre
> laboratorio controlado / pruebas autorizadas. Terminal = más adelante.

### Fase 0 — WiFi / Config (COMPLETA)
Teclado de contraseña, Config editable y Deauth sostenido en UI implementados y
validados (ver `TODO.md`).

### Fase 1 — WiFi avanzado (FUNCIONAL; validaciones finales pendientes)
- Escaneo de clientes conectados por AP (airodump con estaciones).
- Deauth selectivo a un cliente (`-c <client>`).
- Evil Twin / AP falso (hostapd + dnsmasq) — **solo lab**.
- Captura PMKID (hcxdumptool) además de handshake.
- Beacon flood / probe spam (demostración).
- Export de `.cap` / `.hccapx` para crackeo externo.
- **WPS** (`wash` + `reaver` Pixie-Dust / `bully`) — **solo lab**. Backend listo
  (`wps_scan`/`wps_attack` en `wifi_ops.py`, parsers con tests); pendiente en la
  Pi `apt install reaver`, sudoers y wire a la UI.

### Fase 2 — LAN (sección `lan`)
- `arp-scan` / `nmap -sn` → descubrimiento de hosts (→ LanScanLog).
- `nmap` escaneo de puertos/servicios/OS de un host.
- `tcpdump` captura de tráfico a archivo.
- MITM básico (`arpspoof` / `ettercap`) — solo lab.
- mDNS/SSDP/NetBIOS discovery.
- Test de puertos abiertos / banner grabbing.

### Fase 3 — Bluetooth (sección `bluetooth`, usa el RTL8821CU)
- Scan clásico + BLE (`bluetoothctl`, `hcitool`, `btmgmt`).
- Enumeración de servicios GATT (`gatttool` / `bleak`).
- Detección de dispositivos (→ BluetoothLog).
- BLE spam / advertising (demostración, lab).

### Fase 4 — RF sub-GHz (CC1101)
- Capturar señales 315/433/868/915 MHz (→ RfCaptureLog).
- Replay de señal capturada.
- Análisis de modulación básico.
- Brute force de mandos simples (lab).

### Fase 5 — 2.4 GHz (NRF24L01)
- Sniffing de dispositivos 2.4 GHz (teclados/ratones inalámbricos).
- Mousejacking (demostración, lab).
- Scanner de canales 2.4 GHz.

### Fase 6 — NFC / RFID (PN532)
- Leer UID / tipo de tag (→ NfcLog).
- Dump de tarjetas MIFARE Classic (lab, tarjetas propias).
- Emulación / escritura de tags.
- Clonado (solo tarjetas propias).

### Fase 7 — IR
- Capturar códigos IR (→ IrLog).
- Reenviar / replay.
- Base de datos de códigos comunes (TV-B-Gone style).

### Fase 8 — Reportes (sección `reportes`)
- Generar reporte por sesión/auditoría desde las tablas de log.
- Export a archivo (txt/markdown/pdf) en `data/reports/` (→ AuditReport).
- Resumen: redes vistas, ataques, hallazgos, timeline.

### Fase 9 — Terminal (diferida por decisión del usuario)
- Terminal embebida para comandos, actualizar la Pi, ver archivos de auditoría.
- Ejecutar scripts propios. Acceso controlado.

### Fase 10 — Extras de hardware (opcional)
- RTL-SDR, GPS/wardriving, 2º WiFi (AP+monitor), batería/UPS, BadUSB gadget-mode.

---

## 9. Build / test / deploy

```bash
# Compilar la librería LVGL (SOLO tras cambiar lv_conf.h)
~/piscanlvgl/scripts/build_lvgl_lib.sh      # genera liblvgl.a

# Compilar la app (incluye todos los .c: ui_shell, pin_lock, confirm_dialog,
# text_input, *_client.c, net_client.c)
~/piscanlvgl/scripts/build_app.sh           # → build/piscan_main

# Correr a mano (si el servicio está parado)
~/piscanlvgl/build/piscan_main

# Servicio autostart
sudo systemctl status piscan
sudo systemctl restart piscan
sudo systemctl stop piscan      # para desarrollar sin que relance
```

- Tras cambiar `lv_conf.h` → **rebuild de liblvgl.a** obligatorio, luego app.
- Compilar entre pasos. Verificar "SINTAXIS OK" antes de probar en pantalla.
- **Self-check / doctor:** `python3 app/diag.py` → JSON con binarios, sudoers,
  wlan0/wlan1 y DB. `exit 0` = todo OK; útil como chequeo antes de salir a campo.
- **Tests:** `python3 -m unittest discover -s tests` (unitarios puros, corren en
  PC o Pi; mockean subprocess/DB).
- Para desarrollar: `systemctl stop piscan` para que no pelee por la pantalla.
- **⚠️ Cambios en `.c`/`.h` NO toman efecto con solo `git pull` + restart:
  hay que `build_app.sh`.** Solo los `.py` toman efecto con pull+restart (no
  compilan). Error ya pagado (2026-10-13): el deauth corría ráfaga ~5s en vez de
  sostenido 20s porque el binario era viejo; el cableado de `deauth_duration`
  (C) estaba en el repo pero no en `build/piscan_main`. Si algo de la UI/C
  "no toma" un cambio del repo, **primero reconstruir el binario.**

### Sudoers (`/etc/sudoers.d/piscan-wifi`, versionado en
`systemd/sudoers-piscan-wifi`)
NOPASSWD para: `/usr/bin/ip`, `/usr/sbin/iw`, `/usr/sbin/aireplay-ng`,
`/usr/sbin/modprobe`, `/usr/bin/timeout`, `/usr/bin/stdbuf`,
`/usr/sbin/airodump-ng`, `/usr/bin/aircrack-ng`, `/usr/bin/nmcli`,
`/usr/bin/hcxdumptool`, `/usr/sbin/mdk4`, `/usr/sbin/hostapd`,
`/usr/sbin/dnsmasq`, `iptables`, `nft`, `/usr/sbin/sysctl`, `/usr/bin/pkill`,
`tcpdump`, `tshark` (estos últimos para el pcap/SNI/mDNS del honeypot),
`wash`, `reaver`, `bully` (WPS, en `/usr/bin` y `/usr/sbin`).
(piscan-power: `/sbin/poweroff`, `/sbin/reboot`.)
**Al agregar un módulo nuevo que necesite root, añadir su binario aquí** (ej.
`nmap`, `arp-scan`, `tcpdump`, `bluetoothctl`, `hcxdumptool`, etc.) con ruta
absoluta.

---

## 10. Git / flujo VS Code

- Repo: `git@github.com:geovan22/piscanlvgl.git` (SSH).
- Flujo: VS Code en el PC + **Remote-SSH** a la Pi (mismo VS Code) → editar,
  commit, compilar en la Pi.
- Commitear lo pendiente antes de cambios grandes. Mensajes claros en español.

### Atribución de commits (de esta sesión en adelante)
```
Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01YT8HfZQv9WaSXrTwiFMhL9
```

---

## 11. Datos del entorno del usuario (lab)

- Router de casa del usuario — las **2 primeras** redes del scan son suyas
  (para pruebas).
  - `CLARO_J74r3f` = **2.4 GHz** (a veces no la detecta / señal variable).
  - `CLARO_fCexjw` = **5 GHz** (red a la que se conecta normalmente).
- **No** usar `CLARO_J74r3f` como red de gestión si se va a hacer deauth contra
  ella (te autodesconectas).
- `CLARO_nEx6Rw` / `CLARO_DzR99M` posiblemente del vecino → **no conectar sin
  confirmar** con el usuario.
- Red/IP: wlan0 con IP estática .195, gateway .1.

> Las contraseñas de prueba son de redes **propias del usuario** y solo para su
> laboratorio. No reutilizar fuera de ese marco.

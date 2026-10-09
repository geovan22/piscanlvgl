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
- **WiFi scan** (`wifi_scan.py` → `iw scan` → JSON), con etiqueta de banda
  **2.4G / 5G** en el listado (5G cyan `0x33CCFF`, 2.4G atenuado).
- **Monitor mode**: botón Monitor **eliminado**. Se activa solo dentro de
  deauth/handshake. (Patrón confiable: reset→monitor→ataque→managed.)
- **Deauth** (burst y sostenido): `deauth(bssid, channel, count, iface,
  duration)`. `duration>0` → `--deauth 0` bajo `timeout <d>` (sostenido);
  si no, `--deauth <count>` (burst).
- **Handshake capture** end-to-end: airodump + aireplay, guarda `.cap` en
  `data/captures/`, detecta handshake por regex.
- **Audit de handshake** contra wordlists: `audit_handshake(cap, bssid,
  wordlist_key, timeout)` → aircrack-ng, parsea `KEY FOUND! [ ... ]`.
  Wordlists: `common` (comunes) y `full` (rockyou). `_resolve_wordlist(key)`
  resuelve por alias/filename/path. `list_wordlists` / `list_captures`.
- **Conectar Red** (wlan0 / NetworkManager, `net_ops.py`): scan + connect +
  listar/activar/olvidar redes guardadas, con teclado de contraseña. Permite
  SSH desde cualquier red. Borra perfil en connect fallido. IP estática
  configurada (.195).
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

---

## 4. ⏳ PENDIENTE

Orden acordado: **primero terminar WiFi/Config, luego el roadmap por fases.**

### 4.1 WiFi / Config (fase inmediata)
1. **`text_input.c`**: agregar botón mostrar/ocultar contraseña (ojo), y matar
   el lag del cursor del todo (desactivar parpadeo — ya está
   `anim_duration=0`; ocultar cursor si hace falta). Estaba a medias.
2. **Sección Config**: UI para editar config guardada en DB. Primer parámetro:
   `deauth_duration` (segundos) configurable.
3. **Deauth sostenido en la UI (Patrón A)**: un solo botón Deauth → vuelve a
   mostrar el listado de redes → seleccionar 1 → usa `deauth_duration` de la DB.

### 4.2 Roadmap por fases (documentado ahora, implementado después)
Ver §8. Terminal queda **para más adelante** (decisión del usuario).

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
  con toggle de CS por unidad. **No batchear** (ver §2).
- `LV_MEM_SIZE = 256 KB`. `RENDER_MODE_PARTIAL`.

### Adaptadores WiFi
- **wlan0** = onboard `brcmfmac` → gestión / SSH. Bug país-99 bloqueaba 5 GHz
  (resuelto fijando país; ojo que `phy` country puede volver a 99 tras cambios
  de módulo — revisar `iw reg get`).
- **wlan1** = USB dedicado a ataque, `unmanaged` en NetworkManager.
  - **Actual: RTL8821CU** (`rtw88_8821cu`, `0bda:c820`), dual-band 2.4/5 GHz +
    **Bluetooth**, estable, monitor + inyección OK.
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
  (Aquí va `deauth_duration`.)
- **SecurityCredential** — `cred_type`, `hash`, `salt`. PIN y otras credenciales.
- **PinConfig** — configuración del PIN.
- **Device** — dispositivos conocidos/descubiertos.
- **Action** — registro de acciones del usuario.
- **WifiScanLog** — historial de scans WiFi.
- **WifiAttackLog** — `attack_type`, `target_ssid`, `target_bssid`,
  `target_client_mac`, `result`, `details` (≤500 chars). Deauth/handshake.

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

### Fase 0 — Terminar WiFi / Config (en curso)
Ver §4.1. (show/hide password, Config con `deauth_duration`, Deauth Patrón A.)

### Fase 1 — WiFi avanzado
- Escaneo de clientes conectados por AP (airodump con estaciones).
- Deauth selectivo a un cliente (`-c <client>`).
- Evil Twin / AP falso (hostapd + dnsmasq) — **solo lab**.
- Captura PMKID (hcxdumptool) además de handshake.
- Beacon flood / probe spam (demostración).
- Export de `.cap` / `.hccapx` para crackeo externo.

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
- Para desarrollar: `systemctl stop piscan` para que no pelee por la pantalla.

### Sudoers (`/etc/sudoers.d/piscan-wifi`, versionado en
`systemd/sudoers-piscan-wifi`)
NOPASSWD para: `/usr/bin/ip`, `/usr/sbin/iw`, `/usr/sbin/aireplay-ng`,
`/usr/sbin/modprobe`, `/usr/bin/timeout`, `/usr/bin/stdbuf`,
`/usr/sbin/airodump-ng`, `/usr/bin/aircrack-ng`, `/usr/bin/nmcli`.
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

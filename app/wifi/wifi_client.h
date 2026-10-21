#ifndef WIFI_CLIENT_H
#define WIFI_CLIENT_H

#define WIFI_MAX_NETWORKS 20

typedef struct {
    char ssid[64];
    char bssid[24];
    float signal;
    int channel;
    char security[16];
    char band[8];
} wifi_network_t;

/* Escanea con wifi_scan.py. Llena out[] (hasta max_count), retorna
 * cuantas encontro, o -1 si hubo error (revisar out_error si no es NULL). */
int wifi_client_scan(wifi_network_t *out, int max_count, char *out_error, int error_size);

/* Retorna 1 si el modo monitor quedo activo, 0 si no (o error).
 * out_mode (si no es NULL) recibe "monitor"/"managed"/"unknown". */
int wifi_client_monitor_set(int enable, char *out_mode, int mode_size);
int wifi_client_monitor_status(char *out_mode, int mode_size);

/* Ataque de deauth. out_output recibe el texto real de aireplay-ng
 * (para mostrar en pantalla si se quiere), out_error si ok=0.
 * duration: 0 = rafaga (envia `count` y para); >0 = sostenido durante
 * `duration` segundos (--deauth 0 bajo timeout).
 * client_mac: NULL o "" = deauth a toda la red (broadcast); si viene un MAC,
 * el deauth es SELECTIVO a ese cliente (-c <mac>). */
int wifi_client_deauth(const char *bssid, int channel, int count, int duration,
                       const char *client_mac,
                       char *out_output, int output_size,
                       char *out_error, int error_size);

/* Captura de handshake WPA. Retorna 1 si se confirmo el handshake, 0 si
 * no (revisar out_detail para el motivo/log de diagnostico).
 * out_cap_file recibe la ruta del archivo .cap generado (siempre, aun
 * si no hubo handshake, por si se quiere revisar despues). */
int wifi_client_handshake(const char *bssid, int channel, int capture_seconds, int deauth_count,
                          char *out_cap_file, int cap_file_size,
                          char *out_detail, int detail_size);

/* Captura PMKID/EAPOL con hcxdumptool (clientless: NO necesita un cliente
 * conectado, a diferencia del handshake). Convierte a .hc22000 (hashcat).
 * Retorna 1 si capturo PMKID o EAPOL PARA ESE bssid, 0 si no capturo nada
 * util para ese AP, -1 si error. out_hc22000 recibe la ruta del .hc22000;
 * out_detail el resumen de hcxpcapngtool (o el mensaje de error). */
int wifi_client_pmkid(const char *bssid, int channel, int seconds,
                      char *out_hc22000, int hc_size,
                      char *out_detail, int detail_size);

/* Beacon flood (mdk4): inunda APs falsos de SSID aleatorio en `channel`
 * durante `seconds`. Demo/ruido, SOLO lab. Retorna 1 si corrio ok, 0 si error
 * (ver out_detail). */
int wifi_client_beacon(int channel, int seconds,
                       char *out_detail, int detail_size);

/* Ataque WPS (reaver Pixie-Dust) contra el AP `bssid` en `channel`, hasta
 * `seconds`. SOLO LAB / redes propias. Retorna 1 si obtuvo PIN o PSK, 0 si no
 * (AP sin WPS o no vulnerable), -1 si error (p.ej. reaver no instalado).
 * out_pin/out_psk reciben lo hallado (o ""); out_detail el resumen/cola de la
 * salida de reaver (o el mensaje de error). */
int wifi_client_wps_attack(const char *bssid, int channel, int seconds,
                           char *out_pin, int pin_size,
                           char *out_psk, int psk_size,
                           char *out_detail, int detail_size);

/* Honeypot de reconocimiento: AP abierto con `ssid` + portal informativo que
 * registra telemetria de los dispositivos (SIN credenciales). Bloquea `seconds`.
 * Retorna el numero de dispositivos registrados (>=0), o -1 si error.
 * out_summary recibe un resumen corto (ej. "3 dispositivo(s) (con internet)"). */
int wifi_client_evil_twin(const char *ssid, int seconds,
                          char *out_summary, int summary_size);

#define WIFI_MAX_CLIENTS 20

/* Un cliente (estacion) asociado a un AP, visto por airodump. */
typedef struct {
    char mac[24];
    char power[8];     /* dBm, como string (ej "-45", o "-1" desconocido) */
    char packets[12];  /* # de paquetes vistos */
    char vendor[24];   /* fabricante por OUI, o "?" si desconocido */
} wifi_client_sta_t;

/* Enumera los clientes asociados al AP `bssid` en `channel` (SOLO ESCUCHA,
 * sin inyeccion; airodump filtrado por bssid unos segundos). Llena out[]
 * (hasta max_count), retorna cuantos encontro, o -1 si error (ver out_error). */
int wifi_client_scan_clients(const char *bssid, int channel,
                             wifi_client_sta_t *out, int max_count,
                             char *out_error, int error_size);


#define WIFI_MAX_CAPTURES 20

/* Una captura .cap listada desde data/captures. */
typedef struct {
    char file[256];    /* ruta completa */
    char ssid[64];     /* nombre de red (cruzado con la DB) o "(desconocida)" */
    char bssid[24];
    long ts;           /* timestamp unix del nombre de archivo */
    long size;         /* tamano del archivo en bytes */
    int has_handshake; /* 1 si aircrack confirma handshake / PMKID */
} wifi_capture_t;

/* Lista las capturas .cap disponibles. Llena out[] (hasta max_count),
 * retorna cuantas hay, o -1 si error. */
int wifi_client_list_captures(wifi_capture_t *out, int max_count,
                              char *out_error, int error_size);

/* Audita un .cap contra una wordlist ("common" rapida / "full" completa).
 * Retorna 1 si encontro la contrasena (red debil), 0 si no la encontro,
 * -1 si error. Si la encuentra, out_password recibe la contrasena. */
int wifi_client_audit(const char *cap_file, const char *bssid,
                      const char *wordlist_key, int timeout_seconds,
                      char *out_password, int password_size,
                      char *out_error, int error_size);

#define WIFI_MAX_WORDLISTS 10

/* Una wordlist disponible en data/wordlists. */
typedef struct {
    char name[64];   /* nombre de archivo, ej "common.txt" */
    char key[64];    /* clave para pasar a audit (== name) */
    float size_mb;   /* tamano en MB */
    int fast;        /* 1 si es chica (<1MB), rapida en el Pi */
} wifi_wordlist_t;

/* Lista las wordlists .txt disponibles. Llena out[] (hasta max_count),
 * retorna cuantas hay, o -1 si error. */
int wifi_client_list_wordlists(wifi_wordlist_t *out, int max_count,
                               char *out_error, int error_size);

/* Self-check / doctor (app/diag.py). out_ok = 1 si todo OK (sin checks
 * en 'fail'); n_ok/n_warn/n_fail = contadores; out_detail = los checks NO-ok
 * (uno por linea, "[status] name: detail") o "Todo OK". Retorna 1 si ejecuto,
 * -1 si no se pudo. */
int diag_client_run(int *out_ok, int *n_ok, int *n_warn, int *n_fail,
                    char *out_detail, int detail_size);
#endif

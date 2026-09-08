#ifndef WIFI_CLIENT_H
#define WIFI_CLIENT_H

#define WIFI_MAX_NETWORKS 15

typedef struct {
    char ssid[64];
    char bssid[24];
    float signal;
    int channel;
    char security[16];
} wifi_network_t;

/* Escanea con wifi_scan.py. Llena out[] (hasta max_count), retorna
 * cuantas encontro, o -1 si hubo error (revisar out_error si no es NULL). */
int wifi_client_scan(wifi_network_t *out, int max_count, char *out_error, int error_size);

/* Retorna 1 si el modo monitor quedo activo, 0 si no (o error).
 * out_mode (si no es NULL) recibe "monitor"/"managed"/"unknown". */
int wifi_client_monitor_set(int enable, char *out_mode, int mode_size);
int wifi_client_monitor_status(char *out_mode, int mode_size);

/* Ataque de deauth. out_output recibe el texto real de aireplay-ng
 * (para mostrar en pantalla si se quiere), out_error si ok=0. */
int wifi_client_deauth(const char *bssid, int channel, int count,
                       char *out_output, int output_size,
                       char *out_error, int error_size);

/* Captura de handshake WPA. Retorna 1 si se confirmo el handshake, 0 si
 * no (revisar out_detail para el motivo/log de diagnostico).
 * out_cap_file recibe la ruta del archivo .cap generado (siempre, aun
 * si no hubo handshake, por si se quiere revisar despues). */
int wifi_client_handshake(const char *bssid, int channel, int capture_seconds, int deauth_count,
                          char *out_cap_file, int cap_file_size,
                          char *out_detail, int detail_size);


#define WIFI_MAX_CAPTURES 20

/* Una captura .cap listada desde data/captures. */
typedef struct {
    char file[256];    /* ruta completa */
    char ssid[64];     /* nombre de red (cruzado con la DB) o "(desconocida)" */
    char bssid[24];
    long ts;           /* timestamp unix del nombre de archivo */
    int has_handshake; /* 1 si aircrack confirma handshake */
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
#endif

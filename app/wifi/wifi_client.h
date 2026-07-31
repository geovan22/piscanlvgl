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

#endif

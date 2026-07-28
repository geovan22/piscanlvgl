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

#endif

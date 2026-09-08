#ifndef NET_CLIENT_H
#define NET_CLIENT_H

#define NET_MAX_NETWORKS 20
#define NET_MAX_SAVED 20

/* Una red de gestion disponible (wlan0). */
typedef struct {
    char ssid[64];
    int signal;
    char security[16];
    int in_use;      /* 1 si es la conectada actualmente */
} net_network_t;

/* Un perfil WiFi guardado en NetworkManager. */
typedef struct {
    char name[64];
    int autoconnect;
    int active;      /* 1 si es el perfil activo en wlan0 */
} net_saved_t;

/* Estado de wlan0. */
typedef struct {
    int connected;
    char ssid[64];
    char ip[24];
} net_status_t;

/* Escanea redes disponibles en wlan0. Retorna cuantas, o -1 si error. */
int net_client_scan(net_network_t *out, int max_count, char *out_error, int error_size);

/* Lista perfiles guardados. Retorna cuantos, o -1 si error. */
int net_client_list_saved(net_saved_t *out, int max_count, char *out_error, int error_size);

/* Estado actual de wlan0. Retorna 1 si ok, 0 si error. */
int net_client_status(net_status_t *out);

/* Conecta a una red. password puede ser NULL/"" para redes abiertas o
 * perfiles ya guardados. Retorna 1 si conecto, 0 si no. out_detail recibe
 * el mensaje de nmcli (motivo del fallo si aplica). */
int net_client_connect(const char *ssid, const char *password,
                       char *out_detail, int detail_size);

/* Olvida (borra) un perfil guardado. Retorna 1 si ok, 0 si no. */
int net_client_forget(const char *ssid);

#endif

#ifndef BT_CLIENT_H
#define BT_CLIENT_H

#define BT_MAX_DEVICES 32

/* Un dispositivo Bluetooth descubierto (BR/EDR o LE). */
typedef struct {
    char mac[24];
    char addr_type[12];  /* "public" | "random" | "" */
    char kind[8];        /* "classic" | "le" | "dual" */
    char name[64];       /* nombre resuelto (o "" si desconocido) */
    char vendor[32];     /* fabricante por OUI, "(aleatoria)" o "" */
    int  rssi;           /* dBm; 0 si no se detecto */
    int  tx_power;       /* dBm; 0 si no se detecto */
    char appearance[48]; /* BLE appearance (ej "Generic Watch (0x00C0)") */
    char icon[32];       /* icono bluez (ej "computer", "audio-card") */
    char cls[16];        /* Class of Device (BR/EDR, hex) */
    char uuids[480];     /* CSV de servicios: "Battery Service, GAP, ..." */
} bt_device_t;

/* Escanea con app/bluetooth/bt_ops.py durante `seconds`. mode: "dual" (default),
 * "le" o "classic". iface es "hci0"/"hci1" (NULL => "hci1" por defecto).
 * Llena out[] (hasta max_count) y retorna cuantos encontro, o -1 si error
 * (ver out_error). */
int bt_client_scan(const char *iface, int seconds, const char *mode,
                   bt_device_t *out, int max_count,
                   char *out_error, int error_size);

/* Enciende el controller (rfkill unblock + btmgmt power on). Retorna 1 ok. */
int bt_client_power(int on, const char *iface,
                    char *out_detail, int detail_size);

#endif

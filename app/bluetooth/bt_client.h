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
    char type_lbl[16];   /* etiqueta corta: PC/Celular/Audio/Teclado/... */
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

/* ───────────── Pentesting / auditoria por device ────────────────────────
 * Las tres acciones hacen fork+exec de bt_ops.py y arman un texto
 * multilinea listo para pintar en el overlay (lv_label). Retornan 1 si la
 * operacion termino bien (o parcialmente ok), 0 si fallo (ver out_text).
 * SOLO lab / dispositivos propios o con autorizacion.
 * ───────────────────────────────────────────────────────────────────── */

/* SDP browse (BR/EDR classic): lista servicios anunciados por SDP del target
 * sin pairing. `addr_type` ("public"/"random"/"" = unknown) es el hint del
 * scan para no bloquear injustamente devices que PiScan vio como "public". */
int bt_client_sdp(const char *mac, const char *addr_type,
                  char *out_text, int out_size);

/* GATT enumeration (LE): conecta via bluetoothctl, lista services + chars,
 * desconecta. Puede tardar >15s. Si el device requiere pairing, falla. */
int bt_client_gatt(const char *mac, const char *iface,
                   char *out_text, int out_size);

/* L2CAP ping (BR/EDR classic). Prueba de alcance + latencia. Count 1..50,
 * size 1..600. `addr_type` como en SDP. */
int bt_client_l2ping(const char *mac, int count, int size,
                     const char *addr_type,
                     char *out_text, int out_size);

/* ───────────── Acciones globales del controller ─────────────────────── */

/* BLE spam: rota el nombre advertising de `iface` durante `seconds` con
 * nombres de data/ble_names.txt (si mode="names") o aleatorios cortos
 * ("random"). bluez admite UN solo advertisement por controller a la vez,
 * por eso se rota. SOLO lab. Retorna 1 si ciclo >0. */
int bt_client_ble_spam(const char *iface, int seconds, const char *mode,
                       char *out_text, int out_size);

/* Captura HCI con btmon a archivo .btsnoop en data/captures/.
 * Devuelve 1 si size>0. Retorna el texto con path + tamaño + duracion. */
int bt_client_capture(const char *iface, int seconds,
                      char *out_text, int out_size);

#endif

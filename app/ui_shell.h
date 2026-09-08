#ifndef UI_SHELL_H
#define UI_SHELL_H

#include "lvgl/lvgl.h"

/* Construye header+footer+body (carrusel de menu) UNA sola vez.
 * Retorna la pantalla principal (ya cargada con lv_screen_load). */
lv_obj_t *ui_shell_build(void);

/* 0=nada, 1=poweroff pedido y confirmado, 2=reboot pedido y confirmado.
 * El loop principal en main.c debe leer esto cada iteracion y, si no es 0,
 * ejecutar la secuencia real de apagado/reinicio (fuera de cualquier
 * callback de LVGL, nunca dentro de uno). */
extern volatile int g_ui_pending_action;

/* Detiene el timer periodico de stats del header. Llamar ANTES de
 * destruir/limpiar la pantalla principal (ej. antes de mostrar el
 * splash de apagado) para evitar usar labels ya liberados. */
void ui_shell_stop_stats_timer(void);

/* Llamar cada iteracion del loop principal — aplica resultados de un
 * scan de WiFi en curso si ya termino (no bloquea si no hay nada). */
void ui_shell_poll_wifi_scan(void);

/* Igual, pero para la operacion de modo monitor. */

/* Igual, para la operacion de deauth. */
void ui_shell_poll_deauth(void);
void ui_shell_poll_handshake(void);
void ui_shell_poll_audit(void);
void ui_shell_poll_caplist(void);


/* Barra de estado global en el footer. El color permite un vistazo
 * rapido del estado; el prefijo ([...]/[OK]/[X]) permite leer el detalle. */
typedef enum {
    UI_STATUS_INFO = 0,   /* verde tenue, sin prefijo (navegacion) */
    UI_STATUS_WORKING,    /* amarillo, [...] operacion en curso */
    UI_STATUS_OK,         /* verde, [OK] exito */
    UI_STATUS_ERROR       /* rojo, [X] fallo */
} ui_status_t;

void ui_shell_set_status(const char *msg, ui_status_t kind);

#endif

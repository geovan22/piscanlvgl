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

#endif

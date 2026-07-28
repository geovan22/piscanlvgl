#ifndef UI_SHELL_H
#define UI_SHELL_H

#include "lvgl/lvgl.h"

/* Construye header+footer+body (carrusel de menu) UNA sola vez.
 * Retorna la pantalla principal (ya cargada con lv_screen_load). */
lv_obj_t *ui_shell_build(void);

#endif

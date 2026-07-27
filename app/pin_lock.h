#ifndef PIN_LOCK_H
#define PIN_LOCK_H

#include "lvgl/lvgl.h"
#include <stdbool.h>

typedef void (*pin_result_cb_t)(bool success, void *user_data);

/* Crear un PIN nuevo (4-8 digitos): ingresar, repetir para confirmar, guardar. */
void pin_lock_show_setup(lv_obj_t *parent, pin_result_cb_t on_result, void *user_data);

/* Verificar el PIN existente contra el guardado en la DB. */
void pin_lock_show_verify(lv_obj_t *parent, pin_result_cb_t on_result, void *user_data);

#endif

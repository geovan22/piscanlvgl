#ifndef TEXT_INPUT_H
#define TEXT_INPUT_H

#include "lvgl/lvgl.h"

/* Callback de resultado: text = texto ingresado si el usuario acepto,
 * o NULL si cancelo. No guardar el puntero: es valido solo durante la
 * llamada (se libera al cerrar el overlay). */
typedef void (*text_input_cb_t)(const char *text, void *user_data);

/* Muestra un overlay con titulo + campo de texto + teclado LVGL.
 * is_password: si es 1, oculta los caracteres. Reutilizable en cualquier
 * pantalla que necesite entrada de texto (contrasenas, nombres, etc.). */
void text_input_show(lv_obj_t *parent, const char *title, int is_password,
                     text_input_cb_t on_result, void *user_data);

#endif

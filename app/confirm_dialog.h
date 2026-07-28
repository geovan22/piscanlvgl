#ifndef CONFIRM_DIALOG_H
#define CONFIRM_DIALOG_H

#include "lvgl/lvgl.h"
#include <stdbool.h>

typedef void (*confirm_result_cb_t)(bool confirmed, void *user_data);

/* Muestra un dialogo Si/No superpuesto sobre 'parent'. Se autodestruye
 * al tocar cualquiera de los dos botones y llama a on_result(). */
void confirm_dialog_show(lv_obj_t *parent, const char *message, confirm_result_cb_t on_result, void *user_data);

#endif

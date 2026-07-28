#ifndef SPLASH_H
#define SPLASH_H

#include "lvgl/lvgl.h"

/* Muestra el splash (imagen leida desde config) en la pantalla activa.
 * Si extra_text no es NULL, agrega una etiqueta abajo (ej "Apagando..."). */
lv_obj_t *splash_show(const char *extra_text);

#endif

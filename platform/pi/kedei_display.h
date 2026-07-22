#ifndef KEDEI_DISPLAY_H
#define KEDEI_DISPLAY_H

#include <stdint.h>

/* Abre SPI de pantalla y touch, corre lcd_init(). Retorna 0 si OK. */
int kedei_platform_init(void);

/* flush_cb para lv_display_set_flush_cb() */
void kedei_flush_cb(void *disp, const void *area, uint8_t *px_map);

/* read_cb para lv_indev_set_read_cb() */
void kedei_touch_read_cb(void *indev, void *data);

/* Lectura cruda para la herramienta de calibracion standalone.
 * Retorna 1 si hay toque (llena px/py calibrados y raw_x/raw_y sin calibrar),
 * 0 si no hay contacto. raw_x/raw_y pueden ser NULL si no interesan. */
int kedei_touch_read_debug(int *px, int *py, int *raw_x, int *raw_y);

/* Dibuja 4 cuadrados de colores en las 4 esquinas fisicas, para
 * verificar orientacion/espejo antes de calibrar el touch. */
void kedei_test_draw_corners(void);

#endif

/* ═══════════════════════════════════════════════════════
   main.c — Punto de entrada de PiScan (Pi 3, LVGL v9)
   Inicializa driver + LVGL, muestra el splash leido desde la config
   (DB via db_client), y lo deja en pantalla. Ctrl+C para salir.
   ═══════════════════════════════════════════════════════ */
#include <stdio.h>
#include <string.h>
#include <time.h>
#include <unistd.h>
#include "lvgl/lvgl.h"
#include "kedei_display.h"
#include "db_client.h"

static uint32_t tick_get_cb(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint32_t)(ts.tv_sec * 1000 + ts.tv_nsec / 1000000);
}

static void log_cb(lv_log_level_t level, const char *buf) {
    (void)level;
    printf("[LVGL LOG] %s\n", buf);
}

int main(void) {
    printf("[PiScan] Iniciando...\n");
    lv_log_register_print_cb(log_cb);

    if (kedei_platform_init() < 0) {
        fprintf(stderr, "[PiScan] Fallo al iniciar pantalla/touch\n");
        return 1;
    }

    lv_init();
    lv_tick_set_cb(tick_get_cb);
    lv_lodepng_init(); /* por si algun dia se carga un PNG, no molesta dejarlo */

    lv_display_t *disp = lv_display_create(480, 320);
    static uint8_t draw_buf[480 * 320 * 2];
    lv_display_set_buffers(disp, draw_buf, NULL, sizeof(draw_buf), LV_DISPLAY_RENDER_MODE_FULL);
    lv_display_set_flush_cb(disp, kedei_flush_cb);

    lv_indev_t *indev = lv_indev_create();
    lv_indev_set_type(indev, LV_INDEV_TYPE_POINTER);
    lv_indev_set_read_cb(indev, kedei_touch_read_cb);

    char splash_path[256] = {0};
    if (!db_config_get("splash_path", splash_path, sizeof(splash_path))) {
        fprintf(stderr, "[PiScan] No se encontro splash_path en config, usando default\n");
        snprintf(splash_path, sizeof(splash_path), "/home/geo22/piscanlvgl/assets/splash/current.bin");
    }
    printf("[PiScan] Splash: %s\n", splash_path);

    char lv_path[300];
    snprintf(lv_path, sizeof(lv_path), "A:%s", splash_path);

    lv_obj_t *scr = lv_screen_active();
    lv_obj_set_style_bg_color(scr, lv_color_black(), 0);
    lv_obj_set_style_bg_opa(scr, LV_OPA_COVER, 0);

    lv_obj_t *img = lv_image_create(scr);
    lv_obj_set_style_image_opa(img, LV_OPA_COVER, 0);
    lv_obj_set_style_image_recolor_opa(img, 0, 0);
    lv_image_set_src(img, lv_path);

    /* Correr un par de ciclos antes de centrar — el tamano real de la
     * imagen se confirma recien en el primer refresh, no al instante. */
    for (int i = 0; i < 3; i++) {
        lv_timer_handler();
        usleep(5000);
    }
    lv_obj_center(img);

    printf("[PiScan] Splash mostrado. Ctrl+C para salir.\n");

    while (1) {
        lv_timer_handler();
        usleep(5000);
    }

    return 0;
}

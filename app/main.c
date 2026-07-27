/* ═══════════════════════════════════════════════════════
   main.c — Punto de entrada de PiScan (Pi 3, LVGL v9)
   Flujo: splash (duracion configurable) -> pantalla negra (placeholder
   del menu, aun no construido) -> loop principal -> en Ctrl+C/SIGTERM,
   splash "Apagando..." y salida limpia.

   El poweroff/reboot REAL queda comentado a proposito — todavia no hay
   boton fisico de power en esta base LVGL nueva. Cuando se construya el
   header con ese boton, se conecta ahi. Por ahora Ctrl+C no apaga el Pi
   de verdad, solo simula la secuencia visual para poder probarla.
   ═══════════════════════════════════════════════════════ */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>
#include <signal.h>
#include "lvgl/lvgl.h"
#include "kedei_display.h"
#include "db_client.h"

static volatile sig_atomic_t g_shutdown_requested = 0;

static void signal_handler(int sig) {
    (void)sig;
    g_shutdown_requested = 1;
}

static uint32_t tick_get_cb(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint32_t)(ts.tv_sec * 1000 + ts.tv_nsec / 1000000);
}

static void log_cb(lv_log_level_t level, const char *buf) {
    (void)level;
    printf("[LVGL LOG] %s\n", buf);
}

/* Muestra el splash. Si extra_text no es NULL, agrega una etiqueta abajo
 * (usada para "Apagando sistema..." al salir). No bloquea — el llamador
 * decide cuanto tiempo dejarlo en pantalla. */
static lv_obj_t *show_splash(const char *extra_text) {
    lv_obj_t *scr = lv_screen_active();
    lv_obj_clean(scr);
    lv_obj_set_style_bg_color(scr, lv_color_black(), 0);
    lv_obj_set_style_bg_opa(scr, LV_OPA_COVER, 0);

    char splash_path[256] = {0};
    if (!db_config_get("splash_path", splash_path, sizeof(splash_path))) {
        snprintf(splash_path, sizeof(splash_path), "/home/geo22/piscanlvgl/assets/splash/current.bin");
    }
    char lv_path[300];
    snprintf(lv_path, sizeof(lv_path), "A:%s", splash_path);

    lv_obj_t *img = lv_image_create(scr);
    lv_obj_set_style_image_opa(img, LV_OPA_COVER, 0);
    lv_obj_set_style_image_recolor_opa(img, 0, 0);
    lv_image_set_src(img, lv_path);

    for (int i = 0; i < 3; i++) { lv_timer_handler(); usleep(5000); }
    lv_obj_center(img);

    if (extra_text) {
        lv_obj_t *label = lv_label_create(scr);
        lv_label_set_text(label, extra_text);
        lv_obj_set_style_text_color(label, lv_color_white(), 0);
        lv_obj_set_style_bg_color(label, lv_color_black(), 0);
        lv_obj_set_style_bg_opa(label, LV_OPA_70, 0);
        lv_obj_set_style_pad_all(label, 6, 0);
        lv_obj_align(label, LV_ALIGN_BOTTOM_MID, 0, -10);
    }

    lv_timer_handler();
    return scr;
}

static int get_config_int(const char *key, int default_val) {
    char value[64] = {0};
    if (!db_config_get(key, value, sizeof(value))) return default_val;
    int parsed = atoi(value);
    return parsed > 0 ? parsed : default_val;
}

int main(void) {
    printf("[PiScan] Iniciando...\n");
    lv_log_register_print_cb(log_cb);

    signal(SIGINT, signal_handler);
    signal(SIGTERM, signal_handler);

    if (kedei_platform_init() < 0) {
        fprintf(stderr, "[PiScan] Fallo al iniciar pantalla/touch\n");
        return 1;
    }

    lv_init();
    lv_tick_set_cb(tick_get_cb);
    lv_lodepng_init();

    lv_display_t *disp = lv_display_create(480, 320);
    static uint8_t draw_buf[480 * 320 * 2];
    lv_display_set_buffers(disp, draw_buf, NULL, sizeof(draw_buf), LV_DISPLAY_RENDER_MODE_FULL);
    lv_display_set_flush_cb(disp, kedei_flush_cb);

    lv_indev_t *indev = lv_indev_create();
    lv_indev_set_type(indev, LV_INDEV_TYPE_POINTER);
    lv_indev_set_read_cb(indev, kedei_touch_read_cb);

    /* ── Splash de arranque ── */
    int splash_ms = get_config_int("splash_duration_ms", 2500);
    printf("[PiScan] Mostrando splash %d ms\n", splash_ms);
    show_splash(NULL);
    uint32_t t0 = tick_get_cb();
    while ((tick_get_cb() - t0) < (uint32_t)splash_ms && !g_shutdown_requested) {
        lv_timer_handler();
        usleep(5000);
    }

    /* ── Placeholder: aqui va el menu principal, aun no construido ── */
    lv_obj_t *scr = lv_screen_active();
    lv_obj_clean(scr);
    lv_obj_set_style_bg_color(scr, lv_color_black(), 0);
    lv_obj_set_style_bg_opa(scr, LV_OPA_COVER, 0);
    lv_obj_t *placeholder = lv_label_create(scr);
    lv_label_set_text(placeholder, "PiScan listo. (Menu principal: pendiente)");
    lv_obj_set_style_text_color(placeholder, lv_color_hex(0x33FF33), 0);
    lv_obj_center(placeholder);
    lv_timer_handler();

    printf("[PiScan] Listo. Ctrl+C para simular apagado.\n");

    while (!g_shutdown_requested) {
        lv_timer_handler();
        usleep(5000);
    }

    /* ── Secuencia de "apagado" (visual solamente por ahora) ── */
    printf("[PiScan] Apagando...\n");
    show_splash("Apagando sistema...");
    uint32_t t1 = tick_get_cb();
    while ((tick_get_cb() - t1) < 1500) {
        lv_timer_handler();
        usleep(5000);
    }

    lv_obj_t *scr2 = lv_screen_active();
    lv_obj_clean(scr2);
    lv_obj_set_style_bg_color(scr2, lv_color_black(), 0);
    lv_obj_set_style_bg_opa(scr2, LV_OPA_COVER, 0);
    lv_timer_handler();

    /* TODO cuando exista el boton de power real en el header:
     *   system("sudo poweroff");   // o "sudo reboot"
     * Por ahora, solo salida limpia para no apagar la Pi en cada prueba. */
    printf("[PiScan] (poweroff real deshabilitado por ahora) Cerrado limpio.\n");

    return 0;
}

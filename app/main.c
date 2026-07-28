/* ═══════════════════════════════════════════════════════
   main.c — Punto de entrada de PiScan (Pi 3, LVGL v9)
   Flujo: splash -> PIN (setup/verify) -> header+body+footer (ui_shell)
   -> loop principal. Ctrl+C/SIGTERM: salida limpia SIN apagar el Pi
   (atajo de desarrollo). Boton de power/reset del header: secuencia
   real de apagado/reinicio (limpieza de procesos + splash + poweroff
   o reboot de verdad), ejecutada aca en el loop principal, nunca
   dentro de un callback de LVGL.
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
#include "pin_lock.h"
#include "ui_shell.h"
#include "splash.h"

static volatile sig_atomic_t g_shutdown_requested = 0;
static volatile sig_atomic_t g_pattern_ok = 0;
static volatile sig_atomic_t g_pattern_done = 0;

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

static void on_pattern_result(bool success, void *user_data) {
    (void)user_data;
    g_pattern_ok = success ? 1 : 0;
    g_pattern_done = 1;
}

static int get_config_int(const char *key, int default_val) {
    char value[64] = {0};
    if (!db_config_get(key, value, sizeof(value))) return default_val;
    int parsed = atoi(value);
    return parsed > 0 ? parsed : default_val;
}

/* Antes de apagar/reiniciar de verdad: matar cualquier proceso hijo que
 * pudiera seguir corriendo (herramientas de red futuras) y devolver
 * wlan1 a modo managed si quedo en monitor mode. */
static void cleanup_before_shutdown(void) {
    system("pkill -9 -f 'python3.*wifi_ops.py' 2>/dev/null");
    system("pkill -9 -f 'python3.*wifi_scan.py' 2>/dev/null");
    system("ip link set wlan1 down 2>/dev/null");
    system("iw dev wlan1 set type managed 2>/dev/null");
    system("ip link set wlan1 up 2>/dev/null");
}

static void run_real_shutdown_sequence(int action) {
    printf("[PiScan] Ejecutando limpieza antes de %s...\n",
           action == 1 ? "apagar" : "reiniciar");
    cleanup_before_shutdown();

    splash_show(action == 1 ? "Apagando sistema..." : "Reiniciando sistema...");
    uint32_t t1 = tick_get_cb();
    while ((tick_get_cb() - t1) < 1500) {
        lv_timer_handler();
        usleep(2000);
    }

    lv_obj_t *scr2 = lv_screen_active();
    lv_obj_clean(scr2);
    lv_obj_set_style_bg_color(scr2, lv_color_black(), 0);
    lv_obj_set_style_bg_opa(scr2, LV_OPA_COVER, 0);
    lv_timer_handler();

    printf("[PiScan] Ejecutando %s real...\n", action == 1 ? "poweroff" : "reboot");
    if (action == 1) {
        system("sudo poweroff");
    } else {
        system("sudo reboot");
    }
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
    static uint8_t draw_buf[480 * 60 * 2];
    lv_display_set_buffers(disp, draw_buf, NULL, sizeof(draw_buf), LV_DISPLAY_RENDER_MODE_PARTIAL);
    lv_display_set_flush_cb(disp, kedei_flush_cb);

    lv_indev_t *indev = lv_indev_create();
    lv_indev_set_type(indev, LV_INDEV_TYPE_POINTER);
    lv_indev_set_read_cb(indev, kedei_touch_read_cb);

    /* ── Splash de arranque ── */
    int splash_ms = get_config_int("splash_duration_ms", 2500);
    printf("[PiScan] Mostrando splash %d ms\n", splash_ms);
    splash_show(NULL);
    uint32_t t0 = tick_get_cb();
    while ((tick_get_cb() - t0) < (uint32_t)splash_ms && !g_shutdown_requested) {
        lv_timer_handler();
        usleep(2000);
    }

    /* ── PIN: setup si no existe, verify si existe ── */
    if (!g_shutdown_requested) {
        lv_obj_t *scr = lv_screen_active();
        int has_pin = db_credential_exists("lock_pin");
        printf("[PiScan] PIN existente: %s\n", has_pin ? "si" : "no");

        g_pattern_done = 0;
        g_pattern_ok = 0;
        if (has_pin) {
            pin_lock_show_verify(scr, on_pattern_result, NULL);
        } else {
            pin_lock_show_setup(scr, on_pattern_result, NULL);
        }

        while (!g_pattern_done && !g_shutdown_requested) {
            lv_timer_handler();
            usleep(2000);
        }
        printf("[PiScan] Resultado PIN: %s\n", g_pattern_ok ? "OK" : "cancelado/fallo");
    }

    /* ── Header + Body (carrusel) + Footer ── */
    if (!g_shutdown_requested) {
        ui_shell_build();
        lv_timer_handler();
    }

    printf("[PiScan] Listo. Ctrl+C simula apagado (sin apagar el Pi de verdad).\n");

    while (!g_shutdown_requested) {
        lv_timer_handler();
        usleep(2000);

        if (g_ui_pending_action != 0) {
            int action = g_ui_pending_action;
            g_ui_pending_action = 0;
            run_real_shutdown_sequence(action);
            /* system("sudo poweroff"/"reboot") deberia cortar el sistema.
             * Si por algun motivo no corta de inmediato, salimos limpio. */
            return 0;
        }
    }

    /* ── Ctrl+C / SIGTERM: solo salida visual, NO apaga el Pi de verdad
     * (atajo de desarrollo, distinto del boton real del header). ── */
    printf("[PiScan] Cerrando (Ctrl+C)...\n");
    splash_show("Cerrando (modo prueba)...");
    uint32_t t1 = tick_get_cb();
    while ((tick_get_cb() - t1) < 1000) {
        lv_timer_handler();
        usleep(2000);
    }

    lv_obj_t *scr2 = lv_screen_active();
    lv_obj_clean(scr2);
    lv_obj_set_style_bg_color(scr2, lv_color_black(), 0);
    lv_obj_set_style_bg_opa(scr2, LV_OPA_COVER, 0);
    lv_timer_handler();

    printf("[PiScan] Cerrado limpio (Pi sigue encendida).\n");
    return 0;
}

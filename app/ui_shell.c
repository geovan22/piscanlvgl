/* ═══════════════════════════════════════════════════════
   ui_shell.c — Header + Footer + Body (carrusel de menu).
   Header muestra: bloqueo, hora, fecha, temp, RAM, disco, load,
   bluetooth, wifi USB (ataque), wifi onboard (gestion), reset, power.
   ═══════════════════════════════════════════════════════ */
#include <stdio.h>
#include <string.h>
#include <time.h>
#include <unistd.h>
#include <sys/statvfs.h>
#include "ui_shell.h"
#include "pin_lock.h"
#include "confirm_dialog.h"

#define HEADER_H 28
#define FOOTER_H 28

typedef struct {
    const char *label;
    const char *id;
} menu_item_t;

static const menu_item_t MENU_ITEMS[] = {
    { "WiFi",          "wifi" },
    { "LAN",           "lan" },
    { "Herramientas",  "tools" },
    { "Config",        "config" },
    { "Conectar Red",  "net_connect" },
};
#define MENU_COUNT (int)(sizeof(MENU_ITEMS) / sizeof(MENU_ITEMS[0]))

static lv_obj_t *g_main_screen;
static lv_obj_t *g_lock_screen;
static lv_obj_t *g_body;
static lv_obj_t *g_footer_label;

static lv_obj_t *g_clock_label;
static lv_obj_t *g_date_label;
static lv_obj_t *g_temp_label;
static lv_obj_t *g_ram_label;
static lv_obj_t *g_disk_label;
static lv_obj_t *g_load_label;
static lv_obj_t *g_bt_label;
static lv_obj_t *g_wlan1_label;
static lv_obj_t *g_wlan0_label;
static lv_obj_t *g_battery_icon_label;
static lv_obj_t *g_battery_pct_label;

static int g_menu_index = 0;
static lv_timer_t *g_stats_timer = NULL;

/* Accion pendiente que el loop principal (main.c) debe ejecutar —
 * el callback del boton NUNCA bloquea ni llama lv_timer_handler() el
 * mismo, solo marca la intencion y retorna de inmediato. */
volatile int g_ui_pending_action = 0; /* 0=nada 1=poweroff 2=reboot */

#define COLOR_OK    lv_color_hex(0x33FF33)
#define COLOR_WARN  lv_color_hex(0xFFCC00)
#define COLOR_ERR   lv_color_hex(0xFF4444)
#define COLOR_DIM   lv_color_hex(0x2a6b2a)

static void set_footer(const char *msg) {
    lv_label_set_text(g_footer_label, msg);
}

/* ── Lecturas de sistema (todas via /proc, /sys — sin subprocess) ── */
static int read_ram_percent(void) {
    FILE *f = fopen("/proc/meminfo", "r");
    if (!f) return -1;
    long total = 0, avail = 0;
    char line[128];
    while (fgets(line, sizeof(line), f)) {
        sscanf(line, "MemTotal: %ld kB", &total);
        sscanf(line, "MemAvailable: %ld kB", &avail);
    }
    fclose(f);
    if (total <= 0) return -1;
    return (int)(100 * (total - avail) / total);
}

static int read_temp_c(void) {
    FILE *f = fopen("/sys/class/thermal/thermal_zone0/temp", "r");
    if (!f) return -1;
    int milli = 0;
    if (fscanf(f, "%d", &milli) != 1) { fclose(f); return -1; }
    fclose(f);
    return milli / 1000;
}

static int read_disk_percent(void) {
    struct statvfs st;
    if (statvfs("/", &st) != 0) return -1;
    if (st.f_blocks == 0) return -1;
    return (int)(100 * (st.f_blocks - st.f_bfree) / st.f_blocks);
}

static float read_load_avg(void) {
    FILE *f = fopen("/proc/loadavg", "r");
    if (!f) return -1.0f;
    float load1 = -1.0f;
    if (fscanf(f, "%f", &load1) != 1) { fclose(f); return -1.0f; }
    fclose(f);
    return load1;
}

/* TODO: implementar lectura real cuando se resuelva el circuito de
 * monitoreo del pack (ej. leer voltaje via ADC, o un fuel-gauge I2C
 * como el MAX17048 si se agrega uno mas adelante). Por ahora retorna
 * -1 ("sin datos") para dejar el espacio ya listo en el header. */
static int read_battery_percent(void) {
    return -1;
}

static int is_bt_present(void) {
    return access("/sys/class/bluetooth/hci0", F_OK) == 0;
}

static int wlan_exists(const char *iface) {
    char path[64];
    snprintf(path, sizeof(path), "/sys/class/net/%s", iface);
    return access(path, F_OK) == 0;
}

static int wlan_is_up(const char *iface) {
    char path[64];
    snprintf(path, sizeof(path), "/sys/class/net/%s/operstate", iface);
    FILE *f = fopen(path, "r");
    if (!f) return 0;
    char buf[16] = {0};
    if (!fgets(buf, sizeof(buf), f)) { fclose(f); return 0; }
    fclose(f);
    return strncmp(buf, "up", 2) == 0;
}

/* ── Timer periodico: actualiza hora/fecha/stats/conectividad ── */
static void stats_timer_cb(lv_timer_t *t) {
    (void)t;
    char buf[16];

    time_t now = time(NULL);
    struct tm *tmv = localtime(&now);
    strftime(buf, sizeof(buf), "%H:%M", tmv);
    lv_label_set_text(g_clock_label, buf);
    strftime(buf, sizeof(buf), "%d/%m", tmv);
    lv_label_set_text(g_date_label, buf);

    int temp = read_temp_c();
    if (temp >= 0) {
        snprintf(buf, sizeof(buf), "%dC", temp);
        lv_label_set_text(g_temp_label, buf);
        lv_obj_set_style_text_color(g_temp_label, temp > 70 ? COLOR_ERR : temp > 60 ? COLOR_WARN : COLOR_OK, 0);
    }

    int ram = read_ram_percent();
    if (ram >= 0) {
        snprintf(buf, sizeof(buf), "R%d%%", ram);
        lv_label_set_text(g_ram_label, buf);
        lv_obj_set_style_text_color(g_ram_label, ram > 85 ? COLOR_ERR : ram > 70 ? COLOR_WARN : COLOR_OK, 0);
    }

    int disk = read_disk_percent();
    if (disk >= 0) {
        snprintf(buf, sizeof(buf), "D%d%%", disk);
        lv_label_set_text(g_disk_label, buf);
        lv_obj_set_style_text_color(g_disk_label, disk > 90 ? COLOR_ERR : disk > 75 ? COLOR_WARN : COLOR_OK, 0);
    }

    float load = read_load_avg();
    if (load >= 0.0f) {
        snprintf(buf, sizeof(buf), "L%.1f", load);
        lv_label_set_text(g_load_label, buf);
        lv_obj_set_style_text_color(g_load_label, load > 3.0f ? COLOR_ERR : load > 1.5f ? COLOR_WARN : COLOR_DIM, 0);
    }

    int batt = read_battery_percent();
    if (batt >= 0) {
        const char *icon = batt > 80 ? LV_SYMBOL_BATTERY_FULL :
                            batt > 60 ? LV_SYMBOL_BATTERY_3 :
                            batt > 40 ? LV_SYMBOL_BATTERY_2 :
                            batt > 15 ? LV_SYMBOL_BATTERY_1 : LV_SYMBOL_BATTERY_EMPTY;
        lv_label_set_text(g_battery_icon_label, icon);
        lv_obj_set_style_text_color(g_battery_icon_label, batt > 20 ? COLOR_OK : COLOR_ERR, 0);
        char bbuf[8];
        snprintf(bbuf, sizeof(bbuf), "%d%%", batt);
        lv_label_set_text(g_battery_pct_label, bbuf);
        lv_obj_set_style_text_color(g_battery_pct_label, batt > 20 ? COLOR_OK : COLOR_ERR, 0);
    } else {
        lv_label_set_text(g_battery_icon_label, LV_SYMBOL_BATTERY_FULL);
        lv_obj_set_style_text_color(g_battery_icon_label, COLOR_DIM, 0);
        lv_label_set_text(g_battery_pct_label, "--");
        lv_obj_set_style_text_color(g_battery_pct_label, COLOR_DIM, 0);
    }

    lv_obj_set_style_text_color(g_bt_label, is_bt_present() ? COLOR_OK : COLOR_DIM, 0);

    /* wlan1 = adaptador USB de ataque: nos importa que EXISTA y este arriba,
     * no que este "conectado a una red" (normalmente esta en monitor mode). */
    int w1_ok = wlan_exists("wlan1") && wlan_is_up("wlan1");
    lv_obj_set_style_text_color(g_wlan1_label, w1_ok ? COLOR_OK : COLOR_ERR, 0);

    /* wlan0 = WiFi de gestion (SSH). Aqui si importa "conectado". */
    int w0_ok = wlan_is_up("wlan0");
    lv_obj_set_style_text_color(g_wlan0_label, w0_ok ? COLOR_OK : COLOR_ERR, 0);
}

/* ── Icono de candado dibujado a mano ── */
static lv_obj_t *make_lock_icon(lv_obj_t *parent) {
    lv_obj_t *c = lv_obj_create(parent);
    lv_obj_set_size(c, 22, 22);
    lv_obj_set_style_bg_opa(c, LV_OPA_TRANSP, 0);
    lv_obj_set_style_border_width(c, 0, 0);
    lv_obj_set_style_pad_all(c, 0, 0);
    lv_obj_clear_flag(c, LV_OBJ_FLAG_SCROLLABLE);

    lv_obj_t *arc = lv_arc_create(c);
    lv_obj_set_size(arc, 14, 14);
    lv_arc_set_bg_angles(arc, 180, 360);
    lv_arc_set_value(arc, 0);
    lv_obj_set_style_arc_color(arc, COLOR_OK, LV_PART_MAIN);
    lv_obj_set_style_arc_width(arc, 3, LV_PART_MAIN);
    lv_obj_remove_style(arc, NULL, LV_PART_KNOB);
    lv_obj_remove_style(arc, NULL, LV_PART_INDICATOR);
    lv_obj_align(arc, LV_ALIGN_TOP_MID, 0, 0);
    lv_obj_clear_flag(arc, LV_OBJ_FLAG_CLICKABLE);

    lv_obj_t *body = lv_obj_create(c);
    lv_obj_set_size(body, 18, 12);
    lv_obj_set_style_bg_color(body, COLOR_OK, 0);
    lv_obj_set_style_bg_opa(body, LV_OPA_COVER, 0);
    lv_obj_set_style_border_width(body, 0, 0);
    lv_obj_set_style_radius(body, 2, 0);
    lv_obj_align(body, LV_ALIGN_BOTTOM_MID, 0, 0);
    lv_obj_clear_flag(body, LV_OBJ_FLAG_CLICKABLE);

    return c;
}

static void draw_menu_current(void);
static void enter_section(const char *id, const char *label);
static void show_carousel(void);

static void on_relock_verified(bool success, void *user_data) {
    (void)user_data;
    if (success) lv_screen_load(g_main_screen);
}

static void lock_icon_event_cb(lv_event_t *e) {
    if (lv_event_get_code(e) != LV_EVENT_PRESSED) return;
    lv_screen_load(g_lock_screen);
    pin_lock_show_verify(g_lock_screen, on_relock_verified, NULL);
}

static void on_power_confirm(bool confirmed, void *user_data) {
    (void)user_data;
    if (confirmed) {
        set_footer("Apagando...");
        g_ui_pending_action = 1;
    }
}

static void on_reset_confirm(bool confirmed, void *user_data) {
    (void)user_data;
    if (confirmed) {
        set_footer("Reiniciando...");
        g_ui_pending_action = 2;
    }
}

static void power_icon_event_cb(lv_event_t *e) {
    if (lv_event_get_code(e) != LV_EVENT_PRESSED) return;
    confirm_dialog_show(g_main_screen, "Apagar el sistema?", on_power_confirm, NULL);
}

static void reset_icon_event_cb(lv_event_t *e) {
    if (lv_event_get_code(e) != LV_EVENT_PRESSED) return;
    confirm_dialog_show(g_main_screen, "Reiniciar el sistema?", on_reset_confirm, NULL);
}

static void back_to_carousel_cb(lv_event_t *e) {
    if (lv_event_get_code(e) != LV_EVENT_PRESSED) return;
    show_carousel();
}

static void enter_section(const char *id, const char *label) {
    (void)id;
    lv_obj_clean(g_body);

    lv_obj_t *title = lv_label_create(g_body);
    char buf[64];
    snprintf(buf, sizeof(buf), "%s\n(Proximamente)", label);
    lv_label_set_text(title, buf);
    lv_obj_set_style_text_color(title, COLOR_OK, 0);
    lv_obj_set_style_text_align(title, LV_TEXT_ALIGN_CENTER, 0);
    lv_obj_center(title);

    lv_obj_t *back = lv_button_create(g_body);
    lv_obj_set_size(back, 90, 36);
    lv_obj_align(back, LV_ALIGN_BOTTOM_LEFT, 20, -20);
    lv_obj_set_style_bg_color(back, lv_color_hex(0x0a2a0a), 0);
    lv_obj_set_style_border_color(back, COLOR_OK, 0);
    lv_obj_set_style_border_width(back, 2, 0);
    lv_obj_set_ext_click_area(back, 15);
    lv_obj_t *back_lbl = lv_label_create(back);
    lv_label_set_text(back_lbl, "< Menu");
    lv_obj_set_style_text_color(back_lbl, COLOR_OK, 0);
    lv_obj_center(back_lbl);
    lv_obj_add_event_cb(back, back_to_carousel_cb, LV_EVENT_PRESSED, NULL);

    set_footer(label);
}

static lv_obj_t *g_carousel_label;

static void select_event_cb(lv_event_t *e) {
    if (lv_event_get_code(e) != LV_EVENT_PRESSED) return;
    enter_section(MENU_ITEMS[g_menu_index].id, MENU_ITEMS[g_menu_index].label);
}

static void arrow_left_cb(lv_event_t *e) {
    if (lv_event_get_code(e) != LV_EVENT_PRESSED) return;
    g_menu_index = (g_menu_index - 1 + MENU_COUNT) % MENU_COUNT;
    draw_menu_current();
}

static void arrow_right_cb(lv_event_t *e) {
    if (lv_event_get_code(e) != LV_EVENT_PRESSED) return;
    g_menu_index = (g_menu_index + 1) % MENU_COUNT;
    draw_menu_current();
}

static void draw_menu_current(void) {
    lv_label_set_text(g_carousel_label, MENU_ITEMS[g_menu_index].label);
}

static void show_carousel(void) {
    lv_obj_clean(g_body);

    lv_obj_t *left = lv_button_create(g_body);
    lv_obj_set_size(left, 50, 60);
    lv_obj_align(left, LV_ALIGN_LEFT_MID, 15, 0);
    lv_obj_set_style_bg_color(left, lv_color_hex(0x0a2a0a), 0);
    lv_obj_set_style_border_color(left, COLOR_OK, 0);
    lv_obj_set_style_border_width(left, 2, 0);
    lv_obj_set_ext_click_area(left, 15);
    lv_obj_t *left_lbl = lv_label_create(left);
    lv_label_set_text(left_lbl, "<");
    lv_obj_set_style_text_color(left_lbl, COLOR_OK, 0);
    lv_obj_center(left_lbl);
    lv_obj_add_event_cb(left, arrow_left_cb, LV_EVENT_PRESSED, NULL);

    lv_obj_t *right = lv_button_create(g_body);
    lv_obj_set_size(right, 50, 60);
    lv_obj_align(right, LV_ALIGN_RIGHT_MID, -15, 0);
    lv_obj_set_style_bg_color(right, lv_color_hex(0x0a2a0a), 0);
    lv_obj_set_style_border_color(right, COLOR_OK, 0);
    lv_obj_set_style_border_width(right, 2, 0);
    lv_obj_set_ext_click_area(right, 15);
    lv_obj_t *right_lbl = lv_label_create(right);
    lv_label_set_text(right_lbl, ">");
    lv_obj_set_style_text_color(right_lbl, COLOR_OK, 0);
    lv_obj_center(right_lbl);
    lv_obj_add_event_cb(right, arrow_right_cb, LV_EVENT_PRESSED, NULL);

    lv_obj_t *center = lv_button_create(g_body);
    lv_obj_set_size(center, 160, 100);
    lv_obj_center(center);
    lv_obj_set_style_bg_color(center, lv_color_hex(0x0a2a0a), 0);
    lv_obj_set_style_border_color(center, COLOR_OK, 0);
    lv_obj_set_style_border_width(center, 2, 0);
    lv_obj_set_ext_click_area(center, 15);
    lv_obj_add_event_cb(center, select_event_cb, LV_EVENT_PRESSED, NULL);

    g_carousel_label = lv_label_create(center);
    lv_obj_set_style_text_color(g_carousel_label, COLOR_OK, 0);
    lv_obj_center(g_carousel_label);
    draw_menu_current();

    set_footer("PiScan listo");
}

static lv_obj_t *make_stat_label(lv_obj_t *parent, int x) {
    lv_obj_t *l = lv_label_create(parent);
    lv_obj_set_style_text_font(l, &lv_font_montserrat_10, 0);
    lv_obj_align(l, LV_ALIGN_LEFT_MID, x, 0);
    return l;
}

/* Detiene el timer de stats del header ANTES de destruir el header
 * (ej. al mostrar el splash de apagado) — evita usar labels ya
 * liberados por lv_obj_clean(). Segura de llamar aunque no exista. */
void ui_shell_stop_stats_timer(void) {
    if (g_stats_timer) {
        lv_timer_del(g_stats_timer);
        g_stats_timer = NULL;
    }
}

lv_obj_t *ui_shell_build(void) {
    g_lock_screen = lv_screen_active();
    g_main_screen = lv_obj_create(NULL);
    lv_obj_set_style_bg_color(g_main_screen, lv_color_black(), 0);
    lv_obj_set_style_bg_opa(g_main_screen, LV_OPA_COVER, 0);
    lv_obj_set_style_pad_all(g_main_screen, 0, 0);
    lv_obj_clear_flag(g_main_screen, LV_OBJ_FLAG_SCROLLABLE);

    /* ── Header ── */
    lv_obj_t *header = lv_obj_create(g_main_screen);
    lv_obj_set_size(header, 480, HEADER_H);
    lv_obj_set_pos(header, 0, 0);
    lv_obj_set_style_bg_color(header, lv_color_hex(0x0a2a0a), 0);
    lv_obj_set_style_bg_opa(header, LV_OPA_COVER, 0);
    lv_obj_set_style_border_width(header, 0, 0);
    lv_obj_set_style_pad_all(header, 2, 0);
    lv_obj_clear_flag(header, LV_OBJ_FLAG_SCROLLABLE);

    lv_obj_t *lock_icon = make_lock_icon(header);
    lv_obj_set_pos(lock_icon, 4, 3);
    lv_obj_add_flag(lock_icon, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_set_ext_click_area(lock_icon, 12);
    lv_obj_add_event_cb(lock_icon, lock_icon_event_cb, LV_EVENT_PRESSED, NULL);

    g_clock_label = lv_label_create(header);
    lv_obj_set_style_text_color(g_clock_label, COLOR_OK, 0);
    lv_obj_align(g_clock_label, LV_ALIGN_LEFT_MID, 32, 0);

    g_date_label = make_stat_label(header, 78);
    lv_obj_set_style_text_color(g_date_label, COLOR_DIM, 0);

    g_temp_label  = make_stat_label(header, 112);
    g_ram_label   = make_stat_label(header, 145);
    g_disk_label  = make_stat_label(header, 182);
    g_load_label  = make_stat_label(header, 222);

    g_bt_label = make_stat_label(header, 258);
    lv_label_set_text(g_bt_label, LV_SYMBOL_BLUETOOTH);

    g_wlan1_label = make_stat_label(header, 280);
    lv_label_set_text(g_wlan1_label, "U");

    g_wlan0_label = make_stat_label(header, 300);
    lv_label_set_text(g_wlan0_label, LV_SYMBOL_WIFI);

    g_battery_icon_label = make_stat_label(header, 328);
    lv_label_set_text(g_battery_icon_label, LV_SYMBOL_BATTERY_FULL);
    lv_obj_set_style_text_color(g_battery_icon_label, COLOR_DIM, 0);

    g_battery_pct_label = make_stat_label(header, 350);
    lv_label_set_text(g_battery_pct_label, "--");
    lv_obj_set_style_text_color(g_battery_pct_label, COLOR_DIM, 0);

    lv_obj_t *reset_btn = lv_obj_create(header);
    lv_obj_set_size(reset_btn, 30, 20);
    lv_obj_align(reset_btn, LV_ALIGN_RIGHT_MID, -60, 0);
    lv_obj_set_style_bg_opa(reset_btn, LV_OPA_TRANSP, 0);
    lv_obj_set_style_border_width(reset_btn, 1, 0);
    lv_obj_set_style_border_color(reset_btn, COLOR_OK, 0);
    lv_obj_set_ext_click_area(reset_btn, 10);
    lv_obj_add_event_cb(reset_btn, reset_icon_event_cb, LV_EVENT_PRESSED, NULL);
    lv_obj_t *reset_lbl = lv_label_create(reset_btn);
    lv_label_set_text(reset_lbl, LV_SYMBOL_REFRESH);
    lv_obj_set_style_text_color(reset_lbl, COLOR_OK, 0);
    lv_obj_center(reset_lbl);

    lv_obj_t *power_btn = lv_obj_create(header);
    lv_obj_set_size(power_btn, 30, 20);
    lv_obj_align(power_btn, LV_ALIGN_RIGHT_MID, 0, 0);
    lv_obj_set_style_bg_opa(power_btn, LV_OPA_TRANSP, 0);
    lv_obj_set_style_border_width(power_btn, 1, 0);
    lv_obj_set_style_border_color(power_btn, COLOR_OK, 0);
    lv_obj_set_ext_click_area(power_btn, 10);
    lv_obj_add_event_cb(power_btn, power_icon_event_cb, LV_EVENT_PRESSED, NULL);
    lv_obj_t *power_lbl = lv_label_create(power_btn);
    lv_label_set_text(power_lbl, LV_SYMBOL_POWER);
    lv_obj_set_style_text_color(power_lbl, COLOR_OK, 0);
    lv_obj_center(power_lbl);

    /* ── Body ── */
    g_body = lv_obj_create(g_main_screen);
    lv_obj_set_size(g_body, 480, 320 - HEADER_H - FOOTER_H);
    lv_obj_set_pos(g_body, 0, HEADER_H);
    lv_obj_set_style_bg_color(g_body, lv_color_black(), 0);
    lv_obj_set_style_bg_opa(g_body, LV_OPA_COVER, 0);
    lv_obj_set_style_border_width(g_body, 0, 0);
    lv_obj_set_style_pad_all(g_body, 0, 0);
    lv_obj_clear_flag(g_body, LV_OBJ_FLAG_SCROLLABLE);

    /* ── Footer ── */
    lv_obj_t *footer = lv_obj_create(g_main_screen);
    lv_obj_set_size(footer, 480, FOOTER_H);
    lv_obj_set_pos(footer, 0, 320 - FOOTER_H);
    lv_obj_set_style_bg_color(footer, lv_color_hex(0x0a2a0a), 0);
    lv_obj_set_style_bg_opa(footer, LV_OPA_COVER, 0);
    lv_obj_set_style_border_width(footer, 0, 0);
    lv_obj_set_style_pad_all(footer, 4, 0);
    lv_obj_clear_flag(footer, LV_OBJ_FLAG_SCROLLABLE);

    g_footer_label = lv_label_create(footer);
    lv_obj_set_style_text_color(g_footer_label, COLOR_OK, 0);
    lv_obj_align(g_footer_label, LV_ALIGN_LEFT_MID, 0, 0);

    show_carousel();
    stats_timer_cb(NULL);
    g_stats_timer = lv_timer_create(stats_timer_cb, 3000, NULL);

    lv_screen_load(g_main_screen);
    return g_main_screen;
}

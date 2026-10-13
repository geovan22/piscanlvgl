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
#include "wifi_client.h"
#include "net_client.h"
#include "text_input.h"
#include "db_client.h"
#include "ui_style.h"
#include <stdlib.h>   /* atoi */
#include <pthread.h>
#define COLOR_OK    lv_color_hex(0x33FF33)
static void row_select_event_cb(lv_event_t *e);
static void show_capture_list(void);
static void show_wordlist_list(void);
static void audit_list_row_cb(lv_event_t *e);
#define COLOR_WARN  lv_color_hex(0xFFCC00)
#define COLOR_ERR   lv_color_hex(0xFF4444)
#define COLOR_DIM   lv_color_hex(0x2a6b2a)

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

/* ── Config: parametro deauth_duration (segundos) ── */
static lv_obj_t *g_cfg_deauth_label = NULL;
static int g_cfg_deauth_val = 0;   /* 0 = rafaga; >0 = sostenido N s */

/* ── Config: parametro beacon_duration (segundos) ── */
static lv_obj_t *g_cfg_beacon_label = NULL;
static int g_cfg_beacon_val = 20;  /* duracion del beacon flood */

/* ── WiFi: escaneo en hilo aparte (nunca tocar LVGL desde el hilo) ── */
static lv_obj_t *g_wifi_status_label = NULL;
static lv_obj_t *g_wifi_results_box = NULL;
static volatile int g_wifi_scan_running = 0;
static volatile int g_wifi_scan_done = 0;
static wifi_network_t g_wifi_scan_results[WIFI_MAX_NETWORKS];
static int g_wifi_scan_count = 0;
static char g_wifi_scan_error[128];

/* ── Monitor mode: mismo patron de hilo separado ── */
static lv_obj_t *g_scan_btn = NULL;

/* ── Objetivo seleccionado para Deauth/Handshake ── */
typedef struct {
    char ssid[64];
    char bssid[24];
    int channel;
    int has_target;
} wifi_target_t;

static wifi_target_t g_wifi_target = {0};
static lv_obj_t *g_target_label = NULL;
static lv_obj_t *g_actions_btn = NULL;   /* abre el menu de Acciones del AP seleccionado */
static lv_obj_t *g_selected_row = NULL;
static int g_list_mode = 0;   /* box de resultados: 0=scan 1=capturas 2=wordlists 3=clientes */
static wifi_capture_t g_captures[WIFI_MAX_CAPTURES];
static int g_captures_count = 0;
static wifi_wordlist_t g_wordlists[WIFI_MAX_WORDLISTS];
static int g_wordlists_count = 0;

/* ── Fase 1: enumeracion de clientes de un AP (solo escucha) ── */
static wifi_client_sta_t g_clients[WIFI_MAX_CLIENTS];
static int g_clients_count = 0;
static volatile int g_clients_running = 0;
static volatile int g_clients_done = 0;
static char g_clients_error[128];

static volatile int g_deauth_running = 0;
static volatile int g_deauth_done = 0;
static int g_deauth_ok = 0;
static char g_deauth_error[128];
static int g_deauth_duration = 0;   /* seg; 0 = rafaga. Leido de la DB al atacar */
static char g_deauth_client_mac[24] = "";  /* "" = toda la red; si no, deauth selectivo a ese cliente */

static void set_wifi_buttons_disabled(int disabled) {
    /* Con el menu de Acciones, en pantalla solo quedan Escanear y Acciones;
     * el resto de acciones vive en el overlay (que se cierra al lanzar una). */
    if (disabled) {
        if (g_scan_btn) lv_obj_add_state(g_scan_btn, LV_STATE_DISABLED);
        if (g_actions_btn) lv_obj_add_state(g_actions_btn, LV_STATE_DISABLED);
    } else {
        if (g_scan_btn) lv_obj_clear_state(g_scan_btn, LV_STATE_DISABLED);
        if (g_actions_btn) lv_obj_clear_state(g_actions_btn, LV_STATE_DISABLED);
    }
}

static void *deauth_thread_fn(void *arg) {
    (void)arg;
    char output[512];
    output[0] = '\0';
    g_deauth_ok = wifi_client_deauth(g_wifi_target.bssid, g_wifi_target.channel, 10, g_deauth_duration,
                                      g_deauth_client_mac,
                                      output, sizeof(output), g_deauth_error, sizeof(g_deauth_error));
    g_deauth_done = 1;
    return NULL;
}

void ui_shell_poll_deauth(void) {
    if (!g_deauth_done) return;
    g_deauth_done = 0;
    g_deauth_running = 0;

    if (!g_wifi_status_label) return; /* salimos de la seccion mientras corria */

    if (g_deauth_ok) {
        char okmsg[64];
        if (g_deauth_duration > 0)
            snprintf(okmsg, sizeof(okmsg), "Deauth sostenido %ds completado", g_deauth_duration);
        else
            snprintf(okmsg, sizeof(okmsg), "Deauth enviado");
        lv_label_set_text(g_wifi_status_label, okmsg);
        ui_shell_set_status(okmsg, UI_STATUS_OK);
    } else {
        char buf[160];
        snprintf(buf, sizeof(buf), "Error deauth: %s", g_deauth_error);
        lv_label_set_text(g_wifi_status_label, buf);
        ui_shell_set_status(buf, UI_STATUS_ERROR);
    }
    set_wifi_buttons_disabled(0);
}

static void on_deauth_confirm(bool confirmed, void *user_data) {
    (void)user_data;
    if (!confirmed) return;
    if (g_deauth_running) return;

    g_deauth_running = 1;
    g_deauth_done = 0;
    const char *tgt = g_deauth_client_mac[0] ? "cliente" : "red";
    char st[96];
    if (g_deauth_duration > 0)
        snprintf(st, sizeof(st), "Deauth sostenido %ds a %s...", g_deauth_duration, tgt);
    else
        snprintf(st, sizeof(st), "Deauth (rafaga) a %s...", tgt);
    if (g_wifi_status_label) lv_label_set_text(g_wifi_status_label, st);
    ui_shell_set_status(st, UI_STATUS_WORKING);
    set_wifi_buttons_disabled(1);

    pthread_t tid;
    pthread_create(&tid, NULL, deauth_thread_fn, NULL);
    pthread_detach(tid);
}

static void trigger_deauth(void) {
    if (!g_wifi_target.has_target) {
        if (g_wifi_status_label) lv_label_set_text(g_wifi_status_label, "Selecciona una red de la lista primero");
        return;
    }
    if (g_deauth_running) return;

    g_deauth_client_mac[0] = '\0';   /* deauth a TODA la red (dirigido a todos los clientes) */

    /* Duracion configurada en la DB (seccion Config -> deauth_duration).
     * 0 = rafaga; >0 = sostenido N s. Se lee fresca en cada ataque para
     * reflejar lo ultimo guardado aunque no se haya abierto Config. */
    char dur[32];
    g_deauth_duration = db_config_get("deauth_duration", dur, sizeof(dur)) ? atoi(dur) : 0;
    if (g_deauth_duration < 0) g_deauth_duration = 0;
    if (g_deauth_duration > 600) g_deauth_duration = 600;

    char msg[128];
    if (g_deauth_duration > 0)
        snprintf(msg, sizeof(msg), "Deauth sostenido %ds a %s?", g_deauth_duration, g_wifi_target.ssid);
    else
        snprintf(msg, sizeof(msg), "Deauth (rafaga) a %s?", g_wifi_target.ssid);
    confirm_dialog_show(g_main_screen, msg, on_deauth_confirm, NULL);
}

/* ── Captura de Handshake: mismo patron de hilo separado ── */
static volatile int g_handshake_running = 0;
static volatile int g_handshake_done = 0;
static int g_handshake_ok = 0;
static char g_handshake_cap_file[256];
static char g_handshake_detail[256];

static void *handshake_thread_fn(void *arg) {
    (void)arg;
    g_handshake_ok = wifi_client_handshake(g_wifi_target.bssid, g_wifi_target.channel, 25, 5,
                                            g_handshake_cap_file, sizeof(g_handshake_cap_file),
                                            g_handshake_detail, sizeof(g_handshake_detail));
    g_handshake_done = 1;
    return NULL;
}

void ui_shell_poll_handshake(void) {
    if (!g_handshake_done) return;
    g_handshake_done = 0;
    g_handshake_running = 0;

    if (!g_wifi_status_label) return; /* salimos de la seccion mientras corria */

    if (g_handshake_ok) {
        lv_label_set_text(g_wifi_status_label, "Handshake capturado!");
        ui_shell_set_status("Handshake capturado", UI_STATUS_OK);
    } else {
        lv_label_set_text(g_wifi_status_label, "Sin handshake (reintenta)");
        ui_shell_set_status("Sin handshake, reintenta", UI_STATUS_ERROR);
    }
    set_wifi_buttons_disabled(0);
}

/* ── PMKID/EAPOL (clientless): hcxdumptool captura del AP sin esperar un
 *    cliente. Mismo patron hilo + poll que el handshake. ── */
static volatile int g_pmkid_running = 0;
static volatile int g_pmkid_done = 0;
static int g_pmkid_ok = 0;              /* 1=capturo, 0=nada, -1=error */
static char g_pmkid_hc[256];
static char g_pmkid_detail[256];

static void *pmkid_thread_fn(void *arg) {
    (void)arg;
    g_pmkid_hc[0] = '\0';
    g_pmkid_detail[0] = '\0';
    g_pmkid_ok = wifi_client_pmkid(g_wifi_target.bssid, g_wifi_target.channel, 30,
                                   g_pmkid_hc, sizeof(g_pmkid_hc),
                                   g_pmkid_detail, sizeof(g_pmkid_detail));
    g_pmkid_done = 1;
    return NULL;
}

void ui_shell_poll_pmkid(void) {
    if (!g_pmkid_done) return;
    g_pmkid_done = 0;
    g_pmkid_running = 0;

    if (!g_wifi_status_label) return; /* salimos de la seccion mientras corria */

    if (g_pmkid_ok == 1) {
        lv_label_set_text(g_wifi_status_label, "PMKID/EAPOL capturado!");
        ui_shell_set_status("PMKID/EAPOL capturado (.hc22000)", UI_STATUS_OK);
    } else if (g_pmkid_ok == 0) {
        lv_label_set_text(g_wifi_status_label, "Sin PMKID/EAPOL (reintenta)");
        ui_shell_set_status("Sin PMKID/EAPOL, reintenta", UI_STATUS_ERROR);
    } else {
        lv_label_set_text(g_wifi_status_label, "Error en PMKID");
        ui_shell_set_status("Error en PMKID (ver log)", UI_STATUS_ERROR);
    }
    set_wifi_buttons_disabled(0);
}

static void on_pmkid_confirm(bool confirmed, void *user_data) {
    (void)user_data;
    if (!confirmed) return;
    if (g_pmkid_running) return;

    g_pmkid_running = 1;
    g_pmkid_done = 0;
    if (g_wifi_status_label) lv_label_set_text(g_wifi_status_label, "Capturando PMKID/EAPOL (~35s)...");
    ui_shell_set_status("Capturando PMKID/EAPOL (~35s)...", UI_STATUS_WORKING);
    set_wifi_buttons_disabled(1);

    pthread_t tid;
    pthread_create(&tid, NULL, pmkid_thread_fn, NULL);
    pthread_detach(tid);
}

/* ── Beacon flood (mdk4): inunda APs falsos en el canal del AP seleccionado.
 *    Demo/ruido, SOLO lab. Mismo patron hilo + poll. ── */
static volatile int g_beacon_running = 0;
static volatile int g_beacon_done = 0;
static int g_beacon_ok = 0;
static int g_beacon_channel = 6;
static int g_beacon_seconds = 20;   /* leido de la DB (beacon_duration) al atacar */
static char g_beacon_detail[256];

static void *beacon_thread_fn(void *arg) {
    (void)arg;
    g_beacon_detail[0] = '\0';
    g_beacon_ok = wifi_client_beacon(g_beacon_channel, g_beacon_seconds,
                                     g_beacon_detail, sizeof(g_beacon_detail));
    g_beacon_done = 1;
    return NULL;
}

void ui_shell_poll_beacon(void) {
    if (!g_beacon_done) return;
    g_beacon_done = 0;
    g_beacon_running = 0;
    if (!g_wifi_status_label) return;
    if (g_beacon_ok) {
        lv_label_set_text(g_wifi_status_label, "Beacon flood terminado");
        ui_shell_set_status("Beacon flood terminado", UI_STATUS_OK);
    } else {
        lv_label_set_text(g_wifi_status_label, "Error en beacon flood");
        ui_shell_set_status("Error en beacon flood (ver log)", UI_STATUS_ERROR);
    }
    set_wifi_buttons_disabled(0);
}

static void on_beacon_confirm(bool confirmed, void *user_data) {
    (void)user_data;
    if (!confirmed) return;
    if (g_beacon_running) return;
    g_beacon_channel = g_wifi_target.channel;
    g_beacon_running = 1;
    g_beacon_done = 0;
    char wm[48];
    snprintf(wm, sizeof(wm), "Beacon flood (~%ds)...", g_beacon_seconds);
    if (g_wifi_status_label) lv_label_set_text(g_wifi_status_label, wm);
    ui_shell_set_status(wm, UI_STATUS_WORKING);
    set_wifi_buttons_disabled(1);
    pthread_t tid;
    pthread_create(&tid, NULL, beacon_thread_fn, NULL);
    pthread_detach(tid);
}

/* ── Evil Twin (AP falso + portal cautivo que captura la clave). SOLO lab.
 *    Operacion larga (hasta ET_SECONDS esperando que la victima ingrese la
 *    clave en el portal). Mismo patron hilo + poll. ── */
#define ET_SECONDS 180
static volatile int g_et_running = 0;
static volatile int g_et_done = 0;
static int g_et_ok = 0;                 /* 1=capturo, 0=no, -1=error */
static char g_et_ssid[64];
static char g_et_bssid[24];
static int g_et_channel = 6;
static char g_et_password[128];
static char g_et_detail[160];

static void *eviltwin_thread_fn(void *arg) {
    (void)arg;
    g_et_password[0] = '\0';
    g_et_detail[0] = '\0';
    g_et_ok = wifi_client_evil_twin(g_et_ssid, g_et_bssid, g_et_channel, ET_SECONDS,
                                    g_et_password, sizeof(g_et_password),
                                    g_et_detail, sizeof(g_et_detail));
    g_et_done = 1;
    return NULL;
}

void ui_shell_poll_eviltwin(void) {
    if (!g_et_done) return;
    g_et_done = 0;
    g_et_running = 0;
    if (!g_wifi_status_label) return;
    if (g_et_ok == 1) {
        char buf[200];
        snprintf(buf, sizeof(buf), "Clave capturada: %s", g_et_password);
        lv_label_set_text(g_wifi_status_label, buf);
        ui_shell_set_status(buf, UI_STATUS_ERROR);   /* rojo: red comprometida */
    } else if (g_et_ok == 0) {
        lv_label_set_text(g_wifi_status_label, "Evil Twin: sin captura");
        ui_shell_set_status("Evil Twin: sin captura", UI_STATUS_OK);
    } else {
        lv_label_set_text(g_wifi_status_label, "Error en Evil Twin");
        ui_shell_set_status("Error en Evil Twin (ver log)", UI_STATUS_ERROR);
    }
    set_wifi_buttons_disabled(0);
}

static void on_eviltwin_confirm(bool confirmed, void *user_data) {
    (void)user_data;
    if (!confirmed) return;
    if (g_et_running) return;
    snprintf(g_et_ssid, sizeof(g_et_ssid), "%s", g_wifi_target.ssid);
    snprintf(g_et_bssid, sizeof(g_et_bssid), "%s", g_wifi_target.bssid);
    g_et_channel = g_wifi_target.channel;
    g_et_running = 1;
    g_et_done = 0;
    char em[128];
    snprintf(em, sizeof(em), "Evil Twin '%s' activo: conecta al AP ABIERTO y abri el portal (~3min)",
             g_wifi_target.ssid);
    if (g_wifi_status_label) lv_label_set_text(g_wifi_status_label, em);
    ui_shell_set_status(em, UI_STATUS_WORKING);
    set_wifi_buttons_disabled(1);
    pthread_t tid;
    pthread_create(&tid, NULL, eviltwin_thread_fn, NULL);
    pthread_detach(tid);
}

/* ── Auditoria de handshake: corre una wordlist contra un .cap elegido.
 *    Mismo patron de hilo separado + poll desde el loop principal. ── */
static volatile int g_audit_running = 0;
static volatile int g_audit_done = 0;
static int g_audit_result = 0;               /* 1=debil(found) 0=fuerte -1=error */
static char g_audit_cap_file[256] = {0};     /* .cap elegido */
static char g_audit_bssid[24] = {0};         /* bssid del .cap */
static char g_audit_ssid[64] = {0};          /* nombre de red, para el mensaje */
static char g_audit_wordlist[64] = "common"; /* wordlist elegida (nombre) */
static char g_audit_password[128] = {0};     /* contrasena encontrada, si aplica */
static char g_audit_error[128] = {0};

static void *audit_thread_fn(void *arg) {
    (void)arg;
    g_audit_password[0] = '\0';
    g_audit_error[0] = '\0';
    /* timeout amplio: la lista chica termina rapido; la grande se corta sola.
     * 600s = 10 min tope (en el Pi under-volted igual conviene lista chica). */
    g_audit_result = wifi_client_audit(g_audit_cap_file, g_audit_bssid,
                                       g_audit_wordlist, 600,
                                       g_audit_password, sizeof(g_audit_password),
                                       g_audit_error, sizeof(g_audit_error));
    g_audit_done = 1;
    return NULL;
}

void ui_shell_poll_audit(void) {
    if (!g_audit_done) return;
    g_audit_done = 0;
    g_audit_running = 0;

    if (!g_wifi_status_label) return; /* salimos de la seccion mientras corria */

    if (g_audit_result == 1) {
        char buf[160];
        snprintf(buf, sizeof(buf), "Debil! %s: %s", g_audit_ssid, g_audit_password);
        ui_shell_set_status(buf, UI_STATUS_ERROR);   /* rojo: red vulnerable */
    } else if (g_audit_result == 0) {
        char buf[160];
        snprintf(buf, sizeof(buf), "Fuerte: %s no esta en la lista", g_audit_ssid);
        ui_shell_set_status(buf, UI_STATUS_OK);       /* verde: no vulnerable a esa lista */
    } else {
        char buf[160];
        snprintf(buf, sizeof(buf), "Error auditando: %s", g_audit_error);
        ui_shell_set_status(buf, UI_STATUS_ERROR);
    }
    set_wifi_buttons_disabled(0);
}

static void on_handshake_confirm(bool confirmed, void *user_data) {
    (void)user_data;
    if (!confirmed) return;
    if (g_handshake_running) return;

    g_handshake_running = 1;
    g_handshake_done = 0;
    if (g_wifi_status_label) lv_label_set_text(g_wifi_status_label, "Capturando handshake (~30s)...");
    ui_shell_set_status("Capturando handshake (~30s)...", UI_STATUS_WORKING);
    set_wifi_buttons_disabled(1);

    pthread_t tid;
    pthread_create(&tid, NULL, handshake_thread_fn, NULL);
    pthread_detach(tid);
}

/* ── Auditoria: lista de capturas y de wordlists en el box de resultados.
 *    g_list_mode indica que se esta mostrando (1=capturas, 2=wordlists) y
 *    el callback unificado audit_list_row_cb actua segun ese modo. Las
 *    filas se crean SIN ui_apply_press_effect (sobre lv_obj_create cuelga
 *    el recalculo de layout); el resaltado de seleccion da el feedback. ── */
static void audit_start_selected(void) {
    if (g_audit_running) return;
    g_audit_running = 1;
    g_audit_done = 0;
    char sb[96];
    snprintf(sb, sizeof(sb), "Auditando %.20s (%s)...", g_audit_ssid, g_audit_wordlist);
    ui_shell_set_status(sb, UI_STATUS_WORKING);
    set_wifi_buttons_disabled(1);
    g_list_mode = 0;
    pthread_t tid;
    pthread_create(&tid, NULL, audit_thread_fn, NULL);
    pthread_detach(tid);
}

static lv_obj_t *make_list_row(int idx) {
    lv_obj_t *row = lv_obj_create(g_wifi_results_box);
    lv_obj_set_size(row, 380, 24);
    lv_obj_set_style_bg_opa(row, LV_OPA_TRANSP, 0);
    lv_obj_set_style_border_width(row, 0, 0);
    lv_obj_set_style_pad_all(row, 0, 0);
    lv_obj_clear_flag(row, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_add_flag(row, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_set_ext_click_area(row, 12);
    lv_obj_set_user_data(row, (void *)(intptr_t)idx);
    lv_obj_add_event_cb(row, audit_list_row_cb, LV_EVENT_PRESSED, NULL);
    return row;
}

static lv_obj_t *make_row_label(lv_obj_t *row, const char *text, lv_color_t color, int x) {
    lv_obj_t *l = lv_label_create(row);
    lv_label_set_text(l, text);
    lv_obj_set_style_text_color(l, color, 0);
    lv_obj_set_style_text_font(l, &lv_font_montserrat_10, 0);
    lv_obj_set_pos(l, x, 0);
    return l;
}

static volatile int g_caplist_running = 0;
static volatile int g_caplist_done = 0;

/* Renderiza las filas de la lista de capturas (ya cargadas en g_captures).
 * Se llama desde el poll, en el hilo principal (seguro para LVGL). */
static void render_capture_list(void) {
    if (!g_wifi_results_box || !g_wifi_status_label) return;
    lv_obj_clean(g_wifi_results_box);
    lv_obj_scroll_to_y(g_wifi_results_box, 0, LV_ANIM_OFF);  /* lista nueva arranca arriba */
    g_selected_row = NULL;
    g_list_mode = 1;
    if (g_captures_count < 0) {
        g_captures_count = 0;
        ui_shell_set_status("Error listando capturas", UI_STATUS_ERROR);
        return;
    }
    if (g_captures_count == 0) {
        lv_label_set_text(g_wifi_status_label, "No hay capturas (captura un handshake primero)");
        ui_shell_set_status("Sin capturas para auditar", UI_STATUS_INFO);
        return;
    }
    lv_label_set_text(g_wifi_status_label, "Capturas en data/captures (toca=auditar; scp->hashcat)");
    ui_shell_set_status("Capturas: toca una para auditar", UI_STATUS_INFO);
    for (int i = 0; i < g_captures_count; i++) {
        lv_obj_t *row = make_list_row(i);
        /* SSID */
        char sbuf[20];
        snprintf(sbuf, sizeof(sbuf), "%.16s", g_captures[i].ssid);
        make_row_label(row, sbuf, COLOR_OK, 0);
        /* BSSID */
        make_row_label(row, g_captures[i].bssid, COLOR_DIM, 115);
        /* Tipo: PMKID (morado) / HS (verde) / -- (rojo) */
        int is_pmkid = (strstr(g_captures[i].file, "pmkid_") != NULL);
        const char *tag = is_pmkid ? "PMKID" : (g_captures[i].has_handshake ? "HS" : "--");
        lv_color_t tagc = is_pmkid ? lv_color_hex(0xCC66FF)
                                   : (g_captures[i].has_handshake ? COLOR_OK : COLOR_ERR);
        make_row_label(row, tag, tagc, 250);
        /* Tamano en KB */
        char zbuf[16];
        snprintf(zbuf, sizeof(zbuf), "%ldKB", g_captures[i].size / 1024);
        make_row_label(row, zbuf, COLOR_DIM, 315);
    }
}

/* El listado hace fork de Python + consultas a la DB (~segundos en el Pi
 * under-volted); corre en un hilo para no congelar la UI. */
static void *caplist_thread_fn(void *arg) {
    (void)arg;
    char err[128];
    g_captures_count = wifi_client_list_captures(g_captures, WIFI_MAX_CAPTURES, err, sizeof(err));
    g_caplist_done = 1;
    return NULL;
}

void ui_shell_poll_caplist(void) {
    if (!g_caplist_done) return;
    g_caplist_done = 0;
    g_caplist_running = 0;
    render_capture_list();
}

static void show_capture_list(void) {
    if (!g_wifi_results_box || !g_wifi_status_label) return;
    if (g_caplist_running) return;
    g_caplist_running = 1;
    g_caplist_done = 0;
    g_list_mode = 1;
    lv_obj_clean(g_wifi_results_box);
    g_selected_row = NULL;
    lv_label_set_text(g_wifi_status_label, "Listando capturas...");
    ui_shell_set_status("Listando capturas...", UI_STATUS_WORKING);
    pthread_t tid;
    pthread_create(&tid, NULL, caplist_thread_fn, NULL);
    pthread_detach(tid);
}
/* ── Fase 1: clientes de un AP (solo escucha) ─────────────────────────
 * airodump filtrado por bssid unos segundos lista las estaciones
 * asociadas. Hilo + poll como el resto (operacion de varios segundos).
 * Filas informativas (sin accion); el deauth selectivo a un cliente es el
 * siguiente item de la fase. Las filas son lv_obj_create SIN press-effect
 * (regla §2). */
/* Tap en una fila de cliente -> deauth SELECTIVO a ese cliente (con confirm).
 * Reutiliza el mismo hilo/confirm que el deauth de red; solo fija el MAC
 * objetivo (g_deauth_client_mac) y la duracion desde la DB. */
static void client_row_cb(lv_event_t *e) {
    if (lv_event_get_code(e) != LV_EVENT_PRESSED) return;
    if (g_deauth_running || g_clients_running) return;
    lv_obj_t *row = lv_event_get_target_obj(e);
    int idx = (int)(intptr_t)lv_obj_get_user_data(row);
    if (idx < 0 || idx >= g_clients_count) return;

    if (g_selected_row) lv_obj_set_style_bg_opa(g_selected_row, LV_OPA_TRANSP, 0);
    lv_obj_set_style_bg_color(row, lv_color_hex(0x1a5c1a), 0);
    lv_obj_set_style_bg_opa(row, LV_OPA_COVER, 0);
    g_selected_row = row;

    snprintf(g_deauth_client_mac, sizeof(g_deauth_client_mac), "%s", g_clients[idx].mac);

    char dur[32];
    g_deauth_duration = db_config_get("deauth_duration", dur, sizeof(dur)) ? atoi(dur) : 0;
    if (g_deauth_duration < 0) g_deauth_duration = 0;
    if (g_deauth_duration > 600) g_deauth_duration = 600;

    char msg[160];
    if (g_deauth_duration > 0)
        snprintf(msg, sizeof(msg), "Deauth sostenido %ds al cliente %s?", g_deauth_duration, g_deauth_client_mac);
    else
        snprintf(msg, sizeof(msg), "Deauth (rafaga) al cliente %s?", g_deauth_client_mac);
    confirm_dialog_show(g_main_screen, msg, on_deauth_confirm, NULL);
}

static void render_client_list(void) {
    if (!g_wifi_results_box || !g_wifi_status_label) return;
    lv_obj_clean(g_wifi_results_box);
    lv_obj_scroll_to_y(g_wifi_results_box, 0, LV_ANIM_OFF);  /* lista nueva arranca arriba */
    g_selected_row = NULL;
    g_list_mode = 3;
    if (g_clients_count < 0) {
        g_clients_count = 0;
        char buf[160];
        snprintf(buf, sizeof(buf), "Error: %s", g_clients_error);
        lv_label_set_text(g_wifi_status_label, buf);
        ui_shell_set_status(buf, UI_STATUS_ERROR);
        return;
    }
    if (g_clients_count == 0) {
        lv_label_set_text(g_wifi_status_label, "Sin clientes asociados (o ninguno activo)");
        ui_shell_set_status("Sin clientes", UI_STATUS_INFO);
        return;
    }
    char hdr[80];
    snprintf(hdr, sizeof(hdr), "%d cliente(s) - toca uno para deauth", g_clients_count);
    lv_label_set_text(g_wifi_status_label, hdr);
    ui_shell_set_status(hdr, UI_STATUS_OK);
    for (int i = 0; i < g_clients_count; i++) {
        lv_obj_t *row = lv_obj_create(g_wifi_results_box);
        lv_obj_set_size(row, 380, 24);
        lv_obj_set_style_bg_opa(row, LV_OPA_TRANSP, 0);
        lv_obj_set_style_border_width(row, 0, 0);
        lv_obj_set_style_pad_all(row, 0, 0);
        lv_obj_clear_flag(row, LV_OBJ_FLAG_SCROLLABLE);
        lv_obj_add_flag(row, LV_OBJ_FLAG_CLICKABLE);
        lv_obj_set_ext_click_area(row, 10);
        lv_obj_set_user_data(row, (void *)(intptr_t)i);
        lv_obj_add_event_cb(row, client_row_cb, LV_EVENT_PRESSED, NULL);
        make_row_label(row, g_clients[i].mac, COLOR_OK, 0);
        char vbuf[20];
        snprintf(vbuf, sizeof(vbuf), "%.12s", g_clients[i].vendor);
        make_row_label(row, vbuf, lv_color_hex(0x33CCFF), 115);
        make_row_label(row, g_clients[i].power, COLOR_DIM, 250);
        char kbuf[16];
        snprintf(kbuf, sizeof(kbuf), "%sp", g_clients[i].packets);
        make_row_label(row, kbuf, COLOR_DIM, 300);
    }
}

static void *clients_thread_fn(void *arg) {
    (void)arg;
    g_clients_error[0] = '\0';
    g_clients_count = wifi_client_scan_clients(g_wifi_target.bssid, g_wifi_target.channel,
                                               g_clients, WIFI_MAX_CLIENTS,
                                               g_clients_error, sizeof(g_clients_error));
    g_clients_done = 1;
    return NULL;
}

void ui_shell_poll_clients(void) {
    if (!g_clients_done) return;
    g_clients_done = 0;
    g_clients_running = 0;
    set_wifi_buttons_disabled(0);
    render_client_list();
}

static void trigger_clients(void) {
    if (!g_wifi_target.has_target) {
        if (g_wifi_status_label)
            lv_label_set_text(g_wifi_status_label, "Selecciona una red de la lista primero");
        return;
    }
    if (g_clients_running) return;
    g_clients_running = 1;
    g_clients_done = 0;
    g_list_mode = 3;
    if (g_wifi_results_box) lv_obj_clean(g_wifi_results_box);
    g_selected_row = NULL;
    if (g_wifi_status_label) lv_label_set_text(g_wifi_status_label, "Buscando clientes...");
    ui_shell_set_status("Buscando clientes (~15s)...", UI_STATUS_WORKING);
    set_wifi_buttons_disabled(1);
    pthread_t tid;
    pthread_create(&tid, NULL, clients_thread_fn, NULL);
    pthread_detach(tid);
}

static void show_wordlist_list(void) {
    if (!g_wifi_results_box || !g_wifi_status_label) return;
    char err[128];
    g_wordlists_count = wifi_client_list_wordlists(g_wordlists, WIFI_MAX_WORDLISTS, err, sizeof(err));
    lv_obj_clean(g_wifi_results_box);
    lv_obj_scroll_to_y(g_wifi_results_box, 0, LV_ANIM_OFF);  /* lista nueva arranca arriba */
    g_selected_row = NULL;
    g_list_mode = 2;
    if (g_wordlists_count <= 0) {
        g_wordlists_count = 0;
        ui_shell_set_status("No hay wordlists en data/wordlists", UI_STATUS_ERROR);
        return;
    }
    char sb[64];
    snprintf(sb, sizeof(sb), "Wordlist para %.16s:", g_audit_ssid);
    lv_label_set_text(g_wifi_status_label, sb);
    ui_shell_set_status("Elegi una wordlist", UI_STATUS_INFO);
    for (int i = 0; i < g_wordlists_count; i++) {
        lv_obj_t *row = make_list_row(i);
        char nbuf[28];
        snprintf(nbuf, sizeof(nbuf), "%.24s", g_wordlists[i].name);
        make_row_label(row, nbuf, COLOR_OK, 0);
        char szbuf[24];
        if (g_wordlists[i].size_mb < 1.0f)
            snprintf(szbuf, sizeof(szbuf), "%.0f KB", g_wordlists[i].size_mb * 1024.0f);
        else
            snprintf(szbuf, sizeof(szbuf), "%.1f MB", g_wordlists[i].size_mb);
        make_row_label(row, szbuf, COLOR_DIM, 210);
        make_row_label(row, g_wordlists[i].fast ? "rapida" : "LENTA",
                       g_wordlists[i].fast ? COLOR_OK : COLOR_WARN, 310);
    }
}

static void audit_list_row_cb(lv_event_t *e) {
    if (lv_event_get_code(e) != LV_EVENT_PRESSED) return;
    lv_obj_t *row = lv_event_get_target_obj(e);
    int idx = (int)(intptr_t)lv_obj_get_user_data(row);

    if (g_selected_row) lv_obj_set_style_bg_opa(g_selected_row, LV_OPA_TRANSP, 0);
    lv_obj_set_style_bg_color(row, lv_color_hex(0x1a5c1a), 0);
    lv_obj_set_style_bg_opa(row, LV_OPA_COVER, 0);
    g_selected_row = row;

    if (g_list_mode == 1) {
        if (idx < 0 || idx >= g_captures_count) return;
        snprintf(g_audit_cap_file, sizeof(g_audit_cap_file), "%s", g_captures[idx].file);
        snprintf(g_audit_bssid, sizeof(g_audit_bssid), "%s", g_captures[idx].bssid);
        snprintf(g_audit_ssid, sizeof(g_audit_ssid), "%s", g_captures[idx].ssid);
        show_wordlist_list();   /* pasa a elegir wordlist */
    } else if (g_list_mode == 2) {
        if (idx < 0 || idx >= g_wordlists_count) return;
        snprintf(g_audit_wordlist, sizeof(g_audit_wordlist), "%s", g_wordlists[idx].key);
        audit_start_selected();  /* lanza la auditoria en el hilo */
    }
}

static void trigger_audit(void) {
    show_capture_list();
}

static void trigger_handshake(void) {
    if (!g_wifi_target.has_target) {
        if (g_wifi_status_label) lv_label_set_text(g_wifi_status_label, "Selecciona una red de la lista primero");
        return;
    }
    if (g_handshake_running) return;

    char msg[96];
    snprintf(msg, sizeof(msg), "Capturar handshake de %s?", g_wifi_target.ssid);
    confirm_dialog_show(g_main_screen, msg, on_handshake_confirm, NULL);
}

static void trigger_pmkid(void) {
    if (!g_wifi_target.has_target) {
        if (g_wifi_status_label) lv_label_set_text(g_wifi_status_label, "Selecciona una red de la lista primero");
        return;
    }
    if (g_pmkid_running) return;

    char msg[96];
    snprintf(msg, sizeof(msg), "Capturar PMKID/EAPOL de %s?", g_wifi_target.ssid);
    confirm_dialog_show(g_main_screen, msg, on_pmkid_confirm, NULL);
}

static void trigger_beacon(void) {
    if (!g_wifi_target.has_target) {
        if (g_wifi_status_label) lv_label_set_text(g_wifi_status_label, "Selecciona una red de la lista primero");
        return;
    }
    if (g_beacon_running) return;

    /* Duracion fresca de la DB (Config -> beacon_duration). Default 20. */
    char dur[32];
    g_beacon_seconds = db_config_get("beacon_duration", dur, sizeof(dur)) ? atoi(dur) : 20;
    if (g_beacon_seconds < 5) g_beacon_seconds = 5;
    if (g_beacon_seconds > 300) g_beacon_seconds = 300;

    char msg[110];
    snprintf(msg, sizeof(msg), "Beacon flood en canal %d (~%ds, solo lab)?",
             g_wifi_target.channel, g_beacon_seconds);
    confirm_dialog_show(g_main_screen, msg, on_beacon_confirm, NULL);
}

static void trigger_eviltwin(void) {
    if (!g_wifi_target.has_target) {
        if (g_wifi_status_label) lv_label_set_text(g_wifi_status_label, "Selecciona una red de la lista primero");
        return;
    }
    if (g_et_running) return;

    char msg[140];
    snprintf(msg, sizeof(msg),
             "Evil Twin de %s?\nDeauth + AP falso + portal (solo lab)",
             g_wifi_target.ssid);
    confirm_dialog_show(g_main_screen, msg, on_eviltwin_confirm, NULL);
}

/* ── Menu de Acciones (overlay) ──────────────────────────────────────────
 * Un solo boton "Acciones" abre esta lista con todo lo que se le puede hacer
 * al AP seleccionado. Escala sin llenar la pantalla de botones. Cada accion
 * cierra el overlay y lanza su trigger_*() (que a su vez muestra su confirm o
 * arranca su hilo). El overlay es modal (cubre la pantalla), asi que la unica
 * salida es por sus propios botones -> no queda colgado al navegar. ── */
static lv_obj_t *g_actions_overlay = NULL;

static void close_actions_menu(void) {
    if (g_actions_overlay) {
        lv_obj_del(g_actions_overlay);
        g_actions_overlay = NULL;
    }
}

static void act_clientes_cb(lv_event_t *e)  { if (lv_event_get_code(e)!=LV_EVENT_PRESSED) return; close_actions_menu(); trigger_clients(); }
static void act_deauth_cb(lv_event_t *e)    { if (lv_event_get_code(e)!=LV_EVENT_PRESSED) return; close_actions_menu(); trigger_deauth(); }
static void act_handshake_cb(lv_event_t *e) { if (lv_event_get_code(e)!=LV_EVENT_PRESSED) return; close_actions_menu(); trigger_handshake(); }
static void act_pmkid_cb(lv_event_t *e)     { if (lv_event_get_code(e)!=LV_EVENT_PRESSED) return; close_actions_menu(); trigger_pmkid(); }
static void act_beacon_cb(lv_event_t *e)    { if (lv_event_get_code(e)!=LV_EVENT_PRESSED) return; close_actions_menu(); trigger_beacon(); }
static void act_eviltwin_cb(lv_event_t *e)  { if (lv_event_get_code(e)!=LV_EVENT_PRESSED) return; close_actions_menu(); trigger_eviltwin(); }
static void act_cancel_cb(lv_event_t *e)    { if (lv_event_get_code(e)!=LV_EVENT_PRESSED) return; close_actions_menu(); }

/* Boton "Capturas" (fila superior, sin necesitar red seleccionada): abre la
 * lista de capturas (handshakes + PMKID) para auditar on-device o ver el
 * archivo a sacar por scp. */
static void capturas_btn_event_cb(lv_event_t *e) {
    if (lv_event_get_code(e) != LV_EVENT_PRESSED) return;
    trigger_audit();
}

static void make_action_btn(lv_obj_t *parent, const char *text, uint32_t color, int y, lv_event_cb_t cb) {
    lv_obj_t *b = lv_button_create(parent);
    lv_obj_set_size(b, 284, 34);
    lv_obj_align(b, LV_ALIGN_TOP_MID, 0, y);
    lv_obj_set_style_bg_color(b, lv_color_hex(0x0a1a0a), 0);
    lv_obj_set_style_border_color(b, lv_color_hex(color), 0);
    lv_obj_set_style_border_width(b, 2, 0);
    lv_obj_set_ext_click_area(b, 4);
    lv_obj_add_event_cb(b, cb, LV_EVENT_PRESSED, NULL);
    ui_apply_press_effect(b);
    lv_obj_t *l = lv_label_create(b);
    lv_label_set_text(l, text);
    lv_obj_set_style_text_color(l, lv_color_hex(color), 0);
    lv_obj_set_style_text_font(l, &lv_font_montserrat_10, 0);
    lv_obj_center(l);
}

static void wifi_actions_menu_show(void) {
    if (g_actions_overlay) return;

    g_actions_overlay = lv_obj_create(g_main_screen);
    lv_obj_set_size(g_actions_overlay, 480, 320);
    lv_obj_set_pos(g_actions_overlay, 0, 0);
    lv_obj_set_style_bg_color(g_actions_overlay, lv_color_black(), 0);
    lv_obj_set_style_bg_opa(g_actions_overlay, LV_OPA_80, 0);
    lv_obj_set_style_border_width(g_actions_overlay, 0, 0);
    lv_obj_set_style_pad_all(g_actions_overlay, 0, 0);
    lv_obj_clear_flag(g_actions_overlay, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_add_flag(g_actions_overlay, LV_OBJ_FLAG_CLICKABLE);

    lv_obj_t *box = lv_obj_create(g_actions_overlay);
    lv_obj_set_size(box, 300, 290);
    lv_obj_center(box);
    lv_obj_set_style_bg_color(box, lv_color_hex(0x071007), 0);
    lv_obj_set_style_bg_opa(box, LV_OPA_COVER, 0);
    lv_obj_set_style_border_color(box, lv_color_hex(0x33FF33), 0);
    lv_obj_set_style_border_width(box, 2, 0);
    lv_obj_set_style_pad_all(box, 6, 0);
    lv_obj_clear_flag(box, LV_OBJ_FLAG_SCROLLABLE);

    lv_obj_t *title = lv_label_create(box);
    char tb[80];
    snprintf(tb, sizeof(tb), "Acciones: %.20s", g_wifi_target.ssid);
    lv_label_set_text(title, tb);
    lv_obj_set_style_text_color(title, lv_color_hex(0x33FF33), 0);
    lv_obj_set_style_text_font(title, &lv_font_montserrat_10, 0);
    lv_obj_align(title, LV_ALIGN_TOP_MID, 0, 2);

    int y = 20;
    make_action_btn(box, "Clientes",      0x33CCFF, y, act_clientes_cb);  y += 36;
    make_action_btn(box, "Deauth red",    0xFF4444, y, act_deauth_cb);    y += 36;
    make_action_btn(box, "Handshake",     0xFFCC33, y, act_handshake_cb); y += 36;
    make_action_btn(box, "PMKID / EAPOL", 0xCC66FF, y, act_pmkid_cb);     y += 36;
    make_action_btn(box, "Beacon flood",  0xFF9933, y, act_beacon_cb);    y += 36;
    make_action_btn(box, "Evil Twin",     0xFF3366, y, act_eviltwin_cb);  y += 36;
    make_action_btn(box, "Cancelar",      0x33FF33, y, act_cancel_cb);
}

static void actions_btn_event_cb(lv_event_t *e) {
    if (lv_event_get_code(e) != LV_EVENT_PRESSED) return;
    if (!g_wifi_target.has_target) {
        if (g_wifi_status_label) lv_label_set_text(g_wifi_status_label, "Selecciona una red de la lista primero");
        return;
    }
    wifi_actions_menu_show();
}

static void *wifi_scan_thread_fn(void *arg) {
    (void)arg;
    g_wifi_scan_count = wifi_client_scan(g_wifi_scan_results, WIFI_MAX_NETWORKS,
                                          g_wifi_scan_error, sizeof(g_wifi_scan_error));
    g_wifi_scan_done = 1;
    return NULL;
}

static void start_wifi_scan(void) {
    if (g_wifi_scan_running) return;
    g_wifi_scan_running = 1;
    g_wifi_scan_done = 0;
    if (g_wifi_status_label) lv_label_set_text(g_wifi_status_label, "Escaneando...");
    ui_shell_set_status("Escaneando redes...", UI_STATUS_WORKING);

    pthread_t tid;
    pthread_create(&tid, NULL, wifi_scan_thread_fn, NULL);
    pthread_detach(tid);
}

/* Llamado desde el loop principal (main.c) cada iteracion. Aplica
 * resultados SOLO si los labels siguen vivos (el usuario no navego
 * afuera de la seccion WiFi mientras escaneaba). */
void ui_shell_poll_wifi_scan(void) {
    if (!g_wifi_scan_done) return;
    g_wifi_scan_done = 0;
    g_wifi_scan_running = 0;

    if (!g_wifi_status_label || !g_wifi_results_box) return;

    lv_obj_clean(g_wifi_results_box);
    lv_obj_scroll_to_y(g_wifi_results_box, 0, LV_ANIM_OFF);  /* lista nueva arranca arriba */
    /* Las filas viejas fueron destruidas por lv_obj_clean; g_selected_row
     * apuntaria a memoria liberada. Reseteamos para evitar puntero colgante. */
    g_selected_row = NULL;

    if (g_wifi_scan_count < 0) {
        char buf[160];
        snprintf(buf, sizeof(buf), "Error: %s", g_wifi_scan_error);
        lv_label_set_text(g_wifi_status_label, buf);
        ui_shell_set_status(buf, UI_STATUS_ERROR);
        return;
    }

    char status[32];
    snprintf(status, sizeof(status), "%d redes encontradas", g_wifi_scan_count);
    lv_label_set_text(g_wifi_status_label, status);
    ui_shell_set_status(status, UI_STATUS_OK);

    int shown = g_wifi_scan_count < WIFI_MAX_NETWORKS ? g_wifi_scan_count : WIFI_MAX_NETWORKS;
    for (int i = 0; i < shown; i++) {
        lv_obj_t *row = lv_obj_create(g_wifi_results_box);
        lv_obj_set_size(row, 380, 24);
        lv_obj_set_style_bg_opa(row, LV_OPA_TRANSP, 0);
        lv_obj_set_style_border_width(row, 0, 0);
        lv_obj_set_style_pad_all(row, 0, 0);
        lv_obj_clear_flag(row, LV_OBJ_FLAG_SCROLLABLE);
        /* Clickeable SI, pero sin lv_button_create (evita la animacion de
         * transicion de estado "presionado" que ya nos colgo una vez). */
        lv_obj_add_flag(row, LV_OBJ_FLAG_CLICKABLE);
        lv_obj_set_ext_click_area(row, 12);
        lv_obj_set_user_data(row, (void *)(intptr_t)i);
        lv_obj_add_event_cb(row, row_select_event_cb, LV_EVENT_PRESSED, NULL);
        /* ui_apply_press_effect(row);  -- test: descartar loop del dialogo */

        char ssid_buf[20];
        snprintf(ssid_buf, sizeof(ssid_buf), "%.18s", g_wifi_scan_results[i].ssid);
        lv_obj_t *ssid_lbl = lv_label_create(row);
        lv_label_set_text(ssid_lbl, ssid_buf);
        lv_obj_set_style_text_color(ssid_lbl, COLOR_OK, 0);
        lv_obj_set_style_text_font(ssid_lbl, &lv_font_montserrat_10, 0);
        lv_obj_set_pos(ssid_lbl, 0, 0);

        char sig_buf[16];
        snprintf(sig_buf, sizeof(sig_buf), "%.0fdBm ch%d",
                 g_wifi_scan_results[i].signal, g_wifi_scan_results[i].channel);
        lv_obj_t *sig_lbl = lv_label_create(row);
        lv_label_set_text(sig_lbl, sig_buf);
        lv_obj_set_style_text_color(sig_lbl, COLOR_DIM, 0);
        lv_obj_set_style_text_font(sig_lbl, &lv_font_montserrat_10, 0);
        lv_obj_set_pos(sig_lbl, 190, 0);

        const char *sec = g_wifi_scan_results[i].security;
        lv_color_t sec_color = strcmp(sec, "OPEN") == 0 ? COLOR_ERR :
                                strcmp(sec, "WPA") == 0  ? COLOR_WARN : COLOR_OK;
        lv_obj_t *sec_lbl = lv_label_create(row);
        lv_label_set_text(sec_lbl, sec);
        lv_obj_set_style_text_color(sec_lbl, sec_color, 0);
        lv_obj_set_style_text_font(sec_lbl, &lv_font_montserrat_10, 0);
        lv_obj_set_pos(sec_lbl, 300, 0);

        const char *bnd = g_wifi_scan_results[i].band;
        lv_obj_t *band_lbl = lv_label_create(row);
        lv_label_set_text(band_lbl, bnd);
        lv_obj_set_style_text_color(band_lbl, strcmp(bnd, "5G") == 0 ? lv_color_hex(0x33CCFF) : COLOR_DIM, 0);
        lv_obj_set_style_text_font(band_lbl, &lv_font_montserrat_10, 0);
        lv_obj_set_pos(band_lbl, 340, 0);
    }
}

static void scan_btn_event_cb(lv_event_t *e) {
    if (lv_event_get_code(e) != LV_EVENT_PRESSED) return;
    start_wifi_scan();
}

static void row_select_event_cb(lv_event_t *e) {
    if (lv_event_get_code(e) != LV_EVENT_PRESSED) return;
    lv_obj_t *row = lv_event_get_target_obj(e);
    int idx = (int)(intptr_t)lv_obj_get_user_data(row);
    if (idx < 0 || idx >= g_wifi_scan_count) return;

    /* Deseleccionar visualmente la fila anterior */
    if (g_selected_row) {
        lv_obj_set_style_bg_opa(g_selected_row, LV_OPA_TRANSP, 0);
    }

    /* Guardar el objetivo */
    snprintf(g_wifi_target.ssid, sizeof(g_wifi_target.ssid), "%s", g_wifi_scan_results[idx].ssid);
    snprintf(g_wifi_target.bssid, sizeof(g_wifi_target.bssid), "%s", g_wifi_scan_results[idx].bssid);
    g_wifi_target.channel = g_wifi_scan_results[idx].channel;
    g_wifi_target.has_target = 1;

    /* Resaltar la fila seleccionada (color solido, sin transicion animada) */
    lv_obj_set_style_bg_color(row, lv_color_hex(0x1a5c1a), 0);
    lv_obj_set_style_bg_opa(row, LV_OPA_COVER, 0);
    g_selected_row = row;

    if (g_target_label) {
        char buf[96];
        snprintf(buf, sizeof(buf), "Objetivo: %s (ch%d)", g_wifi_target.ssid, g_wifi_target.channel);
        lv_label_set_text(g_target_label, buf);
    }
}

static void scroll_up_cb(lv_event_t *e) {
    if (lv_event_get_code(e) != LV_EVENT_PRESSED) return;
    if (g_wifi_results_box) lv_obj_scroll_by(g_wifi_results_box, 0, 40, LV_ANIM_OFF);
}

static void scroll_down_cb(lv_event_t *e) {
    if (lv_event_get_code(e) != LV_EVENT_PRESSED) return;
    if (g_wifi_results_box) lv_obj_scroll_by(g_wifi_results_box, 0, -40, LV_ANIM_OFF);
}

/* Accion pendiente que el loop principal (main.c) debe ejecutar —
 * el callback del boton NUNCA bloquea ni llama lv_timer_handler() el
 * mismo, solo marca la intencion y retorna de inmediato. */
volatile int g_ui_pending_action = 0; /* 0=nada 1=poweroff 2=reboot */


static void set_footer(const char *msg) {
    lv_label_set_text(g_footer_label, msg);
}

/* Barra de estado global (footer). Cada nivel tiene su color para
 * vistazo rapido y su prefijo para leer que esta pasando:
 *   INFO     verde tenue   (sin prefijo, navegacion normal)
 *   WORKING  amarillo      [...] operacion en curso
 *   OK       verde         [OK] operacion exitosa
 *   ERROR    rojo          [X]  fallo */
void ui_shell_set_status(const char *msg, ui_status_t kind) {
    if (!g_footer_label) return;
    char buf[160];
    lv_color_t color;
    switch (kind) {
        case UI_STATUS_WORKING:
            snprintf(buf, sizeof(buf), "[...] %s", msg);
            color = COLOR_WARN;
            break;
        case UI_STATUS_OK:
            snprintf(buf, sizeof(buf), "[OK] %s", msg);
            color = COLOR_OK;
            break;
        case UI_STATUS_ERROR:
            snprintf(buf, sizeof(buf), "[X] %s", msg);
            color = COLOR_ERR;
            break;
        default: /* UI_STATUS_INFO */
            snprintf(buf, sizeof(buf), "%s", msg);
            color = COLOR_OK;
            break;
    }
    lv_label_set_text(g_footer_label, buf);
    lv_obj_set_style_text_color(g_footer_label, color, 0);
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
static void row_select_event_cb(lv_event_t *e);
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


/* ═══ Conectar Red: gestion de wlan0 (red de gestion/SSH) ═══
 * Reutiliza el box de resultados con un modo propio. Las operaciones de
 * red (scan, connect) hacen fork de Python; corren en hilos para no
 * congelar la UI, con poll desde el loop principal. */
static int g_net_view = 0;            /* 0=disponibles 1=guardadas */
static net_network_t g_net_nets[NET_MAX_NETWORKS];
static int g_net_nets_count = 0;
static net_saved_t g_net_saved[NET_MAX_SAVED];
static int g_net_saved_count = 0;
static lv_obj_t *g_net_status_label = NULL;   /* label de estado arriba */
static lv_obj_t *g_net_box = NULL;            /* contenedor de la lista */
static lv_obj_t *g_net_selected_row = NULL;

/* Objetivo elegido para conectar (si necesita password) */
static char g_net_target_ssid[64] = {0};
static int g_net_target_secured = 0;

/* ── scan de red de gestion en hilo ── */
static volatile int g_netscan_running = 0;
static volatile int g_netscan_done = 0;
static void *netscan_thread_fn(void *arg) {
    (void)arg;
    char err[128];
    g_net_nets_count = net_client_scan(g_net_nets, NET_MAX_NETWORKS, err, sizeof(err));
    g_netscan_done = 1;
    return NULL;
}

/* ── connect en hilo ── */
static volatile int g_netconn_running = 0;
static volatile int g_netconn_done = 0;
static int g_netconn_ok = 0;
static char g_netconn_ssid[64] = {0};
static char g_netconn_password[128] = {0};
static char g_netconn_detail[256] = {0};
static void *netconn_thread_fn(void *arg) {
    (void)arg;
    g_netconn_detail[0] = '\0';
    g_netconn_ok = net_client_connect(g_netconn_ssid, g_netconn_password,
                                      g_netconn_detail, sizeof(g_netconn_detail));
    g_netconn_done = 1;
    return NULL;
}

/* Forward decls de las funciones de render (definidas en el Paso 2). */
static void net_render_available(void);
static void net_render_saved(void);
static void net_update_status_label(void);

void ui_shell_poll_netscan(void) {
    if (!g_netscan_done) return;
    g_netscan_done = 0;
    g_netscan_running = 0;
    if (!g_net_box) return;   /* salimos de la seccion */
    net_render_available();
}

void ui_shell_poll_netconn(void) {
    if (!g_netconn_done) return;
    g_netconn_done = 0;
    g_netconn_running = 0;
    if (!g_net_status_label) return;
    if (g_netconn_ok) {
        char buf[96];
        snprintf(buf, sizeof(buf), "Conectado a %s", g_netconn_ssid);
        ui_shell_set_status(buf, UI_STATUS_OK);
    } else {
        ui_shell_set_status("No se pudo conectar (revisa la clave)", UI_STATUS_ERROR);
    }
    net_update_status_label();
}

/* Forward decls de callbacks (definidos mas abajo / Paso 3). */
static void net_row_cb(lv_event_t *e);
static void net_start_scan(void);
static void net_connect_selected(const char *password);

/* Flechas de la lista de Conectar Red (desplazan sin tocar las filas). */
static void net_scroll_up_cb(lv_event_t *e) {
    if (lv_event_get_code(e) != LV_EVENT_PRESSED) return;
    if (g_net_box) lv_obj_scroll_by(g_net_box, 0, 40, LV_ANIM_OFF);
}

static void net_scroll_down_cb(lv_event_t *e) {
    if (lv_event_get_code(e) != LV_EVENT_PRESSED) return;
    if (g_net_box) lv_obj_scroll_by(g_net_box, 0, -40, LV_ANIM_OFF);
}

static lv_obj_t *net_make_row(int idx) {
    lv_obj_t *row = lv_obj_create(g_net_box);
    lv_obj_set_size(row, 330, 24);
    lv_obj_set_style_bg_opa(row, LV_OPA_TRANSP, 0);
    lv_obj_set_style_border_width(row, 0, 0);
    lv_obj_set_style_pad_all(row, 0, 0);
    lv_obj_clear_flag(row, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_add_flag(row, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_set_ext_click_area(row, 12);
    lv_obj_set_user_data(row, (void *)(intptr_t)idx);
    /* CLICKED (no PRESSED): se dispara al soltar sin desplazar, asi un
     * gesto de scroll que arranca sobre una fila NO conecta por accidente. */
    lv_obj_add_event_cb(row, net_row_cb, LV_EVENT_CLICKED, NULL);
    return row;
}

static lv_obj_t *net_row_label(lv_obj_t *row, const char *text, lv_color_t color, int x) {
    lv_obj_t *l = lv_label_create(row);
    lv_label_set_text(l, text);
    lv_obj_set_style_text_color(l, color, 0);
    lv_obj_set_style_text_font(l, &lv_font_montserrat_10, 0);
    lv_obj_set_pos(l, x, 0);
    return l;
}

static void net_update_status_label(void) {
    if (!g_net_status_label) return;
    net_status_t st;
    char buf[128];
    if (net_client_status(&st) && st.connected) {
        snprintf(buf, sizeof(buf), "Conectado: %s  %s", st.ssid, st.ip);
    } else {
        snprintf(buf, sizeof(buf), "wlan0: sin conexion");
    }
    lv_label_set_text(g_net_status_label, buf);
}

static void net_render_available(void) {
    if (!g_net_box || !g_net_status_label) return;
    lv_obj_clean(g_net_box);
    g_net_selected_row = NULL;
    g_net_view = 0;
    if (g_net_nets_count <= 0) {
        ui_shell_set_status("No se detectaron redes", UI_STATUS_INFO);
        return;
    }
    char st[32];
    snprintf(st, sizeof(st), "%d redes disponibles", g_net_nets_count);
    ui_shell_set_status(st, UI_STATUS_OK);
    for (int i = 0; i < g_net_nets_count; i++) {
        lv_obj_t *row = net_make_row(i);
        char sbuf[24];
        snprintf(sbuf, sizeof(sbuf), "%.18s", g_net_nets[i].ssid);
        net_row_label(row, sbuf, g_net_nets[i].in_use ? COLOR_OK : lv_color_hex(0xCCCCCC), 0);
        char sig[12];
        snprintf(sig, sizeof(sig), "%d%%", g_net_nets[i].signal);
        net_row_label(row, sig, COLOR_DIM, 215);
        int secured = (strcmp(g_net_nets[i].security, "OPEN") != 0 &&
                       g_net_nets[i].security[0] != '\0');
        net_row_label(row, secured ? LV_SYMBOL_WARNING : "abierta",
                      secured ? COLOR_WARN : COLOR_OK, 270);
    }
}

static void net_render_saved(void) {
    if (!g_net_box || !g_net_status_label) return;
    char err[128];
    g_net_saved_count = net_client_list_saved(g_net_saved, NET_MAX_SAVED, err, sizeof(err));
    lv_obj_clean(g_net_box);
    g_net_selected_row = NULL;
    g_net_view = 1;
    if (g_net_saved_count <= 0) {
        ui_shell_set_status("No hay redes guardadas", UI_STATUS_INFO);
        return;
    }
    ui_shell_set_status("Guardadas (toca para elegir)", UI_STATUS_INFO);
    for (int i = 0; i < g_net_saved_count; i++) {
        lv_obj_t *row = net_make_row(i);
        char sbuf[24];
        snprintf(sbuf, sizeof(sbuf), "%.20s", g_net_saved[i].name);
        net_row_label(row, sbuf, g_net_saved[i].active ? COLOR_OK : lv_color_hex(0xCCCCCC), 0);
        net_row_label(row, g_net_saved[i].active ? "activa" :
                      (g_net_saved[i].autoconnect ? "auto" : "-"),
                      g_net_saved[i].active ? COLOR_OK : COLOR_DIM, 270);
    }
}

/* Callback cuando el teclado devuelve la contrasena (o NULL si cancelo). */
static void net_password_cb(const char *text, void *user_data) {
    (void)user_data;
    if (!text) {   /* cancelado */
        ui_shell_set_status("Conexion cancelada", UI_STATUS_INFO);
        return;
    }
    net_connect_selected(text);
}

static void net_connect_selected(const char *password) {
    if (g_netconn_running) return;
    g_netconn_running = 1;
    g_netconn_done = 0;
    snprintf(g_netconn_ssid, sizeof(g_netconn_ssid), "%s", g_net_target_ssid);
    snprintf(g_netconn_password, sizeof(g_netconn_password), "%s", password ? password : "");
    char buf[96];
    snprintf(buf, sizeof(buf), "Conectando a %.20s...", g_net_target_ssid);
    ui_shell_set_status(buf, UI_STATUS_WORKING);
    pthread_t tid;
    pthread_create(&tid, NULL, netconn_thread_fn, NULL);
    pthread_detach(tid);
}

static void net_row_cb(lv_event_t *e) {
    if (lv_event_get_code(e) != LV_EVENT_CLICKED) return;
    lv_obj_t *row = lv_event_get_target_obj(e);
    int idx = (int)(intptr_t)lv_obj_get_user_data(row);

    /* Primer toque: solo resalta la fila y la marca como objetivo. El
     * segundo toque sobre la MISMA fila ya resaltada es el que conecta.
     * Asi se puede recorrer la lista (con el dedo o con las flechas) y
     * elegir una red sin conectarse por accidente. */
    if (row != g_net_selected_row) {
        if (g_net_selected_row) lv_obj_set_style_bg_opa(g_net_selected_row, LV_OPA_TRANSP, 0);
        lv_obj_set_style_bg_color(row, lv_color_hex(0x1a5c1a), 0);
        lv_obj_set_style_bg_opa(row, LV_OPA_COVER, 0);
        g_net_selected_row = row;
        ui_shell_set_status("Toca otra vez para conectar", UI_STATUS_INFO);
        return;
    }

    if (g_net_view == 0) {   /* redes disponibles */
        if (idx < 0 || idx >= g_net_nets_count) return;
        snprintf(g_net_target_ssid, sizeof(g_net_target_ssid), "%s", g_net_nets[idx].ssid);
        g_net_target_secured = (strcmp(g_net_nets[idx].security, "OPEN") != 0 &&
                                g_net_nets[idx].security[0] != '\0');
        /* Si ya esta guardada, conectar sin pedir clave. */
        int is_saved = 0;
        char err[64];
        int n = net_client_list_saved(g_net_saved, NET_MAX_SAVED, err, sizeof(err));
        for (int i = 0; i < n; i++) {
            if (strcmp(g_net_saved[i].name, g_net_target_ssid) == 0) { is_saved = 1; break; }
        }
        if (is_saved || !g_net_target_secured) {
            net_connect_selected(NULL);
        } else {
            char t[80];
            snprintf(t, sizeof(t), "Clave de %.20s", g_net_target_ssid);
            text_input_show(g_main_screen, t, 1, net_password_cb, NULL);
        }
    } else {                 /* guardadas: conectar directo */
        if (idx < 0 || idx >= g_net_saved_count) return;
        snprintf(g_net_target_ssid, sizeof(g_net_target_ssid), "%s", g_net_saved[idx].name);
        net_connect_selected(NULL);
    }
}

static void net_start_scan(void) {
    if (g_netscan_running) return;
    g_netscan_running = 1;
    g_netscan_done = 0;
    if (g_net_box) lv_obj_clean(g_net_box);
    g_net_selected_row = NULL;
    ui_shell_set_status("Escaneando redes...", UI_STATUS_WORKING);
    pthread_t tid;
    pthread_create(&tid, NULL, netscan_thread_fn, NULL);
    pthread_detach(tid);
}

static void net_scan_btn_cb(lv_event_t *e) {
    if (lv_event_get_code(e) != LV_EVENT_PRESSED) return;
    net_start_scan();
}

static void net_saved_btn_cb(lv_event_t *e) {
    if (lv_event_get_code(e) != LV_EVENT_PRESSED) return;
    net_render_saved();
}

/* ── Config: deauth_duration ──────────────────────────────────────────
 * Un solo parametro por ahora; la seccion esta pensada para crecer.
 * El valor se guarda en la tabla Config (key=deauth_duration, cat=wifi) y
 * lo consume el deauth sostenido (Patron A). 0 = rafaga. */
#define CFG_DEAUTH_STEP 5
#define CFG_DEAUTH_MIN  0
#define CFG_DEAUTH_MAX  600

static void cfg_refresh_deauth_label(void) {
    if (!g_cfg_deauth_label) return;
    char buf[64];
    if (g_cfg_deauth_val <= 0)
        snprintf(buf, sizeof(buf), "Deauth: %d s  (0 = rafaga)", g_cfg_deauth_val);
    else
        snprintf(buf, sizeof(buf), "Deauth: %d s  (sostenido)", g_cfg_deauth_val);
    lv_label_set_text(g_cfg_deauth_label, buf);
}

static void cfg_load_deauth(void) {
    char val[32];
    if (db_config_get("deauth_duration", val, sizeof(val)))
        g_cfg_deauth_val = atoi(val);
    else
        g_cfg_deauth_val = 0;   /* no configurado -> rafaga por defecto */
    if (g_cfg_deauth_val < CFG_DEAUTH_MIN) g_cfg_deauth_val = CFG_DEAUTH_MIN;
    if (g_cfg_deauth_val > CFG_DEAUTH_MAX) g_cfg_deauth_val = CFG_DEAUTH_MAX;
}

static void cfg_save_deauth(void) {
    char val[16], err[128] = {0};
    snprintf(val, sizeof(val), "%d", g_cfg_deauth_val);
    if (db_config_set("deauth_duration", val, "wifi", err, sizeof(err))) {
        ui_shell_set_status("deauth_duration guardado", UI_STATUS_OK);
    } else {
        char m[160];
        snprintf(m, sizeof(m), "Error DB: %s", err[0] ? err : "desconocido");
        ui_shell_set_status(m, UI_STATUS_ERROR);
    }
}

static void cfg_deauth_adjust(int delta) {
    g_cfg_deauth_val += delta;
    if (g_cfg_deauth_val < CFG_DEAUTH_MIN) g_cfg_deauth_val = CFG_DEAUTH_MIN;
    if (g_cfg_deauth_val > CFG_DEAUTH_MAX) g_cfg_deauth_val = CFG_DEAUTH_MAX;
    cfg_refresh_deauth_label();
    cfg_save_deauth();
}

static void cfg_deauth_minus_cb(lv_event_t *e) {
    if (lv_event_get_code(e) != LV_EVENT_PRESSED) return;
    cfg_deauth_adjust(-CFG_DEAUTH_STEP);
}

static void cfg_deauth_plus_cb(lv_event_t *e) {
    if (lv_event_get_code(e) != LV_EVENT_PRESSED) return;
    cfg_deauth_adjust(+CFG_DEAUTH_STEP);
}

/* ── Config: beacon_duration (seg). Lo consume el beacon flood. Min 5s. ── */
#define CFG_BEACON_STEP 5
#define CFG_BEACON_MIN  5
#define CFG_BEACON_MAX  300

static void cfg_refresh_beacon_label(void) {
    if (!g_cfg_beacon_label) return;
    char buf[64];
    snprintf(buf, sizeof(buf), "Beacon flood: %d s", g_cfg_beacon_val);
    lv_label_set_text(g_cfg_beacon_label, buf);
}

static void cfg_load_beacon(void) {
    char val[32];
    if (db_config_get("beacon_duration", val, sizeof(val)))
        g_cfg_beacon_val = atoi(val);
    else
        g_cfg_beacon_val = 20;   /* default */
    if (g_cfg_beacon_val < CFG_BEACON_MIN) g_cfg_beacon_val = CFG_BEACON_MIN;
    if (g_cfg_beacon_val > CFG_BEACON_MAX) g_cfg_beacon_val = CFG_BEACON_MAX;
}

static void cfg_save_beacon(void) {
    char val[16], err[128] = {0};
    snprintf(val, sizeof(val), "%d", g_cfg_beacon_val);
    if (db_config_set("beacon_duration", val, "wifi", err, sizeof(err))) {
        ui_shell_set_status("beacon_duration guardado", UI_STATUS_OK);
    } else {
        char m[160];
        snprintf(m, sizeof(m), "Error DB: %s", err[0] ? err : "desconocido");
        ui_shell_set_status(m, UI_STATUS_ERROR);
    }
}

static void cfg_beacon_adjust(int delta) {
    g_cfg_beacon_val += delta;
    if (g_cfg_beacon_val < CFG_BEACON_MIN) g_cfg_beacon_val = CFG_BEACON_MIN;
    if (g_cfg_beacon_val > CFG_BEACON_MAX) g_cfg_beacon_val = CFG_BEACON_MAX;
    cfg_refresh_beacon_label();
    cfg_save_beacon();
}

static void cfg_beacon_minus_cb(lv_event_t *e) {
    if (lv_event_get_code(e) != LV_EVENT_PRESSED) return;
    cfg_beacon_adjust(-CFG_BEACON_STEP);
}

static void cfg_beacon_plus_cb(lv_event_t *e) {
    if (lv_event_get_code(e) != LV_EVENT_PRESSED) return;
    cfg_beacon_adjust(+CFG_BEACON_STEP);
}

static void enter_section(const char *id, const char *label) {
    lv_obj_clean(g_body);
    g_wifi_status_label = NULL;
    g_wifi_results_box = NULL;
    g_scan_btn = NULL;
    g_target_label = NULL;
    g_actions_btn = NULL;
    g_net_box = NULL;
    g_net_status_label = NULL;
    g_net_selected_row = NULL;
    g_selected_row = NULL;
    g_cfg_deauth_label = NULL;
    g_cfg_beacon_label = NULL;
    close_actions_menu();   /* por si quedo un overlay de acciones abierto */

    if (strcmp(id, "wifi") == 0) {
        lv_obj_t *title = lv_label_create(g_body);
        lv_label_set_text(title, "WiFi");
        lv_obj_set_style_text_color(title, COLOR_OK, 0);
        lv_obj_align(title, LV_ALIGN_TOP_MID, 0, 6);

        lv_obj_t *scan_btn = lv_button_create(g_body);
        lv_obj_set_size(scan_btn, 100, 34);
        lv_obj_align(scan_btn, LV_ALIGN_TOP_LEFT, 60, 28);
        lv_obj_set_style_bg_color(scan_btn, lv_color_hex(0x0a2a0a), 0);
        lv_obj_set_style_border_color(scan_btn, COLOR_OK, 0);
        lv_obj_set_style_border_width(scan_btn, 2, 0);
        lv_obj_set_ext_click_area(scan_btn, 15);
        lv_obj_add_event_cb(scan_btn, scan_btn_event_cb, LV_EVENT_PRESSED, NULL);
        ui_apply_press_effect(scan_btn);
        g_scan_btn = scan_btn;
        lv_obj_t *scan_lbl = lv_label_create(scan_btn);
        lv_label_set_text(scan_lbl, "Escanear");
        lv_obj_set_style_text_color(scan_lbl, COLOR_OK, 0);
        lv_obj_center(scan_lbl);

        /* Boton Acciones: abre el menu overlay con Clientes/Deauth/Handshake/
         * PMKID/Auditar del AP seleccionado. Reemplaza la fila de botones. */
        g_actions_btn = lv_button_create(g_body);
        lv_obj_set_size(g_actions_btn, 125, 34);
        lv_obj_align(g_actions_btn, LV_ALIGN_TOP_LEFT, 168, 28);
        lv_obj_set_style_bg_color(g_actions_btn, lv_color_hex(0x0a2a0a), 0);
        lv_obj_set_style_border_color(g_actions_btn, lv_color_hex(0x33CCFF), 0);
        lv_obj_set_style_border_width(g_actions_btn, 2, 0);
        lv_obj_set_ext_click_area(g_actions_btn, 10);
        lv_obj_add_event_cb(g_actions_btn, actions_btn_event_cb, LV_EVENT_PRESSED, NULL);
        ui_apply_press_effect(g_actions_btn);
        lv_obj_t *actions_lbl = lv_label_create(g_actions_btn);
        lv_label_set_text(actions_lbl, "Acciones " LV_SYMBOL_LIST);
        lv_obj_set_style_text_color(actions_lbl, lv_color_hex(0x33CCFF), 0);
        lv_obj_set_style_text_font(actions_lbl, &lv_font_montserrat_10, 0);
        lv_obj_center(actions_lbl);

        /* Boton Capturas: abre la lista de capturas (HS + PMKID) sin necesitar
         * red seleccionada; para auditar on-device o ver el archivo para scp. */
        lv_obj_t *cap_btn = lv_button_create(g_body);
        lv_obj_set_size(cap_btn, 105, 34);
        lv_obj_align(cap_btn, LV_ALIGN_TOP_LEFT, 298, 28);
        lv_obj_set_style_bg_color(cap_btn, lv_color_hex(0x0a2a0a), 0);
        lv_obj_set_style_border_color(cap_btn, lv_color_hex(0xCC66FF), 0);
        lv_obj_set_style_border_width(cap_btn, 2, 0);
        lv_obj_set_ext_click_area(cap_btn, 10);
        lv_obj_add_event_cb(cap_btn, capturas_btn_event_cb, LV_EVENT_PRESSED, NULL);
        ui_apply_press_effect(cap_btn);
        lv_obj_t *cap_lbl = lv_label_create(cap_btn);
        lv_label_set_text(cap_lbl, "Capturas");
        lv_obj_set_style_text_color(cap_lbl, lv_color_hex(0xCC66FF), 0);
        lv_obj_set_style_text_font(cap_lbl, &lv_font_montserrat_10, 0);
        lv_obj_center(cap_lbl);

        g_wifi_status_label = lv_label_create(g_body);
        lv_label_set_text(g_wifi_status_label, "Toca Escanear para buscar redes");
        lv_obj_set_style_text_color(g_wifi_status_label, COLOR_DIM, 0);
        lv_obj_set_style_text_font(g_wifi_status_label, &lv_font_montserrat_10, 0);
        lv_obj_align(g_wifi_status_label, LV_ALIGN_TOP_MID, 0, 68);

        g_wifi_results_box = lv_obj_create(g_body);
        lv_obj_set_size(g_wifi_results_box, 390, 100);
        lv_obj_align(g_wifi_results_box, LV_ALIGN_TOP_LEFT, 10, 92);
        lv_obj_set_style_bg_opa(g_wifi_results_box, LV_OPA_TRANSP, 0);
        lv_obj_set_style_border_width(g_wifi_results_box, 0, 0);
        lv_obj_set_style_pad_all(g_wifi_results_box, 0, 0);
        lv_obj_set_style_pad_top(g_wifi_results_box, 6, 0);
        lv_obj_set_flex_flow(g_wifi_results_box, LV_FLEX_FLOW_COLUMN);
        lv_obj_set_style_pad_row(g_wifi_results_box, 4, 0);

        lv_obj_t *up_btn = lv_button_create(g_body);
        lv_obj_set_size(up_btn, 40, 45);
        lv_obj_align(up_btn, LV_ALIGN_TOP_RIGHT, -10, 88);
        lv_obj_set_style_bg_color(up_btn, lv_color_hex(0x0a2a0a), 0);
        lv_obj_set_style_border_color(up_btn, COLOR_OK, 0);
        lv_obj_set_style_border_width(up_btn, 2, 0);
        lv_obj_set_ext_click_area(up_btn, 10);
        lv_obj_add_event_cb(up_btn, scroll_up_cb, LV_EVENT_PRESSED, NULL);
        ui_apply_press_effect(up_btn);
        lv_obj_t *up_lbl = lv_label_create(up_btn);
        lv_label_set_text(up_lbl, LV_SYMBOL_UP);
        lv_obj_set_style_text_color(up_lbl, COLOR_OK, 0);
        lv_obj_center(up_lbl);

        lv_obj_t *down_btn = lv_button_create(g_body);
        lv_obj_set_size(down_btn, 40, 45);
        lv_obj_align(down_btn, LV_ALIGN_TOP_RIGHT, -10, 138);
        lv_obj_set_style_bg_color(down_btn, lv_color_hex(0x0a2a0a), 0);
        lv_obj_set_style_border_color(down_btn, COLOR_OK, 0);
        lv_obj_set_style_border_width(down_btn, 2, 0);
        lv_obj_set_ext_click_area(down_btn, 10);
        lv_obj_add_event_cb(down_btn, scroll_down_cb, LV_EVENT_PRESSED, NULL);
        ui_apply_press_effect(down_btn);
        lv_obj_t *down_lbl = lv_label_create(down_btn);
        lv_label_set_text(down_lbl, LV_SYMBOL_DOWN);
        lv_obj_set_style_text_color(down_lbl, COLOR_OK, 0);
        lv_obj_center(down_lbl);

        g_target_label = lv_label_create(g_body);
        lv_label_set_text(g_target_label, "Objetivo: ninguno (toca una red)");
        lv_obj_set_style_text_color(g_target_label, COLOR_DIM, 0);
        lv_obj_set_style_text_font(g_target_label, &lv_font_montserrat_10, 0);
        lv_obj_align(g_target_label, LV_ALIGN_BOTTOM_LEFT, 10, -48);

        /* Las acciones (Deauth/Handshake/PMKID/Clientes/Auditar) ya no son
         * botones sueltos: viven en el overlay que abre el boton "Acciones". */
    } else if (strcmp(id, "net_connect") == 0) {
        lv_obj_t *title = lv_label_create(g_body);
        lv_label_set_text(title, "Conectar Red (gestion / SSH)");
        lv_obj_set_style_text_color(title, COLOR_OK, 0);
        lv_obj_set_style_text_font(title, &lv_font_montserrat_10, 0);
        lv_obj_align(title, LV_ALIGN_TOP_MID, 0, 4);

        g_net_status_label = lv_label_create(g_body);
        lv_obj_set_style_text_color(g_net_status_label, COLOR_DIM, 0);
        lv_obj_set_style_text_font(g_net_status_label, &lv_font_montserrat_10, 0);
        lv_obj_align(g_net_status_label, LV_ALIGN_TOP_LEFT, 10, 22);
        net_update_status_label();

        lv_obj_t *scan_btn = lv_button_create(g_body);
        lv_obj_set_size(scan_btn, 110, 30);
        lv_obj_align(scan_btn, LV_ALIGN_TOP_LEFT, 10, 40);
        lv_obj_set_style_bg_color(scan_btn, lv_color_hex(0x0a2a0a), 0);
        lv_obj_set_style_border_color(scan_btn, COLOR_OK, 0);
        lv_obj_set_style_border_width(scan_btn, 2, 0);
        lv_obj_set_ext_click_area(scan_btn, 12);
        lv_obj_add_event_cb(scan_btn, net_scan_btn_cb, LV_EVENT_PRESSED, NULL);
        ui_apply_press_effect(scan_btn);
        lv_obj_t *sl = lv_label_create(scan_btn);
        lv_label_set_text(sl, "Escanear");
        lv_obj_set_style_text_color(sl, COLOR_OK, 0);
        lv_obj_center(sl);

        lv_obj_t *saved_btn = lv_button_create(g_body);
        lv_obj_set_size(saved_btn, 110, 30);
        lv_obj_align(saved_btn, LV_ALIGN_TOP_LEFT, 130, 40);
        lv_obj_set_style_bg_color(saved_btn, lv_color_hex(0x0a2a0a), 0);
        lv_obj_set_style_border_color(saved_btn, lv_color_hex(0x33CCFF), 0);
        lv_obj_set_style_border_width(saved_btn, 2, 0);
        lv_obj_set_ext_click_area(saved_btn, 12);
        lv_obj_add_event_cb(saved_btn, net_saved_btn_cb, LV_EVENT_PRESSED, NULL);
        ui_apply_press_effect(saved_btn);
        lv_obj_t *vl = lv_label_create(saved_btn);
        lv_label_set_text(vl, "Guardadas");
        lv_obj_set_style_text_color(vl, lv_color_hex(0x33CCFF), 0);
        lv_obj_set_style_text_font(vl, &lv_font_montserrat_10, 0);
        lv_obj_center(vl);

        g_net_box = lv_obj_create(g_body);
        lv_obj_set_size(g_net_box, 348, 130);
        lv_obj_align(g_net_box, LV_ALIGN_TOP_LEFT, 10, 78);
        lv_obj_set_style_bg_opa(g_net_box, LV_OPA_TRANSP, 0);
        lv_obj_set_style_border_width(g_net_box, 0, 0);
        lv_obj_set_style_pad_all(g_net_box, 0, 0);
        lv_obj_set_style_pad_top(g_net_box, 4, 0);
        lv_obj_set_flex_flow(g_net_box, LV_FLEX_FLOW_COLUMN);
        lv_obj_set_style_pad_row(g_net_box, 4, 0);

        /* Flechas para recorrer la lista sin tocar (ni conectar) una red. */
        lv_obj_t *nup = lv_button_create(g_body);
        lv_obj_set_size(nup, 40, 45);
        lv_obj_align(nup, LV_ALIGN_TOP_RIGHT, -10, 80);
        lv_obj_set_style_bg_color(nup, lv_color_hex(0x0a2a0a), 0);
        lv_obj_set_style_border_color(nup, COLOR_OK, 0);
        lv_obj_set_style_border_width(nup, 2, 0);
        lv_obj_set_ext_click_area(nup, 10);
        lv_obj_add_event_cb(nup, net_scroll_up_cb, LV_EVENT_PRESSED, NULL);
        ui_apply_press_effect(nup);
        lv_obj_t *nup_lbl = lv_label_create(nup);
        lv_label_set_text(nup_lbl, LV_SYMBOL_UP);
        lv_obj_set_style_text_color(nup_lbl, COLOR_OK, 0);
        lv_obj_center(nup_lbl);

        lv_obj_t *ndn = lv_button_create(g_body);
        lv_obj_set_size(ndn, 40, 45);
        lv_obj_align(ndn, LV_ALIGN_TOP_RIGHT, -10, 130);
        lv_obj_set_style_bg_color(ndn, lv_color_hex(0x0a2a0a), 0);
        lv_obj_set_style_border_color(ndn, COLOR_OK, 0);
        lv_obj_set_style_border_width(ndn, 2, 0);
        lv_obj_set_ext_click_area(ndn, 10);
        lv_obj_add_event_cb(ndn, net_scroll_down_cb, LV_EVENT_PRESSED, NULL);
        ui_apply_press_effect(ndn);
        lv_obj_t *ndn_lbl = lv_label_create(ndn);
        lv_label_set_text(ndn_lbl, LV_SYMBOL_DOWN);
        lv_obj_set_style_text_color(ndn_lbl, COLOR_OK, 0);
        lv_obj_center(ndn_lbl);

    } else if (strcmp(id, "config") == 0) {
        lv_obj_t *title = lv_label_create(g_body);
        lv_label_set_text(title, "Config");
        lv_obj_set_style_text_color(title, COLOR_OK, 0);
        lv_obj_align(title, LV_ALIGN_TOP_MID, 0, 6);

        cfg_load_deauth();

        /* Valor actual del parametro */
        g_cfg_deauth_label = lv_label_create(g_body);
        lv_obj_set_style_text_color(g_cfg_deauth_label, COLOR_OK, 0);
        lv_obj_set_style_text_font(g_cfg_deauth_label, &lv_font_montserrat_10, 0);
        lv_obj_align(g_cfg_deauth_label, LV_ALIGN_TOP_LEFT, 12, 38);
        cfg_refresh_deauth_label();

        /* Descripcion */
        lv_obj_t *desc = lv_label_create(g_body);
        lv_label_set_text(desc, "Duracion del deauth sostenido (seg).\n"
                                "0 = rafaga (envia y para).");
        lv_obj_set_style_text_color(desc, COLOR_DIM, 0);
        lv_obj_set_style_text_font(desc, &lv_font_montserrat_10, 0);
        lv_obj_align(desc, LV_ALIGN_TOP_LEFT, 12, 56);

        /* Boton -5s */
        lv_obj_t *minus = lv_button_create(g_body);
        lv_obj_set_size(minus, 70, 46);
        lv_obj_align(minus, LV_ALIGN_TOP_LEFT, 12, 95);
        lv_obj_set_style_bg_color(minus, lv_color_hex(0x0a2a0a), 0);
        lv_obj_set_style_border_color(minus, COLOR_OK, 0);
        lv_obj_set_style_border_width(minus, 2, 0);
        lv_obj_set_ext_click_area(minus, 12);
        lv_obj_add_event_cb(minus, cfg_deauth_minus_cb, LV_EVENT_PRESSED, NULL);
        ui_apply_press_effect(minus);
        lv_obj_t *ml = lv_label_create(minus);
        lv_label_set_text(ml, "-5s");
        lv_obj_set_style_text_color(ml, COLOR_OK, 0);
        lv_obj_center(ml);

        /* Boton +5s */
        lv_obj_t *plus = lv_button_create(g_body);
        lv_obj_set_size(plus, 70, 46);
        lv_obj_align(plus, LV_ALIGN_TOP_LEFT, 92, 95);
        lv_obj_set_style_bg_color(plus, lv_color_hex(0x0a2a0a), 0);
        lv_obj_set_style_border_color(plus, COLOR_OK, 0);
        lv_obj_set_style_border_width(plus, 2, 0);
        lv_obj_set_ext_click_area(plus, 12);
        lv_obj_add_event_cb(plus, cfg_deauth_plus_cb, LV_EVENT_PRESSED, NULL);
        ui_apply_press_effect(plus);
        lv_obj_t *pl = lv_label_create(plus);
        lv_label_set_text(pl, "+5s");
        lv_obj_set_style_text_color(pl, COLOR_OK, 0);
        lv_obj_center(pl);

        /* ── beacon_duration ── */
        cfg_load_beacon();
        g_cfg_beacon_label = lv_label_create(g_body);
        lv_obj_set_style_text_color(g_cfg_beacon_label, lv_color_hex(0xFF9933), 0);
        lv_obj_set_style_text_font(g_cfg_beacon_label, &lv_font_montserrat_10, 0);
        lv_obj_align(g_cfg_beacon_label, LV_ALIGN_TOP_LEFT, 12, 152);
        cfg_refresh_beacon_label();

        lv_obj_t *bdesc = lv_label_create(g_body);
        lv_label_set_text(bdesc, "Duracion del beacon flood (seg).");
        lv_obj_set_style_text_color(bdesc, COLOR_DIM, 0);
        lv_obj_set_style_text_font(bdesc, &lv_font_montserrat_10, 0);
        lv_obj_align(bdesc, LV_ALIGN_TOP_LEFT, 12, 170);

        lv_obj_t *bminus = lv_button_create(g_body);
        lv_obj_set_size(bminus, 70, 44);
        lv_obj_align(bminus, LV_ALIGN_TOP_LEFT, 12, 192);
        lv_obj_set_style_bg_color(bminus, lv_color_hex(0x0a2a0a), 0);
        lv_obj_set_style_border_color(bminus, lv_color_hex(0xFF9933), 0);
        lv_obj_set_style_border_width(bminus, 2, 0);
        lv_obj_set_ext_click_area(bminus, 12);
        lv_obj_add_event_cb(bminus, cfg_beacon_minus_cb, LV_EVENT_PRESSED, NULL);
        ui_apply_press_effect(bminus);
        lv_obj_t *bml = lv_label_create(bminus);
        lv_label_set_text(bml, "-5s");
        lv_obj_set_style_text_color(bml, lv_color_hex(0xFF9933), 0);
        lv_obj_center(bml);

        lv_obj_t *bplus = lv_button_create(g_body);
        lv_obj_set_size(bplus, 70, 44);
        lv_obj_align(bplus, LV_ALIGN_TOP_LEFT, 92, 192);
        lv_obj_set_style_bg_color(bplus, lv_color_hex(0x0a2a0a), 0);
        lv_obj_set_style_border_color(bplus, lv_color_hex(0xFF9933), 0);
        lv_obj_set_style_border_width(bplus, 2, 0);
        lv_obj_set_ext_click_area(bplus, 12);
        lv_obj_add_event_cb(bplus, cfg_beacon_plus_cb, LV_EVENT_PRESSED, NULL);
        ui_apply_press_effect(bplus);
        lv_obj_t *bpl = lv_label_create(bplus);
        lv_label_set_text(bpl, "+5s");
        lv_obj_set_style_text_color(bpl, lv_color_hex(0xFF9933), 0);
        lv_obj_center(bpl);

    } else {
        lv_obj_t *title = lv_label_create(g_body);
        char buf[64];
        snprintf(buf, sizeof(buf), "%s\n(Proximamente)", label);
        lv_label_set_text(title, buf);
        lv_obj_set_style_text_color(title, COLOR_OK, 0);
        lv_obj_set_style_text_align(title, LV_TEXT_ALIGN_CENTER, 0);
        lv_obj_center(title);
    }

    lv_obj_t *back = lv_button_create(g_body);
    lv_obj_set_size(back, 90, 36);
    lv_obj_align(back, LV_ALIGN_BOTTOM_RIGHT, -10, -14);
    lv_obj_set_style_bg_color(back, lv_color_hex(0x0a2a0a), 0);
    lv_obj_set_style_border_color(back, COLOR_OK, 0);
    lv_obj_set_style_border_width(back, 2, 0);
    lv_obj_set_ext_click_area(back, 15);
    lv_obj_t *back_lbl = lv_label_create(back);
    lv_label_set_text(back_lbl, "< Menu");
    lv_obj_set_style_text_color(back_lbl, COLOR_OK, 0);
    lv_obj_center(back_lbl);
    lv_obj_add_event_cb(back, back_to_carousel_cb, LV_EVENT_PRESSED, NULL);
    ui_apply_press_effect(back);

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
    close_actions_menu();   /* por si quedara un overlay de acciones abierto */
    lv_obj_clean(g_body);
    g_net_box = NULL;
    g_net_status_label = NULL;
    g_net_selected_row = NULL;
    g_wifi_status_label = NULL;
    g_wifi_results_box = NULL;
    g_scan_btn = NULL;
    g_net_box = NULL;
    g_net_status_label = NULL;
    g_net_selected_row = NULL;
    g_target_label = NULL;
    g_actions_btn = NULL;
    g_net_box = NULL;
    g_net_status_label = NULL;
    g_net_selected_row = NULL;
    g_selected_row = NULL;

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
    ui_apply_press_effect(left);

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
    ui_apply_press_effect(right);

    lv_obj_t *center = lv_button_create(g_body);
    lv_obj_set_size(center, 160, 100);
    lv_obj_center(center);
    lv_obj_set_style_bg_color(center, lv_color_hex(0x0a2a0a), 0);
    lv_obj_set_style_border_color(center, COLOR_OK, 0);
    lv_obj_set_style_border_width(center, 2, 0);
    lv_obj_set_ext_click_area(center, 15);
    lv_obj_add_event_cb(center, select_event_cb, LV_EVENT_PRESSED, NULL);
    ui_apply_press_effect(center);

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
    ui_apply_press_effect(lock_icon);

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
    ui_apply_press_effect(reset_btn);
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
    ui_apply_press_effect(power_btn);
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

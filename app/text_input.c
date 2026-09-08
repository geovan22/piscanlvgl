/* ═══════════════════════════════════════════════════════
   text_input.c — Overlay reutilizable de entrada de texto usando el
   teclado nativo de LVGL (lv_keyboard). Patron similar a confirm_dialog.
   ═══════════════════════════════════════════════════════ */
#include <stdio.h>
#include "text_input.h"
#include "ui_style.h"

#define TI_OK   lv_color_hex(0x33FF33)
#define TI_DIM  lv_color_hex(0x2a6b2a)

static lv_obj_t *g_ti_overlay = NULL;
static lv_obj_t *g_ti_textarea = NULL;
static text_input_cb_t g_ti_cb = NULL;
static void *g_ti_ud = NULL;

static void ti_close(void) {
    if (g_ti_overlay) {
        lv_obj_del(g_ti_overlay);
        g_ti_overlay = NULL;
        g_ti_textarea = NULL;
    }
}

/* El teclado LVGL emite LV_EVENT_READY al tocar el check (OK) y
 * LV_EVENT_CANCEL al tocar la X. Manejamos ambos aca. */
static void ti_kb_event_cb(lv_event_t *e) {
    lv_event_code_t code = lv_event_get_code(e);
    if (code == LV_EVENT_READY) {
        const char *txt = g_ti_textarea ? lv_textarea_get_text(g_ti_textarea) : NULL;
        text_input_cb_t cb = g_ti_cb;
        void *ud = g_ti_ud;
        /* Copiamos el texto antes de cerrar (se libera con el overlay). */
        char buf[128];
        snprintf(buf, sizeof(buf), "%s", txt ? txt : "");
        ti_close();
        if (cb) cb(buf, ud);
    } else if (code == LV_EVENT_CANCEL) {
        text_input_cb_t cb = g_ti_cb;
        void *ud = g_ti_ud;
        ti_close();
        if (cb) cb(NULL, ud);
    }
}

void text_input_show(lv_obj_t *parent, const char *title, int is_password,
                     text_input_cb_t on_result, void *user_data) {
    g_ti_cb = on_result;
    g_ti_ud = user_data;

    g_ti_overlay = lv_obj_create(parent);
    lv_obj_set_size(g_ti_overlay, 480, 320);
    lv_obj_set_pos(g_ti_overlay, 0, 0);
    lv_obj_set_style_bg_color(g_ti_overlay, lv_color_black(), 0);
    lv_obj_set_style_bg_opa(g_ti_overlay, LV_OPA_COVER, 0);
    lv_obj_set_style_border_width(g_ti_overlay, 0, 0);
    lv_obj_set_style_pad_all(g_ti_overlay, 0, 0);
    lv_obj_clear_flag(g_ti_overlay, LV_OBJ_FLAG_SCROLLABLE);

    lv_obj_t *ttl = lv_label_create(g_ti_overlay);
    lv_label_set_text(ttl, title ? title : "Ingresa texto");
    lv_obj_set_style_text_color(ttl, TI_OK, 0);
    lv_obj_set_style_text_font(ttl, &lv_font_montserrat_10, 0);
    lv_obj_align(ttl, LV_ALIGN_TOP_LEFT, 8, 4);

    g_ti_textarea = lv_textarea_create(g_ti_overlay);
    lv_textarea_set_one_line(g_ti_textarea, true);
    lv_textarea_set_password_mode(g_ti_textarea, is_password ? true : false);
    lv_obj_set_style_anim_duration(g_ti_textarea, 0, LV_PART_CURSOR);
    lv_obj_set_size(g_ti_textarea, 460, 34);
    lv_obj_align(g_ti_textarea, LV_ALIGN_TOP_MID, 0, 20);
    lv_obj_set_style_bg_color(g_ti_textarea, lv_color_hex(0x0a2a0a), 0);
    lv_obj_set_style_text_color(g_ti_textarea, TI_OK, 0);
    lv_obj_set_style_border_color(g_ti_textarea, TI_OK, 0);
    lv_obj_set_style_border_width(g_ti_textarea, 2, 0);

    lv_obj_t *kb = lv_keyboard_create(g_ti_overlay);
    lv_obj_set_size(kb, 480, 200);
    lv_obj_align(kb, LV_ALIGN_BOTTOM_MID, 0, 0);
    lv_keyboard_set_textarea(kb, g_ti_textarea);
    lv_obj_add_event_cb(kb, ti_kb_event_cb, LV_EVENT_READY, NULL);
    lv_obj_add_event_cb(kb, ti_kb_event_cb, LV_EVENT_CANCEL, NULL);
}

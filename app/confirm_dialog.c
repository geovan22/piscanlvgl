/* ═══════════════════════════════════════════════════════
   confirm_dialog.c — Popup Si/No reutilizable.
   ═══════════════════════════════════════════════════════ */
#include "confirm_dialog.h"

static lv_obj_t *g_overlay = NULL;
static confirm_result_cb_t g_cb;
static void *g_ud;

static void close_dialog(void) {
    if (g_overlay) {
        lv_obj_del(g_overlay);
        g_overlay = NULL;
    }
}

static void yes_cb(lv_event_t *e) {
    if (lv_event_get_code(e) != LV_EVENT_PRESSED) return;
    confirm_result_cb_t cb = g_cb;
    void *ud = g_ud;
    close_dialog();
    if (cb) cb(true, ud);
}

static void no_cb(lv_event_t *e) {
    if (lv_event_get_code(e) != LV_EVENT_PRESSED) return;
    confirm_result_cb_t cb = g_cb;
    void *ud = g_ud;
    close_dialog();
    if (cb) cb(false, ud);
}

void confirm_dialog_show(lv_obj_t *parent, const char *message, confirm_result_cb_t on_result, void *user_data) {
    g_cb = on_result;
    g_ud = user_data;

    g_overlay = lv_obj_create(parent);
    lv_obj_set_size(g_overlay, 480, 320);
    lv_obj_set_pos(g_overlay, 0, 0);
    lv_obj_set_style_bg_color(g_overlay, lv_color_black(), 0);
    lv_obj_set_style_bg_opa(g_overlay, LV_OPA_80, 0);
    lv_obj_set_style_border_width(g_overlay, 0, 0);
    lv_obj_set_style_pad_all(g_overlay, 0, 0);
    lv_obj_clear_flag(g_overlay, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_add_flag(g_overlay, LV_OBJ_FLAG_CLICKABLE);

    lv_obj_t *box = lv_obj_create(g_overlay);
    lv_obj_set_size(box, 280, 120);
    lv_obj_center(box);
    lv_obj_set_style_bg_color(box, lv_color_hex(0x0a2a0a), 0);
    lv_obj_set_style_bg_opa(box, LV_OPA_COVER, 0);
    lv_obj_set_style_border_color(box, lv_color_hex(0x33FF33), 0);
    lv_obj_set_style_border_width(box, 2, 0);
    lv_obj_clear_flag(box, LV_OBJ_FLAG_SCROLLABLE);

    lv_obj_t *msg = lv_label_create(box);
    lv_label_set_text(msg, message);
    lv_obj_set_style_text_color(msg, lv_color_hex(0x33FF33), 0);
    lv_obj_set_style_text_align(msg, LV_TEXT_ALIGN_CENTER, 0);
    lv_obj_set_width(msg, 260);
    lv_obj_align(msg, LV_ALIGN_TOP_MID, 0, 12);

    lv_obj_t *yes_btn = lv_button_create(box);
    lv_obj_set_size(yes_btn, 100, 40);
    lv_obj_align(yes_btn, LV_ALIGN_BOTTOM_LEFT, 15, -12);
    lv_obj_set_style_bg_color(yes_btn, lv_color_hex(0x330000), 0);
    lv_obj_set_style_border_color(yes_btn, lv_color_hex(0xFF4444), 0);
    lv_obj_set_style_border_width(yes_btn, 2, 0);
    lv_obj_set_ext_click_area(yes_btn, 12);
    lv_obj_add_event_cb(yes_btn, yes_cb, LV_EVENT_PRESSED, NULL);
    lv_obj_t *yes_lbl = lv_label_create(yes_btn);
    lv_label_set_text(yes_lbl, "Si");
    lv_obj_set_style_text_color(yes_lbl, lv_color_hex(0xFF4444), 0);
    lv_obj_center(yes_lbl);

    lv_obj_t *no_btn = lv_button_create(box);
    lv_obj_set_size(no_btn, 100, 40);
    lv_obj_align(no_btn, LV_ALIGN_BOTTOM_RIGHT, -15, -12);
    lv_obj_set_style_bg_color(no_btn, lv_color_hex(0x0a2a0a), 0);
    lv_obj_set_style_border_color(no_btn, lv_color_hex(0x33FF33), 0);
    lv_obj_set_style_border_width(no_btn, 2, 0);
    lv_obj_set_ext_click_area(no_btn, 12);
    lv_obj_add_event_cb(no_btn, no_cb, LV_EVENT_PRESSED, NULL);
    lv_obj_t *no_lbl = lv_label_create(no_btn);
    lv_label_set_text(no_lbl, "No");
    lv_obj_set_style_text_color(no_lbl, lv_color_hex(0x33FF33), 0);
    lv_obj_center(no_lbl);
}

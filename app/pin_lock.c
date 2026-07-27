/* ═══════════════════════════════════════════════════════
   pin_lock.c — PIN numerico (4-8 digitos) con teclado en pantalla.
   Reemplaza el patron de arrastre: mas confiable en este panel
   resistivo, ya que solo depende de toques discretos (tap), no de
   arrastre continuo — que es justo lo que peor maneja este hardware.
   ═══════════════════════════════════════════════════════ */
#include <stdio.h>
#include <string.h>
#include "pin_lock.h"
#include "db_client.h"

#define MIN_PIN_LEN 4
#define MAX_PIN_LEN 8

typedef enum { MODE_VERIFY, MODE_SETUP_FIRST, MODE_SETUP_CONFIRM } pl_mode_t;

static lv_obj_t *bg;
static lv_obj_t *title_label;
static lv_obj_t *display_label;
static char      pin_buf[MAX_PIN_LEN + 1];
static int       pin_len = 0;

static pl_mode_t mode;
static pin_result_cb_t result_cb;
static void *result_ud;
static char first_pin[MAX_PIN_LEN + 1];

static void set_title(const char *msg, bool is_error) {
    lv_label_set_text(title_label, msg);
    lv_obj_set_style_text_color(title_label, is_error ? lv_color_hex(0xFF4444) : lv_color_hex(0x33FF33), 0);
}

static void update_display(void) {
    char stars[MAX_PIN_LEN + 1];
    int i;
    for (i = 0; i < pin_len; i++) stars[i] = '*';
    stars[i] = '\0';
    lv_label_set_text(display_label, pin_len > 0 ? stars : "-");
}

static void reset_pin(void) {
    pin_len = 0;
    pin_buf[0] = '\0';
    update_display();
}

static void finish_pin(void) {
    if (pin_len < MIN_PIN_LEN) {
        set_title("PIN muy corto (min 4)", true);
        return; /* no reseteamos, dejamos que siga completando */
    }

    if (mode == MODE_VERIFY) {
        int ok = db_credential_verify("lock_pin", pin_buf);
        if (ok) {
            set_title("Correcto", false);
            if (result_cb) result_cb(true, result_ud);
        } else {
            set_title("PIN incorrecto, intenta de nuevo", true);
            reset_pin();
        }
    } else if (mode == MODE_SETUP_FIRST) {
        strncpy(first_pin, pin_buf, sizeof(first_pin) - 1);
        mode = MODE_SETUP_CONFIRM;
        set_title("Repite el PIN para confirmar", false);
        reset_pin();
    } else { /* MODE_SETUP_CONFIRM */
        if (strcmp(pin_buf, first_pin) == 0) {
            int ok = db_credential_set("lock_pin", pin_buf);
            if (ok) {
                set_title("PIN guardado", false);
                if (result_cb) result_cb(true, result_ud);
            } else {
                set_title("Error guardando, intenta de nuevo", true);
                mode = MODE_SETUP_FIRST;
                reset_pin();
            }
        } else {
            set_title("No coincide, empecemos de nuevo", true);
            mode = MODE_SETUP_FIRST;
            reset_pin();
        }
    }
}

static void digit_event_cb(lv_event_t *e) {
    if (lv_event_get_code(e) != LV_EVENT_PRESSED) return;
    int digit = (int)(intptr_t)lv_event_get_user_data(e);
    if (pin_len < MAX_PIN_LEN) {
        pin_buf[pin_len] = (char)('0' + digit);
        pin_len++;
        pin_buf[pin_len] = '\0';
        update_display();
    }
}

static void backspace_event_cb(lv_event_t *e) {
    if (lv_event_get_code(e) != LV_EVENT_PRESSED) return;
    if (pin_len > 0) {
        pin_len--;
        pin_buf[pin_len] = '\0';
        update_display();
    }
}

static void enter_event_cb(lv_event_t *e) {
    if (lv_event_get_code(e) != LV_EVENT_PRESSED) return;
    finish_pin();
}

static lv_obj_t *make_key(lv_obj_t *parent, const char *label_text, int x, int y, int w, int h) {
    lv_obj_t *btn = lv_button_create(parent);
    lv_obj_set_size(btn, w, h);
    lv_obj_set_pos(btn, x, y);
    lv_obj_set_style_bg_color(btn, lv_color_hex(0x0a2a0a), 0);
    lv_obj_set_style_bg_opa(btn, LV_OPA_COVER, 0);
    lv_obj_set_style_border_color(btn, lv_color_hex(0x33FF33), 0);
    lv_obj_set_style_border_width(btn, 2, 0);
    lv_obj_set_style_radius(btn, 6, 0);

    lv_obj_t *lbl = lv_label_create(btn);
    lv_label_set_text(lbl, label_text);
    lv_obj_set_style_text_color(lbl, lv_color_hex(0x33FF33), 0);
    lv_obj_center(lbl);

    /* Zona de deteccion mas grande que el boton visual — el panel
     * resistivo tiene imprecision conocida, ya lo vimos en todo el
     * proyecto anterior. */
    lv_obj_set_ext_click_area(btn, 15);

    return btn;
}

static void build_screen(lv_obj_t *parent, const char *title_text) {
    lv_obj_clean(parent);
    bg = parent;
    lv_obj_set_style_bg_color(bg, lv_color_black(), 0);
    lv_obj_set_style_bg_opa(bg, LV_OPA_COVER, 0);

    title_label = lv_label_create(bg);
    lv_label_set_text(title_label, title_text);
    lv_obj_set_style_text_color(title_label, lv_color_hex(0x33FF33), 0);
    lv_obj_align(title_label, LV_ALIGN_TOP_MID, 0, 12);

    display_label = lv_label_create(bg);
    lv_obj_set_style_text_color(display_label, lv_color_white(), 0);
    lv_obj_set_style_text_font(display_label, &lv_font_montserrat_14, 0);
    lv_obj_align(display_label, LV_ALIGN_TOP_MID, 0, 40);

    /* Teclado 3x4: 1-9, [Borrar] 0 [OK] */
    int key_w = 95, key_h = 42, gap = 8;
    int grid_w = key_w * 3 + gap * 2;
    int x0 = (480 - grid_w) / 2;
    int y0 = 78;

    const char *labels[12] = {"1","2","3","4","5","6","7","8","9","Borrar","0","OK"};
    for (int i = 0; i < 12; i++) {
        int row = i / 3, col = i % 3;
        int x = x0 + col * (key_w + gap);
        int y = y0 + row * (key_h + gap);
        lv_obj_t *btn = make_key(bg, labels[i], x, y, key_w, key_h);
        if (i < 9) {
            lv_obj_add_event_cb(btn, digit_event_cb, LV_EVENT_PRESSED, (void *)(intptr_t)(i + 1));
        } else if (i == 9) {
            lv_obj_add_event_cb(btn, backspace_event_cb, LV_EVENT_PRESSED, NULL);
        } else if (i == 10) {
            lv_obj_add_event_cb(btn, digit_event_cb, LV_EVENT_PRESSED, (void *)(intptr_t)0);
        } else {
            lv_obj_add_event_cb(btn, enter_event_cb, LV_EVENT_PRESSED, NULL);
        }
    }

    reset_pin();
}

void pin_lock_show_verify(lv_obj_t *parent, pin_result_cb_t on_result, void *user_data) {
    mode = MODE_VERIFY;
    result_cb = on_result;
    result_ud = user_data;
    build_screen(parent, "Ingresa tu PIN");
}

void pin_lock_show_setup(lv_obj_t *parent, pin_result_cb_t on_result, void *user_data) {
    mode = MODE_SETUP_FIRST;
    result_cb = on_result;
    result_ud = user_data;
    first_pin[0] = '\0';
    build_screen(parent, "Crea un PIN nuevo (4-8 digitos)");
}

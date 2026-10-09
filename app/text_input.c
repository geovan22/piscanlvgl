/* ═══════════════════════════════════════════════════════
   text_input.c — Overlay reutilizable de entrada de texto con un teclado
   PROPIO de botones (estilo teclado del PIN), no el lv_keyboard nativo.
   Motivo: el lv_keyboard redibuja mucho (cursor, animaciones) y en el
   display KeDei (SPI lento) va pesado; botones simples solo se redibujan
   al presionarse. QWERTY + capa de simbolos + ñ. Reutilizable en cualquier
   pantalla que necesite entrada de texto.
   ═══════════════════════════════════════════════════════ */
#include <stdio.h>
#include <string.h>
#include "text_input.h"
#include "ui_style.h"

#define TI_OK   lv_color_hex(0x33FF33)
#define TI_DIM  lv_color_hex(0x2a6b2a)
#define TI_BG   lv_color_hex(0x0a2a0a)
#define TI_SPEC lv_color_hex(0x33CCFF)   /* teclas especiales en celeste */

static lv_obj_t *g_ti_overlay = NULL;
static lv_obj_t *g_ti_textarea = NULL;
static lv_obj_t *g_ti_kb = NULL;         /* contenedor del teclado */
static lv_obj_t *g_ti_eye_lbl = NULL;    /* etiqueta del boton ver/ocultar */
static text_input_cb_t g_ti_cb = NULL;
static void *g_ti_ud = NULL;

static int g_ti_pw_visible = 0;   /* 0 = oculto (password mode on), 1 = visible */

static int g_shift = 0;   /* 0=minuscula 1=mayuscula */
static int g_layer = 0;   /* 0=letras 1=simbolos */

static void ti_close(void) {
    if (g_ti_overlay) {
        lv_obj_del(g_ti_overlay);
        g_ti_overlay = NULL;
        g_ti_textarea = NULL;
        g_ti_kb = NULL;
        g_ti_eye_lbl = NULL;
    }
}

static void ti_rebuild_keyboard(void);   /* fwd */

/* ── Callbacks de las teclas ── */
static void key_char_cb(lv_event_t *e) {
    if (lv_event_get_code(e) != LV_EVENT_PRESSED) return;
    const char *s = (const char *)lv_event_get_user_data(e);
    if (g_ti_textarea && s) lv_textarea_add_text(g_ti_textarea, s);
}

static void key_backspace_cb(lv_event_t *e) {
    if (lv_event_get_code(e) != LV_EVENT_PRESSED) return;
    if (g_ti_textarea) lv_textarea_delete_char(g_ti_textarea);
}

static void key_space_cb(lv_event_t *e) {
    if (lv_event_get_code(e) != LV_EVENT_PRESSED) return;
    if (g_ti_textarea) lv_textarea_add_text(g_ti_textarea, " ");
}

static void key_shift_cb(lv_event_t *e) {
    if (lv_event_get_code(e) != LV_EVENT_PRESSED) return;
    g_shift = !g_shift;
    ti_rebuild_keyboard();
}

static void key_layer_cb(lv_event_t *e) {
    if (lv_event_get_code(e) != LV_EVENT_PRESSED) return;
    g_layer = !g_layer;
    ti_rebuild_keyboard();
}

/* Alterna mostrar/ocultar la contrasena (solo visible en modo password). */
static void key_eye_cb(lv_event_t *e) {
    if (lv_event_get_code(e) != LV_EVENT_PRESSED) return;
    if (!g_ti_textarea) return;
    g_ti_pw_visible = !g_ti_pw_visible;
    lv_textarea_set_password_mode(g_ti_textarea, g_ti_pw_visible ? false : true);
    if (g_ti_eye_lbl) lv_label_set_text(g_ti_eye_lbl, g_ti_pw_visible ? "ocu" : "ver");
}

static void key_ok_cb(lv_event_t *e) {
    if (lv_event_get_code(e) != LV_EVENT_PRESSED) return;
    const char *txt = g_ti_textarea ? lv_textarea_get_text(g_ti_textarea) : NULL;
    text_input_cb_t cb = g_ti_cb;
    void *ud = g_ti_ud;
    char buf[128];
    snprintf(buf, sizeof(buf), "%s", txt ? txt : "");
    ti_close();
    if (cb) cb(buf, ud);
}

static void key_cancel_cb(lv_event_t *e) {
    if (lv_event_get_code(e) != LV_EVENT_PRESSED) return;
    text_input_cb_t cb = g_ti_cb;
    void *ud = g_ti_ud;
    ti_close();
    if (cb) cb(NULL, ud);
}

/* Cada tecla de caracter guarda su string en user_data. Como el teclado se
 * reconstruye al cambiar capa/shift, usamos un pool estatico de strings de
 * 2 bytes (char + '\0') que persiste mientras el overlay vive. */
#define TI_MAX_KEYS 64
static char g_keystr[TI_MAX_KEYS][2];
static int g_keystr_n = 0;

static lv_obj_t *make_key(lv_obj_t *parent, const char *label, int x, int y, int w, int h,
                          lv_color_t color, lv_event_cb_t cb, void *ud) {
    lv_obj_t *btn = lv_button_create(parent);
    lv_obj_set_size(btn, w, h);
    lv_obj_set_pos(btn, x, y);
    lv_obj_set_style_bg_color(btn, TI_BG, 0);
    lv_obj_set_style_border_color(btn, color, 0);
    lv_obj_set_style_border_width(btn, 1, 0);
    lv_obj_set_style_radius(btn, 4, 0);
    lv_obj_set_ext_click_area(btn, 4);
    lv_obj_add_event_cb(btn, cb, LV_EVENT_PRESSED, ud);
    ui_apply_press_effect(btn);
    lv_obj_t *lbl = lv_label_create(btn);
    lv_label_set_text(lbl, label);
    lv_obj_set_style_text_color(lbl, color, 0);
    lv_obj_set_style_text_font(lbl, &lv_font_montserrat_10, 0);
    lv_obj_center(lbl);
    return btn;
}

/* Agrega una tecla de caracter: guarda el string en el pool y crea el boton. */
static void add_char_key(lv_obj_t *parent, char c, int x, int y, int w, int h) {
    if (g_keystr_n >= TI_MAX_KEYS) return;
    g_keystr[g_keystr_n][0] = c;
    g_keystr[g_keystr_n][1] = '\0';
    char *s = g_keystr[g_keystr_n];
    g_keystr_n++;
    char label[2] = { c, '\0' };
    make_key(parent, label, x, y, w, h, TI_OK, key_char_cb, s);
}

/* Filas QWERTY (minusculas). shift las pasa a mayusculas. */
static const char *ROW1 = "qwertyuiop";
static const char *ROW2 = "asdfghjkl\xC3\xB1";   /* incluye ñ (UTF-8 C3 B1) al final */
static const char *ROW3 = "zxcvbnm";
/* Capa de simbolos */
static const char *SYM1 = "1234567890";
static const char *SYM2 = "@#$%&*()-_";
static const char *SYM3 = ".,:;/?!+=";

static void ti_build_layer_letters(lv_obj_t *kb) {
    int kw = 44, kh = 40, gap = 3;
    int y = 6;
    /* Fila 1: 10 teclas */
    int x = 6;
    for (int i = 0; ROW1[i]; i++) {
        char c = g_shift ? (char)(ROW1[i] - 32) : ROW1[i];
        add_char_key(kb, c, x, y, kw, kh);
        x += kw + gap;
    }
    /* Fila 2: a..l + ñ (la ñ es 2 bytes UTF-8, la manejamos aparte) */
    y += kh + gap; x = 6 + kw / 2;
    const char *r2 = "asdfghjkl";
    for (int i = 0; r2[i]; i++) {
        char c = g_shift ? (char)(r2[i] - 32) : r2[i];
        add_char_key(kb, c, x, y, kw, kh);
        x += kw + gap;
    }
    /* ñ: tecla especial de 2 bytes */
    if (g_keystr_n < TI_MAX_KEYS) {
        /* guardamos "ñ" (2 bytes) en un buffer aparte reutilizando pool ancho */
        static char enye_lo[3] = { (char)0xC3, (char)0xB1, '\0' };
        static char enye_up[3] = { (char)0xC3, (char)0x91, '\0' };
        make_key(kb, g_shift ? enye_up : enye_lo, x, y, kw, kh, TI_OK, key_char_cb,
                 g_shift ? enye_up : enye_lo);
    }
    /* Fila 3: shift + z..m + backspace */
    y += kh + gap; x = 6;
    make_key(kb, "Sh", x, y, kw, kh, TI_SPEC, key_shift_cb, NULL); x += kw + gap;
    for (int i = 0; ROW3[i]; i++) {
        char c = g_shift ? (char)(ROW3[i] - 32) : ROW3[i];
        add_char_key(kb, c, x, y, kw, kh);
        x += kw + gap;
    }
    make_key(kb, "<-", x, y, kw + 20, kh, TI_SPEC, key_backspace_cb, NULL);
    /* Fila 4: 123 + espacio + . + @ + OK + X */
    y += kh + gap; x = 6;
    make_key(kb, "123", x, y, kw, kh, TI_SPEC, key_layer_cb, NULL); x += kw + gap;
    make_key(kb, "espacio", x, y, kw * 3, kh, TI_OK, key_space_cb, NULL); x += kw * 3 + gap;
    add_char_key(kb, '.', x, y, kw, kh); x += kw + gap;
    make_key(kb, "OK", x, y, kw + 10, kh, TI_OK, key_ok_cb, NULL); x += kw + 10 + gap;
    make_key(kb, "X", x, y, kw - 6, kh, TI_SPEC, key_cancel_cb, NULL);
}

static void ti_build_layer_symbols(lv_obj_t *kb) {
    int kw = 44, kh = 40, gap = 3;
    int y = 6, x = 6;
    for (int i = 0; SYM1[i]; i++) { add_char_key(kb, SYM1[i], x, y, kw, kh); x += kw + gap; }
    y += kh + gap; x = 6;
    for (int i = 0; SYM2[i]; i++) { add_char_key(kb, SYM2[i], x, y, kw, kh); x += kw + gap; }
    y += kh + gap; x = 6;
    for (int i = 0; SYM3[i]; i++) { add_char_key(kb, SYM3[i], x, y, kw, kh); x += kw + gap; }
    make_key(kb, "<-", x, y, kw + 20, kh, TI_SPEC, key_backspace_cb, NULL);
    y += kh + gap; x = 6;
    make_key(kb, "abc", x, y, kw, kh, TI_SPEC, key_layer_cb, NULL); x += kw + gap;
    make_key(kb, "espacio", x, y, kw * 3, kh, TI_OK, key_space_cb, NULL); x += kw * 3 + gap;
    add_char_key(kb, ',', x, y, kw, kh); x += kw + gap;
    make_key(kb, "OK", x, y, kw + 10, kh, TI_OK, key_ok_cb, NULL); x += kw + 10 + gap;
    make_key(kb, "X", x, y, kw - 6, kh, TI_SPEC, key_cancel_cb, NULL);
}

static void ti_rebuild_keyboard(void) {
    if (!g_ti_kb) return;
    lv_obj_clean(g_ti_kb);
    g_keystr_n = 0;   /* el pool se reusa en cada reconstruccion */
    if (g_layer == 0) ti_build_layer_letters(g_ti_kb);
    else ti_build_layer_symbols(g_ti_kb);
}

void text_input_show(lv_obj_t *parent, const char *title, int is_password,
                     text_input_cb_t on_result, void *user_data) {
    g_ti_cb = on_result;
    g_ti_ud = user_data;
    g_shift = 0;
    g_layer = 0;
    g_ti_pw_visible = 0;      /* la contrasena arranca oculta */
    g_ti_eye_lbl = NULL;

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
    lv_obj_align(ttl, LV_ALIGN_TOP_LEFT, 8, 2);

    g_ti_textarea = lv_textarea_create(g_ti_overlay);
    lv_textarea_set_one_line(g_ti_textarea, true);
    lv_textarea_set_password_mode(g_ti_textarea, is_password ? true : false);
    /* Sin tiempo de "mostrar en claro": por defecto LVGL deja ver cada letra
     * ~1.5s y luego un timer la reemplaza por el bullet; ese redibujado tardio
     * del campo sobre el SPI por-pixel del KeDei es lo que se ve como que el
     * texto "se mueve"/parpadea y pesa por letra. Con 0 el bullet aparece de
     * una (un solo redibujo); para ver la clave esta el boton "ver". */
    lv_textarea_set_password_show_time(g_ti_textarea, 0);
    lv_obj_set_style_anim_duration(g_ti_textarea, 0, LV_PART_CURSOR);
    /* En modo password dejamos lugar a la derecha para el boton ver/ocultar. */
    lv_obj_set_size(g_ti_textarea, is_password ? 402 : 464, 30);
    lv_obj_align(g_ti_textarea, LV_ALIGN_TOP_LEFT, 8, 16);
    lv_obj_set_style_bg_color(g_ti_textarea, TI_BG, 0);
    lv_obj_set_style_text_color(g_ti_textarea, TI_OK, 0);
    lv_obj_set_style_border_color(g_ti_textarea, TI_OK, 0);
    lv_obj_set_style_border_width(g_ti_textarea, 2, 0);

    /* El cursor seguia parpadeando (y perdiendo el "foco" visual medio
     * segundo si y medio no) pese al anim_duration=0 de arriba: LVGL
     * arranca la animacion de parpadeo en el constructor del textarea
     * leyendo el anim_duration por defecto del tema, y fijarlo a 0 DESPUES
     * no la reinicia (en STYLE_CHANGED el textarea solo hace scroll, nunca
     * vuelve a llamar a start_cursor_blink). Reenviar FOCUSED fuerza a
     * start_cursor_blink a releer anim_duration=0 y BORRAR la animacion:
     * cursor fijo, sin parpadeo y sin el redibujo periodico que pesaba en
     * el SPI por-pixel del KeDei. */
    lv_obj_send_event(g_ti_textarea, LV_EVENT_FOCUSED, NULL);

    /* Boton ver/ocultar contrasena: solo en modo password. "ver" cuando esta
     * oculta (tocar para mostrarla), "ocu" cuando esta visible. */
    if (is_password) {
        lv_obj_t *eye = make_key(g_ti_overlay, "ver", 0, 0, 52, 30, TI_SPEC,
                                 key_eye_cb, NULL);
        lv_obj_align(eye, LV_ALIGN_TOP_RIGHT, -6, 16);
        g_ti_eye_lbl = lv_obj_get_child(eye, 0);
    }

    /* Contenedor del teclado propio */
    g_ti_kb = lv_obj_create(g_ti_overlay);
    lv_obj_set_size(g_ti_kb, 480, 268);
    lv_obj_align(g_ti_kb, LV_ALIGN_BOTTOM_MID, 0, 0);
    lv_obj_set_style_bg_opa(g_ti_kb, LV_OPA_TRANSP, 0);
    lv_obj_set_style_border_width(g_ti_kb, 0, 0);
    lv_obj_set_style_pad_all(g_ti_kb, 0, 0);
    lv_obj_clear_flag(g_ti_kb, LV_OBJ_FLAG_SCROLLABLE);

    ti_rebuild_keyboard();
}

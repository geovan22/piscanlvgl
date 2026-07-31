#ifndef UI_STYLE_H
#define UI_STYLE_H

#include "lvgl/lvgl.h"

/* ═══════════════════════════════════════════════════════
   ui_style.h — Helpers de estilo compartidos en toda la UI.
   Header-only (static inline) para no tener que enlazar un .c
   ni tocar el script de build.
   ═══════════════════════════════════════════════════════ */

/* Aplica un efecto visual de presion consistente a cualquier boton u
 * objeto clickeable: al presionar, el fondo se aclara a un verde mas
 * brillante y el borde tambien. Da feedback tactil claro en el panel
 * resistivo, donde el efecto por defecto del tema es demasiado sutil. */
static inline void ui_apply_press_effect(lv_obj_t *btn) {
    lv_obj_set_style_bg_color(btn, lv_color_hex(0x1f7a1f), LV_STATE_PRESSED);
    lv_obj_set_style_bg_opa(btn, LV_OPA_COVER, LV_STATE_PRESSED);
    lv_obj_set_style_border_color(btn, lv_color_hex(0x66FF66), LV_STATE_PRESSED);
}

/* Variante para botones "peligrosos" (deauth): al presionar se aclara
 * en rojo en vez de verde. */
static inline void ui_apply_press_effect_danger(lv_obj_t *btn) {
    lv_obj_set_style_bg_color(btn, lv_color_hex(0x7a1f1f), LV_STATE_PRESSED);
    lv_obj_set_style_bg_opa(btn, LV_OPA_COVER, LV_STATE_PRESSED);
    lv_obj_set_style_border_color(btn, lv_color_hex(0xFF6666), LV_STATE_PRESSED);
}

#endif

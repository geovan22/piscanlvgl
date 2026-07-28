#include <stdio.h>
#include <string.h>
#include <unistd.h>
#include "splash.h"
#include "db_client.h"

lv_obj_t *splash_show(const char *extra_text) {
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

    for (int i = 0; i < 3; i++) { lv_timer_handler(); usleep(2000); }
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

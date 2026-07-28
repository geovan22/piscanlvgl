#!/bin/bash
set -e
cd ~/piscanlvgl
gcc -O2 -I lvgl_sim/LvglPlatform -I platform/pi -I app/db_client \
    app/main.c app/pin_lock.c app/ui_shell.c app/confirm_dialog.c app/splash.c \
    platform/pi/kedei_display.c app/db_client/db_client.c \
    build/liblvgl.a -lcjson -lm -lpthread \
    -o build/piscan_main
echo "Exit code: $?"

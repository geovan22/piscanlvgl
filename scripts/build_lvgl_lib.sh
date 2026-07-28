#!/bin/bash
# Compila LVGL como biblioteca estatica una sola vez, en una ubicacion
# PERSISTENTE (no /tmp, que se limpia en cada reinicio).
# Correr de nuevo solo si cambia lv_conf.h o se actualiza LVGL.
set -e
cd ~/piscanlvgl
LVGL_SRC=lvgl_sim/LvglPlatform/lvgl/src
BUILD_DIR=~/piscanlvgl/build/lvgl_objs
mkdir -p "$BUILD_DIR"

echo "Buscando archivos fuente de LVGL..."
FILES=$(find "$LVGL_SRC" -name "*.c")
COUNT=$(echo "$FILES" | wc -l)
echo "Compilando $COUNT archivos..."

i=0
for f in $FILES; do
    i=$((i+1))
    obj="$BUILD_DIR/$(echo "$f" | tr '/' '_').o"
    gcc -O2 -c -I lvgl_sim/LvglPlatform -I platform/pi "$f" -o "$obj"
    if [ $((i % 50)) -eq 0 ]; then echo "  $i/$COUNT..."; fi
done

echo "Empaquetando liblvgl.a..."
ar rcs ~/piscanlvgl/build/liblvgl.a "$BUILD_DIR"/*.o
echo "Listo: ~/piscanlvgl/build/liblvgl.a"

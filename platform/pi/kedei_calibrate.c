/* ═══════════════════════════════════════════════════════
   kedei_calibrate.c — Herramienta standalone de calibracion tactil.
   Reemplaza el "cat /dev/shm/piscan_evt en loop" manual que usamos
   todo el tiempo en el Pi 1. Corre sola, sin LVGL ni el daemon completo.

   Uso:
     gcc -O2 -o kedei_calibrate kedei_calibrate.c kedei_display.c
     sudo ./kedei_calibrate

   Toca las 4 esquinas fisicas cuando se te pida. Al final imprime
   los 4 valores TCH_X_MIN/MAX y TCH_Y_MIN/MAX listos para usar como
   variables de entorno o para hardcodear en kedei_display.c
   ═══════════════════════════════════════════════════════ */
#include <stdio.h>
#include <unistd.h>
#include "kedei_display.h"

static int wait_for_touch(int *raw_x, int *raw_y, const char *label) {
    printf("\n>> Toca y sostene 1 segundo: %s\n", label);
    fflush(stdout);

    /* Esperar a que NO haya contacto (por si quedo un toque previo) */
    int px, py, rx, ry;
    while (kedei_touch_read_debug(&px, &py, &rx, &ry)) usleep(20000);

    /* Esperar el nuevo contacto */
    while (!kedei_touch_read_debug(&px, &py, &rx, &ry)) usleep(20000);

    /* Promediar unas muestras mientras se mantiene el contacto, para
     * reducir ruido — igual que ya haciamos a mano con las capturas
     * repetidas en el log del Pi 1 */
    int sum_x = 0, sum_y = 0, n = 0;
    for (int i = 0; i < 15 && kedei_touch_read_debug(&px, &py, &rx, &ry); i++) {
        sum_x += rx; sum_y += ry; n++;
        usleep(20000);
    }
    *raw_x = n ? sum_x / n : rx;
    *raw_y = n ? sum_y / n : ry;

    printf("   RAW capturado: rx=%d ry=%d (%d muestras)\n", *raw_x, *raw_y, n);

    /* Esperar a que se suelte, para no arrastrar el toque a la siguiente esquina */
    while (kedei_touch_read_debug(&px, &py, &rx, &ry)) usleep(20000);
    return 1;
}

int main(void) {
    if (kedei_platform_init() < 0) {
        fprintf(stderr, "No se pudo inicializar la pantalla/touch.\n");
        return 1;
    }

    printf("=== Calibracion tactil Kedei ===\n");
    kedei_test_draw_corners();
    printf("\n>> Mira la pantalla AHORA. Deberias ver:\n");
    printf("   ROJO arriba-izquierda, VERDE arriba-derecha,\n");
    printf("   AZUL abajo-izquierda, AMARILLO abajo-derecha.\n");
    printf(">> Presiona ENTER y decime que colores ves realmente en cada esquina.\n");
    getchar();
    printf("\nSe te va a pedir tocar las 4 esquinas FISICAS del panel, una por una.\n");

    int rx_tl, ry_tl, rx_tr, ry_tr, rx_bl, ry_bl, rx_br, ry_br;

    wait_for_touch(&rx_tl, &ry_tl, "esquina SUPERIOR IZQUIERDA");
    wait_for_touch(&rx_tr, &ry_tr, "esquina SUPERIOR DERECHA");
    wait_for_touch(&rx_bl, &ry_bl, "esquina INFERIOR IZQUIERDA");
    wait_for_touch(&rx_br, &ry_br, "esquina INFERIOR DERECHA");

    /* Segun la formula validada en el Pi 1: ry controla el eje X de pantalla,
     * rx controla el eje Y. Tomamos min/max reales de las 4 muestras para
     * cada eje, en vez de asumir cual esquina da cual extremo — asi esta
     * herramienta sigue funcionando aunque la orientacion (MADCTL) cambie
     * entre pruebas. */
    int ry_vals[4] = {ry_tl, ry_tr, ry_bl, ry_br};
    int rx_vals[4] = {rx_tl, rx_tr, rx_bl, rx_br};

    int ry_min = ry_vals[0], ry_max = ry_vals[0];
    int rx_min = rx_vals[0], rx_max = rx_vals[0];
    for (int i = 1; i < 4; i++) {
        if (ry_vals[i] < ry_min) ry_min = ry_vals[i];
        if (ry_vals[i] > ry_max) ry_max = ry_vals[i];
        if (rx_vals[i] < rx_min) rx_min = rx_vals[i];
        if (rx_vals[i] > rx_max) rx_max = rx_vals[i];
    }

    printf("\n=== Resultado ===\n");
    printf("RAW por esquina:\n");
    printf("  Sup-Izq:  rx=%d ry=%d\n", rx_tl, ry_tl);
    printf("  Sup-Der:  rx=%d ry=%d\n", rx_tr, ry_tr);
    printf("  Inf-Izq:  rx=%d ry=%d\n", rx_bl, ry_bl);
    printf("  Inf-Der:  rx=%d ry=%d\n", rx_br, ry_br);

    printf("\nValores calculados (ry -> eje X pantalla, rx -> eje Y pantalla):\n");
    printf("  TCH_Y_MIN=%d  TCH_Y_MAX=%d   (usados para el eje X)\n", ry_min, ry_max);
    printf("  TCH_X_MIN=%d  TCH_X_MAX=%d   (usados para el eje Y)\n", rx_min, rx_max);

    printf("\nPara probar sin recompilar:\n");
    printf("  KEDEI_TCH_X_MIN=%d KEDEI_TCH_X_MAX=%d KEDEI_TCH_Y_MIN=%d KEDEI_TCH_Y_MAX=%d ./tu_app\n",
           rx_min, rx_max, ry_min, ry_max);

    printf("\nSi el touch sigue sintiendose espejado con estos valores, no cambies\n");
    printf("mas la formula de calibracion — probá otro KEDEI_MADCTL (ver comentario\n");
    printf("en kedei_display.c) y volvé a correr esta herramienta desde cero.\n");

    return 0;
}

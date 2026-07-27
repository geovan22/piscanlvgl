/* ═══════════════════════════════════════════════════════
   kedei_display.c — Driver de bajo nivel Kedei 3.5 v5.0 + XPT2046
   Portado de piscan_daemon.c (Pi 1 B+), validado en hardware real.
   Provee flush_cb / read_cb para LVGL v9.

   Ajustable en runtime via variables de entorno (evita recompilar
   cada vez que se prueba un valor distinto, como nos paso en el Pi 1):
     KEDEI_SPI_SPEED   (default 32000000 — techo seguro en Pi 1/BCM2835,
                        REVALIDAR en Pi 3/BCM2837, no asumir igual)
     KEDEI_TCH_X_MIN / KEDEI_TCH_X_MAX
     KEDEI_TCH_Y_MIN / KEDEI_TCH_Y_MAX  (defaults = calibracion Pi 1)
   ═══════════════════════════════════════════════════════ */
#define _POSIX_C_SOURCE 199309L
#define _DEFAULT_SOURCE
#include <time.h>
#include <string.h>
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
#include <fcntl.h>
#include <sys/ioctl.h>
#include <linux/spi/spidev.h>
#include "kedei_display.h"
#include "lvgl/lvgl.h"

#define LCD_DEVICE   "/dev/spidev0.1"
#define TOUCH_DEVICE "/dev/spidev0.0"
#define LCD_WIDTH  480
#define LCD_HEIGHT 320

static const uint32_t mode  = 0;
static const uint8_t  bits  = 8;
/* 34MHz pedido -> ~33.33MHz reales (divisor 12 del core@400MHz del Pi 3).
 * Validado estable con touch funcionando. 42MHz (divisor 10, ~40MHz reales)
 * dejo la pantalla en blanco — no intentar saltos mas grandes sin retomar
 * el mismo proceso incremental que uso Geo para encontrar este valor. */
static uint32_t speed = 34000000;
static int spih = -1;
static int tch_fd = -1;
static const uint32_t tch_speed = 2000000;

/* Calibrado en el Pi 3 (misma pantalla fisica, chip touch distinto al Pi 1) */
static int TCH_X_MIN = 260, TCH_X_MAX = 1798;
static int TCH_Y_MIN = 211, TCH_Y_MAX = 1853;

/* Zona segura para elementos tactiles: el panel resistivo pierde linealidad
 * cerca de los bordes (confirmado empiricamente, mas marcado en el borde
 * inferior). NO poner botones/elementos interactivos mas cerca que esto
 * del borde fisico de la pantalla al disenar screens en app/. Los primeros
 * milimetros existen, pero el touch ahi es menos preciso — dejarlos para
 * fondos/decoracion, no para hit-targets. */
#define KEDEI_SAFE_MARGIN_X   15  /* px, bordes izquierdo/derecho */
#define KEDEI_SAFE_MARGIN_TOP 15  /* px, borde superior */
#define KEDEI_SAFE_MARGIN_BOT 25  /* px, borde inferior — mas margen, peor comportamiento ahi */

/* MADCTL — orientacion/espejo del panel. El Pi 1 usaba 0b10101010 (170),
 * que resulto espejada en X (de ahi todo el dolor de cabeza con el mirror
 * en cada hit-test). Las 4 opciones originales del lcd_rotations[] eran:
 * 0b10101010=170, 0b01001010=74, 0b00101010=42, 0b10001010=138.
 * Default = la misma del Pi 1 para no romper nada al primer intento,
 * pero la idea es PROBAR LAS OTRAS 3 con KEDEI_MADCTL antes de calibrar
 * el touch, a ver si alguna da orientacion derecha sin necesitar espejo. */
static int madctl_value = 0b00101010; /* 0x2A — confirmado sin espejo en el Pi 3 */

static void load_env_overrides(void) {
    const char *v;
    if ((v = getenv("KEDEI_SPI_SPEED")))  speed        = (uint32_t)atol(v);
    if ((v = getenv("KEDEI_TCH_X_MIN")))  TCH_X_MIN    = atoi(v);
    if ((v = getenv("KEDEI_TCH_X_MAX")))  TCH_X_MAX    = atoi(v);
    if ((v = getenv("KEDEI_TCH_Y_MIN")))  TCH_Y_MIN    = atoi(v);
    if ((v = getenv("KEDEI_TCH_Y_MAX")))  TCH_Y_MAX    = atoi(v);
    if ((v = getenv("KEDEI_MADCTL")))     madctl_value = atoi(v);
    printf("[kedei] speed=%u tch_x=[%d,%d] tch_y=[%d,%d] madctl=0x%02X\n",
           speed, TCH_X_MIN, TCH_X_MAX, TCH_Y_MIN, TCH_Y_MAX, madctl_value);
}

static void delayms(int ms) {
    struct timespec req;
    req.tv_sec = 0;
    req.tv_nsec = ms * 1000000L;
    nanosleep(&req, NULL);
}

static int spi_transmit(uint8_t *data, int len) {
    struct spi_ioc_transfer tr = {
        .tx_buf = (unsigned long)data, .rx_buf = (unsigned long)NULL,
        .len = len, .speed_hz = speed, .bits_per_word = bits
    };
    return ioctl(spih, SPI_IOC_MESSAGE(1), &tr);
}

static void lcd_cmd(uint8_t cmd) {
    uint8_t b[2];
    b[0]=cmd>>1; b[1]=((cmd&1)<<5)|0x11; spi_transmit(b,2);
    b[0]=cmd>>1; b[1]=((cmd&1)<<5)|0x1B; spi_transmit(b,2);
}

static void lcd_data(uint8_t dat) {
    uint8_t b[2];
    b[0]=dat>>1; b[1]=((dat&1)<<5)|0x15; spi_transmit(b,2);
    b[0]=dat>>1; b[1]=((dat&1)<<5)|0x1F; spi_transmit(b,2);
}

static void lcd_color(uint16_t col) {
    uint8_t b[3];
    uint8_t pseud = ((col>>5)&0x40)|((col<<5)&0x20);
    b[0]=col>>8; b[1]=col&0xFF; b[2]=pseud|0x15; spi_transmit(b,3);
    b[0]=col>>8; b[1]=col&0xFF; b[2]=pseud|0x1F; spi_transmit(b,3);
}

static void lcd_setframe(uint16_t x, uint16_t y, uint16_t w, uint16_t h) {
    lcd_cmd(0x2A);
    lcd_data(x>>8); lcd_data(x&0xFF);
    lcd_data(((w+x)-1)>>8); lcd_data(((w+x)-1)&0xFF);
    lcd_cmd(0x2B);
    lcd_data(y>>8); lcd_data(y&0xFF);
    lcd_data(((h+y)-1)>>8); lcd_data(((h+y)-1)&0xFF);
    lcd_cmd(0x2C);
}

static void lcd_fillRGB(uint8_t r, uint8_t g, uint8_t b) {
    uint16_t col = ((r<<8)&0xF800)|((g<<3)&0x07E0)|((b>>3)&0x001F);
    int span = LCD_WIDTH*LCD_HEIGHT, q;
    lcd_setframe(0,0,LCD_WIDTH,LCD_HEIGHT);
    for (q=0;q<span;q++) lcd_color(col);
}

static void lcd_init_sequence(void) {
    uint8_t r=0x00; spi_transmit(&r,1); delayms(150);
    r=0x01;         spi_transmit(&r,1); delayms(250);

    lcd_cmd(0x00);
    lcd_cmd(0x11); delayms(200);
    lcd_cmd(0xEE); lcd_data(0x02);lcd_data(0x01);lcd_data(0x02);lcd_data(0x01);
    lcd_cmd(0xED);
        lcd_data(0x00);lcd_data(0x00);lcd_data(0x9A);lcd_data(0x9A);
        lcd_data(0x9B);lcd_data(0x9B);lcd_data(0x00);lcd_data(0x00);
        lcd_data(0x00);lcd_data(0x00);lcd_data(0xAE);lcd_data(0xAE);
        lcd_data(0x01);lcd_data(0xA2);lcd_data(0x00);
    lcd_cmd(0xB4); lcd_data(0x00);
    lcd_cmd(0xC0); lcd_data(0x10);lcd_data(0x3B);lcd_data(0x00);lcd_data(0x02);lcd_data(0x11);
    lcd_cmd(0xC1); lcd_data(0x10);
    lcd_cmd(0xC8);
        lcd_data(0x00);lcd_data(0x46);lcd_data(0x12);lcd_data(0x20);
        lcd_data(0x0C);lcd_data(0x00);lcd_data(0x56);lcd_data(0x12);
        lcd_data(0x67);lcd_data(0x02);lcd_data(0x00);lcd_data(0x0C);
    lcd_cmd(0xD0); lcd_data(0x44);lcd_data(0x42);lcd_data(0x06);
    lcd_cmd(0xD1); lcd_data(0x43);lcd_data(0x16);
    lcd_cmd(0xD2); lcd_data(0x04);lcd_data(0x22);
    lcd_cmd(0xD3); lcd_data(0x04);lcd_data(0x12);
    lcd_cmd(0xD4); lcd_data(0x07);lcd_data(0x12);
    lcd_cmd(0xE9); lcd_data(0x00);
    lcd_cmd(0xC5); lcd_data(0x08);
    /* MADCTL — orientacion. 0b10101010 es la que usaba el Pi 1 (con mirror).
     * TODO al calibrar en Pi 3: probar tambien 0b01001010 / 0b00101010 / 0b10001010
     * (las 4 opciones del lcd_rotations[] original) a ver si alguna da
     * orientacion NO espejada, evitando el problema que nos persiguio
     * todo el proyecto anterior. Ver KEDEI_MADCTL abajo para probar sin recompilar. */
    lcd_cmd(0x36); lcd_data((uint8_t)madctl_value);
    lcd_cmd(0x3A); lcd_data(0x66);
    lcd_cmd(0x35); lcd_data(0x00);
    lcd_cmd(0x29); delayms(200);
    lcd_cmd(0x00);
    lcd_cmd(0x11); delayms(200);
    lcd_cmd(0xEE); lcd_data(0x02);lcd_data(0x01);lcd_data(0x02);lcd_data(0x01);
    lcd_cmd(0xED);
        lcd_data(0x00);lcd_data(0x00);lcd_data(0x9A);lcd_data(0x9A);
        lcd_data(0x9B);lcd_data(0x9B);lcd_data(0x00);lcd_data(0x00);
        lcd_data(0x00);lcd_data(0x00);lcd_data(0xAE);lcd_data(0xAF);
        lcd_data(0x01);lcd_data(0xA2);lcd_data(0x01);lcd_data(0xBF);lcd_data(0x2A);
}

static int tch_read_chan(uint8_t cmd) {
    uint8_t tx[3]={cmd,0,0}, rx[3]={0};
    struct spi_ioc_transfer tr = {
        .tx_buf=(unsigned long)tx, .rx_buf=(unsigned long)rx,
        .len=3, .speed_hz=tch_speed, .bits_per_word=bits
    };
    if (ioctl(tch_fd, SPI_IOC_MESSAGE(1), &tr) < 0) return -1;
    return ((rx[1]<<8)|rx[2])>>4;
}

static int touch_read_raw(int *px, int *py, int *raw_x, int *raw_y) {
    if (tch_fd < 0) return 0;
    int z1 = tch_read_chan(0xB0);
    int z2 = tch_read_chan(0xC0);
    if (z1 < 100 || z2 > 3900) return 0;

    int rx=0, ry=0, i;
    for (i=0;i<4;i++) { rx+=tch_read_chan(0xD0); ry+=tch_read_chan(0x90); }
    rx/=4; ry/=4;
    if (raw_x) *raw_x = rx;
    if (raw_y) *raw_y = ry;

    int ax = ry;
    int ay = rx;
    *px = (ax - TCH_Y_MIN) * LCD_WIDTH  / (TCH_Y_MAX - TCH_Y_MIN);
    *py = (ay - TCH_X_MIN) * LCD_HEIGHT / (TCH_X_MAX - TCH_X_MIN);
    *py = LCD_HEIGHT - 1 - *py;

    if (*px < 0) *px=0; if (*px >= LCD_WIDTH)  *px=LCD_WIDTH-1;
    if (*py < 0) *py=0; if (*py >= LCD_HEIGHT) *py=LCD_HEIGHT-1;
    return 1;
}

int kedei_platform_init(void) {
    load_env_overrides();

    spih = open(LCD_DEVICE, O_WRONLY);
    if (spih < 0) { perror("kedei: open LCD"); return -1; }
    if (ioctl(spih, SPI_IOC_WR_MODE32,        &mode) < 0) return -1;
    if (ioctl(spih, SPI_IOC_WR_BITS_PER_WORD, &bits) < 0) return -1;
    if (ioctl(spih, SPI_IOC_WR_MAX_SPEED_HZ,  &speed) < 0) return -1;

    tch_fd = open(TOUCH_DEVICE, O_RDWR);
    if (tch_fd < 0) {
        perror("kedei: open touch (no fatal)");
    } else {
        ioctl(tch_fd, SPI_IOC_WR_MODE32,        &mode);
        ioctl(tch_fd, SPI_IOC_WR_BITS_PER_WORD, &bits);
        ioctl(tch_fd, SPI_IOC_WR_MAX_SPEED_HZ,  &tch_speed);
    }

    lcd_init_sequence();
    lcd_fillRGB(0,0,0);
    return 0;
}


void kedei_flush_cb(lv_display_t *disp, const lv_area_t *area, uint8_t *px_map) {
    uint16_t w = (uint16_t)(area->x2 - area->x1 + 1);
    uint16_t h = (uint16_t)(area->y2 - area->y1 + 1);
    uint16_t *px16 = (uint16_t *)px_map;
    uint32_t count = (uint32_t)w * h;

    lcd_setframe((uint16_t)area->x1, (uint16_t)area->y1, w, h);
    for (uint32_t i = 0; i < count; i++) {
        lcd_color(px16[i]);
    }
    lv_display_flush_ready(disp);
}

void kedei_touch_read_cb(lv_indev_t *indev, lv_indev_data_t *data) {
    (void)indev;
    int px, py;
    if (touch_read_raw(&px, &py, NULL, NULL)) {
        data->point.x = px;
        data->point.y = py;
        data->state = LV_INDEV_STATE_PRESSED;
    } else {
        data->state = LV_INDEV_STATE_RELEASED;
    }
}

/* Dibuja 4 rectangulos de colores distintos, uno en cada esquina fisica,
 * para verificar visualmente orientacion/espejo ANTES de calibrar el touch.
 * No depende de LVGL ni de fuentes de texto — solo colores planos. */
void kedei_test_draw_corners(void) {
    int sq = 60; /* tamano del cuadrado, en px */
    lcd_fillRGB(0, 0, 0); /* limpiar todo a negro primero */

    /* Rojo = arriba-izquierda */
    lcd_setframe(0, 0, sq, sq);
    for (int i = 0; i < sq*sq; i++) lcd_color(0xF800); /* rojo puro RGB565 */

    /* Verde = arriba-derecha */
    lcd_setframe(LCD_WIDTH - sq, 0, sq, sq);
    for (int i = 0; i < sq*sq; i++) lcd_color(0x07E0); /* verde puro */

    /* Azul = abajo-izquierda */
    lcd_setframe(0, LCD_HEIGHT - sq, sq, sq);
    for (int i = 0; i < sq*sq; i++) lcd_color(0x001F); /* azul puro */

    /* Amarillo = abajo-derecha */
    lcd_setframe(LCD_WIDTH - sq, LCD_HEIGHT - sq, sq, sq);
    for (int i = 0; i < sq*sq; i++) lcd_color(0xFFE0); /* amarillo puro */
}

/* Expuesto para la herramienta de calibracion standalone */
int kedei_touch_read_debug(int *px, int *py, int *raw_x, int *raw_y) {
    return touch_read_raw(px, py, raw_x, raw_y);
}

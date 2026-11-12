#ifndef REPORT_CLIENT_H
#define REPORT_CLIENT_H

/* Puente C -> app/db_tool.py report {generate,list,show,delete}
 * (Fase 8). Todas devuelven 1 en exito, 0 en fallo. */

#define REPORT_PATH_MAX   256
#define REPORT_SUMMARY_MAX 384
#define REPORT_LABEL_MAX  128
#define REPORT_LIST_MAX    64

typedef struct {
    int  id;
    int  size_bytes;
    char window[8];                  /* "24h" | "7d" | "all" */
    char ts[24];                     /* YYYY-MM-DD HH:MM:SS */
    char path[REPORT_PATH_MAX];
    char summary[REPORT_SUMMARY_MAX];
    char label[REPORT_LABEL_MAX];    /* linea formateada lista para la UI */
} report_meta_t;

/* Genera un reporte Markdown de la ventana ("24h" | "7d" | "all"). include_creds
 * != 0 opt-in para incluir portal_user/portal_pass del honeypot (default: NO).
 * Si out != NULL, se llena con el meta de la fila creada. */
int report_client_generate(const char *window, int include_creds,
                           report_meta_t *out,
                           char *out_error, int error_size);

/* Trae hasta `max` filas de audit_report (mas recientes primero). Retorna
 * cuantas filas escribio. */
int report_client_list(report_meta_t out[], int max);

/* Lee el .md del reporte `id` a `out_text` (truncado a `out_size - 1`). */
int report_client_show(int id, char *out_text, int out_size,
                       char *out_path, int path_size);

/* Borra el .md y la fila. */
int report_client_delete(int id);

#endif

#ifndef PROC_BRIDGE_H
#define PROC_BRIDGE_H

#include <cjson/cJSON.h>

/* Puente C -> script Python, compartido por todos los *_client.c
 * (db/wifi/net/bt/report). Patron fork + exec + pipe, sin shell (evita
 * inyeccion: argv se pasa tal cual a execvp). */

/* Ejecuta argv[] y captura TODO su stdout. Devuelve un buffer malloc'd
 * (el que llama lo libera con free) terminado en '\0', o NULL si fallo el
 * pipe/fork. argv[0] se resuelve por PATH (execvp). */
char *piscan_run_capture(char *const argv[]);

/* Parsea `raw` como JSON. En exito deja *out_root con el objeto (liberar con
 * cJSON_Delete) y retorna 1 SOLO si ademas "ok" == true. En cualquier otro
 * caso retorna 0 y *out_root = NULL (si el root existia pero ok era falso, lo
 * borra antes de volver). */
int piscan_parse_ok_json(const char *raw, cJSON **out_root);

#endif

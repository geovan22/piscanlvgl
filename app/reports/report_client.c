/* ═══════════════════════════════════════════════════════
   report_client.c — Puente C -> app/db_tool.py report ... (Fase 8).
   Patron fork+exec+pipe + parse cJSON, igual que db_client.
   ═══════════════════════════════════════════════════════ */
#define _POSIX_C_SOURCE 200809L
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <sys/wait.h>
#include <cjson/cJSON.h>
#include "report_client.h"
#include "proc_bridge.h"

#ifndef PISCAN_DB_TOOL_PATH
#define PISCAN_DB_TOOL_PATH "/home/geo22/piscanlvgl/app/db_tool.py"
#endif

/* Delegan en el helper compartido (app/common/proc_bridge.c); nombres locales
 * conservados para no tocar los call sites de abajo. */
static char *run_and_capture(char *const argv[]) {
    return piscan_run_capture(argv);
}

static int parse_ok_json(const char *raw, cJSON **out_root) {
    return piscan_parse_ok_json(raw, out_root);
}

/* Arma una cadena corta "WiFi:X/Y · HP:S/C · BT:D/A/S · Sys:N(e)" con los
 * conteos del objeto summary del reporte. */
static void summarize(cJSON *summary, char *out, int out_size) {
    if (!summary || !cJSON_IsObject(summary)) { out[0] = '\0'; return; }
    int wscans   = 0, wattacks = 0;
    int hpses    = 0, hpcli   = 0;
    int btd      = 0, bta     = 0, bts = 0;
    int acts     = 0, aerr    = 0;
    cJSON *v;
    v = cJSON_GetObjectItemCaseSensitive(summary, "wifi_scans");
    if (cJSON_IsNumber(v)) wscans = v->valueint;
    v = cJSON_GetObjectItemCaseSensitive(summary, "wifi_attacks");
    if (cJSON_IsNumber(v)) wattacks = v->valueint;
    v = cJSON_GetObjectItemCaseSensitive(summary, "hp_sessions");
    if (cJSON_IsNumber(v)) hpses = v->valueint;
    v = cJSON_GetObjectItemCaseSensitive(summary, "hp_clients");
    if (cJSON_IsNumber(v)) hpcli = v->valueint;
    v = cJSON_GetObjectItemCaseSensitive(summary, "bt_devices");
    if (cJSON_IsNumber(v)) btd = v->valueint;
    v = cJSON_GetObjectItemCaseSensitive(summary, "bt_attacks");
    if (cJSON_IsNumber(v)) bta = v->valueint;
    v = cJSON_GetObjectItemCaseSensitive(summary, "bt_sightings");
    if (cJSON_IsNumber(v)) bts = v->valueint;
    v = cJSON_GetObjectItemCaseSensitive(summary, "actions");
    if (cJSON_IsNumber(v)) acts = v->valueint;
    v = cJSON_GetObjectItemCaseSensitive(summary, "action_errors");
    if (cJSON_IsNumber(v)) aerr = v->valueint;
    snprintf(out, out_size,
             "WiFi %d/%d  HP %d/%d  BT %d/%d/%d  Sys %d(%d err)",
             wscans, wattacks, hpses, hpcli, btd, bta, bts, acts, aerr);
}

static void fill_meta(cJSON *rep, report_meta_t *m) {
    if (!rep || !m) return;
    memset(m, 0, sizeof(*m));
    cJSON *id   = cJSON_GetObjectItemCaseSensitive(rep, "id");
    cJSON *sz   = cJSON_GetObjectItemCaseSensitive(rep, "size_bytes");
    cJSON *win  = cJSON_GetObjectItemCaseSensitive(rep, "window");
    cJSON *ts   = cJSON_GetObjectItemCaseSensitive(rep, "ts");
    cJSON *path = cJSON_GetObjectItemCaseSensitive(rep, "path");
    cJSON *sum  = cJSON_GetObjectItemCaseSensitive(rep, "summary");
    if (cJSON_IsNumber(id)) m->id = id->valueint;
    if (cJSON_IsNumber(sz)) m->size_bytes = sz->valueint;
    if (cJSON_IsString(win) && win->valuestring)
        snprintf(m->window, sizeof(m->window), "%s", win->valuestring);
    if (cJSON_IsString(ts) && ts->valuestring)
        snprintf(m->ts, sizeof(m->ts), "%s", ts->valuestring);
    if (cJSON_IsString(path) && path->valuestring)
        snprintf(m->path, sizeof(m->path), "%s", path->valuestring);
    summarize(sum, m->summary, sizeof(m->summary));
    snprintf(m->label, sizeof(m->label), "#%d  %s  [%s]  %s",
             m->id, m->ts, m->window, m->summary);
}

int report_client_generate(const char *window, int include_creds,
                           report_meta_t *out,
                           char *out_error, int error_size) {
    char *argv_path = PISCAN_DB_TOOL_PATH;
    /* python3 path report generate <window> [--creds] */
    char *argv[8];
    int i = 0;
    argv[i++] = "python3";
    argv[i++] = argv_path;
    argv[i++] = "report";
    argv[i++] = "generate";
    argv[i++] = (char *)(window ? window : "24h");
    if (include_creds) argv[i++] = "--creds";
    argv[i] = NULL;

    char *raw = run_and_capture(argv);
    if (!raw) {
        if (out_error) snprintf(out_error, error_size, "no se pudo ejecutar db_tool.py");
        return 0;
    }
    cJSON *root = cJSON_Parse(raw);
    free(raw);
    if (!root) {
        if (out_error) snprintf(out_error, error_size, "JSON invalido");
        return 0;
    }
    cJSON *ok = cJSON_GetObjectItemCaseSensitive(root, "ok");
    int success = cJSON_IsTrue(ok) ? 1 : 0;
    if (!success) {
        if (out_error) {
            cJSON *er = cJSON_GetObjectItemCaseSensitive(root, "error");
            snprintf(out_error, error_size, "%s",
                     cJSON_IsString(er) ? er->valuestring : "error");
        }
        cJSON_Delete(root);
        return 0;
    }
    if (out) {
        cJSON *rep = cJSON_GetObjectItemCaseSensitive(root, "report");
        fill_meta(rep, out);
    }
    cJSON_Delete(root);
    return 1;
}

int report_client_list(report_meta_t out[], int max) {
    char *argv_path = PISCAN_DB_TOOL_PATH;
    char limstr[16];
    snprintf(limstr, sizeof(limstr), "%d", max > 0 ? max : 50);
    char *argv[] = { "python3", argv_path, "report", "list", limstr, NULL };
    char *raw = run_and_capture(argv);
    cJSON *root;
    int count = 0;
    if (parse_ok_json(raw, &root)) {
        cJSON *items = cJSON_GetObjectItemCaseSensitive(root, "items");
        cJSON *it;
        cJSON_ArrayForEach(it, items) {
            if (count >= max) break;
            fill_meta(it, &out[count]);
            count++;
        }
        cJSON_Delete(root);
    }
    free(raw);
    return count;
}

int report_client_show(int id, char *out_text, int out_size,
                       char *out_path, int path_size) {
    char idstr[16];
    snprintf(idstr, sizeof(idstr), "%d", id);
    char maxstr[32];
    /* Pedimos hasta out_size-1 chars; si el archivo es mas grande, el Python
     * trunca y pone "[truncado]" al final. */
    snprintf(maxstr, sizeof(maxstr), "--max-text=%d",
             out_size > 1 ? (out_size - 128) : 4096);
    char *argv[] = { "python3", PISCAN_DB_TOOL_PATH,
                     "report", "show", idstr, maxstr, NULL };
    char *raw = run_and_capture(argv);
    cJSON *root;
    int ok = 0;
    if (out_text && out_size > 0) out_text[0] = '\0';
    if (out_path && path_size > 0) out_path[0] = '\0';
    if (parse_ok_json(raw, &root)) {
        cJSON *rep = cJSON_GetObjectItemCaseSensitive(root, "report");
        if (rep) {
            cJSON *txt  = cJSON_GetObjectItemCaseSensitive(rep, "text");
            cJSON *path = cJSON_GetObjectItemCaseSensitive(rep, "path");
            if (out_text && cJSON_IsString(txt) && txt->valuestring) {
                snprintf(out_text, out_size, "%s", txt->valuestring);
            }
            if (out_path && cJSON_IsString(path) && path->valuestring) {
                snprintf(out_path, path_size, "%s", path->valuestring);
            }
            ok = 1;
        }
        cJSON_Delete(root);
    }
    free(raw);
    return ok;
}

int report_client_delete(int id) {
    char idstr[16];
    snprintf(idstr, sizeof(idstr), "%d", id);
    char *argv[] = { "python3", PISCAN_DB_TOOL_PATH,
                     "report", "delete", idstr, NULL };
    char *raw = run_and_capture(argv);
    cJSON *root;
    int ok = parse_ok_json(raw, &root);
    if (ok) cJSON_Delete(root);
    free(raw);
    return ok;
}

/* ═══════════════════════════════════════════════════════
   db_client.c — Puente C -> db_tool.py (SQLAlchemy) via fork+exec+pipe.
   Usa fork/exec en vez de popen() para no tener que escapar argumentos
   a mano en un string de shell (mas seguro, evita injection por accidente
   si algun valor de config llegara a tener caracteres raros).
   ═══════════════════════════════════════════════════════ */
#define _POSIX_C_SOURCE 200809L
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <sys/wait.h>
#include <cjson/cJSON.h>
#include "db_client.h"

#ifndef PISCAN_DB_TOOL_PATH
#define PISCAN_DB_TOOL_PATH "/home/geo22/piscanlvgl/app/db_tool.py"
#endif

/* Ejecuta argv[] y captura su stdout completo. NULL si fallo el fork/exec. */
static char *run_and_capture(char *const argv[]) {
    int pipefd[2];
    if (pipe(pipefd) != 0) return NULL;

    pid_t pid = fork();
    if (pid < 0) { close(pipefd[0]); close(pipefd[1]); return NULL; }

    if (pid == 0) {
        close(pipefd[0]);
        dup2(pipefd[1], STDOUT_FILENO);
        close(pipefd[1]);
        execvp(argv[0], argv);
        _exit(127); /* solo si execvp fallo */
    }

    close(pipefd[1]);
    size_t cap = 4096, len = 0;
    char *buf = malloc(cap);
    if (!buf) { close(pipefd[0]); waitpid(pid, NULL, 0); return NULL; }

    ssize_t n;
    while ((n = read(pipefd[0], buf + len, cap - len - 1)) > 0) {
        len += (size_t)n;
        if (len + 1 >= cap) {
            cap *= 2;
            char *nb = realloc(buf, cap);
            if (!nb) { free(buf); close(pipefd[0]); waitpid(pid, NULL, 0); return NULL; }
            buf = nb;
        }
    }
    buf[len] = '\0';
    close(pipefd[0]);
    waitpid(pid, NULL, 0);
    return buf;
}

/* Parsea la salida JSON. *out_root queda con el objeto parseado (liberar con cJSON_Delete).
 * Retorna 1 si el JSON es valido Y "ok" es true, 0 en cualquier otro caso. */
static int parse_ok_json(const char *raw, cJSON **out_root) {
    *out_root = NULL;
    if (!raw) return 0;
    cJSON *root = cJSON_Parse(raw);
    if (!root) return 0;
    cJSON *ok = cJSON_GetObjectItemCaseSensitive(root, "ok");
    if (!cJSON_IsTrue(ok)) { cJSON_Delete(root); return 0; }
    *out_root = root;
    return 1;
}

int db_config_get(const char *key, char *value_out, int value_out_size) {
    char *argv_path = PISCAN_DB_TOOL_PATH;
    char *argv[] = { "python3", argv_path, "config", "get", (char *)key, NULL };
    char *raw = run_and_capture(argv);
    cJSON *root;
    int result = 0;
    if (parse_ok_json(raw, &root)) {
        cJSON *value = cJSON_GetObjectItemCaseSensitive(root, "value");
        if (cJSON_IsString(value) && value->valuestring) {
            snprintf(value_out, value_out_size, "%s", value->valuestring);
            result = 1;
        }
        cJSON_Delete(root);
    }
    free(raw);
    return result;
}

int db_config_set(const char *key, const char *value, const char *category,
                  char *out_error, int error_size) {
    char *argv_path = PISCAN_DB_TOOL_PATH;
    char *argv[7];
    int i = 0;
    argv[i++] = "python3";
    argv[i++] = argv_path;
    argv[i++] = "config";
    argv[i++] = "set";
    argv[i++] = (char *)key;
    argv[i++] = (char *)value;
    if (category) argv[i++] = (char *)category;
    argv[i] = NULL;

    char *raw = run_and_capture(argv);
    if (!raw) {
        if (out_error) snprintf(out_error, error_size, "no se pudo ejecutar db_tool.py");
        return 0;
    }
    cJSON *root = cJSON_Parse(raw);
    free(raw);
    if (!root) {
        if (out_error) snprintf(out_error, error_size, "JSON invalido de db_tool.py");
        return 0;
    }
    cJSON *ok = cJSON_GetObjectItemCaseSensitive(root, "ok");
    int success = cJSON_IsTrue(ok) ? 1 : 0;
    if (!success && out_error) {
        cJSON *err = cJSON_GetObjectItemCaseSensitive(root, "error");
        snprintf(out_error, error_size, "%s",
                 cJSON_IsString(err) ? err->valuestring : "error desconocido");
    }
    cJSON_Delete(root);
    return success;
}

int db_config_list(const char *category, char keys[][64], char vals[][128], int max) {
    char *argv[6];
    int i = 0;
    argv[i++] = "python3";
    argv[i++] = PISCAN_DB_TOOL_PATH;
    argv[i++] = "config";
    argv[i++] = "list";
    if (category) argv[i++] = (char *)category;
    argv[i] = NULL;

    char *raw = run_and_capture(argv);
    cJSON *root;
    int count = 0;
    if (parse_ok_json(raw, &root)) {
        cJSON *items = cJSON_GetObjectItemCaseSensitive(root, "items");
        cJSON *it;
        cJSON_ArrayForEach(it, items) {
            if (count >= max) break;
            cJSON *k = cJSON_GetObjectItemCaseSensitive(it, "key");
            cJSON *v = cJSON_GetObjectItemCaseSensitive(it, "value");
            if (cJSON_IsString(k) && k->valuestring) {
                snprintf(keys[count], 64, "%s", k->valuestring);
                snprintf(vals[count], 128, "%s",
                         (cJSON_IsString(v) && v->valuestring) ? v->valuestring : "");
                count++;
            }
        }
        cJSON_Delete(root);
    }
    free(raw);
    return count;
}

int db_config_set_many(const char *category, const char *const *keys,
                       const char *const *vals, int n,
                       char *out_error, int error_size) {
    if (n <= 0) return 1;   /* nada que guardar */
    /* argv: python3, path, config, setmany, <cat>, k,v,k,v..., NULL */
    int argc = 5 + n * 2 + 1;
    char **argv = malloc(sizeof(char *) * (size_t)argc);
    if (!argv) {
        if (out_error) snprintf(out_error, error_size, "malloc");
        return 0;
    }
    int i = 0;
    argv[i++] = "python3";
    argv[i++] = PISCAN_DB_TOOL_PATH;
    argv[i++] = "config";
    argv[i++] = "setmany";
    argv[i++] = (char *)(category ? category : "");
    for (int k = 0; k < n; k++) {
        argv[i++] = (char *)keys[k];
        argv[i++] = (char *)vals[k];
    }
    argv[i] = NULL;

    char *raw = run_and_capture(argv);
    free(argv);
    if (!raw) {
        if (out_error) snprintf(out_error, error_size, "no se pudo ejecutar db_tool.py");
        return 0;
    }
    cJSON *root = cJSON_Parse(raw);
    free(raw);
    if (!root) {
        if (out_error) snprintf(out_error, error_size, "JSON invalido de db_tool.py");
        return 0;
    }
    cJSON *ok = cJSON_GetObjectItemCaseSensitive(root, "ok");
    int success = cJSON_IsTrue(ok) ? 1 : 0;
    if (!success && out_error) {
        cJSON *er = cJSON_GetObjectItemCaseSensitive(root, "error");
        snprintf(out_error, error_size, "%s",
                 cJSON_IsString(er) ? er->valuestring : "error desconocido");
    }
    cJSON_Delete(root);
    return success;
}

int db_credential_set(const char *cred_type, const char *plaintext) {
    char *argv_path = PISCAN_DB_TOOL_PATH;
    char *argv[] = { "python3", argv_path, "credential", "set", (char *)cred_type, (char *)plaintext, NULL };
    char *raw = run_and_capture(argv);
    cJSON *root;
    int result = parse_ok_json(raw, &root);
    if (result) cJSON_Delete(root);
    free(raw);
    return result;
}

int db_credential_verify(const char *cred_type, const char *plaintext) {
    char *argv_path = PISCAN_DB_TOOL_PATH;
    char *argv[] = { "python3", argv_path, "credential", "verify", (char *)cred_type, (char *)plaintext, NULL };
    char *raw = run_and_capture(argv);
    cJSON *root;
    int result = 0;
    if (parse_ok_json(raw, &root)) {
        cJSON *valid = cJSON_GetObjectItemCaseSensitive(root, "valid");
        result = cJSON_IsTrue(valid) ? 1 : 0;
        cJSON_Delete(root);
    }
    free(raw);
    return result;
}

int db_credential_exists(const char *cred_type) {
    char *argv_path = PISCAN_DB_TOOL_PATH;
    char *argv[] = { "python3", argv_path, "credential", "exists", (char *)cred_type, NULL };
    char *raw = run_and_capture(argv);
    cJSON *root;
    int result = 0;
    if (parse_ok_json(raw, &root)) {
        cJSON *exists = cJSON_GetObjectItemCaseSensitive(root, "exists");
        result = cJSON_IsTrue(exists) ? 1 : 0;
        cJSON_Delete(root);
    }
    free(raw);
    return result;
}

int db_action_recent(int limit, int errors_only, char *out, int out_size) {
    char *argv_path = PISCAN_DB_TOOL_PATH;
    char lim_str[16];
    snprintf(lim_str, sizeof(lim_str), "%d", limit > 0 ? limit : 20);
    char *argv_normal[]  = { "python3", argv_path, "action", "list", lim_str, NULL };
    char *argv_err[]     = { "python3", argv_path, "action", "list", lim_str, "--errors", NULL };
    char *raw = run_and_capture(errors_only ? argv_err : argv_normal);
    cJSON *root = NULL;
    int ok = parse_ok_json(raw, &root);
    out[0] = '\0';
    if (!ok) {
        free(raw);
        if (root) cJSON_Delete(root);
        return 0;
    }
    cJSON *items = cJSON_GetObjectItemCaseSensitive(root, "items");
    size_t used = 0;
    if (cJSON_IsArray(items)) {
        cJSON *it;
        cJSON_ArrayForEach(it, items) {
            cJSON *section = cJSON_GetObjectItemCaseSensitive(it, "section");
            cJSON *tool    = cJSON_GetObjectItemCaseSensitive(it, "tool");
            cJSON *result  = cJSON_GetObjectItemCaseSensitive(it, "result");
            cJSON *inputs  = cJSON_GetObjectItemCaseSensitive(it, "inputs");
            cJSON *errj    = cJSON_GetObjectItemCaseSensitive(it, "error");
            cJSON *dur     = cJSON_GetObjectItemCaseSensitive(it, "duration_ms");
            cJSON *ts      = cJSON_GetObjectItemCaseSensitive(it, "ts");
            const char *rs = cJSON_IsString(result) ? result->valuestring : "?";
            const char *tag = (strcmp(rs, "ok") == 0) ? "ok " : "ERR";
            const char *sec = cJSON_IsString(section) ? section->valuestring : "?";
            const char *tl  = cJSON_IsString(tool)    ? tool->valuestring    : "?";
            const char *in  = cJSON_IsString(inputs)  ? inputs->valuestring  : "";
            const char *er  = cJSON_IsString(errj)    ? errj->valuestring    : NULL;
            int ms = cJSON_IsNumber(dur) ? dur->valueint : -1;
            const char *tss = cJSON_IsString(ts) ? ts->valuestring : "";
            /* HH:MM:SS: cortamos de "YYYY-MM-DDTHH:MM:SS..." -> ultimos 8 chars de la hora */
            char hhmmss[12] = "--:--:--";
            if (tss[0]) {
                const char *tt = strchr(tss, 'T');
                if (tt && strlen(tt) >= 9) snprintf(hhmmss, sizeof(hhmmss), "%.8s", tt + 1);
            }
            char line[320];
            if (er && er[0]) {
                snprintf(line, sizeof(line), "[%s %s] %s/%s %dms | %s\n",
                         tag, hhmmss, sec, tl, ms, er);
            } else {
                snprintf(line, sizeof(line), "[%s %s] %s/%s %dms %s%s\n",
                         tag, hhmmss, sec, tl, ms, in[0] ? "· " : "", in);
            }
            size_t ln = strlen(line);
            if (used + ln + 1 >= (size_t)out_size) break;
            memcpy(out + used, line, ln);
            used += ln;
            out[used] = '\0';
        }
    }
    cJSON_Delete(root);
    free(raw);
    if (used == 0) snprintf(out, out_size, "(sin registros)");
    return 1;
}

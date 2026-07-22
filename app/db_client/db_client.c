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

int db_config_set(const char *key, const char *value, const char *category) {
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
    cJSON *root;
    int result = parse_ok_json(raw, &root);
    if (result) cJSON_Delete(root);
    free(raw);
    return result;
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

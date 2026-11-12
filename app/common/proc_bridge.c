/* ═══════════════════════════════════════════════════════
   proc_bridge.c — fork+exec+pipe + parse cJSON, compartido por todos los
   *_client.c. Antes estaba copiado en db/wifi/net/bt/report (~35 lineas x5).
   ═══════════════════════════════════════════════════════ */
#define _POSIX_C_SOURCE 200809L
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <sys/wait.h>
#include <cjson/cJSON.h>
#include "proc_bridge.h"

char *piscan_run_capture(char *const argv[]) {
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
    size_t cap = 8192, len = 0;
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

int piscan_parse_ok_json(const char *raw, cJSON **out_root) {
    *out_root = NULL;
    if (!raw) return 0;
    cJSON *root = cJSON_Parse(raw);
    if (!root) return 0;
    cJSON *ok = cJSON_GetObjectItemCaseSensitive(root, "ok");
    if (!cJSON_IsTrue(ok)) { cJSON_Delete(root); return 0; }
    *out_root = root;
    return 1;
}

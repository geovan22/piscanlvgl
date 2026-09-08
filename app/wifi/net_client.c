/* ═══════════════════════════════════════════════════════
   net_client.c — Puente C -> net_ops.py (gestion de wlan0).
   Mismo patron fork+exec+pipe + cJSON que wifi_client.c.
   ═══════════════════════════════════════════════════════ */
#define _POSIX_C_SOURCE 200809L
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <sys/wait.h>
#include <cjson/cJSON.h>
#include "net_client.h"

#ifndef PISCAN_NET_OPS_PATH
#define PISCAN_NET_OPS_PATH "/home/geo22/piscanlvgl/app/wifi/net_ops.py"
#endif

static char *net_run_and_capture(char *const argv[]) {
    int pipefd[2];
    if (pipe(pipefd) != 0) return NULL;
    pid_t pid = fork();
    if (pid < 0) { close(pipefd[0]); close(pipefd[1]); return NULL; }
    if (pid == 0) {
        close(pipefd[0]);
        dup2(pipefd[1], STDOUT_FILENO);
        close(pipefd[1]);
        execvp(argv[0], argv);
        _exit(127);
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

int net_client_scan(net_network_t *out, int max_count, char *out_error, int error_size) {
    char *argv[] = { "python3", PISCAN_NET_OPS_PATH, "scan", NULL };
    char *raw = net_run_and_capture(argv);
    if (!raw) { if (out_error) snprintf(out_error, error_size, "no se pudo ejecutar net_ops.py"); return -1; }
    cJSON *root = cJSON_Parse(raw);
    free(raw);
    if (!root) { if (out_error) snprintf(out_error, error_size, "JSON invalido de scan"); return -1; }
    cJSON *ok = cJSON_GetObjectItemCaseSensitive(root, "ok");
    if (!cJSON_IsTrue(ok)) { if (out_error) snprintf(out_error, error_size, "scan fallo"); cJSON_Delete(root); return -1; }
    cJSON *nets = cJSON_GetObjectItemCaseSensitive(root, "networks");
    int count = 0;
    if (cJSON_IsArray(nets)) {
        cJSON *item;
        cJSON_ArrayForEach(item, nets) {
            if (count >= max_count) break;
            cJSON *ssid = cJSON_GetObjectItemCaseSensitive(item, "ssid");
            cJSON *sig = cJSON_GetObjectItemCaseSensitive(item, "signal");
            cJSON *sec = cJSON_GetObjectItemCaseSensitive(item, "security");
            cJSON *inuse = cJSON_GetObjectItemCaseSensitive(item, "in_use");
            snprintf(out[count].ssid, sizeof(out[count].ssid), "%s", cJSON_IsString(ssid) ? ssid->valuestring : "");
            out[count].signal = cJSON_IsNumber(sig) ? (int)sig->valuedouble : 0;
            snprintf(out[count].security, sizeof(out[count].security), "%s", cJSON_IsString(sec) ? sec->valuestring : "?");
            out[count].in_use = cJSON_IsTrue(inuse) ? 1 : 0;
            count++;
        }
    }
    cJSON_Delete(root);
    return count;
}

int net_client_list_saved(net_saved_t *out, int max_count, char *out_error, int error_size) {
    char *argv[] = { "python3", PISCAN_NET_OPS_PATH, "list_saved", NULL };
    char *raw = net_run_and_capture(argv);
    if (!raw) { if (out_error) snprintf(out_error, error_size, "no se pudo ejecutar net_ops.py"); return -1; }
    cJSON *root = cJSON_Parse(raw);
    free(raw);
    if (!root) { if (out_error) snprintf(out_error, error_size, "JSON invalido de list_saved"); return -1; }
    cJSON *ok = cJSON_GetObjectItemCaseSensitive(root, "ok");
    if (!cJSON_IsTrue(ok)) { if (out_error) snprintf(out_error, error_size, "list_saved fallo"); cJSON_Delete(root); return -1; }
    cJSON *saved = cJSON_GetObjectItemCaseSensitive(root, "saved");
    int count = 0;
    if (cJSON_IsArray(saved)) {
        cJSON *item;
        cJSON_ArrayForEach(item, saved) {
            if (count >= max_count) break;
            cJSON *name = cJSON_GetObjectItemCaseSensitive(item, "name");
            cJSON *ac = cJSON_GetObjectItemCaseSensitive(item, "autoconnect");
            cJSON *act = cJSON_GetObjectItemCaseSensitive(item, "active");
            snprintf(out[count].name, sizeof(out[count].name), "%s", cJSON_IsString(name) ? name->valuestring : "");
            out[count].autoconnect = cJSON_IsTrue(ac) ? 1 : 0;
            out[count].active = cJSON_IsTrue(act) ? 1 : 0;
            count++;
        }
    }
    cJSON_Delete(root);
    return count;
}

int net_client_status(net_status_t *out) {
    char *argv[] = { "python3", PISCAN_NET_OPS_PATH, "status", NULL };
    char *raw = net_run_and_capture(argv);
    if (!raw) return 0;
    cJSON *root = cJSON_Parse(raw);
    free(raw);
    if (!root) return 0;
    cJSON *st = cJSON_GetObjectItemCaseSensitive(root, "status");
    if (!st) { cJSON_Delete(root); return 0; }
    cJSON *conn = cJSON_GetObjectItemCaseSensitive(st, "connected");
    cJSON *ssid = cJSON_GetObjectItemCaseSensitive(st, "ssid");
    cJSON *ip = cJSON_GetObjectItemCaseSensitive(st, "ip");
    out->connected = cJSON_IsTrue(conn) ? 1 : 0;
    snprintf(out->ssid, sizeof(out->ssid), "%s", cJSON_IsString(ssid) ? ssid->valuestring : "");
    snprintf(out->ip, sizeof(out->ip), "%s", cJSON_IsString(ip) ? ip->valuestring : "");
    cJSON_Delete(root);
    return 1;
}

int net_client_connect(const char *ssid, const char *password,
                       char *out_detail, int detail_size) {
    char *argv_pw[] = { "python3", PISCAN_NET_OPS_PATH, "connect", (char *)ssid, (char *)password, NULL };
    char *argv_nopw[] = { "python3", PISCAN_NET_OPS_PATH, "connect", (char *)ssid, NULL };
    char *raw = net_run_and_capture((password && password[0]) ? argv_pw : argv_nopw);
    if (!raw) { if (out_detail) snprintf(out_detail, detail_size, "no se pudo ejecutar connect"); return 0; }
    cJSON *root = cJSON_Parse(raw);
    free(raw);
    if (!root) { if (out_detail) snprintf(out_detail, detail_size, "JSON invalido de connect"); return 0; }
    cJSON *ok = cJSON_GetObjectItemCaseSensitive(root, "ok");
    cJSON *detail = cJSON_GetObjectItemCaseSensitive(root, "detail");
    if (out_detail && cJSON_IsString(detail)) snprintf(out_detail, detail_size, "%s", detail->valuestring);
    int result = cJSON_IsTrue(ok) ? 1 : 0;
    cJSON_Delete(root);
    return result;
}

int net_client_forget(const char *ssid) {
    char *argv[] = { "python3", PISCAN_NET_OPS_PATH, "forget", (char *)ssid, NULL };
    char *raw = net_run_and_capture(argv);
    if (!raw) return 0;
    cJSON *root = cJSON_Parse(raw);
    free(raw);
    if (!root) return 0;
    cJSON *ok = cJSON_GetObjectItemCaseSensitive(root, "ok");
    int result = cJSON_IsTrue(ok) ? 1 : 0;
    cJSON_Delete(root);
    return result;
}

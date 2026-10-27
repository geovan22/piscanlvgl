/* ═══════════════════════════════════════════════════════
   bt_client.c — Puente C -> app/bluetooth/bt_ops.py
   Mismo patron fork+exec+pipe + cJSON que wifi_client.c.
   ═══════════════════════════════════════════════════════ */
#define _POSIX_C_SOURCE 200809L
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <sys/wait.h>
#include <cjson/cJSON.h>
#include "bt_client.h"

#ifndef PISCAN_BT_OPS_PATH
#define PISCAN_BT_OPS_PATH "/home/geo22/piscanlvgl/app/bluetooth/bt_ops.py"
#endif

#define BT_DEFAULT_IFACE "hci1"

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

int bt_client_scan(const char *iface, int seconds, const char *mode,
                   bt_device_t *out, int max_count,
                   char *out_error, int error_size) {
    const char *iface_s = (iface && iface[0]) ? iface : BT_DEFAULT_IFACE;
    const char *mode_s  = (mode  && mode[0])  ? mode  : "dual";
    char sec_str[16];
    snprintf(sec_str, sizeof(sec_str), "%d", seconds > 0 ? seconds : 10);

    /* bt_ops.py scan <iface> <seconds> <mode> */
    char *argv[] = { "python3", (char *)PISCAN_BT_OPS_PATH, "scan",
                     (char *)iface_s, sec_str, (char *)mode_s, NULL };

    char *raw = run_and_capture(argv);
    if (!raw) {
        if (out_error) snprintf(out_error, error_size, "no se pudo ejecutar bt_ops.py");
        return -1;
    }

    cJSON *root = cJSON_Parse(raw);
    free(raw);
    if (!root) {
        if (out_error) snprintf(out_error, error_size, "JSON invalido de bt_ops.py");
        return -1;
    }

    cJSON *ok = cJSON_GetObjectItemCaseSensitive(root, "ok");
    if (!cJSON_IsTrue(ok)) {
        cJSON *err = cJSON_GetObjectItemCaseSensitive(root, "error");
        if (out_error)
            snprintf(out_error, error_size, "%s",
                     cJSON_IsString(err) ? err->valuestring : "error desconocido");
        cJSON_Delete(root);
        return -1;
    }

    cJSON *devices = cJSON_GetObjectItemCaseSensitive(root, "devices");
    int count = 0;
    if (cJSON_IsArray(devices)) {
        cJSON *item;
        cJSON_ArrayForEach(item, devices) {
            if (count >= max_count) break;
            cJSON *mac = cJSON_GetObjectItemCaseSensitive(item, "mac");
            cJSON *addr_type = cJSON_GetObjectItemCaseSensitive(item, "addr_type");
            cJSON *kind = cJSON_GetObjectItemCaseSensitive(item, "kind");
            cJSON *name = cJSON_GetObjectItemCaseSensitive(item, "name");
            cJSON *vendor = cJSON_GetObjectItemCaseSensitive(item, "vendor");
            cJSON *rssi = cJSON_GetObjectItemCaseSensitive(item, "rssi");
            cJSON *tx_power = cJSON_GetObjectItemCaseSensitive(item, "tx_power");
            cJSON *appearance = cJSON_GetObjectItemCaseSensitive(item, "appearance");
            cJSON *icon = cJSON_GetObjectItemCaseSensitive(item, "icon");
            cJSON *cls = cJSON_GetObjectItemCaseSensitive(item, "class");

            snprintf(out[count].mac, sizeof(out[count].mac), "%s",
                     cJSON_IsString(mac) ? mac->valuestring : "");
            snprintf(out[count].addr_type, sizeof(out[count].addr_type), "%s",
                     cJSON_IsString(addr_type) ? addr_type->valuestring : "");
            snprintf(out[count].kind, sizeof(out[count].kind), "%s",
                     cJSON_IsString(kind) ? kind->valuestring : "");
            snprintf(out[count].name, sizeof(out[count].name), "%s",
                     cJSON_IsString(name) ? name->valuestring : "");
            snprintf(out[count].vendor, sizeof(out[count].vendor), "%s",
                     cJSON_IsString(vendor) ? vendor->valuestring : "");
            out[count].rssi = cJSON_IsNumber(rssi) ? rssi->valueint : 0;
            out[count].tx_power = cJSON_IsNumber(tx_power) ? tx_power->valueint : 0;
            snprintf(out[count].appearance, sizeof(out[count].appearance), "%s",
                     cJSON_IsString(appearance) ? appearance->valuestring : "");
            snprintf(out[count].icon, sizeof(out[count].icon), "%s",
                     cJSON_IsString(icon) ? icon->valuestring : "");
            snprintf(out[count].cls, sizeof(out[count].cls), "%s",
                     cJSON_IsString(cls) ? cls->valuestring : "");
            count++;
        }
    }

    cJSON_Delete(root);
    return count;
}

int bt_client_power(int on, const char *iface,
                    char *out_detail, int detail_size) {
    const char *iface_s = (iface && iface[0]) ? iface : BT_DEFAULT_IFACE;
    char *argv[] = { "python3", (char *)PISCAN_BT_OPS_PATH,
                     (char *)(on ? "up" : "down"), (char *)iface_s, NULL };

    char *raw = run_and_capture(argv);
    if (!raw) return 0;
    cJSON *root = cJSON_Parse(raw);
    free(raw);
    if (!root) return 0;

    cJSON *ok = cJSON_GetObjectItemCaseSensitive(root, "ok");
    cJSON *msg = cJSON_GetObjectItemCaseSensitive(root, "msg");
    int success = cJSON_IsTrue(ok) ? 1 : 0;
    if (out_detail && cJSON_IsString(msg))
        snprintf(out_detail, detail_size, "%s", msg->valuestring);
    cJSON_Delete(root);
    return success;
}

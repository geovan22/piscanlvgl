/* ═══════════════════════════════════════════════════════
   wifi_client.c — Puente C -> wifi_scan.py, mismo patron fork+exec+pipe
   + cJSON que ya usamos en db_client.c.
   ═══════════════════════════════════════════════════════ */
#define _POSIX_C_SOURCE 200809L
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <sys/wait.h>
#include <cjson/cJSON.h>
#include "wifi_client.h"

#ifndef PISCAN_WIFI_SCAN_PATH
#define PISCAN_WIFI_SCAN_PATH "/home/geo22/piscanlvgl/app/wifi/wifi_scan.py"
#endif
#ifndef PISCAN_WIFI_OPS_PATH
#define PISCAN_WIFI_OPS_PATH "/home/geo22/piscanlvgl/app/wifi/wifi_ops.py"
#endif

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

int wifi_client_scan(wifi_network_t *out, int max_count, char *out_error, int error_size) {
    char *argv_path = PISCAN_WIFI_SCAN_PATH;
    char *argv[] = { "python3", argv_path, "scan", "wlan1", NULL };

    char *raw = run_and_capture(argv);
    if (!raw) {
        if (out_error) snprintf(out_error, error_size, "no se pudo ejecutar wifi_scan.py");
        return -1;
    }

    cJSON *root = cJSON_Parse(raw);
    free(raw);
    if (!root) {
        if (out_error) snprintf(out_error, error_size, "JSON invalido de wifi_scan.py");
        return -1;
    }

    cJSON *ok = cJSON_GetObjectItemCaseSensitive(root, "ok");
    if (!cJSON_IsTrue(ok)) {
        cJSON *err = cJSON_GetObjectItemCaseSensitive(root, "error");
        if (out_error) {
            snprintf(out_error, error_size, "%s",
                     cJSON_IsString(err) ? err->valuestring : "error desconocido");
        }
        cJSON_Delete(root);
        return -1;
    }

    cJSON *networks = cJSON_GetObjectItemCaseSensitive(root, "networks");
    int count = 0;
    if (cJSON_IsArray(networks)) {
        cJSON *item;
        cJSON_ArrayForEach(item, networks) {
            if (count >= max_count) break;
            cJSON *ssid = cJSON_GetObjectItemCaseSensitive(item, "ssid");
            cJSON *bssid = cJSON_GetObjectItemCaseSensitive(item, "bssid");
            cJSON *signal = cJSON_GetObjectItemCaseSensitive(item, "signal");
            cJSON *channel = cJSON_GetObjectItemCaseSensitive(item, "channel");
            cJSON *security = cJSON_GetObjectItemCaseSensitive(item, "security");

            snprintf(out[count].ssid, sizeof(out[count].ssid), "%s",
                     cJSON_IsString(ssid) ? ssid->valuestring : "?");
            snprintf(out[count].bssid, sizeof(out[count].bssid), "%s",
                     cJSON_IsString(bssid) ? bssid->valuestring : "");
            out[count].signal = cJSON_IsNumber(signal) ? (float)signal->valuedouble : -100.0f;
            out[count].channel = cJSON_IsNumber(channel) ? channel->valueint : 0;
            snprintf(out[count].security, sizeof(out[count].security), "%s",
                     cJSON_IsString(security) ? security->valuestring : "?");
            count++;
        }
    }

    cJSON_Delete(root);
    return count;
}

static int run_wifi_ops(const char *cmd, char *out_mode, int mode_size) {
    char *argv_path = PISCAN_WIFI_OPS_PATH;
    char *argv[] = { "python3", argv_path, (char *)cmd, "wlan1", NULL };

    char *raw = run_and_capture(argv);
    if (!raw) return 0;

    cJSON *root = cJSON_Parse(raw);
    free(raw);
    if (!root) return 0;

    cJSON *ok = cJSON_GetObjectItemCaseSensitive(root, "ok");
    cJSON *mode = cJSON_GetObjectItemCaseSensitive(root, "mode");
    int success = cJSON_IsTrue(ok) ? 1 : 0;

    if (out_mode && cJSON_IsString(mode)) {
        snprintf(out_mode, mode_size, "%s", mode->valuestring);
    }

    cJSON_Delete(root);
    return success;
}

int wifi_client_monitor_set(int enable, char *out_mode, int mode_size) {
    return run_wifi_ops(enable ? "enable" : "disable", out_mode, mode_size);
}

int wifi_client_monitor_status(char *out_mode, int mode_size) {
    return run_wifi_ops("status", out_mode, mode_size);
}

int wifi_client_deauth(const char *bssid, int channel, int count,
                       char *out_output, int output_size,
                       char *out_error, int error_size) {
    char *argv_path = PISCAN_WIFI_OPS_PATH;
    char channel_str[8], count_str[8];
    snprintf(channel_str, sizeof(channel_str), "%d", channel);
    snprintf(count_str, sizeof(count_str), "%d", count);

    char *argv[] = { "python3", argv_path, "deauth", (char *)bssid, channel_str, count_str, "wlan1", NULL };

    char *raw = run_and_capture(argv);
    if (!raw) {
        if (out_error) snprintf(out_error, error_size, "no se pudo ejecutar wifi_ops.py");
        return 0;
    }

    cJSON *root = cJSON_Parse(raw);
    free(raw);
    if (!root) {
        if (out_error) snprintf(out_error, error_size, "JSON invalido de wifi_ops.py");
        return 0;
    }

    cJSON *ok = cJSON_GetObjectItemCaseSensitive(root, "ok");
    int success = cJSON_IsTrue(ok) ? 1 : 0;

    if (success) {
        cJSON *output = cJSON_GetObjectItemCaseSensitive(root, "output");
        if (out_output && cJSON_IsString(output)) {
            snprintf(out_output, output_size, "%s", output->valuestring);
        }
    } else {
        cJSON *err = cJSON_GetObjectItemCaseSensitive(root, "error");
        if (out_error) {
            snprintf(out_error, error_size, "%s",
                     cJSON_IsString(err) ? err->valuestring : "error desconocido");
        }
    }

    cJSON_Delete(root);
    return success;
}

int wifi_client_handshake(const char *bssid, int channel, int capture_seconds, int deauth_count,
                          char *out_cap_file, int cap_file_size,
                          char *out_detail, int detail_size) {
    char *argv_path = PISCAN_WIFI_OPS_PATH;
    char channel_str[8], seconds_str[8], count_str[8];
    snprintf(channel_str, sizeof(channel_str), "%d", channel);
    snprintf(seconds_str, sizeof(seconds_str), "%d", capture_seconds);
    snprintf(count_str, sizeof(count_str), "%d", deauth_count);

    char *argv[] = { "python3", argv_path, "handshake", (char *)bssid, channel_str,
                     "wlan1", seconds_str, count_str, NULL };

    char *raw = run_and_capture(argv);
    if (!raw) {
        if (out_detail) snprintf(out_detail, detail_size, "no se pudo ejecutar wifi_ops.py");
        return 0;
    }

    cJSON *root = cJSON_Parse(raw);
    free(raw);
    if (!root) {
        if (out_detail) snprintf(out_detail, detail_size, "JSON invalido de wifi_ops.py");
        return 0;
    }

    cJSON *ok = cJSON_GetObjectItemCaseSensitive(root, "ok");
    cJSON *handshake = cJSON_GetObjectItemCaseSensitive(root, "handshake");
    cJSON *cap_file = cJSON_GetObjectItemCaseSensitive(root, "cap_file");
    int got_handshake = (cJSON_IsTrue(ok) && cJSON_IsTrue(handshake)) ? 1 : 0;

    if (out_cap_file && cJSON_IsString(cap_file)) {
        snprintf(out_cap_file, cap_file_size, "%s", cap_file->valuestring);
    }

    if (!got_handshake) {
        cJSON *detail = cJSON_GetObjectItemCaseSensitive(root, "detail");
        cJSON *err = cJSON_GetObjectItemCaseSensitive(root, "error");
        const char *msg = cJSON_IsString(detail) ? detail->valuestring :
                           cJSON_IsString(err) ? err->valuestring : "sin handshake";
        if (out_detail) snprintf(out_detail, detail_size, "%s", msg);
    }

    cJSON_Delete(root);
    return got_handshake;
}

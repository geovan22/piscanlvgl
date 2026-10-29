/* ═══════════════════════════════════════════════════════
   bt_client.c — Puente C -> app/bluetooth/bt_ops.py
   Mismo patron fork+exec+pipe + cJSON que wifi_client.c.
   ═══════════════════════════════════════════════════════ */
#define _POSIX_C_SOURCE 200809L
#include <stdio.h>
#include <stdarg.h>
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
            cJSON *type_l = cJSON_GetObjectItemCaseSensitive(item, "type");
            cJSON *uuids = cJSON_GetObjectItemCaseSensitive(item, "uuids");

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
            snprintf(out[count].type_lbl, sizeof(out[count].type_lbl), "%s",
                     cJSON_IsString(type_l) ? type_l->valuestring : "");

            /* uuids: cada entrada viene como "uuid|Etiqueta humana". Preferimos
             * la etiqueta (es lo util para el usuario); si no hay, usamos la
             * UUID corta. Juntamos con ", " hasta llenar el buffer. */
            out[count].uuids[0] = '\0';
            if (cJSON_IsArray(uuids)) {
                size_t used = 0;
                cJSON *uu;
                cJSON_ArrayForEach(uu, uuids) {
                    if (!cJSON_IsString(uu)) continue;
                    const char *s = uu->valuestring;
                    const char *pipe = strchr(s, '|');
                    const char *label;
                    char short_uuid[12];
                    if (pipe && pipe[1]) {
                        label = pipe + 1;
                    } else {
                        /* uuid sin label: mostramos los 4 chars utiles (bytes 4-7). */
                        size_t L = strlen(s);
                        if (L >= 8) { snprintf(short_uuid, sizeof(short_uuid), "%.4s", s + 4); }
                        else        { snprintf(short_uuid, sizeof(short_uuid), "%s", s); }
                        label = short_uuid;
                    }
                    size_t avail = sizeof(out[count].uuids) - used;
                    if (avail <= 4) break;  /* no entra, cortamos */
                    int w;
                    if (used == 0) w = snprintf(out[count].uuids + used, avail, "%s", label);
                    else           w = snprintf(out[count].uuids + used, avail, ", %s", label);
                    if (w <= 0) break;
                    if ((size_t)w >= avail) {
                        used = sizeof(out[count].uuids) - 1;
                        out[count].uuids[used] = '\0';
                        break;
                    }
                    used += (size_t)w;
                }
            }
            count++;
        }
    }

    cJSON_Delete(root);
    return count;
}

/* ----------------------------------------------------------------------
 * Pentesting / auditoria: SDP + GATT + L2ping. Cada funcion arma texto
 * multilinea para pintar en el overlay (lv_label). SOLO lab.
 * ---------------------------------------------------------------------- */

static int _append(char *buf, int cap, int *used, const char *fmt, ...) {
    if (*used + 1 >= cap) return 0;
    va_list ap; va_start(ap, fmt);
    int w = vsnprintf(buf + *used, cap - *used, fmt, ap);
    va_end(ap);
    if (w <= 0) return 0;
    if (*used + w >= cap) { *used = cap - 1; buf[*used] = '\0'; return 0; }
    *used += w;
    return 1;
}

int bt_client_sdp(const char *mac, const char *addr_type,
                  char *out_text, int out_size) {
    if (!mac || !out_text || out_size < 32) return 0;
    const char *at = (addr_type && addr_type[0]) ? addr_type : "";
    char *argv_w[]  = { "python3", (char *)PISCAN_BT_OPS_PATH, "sdp",
                        (char *)mac, (char *)at, NULL };
    char *argv_wo[] = { "python3", (char *)PISCAN_BT_OPS_PATH, "sdp",
                        (char *)mac, NULL };
    char *raw = run_and_capture(at[0] ? argv_w : argv_wo);
    if (!raw) {
        snprintf(out_text, out_size, "SDP: no se pudo ejecutar bt_ops.py");
        return 0;
    }
    cJSON *root = cJSON_Parse(raw);
    free(raw);
    if (!root) {
        snprintf(out_text, out_size, "SDP: JSON invalido");
        return 0;
    }
    cJSON *ok = cJSON_GetObjectItemCaseSensitive(root, "ok");
    int ok_b = cJSON_IsTrue(ok) ? 1 : 0;
    cJSON *services = cJSON_GetObjectItemCaseSensitive(root, "services");
    cJSON *err = cJSON_GetObjectItemCaseSensitive(root, "error");
    cJSON *warn = cJSON_GetObjectItemCaseSensitive(root, "warn");

    out_text[0] = '\0';
    int used = 0;
    if (!ok_b) {
        _append(out_text, out_size, &used, "SDP %s FALLO:\n%s\n", mac,
                cJSON_IsString(err) ? err->valuestring : "desconocido");
        cJSON_Delete(root);
        return 0;
    }
    int n = (cJSON_IsArray(services)) ? cJSON_GetArraySize(services) : 0;
    _append(out_text, out_size, &used, "SDP %s - %d servicio(s)%s\n",
            mac, n,
            cJSON_IsString(warn) ? " (parcial)" : "");
    if (cJSON_IsArray(services)) {
        cJSON *s;
        int i = 0;
        cJSON_ArrayForEach(s, services) {
            cJSON *name = cJSON_GetObjectItemCaseSensitive(s, "name");
            cJSON *rh   = cJSON_GetObjectItemCaseSensitive(s, "rec_handle");
            cJSON *cls  = cJSON_GetObjectItemCaseSensitive(s, "classes");
            _append(out_text, out_size, &used, "* %s",
                    cJSON_IsString(name) ? name->valuestring : "?");
            if (cJSON_IsString(rh) && rh->valuestring[0])
                _append(out_text, out_size, &used, "  [%s]", rh->valuestring);
            _append(out_text, out_size, &used, "\n");
            /* class names */
            if (cJSON_IsArray(cls)) {
                int ci = 0; cJSON *c;
                cJSON_ArrayForEach(c, cls) {
                    if (!cJSON_IsArray(c) || cJSON_GetArraySize(c) < 1) continue;
                    cJSON *cn = cJSON_GetArrayItem(c, 0);
                    if (cJSON_IsString(cn))
                        _append(out_text, out_size, &used, "    - %s\n",
                                cn->valuestring);
                    if (++ci >= 2) break;
                }
            }
            if (++i >= 10) {
                _append(out_text, out_size, &used, "... (%d mas)\n", n - i);
                break;
            }
        }
    }
    if (n == 0 && cJSON_IsString(warn)) {
        _append(out_text, out_size, &used, "\nAviso: %s", warn->valuestring);
    }
    cJSON_Delete(root);
    return (n > 0) ? 1 : 0;
}

int bt_client_gatt(const char *mac, const char *iface,
                   char *out_text, int out_size) {
    if (!mac || !out_text || out_size < 32) return 0;
    const char *ifs = (iface && iface[0]) ? iface : BT_DEFAULT_IFACE;
    char *argv[] = { "python3", (char *)PISCAN_BT_OPS_PATH, "gatt",
                     (char *)ifs, (char *)mac, NULL };
    char *raw = run_and_capture(argv);
    if (!raw) {
        snprintf(out_text, out_size, "GATT: no se pudo ejecutar bt_ops.py");
        return 0;
    }
    cJSON *root = cJSON_Parse(raw);
    free(raw);
    if (!root) {
        snprintf(out_text, out_size, "GATT: JSON invalido");
        return 0;
    }
    cJSON *ok = cJSON_GetObjectItemCaseSensitive(root, "ok");
    int ok_b = cJSON_IsTrue(ok) ? 1 : 0;
    cJSON *services = cJSON_GetObjectItemCaseSensitive(root, "services");
    cJSON *chars    = cJSON_GetObjectItemCaseSensitive(root, "characteristics");
    cJSON *err  = cJSON_GetObjectItemCaseSensitive(root, "error");
    cJSON *warn = cJSON_GetObjectItemCaseSensitive(root, "warn");

    out_text[0] = '\0';
    int used = 0;
    if (!ok_b) {
        _append(out_text, out_size, &used, "GATT %s FALLO:\n%s\n", mac,
                cJSON_IsString(err) ? err->valuestring : "desconocido");
        cJSON_Delete(root);
        return 0;
    }
    int ns = cJSON_IsArray(services) ? cJSON_GetArraySize(services) : 0;
    int nc = cJSON_IsArray(chars) ? cJSON_GetArraySize(chars) : 0;
    _append(out_text, out_size, &used, "GATT %s - %d svc, %d chars%s\n",
            mac, ns, nc,
            cJSON_IsString(warn) ? " (parcial)" : "");
    if (cJSON_IsArray(services)) {
        cJSON *s; int i = 0;
        cJSON_ArrayForEach(s, services) {
            cJSON *uuid = cJSON_GetObjectItemCaseSensitive(s, "uuid");
            cJSON *lbl  = cJSON_GetObjectItemCaseSensitive(s, "label");
            const char *u = cJSON_IsString(uuid) ? uuid->valuestring : "?";
            const char *l = (cJSON_IsString(lbl) && lbl->valuestring[0])
                            ? lbl->valuestring : "";
            if (l[0])
                _append(out_text, out_size, &used, "svc %.8s %s\n", u, l);
            else
                _append(out_text, out_size, &used, "svc %.36s\n", u);
            if (++i >= 8) {
                _append(out_text, out_size, &used, "... (%d svc mas)\n", ns - i);
                break;
            }
        }
    }
    if (nc > 0) {
        _append(out_text, out_size, &used, "chars: %d (ver DB para detalle)\n", nc);
    }
    if (cJSON_IsString(warn)) {
        _append(out_text, out_size, &used, "\nAviso: %s", warn->valuestring);
    }
    cJSON_Delete(root);
    return (ns + nc > 0) ? 1 : 0;
}

int bt_client_l2ping(const char *mac, int count, int size,
                     const char *addr_type,
                     char *out_text, int out_size) {
    if (!mac || !out_text || out_size < 32) return 0;
    if (count <= 0) count = 5;
    if (size <= 0)  size = 44;
    char cstr[8], sstr[8];
    snprintf(cstr, sizeof(cstr), "%d", count);
    snprintf(sstr, sizeof(sstr), "%d", size);
    const char *at = (addr_type && addr_type[0]) ? addr_type : "";
    char *argv_w[]  = { "python3", (char *)PISCAN_BT_OPS_PATH, "l2ping",
                        (char *)mac, cstr, sstr, (char *)at, NULL };
    char *argv_wo[] = { "python3", (char *)PISCAN_BT_OPS_PATH, "l2ping",
                        (char *)mac, cstr, sstr, NULL };
    char *raw = run_and_capture(at[0] ? argv_w : argv_wo);
    if (!raw) {
        snprintf(out_text, out_size, "L2ping: no se pudo ejecutar bt_ops.py");
        return 0;
    }
    cJSON *root = cJSON_Parse(raw);
    free(raw);
    if (!root) {
        snprintf(out_text, out_size, "L2ping: JSON invalido");
        return 0;
    }
    cJSON *ok = cJSON_GetObjectItemCaseSensitive(root, "ok");
    int ok_b = cJSON_IsTrue(ok) ? 1 : 0;
    cJSON *stats = cJSON_GetObjectItemCaseSensitive(root, "stats");
    cJSON *err  = cJSON_GetObjectItemCaseSensitive(root, "error");

    out_text[0] = '\0';
    int used = 0;
    if (!ok_b && !cJSON_IsObject(stats)) {
        _append(out_text, out_size, &used, "L2ping %s FALLO:\n%s\n", mac,
                cJSON_IsString(err) ? err->valuestring : "desconocido");
        cJSON_Delete(root);
        return 0;
    }
    cJSON *sent = cJSON_GetObjectItemCaseSensitive(stats, "sent");
    cJSON *recv = cJSON_GetObjectItemCaseSensitive(stats, "received");
    cJSON *loss = cJSON_GetObjectItemCaseSensitive(stats, "loss_pct");
    cJSON *avg  = cJSON_GetObjectItemCaseSensitive(stats, "avg_ms");
    cJSON *mn   = cJSON_GetObjectItemCaseSensitive(stats, "min_ms");
    cJSON *mx   = cJSON_GetObjectItemCaseSensitive(stats, "max_ms");
    int snt = cJSON_IsNumber(sent) ? sent->valueint : 0;
    int rcv = cJSON_IsNumber(recv) ? recv->valueint : 0;
    _append(out_text, out_size, &used, "L2ping %s\n", mac);
    _append(out_text, out_size, &used, "sent=%d  recv=%d", snt, rcv);
    if (cJSON_IsNumber(loss))
        _append(out_text, out_size, &used, "  loss=%d%%", loss->valueint);
    _append(out_text, out_size, &used, "\n");
    if (cJSON_IsNumber(avg))
        _append(out_text, out_size, &used, "avg=%.2fms  min=%.2fms  max=%.2fms\n",
                avg->valuedouble,
                cJSON_IsNumber(mn) ? mn->valuedouble : 0.0,
                cJSON_IsNumber(mx) ? mx->valuedouble : 0.0);
    if (!ok_b && cJSON_IsString(err))
        _append(out_text, out_size, &used, "\nAviso: %s", err->valuestring);
    cJSON_Delete(root);
    return rcv > 0 ? 1 : 0;
}

int bt_client_ble_spam(const char *iface, int seconds, const char *mode,
                       char *out_text, int out_size) {
    if (!out_text || out_size < 32) return 0;
    const char *ifs = (iface && iface[0]) ? iface : BT_DEFAULT_IFACE;
    const char *m   = (mode  && mode[0])  ? mode  : "names";
    char ss[8];
    snprintf(ss, sizeof(ss), "%d", seconds > 0 ? seconds : 20);
    char *argv[] = { "python3", (char *)PISCAN_BT_OPS_PATH, "spam",
                     (char *)ifs, ss, (char *)m, NULL };
    char *raw = run_and_capture(argv);
    if (!raw) {
        snprintf(out_text, out_size, "BLE spam: no se pudo ejecutar bt_ops.py");
        return 0;
    }
    cJSON *root = cJSON_Parse(raw);
    free(raw);
    if (!root) {
        snprintf(out_text, out_size, "BLE spam: JSON invalido");
        return 0;
    }
    cJSON *ok = cJSON_GetObjectItemCaseSensitive(root, "ok");
    cJSON *err = cJSON_GetObjectItemCaseSensitive(root, "error");
    cJSON *cycles = cJSON_GetObjectItemCaseSensitive(root, "cycles");
    cJSON *uniq = cJSON_GetObjectItemCaseSensitive(root, "uniq_names");
    cJSON *last = cJSON_GetObjectItemCaseSensitive(root, "last_name");
    cJSON *modej = cJSON_GetObjectItemCaseSensitive(root, "mode");
    cJSON *secj = cJSON_GetObjectItemCaseSensitive(root, "seconds");
    cJSON *warn = cJSON_GetObjectItemCaseSensitive(root, "warn");
    int ok_b = cJSON_IsTrue(ok) ? 1 : 0;
    int used = 0;
    out_text[0] = '\0';
    if (!ok_b) {
        _append(out_text, out_size, &used, "BLE spam FALLO:\n%s\n",
                cJSON_IsString(err) ? err->valuestring : "desconocido");
        cJSON_Delete(root);
        return 0;
    }
    int cy = cJSON_IsNumber(cycles) ? cycles->valueint : 0;
    int un = cJSON_IsNumber(uniq)  ? uniq->valueint  : 0;
    int sc = cJSON_IsNumber(secj)  ? secj->valueint  : 0;
    _append(out_text, out_size, &used,
            "BLE spam %s mode=%s %ds\n"
            "ciclos=%d  nombres unicos=%d\n"
            "ultimo nombre: %s",
            (iface && iface[0]) ? iface : "hci1",
            cJSON_IsString(modej) ? modej->valuestring : "?",
            sc, cy, un,
            (cJSON_IsString(last) && last->valuestring[0]) ? last->valuestring : "-");
    /* Si no salio ningun advert al aire (cy==0) y el backend adjunto un
       diagnostico (p.ej. HCI status 0x0C command disallowed), lo mostramos:
       sin esto la UI solo decia "ciclos=0" sin causa. */
    if (cy == 0 && cJSON_IsString(warn) && warn->valuestring[0]) {
        _append(out_text, out_size, &used, "\n%s", warn->valuestring);
    }
    cJSON_Delete(root);
    return cy > 0 ? 1 : 0;
}

int bt_client_capture(const char *iface, int seconds,
                      char *out_text, int out_size) {
    if (!out_text || out_size < 32) return 0;
    const char *ifs = (iface && iface[0]) ? iface : BT_DEFAULT_IFACE;
    char ss[8];
    snprintf(ss, sizeof(ss), "%d", seconds > 0 ? seconds : 30);
    char *argv[] = { "python3", (char *)PISCAN_BT_OPS_PATH, "capture",
                     (char *)ifs, ss, NULL };
    char *raw = run_and_capture(argv);
    if (!raw) {
        snprintf(out_text, out_size, "Capture: no se pudo ejecutar bt_ops.py");
        return 0;
    }
    cJSON *root = cJSON_Parse(raw);
    free(raw);
    if (!root) {
        snprintf(out_text, out_size, "Capture: JSON invalido");
        return 0;
    }
    cJSON *ok = cJSON_GetObjectItemCaseSensitive(root, "ok");
    cJSON *err = cJSON_GetObjectItemCaseSensitive(root, "error");
    cJSON *warn = cJSON_GetObjectItemCaseSensitive(root, "warn");
    cJSON *path = cJSON_GetObjectItemCaseSensitive(root, "path");
    cJSON *size = cJSON_GetObjectItemCaseSensitive(root, "size");
    cJSON *dur  = cJSON_GetObjectItemCaseSensitive(root, "duration_s");
    cJSON *pkts = cJSON_GetObjectItemCaseSensitive(root, "packets");
    int ok_b = cJSON_IsTrue(ok) ? 1 : 0;
    int sz = cJSON_IsNumber(size) ? size->valueint : 0;
    int used = 0;
    out_text[0] = '\0';
    if (!ok_b && sz == 0) {
        _append(out_text, out_size, &used, "Capture FALLO:\n%s\n",
                cJSON_IsString(err) ? err->valuestring : "desconocido");
        cJSON_Delete(root);
        return 0;
    }
    const char *p = cJSON_IsString(path) ? path->valuestring : "?";
    const char *fname = p;
    const char *slash = strrchr(p, '/');
    if (slash && slash[1]) fname = slash + 1;
    double d = cJSON_IsNumber(dur) ? dur->valuedouble : 0.0;
    int n = cJSON_IsNumber(pkts) ? pkts->valueint : -1;
    _append(out_text, out_size, &used,
            "Captura %s  %s\n"
            "paquetes HCI: %s\n"
            "size: %.1f KB (%d B)\n"
            "duracion: %.1f s\n"
            "path: %s",
            (iface && iface[0]) ? iface : "hci1", fname,
            (n < 0) ? "(no parseable)" : "",
            sz / 1024.0, sz, d, p);
    /* Reemplazar el placeholder de paquetes con el numero real */
    if (n >= 0) {
        /* buscamos y reemplazamos "HCI: \n" por "HCI: N\n" — simplifico
           regenerando con la cuenta correcta */
        used = 0; out_text[0] = '\0';
        _append(out_text, out_size, &used,
                "Captura %s  %s\n"
                "paquetes HCI: %d\n"
                "size: %.1f KB (%d B)\n"
                "duracion: %.1f s\n"
                "path: %s",
                (iface && iface[0]) ? iface : "hci1", fname,
                n, sz / 1024.0, sz, d, p);
    }
    if (cJSON_IsString(warn)) {
        _append(out_text, out_size, &used, "\nAviso: %s", warn->valuestring);
    }
    cJSON_Delete(root);
    return sz > 0 ? 1 : 0;
}

int bt_client_ble_beacon(const char *iface, int seconds, const char *btype,
                         int major, int minor,
                         char *out_text, int out_size) {
    if (!out_text || out_size < 32) return 0;
    const char *ifs = (iface && iface[0]) ? iface : BT_DEFAULT_IFACE;
    const char *bt  = (btype && btype[0]) ? btype : "ibeacon";
    char ss[8], mj[8], mn[8];
    snprintf(ss, sizeof(ss), "%d", seconds > 0 ? seconds : 30);
    snprintf(mj, sizeof(mj), "%d", major < 0 ? 0 : (major > 65535 ? 65535 : major));
    snprintf(mn, sizeof(mn), "%d", minor < 0 ? 0 : (minor > 65535 ? 65535 : minor));
    char *argv[] = { "python3", (char *)PISCAN_BT_OPS_PATH, "beacon",
                     (char *)ifs, ss, (char *)bt, mj, mn, NULL };
    char *raw = run_and_capture(argv);
    if (!raw) {
        snprintf(out_text, out_size, "Beacon: no se pudo ejecutar bt_ops.py");
        return 0;
    }
    cJSON *root = cJSON_Parse(raw);
    free(raw);
    if (!root) {
        snprintf(out_text, out_size, "Beacon: JSON invalido");
        return 0;
    }
    cJSON *ok    = cJSON_GetObjectItemCaseSensitive(root, "ok");
    cJSON *err   = cJSON_GetObjectItemCaseSensitive(root, "error");
    cJSON *warn  = cJSON_GetObjectItemCaseSensitive(root, "warn");
    cJSON *typej = cJSON_GetObjectItemCaseSensitive(root, "type");
    cJSON *secj  = cJSON_GetObjectItemCaseSensitive(root, "seconds");
    cJSON *labj  = cJSON_GetObjectItemCaseSensitive(root, "label");
    cJSON *airj  = cJSON_GetObjectItemCaseSensitive(root, "on_air");
    int ok_b = cJSON_IsTrue(ok) ? 1 : 0;
    int used = 0;
    out_text[0] = '\0';
    if (cJSON_IsString(err) && !cJSON_IsTrue(airj)) {
        _append(out_text, out_size, &used, "Beacon FALLO:\n%s",
                err->valuestring);
        cJSON_Delete(root);
        return 0;
    }
    int sc = cJSON_IsNumber(secj) ? secj->valueint : 0;
    _append(out_text, out_size, &used,
            "Beacon %s  tipo=%s  %ds\n%s\n%s",
            (iface && iface[0]) ? iface : "hci1",
            cJSON_IsString(typej) ? typej->valuestring : "?",
            sc,
            cJSON_IsString(labj) ? labj->valuestring : "",
            ok_b ? "en el aire (no-conectable)" : "no salio al aire");
    if (cJSON_IsString(warn) && warn->valuestring[0]) {
        _append(out_text, out_size, &used, "\nAviso: %s", warn->valuestring);
    }
    cJSON_Delete(root);
    return ok_b;
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

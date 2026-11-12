"""
proc.py — ejecucion de subprocesos con captura + timeout, compartida por los
scripts de red (wifi_ops, net_ops, wifi_scan) y cualquier otro dispatcher.

Contrato (igual que los `_run` locales que reemplaza):
  * Devuelve el `subprocess.CompletedProcess` en exito.
  * Devuelve None si el proceso no se pudo lanzar o se paso del timeout.
    (Nunca lanza: el que llama decide que hacer con None.)

El timeout default NO se fija aqui a proposito: cada modulo tiene su propio
default historico (wifi=10s, net=30s). Cada uno mantiene su wrapper `_run`
con ese default y delega el trabajo real a esta funcion.
"""
import subprocess


def run(cmd, timeout, stdin=None):
    """Ejecuta `cmd` (lista de args, NUNCA shell) y captura stdout/stderr.
    `timeout` en segundos. None si fallo el lanzamiento o expiro el tiempo.

    `stdin`: por defecto None = hereda el stdin del proceso (comportamiento
    historico de wifi/net). bt_ops pasa subprocess.DEVNULL a proposito: bajo
    systemd el servicio corre sin TTY y btmgmt 5.82 se cuelga si no detecta un
    stdin cerrado/pipe (bug documentado en bt_ops._run)."""
    try:
        return subprocess.run(cmd, capture_output=True, text=True,
                              timeout=timeout, stdin=stdin)
    except Exception:
        return None

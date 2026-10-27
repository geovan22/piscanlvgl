"""
action_log.py - Logging centralizado de operaciones de PiScan.

Objetivo: dejar un rastro de **cada invocacion** de un dispatcher Python
(bt_ops, wifi_ops, net_ops, diag, ...) en la tabla action_log con:
  - section  (pantalla / modulo que lo llamo)
  - tool     (sub-comando, p.ej. "scan", "deauth", "wps_attack")
  - inputs   (argv / parametros)
  - outputs  (salida JSON, truncada)
  - error    (si el JSON trae ok=false)
  - result   ("ok" | "error" | "warn")
  - duration_ms

Diseño:
  * log_action()           -> helper directo (desde dentro de las ops).
  * run_cli_with_log()     -> envoltorio para `if __name__ == "__main__":`
                              captura stdout + exit code sin pedir que cada
                              rama del main() llame al log a mano.

Reglas:
  * NUNCA tira el script si la DB no esta disponible (todo bajo try/except).
  * NUNCA pisa el stdout del CLI (es el contrato con el lado C).
  * El exit code y la salida siguen siendo exactamente los mismos que antes.
"""
import os
import sys
import io
import json
import time
import contextlib


_OUT_LIMIT    = 2000
_IN_LIMIT     = 2000
_ERR_LIMIT    = 1000
_SECTION_LIM  = 32
_TOOL_LIM     = 48


def _ensure_app_in_path():
    """Permite usar este modulo desde scripts (ej. app/wifi/wifi_ops.py)."""
    if "db.models" in sys.modules:
        return
    here = os.path.dirname(os.path.abspath(__file__))
    app_dir = os.path.abspath(os.path.join(here, ".."))
    if app_dir not in sys.path:
        sys.path.insert(0, app_dir)


def _to_str(x, lim):
    if x is None:
        return None
    if isinstance(x, (bytes, bytearray)):
        try:
            x = x.decode("utf-8", errors="replace")
        except Exception:
            x = str(x)
    if not isinstance(x, str):
        try:
            x = json.dumps(x, ensure_ascii=True, default=str)
        except Exception:
            x = str(x)
    return x[:lim] if lim and len(x) > lim else x


def log_action(section, tool, inputs=None, outputs=None, error=None,
               result="ok", duration_ms=None):
    """Inserta una fila en action_log. Silencia cualquier fallo de DB."""
    try:
        _ensure_app_in_path()
        from db.models import get_session, ActionLog
        s = get_session()
        s.add(ActionLog(
            section=(section or "")[:_SECTION_LIM],
            tool=(tool or "")[:_TOOL_LIM],
            inputs=_to_str(inputs, _IN_LIMIT),
            outputs=_to_str(outputs, _OUT_LIMIT),
            error=_to_str(error, _ERR_LIMIT),
            result=(result or "ok")[:16],
            duration_ms=duration_ms,
        ))
        s.commit()
        s.close()
    except Exception:
        pass


def _extract_tool_and_error(argv, stdout_text, exit_code):
    """Infiere el sub-comando y el error del JSON de salida."""
    tool = argv[0] if argv else ""
    error = None
    try:
        data = json.loads(stdout_text) if stdout_text.strip() else {}
        if isinstance(data, dict):
            if data.get("ok") is False and data.get("error"):
                error = str(data["error"])
    except Exception:
        pass
    if exit_code != 0 and not error and stdout_text.strip():
        error = stdout_text.strip()[-_ERR_LIMIT:]
    return tool, error


def run_cli_with_log(section, main_fn):
    """Envuelve main() de un dispatcher Python: captura stdout + exit code,
    logea en action_log y propaga la salida/exit como siempre.

    Uso tipico:
        if __name__ == "__main__":
            from db.action_log import run_cli_with_log
            run_cli_with_log("bluetooth", main)

    Garantias:
      * stdout final == el que hubiera impreso el main() directo.
      * exit code == el mismo.
      * un fallo del log NO cambia nada de lo anterior.
    """
    t0 = time.time()
    buf = io.StringIO()
    exit_code = 0
    exc_text = None
    try:
        with contextlib.redirect_stdout(buf):
            main_fn()
    except SystemExit as e:
        try:
            exit_code = int(e.code) if e.code is not None else 0
        except Exception:
            exit_code = 1
    except Exception as e:
        exit_code = 2
        exc_text = f"{type(e).__name__}: {e}"
    stdout_text = buf.getvalue()
    # Reemitir la salida ANTES de intentar logear (si log falla, no pasa nada).
    try:
        sys.stdout.write(stdout_text)
        sys.stdout.flush()
    except Exception:
        pass

    argv = sys.argv[1:]
    tool, error = _extract_tool_and_error(argv, stdout_text, exit_code)
    if exc_text and not error:
        error = exc_text
    result = "ok" if exit_code == 0 and not error else "error"
    dur = int((time.time() - t0) * 1000)
    log_action(section, tool,
               inputs=" ".join(argv) if argv else "",
               outputs=stdout_text,
               error=error, result=result, duration_ms=dur)
    sys.exit(exit_code)

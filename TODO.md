# TODO — PiScan

> Lista de trabajo viva para no perder el hilo entre sesiones. Versionada en el
> repo. Convención: `[x]` hecho, `[ ]` pendiente, `[~]` en progreso/a medias.
> El roadmap grande y el "por qué" de las decisiones viven en `CLAUDE.md`
> (§4 PENDIENTE, §8 Roadmap). Acá va el detalle operativo de la fase en curso.

---

## Fase 0 — Terminar WiFi / Config (en curso)

### 1. text_input.c (teclado/entrada reutilizable)
- [x] Matar el parpadeo del cursor (reenvío de `FOCUSED` tras `anim_duration=0`).
      *(ya venía del commit anterior)*
- [x] Botón **ver/ocultar contraseña** (ojo): aparece solo en modo password,
      alterna `lv_textarea_set_password_mode` y cambia etiqueta `ver`↔`ocu`.
      Como la pantalla de conectar WiFi usa este mismo componente
      (`text_input_show(..., is_password=1, ...)` en `ui_shell.c`), el botón ya
      aplica ahí — es el único camino de contraseña.
- [x] `password_show_time = 0`: evita el redibujado tardío (~1.5 s) que
      reemplazaba la letra en claro por el bullet y se veía como que el texto
      "se movía"/parpadeaba y pesaba por letra sobre el SPI del KeDei.

### 2. Sección Config (editar config en DB)
- [ ] UI de la sección `config` para leer/editar la tabla **Config** (clave/valor).
- [ ] Primer parámetro editable: **`deauth_duration`** (segundos).
- [ ] Contrato con `db_tool.py` para get/set de Config.

### 3. Deauth sostenido en la UI (Patrón A)
- [ ] Un solo botón **Deauth** → vuelve a mostrar el listado de redes →
      seleccionar 1 → usa `deauth_duration` de la DB como `duration`.

---

## Por revisar (confirmar en pantalla; revertir si molesta)
- [ ] **Scroll animado del textarea** (`lv_obj_scroll_to_x(..., LV_ANIM_ON)` en
      `lv_textarea_scroll_to_cusor_pos`, en cada tecla). Con `password_show_time=0`
      debería notarse mucho menos; en claves **largas** que exceden el ancho del
      campo (~402 px) todavía puede animar el desplazamiento. Si molesta: apagarlo
      sin hackear internals de LVGL (evaluar opciones) o aceptar como limitación
      del SPI por-pixel (documentada en CLAUDE.md §2/§6).
- [ ] Verificar en la Pi que el botón **ver/ocultar** no pisa el campo ni el
      teclado (campo 402 px + ojo 52 px a la derecha, y=16).

## Por revertir (si una prueba sale mal)
- *(vacío por ahora — anotar aquí cualquier cambio a deshacer si rompe algo)*

---

## Hecho (histórico breve)
> El detalle completo de lo ya en producción está en `CLAUDE.md §3`.
- Fase 0 · ítem 1 (text_input: cursor + ojo + show_time) — ver arriba.

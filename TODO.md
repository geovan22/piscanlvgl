# TODO — PiScan

> Lista de trabajo viva para no perder el hilo entre sesiones. Versionada en el
> repo. Convención: `[x]` hecho, `[ ]` pendiente, `[~]` en progreso/a medias.
> El roadmap grande y el "por qué" de las decisiones viven en `CLAUDE.md`
> (§4 PENDIENTE, §8 Roadmap). Acá va el detalle operativo de la fase en curso.

---

## Fase 0 — Terminar WiFi / Config (en curso)

### 1. text_input.c (teclado/entrada reutilizable) — ✅ CERRADO (validado en pantalla)
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
- [x] Texto que **subía y bajaba** dentro del campo: la fuente por defecto
      (montserrat_14) no cabía en los 30px → el campo scrolleaba en Y. Fijado
      a **montserrat_10 + pad_ver 2** → el texto entra holgado, `scroll_max_y=0`
      y el bobbing vertical desaparece. (Confirmado por el usuario que el ojo y
      las teclas andan; validar este fix en pantalla.)

### 2. Sección Config (editar config en DB) — [~] a validar en pantalla
- [x] Contrato con DB ya existía: `db_tool.py config get/set/list` +
      `db_client.c` (`db_config_get/set`). Incluido `db_client.h` en ui_shell.c.
- [x] UI de la sección `config` (rama en `enter_section`): título, valor actual,
      descripción, botones **-5s / +5s**.
- [x] Primer parámetro editable: **`deauth_duration`** (0–600 s, paso 5,
      key=`deauth_duration`, cat=`wifi`). 0 = ráfaga; >0 = sostenido.
      Carga al entrar, guarda en DB en cada ajuste, feedback en el footer.
- [ ] **Validar en pantalla**: que carga el valor, que -/+ ajustan y persisten
      (reabrir Config debe mostrar el último valor).
- Nota: guarda en cada tap (fork+exec python ~150ms). Si se siente lento al
  tap repetido, pasar a guardar-al-salir o con botón "Guardar" (anotar en
  "Por revisar").

### 3. Deauth sostenido en la UI (Patrón A)
- [ ] Un solo botón **Deauth** → vuelve a mostrar el listado de redes →
      seleccionar 1 → usa `deauth_duration` de la DB como `duration`.

---

## Por revisar (confirmar en pantalla; revertir si molesta)
- [ ] **Scroll animado HORIZONTAL del textarea** (`lv_obj_scroll_to_x(..., LV_ANIM_ON)`
      en `lv_textarea_scroll_to_cusor_pos`, en cada tecla). Solo aplica a claves
      **largas** que exceden el ancho del campo (~402 px; con montserrat_10 entran
      más chars antes de scrollear). Si molesta: apagarlo sin hackear internals de
      LVGL (evaluar opciones) o aceptar como limitación del SPI por-pixel
      (documentada en CLAUDE.md §2/§6). El bobbing **vertical** ya quedó resuelto.
- [ ] Verificar en la Pi que el botón **ver/ocultar** no pisa el campo ni el
      teclado (campo 402 px + ojo 52 px a la derecha, y=16).

## Por revertir (si una prueba sale mal)
- *(vacío por ahora — anotar aquí cualquier cambio a deshacer si rompe algo)*

---

## Hecho (histórico breve)
> El detalle completo de lo ya en producción está en `CLAUDE.md §3`.
- Fase 0 · ítem 1 (text_input: cursor + ojo + show_time) — ver arriba.

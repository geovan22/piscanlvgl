Plantillas de portal del honeypot (PiScan) — SOLO laboratorio / análisis.

- Cada archivo .html de esta carpeta es un portal informativo que se le muestra
  al dispositivo que se conecta al AP honeypot.
- NO se piden ni se guardan contraseñas: el honeypot solo registra telemetría
  (MAC, fabricante, IP, hostname, SO por User-Agent, dominios DNS) para análisis
  en una auditoría autorizada.
- El texto {{SSID}} se reemplaza por el nombre del AP.
- Para elegir cuál usar: en la DB, Config key `honeypot_portal` = nombre de
  archivo (ej. `bienvenida.html`). Vacío = portal por defecto embebido.
  Para activar el puente de internet: Config `honeypot_bridge` = `1`.
  (Más adelante estos se podrán elegir desde la pantalla Config.)

Para agregar uno nuevo: copiá este formato, cambiá el contenido, subilo al repo.
Si incluís un <form>, su envío NO se procesa como credenciales (solo cuenta la
visita); mantené el portal informativo.

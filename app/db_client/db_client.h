#ifndef DB_CLIENT_H
#define DB_CLIENT_H

/* Todas retornan 1 en exito, 0 en fallo/error. */

int db_config_get(const char *key, char *value_out, int value_out_size);
/* out_error (si no es NULL) recibe el error de db_tool.py cuando falla (ej.
 * "attempt to write a readonly database"). category puede ser NULL. */
int db_config_set(const char *key, const char *value, const char *category,
                  char *out_error, int error_size);

int db_credential_set(const char *cred_type, const char *plaintext);
int db_credential_verify(const char *cred_type, const char *plaintext); /* 1=valido, 0=invalido o error */
int db_credential_exists(const char *cred_type);

#endif

#ifndef UPLOADER_TLS_H
#define UPLOADER_TLS_H

#include <stddef.h>
#include <sys/types.h>
#include <openssl/ssl.h>

/* Worker가 SSL_CTX를 소유하고, 요청마다 SSL과 TCP fd를 별도로 소유한다. */
typedef struct {
    SSL *ssl;
    int fd;
    long long transfer_deadline_ms;
    int stall_timeout_ms;
} uploader_tls_connection_t;

SSL_CTX *uploader_tls_context_create(const char *ca_file, char *error, size_t size);
int uploader_tls_connect(SSL_CTX *context, uploader_tls_connection_t *connection,
                         int fd, const char *host, long long connect_deadline_ms,
                         int transfer_timeout_ms, int stall_timeout_ms,
                         char *error, size_t size);
ssize_t uploader_tls_write(uploader_tls_connection_t *connection,
                           const void *data, size_t length, char *error, size_t size);
ssize_t uploader_tls_read(uploader_tls_connection_t *connection,
                          void *data, size_t length, char *error, size_t size);
void uploader_tls_close(uploader_tls_connection_t *connection);
long long uploader_tls_now_ms(void);

#endif

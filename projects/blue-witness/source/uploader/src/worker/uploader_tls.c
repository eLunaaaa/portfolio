#define _POSIX_C_SOURCE 200809L

#include "uploader_tls.h"

#include <arpa/inet.h>
#include <errno.h>
#include <limits.h>
#include <poll.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <time.h>
#include <openssl/x509_vfy.h>

#include "uploader_watchdog.h"

long long uploader_tls_now_ms(void)
{
    struct timespec now;
    if (clock_gettime(CLOCK_MONOTONIC, &now) != 0) return -1;
    return (long long)now.tv_sec * 1000LL + now.tv_nsec / 1000000LL;
}

static int wait_socket(int fd, short events, long long deadline_ms,
                       char *error, size_t size, const char *category)
{
    struct pollfd descriptor = { .fd = fd, .events = events };
    for (;;) {
        long long remaining = deadline_ms - uploader_tls_now_ms();
        int result;
        if (remaining <= 0 || remaining > INT_MAX) {
            snprintf(error, size, "%s: timeout", category);
            return -1;
        }
        uploader_watchdog_progress((uint32_t)remaining + 10000U);
        result = poll(&descriptor, 1, (int)remaining);
        if (result < 0 && errno == EINTR) continue;
        if (result <= 0 || (descriptor.revents & (POLLERR | POLLNVAL)) != 0 ||
            (descriptor.revents & events) == 0) {
            snprintf(error, size, "%s: socket wait failed", category);
            return -1;
        }
        return 0;
    }
}

SSL_CTX *uploader_tls_context_create(const char *ca_file, char *error, size_t size)
{
    SSL_CTX *context;
    if (ca_file == NULL || *ca_file == '\0') {
        snprintf(error, size, "TLS_CA_LOAD_FAILED: CA file path is empty");
        return NULL;
    }
    context = SSL_CTX_new(TLS_client_method());
    if (context == NULL) {
        snprintf(error, size, "TLS_CA_LOAD_FAILED: TLS context creation failed");
        return NULL;
    }
    if (SSL_CTX_set_min_proto_version(context, TLS1_2_VERSION) != 1 ||
        SSL_CTX_load_verify_locations(context, ca_file, NULL) != 1) {
        snprintf(error, size, "TLS_CA_LOAD_FAILED: cannot load configured CA file");
        SSL_CTX_free(context);
        return NULL;
    }
    SSL_CTX_set_verify(context, SSL_VERIFY_PEER, NULL);
    return context;
}

/* OpenSSL 검증 API가 IP SAN 또는 DNS SAN을 검사한다. 직접 인증서 필드를 파싱하지 않는다. */
int uploader_tls_connect(SSL_CTX *context, uploader_tls_connection_t *connection,
                         int fd, const char *host, long long connect_deadline_ms,
                         int transfer_timeout_ms, int stall_timeout_ms,
                         char *error, size_t size)
{
    unsigned char ip_bytes[16];
    int is_ip = inet_pton(AF_INET, host, ip_bytes) == 1;
    SSL *ssl = SSL_new(context);
    if (ssl == NULL) {
        snprintf(error, size, "TLS_HANDSHAKE_FAILED: SSL allocation failed");
        return -1;
    }
    if (SSL_set_fd(ssl, fd) != 1 ||
        (is_ip ? X509_VERIFY_PARAM_set1_ip_asc(SSL_get0_param(ssl), host) != 1
               : (SSL_set1_host(ssl, host) != 1 ||
                  SSL_set_tlsext_host_name(ssl, host) != 1))) {
        snprintf(error, size, "TLS_HOST_VERIFY_FAILED: invalid server identity");
        SSL_free(ssl);
        return -1;
    }
    for (;;) {
        int result = SSL_connect(ssl);
        int ssl_error;
        if (result == 1) {
            if (SSL_get_verify_result(ssl) == X509_V_OK) break;
            snprintf(error, size, "TLS_CERT_VERIFY_FAILED: certificate rejected");
            SSL_free(ssl);
            return -1;
        }
        ssl_error = SSL_get_error(ssl, result);
        if (ssl_error == SSL_ERROR_WANT_READ || ssl_error == SSL_ERROR_WANT_WRITE) {
            if (wait_socket(fd, ssl_error == SSL_ERROR_WANT_READ ? POLLIN : POLLOUT,
                            connect_deadline_ms, error, size,
                            "TLS_HANDSHAKE_FAILED") == 0) continue;
        } else if (SSL_get_verify_result(ssl) != X509_V_OK) {
            long verify_error = SSL_get_verify_result(ssl);
            snprintf(error, size, "%s: %s",
                     verify_error == X509_V_ERR_IP_ADDRESS_MISMATCH ||
                     verify_error == X509_V_ERR_HOSTNAME_MISMATCH
                         ? "TLS_HOST_VERIFY_FAILED" : "TLS_CERT_VERIFY_FAILED",
                     X509_verify_cert_error_string(verify_error));
        } else {
            snprintf(error, size, "TLS_HANDSHAKE_FAILED: TLS negotiation failed");
        }
        SSL_free(ssl);
        return -1;
    }
    connection->ssl = ssl;
    connection->fd = fd;
    connection->transfer_deadline_ms = uploader_tls_now_ms() + transfer_timeout_ms;
    connection->stall_timeout_ms = stall_timeout_ms;
    return 0;
}

static ssize_t tls_io(uploader_tls_connection_t *connection, void *data, size_t length,
                      int writing, char *error, size_t size)
{
    long long stall_deadline = uploader_tls_now_ms() + connection->stall_timeout_ms;
    const char *category = writing ? "TLS_WRITE_FAILED" : "TLS_READ_FAILED";
    for (;;) {
        if (uploader_tls_now_ms() >= stall_deadline ||
            uploader_tls_now_ms() >= connection->transfer_deadline_ms) {
            snprintf(error, size, "%s: timeout", category);
            return -1;
        }
        int result = writing ? SSL_write(connection->ssl, data, (int)length)
                             : SSL_read(connection->ssl, data, (int)length);
        int ssl_error;
        if (result > 0) {
            uploader_watchdog_progress((uint32_t)connection->stall_timeout_ms + 10000U);
            return result;
        }
        ssl_error = SSL_get_error(connection->ssl, result);
        if (!writing && ssl_error == SSL_ERROR_ZERO_RETURN) return 0;
        if (ssl_error == SSL_ERROR_SYSCALL && errno == EINTR) continue;
        if (ssl_error == SSL_ERROR_WANT_READ || ssl_error == SSL_ERROR_WANT_WRITE) {
            long long deadline = stall_deadline < connection->transfer_deadline_ms
                ? stall_deadline : connection->transfer_deadline_ms;
            if (wait_socket(connection->fd,
                            ssl_error == SSL_ERROR_WANT_READ ? POLLIN : POLLOUT,
                            deadline, error, size, category) == 0) continue;
        } else {
            snprintf(error, size, "%s: TLS connection failed", category);
        }
        return -1;
    }
}

ssize_t uploader_tls_write(uploader_tls_connection_t *connection,
                           const void *data, size_t length, char *error, size_t size)
{
    return tls_io(connection, (void *)data, length, 1, error, size);
}

ssize_t uploader_tls_read(uploader_tls_connection_t *connection,
                          void *data, size_t length, char *error, size_t size)
{
    return tls_io(connection, data, length, 0, error, size);
}

void uploader_tls_close(uploader_tls_connection_t *connection)
{
    if (connection->ssl != NULL) {
        (void)SSL_shutdown(connection->ssl); /* nonblocking best-effort */
        SSL_free(connection->ssl);
        connection->ssl = NULL;
    }
}

/* 파일 역할: OpenSSL 기반 업로더 TLS 연결·인증서 검증·제한 시간 송수신 계층.
 * HTTP 계층이 제공하는 비차단 소켓을 사용하고 CA·서버 이름을 검증한 뒤 암호화한다.
 * 전체 전송 기한과 무진행 기한을 함께 적용해 작업자가 소켓에서 무한 대기하지 않게 한다.
 */
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

/*
 * 기능: 시간 제한 계산에 쓸 단조 증가 시각을 읽는다.
 * 입력: 없음.
 * 출력: 밀리초 시각, clock_gettime 실패 시 -1.
 * 구조·이유: CLOCK_MONOTONIC을 사용해 시스템 벽시계 보정이 전송 기한 계산에 영향을 주지 않게 한다.
 */
long long uploader_tls_now_ms(void)
{
    struct timespec now;
    if (clock_gettime(CLOCK_MONOTONIC, &now) != 0) return -1;
    return (long long)now.tv_sec * 1000LL + now.tv_nsec / 1000000LL;
}

/*
 * 기능: 기한 내에 소켓이 읽기·쓰기 가능한 상태가 되는지 기다린다.
 * 입력: fd, poll 이벤트, 절대 기한 deadline_ms, 오류 버퍼와 크기·분류명.
 * 출력: 준비 시 0, 시간 초과·소켓 오류 시 -1과 오류 문자열.
 * 구조·이유: 남은 시간을 계산해 poll하고 EINTR는 재시도한다. 대기 전에 watchdog 진행 기한을 갱신한다.
 */
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

/*
 * 기능: 서버 인증에 사용할 TLS 클라이언트 컨텍스트를 생성한다.
 * 입력: CA 파일 경로와 오류 버퍼·크기.
 * 출력: SSL_CTX 포인터 또는 실패 시 NULL과 오류 문자열.
 * 구조·이유: 최소 TLS 1.2와 서버 인증서 검증을 설정한다. 성공한 컨텍스트는 호출자가 SSL_CTX_free로 해제한다.
 */
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
/*
 * 기능: 서버 인증서와 서버 신원을 검증하며 TLS 연결을 수립한다.
 * 입력: TLS context, 출력 connection, 비차단 fd, host, 접속·전송·무진행 기한과 오류 버퍼.
 * 출력: 성공 0과 초기화된 connection, 실패 -1과 오류 문자열.
 * 구조·이유: 호스트가 IPv4면 IP SAN, 그 외는 호스트 이름과 SNI를 설정한다. WANT_READ/WRITE는 접속 기한까지 poll로 재시도한다.
 */
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

/*
 * 기능: TLS 읽기·쓰기의 재시도와 시간 제한을 공통 처리한다.
 * 입력: connection, 데이터 포인터·길이, 쓰기 여부 writing, 오류 버퍼·크기.
 * 출력: 처리한 바이트 수, 읽기 정상 종료 0, 실패 -1.
 * 구조·이유: SSL_read/write 후 WANT_READ/WRITE를 대기한다. 한 번의 성공량만 반환하므로 전체 길이 누적은 상위 계층이 담당한다.
 */
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

/*
 * 기능: TLS 연결로 데이터 일부를 전송한다.
 * 입력: connection, 읽기 전용 data·length, 오류 버퍼·크기.
 * 출력: 실제 송신 바이트 수 또는 -1.
 * 구조·이유: 공통 tls_io를 쓰기 모드로 호출한다. 부분 송신이면 호출자가 남은 데이터를 다시 전달한다.
 */
ssize_t uploader_tls_write(uploader_tls_connection_t *connection,
                           const void *data, size_t length, char *error, size_t size)
{
    return tls_io(connection, (void *)data, length, 1, error, size);
}

/*
 * 기능: TLS 연결에서 데이터를 읽는다.
 * 입력: connection, 출력 data·버퍼 길이, 오류 버퍼·크기.
 * 출력: 수신 바이트 수, 정상 TLS 종료 0, 실패 -1.
 * 구조·이유: 공통 tls_io를 읽기 모드로 사용하며 HTTP 메시지 경계 해석은 상위 계층이 담당한다.
 */
ssize_t uploader_tls_read(uploader_tls_connection_t *connection,
                          void *data, size_t length, char *error, size_t size)
{
    return tls_io(connection, data, length, 0, error, size);
}

/*
 * 기능: TLS 세션을 종료하고 OpenSSL 객체를 해제한다.
 * 입력: connection.
 * 출력: connection->ssl을 NULL로 변경; 반환값 없음.
 * 구조·이유: 비차단 shutdown을 한 번 시도한 뒤 SSL_free한다. 소켓 fd는 여기서 닫지 않으므로 호출자가 별도로 관리한다.
 */
void uploader_tls_close(uploader_tls_connection_t *connection)
{
    if (connection->ssl != NULL) {
        (void)SSL_shutdown(connection->ssl); /* nonblocking best-effort */
        SSL_free(connection->ssl);
        connection->ssl = NULL;
    }
}

#define _POSIX_C_SOURCE 200809L

#include "uploader_http.h"

#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <netdb.h>
#include <poll.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/time.h>
#include <sys/types.h>
#include <unistd.h>

#include "uploader_settings.h"
#include "uploader_watchdog.h"
#include "uploader_ack.h"
#include "uploader_tls.h"

#ifndef MSG_NOSIGNAL
#define MSG_NOSIGNAL 0
#endif

typedef struct {
    char host[256];
    char port[16];
    int use_tls;
} http_address_t;

/* Worker만 접근한다. Context는 Worker lifetime, SSL은 요청 lifetime이다. */
static _Thread_local SSL_CTX *worker_tls_context;
static _Thread_local uploader_tls_connection_t active_tls_connection;

/* =========================================================================
 * #### HTTP 주소와 오류 문자열 처리
 * ========================================================================= */

/**
 * @brief 호출자가 제공한 error buffer에 단일 오류 문자열을 기록함.
 * @return 반환값 없음. 사용할 수 없는 buffer이면 아무 작업도 하지 않음.
 */
static void set_error(
    char *error,
    size_t error_size,
    const char *message)
{
    if (error == NULL || error_size == 0U) {
        return;
    }

    snprintf(
        error,
        error_size,
        "%s",
        message != NULL ? message : "");
}

/**
 * @brief HTTP/HTTPS URL을 host와 port로 분리함.
 * @param address 성공 시 DNS/IP host와 TCP port를 돌려받음.
 * @return 형식과 길이가 유효하면 0, 지원하지 않는 URL이면 -1.
 */
static int parse_server_url(
    const char *server_url,
    http_address_t *address,
    char *error,
    size_t error_size)
{
    static const char http_prefix[] = "http://";
    static const char https_prefix[] = "https://";
    int use_tls;

    char authority[256];
    char *colon;
    char *slash;
    char *port_end;
    long port_number;
    size_t authority_length;

    if (server_url == NULL || address == NULL) {
        set_error(error, error_size, "invalid server URL argument");
        return -1;
    }

    if (strncmp(server_url, https_prefix, sizeof(https_prefix) - 1U) == 0) {
        server_url += sizeof(https_prefix) - 1U;
        use_tls = 1;
    } else if (strncmp(server_url, http_prefix, sizeof(http_prefix) - 1U) == 0) {
        server_url += sizeof(http_prefix) - 1U;
        use_tls = 0;
    } else {
        set_error(error, error_size, "server URL must use http:// or https://");
        return -1;
    }

    if (*server_url == '\0') {
        set_error(error, error_size, "server host is empty");
        return -1;
    }

    slash = strchr(server_url, '/');

    if (slash != NULL) {
        const char *check = slash;

        while (*check != '\0') {
            if (*check != '/') {
                set_error(
                    error,
                    error_size,
                    "server URL must not include an API path");

                return -1;
            }

            check++;
        }

        authority_length = (size_t)(slash - server_url);
    } else {
        authority_length = strlen(server_url);
    }

    if (authority_length == 0U ||
        authority_length >= sizeof(authority)) {
        set_error(error, error_size, "server authority is invalid");
        return -1;
    }

    memcpy(authority, server_url, authority_length);
    authority[authority_length] = '\0';

    colon = strrchr(authority, ':');

    memset(address, 0, sizeof(*address));
    address->use_tls = use_tls;

    if (colon == NULL) {
        snprintf(address->host, sizeof(address->host), "%s", authority);
        snprintf(address->port, sizeof(address->port), "%s", use_tls ? "443" : "80");
        return 0;
    }

    *colon = '\0';
    colon++;

    if (authority[0] == '\0' || colon[0] == '\0') {
        set_error(error, error_size, "server host or port is empty");
        return -1;
    }

    errno = 0;
    port_number = strtol(colon, &port_end, 10);

    if (errno != 0 ||
        *port_end != '\0' ||
        port_number < 1 ||
        port_number > 65535) {
        set_error(error, error_size, "server port is invalid");
        return -1;
    }

    snprintf(address->host, sizeof(address->host), "%s", authority);
    snprintf(address->port, sizeof(address->port), "%ld", port_number);

    return 0;
}

int http_client_prepare(const char *server_url, const char *ca_file,
                        char *error, size_t error_size)
{
    http_address_t address;
    if (parse_server_url(server_url, &address, error, error_size) != 0) return -1;
    if (!address.use_tls || worker_tls_context != NULL) return 0;
    worker_tls_context = uploader_tls_context_create(ca_file, error, error_size);
    return worker_tls_context != NULL ? 0 : -1;
}

void http_client_cleanup(void)
{
    uploader_tls_close(&active_tls_connection);
    SSL_CTX_free(worker_tls_context);
    worker_tls_context = NULL;
}

/* =========================================================================
 * #### Timeout이 있는 TCP 연결
 * ========================================================================= */

/**
 * @brief nonblocking connect 완료를 poll로 제한 시간 동안 기다림.
 * @return 연결이 성립하면 0, timeout/socket 오류면 -1.
 */
static int wait_for_connect(
    int socket_fd,
    int timeout_ms)
{
    struct pollfd descriptor;
    int socket_error = 0;
    socklen_t socket_error_size = sizeof(socket_error);
    int result;

    memset(&descriptor, 0, sizeof(descriptor));
    descriptor.fd = socket_fd;
    descriptor.events = POLLOUT;

    uploader_watchdog_progress((uint32_t)timeout_ms + 10000U);
    do {
        result = poll(&descriptor, 1, timeout_ms);
    } while (result < 0 && errno == EINTR);

    if (result <= 0) {
        return -1;
    }

    if (getsockopt(
            socket_fd,
            SOL_SOCKET,
            SO_ERROR,
            &socket_error,
            &socket_error_size) != 0) {
        return -1;
    }

    if (socket_error != 0) {
        errno = socket_error;
        return -1;
    }

    return 0;
}

/**
 * @brief 연결된 socket에 송수신 timeout을 설정함.
 * @return 반환값 없음. setsockopt는 best-effort로 적용함.
 */
static _Thread_local uint32_t transfer_progress_ms = UPLOADER_LOCAL_PROGRESS_MS;

static void set_transfer_timeout(
    int socket_fd,
    int timeout_ms)
{
    struct timeval timeout;

    timeout.tv_sec = timeout_ms / 1000;
    timeout.tv_usec = (timeout_ms % 1000) * 1000;
    transfer_progress_ms = (uint32_t)timeout_ms + 10000U;
    uploader_watchdog_progress(transfer_progress_ms);

    setsockopt(
        socket_fd,
        SOL_SOCKET,
        SO_RCVTIMEO,
        &timeout,
        sizeof(timeout));

    setsockopt(
        socket_fd,
        SOL_SOCKET,
        SO_SNDTIMEO,
        &timeout,
        sizeof(timeout));
}

/**
 * @brief URL을 해석하고 주소 후보를 순회하여 timeout 내 TCP 연결을 만듦.
 * @return 성공한 socket fd, 모든 주소가 실패하면 -1.
 * @note 성공 fd의 소유권은 호출자에게 넘어가며 반드시 close해야 함.
 */
static int connect_server(
    const http_address_t *address,
    int transfer_timeout_ms,
    char *error,
    size_t error_size)
{
    struct addrinfo hints;
    struct addrinfo *addresses = NULL;
    struct addrinfo *current;
    int socket_fd = -1;
    int original_flags;
    int result;
    long long connect_deadline_ms = uploader_tls_now_ms() + UPLOADER_CONNECT_TIMEOUT_MS;

    if (address->use_tls && worker_tls_context == NULL) {
        set_error(error, error_size, "TLS_CA_LOAD_FAILED: TLS transport is not configured");
        return -1;
    }

    memset(&hints, 0, sizeof(hints));
    hints.ai_family = AF_INET;
    hints.ai_socktype = SOCK_STREAM;

    /* Resolver time is not covered by SO_RCVTIMEO/SO_SNDTIMEO. */
    uploader_watchdog_progress(30000U);
    result = getaddrinfo(
        address->host,
        address->port,
        &hints,
        &addresses);

    if (result != 0) {
        snprintf(
            error,
            error_size,
            "resolve server: %s",
            gai_strerror(result));

        return -1;
    }

    for (current = addresses;
         current != NULL;
         current = current->ai_next) {
        socket_fd = socket(
            current->ai_family,
            current->ai_socktype,
            current->ai_protocol);

        if (socket_fd < 0) {
            continue;
        }

        original_flags = fcntl(socket_fd, F_GETFL, 0);

        if (original_flags < 0 ||
            fcntl(
                socket_fd,
                F_SETFL,
                original_flags | O_NONBLOCK) != 0) {
            close(socket_fd);
            socket_fd = -1;
            continue;
        }

        result = connect(
            socket_fd,
            current->ai_addr,
            current->ai_addrlen);

        if (result != 0 && errno == EINPROGRESS) {
            long long remaining = address->use_tls
                ? connect_deadline_ms - uploader_tls_now_ms()
                : UPLOADER_CONNECT_TIMEOUT_MS;
            if (remaining <= 0) {
                errno = ETIMEDOUT;
                close(socket_fd);
                socket_fd = -1;
                break;
            }
            result = wait_for_connect(
                socket_fd,
                (int)remaining);
        }

        if (result == 0) {
            if (address->use_tls) {
                if (uploader_tls_connect(worker_tls_context, &active_tls_connection,
                        socket_fd, address->host, connect_deadline_ms,
                        transfer_timeout_ms,
                        transfer_timeout_ms < UPLOADER_TLS_STALL_TIMEOUT_MS
                            ? transfer_timeout_ms : UPLOADER_TLS_STALL_TIMEOUT_MS,
                        error, error_size) != 0) {
                    close(socket_fd);
                    socket_fd = -1;
                    freeaddrinfo(addresses);
                    return -1;
                }
                transfer_progress_ms = (uint32_t)UPLOADER_TLS_STALL_TIMEOUT_MS + 10000U;
            } else {
                fcntl(socket_fd, F_SETFL, original_flags);
                set_transfer_timeout(socket_fd, transfer_timeout_ms);
            }

            break;
        }

        close(socket_fd);
        socket_fd = -1;
    }

    freeaddrinfo(addresses);

    if (socket_fd < 0) {
        snprintf(
            error,
            error_size,
            "TCP_CONNECT_FAILED: connect %s:%s failed: %s",
            address->host,
            address->port,
            strerror(errno));

        return -1;
    }

    return socket_fd;
}

static void close_connection(int socket_fd)
{
    uploader_tls_close(&active_tls_connection);
    close(socket_fd);
}

/* =========================================================================
 * #### 제한된 메모리로 HTTP 요청 송신
 * ========================================================================= */

/**
 * @brief partial send와 EINTR를 처리하며 memory buffer 전체를 전송함.
 * @return 전체 전송 성공 시 0, socket 오류/연결 종료 시 -1.
 */
static int send_all(
    int socket_fd,
    const void *data,
    size_t data_size,
    char *error,
    size_t error_size)
{
    const unsigned char *cursor = data;
    size_t remaining = data_size;

    uploader_watchdog_progress(transfer_progress_ms);
    while (remaining > 0U) {
        ssize_t sent = active_tls_connection.ssl != NULL
            ? uploader_tls_write(&active_tls_connection, cursor, remaining,
                                 error, error_size)
            : send(socket_fd, cursor, remaining, MSG_NOSIGNAL);

        if (sent < 0 && errno == EINTR) {
            continue;
        }

        if (sent <= 0) {
            if (active_tls_connection.ssl == NULL)
                snprintf(error, error_size, "send failed: %s",
                         sent < 0 ? strerror(errno) : "connection closed");

            return -1;
        }

        cursor += (size_t)sent;
        remaining -= (size_t)sent;
        uploader_watchdog_progress(transfer_progress_ms);
    }

    return 0;
}

/**
 * @brief MP4 파일을 고정 64KiB buffer로 읽어 socket에 streaming 전송함.
 * @return 파일 전체 전송과 close 성공 시 0, 로컬 파일/socket 오류 시 -1.
 * @note 영상 전체를 heap 또는 stack에 올리지 않음.
 */
static int send_video_file(
    int socket_fd,
    const char *video_path,
    uint64_t expected_size,
    char *error,
    size_t error_size)
{
    unsigned char buffer[UPLOADER_HTTP_CHUNK_SIZE];
    FILE *video;
    uint64_t remaining = expected_size;

    video = fopen(video_path, "rb");

    if (video == NULL) {
        snprintf(
            error,
            error_size,
            "open video: %s",
            strerror(errno));

        return -1;
    }

    while (remaining > 0U) {
        size_t request_size =
            remaining < sizeof(buffer)
                ? (size_t)remaining
                : sizeof(buffer);

        size_t read_size = fread(
            buffer,
            1U,
            request_size,
            video);

        if (read_size == 0U) {
            snprintf(error, error_size, "video read incomplete");
            fclose(video);
            return -1;
        }

        if (send_all(
                socket_fd,
                buffer,
                read_size,
                error,
                error_size) != 0) {
            fclose(video);
            return -1;
        }

        remaining -= (uint64_t)read_size;
    }

    if (fclose(video) != 0) {
        snprintf(
            error,
            error_size,
            "close video: %s",
            strerror(errno));

        return -1;
    }

    return 0;
}

/* =========================================================================
 * #### 크기가 제한된 HTTP 응답 수신과 해석
 * ========================================================================= */

/**
 * @brief 서버가 연결을 닫을 때까지 응답을 고정 상한 buffer로 수신함.
 * @param response_size 실제로 받은 byte 수를 돌려받음.
 * @return NUL 종료 응답 수신 성공 시 0, timeout/초과/socket 오류 시 -1.
 */
static int receive_response(
    int socket_fd,
    char *response,
    size_t response_capacity,
    size_t *response_size,
    char *error,
    size_t error_size)
{
    size_t used = 0U;

    uploader_watchdog_progress(transfer_progress_ms);
    while (used + 1U < response_capacity) {
        ssize_t received = active_tls_connection.ssl != NULL
            ? uploader_tls_read(&active_tls_connection, response + used,
                                response_capacity - used - 1U,
                                error, error_size)
            : recv(socket_fd, response + used,
                   response_capacity - used - 1U, 0);

        if (received < 0 && errno == EINTR) {
            continue;
        }

        if (received < 0) {
            if (active_tls_connection.ssl == NULL)
                snprintf(error, error_size, "receive failed: %s", strerror(errno));

            return -1;
        }

        if (received == 0) {
            break;
        }

        used += (size_t)received;
        uploader_watchdog_progress(transfer_progress_ms);
    }

    if (used + 1U >= response_capacity) {
        set_error(error, error_size, "HTTP response exceeds limit");
        return -1;
    }

    if (memchr(response, '\0', used) != NULL) {
        set_error(error, error_size, "HTTP response contains NUL");
        return -1;
    }
    response[used] = '\0';
    *response_size = used;

    return 0;
}

/**
 * @brief HTTP status line과 header/body 경계를 찾아 상태와 body 포인터를 반환함.
 * @return 최소 응답 형식이 유효하면 0, 손상되거나 불완전하면 -1.
 */
static int parse_http_response(
    char *response,
    int *status_code,
    char **response_body,
    char *error,
    size_t error_size)
{
    char *body;
    char *cursor;
    char *line_end;
    char *length_end;
    unsigned long long content_length = 0U;
    int length_seen = 0;
    int parsed_status = 0;

    *status_code = 0;
    if (sscanf(response, "HTTP/%*u.%*u %d", &parsed_status) != 1 ||
        parsed_status < 100 || parsed_status > 599) {
        set_error(error, error_size, "invalid HTTP status line");
        return -1;
    }

    body = strstr(response, "\r\n\r\n");

    if (body == NULL) {
        set_error(error, error_size, "HTTP header terminator missing");
        return -1;
    }

    cursor = strstr(response, "\r\n");
    if (cursor == NULL) return -1;
    cursor += 2;
    while (cursor < body) {
        line_end = strstr(cursor, "\r\n");
        if (line_end == NULL || line_end > body) return -1;
        if (strncasecmp(cursor, "Content-Length:", 15U) == 0) {
            char *number = cursor + 15;
            while (*number == ' ' || *number == '\t') number++;
            if (length_seen || *number < '0' || *number > '9') return -1;
            errno = 0;
            content_length = strtoull(number, &length_end, 10);
            while (*length_end == ' ' || *length_end == '\t') length_end++;
            if (errno != 0 || length_end != line_end) return -1;
            length_seen = 1;
        } else if (strncasecmp(cursor, "Transfer-Encoding:", 18U) == 0) {
            set_error(error, error_size, "unsupported HTTP transfer encoding");
            return -1;
        }
        cursor = line_end + 2;
    }
    body += 4;
    if (!length_seen || content_length != (unsigned long long)strlen(body)) {
        set_error(error, error_size, "HTTP response body is incomplete or unframed");
        return -1;
    }
    *status_code = parsed_status;
    cursor = body;

    while (*cursor == ' ' ||
           *cursor == '\t' ||
           *cursor == '\r' ||
           *cursor == '\n') {
        cursor++;
    }

    *response_body = cursor;
    return 0;
}

/**
 * @brief leading whitespace 뒤 응답 body가 JSON object/array로 시작하는지 검사함.
 * @return JSON 형태이면 1, 빈 body 또는 다른 형식이면 0.
 */
static int response_is_json(const char *body)
{
    if (body == NULL) {
        return 0;
    }

    return body[0] == '{' || body[0] == '[';
}

/**
 * 서버 ACK를 호출자가 소유한 고정 크기 buffer로 복사한다.
 *
 * HTTP 응답 buffer는 이 함수의 stack에 있으므로 주소를 반환하면 dangling
 * pointer가 된다. 크기를 제한해 복사하면 heap 할당이나 소유권 혼동 없이
 * Worker가 로그에 남길 안정적인 근거를 얻는다. 현재 서버 README는 JSON 성공
 * 응답만 보장하므로 HTTP 2xx와 JSON object/array의 조합을 ACK 규약으로 삼는다.
 */
static int copy_ack_body(
    const char *body,
    char *ack_body,
    size_t ack_body_size,
    char *error,
    size_t error_size)
{
    size_t body_size;
    size_t index;

    if (body == NULL || ack_body == NULL || ack_body_size < 2U) {
        set_error(error, error_size, "invalid upload ACK buffer");
        return -1;
    }

    body_size = strlen(body);

    if (body_size >= ack_body_size) {
        set_error(error, error_size, "upload ACK exceeds log buffer");
        return -1;
    }

    memcpy(ack_body, body, body_size + 1U);

    /* 보기 좋게 줄바꿈된 JSON도 구조화 로그 event 하나가 한 줄에 유지되게 한다. */
    for (index = 0U; index < body_size; index++) {
        if (ack_body[index] == '\r' ||
            ack_body[index] == '\n' ||
            ack_body[index] == '\t') {
            ack_body[index] = ' ';
        }
    }

    return 0;
}

/* =========================================================================
 * #### FastAPI Health 요청
 * ========================================================================= */

/**
 * @brief GET /api/v1/health를 보내고 HTTP 200과 health JSON을 검증함.
 * @param http_status 응답 상태 또는 연결 전 실패 시 0을 돌려받음.
 * @return 서버가 사용 가능하면 0, 연결/응답/ACK 검증 실패 시 -1.
 */
int http_health_check(
    const char *server_url,
    int *http_status,
    char *error,
    size_t error_size)
{
    http_address_t address;
    char request[1024];
    char response[8192];
    char *body = NULL;
    size_t response_size = 0U;
    int request_length;
    int socket_fd;

    *http_status = 0;

    if (parse_server_url(
            server_url,
            &address,
            error,
            error_size) != 0) {
        return -1;
    }

    request_length = snprintf(
        request,
        sizeof(request),
        "GET " UPLOADER_HEALTH_PATH " HTTP/1.1\r\n"
        "Host: %s:%s\r\n"
        "User-Agent: uploader/1.0\r\n"
        "Accept: application/json\r\n"
        "Connection: close\r\n"
        "\r\n",
        address.host,
        address.port);

    if (request_length < 0 ||
        (size_t)request_length >= sizeof(request)) {
        set_error(error, error_size, "health request is too large");
        return -1;
    }

    socket_fd = connect_server(
        &address,
        UPLOADER_HEALTH_TIMEOUT_MS,
        error,
        error_size);

    if (socket_fd < 0) {
        return -1;
    }
    if (send_all(
            socket_fd,
            request,
            (size_t)request_length,
            error,
            error_size) != 0 ||
        receive_response(
            socket_fd,
            response,
            sizeof(response),
            &response_size,
            error,
            error_size) != 0) {
        close_connection(socket_fd);
        return -1;
    }

    close_connection(socket_fd);
    (void)response_size;

    if (parse_http_response(
            response,
            http_status,
            &body,
            error,
            error_size) != 0) {
        return -1;
    }

    if (*http_status != 200 ||
        !response_is_json(body) ||
        server_health_ack_validate(body, error, error_size) != 0) {
        snprintf(
            error,
            error_size,
            "health rejected: HTTP %d or invalid health ACK",
            *http_status);

        return -1;
    }

    return 0;
}

/* =========================================================================
 * #### ACK 유실 시 서버 Evidence 상태 조회
 * ========================================================================= */

/**
 * @brief action과 Node 인증정보, SHA로 서버 저장 상태를 조회함.
 * @param http_status 서버 HTTP 상태를 돌려받음.
 * @param response_body 정책 판단에 사용할 JSON body를 돌려받음.
 * @return HTTP 응답을 정상 수신·해석하면 0, transport/형식 실패 시 -1.
 * @note 404나 5xx의 재시도 정책은 상위 Worker가 결정함.
 */
int http_query_evidence_status(
    const char *server_url,
    const char *event_id,
    const char *node_id,
    const char *api_key,
    const char *content_sha256,
    int *http_status,
    char *response_body,
    size_t response_body_size,
    char *error,
    size_t error_size)
{
    http_address_t address;
    char path[256];
    char request[3072];
    char response[UPLOADER_HTTP_RESPONSE_MAX_SIZE];
    char *body = NULL;
    size_t response_size = 0U;
    int path_length;
    int request_length;
    int socket_fd;

    if (http_status == NULL || response_body == NULL ||
        response_body_size < 2U) {
        set_error(error, error_size, "invalid evidence status output");
        return -1;
    }

    *http_status = 0;
    response_body[0] = '\0';

    if (event_id == NULL || node_id == NULL || api_key == NULL ||
        content_sha256 == NULL || event_id[0] == '\0' ||
        node_id[0] == '\0' || api_key[0] == '\0' ||
        strlen(content_sha256) != SHA256_HEX_LENGTH) {
        set_error(error, error_size, "invalid evidence status authentication argument");
        return -1;
    }

    if (parse_server_url(
            server_url,
            &address,
            error,
            error_size) != 0) {
        return -1;
    }

    path_length = snprintf(
        path,
        sizeof(path),
        "/api/v1/events/%s/evidence/status",
        event_id);

    if (path_length < 0 || (size_t)path_length >= sizeof(path)) {
        set_error(error, error_size, "evidence status path is too large");
        return -1;
    }

    request_length = snprintf(
        request,
        sizeof(request),
        "GET %s HTTP/1.1\r\n"
        "Host: %s:%s\r\n"
        "User-Agent: uploader/1.0\r\n"
        "Accept: application/json\r\n"
        "X-ACE-Node-ID: %s\r\n"
        "X-ACE-API-Key: %s\r\n"
        "X-ACE-Content-SHA256: %s\r\n"
        "Connection: close\r\n"
        "\r\n",
        path,
        address.host,
        address.port,
        node_id,
        api_key,
        content_sha256);

    if (request_length < 0 || (size_t)request_length >= sizeof(request)) {
        set_error(error, error_size, "evidence status request is too large");
        return -1;
    }

    socket_fd = connect_server(
        &address,
        UPLOADER_HEALTH_TIMEOUT_MS,
        error,
        error_size);

    if (socket_fd < 0) {
        return -1;
    }

    if (send_all(
            socket_fd,
            request,
            (size_t)request_length,
            error,
            error_size) != 0 ||
        receive_response(
            socket_fd,
            response,
            sizeof(response),
            &response_size,
            error,
            error_size) != 0) {
        close_connection(socket_fd);
        return -1;
    }

    close_connection(socket_fd);
    (void)response_size;

    if (parse_http_response(
            response,
            http_status,
            &body,
            error,
            error_size) != 0) {
        return -1;
    }

    /* 404 등은 Worker의 복구 정책이 상태코드로 판정하므로 body가 없어도 정상
     * 반환한다. HTTP 200만 강한 JSON ACK 검증을 위해 body를 복사한다. */
    if (*http_status == 200) {
        if (!response_is_json(body) ||
            copy_ack_body(
                body,
                response_body,
                response_body_size,
                error,
                error_size) != 0) {
            set_error(error, error_size, "evidence status ACK is invalid");
            return -1;
        }
    }

    return 0;
}

/* =========================================================================
 * #### Metadata + MP4 multipart 업로드
 * ========================================================================= */

/**
 * @brief 인증/SHA header와 JSON metadata, MP4를 multipart/form-data로 전송함.
 * @param metadata 메모리에 로드된 제한 크기 JSON.
 * @param video_path streaming할 로컬 spool MP4 경로.
 * @param http_status 서버 응답 상태를 돌려받음.
 * @param ack_body DONE 판단에 사용할 JSON body를 돌려받음.
 * @return 요청 송신과 응답 수신·형식 확인 성공 시 0, 그 외 -1.
 * @note 서버 저장 여부와 ID/SHA 일치는 별도 server_upload_ack_validate()가 확인함.
 */
int http_upload_evidence(
    const char *server_url,
    const char *event_id,
    const char *metadata,
    size_t metadata_size,
    const char *video_path,
    const char *node_id,
    const char *api_key,
    const char *content_sha256,
    upload_phase_t *phase,
    int *http_status,
    char *ack_body,
    size_t ack_body_size,
    char *error,
    size_t error_size)
{
    http_address_t address;
    struct stat video_info;

    char boundary[96];
    char path[256];
    char header[3072];
    char metadata_prefix[512];
    char video_prefix[512];
    char ending[160];
    char response[UPLOADER_HTTP_RESPONSE_MAX_SIZE];

    char *body = NULL;
    size_t response_size = 0U;

    int metadata_prefix_length;
    int video_prefix_length;
    int ending_length;
    int header_length;
    int socket_fd;

    uint64_t content_length;

    *http_status = 0;
    if (phase != NULL) {
        *phase = UPLOADER_PHASE_NOT_CONNECTED;
    }

    if (ack_body == NULL || ack_body_size < 2U) {
        set_error(error, error_size, "invalid upload ACK argument");
        return -1;
    }

    ack_body[0] = '\0';

    if (event_id == NULL || node_id == NULL || api_key == NULL ||
        content_sha256 == NULL || event_id[0] == '\0' ||
        node_id[0] == '\0' || api_key[0] == '\0' ||
        strlen(content_sha256) != SHA256_HEX_LENGTH) {
        set_error(error, error_size, "invalid upload authentication argument");
        return -1;
    }

    if (parse_server_url(
            server_url,
            &address,
            error,
            error_size) != 0) {
        return -1;
    }

    if (stat(video_path, &video_info) != 0) {
        snprintf(
            error,
            error_size,
            "stat video: %s",
            strerror(errno));

        return -1;
    }

    if (!S_ISREG(video_info.st_mode) ||
        video_info.st_size <= 0) {
        set_error(error, error_size, "video is empty or invalid");
        return -1;
    }

    snprintf(
        boundary,
        sizeof(boundary),
        "ACEBoundary-%ld-%" PRIu64,
        (long)getpid(),
        (uint64_t)video_info.st_size);

    snprintf(
        path,
        sizeof(path),
        "/api/v1/events/%s/evidence",
        event_id);

    metadata_prefix_length = snprintf(
        metadata_prefix,
        sizeof(metadata_prefix),
        "--%s\r\n"
        "Content-Disposition: form-data; name=\"metadata\"\r\n"
        "Content-Type: application/json; charset=utf-8\r\n"
        "\r\n",
        boundary);

    video_prefix_length = snprintf(
        video_prefix,
        sizeof(video_prefix),
        "\r\n--%s\r\n"
        "Content-Disposition: form-data; "
        "name=\"file\"; filename=\"clip.mp4\"\r\n"
        "Content-Type: video/mp4\r\n"
        "\r\n",
        boundary);

    ending_length = snprintf(
        ending,
        sizeof(ending),
        "\r\n--%s--\r\n",
        boundary);

    if (metadata_prefix_length < 0 ||
        video_prefix_length < 0 ||
        ending_length < 0 ||
        (size_t)metadata_prefix_length >= sizeof(metadata_prefix) ||
        (size_t)video_prefix_length >= sizeof(video_prefix) ||
        (size_t)ending_length >= sizeof(ending)) {
        set_error(error, error_size, "multipart header is too large");
        return -1;
    }

    content_length =
        (uint64_t)metadata_prefix_length +
        (uint64_t)metadata_size +
        (uint64_t)video_prefix_length +
        (uint64_t)video_info.st_size +
        (uint64_t)ending_length;

    header_length = snprintf(
        header,
        sizeof(header),
        "POST %s HTTP/1.1\r\n"
        "Host: %s:%s\r\n"
        "User-Agent: uploader/1.0\r\n"
        "Accept: application/json\r\n"
        "X-ACE-Node-ID: %s\r\n"
        "X-ACE-API-Key: %s\r\n"
        "X-ACE-Content-SHA256: %s\r\n"
        "Content-Type: multipart/form-data; boundary=%s\r\n"
        "Content-Length: %" PRIu64 "\r\n"
        "Connection: close\r\n"
        "\r\n",
        path,
        address.host,
        address.port,
        node_id,
        api_key,
        content_sha256,
        boundary,
        content_length);

    if (header_length < 0 ||
        (size_t)header_length >= sizeof(header)) {
        set_error(error, error_size, "HTTP upload header is too large");
        return -1;
    }

    socket_fd = connect_server(
        &address,
        UPLOADER_TRANSFER_TIMEOUT_MS,
        error,
        error_size);

    if (socket_fd < 0) {
        return -1;
    }
    if (phase != NULL) {
        *phase = UPLOADER_PHASE_CONNECTED;
    }

    if (phase != NULL) {
        *phase = UPLOADER_PHASE_BODY_SENDING;
    }
    if (send_all(
            socket_fd,
            header,
            (size_t)header_length,
            error,
            error_size) != 0 ||
        send_all(
            socket_fd,
            metadata_prefix,
            (size_t)metadata_prefix_length,
            error,
            error_size) != 0 ||
        send_all(
            socket_fd,
            metadata,
            metadata_size,
            error,
            error_size) != 0 ||
        send_all(
            socket_fd,
            video_prefix,
            (size_t)video_prefix_length,
            error,
            error_size) != 0 ||
        send_video_file(
            socket_fd,
            video_path,
            (uint64_t)video_info.st_size,
            error,
            error_size) != 0 ||
        send_all(
            socket_fd,
            ending,
            (size_t)ending_length,
            error,
            error_size) != 0) {
        close_connection(socket_fd);
        return -1;
    }

    if (phase != NULL) {
        *phase = UPLOADER_PHASE_WAITING_ACK;
    }
    if (receive_response(
            socket_fd,
            response,
            sizeof(response),
            &response_size,
            error,
            error_size) != 0) {
        close_connection(socket_fd);
        return -1;
    }

    close_connection(socket_fd);
    (void)response_size;

    if (parse_http_response(
            response,
            http_status,
            &body,
            error,
            error_size) != 0) {
        return -1;
    }

    if (*http_status != 200 && *http_status != 201) {
        snprintf(
            error,
            error_size,
            "upload rejected: HTTP %d",
            *http_status);

        return -1;
    }

    if (!response_is_json(body)) {
        snprintf(
            error,
            error_size,
            "upload ACK is not JSON: HTTP %d",
            *http_status);

        return -1;
    }

    if (copy_ack_body(
            body,
            ack_body,
            ack_body_size,
            error,
            error_size) != 0) {
        return -1;
    }

    return 0;
}

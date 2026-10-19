/* 파일 역할: Unix 도메인 스트림 소켓으로 프로세스 간 헤더·페이로드 메시지를 주고받는다.
 * 구조: 서버 생성/접속 → 고정 크기 ipc_header_t → header.length 바이트의 본문.
 * 바이트 스트림의 부분 송수신을 내부 반복문에서 처리하며, 반환 상태로 연결 종료와 오류를 구분한다.
 */
#include "ipc.h"

#include <stdio.h>
#include <string.h>
#include <unistd.h>

#include <sys/socket.h>
#include <sys/un.h>
#include <sys/stat.h>

/*
 * 기능: 지정 길이의 바이트를 모두 송신한다.
 * 입력: 소켓 fd, 읽을 buffer, 바이트 수 length.
 * 출력: IPC_SUCCESS 또는 IPC_ERROR_SEND.
 * 구조·이유: 부분 송신마다 오프셋을 전진한다. 송신량 0도 실패로 처리해 무한 반복을 막는다.
 */
static int send_all(
    int fd,
    const void *buffer,
    size_t length
)
{
    const char *data = buffer;
    size_t total = 0;

    while (total < length)
    {
        ssize_t sent = send(
            fd,
            data + total,
            length - total,
            0
        );

        if (sent < 0)
        {
            perror("send");
            return IPC_ERROR_SEND;
        }

        if (sent == 0)
        {
            return IPC_ERROR_SEND;
        }

        total += (size_t)sent;
    }

    return IPC_SUCCESS;
}

/*
 * 기능: 지정 길이의 바이트를 모두 수신한다.
 * 입력: 소켓 fd, 쓸 buffer, 요구 길이 length.
 * 출력: 성공·수신 오류·상대 연결 종료를 나타내는 IPC 상태 코드.
 * 구조·이유: 부분 수신량을 누적하며 recv가 0이면 IPC_CONNECTION_CLOSED로 구분한다.
 */
static int recv_all(
    int fd,
    void *buffer,
    size_t length
)
{
    char *data = buffer;
    size_t total = 0;

    while (total < length)
    {
        ssize_t received = recv(
            fd,
            data + total,
            length - total,
            0
        );

        if (received < 0)
        {
            perror("recv");
            return IPC_ERROR_RECV;
        }

        if (received == 0)
        {
            return IPC_CONNECTION_CLOSED;
        }

        total += (size_t)received;
    }

    return IPC_SUCCESS;
}

/*
 * 기능: 로컬 IPC 연결을 받을 서버 소켓을 만든다.
 * 입력: Unix 소켓 파일 경로 path.
 * 출력: 리스닝 파일 디스크립터 또는 단계별 음수 오류 코드.
 * 구조·이유: 기존 경로 제거 → socket/bind → 권한 0666 → listen. 경로 권한은 실제 설정값이며 연결 승인 검사는 수행하지 않는다.
 */
int ipc_server_create(const char *path)
{
    int server_fd;
    struct sockaddr_un addr;

    server_fd = socket(AF_UNIX, SOCK_STREAM, 0);

    if (server_fd < 0)
    {
        perror("socket");
        return IPC_ERROR_SOCKET;
    }

    memset(&addr, 0, sizeof(addr));

    addr.sun_family = AF_UNIX;
    strncpy(addr.sun_path, path, sizeof(addr.sun_path) - 1);

    unlink(path);

    if (bind(server_fd,
             (struct sockaddr *)&addr,
             sizeof(addr)) < 0)
    {
        perror("bind");
        close(server_fd);
        return IPC_ERROR_BIND;
    }

    if (chmod(path, 0666) < 0)
    {
        perror("chmod");
        close(server_fd);
        unlink(path);
        return IPC_ERROR_BIND;
    }

    if (listen(server_fd, 5) < 0)
    {
        perror("listen");
        close(server_fd);
        return IPC_ERROR_LISTEN;
    }

    return server_fd;
}


/*
 * 기능: 대기 중인 IPC 클라이언트 연결 하나를 수락한다.
 * 입력: 리스닝 소켓 server_fd.
 * 출력: 연결된 소켓 fd 또는 IPC_ERROR_ACCEPT.
 * 구조·이유: accept로 얻은 fd를 반환하며 이후 메시지 송수신과 close는 호출자가 담당한다.
 */
int ipc_server_accept(int server_fd)
{
    int client_fd;

    client_fd = accept(server_fd, NULL, NULL);

    if (client_fd < 0)
    {
        perror("accept");
        return IPC_ERROR_ACCEPT;
    }

    return client_fd;
}

/*
 * 기능: 지정 Unix 소켓 서버에 연결한다.
 * 입력: 서버 소켓 파일 경로 path.
 * 출력: 연결된 fd 또는 소켓 생성·접속 오류 코드.
 * 구조·이유: AF_UNIX 스트림 소켓을 만든 뒤 connect하며 실패하면 생성한 fd를 닫는다.
 */
int ipc_client_connect(const char *path)
{
    int client_fd;
    struct sockaddr_un addr;

    client_fd = socket(AF_UNIX, SOCK_STREAM, 0);

    if (client_fd < 0)
    {
        perror("socket");
        return IPC_ERROR_SOCKET;
    }

    memset(&addr, 0, sizeof(addr));

    addr.sun_family = AF_UNIX;
    strncpy(addr.sun_path, path, sizeof(addr.sun_path) - 1);

    if (connect(client_fd,
                (struct sockaddr *)&addr,
                sizeof(addr)) < 0)
    {
        perror("connect");
        close(client_fd);
        return IPC_ERROR_CONNECT;
    }

    return client_fd;
}

/*
 * 기능: 헤더와 페이로드를 하나의 IPC 메시지 순서로 송신한다.
 * 입력: fd, 수정 가능한 header, payload 포인터와 payload_size.
 * 출력: IPC_SUCCESS 또는 IPC_ERROR_SEND; header.length도 갱신.
 * 구조·이유: 본문 길이를 헤더에 기록하고 헤더 → 본문 순으로 전송한다. 호출자는 유효한 포인터와 동시 송신 제어를 보장해야 한다.
 */
int ipc_send(
    int fd,
    ipc_header_t *header,
    const void *payload,
    size_t payload_size
)
{
    header->length = (uint32_t)payload_size;

    if (
        send_all(
            fd,
            header,
            sizeof(ipc_header_t)
        ) != IPC_SUCCESS
    )
    {
        return IPC_ERROR_SEND;
    }

    if (
        payload_size > 0 &&
        send_all(
            fd,
            payload,
            payload_size
        ) != IPC_SUCCESS
    )
    {
        return IPC_ERROR_SEND;
    }

    return IPC_SUCCESS;
}

/*
 * 기능: 헤더와 헤더에 명시된 길이의 본문을 수신한다.
 * 입력: fd, 출력 header·payload, 수신 버퍼 용량 payload_capacity.
 * 출력: 채워진 헤더·본문과 IPC 상태 코드.
 * 구조·이유: 헤더 수신 뒤 본문 길이를 용량과 비교한다. 용량 초과 시 본문을 소비하지 않고 오류를 반환한다.
 */
int ipc_recv(
    int fd,
    ipc_header_t *header,
    void *payload,
    size_t payload_capacity
)
{
    int result;

    result = recv_all(
        fd,
        header,
        sizeof(ipc_header_t)
    );

    if (result != IPC_SUCCESS)
    {
        return result;
    }

    if (header->length > payload_capacity)
    {
        return IPC_ERROR_INVALID_LENGTH;
    }

    if (header->length > 0)
    {
        result = recv_all(
            fd,
            payload,
            header->length
        );

        if (result != IPC_SUCCESS)
        {
            return result;
        }
    }

    return IPC_SUCCESS;
}

/*
 * 기능: IPC 소켓 파일 디스크립터를 닫는다.
 * 입력: 사용이 끝난 fd.
 * 출력: 반환값 없음.
 * 구조·이유: 통신 자원을 반환한다. 서버 소켓 경로 파일의 삭제는 수행하지 않는다.
 */
void ipc_close(int fd)
{
    close(fd);
}

#include "ipc.h"

#include <stdio.h>
#include <string.h>
#include <unistd.h>

#include <sys/socket.h>
#include <sys/un.h>
#include <sys/stat.h>

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

void ipc_close(int fd)
{
    close(fd);
}

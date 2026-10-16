#ifndef IPC_H
#define IPC_H

#include <stddef.h>
#include "protocol.h"

/*==================================================
 * IPC Result
 *==================================================*/
typedef enum
{
    IPC_SUCCESS = 0,

    IPC_ERROR_SOCKET          = -1,
    IPC_ERROR_BIND            = -2,
    IPC_ERROR_LISTEN          = -3,
    IPC_ERROR_ACCEPT          = -4,
    IPC_ERROR_CONNECT         = -5,
    IPC_ERROR_SEND            = -6,
    IPC_ERROR_RECV            = -7,
    IPC_ERROR_INVALID_LENGTH  = -8,
    IPC_CONNECTION_CLOSED     = -9

} ipc_result_t;

int ipc_server_create(const char *path);
int ipc_server_accept(int server_fd);

int ipc_client_connect(const char *path);

int ipc_send(
    int fd,
    ipc_header_t *header,
    const void *payload,
    size_t payload_size
);

int ipc_recv(
    int fd,
    ipc_header_t *header,
    void *payload,
    size_t payload_capacity
);

void ipc_close(int fd);

#endif

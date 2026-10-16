#define _POSIX_C_SOURCE 200809L
#include <inttypes.h>
#include <stdio.h>
#include <string.h>

#include "ipc.h"
#include "uploader_ipc_protocol.h"

int main(int argc, char **argv)
{
    uploader_evidence_ready_t ready = {0};
    uploader_response_t response = {0};
    ipc_header_t request = {0};
    ipc_header_t reply = {0};
    int fd;
    if (argc != 2) return 2;
    ready.version = UPLOADER_PROTOCOL_VERSION;
    ready.event_time_ns = INT64_C(1787000000000000000);
    ready.clip_start_time_ns = ready.event_time_ns - INT64_C(5000000000);
    ready.clip_end_time_ns = ready.event_time_ns + INT64_C(5000000000);
    snprintf(ready.action_id, sizeof(ready.action_id), "65537");
    snprintf(ready.accident_vehicle_id, sizeof(ready.accident_vehicle_id), "CAR_01");
    snprintf(ready.vehicle_id, sizeof(ready.vehicle_id), "CAR_99");
    snprintf(ready.video_path, sizeof(ready.video_path), "%s", argv[1]);
    snprintf(ready.time_sync_status, sizeof(ready.time_sync_status), "SYNCED");
    request.type = IPC_MSG_CAMERA_READY;
    request.sequence = 1;
    fd = ipc_client_connect(UPLOADER_SOCKET_PATH);
    if (fd < 0) return 3;
    if (ipc_send(fd, &request, &ready, sizeof(ready)) != IPC_SUCCESS ||
        ipc_recv(fd, &reply, &response, sizeof(response)) != IPC_SUCCESS) {
        ipc_close(fd);
        return 4;
    }
    ipc_close(fd);
    printf("CAMERA_READY response=%d message=%s\n", response.code, response.message);
    return response.code == UPLOADER_RESPONSE_OK ? 0 : 5;
}

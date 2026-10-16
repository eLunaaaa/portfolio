#include <stdio.h>
#include <stdint.h>
#include <string.h>

#include "uploader_http.h"

/* 독립 transport 시험에서는 process watchdog 대신 성공/실패만 확인한다. */
void uploader_watchdog_progress(uint32_t timeout_ms) { (void)timeout_ms; }

int main(int argc, char **argv)
{
    char error[256] = {0};
    char body[2048] = {0};
    int status = 0;
    upload_phase_t phase;
    const char *sha = "0000000000000000000000000000000000000000000000000000000000000000";
    const char *metadata = "{\"event_id\":\"EVT_TEST\"}";
    if (argc != 4) {
        fprintf(stderr, "usage: %s URL CA_FILE VIDEO_FILE\n", argv[0]);
        return 2;
    }
    if (http_client_prepare(argv[1], argv[2], error, sizeof(error)) != 0) {
        fprintf(stderr, "prepare: %s\n", error);
        return 3;
    }
    if (http_health_check(argv[1], &status, error, sizeof(error)) != 0) {
        fprintf(stderr, "health: %s\n", error);
        return 4;
    }
    if (http_query_evidence_status(argv[1], "EVT_TEST", "NODE_TEST", "dummy",
            sha, &status, body, sizeof(body), error, sizeof(error)) != 0 || status != 200) {
        fprintf(stderr, "status: %s HTTP %d\n", error, status);
        return 5;
    }
    if (http_upload_evidence(argv[1], "EVT_TEST", metadata, strlen(metadata),
            argv[3], "NODE_TEST", "dummy", sha, &phase, &status,
            body, sizeof(body), error, sizeof(error)) != 0 || status != 201) {
        fprintf(stderr, "upload: %s HTTP %d phase %d\n", error, status, (int)phase);
        return 6;
    }
    http_client_cleanup();
    puts("HTTP health/status/upload OK");
    return 0;
}

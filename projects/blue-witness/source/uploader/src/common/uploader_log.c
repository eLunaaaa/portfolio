#define _POSIX_C_SOURCE 200809L

#include "uploader_log.h"

#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

/* =========================================================================
 * #### 업로더 공통 로그 표시 방식
 * ========================================================================= */

typedef enum
{
    UPLOADER_LOG_VIEW_STRUCTURED,
    UPLOADER_LOG_VIEW_HUMAN,
    UPLOADER_LOG_VIEW_BOTH

} uploader_log_view_t;


typedef struct
{
    const char *event;
    const char *title;
    const char *situation;
    const char *next_action;

} uploader_event_description_t;


/*
 * 고정 event 이름을 사람이 바로 이해할 수 있는 한글 설명으로 연결한다.
 * event 값 자체는 기존 로그 검색과 시연 스크립트 호환성을 위해 변경하지 않는다.
 */
static const uploader_event_description_t event_descriptions[] =
{
    { "PROCESS_START", "업로더 실행 시작",
      "필요한 스레드와 저장 경로가 준비되어 CAMERA_READY를 기다리는 상태임.",
      "카메라·Filter 프로세스가 사고 영상 준비 메시지를 보내면 자동 처리함." },
    { "PROCESS_STOP", "업로더 종료",
      "종료 요청을 처리하고 스레드, 소켓, 내부 자원을 정리했음.",
      "예상하지 않은 종료라면 직전 ERROR 로그와 systemd 재시작 횟수를 확인함." },
    { "STARTUP_FAILED", "업로더 시작 실패",
      "설정, 디렉터리, 스레드 또는 내부 런타임 준비 단계에서 실패했음.",
      "세부정보의 stage와 error를 확인한 뒤 설정 또는 권한을 수정함." },
    { "QUEUE_THREAD_READY", "SQLite Queue 준비 완료",
      "Queue 스레드가 SQLite를 열고 재시작 전 UPLOAD 작업을 복구했음.",
      "추가 조치 없이 CAMERA_READY 또는 기존 PEND 작업을 처리함." },
    { "QUEUE_THREAD_STOPPED", "SQLite Queue 종료",
      "Queue 스레드가 SQLite 연결을 정상적으로 닫았음.",
      "프로세스 종료 과정이면 정상이며 단독 발생 시 앞선 오류를 확인함." },
    { "WORKER_THREAD_READY", "업로드 Worker 준비 완료",
      "Worker가 서버 주소와 차량 인증정보를 검증하고 작업을 기다리는 상태임.",
      "Queue에 PEND 작업이 들어오면 health 확인 후 자동 업로드함." },
    { "WORKER_THREAD_STOPPED", "업로드 Worker 종료",
      "Worker 스레드가 대기 또는 전송 작업을 끝내고 종료했음.",
      "정상 종료가 아니라면 직전 재시도·Queue 보고 오류를 확인함." },
    { "INGEST_THREAD_READY", "외부 IPC 수신 준비 완료",
      "Uploader 소켓이 열려 CAMERA_READY 메시지를 받을 수 있음.",
      "송신 프로세스의 소켓 주소와 IPC 구조체 버전이 일치해야 함." },
    { "CAMERA_READY_ACCEPTED", "사고 영상 요청 검증 완료",
      "사건·차량 정보와 MP4를 검증하고 결정적 작업 ID를 생성했음.",
      "이어서 metadata 생성, spool 안전 저장, Queue 등록을 수행함." },
    { "CAMERA_READY_REJECTED", "사고 영상 요청 거부",
      "CAMERA_READY의 메타데이터 또는 원본 영상 조건을 만족하지 못했음.",
      "action_id, vehicle_id, timestamp, video_path와 error를 확인함." },
    { "VIDEO_SHA256_COMPLETE", "영상 SHA-256 계산 완료",
      "전송 전 영상 무결성 확인에 사용할 SHA-256을 계산했음.",
      "이 값은 서버 저장 ACK의 해시와 비교됨." },
    { "VIDEO_INVALID", "MP4 영상 검증 실패",
      "영상이 없거나 비어 있거나 MP4 ftyp 헤더를 확인할 수 없음.",
      "카메라가 close·fsync·rename을 완료한 최종 MP4 경로를 보냈는지 확인함." },
    { "METADATA_INVALID", "메타데이터 검증 실패",
      "필수 JSON 필드, 문자열 크기 또는 timestamp 규약을 만족하지 못했음.",
      "Filter가 생성한 CAMERA_READY 값과 서버 규약을 비교함." },
    { "METADATA_IDENTITY_MISMATCH", "차량 인증정보 불일치",
      "메타데이터 vehicle_id와 이 Raspberry Pi에 설정된 vehicle_id가 다름.",
      "UPLOADER_VEHICLE_ID와 CAMERA_READY의 vehicle_id를 동일하게 설정함." },
    { "LOW_SPACE_REJECTED", "저장공간 부족으로 수신 거부",
      "안전한 로컬 사본과 예약공간을 보장할 여유 디스크가 부족함.",
      "불필요한 파일을 정리하되 기존 spool 작업은 임의 삭제하지 않음." },
    { "SPOOL_COMMIT_COMPLETE", "로컬 영상 안전 저장 완료",
      "MP4와 metadata를 fsync하고 최종 spool 디렉터리로 공개했음.",
      "이 시점부터 로컬 사본은 보존되며 Queue 등록을 진행함." },
    { "SPOOL_COMMIT_FAILED", "로컬 영상 안전 저장 실패",
      "원본을 spool에 완전히 저장하기 전에 파일 또는 디스크 오류가 발생했음.",
      "원본 파일 보존 여부와 error를 확인하고 저장장치 상태를 점검함." },
    { "SPOOL_TEMP_CLEANUP_COMPLETE", "임시 spool 정리 완료",
      "실패한 현재 복사 작업의 임시 파일을 안전하게 정리했음.",
      "최종 공개된 작업과 다른 프로세스 파일은 삭제하지 않음." },
    { "SPOOL_TEMP_CLEANUP_FAILED", "임시 spool 정리 실패",
      "오래되거나 실패한 임시 복사본 일부를 정리하지 못했음.",
      "경로, 소유권, 권한과 저장장치 오류를 확인함." },
    { "SPOOL_STALE_INCOMING_REMOVED", "오래된 임시 spool 검사 완료",
      "강제 종료로 남은 오래된 .incoming 작업을 검사·정리했음.",
      "removed와 failed가 0이면 추가 조치가 필요 없음." },
    { "SPOOL_RECOVERY_COMPLETE", "spool과 Queue 복구 검사 완료",
      "디스크의 완성 작업을 SQLite Queue와 다시 대조했음.",
      "failed가 있으면 해당 작업 경로와 후속 재등록 로그를 확인함." },
    { "QUEUE_ENQUEUE_ACK", "Queue 작업 등록 완료",
      "안전하게 저장된 영상을 PEND 상태로 SQLite FIFO에 등록했음.",
      "Worker가 깨어나 서버 상태 확인과 업로드를 시작함." },
    { "QUEUE_ENQUEUE_DEFERRED", "Queue 등록 지연",
      "spool 저장은 성공했지만 SQLite 등록이 일시적으로 실패했음.",
      "영상은 보존되며 실행 중 주기적 복구가 자동 재등록함." },
    { "QUEUE_ENQUEUE_RETRY", "Queue 재등록 시도",
      "spool에는 있으나 DB에 없는 작업을 다시 등록하고 있음.",
      "완료 또는 RETRY_FAILED 로그를 이어서 확인함." },
    { "QUEUE_ENQUEUE_RETRY_FAILED", "Queue 재등록 실패",
      "이번 spool 대조에서도 SQLite 등록을 완료하지 못했음.",
      "영상은 보존되며 다음 주기에 다시 시도하므로 DB 오류를 점검함." },
    { "QUEUE_ENQUEUE_RECOVERED", "Queue 재등록 복구 완료",
      "이전에 지연된 spool 작업을 PEND 상태로 복구했음.",
      "Worker가 자동으로 해당 작업을 업로드함." },
    { "QUEUE_JOB_CLAIMED", "업로드 작업 선택",
      "FIFO에서 전송 가능한 작업을 가져와 UPLOAD 상태로 전환했음.",
      "이어서 메타데이터·영상·서버 상태를 검증함." },
    { "QUEUE_JOB_DONE", "Queue 작업 완료 확정",
      "서버 저장 ACK 검증 후 작업 상태를 DONE으로 기록했음.",
      "로컬 보존·정리 정책에 따라 후속 처리를 수행할 수 있음." },
    { "QUEUE_JOB_FAIL", "Queue 작업 영구 실패",
      "동일 입력으로 재시도해도 해결되지 않는 영상·메타데이터 오류임.",
      "파일을 보존한 상태에서 last_error를 확인하고 입력을 수정함." },
    { "HEALTH_CHECK_OK", "FastAPI 서버 상태 정상",
      "서버 health 응답과 데이터베이스 상태 검증에 성공했음.",
      "Queue 작업이 있으면 바로 전송을 계속함." },
    { "HEALTH_CHECK_FAILED", "FastAPI 서버 연결 불가",
      "서버 연결, HTTP 응답 또는 health ACK 검증에 실패했음.",
      "작업을 claim하지 않고 파일을 보존한 채 다음 주기에 재확인함." },
    { "UPLOAD_START", "영상 업로드 시작",
      "metadata와 MP4를 multipart/form-data로 서버에 전송하기 시작함.",
      "전송 완료 후 서버 저장 ACK를 반드시 검증함." },
    { "UPLOAD_SERVER_ACK", "서버 저장 ACK 검증 성공",
      "서버가 사건·차량·SHA-256이 일치하는 저장 완료를 확인했음.",
      "Queue에 DONE 결과를 기록함." },
    { "UPLOAD_COMPLETE", "영상 업로드 전체 완료",
      "HTTP 전송, 서버 저장 ACK, Queue DONE 전이가 모두 완료됐음.",
      "이 사건 영상은 서버에 안전하게 저장된 상태임." },
    { "UPLOAD_RETRYABLE_FAILURE", "일시 오류로 업로드 재시도 예정",
      "네트워크 또는 서버 일시 장애로 이번 전송을 완료하지 못했음.",
      "영상은 보존되며 지수 backoff 후 횟수 제한 없이 자동 재시도함." },
    { "UPLOAD_CONNECTION_LOST", "전송 중 연결 끊김",
      "본문 전송 중 연결이 끊겨 서버 저장 여부가 불확실함.",
      "재전송 전에 서버 evidence 상태를 조회하여 중복 저장을 방지함." },
    { "UPLOAD_ACK_UNCERTAIN", "서버 ACK 유실",
      "서버가 저장했을 수 있으나 최종 ACK를 확인하지 못했음.",
      "다음 작업에서 서버 상태를 먼저 조회하여 DONE 여부를 확정함." },
    { "UPLOAD_RETRY_STARTED", "업로드 재시도 시작",
      "backoff 대기가 끝나 보존된 작업의 전송을 다시 시작함.",
      "성공, 추가 재시도 또는 영구 입력 오류 로그를 확인함." },
    { "RETRY_SCHEDULED", "다음 재시도 예약",
      "일시 장애 작업을 PEND 또는 VERIFY 상태로 되돌렸음.",
      "retry_after_ms 후 Worker가 자동으로 다시 처리함." },
    { "UPLOAD_PERMANENT_FAILURE", "업로드 영구 오류",
      "서버가 명확한 영구 거절을 반환했거나 입력이 손상됐음.",
      "HTTP 상태, metadata, 인증정보와 error를 수정한 뒤 재등록함." },
    { "EVIDENCE_STATUS_QUERY_START", "서버 저장 여부 확인 시작",
      "이전 연결 또는 ACK 유실 작업이 이미 저장됐는지 조회함.",
      "조회 결과에 따라 DONE 처리하거나 안전하게 재전송함." },
    { "EVIDENCE_STATUS_QUERY_RESULT", "서버 저장 여부 확인 완료",
      "서버의 evidence 상태와 로컬 사건·차량·해시를 비교했음.",
      "stored 결과가 참이면 DONE, 아니면 재전송함." },
    { "EVIDENCE_STATUS_NOT_FOUND", "서버에 기존 영상 없음",
      "조회 결과 해당 evidence가 아직 서버에 저장되지 않았음.",
      "보존된 로컬 파일을 사용해 정상 업로드를 진행함." },
    { "UPLOAD_RECOVERED_FROM_SERVER_ACK", "ACK 유실 작업 복구 완료",
      "서버 조회로 기존 저장 사실을 확인해 중복 전송 없이 복구했음.",
      "Queue 상태를 DONE으로 확정함." },
    { "QUEUE_RESULT_REPORT_RETRY", "Queue 결과 기록 재시도",
      "업로드 결과를 SQLite에 기록하는 내부 요청이 일시 실패했음.",
      "짧은 backoff 후 같은 결과를 다시 기록함." },
    { "QUEUE_RESULT_REPORT_RECOVERED", "Queue 결과 기록 복구",
      "재시도 후 SQLite 상태 전이를 정상 완료했음.",
      "추가 조치가 필요 없음." },
    { "QUEUE_RESULT_REPORT_FATAL", "Queue 결과 기록 최종 실패",
      "업로드 결과를 SQLite에 확정하지 못해 안전한 계속 실행이 불가능함.",
      "프로세스를 종료하여 systemd 재시작과 DB 복구를 유도함." },
    { "QUEUE_RESULT_FAILED", "Queue 결과 반영 실패",
      "Worker가 계산한 성공 또는 실패 결과를 Queue 상태에 반영하지 못했음.",
      "직전 RESULT_REPORT 로그와 SQLite 오류를 확인하며 안전 종료 여부를 확인함." },
    { "UPLOAD_LEASE_RECOVERED", "멈춘 UPLOAD 작업 복구",
      "오래된 UPLOAD lease를 PEND로 되돌리고 서버 확인 대상으로 표시했음.",
      "Worker가 저장 여부를 확인한 후 재전송 또는 DONE 처리함." },
    { "UPLOAD_PAUSE_REQUESTED", "업로드 일시정지 적용",
      "외부 제어 요청에 따라 지정 시간 동안 새 업로드 시작을 멈췄음.",
      "lease 만료 또는 RESUME 요청 시 자동 재개함." },
    { "UPLOAD_RESUME_REQUESTED", "업로드 재개",
      "일시정지 상태를 해제하고 Queue 작업 처리를 재개했음.",
      "대기 중인 PEND 작업이 있으면 Worker가 자동 처리함." }
};


static const uploader_event_description_t *find_event_description(
    const char *event
)
{
    size_t index;

    if (event == NULL)
    {
        return NULL;
    }

    for (
        index = 0;
        index < sizeof(event_descriptions) / sizeof(event_descriptions[0]);
        index++
    )
    {
        if (strcmp(event_descriptions[index].event, event) == 0)
        {
            return &event_descriptions[index];
        }
    }

    return NULL;
}


/*
 * UPLOADER_LOG_VIEW 값:
 *
 *     human     : 한글 다중행 로그만 출력
 *     structured: 기존 key=value 한 줄 로그만 출력
 *     both      : 두 형식을 모두 출력
 *
 * 값이 없으면 직접 실행한 터미널에서는 human, systemd/journald처럼 stderr가
 * 터미널이 아닌 환경에서는 structured를 자동 선택한다.
 */
static uploader_log_view_t configured_log_view(void)
{
    const char *configured = getenv("UPLOADER_LOG_VIEW");

    if (configured != NULL)
    {
        if (strcmp(configured, "human") == 0)
        {
            return UPLOADER_LOG_VIEW_HUMAN;
        }

        if (strcmp(configured, "structured") == 0)
        {
            return UPLOADER_LOG_VIEW_STRUCTURED;
        }

        if (strcmp(configured, "both") == 0)
        {
            return UPLOADER_LOG_VIEW_BOTH;
        }
    }

    return isatty(STDERR_FILENO) != 0
        ? UPLOADER_LOG_VIEW_HUMAN
        : UPLOADER_LOG_VIEW_STRUCTURED;
}


static const char *human_level(const char *level)
{
    if (level != NULL && strcmp(level, "ERROR") == 0)
    {
        return "오류";
    }

    if (level != NULL && strcmp(level, "WARN") == 0)
    {
        return "주의";
    }

    return "진행";
}


static void print_structured_log(
    const char *timestamp,
    long milliseconds,
    const char *level,
    const char *component,
    const char *event,
    const char *detail
)
{
    fprintf(
        stderr,
        "time=%s.%03ld level=%s component=%s event=%s msg=\"%s\"\n",
        timestamp,
        milliseconds,
        level,
        component,
        event,
        detail
    );
}


static void print_human_log(
    const char *timestamp,
    long milliseconds,
    const char *level,
    const char *component,
    const char *event,
    const char *detail
)
{
    const uploader_event_description_t *description =
        find_event_description(event);

    fprintf(
        stderr,
        "\n"
        "============================================================\n"
        "[업로더][%s] %s\n"
        "------------------------------------------------------------\n"
        "  발생 시각 : %s.%03ld\n"
        "  구성 요소 : %s\n"
        "  이벤트 ID : %s\n",
        human_level(level),
        description != NULL ? description->title : event,
        timestamp,
        milliseconds,
        component,
        event
    );

    if (description != NULL)
    {
        fprintf(
            stderr,
            "  현재 상황 : %s\n"
            "  다음 동작 : %s\n",
            description->situation,
            description->next_action
        );
    }

    fprintf(
        stderr,
        "  상세 정보 : %s\n"
        "============================================================\n",
        detail
    );
}


/* =========================================================================
 * #### 업로더 공통 로그 진입점
 * ========================================================================= */

/**
 * @brief 동일한 event를 실행 환경에 맞는 구조화 또는 한글 형식으로 기록함.
 * @param level INFO/WARN/ERROR와 같은 로그 심각도 문자열.
 * @param component 로그를 발생시킨 기능 이름.
 * @param event 검색과 시연에 사용할 고정 event 식별자.
 * @param format 선택적인 상세 필드 printf 형식. NULL이면 상세 필드를 생략함.
 * @return 반환값 없음.
 * @note 매 로그마다 fflush하여 프로세스 장애 직전 로그가 사용자 공간에 머무는 시간을 줄임.
 */
void uploader_log_event(
    const char *level,
    const char *component,
    const char *event,
    const char *format,
    ...)
{
    struct timespec now;
    struct tm local;
    char timestamp[64];
    char detail[2048];
    va_list arguments;
    uploader_log_view_t view;

    if (clock_gettime(CLOCK_REALTIME, &now) != 0) {
        now.tv_sec = 0;
        now.tv_nsec = 0;
    }

    localtime_r(&now.tv_sec, &local);
    strftime(
        timestamp,
        sizeof(timestamp),
        "%Y-%m-%dT%H:%M:%S%z",
        &local);

    va_start(arguments, format);
    vsnprintf(
        detail,
        sizeof(detail),
        format != NULL ? format : "",
        arguments
    );
    va_end(arguments);

    view = configured_log_view();

    /* 세 스레드가 동시에 로그를 남겨도 한 블록이 서로 섞이지 않게 보호함. */
    flockfile(stderr);

    if (
        view == UPLOADER_LOG_VIEW_STRUCTURED ||
        view == UPLOADER_LOG_VIEW_BOTH
    )
    {
        print_structured_log(
            timestamp,
            now.tv_nsec / 1000000L,
            level,
            component,
            event,
            detail
        );
    }

    if (
        view == UPLOADER_LOG_VIEW_HUMAN ||
        view == UPLOADER_LOG_VIEW_BOTH
    )
    {
        print_human_log(
            timestamp,
            now.tv_nsec / 1000000L,
            level,
            component,
            event,
            detail
        );
    }

    fflush(stderr);
    funlockfile(stderr);
}

#ifndef UPLOADER_LOG_H
#define UPLOADER_LOG_H

/**
 * @file uploader_log.h
 * @brief 실행 환경에 따라 운영용 구조화 로그와 터미널용 한글 로그를 출력함.
 * @scope 현재는 Queue, Worker, Ingest에서만 사용.
 *
 * 범용 형태의 API이지만 아직 프로젝트 전체의 로그 규약은 아니다.
 * AI/P2P/Camera 코드는 팀이 공통 로그 schema에 합의하고 프로젝트 공용
 * 라이브러리로 옮긴 뒤에 사용해야 한다.
 */

void uploader_log_event(
    const char *level,
    const char *component,
    const char *event,
    const char *format,
    ...);

#endif

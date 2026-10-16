#!/bin/sh

# hostname에 맞는 AI 실행 파일과 서비스 파일을 Pi의 clone 저장소에 배포한다.
set -eu

usage()
{
    echo "사용법: $0 [--build] <user@raspberry-pi-ip> [remote-project-root]" >&2
    exit 2
}

BUILD=0
if [ "${1:-}" = "--build" ]; then
    BUILD=1
    shift
fi
TARGET="${1:-}"
REMOTE_PROJECT="${2:-}"
[ -n "$TARGET" ] || usage
case "$TARGET" in
    *[!A-Za-z0-9_.@:-]*) echo "ERROR: target 형식이 올바르지 않습니다." >&2; exit 2 ;;
esac

if [ -z "$REMOTE_PROJECT" ]; then
    REMOTE_HOME=$(ssh "$TARGET" 'printf "%s" "$HOME"')
    REMOTE_PROJECT="$REMOTE_HOME/2026ESWContest_mobility_ACE"
fi
case "$REMOTE_PROJECT" in
    /*) ;;
    *) echo "ERROR: remote-project-root는 절대 경로여야 합니다." >&2; exit 2 ;;
esac
case "$REMOTE_PROJECT" in
    *[!A-Za-z0-9_./-]*) echo "ERROR: 프로젝트 경로 형식이 올바르지 않습니다." >&2; exit 2 ;;
esac

REMOTE_HOST=$(ssh "$TARGET" 'hostname -s')
case "$REMOTE_HOST" in
    craft2)
        BINARY_NAME=video_processor
        MAKE_TARGET=video_processor
        SERVICE_NAME=video_processor.service
        ;;
    craft3|craft4|craft5|seongugi|craft6)
        BINARY_NAME=inference
        MAKE_TARGET=inference
        SERVICE_NAME=inference.service
        ;;
    *)
        echo "ERROR: 이 hostname에는 AI 역할이 정의되지 않았습니다: $REMOTE_HOST" >&2
        exit 1
        ;;
esac

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PROJECT_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/../.." && pwd)
if [ "$BUILD" -eq 1 ]; then
    make -C "$PROJECT_ROOT/ai" "$MAKE_TARGET"
fi

BINARY="$PROJECT_ROOT/test/$BINARY_NAME"
[ -s "$BINARY" ] || {
    echo "ERROR: 제품 빌드 결과가 없습니다: $BINARY" >&2
    exit 1
}
if [ "$BINARY_NAME" = inference ]; then
    [ -s "$PROJECT_ROOT/ai/vehicle_detector.onnx" ] || {
        echo "ERROR: inference 모델이 없습니다: $PROJECT_ROOT/ai/vehicle_detector.onnx" >&2
        exit 1
    }
fi

REMOTE_MODULE="$REMOTE_PROJECT/ai/deploy"
EXEC_DIR="$REMOTE_PROJECT/test"
STAGE=$(ssh "$TARGET" "test -f '$REMOTE_PROJECT/ai/Makefile' && mktemp -d '$REMOTE_PROJECT/.ai-deploy.XXXXXX'")
case "$STAGE" in
    "$REMOTE_PROJECT/.ai-deploy."*) ;;
    *) echo "ERROR: 원격 배포 경로가 올바르지 않습니다." >&2; exit 1 ;;
esac

cleanup()
{
    ssh "$TARGET" "rm -f '$STAGE/$BINARY_NAME' '$STAGE/vehicle_detector.onnx' '$STAGE/$SERVICE_NAME' '$STAGE/install-service.sh' && rmdir '$STAGE'" >/dev/null 2>&1 || true
}
trap cleanup EXIT HUP INT TERM

scp "$BINARY" "$SCRIPT_DIR/systemd/$SERVICE_NAME" \
    "$SCRIPT_DIR/install-service.sh" "$TARGET:$STAGE/"
if [ "$BINARY_NAME" = inference ]; then
    scp "$PROJECT_ROOT/ai/vehicle_detector.onnx" "$TARGET:$STAGE/"
fi

ssh "$TARGET" \
    "chmod 0755 '$STAGE/$BINARY_NAME' '$STAGE/install-service.sh' && \
     chmod 0644 '$STAGE/$SERVICE_NAME' && \
     mkdir -p '$EXEC_DIR' '$REMOTE_MODULE/systemd' && \
     mv '$STAGE/$BINARY_NAME' '$EXEC_DIR/$BINARY_NAME' && \
     mv '$STAGE/$SERVICE_NAME' '$REMOTE_MODULE/systemd/$SERVICE_NAME' && \
     mv '$STAGE/install-service.sh' '$REMOTE_MODULE/install-service.sh'"

if [ "$BINARY_NAME" = inference ]; then
    ssh "$TARGET" \
        "mv '$STAGE/vehicle_detector.onnx' '$REMOTE_PROJECT/ai/vehicle_detector.onnx' && rmdir '$STAGE'"
else
    ssh "$TARGET" "rmdir '$STAGE'"
fi

trap - EXIT HUP INT TERM
echo "OK: $REMOTE_HOST용 $BINARY_NAME 배포 완료"
echo "Pi 서비스 등록·갱신: sudo sh '$REMOTE_MODULE/install-service.sh'"

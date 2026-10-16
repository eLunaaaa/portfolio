#!/bin/sh

# ARM64 실행 파일과 운영 설정을 Pi의 clone 받은 저장소 안에 배포한다.
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

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PROJECT_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/../.." && pwd)
if [ "$BUILD" -eq 1 ]; then
    make -C "$PROJECT_ROOT/uploader" clean
    make -C "$PROJECT_ROOT/uploader"
fi
[ -s "$PROJECT_ROOT/test/uploader" ] || {
    echo "ERROR: 제품 빌드 결과가 없습니다: $PROJECT_ROOT/test/uploader" >&2
    exit 1
}
readelf -h "$PROJECT_ROOT/test/uploader" | grep -q 'Machine:.*AArch64' || {
    echo "ERROR: Raspberry Pi 배포에는 AArch64 uploader 바이너리가 필요합니다." >&2
    exit 1
}
openssl x509 -in "$SCRIPT_DIR/certs/ace-ca.crt" -noout >/dev/null 2>&1 || {
    echo "ERROR: 공개 CA 인증서가 없거나 형식이 올바르지 않습니다." >&2
    exit 1
}

REMOTE_MODULE="$REMOTE_PROJECT/uploader/deploy"
EXEC_DIR="$REMOTE_PROJECT/test"
# clone된 모듈을 확인한 뒤 같은 저장소 안에 전송용 임시 폴더를 만든다.
STAGE=$(ssh "$TARGET" "test -f '$REMOTE_PROJECT/uploader/Makefile' && mktemp -d '$REMOTE_PROJECT/.uploader-deploy.XXXXXX'")
case "$STAGE" in
    "$REMOTE_PROJECT/.uploader-deploy."*) ;;
    *) echo "ERROR: 원격 배포 경로가 올바르지 않습니다." >&2; exit 1 ;;
esac
case "$STAGE" in
    *[!A-Za-z0-9_./-]*) echo "ERROR: 원격 임시 경로 형식이 올바르지 않습니다." >&2; exit 1 ;;
esac
cleanup()
{
    ssh "$TARGET" "rm -f '$STAGE/uploader' '$STAGE/uploader.env' '$STAGE/ace-ca.crt' '$STAGE/uploader.service' '$STAGE/install-service.sh' && rmdir '$STAGE'" >/dev/null 2>&1 || true
}
trap cleanup EXIT HUP INT TERM

scp "$PROJECT_ROOT/test/uploader" "$SCRIPT_DIR/uploader.env" \
    "$SCRIPT_DIR/certs/ace-ca.crt" \
    "$SCRIPT_DIR/systemd/uploader.service" "$SCRIPT_DIR/install-service.sh" \
    "$TARGET:$STAGE/"

ssh "$TARGET" \
    "chmod 0755 '$STAGE/uploader' '$STAGE/install-service.sh' && \
     chmod 0644 '$STAGE/uploader.service' '$STAGE/uploader.env' '$STAGE/ace-ca.crt' && \
     '$STAGE/uploader' check && \
     mkdir -p '$EXEC_DIR' '$REMOTE_MODULE/systemd' '$REMOTE_MODULE/certs' && \
     mv '$STAGE/uploader' '$EXEC_DIR/uploader' && \
     mv '$STAGE/uploader.env' '$REMOTE_MODULE/uploader.env' && \
     mv '$STAGE/ace-ca.crt' '$REMOTE_MODULE/certs/ace-ca.crt' && \
     mv '$STAGE/uploader.service' '$REMOTE_MODULE/systemd/uploader.service' && \
     mv '$STAGE/install-service.sh' '$REMOTE_MODULE/install-service.sh' && \
     rmdir '$STAGE'"
trap - EXIT HUP INT TERM
echo "OK: $TARGET:$EXEC_DIR/uploader 배포 완료"
echo "Pi 서비스 등록·갱신: sudo sh '$REMOTE_MODULE/install-service.sh'"

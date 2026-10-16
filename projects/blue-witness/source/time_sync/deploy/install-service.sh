#!/bin/sh

# Raspberry Pi에서 실제 로그인 계정과 배포 경로를 감지하여
# time_sync.service를 설치하고 즉시 시작함.
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
SERVICE_USER="${TIME_SYNC_SERVICE_USER:-${SUDO_USER:-}}"

if [ -z "$SERVICE_USER" ]; then
    SERVICE_USER=$(stat -c %U "$SCRIPT_DIR")
fi
case "$SERVICE_USER" in
    ''|*[!A-Za-z0-9_-]*)
        echo "ERROR: 서비스 계정 형식이 올바르지 않습니다: $SERVICE_USER" >&2
        exit 2
        ;;
esac
SERVICE_GROUP=$(id -gn "$SERVICE_USER")
SERVICE_HOME=$(getent passwd "$SERVICE_USER" | cut -d: -f6)
case "$SERVICE_HOME" in
    /*) ;;
    *) echo "ERROR: 서비스 계정 HOME을 확인하지 못했습니다: $SERVICE_USER" >&2; exit 1 ;;
esac

# clone 받은 저장소의 실행 파일과 설정을 그대로 사용한다.
PROJECT_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/../.." && pwd)
INSTALL_ROOT="$SCRIPT_DIR"
EXEC_ROOT="$PROJECT_ROOT/test"
UNIT_SOURCE="$SCRIPT_DIR/systemd/time_sync.service"
for SERVICE_PATH in "$INSTALL_ROOT" "$EXEC_ROOT" "$SERVICE_HOME"; do
    case "$SERVICE_PATH" in
        /*) ;;
        *) echo "ERROR: 서비스 경로는 절대 경로여야 합니다." >&2; exit 2 ;;
    esac
    case "$SERVICE_PATH" in
        *[!A-Za-z0-9_./-]*)
            echo "ERROR: 서비스 경로 형식이 올바르지 않습니다: $SERVICE_PATH" >&2
            exit 2
            ;;
    esac
done
UNIT_TARGET=/etc/systemd/system/time_sync.service
ENV_FILE="$INSTALL_ROOT/time_sync.env"
RENDERED_UNIT=$(mktemp /tmp/time_sync.service.XXXXXX)

cleanup()
{
    rm -f "$RENDERED_UNIT"
}
trap cleanup EXIT HUP INT TERM

if [ "$(id -u)" -ne 0 ]; then
    echo "ERROR: sudo ./install-service.sh 로 실행해야 합니다." >&2
    exit 1
fi

test -x "$EXEC_ROOT/time_sync" || {
    echo "ERROR: $EXEC_ROOT/time_sync가 없습니다." >&2
    exit 1
}
test -f "$UNIT_SOURCE" || {
    echo "ERROR: $UNIT_SOURCE가 없습니다." >&2
    exit 1
}
test -f "$ENV_FILE" || {
    echo "ERROR: $ENV_FILE이 없습니다." >&2
    exit 1
}

"$EXEC_ROOT/time_sync" check
sed \
    -e "s|@TIME_SYNC_USER@|$SERVICE_USER|g" \
    -e "s|@TIME_SYNC_GROUP@|$SERVICE_GROUP|g" \
    -e "s|@SERVICE_HOME@|$SERVICE_HOME|g" \
    -e "s|@INSTALL_ROOT@|$INSTALL_ROOT|g" \
    -e "s|@EXEC_ROOT@|$EXEC_ROOT|g" \
    "$UNIT_SOURCE" > "$RENDERED_UNIT"

if grep -q '@[A-Z_][A-Z_]*@' "$RENDERED_UNIT"; then
    echo "ERROR: systemd 템플릿에 치환되지 않은 값이 있습니다." >&2
    exit 1
fi

install -m 0644 "$RENDERED_UNIT" "$UNIT_TARGET"
systemctl daemon-reload
systemctl enable time_sync.service
systemctl restart time_sync.service

if ! systemctl is-active --quiet time_sync.service; then
    systemctl --no-pager --full status time_sync.service || true
    echo "ERROR: $EXEC_ROOT/time_sync 서비스가 시작되지 않았습니다." >&2
    exit 1
fi

# 새 서비스가 정상 시작된 뒤에만 과거 실행 파일을 정리함.
rm -f "$EXEC_ROOT/time-sync"

systemctl --no-pager --full status time_sync.service

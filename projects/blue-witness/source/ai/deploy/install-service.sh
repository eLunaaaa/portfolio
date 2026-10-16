#!/bin/sh

# hostname에 맞는 사고차/목격차 AI 서비스를 설치하고 즉시 시작한다.
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
SERVICE_USER="${AI_SERVICE_USER:-${SUDO_USER:-}}"

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

PROJECT_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/../.." && pwd)
AI_ROOT="$PROJECT_ROOT/ai"
EXEC_ROOT="$PROJECT_ROOT/test"
UPLOADER_ENV="$PROJECT_ROOT/uploader/deploy/uploader.env"
for SERVICE_PATH in "$AI_ROOT" "$EXEC_ROOT" "$UPLOADER_ENV" "$SERVICE_HOME"; do
    case "$SERVICE_PATH" in
        *[!A-Za-z0-9_./-]*)
            echo "ERROR: 서비스 경로 형식이 올바르지 않습니다: $SERVICE_PATH" >&2
            exit 2
            ;;
    esac
done

case "$(hostname -s)" in
    craft2)
        SERVICE_NAME=video_processor.service
        BINARY_NAME=video_processor
        OTHER_SERVICE=inference.service
        DEVICE_GROUPS=video
        ;;
    craft3|craft4|craft5|seongugi|craft6)
        SERVICE_NAME=inference.service
        BINARY_NAME=inference
        OTHER_SERVICE=video_processor.service
        DEVICE_GROUPS="video gpio"
        ;;
    *)
        echo "ERROR: 이 hostname에는 AI 역할이 정의되지 않았습니다: $(hostname -s)" >&2
        exit 1
        ;;
esac

UNIT_SOURCE="$SCRIPT_DIR/systemd/$SERVICE_NAME"
UNIT_TARGET="/etc/systemd/system/$SERVICE_NAME"
RENDERED_UNIT=$(mktemp "/tmp/$SERVICE_NAME.XXXXXX")

cleanup()
{
    rm -f "$RENDERED_UNIT"
}
trap cleanup EXIT HUP INT TERM

if [ "$(id -u)" -ne 0 ]; then
    echo "ERROR: sudo ./install-service.sh 로 실행해야 합니다." >&2
    exit 1
fi

test -x "$EXEC_ROOT/$BINARY_NAME" || {
    echo "ERROR: $EXEC_ROOT/$BINARY_NAME가 없습니다." >&2
    exit 1
}
test -f "$UPLOADER_ENV" || {
    echo "ERROR: $UPLOADER_ENV가 없습니다." >&2
    exit 1
}
test -f "$UNIT_SOURCE" || {
    echo "ERROR: $UNIT_SOURCE가 없습니다." >&2
    exit 1
}
if [ "$BINARY_NAME" = inference ]; then
    test -r "$AI_ROOT/vehicle_detector.onnx" || {
        echo "ERROR: $AI_ROOT/vehicle_detector.onnx를 읽을 수 없습니다." >&2
        exit 1
    }
fi

for DEVICE_GROUP in $DEVICE_GROUPS; do
    getent group "$DEVICE_GROUP" >/dev/null || {
        echo "ERROR: 카메라/GPIO 장치 그룹이 없습니다: $DEVICE_GROUP" >&2
        exit 1
    }
done

# C++ 프로세스는 환경 파일을 직접 읽지 않으므로 설치 시에도 동일 계약을 검증한다.
(
    . "$UPLOADER_ENV"
    test -n "${UPLOADER_VEHICLE_ID:-}"
) || {
    echo "ERROR: uploader.env에서 UPLOADER_VEHICLE_ID를 설정하지 못했습니다." >&2
    exit 1
}

sed \
    -e "s|@AI_USER@|$SERVICE_USER|g" \
    -e "s|@AI_GROUP@|$SERVICE_GROUP|g" \
    -e "s|@SERVICE_HOME@|$SERVICE_HOME|g" \
    -e "s|@AI_ROOT@|$AI_ROOT|g" \
    -e "s|@EXEC_ROOT@|$EXEC_ROOT|g" \
    -e "s|@UPLOADER_ENV@|$UPLOADER_ENV|g" \
    "$UNIT_SOURCE" > "$RENDERED_UNIT"

if grep -q '@[A-Z_][A-Z_]*@' "$RENDERED_UNIT"; then
    echo "ERROR: systemd 템플릿에 치환되지 않은 값이 있습니다." >&2
    exit 1
fi

install -d -m 0750 -o "$SERVICE_USER" -g "$SERVICE_GROUP" "$AI_ROOT/recordings"
install -m 0644 "$RENDERED_UNIT" "$UNIT_TARGET"
systemctl daemon-reload
# 역할 변경 후에도 두 프로세스가 같은 카메라를 동시에 열지 않게 한다.
if systemctl cat "$OTHER_SERVICE" >/dev/null 2>&1; then
    systemctl disable --now "$OTHER_SERVICE"
fi
systemctl enable "$SERVICE_NAME"
systemctl restart "$SERVICE_NAME"

if ! systemctl is-active --quiet "$SERVICE_NAME"; then
    systemctl --no-pager --full status "$SERVICE_NAME" || true
    echo "ERROR: $SERVICE_NAME 서비스가 시작되지 않았습니다." >&2
    exit 1
fi

systemctl --no-pager --full status "$SERVICE_NAME"

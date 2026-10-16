#!/bin/sh

# Raspberry Pi에서 현재 배포 경로와 sudo 호출 계정을 자동 감지하여
# uploader.service를 설치하고 즉시 시작함.
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
SERVICE_USER="${UPLOADER_SERVICE_USER:-${SUDO_USER:-}}"

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
UNIT_SOURCE="$SCRIPT_DIR/systemd/uploader.service"
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
UNIT_TARGET=/etc/systemd/system/uploader.service
RENDERED_UNIT=$(mktemp /tmp/uploader.service.XXXXXX)

cleanup()
{
    rm -f "$RENDERED_UNIT"
}
trap cleanup EXIT HUP INT TERM

if [ "$(id -u)" -ne 0 ]; then
    echo "ERROR: sudo ./install-service.sh 로 실행해야 합니다." >&2
    exit 1
fi

test -x "$EXEC_ROOT/uploader" || {
    echo "ERROR: $EXEC_ROOT/uploader가 없습니다." >&2
    exit 1
}
test -f "$INSTALL_ROOT/uploader.env" || {
    echo "ERROR: uploader.env가 없습니다." >&2
    exit 1
}
test -f "$UNIT_SOURCE" || {
    echo "ERROR: $UNIT_SOURCE가 없습니다." >&2
    exit 1
}
CA_SOURCE="$INSTALL_ROOT/certs/ace-ca.crt"
test -f "$CA_SOURCE" || {
    echo "ERROR: 공개 CA 인증서가 없습니다: $CA_SOURCE" >&2
    exit 1
}
openssl x509 -in "$CA_SOURCE" -noout >/dev/null 2>&1 || {
    echo "ERROR: 공개 CA 인증서 형식이 올바르지 않습니다." >&2
    exit 1
}

(
    . "$INSTALL_ROOT/uploader.env"
    "$EXEC_ROOT/uploader" check
)
# 공개 CA만 root 관리 경로에 설치한다. 기존 서비스는 새 파일이 준비된 뒤 재시작한다.
install -d -m 0755 /etc/ace/certs
install -m 0644 "$CA_SOURCE" /etc/ace/certs/ace-ca.crt.new
mv /etc/ace/certs/ace-ca.crt.new /etc/ace/certs/ace-ca.crt
sed \
    -e "s|@UPLOADER_USER@|$SERVICE_USER|g" \
    -e "s|@UPLOADER_GROUP@|$SERVICE_GROUP|g" \
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
systemctl enable uploader.service
systemctl restart uploader.service

if ! systemctl is-active --quiet uploader.service; then
    systemctl --no-pager --full status uploader.service || true
    echo "ERROR: 새 $EXEC_ROOT/uploader 서비스가 시작되지 않았습니다." >&2
    exit 1
fi

systemctl --no-pager --full status uploader.service

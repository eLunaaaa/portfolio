#!/usr/bin/env bash
set -Eeuo pipefail

release_id=${1:?release ID required}
expected_host=${2:?expected hostname required}
[[ "$release_id" =~ ^[0-9]+$ ]] || exit 2
[[ "$(hostname -s)" == "$expected_host" ]] || { echo "Hostname mismatch: $(hostname -s)" >&2; exit 1; }
project_root="$HOME/2026ESWContest_mobility_ACE"
release_root="$project_root/.fleet-releases"
archive="$HOME/pi-release-$release_id.tar.gz"
[[ -f "$project_root/sensor/Makefile" && -s "$archive" ]] || { echo 'Pi project clone or artifact missing' >&2; exit 1; }
mkdir -p "$release_root"
stage=$(mktemp -d "$release_root/.stage.XXXXXX")
backup="$release_root/rollback-$release_id"
changed=false

case "$expected_host" in
  craft2)
    binaries=(time_sync uploader accident_aruco video_processor broadcast_sender p2p_go.sh)
    services=(time_sync.service uploader.service video_processor.service p2p-go.service sensor@accident.service)
    service_binaries=(time_sync uploader video_processor broadcast_sender accident_aruco)
    sockets=(/tmp/time-sync.sock /tmp/uploader.sock /tmp/video_processor_event.sock /tmp/p2p.sock)
    ;;
  craft3|craft4|seongugi|craft6)
    binaries=(time_sync uploader witness_aruco inference witness_filter broadcast_receiver p2p_client.sh)
    services=(time_sync.service uploader.service inference.service witness_filter.service p2p-client.service sensor@witness.service)
    service_binaries=(time_sync uploader inference witness_filter broadcast_receiver witness_aruco)
    sockets=(/tmp/time-sync.sock /tmp/uploader.sock /tmp/inference_event.sock /tmp/witness_sensor.sock /tmp/filter.sock)
    ;;
  *) echo "Unknown fleet host: $expected_host" >&2; exit 2 ;;
esac

restart_services() {
  local service
  for service in "${services[@]}"; do
    echo "Restart $service"
    if ! timeout 60 sudo -n systemctl restart "$service"; then
      echo "Restart failed: $service" >&2
      return 1
    fi
  done
}

can_open_device() {
  local device=$1
  # 배포 로그인 계정은 target 설치 시 video/gpio/i2c 그룹에 속한다.
  timeout 5 bash -c 'exec 3< "$1"' _ "$device"
}

smoke() {
  local service pid socket binary attempt index device camera_open process_path
  for attempt in $(seq 1 30); do
    local healthy=true
    for index in "${!services[@]}"; do
      service=${services[$index]}
      if ! systemctl is-active --quiet "$service"; then healthy=false; break; fi
      pid=$(systemctl show -p MainPID --value "$service")
      if [[ ! "$pid" =~ ^[1-9][0-9]*$ ]] || ! ps -p "$pid" -o pid= >/dev/null; then healthy=false; break; fi
      # root service의 /proc/PID/exe는 일반 SSH 계정에서 읽을 수 없다.
      # 실제 MainPID의 cmdline 첫 인자를 검사한다(P2P notify 후에는 자식 binary가 MainPID).
      if ! IFS= read -r -d "" process_path < "/proc/$pid/cmdline"; then healthy=false; break; fi
      if [[ "$process_path" != "$project_root/test/${service_binaries[$index]}" ]]; then healthy=false; break; fi
    done
    if [[ "$healthy" == true ]]; then
      for socket in "${sockets[@]}"; do
        if [[ ! -S "$socket" ]]; then healthy=false; break; fi
      done
    fi
    [[ "$healthy" == true ]] && break
    sleep 1
  done
  [[ "$healthy" == true ]] || { echo 'Smoke failure: service/process/IPC socket'; return 1; }
  for binary in "${binaries[@]}"; do
    [[ -x "$project_root/test/$binary" ]] || { echo "Not executable: $binary"; return 1; }
  done
  ip link show wlan0 >/dev/null
  # service가 active여도 P2P 그룹 interface는 이미 사라졌을 수 있다.
  # 실제 역할별 주소와 peer 통신을 확인해 연결 상실을 smoke 실패로 처리한다.
  case "$expected_host" in
    craft2) p2p_address=192.168.49.1; p2p_peers=(192.168.49.2 192.168.49.3 192.168.49.4 192.168.49.5) ;;
    craft3) p2p_address=192.168.49.2; p2p_peers=(192.168.49.1) ;;
    craft4) p2p_address=192.168.49.3; p2p_peers=(192.168.49.1) ;;
    seongugi) p2p_address=192.168.49.4; p2p_peers=(192.168.49.1) ;;
    craft6) p2p_address=192.168.49.5; p2p_peers=(192.168.49.1) ;;
  esac
  p2p_interface=$(ip -4 -o addr show | awk -v address="$p2p_address/24" '$4 == address {print $2; exit}')
  [[ "$p2p_interface" == p2p-* ]] || { echo 'P2P address/interface unavailable'; return 1; }
  for peer in "${p2p_peers[@]}"; do
    timeout 3 ping -I "$p2p_interface" -c 1 -W 1 "$peer" >/dev/null || {
      echo "P2P peer unreachable: $peer" >&2
      return 1
    }
  done
  can_open_device /dev/gpiochip0 || { echo 'GPIO device unavailable'; return 1; }
  if [[ "$expected_host" == craft2 ]]; then
    can_open_device /dev/i2c-1 || { echo 'BNO I2C device unavailable'; return 1; }
  fi
  camera_open=false
  for device in /dev/media*; do
    if [[ -e "$device" ]] && can_open_device "$device"; then camera_open=true; break; fi
  done
  [[ "$camera_open" == true ]] || { echo 'Camera media device unavailable'; return 1; }
  # 운영 중인 uploader 설정에서 실제 서버 URL을 읽고 공개 CA로 /health를 확인한다.
  source "$project_root/uploader/deploy/uploader.env"
  [[ -n "${UPLOADER_SERVER_URL:-}" ]] || { echo 'UPLOADER_SERVER_URL missing'; return 1; }
  curl --fail --silent --show-error --max-time 5 \
    --cacert /etc/ace/certs/ace-ca.crt \
    -o /dev/null "${UPLOADER_SERVER_URL%/}/api/v1/health"
  echo 'Smoke PASS: binaries, systemd, processes, IPC, interfaces, server health'
}

rollback() {
  local failed_status=$?
  trap - EXIT
  if [[ "$changed" == true ]]; then
    echo "ROLLBACK $expected_host: restoring prior binaries"
    for binary in "${binaries[@]}"; do
      if [[ -f "$backup/$binary" ]]; then
        install -m 0755 "$backup/$binary" "$project_root/test/$binary.restore-$release_id"
        mv -f "$project_root/test/$binary.restore-$release_id" "$project_root/test/$binary"
      fi
    done
    if restart_services && smoke; then
      echo "ROLLBACK PASS $expected_host"
    else
      echo "ROLLBACK FAILED $expected_host: manual service check required" >&2
    fi
  fi
  rm -f "$archive"
  if [[ -d "$stage" ]]; then rm -f "$stage"/*; rmdir "$stage" 2>/dev/null || :; fi
  exit "$failed_status"
}
trap rollback EXIT

# 이전 설치와 unit 상태를 확인한 후에만 파일을 바꾼다.
for service in "${services[@]}"; do
  [[ $(systemctl show -p LoadState --value "$service") == loaded ]] || { echo "Unit missing: $service" >&2; exit 1; }
  # sudo -l의 성공만으로는 비밀번호 없이 실행 가능한지 알 수 없다.
  # 실제 파일을 교체하기 전에 NOPASSWD 정책(!authenticate)을 확인한다.
  sudo -n -ll /usr/bin/systemctl restart "$service" 2>/dev/null |
    grep -Eq "^[[:space:]]*Options:.*!authenticate" || { echo "Passwordless restart unavailable: $service" >&2; exit 1; }
done
for binary in "${binaries[@]}"; do
  [[ -x "$project_root/test/$binary" ]] || { echo "Current binary missing: $binary" >&2; exit 1; }
done
tar -xzf "$archive" -C "$stage"
for binary in "${binaries[@]}"; do
  [[ -x "$stage/$binary" ]] || { echo "Artifact missing: $binary" >&2; exit 1; }
done
mkdir "$backup"
for binary in "${binaries[@]}"; do
  cp -a "$project_root/test/$binary" "$backup/$binary"
done
changed=true
for binary in "${binaries[@]}"; do
  install -m 0755 "$stage/$binary" "$project_root/test/$binary.new-$release_id"
  mv -f "$project_root/test/$binary.new-$release_id" "$project_root/test/$binary"
done
restart_services
smoke
# 성공한 배포도 이전 버전을 한 개 이상 계속 보존한다.
ln -s "$(basename "$backup")" "$release_root/previous.new-$release_id"
mv -Tf "$release_root/previous.new-$release_id" "$release_root/previous"
changed=false
rm -f "$archive"
trap - EXIT
rm -f "$stage"/*
rmdir "$stage"
echo "DEPLOY PASS $expected_host release=$release_id previous=$backup"

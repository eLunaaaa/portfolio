#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
# 최초 설치한 root 소유 helper를 사용한다. 배포가 helper 자체를 교체하지 않는다.
[[ "$0" == /usr/local/libexec/ace-fleet-apply ]] || { echo 'Use the installed fleet helper' >&2; exit 1; }
[[ $(stat -c '%u:%a' "$0") == 0:755 ]] || { echo 'Fleet helper must be root:0755' >&2; exit 1; }

[[ $# == 2 ]] || { echo 'Expected release ID and hostname' >&2; exit 2; }
release_id=${1:?release ID required}
expected_host=${2:?expected hostname required}
[[ "$release_id" =~ ^[0-9]+-[0-9]+$ ]] || exit 2
[[ $EUID == 0 && "${SUDO_USER:-}" == "$expected_host" ]] || { echo 'Run through the fleet SSH account and sudo' >&2; exit 1; }
[[ "$(hostname -s)" == "$expected_host" ]] || { echo 'Hostname mismatch' >&2; exit 1; }
login_home=$(getent passwd "$expected_host" | cut -d: -f6)
login_group=$(id -gn "$expected_host")
[[ "$login_home" == /home/"$expected_host" ]] || { echo 'Unexpected fleet account home' >&2; exit 1; }
project_root="$login_home/2026ESWContest_mobility_ACE"
release_root="$project_root/.fleet-releases"
archive="$login_home/pi-release-$release_id.tar.gz"
[[ -f "$project_root/sensor/Makefile" && -s "$archive" ]] || { echo 'Pi project clone or artifact missing' >&2; exit 1; }
install -d -m 0755 -o "$expected_host" -g "$login_group" "$release_root"
stage=$(mktemp -d "$release_root/.stage.XXXXXX")
backup="$release_root/rollback-$release_id"
changed=false

common_files=(time_sync/deploy/install-service.sh time_sync/deploy/systemd/time_sync.service
  time_sync/deploy/time_sync.env uploader/deploy/install-service.sh
  uploader/deploy/systemd/uploader.service uploader/deploy/uploader.env
  uploader/deploy/certs/ace-ca.crt ai/deploy/install-service.sh
  p2p/deploy/install-service.sh sensor/sensor@.service)
case "$expected_host" in
  craft2)
    binaries=(time_sync uploader accident_aruco video_processor broadcast_sender p2p_go.sh)
    services=(time_sync.service uploader.service video_processor.service p2p-go.service sensor@accident.service)
    service_binaries=(time_sync uploader video_processor broadcast_sender accident_aruco)
    sockets=(/tmp/time-sync.sock /tmp/uploader.sock /tmp/video_processor_event.sock /tmp/p2p.sock)
    role_files=(ai/deploy/systemd/video_processor.service p2p/deploy/systemd/p2p-go.service)
    opposite_services=(inference.service p2p-client.service sensor@witness.service)
    p2p_address=192.168.49.1
    p2p_peers=(192.168.49.2 192.168.49.3 192.168.49.4 192.168.49.5)
    ;;
  craft3|craft4|seongugi|craft6)
    binaries=(time_sync uploader witness_aruco inference witness_filter broadcast_receiver p2p_client.sh)
    services=(time_sync.service uploader.service inference.service witness_filter.service p2p-client.service sensor@witness.service)
    service_binaries=(time_sync uploader inference witness_filter broadcast_receiver witness_aruco)
    sockets=(/tmp/time-sync.sock /tmp/uploader.sock /tmp/inference_event.sock /tmp/witness_sensor.sock /tmp/filter.sock)
    role_files=(ai/deploy/systemd/inference.service filter/deploy/install-service.sh
      filter/deploy/systemd/witness_filter.service p2p/deploy/systemd/p2p-client.service)
    opposite_services=(video_processor.service p2p-go.service sensor@accident.service)
    case "$expected_host" in craft3) p2p_address=192.168.49.2;; craft4) p2p_address=192.168.49.3;;
      seongugi) p2p_address=192.168.49.4;; craft6) p2p_address=192.168.49.5;; esac
    p2p_peers=(192.168.49.1)
    ;;
  *) echo "Unknown fleet host: $expected_host" >&2; exit 2 ;;
esac
companions=("${common_files[@]}" "${role_files[@]}")
declare -A reinstall=() prior_enabled=() prior_active=() service_users=()
managed_files=()
system_files=()

restart_one() {
  local service=$1 budget=60
  [[ "$service" == p2p-*.service ]] && budget=300
  timeout "$budget" systemctl restart "$service" || { echo "Restart failed: $service" >&2; return 1; }
}

smoke() {
  local attempt index pid process_path socket healthy=false stable=0 p2p_interface peer
  for attempt in $(seq 1 30); do
    healthy=true
    for index in "${!services[@]}"; do
      systemctl is-active --quiet "${services[$index]}" || { healthy=false; break; }
      pid=$(systemctl show -p MainPID --value "${services[$index]}") || return 1
      [[ "$pid" =~ ^[1-9][0-9]*$ ]] && ps -p "$pid" -o pid= >/dev/null || { healthy=false; break; }
      IFS= read -r -d "" process_path < "/proc/$pid/cmdline" || { healthy=false; break; }
      [[ "$process_path" == "$project_root/test/${service_binaries[$index]}" ]] || { healthy=false; break; }
    done
    if [[ "$healthy" == true ]]; then
      for socket in "${sockets[@]}"; do [[ -S "$socket" ]] || { healthy=false; break; }; done
    fi
    if [[ "$healthy" == true ]]; then stable=$((stable + 1)); else stable=0; fi
    [[ $stable -ge 3 ]] && break
    sleep 1
  done
  [[ $stable -ge 3 ]] || { echo 'Smoke failure: service/process/IPC socket' >&2; return 1; }
  ip link show wlan0 >/dev/null || return 1
  p2p_interface=$(ip -4 -o addr show | awk -v address="$p2p_address/24" '$4 == address {print $2; exit}') || return 1
  [[ "$p2p_interface" == p2p-* ]] || { echo 'P2P address/interface unavailable' >&2; return 1; }
  for peer in "${p2p_peers[@]}"; do
    timeout 3 ping -I "$p2p_interface" -c 1 -W 1 "$peer" >/dev/null || {
      echo "P2P peer unreachable: $peer" >&2; return 1;
    }
  done
  # 장치 접근은 실제 서비스 계정과 watchdog이 처리한다. 서버 연결은 별도로 표시한다.
  if (
    source "$project_root/uploader/deploy/uploader.env" || exit 1
    [[ -n "${UPLOADER_SERVER_URL:-}" ]] || exit 1
    curl_args=(--fail --silent --show-error --max-time 5)
    if [[ "$UPLOADER_SERVER_URL" == https://* ]]; then
      curl_args+=(--cacert "${UPLOADER_CA_FILE:-/etc/ace/certs/ace-ca.crt}")
    fi
    curl "${curl_args[@]}" -o /dev/null "${UPLOADER_SERVER_URL%/}/api/v1/health"
  ); then echo 'Server health: reachable'; else echo 'Server health: unavailable; local operation continues'; fi
  echo 'Smoke PASS: services, processes, IPC and P2P'
}

save_file() {
  local source=$1 saved=$2
  mkdir -p "$(dirname "$saved")" || return 1
  if [[ -e "$source" || -L "$source" ]]; then cp -a -- "$source" "$saved" || return 1;
  else printf '%s\n' "$source" >> "$backup/absent" || return 1; fi
}

restore_file() {
  local target=$1 saved=$2
  if [[ -e "$saved" || -L "$saved" ]]; then
    cp -a -- "$saved" "$target.restore-$release_id" && mv -Tf -- "$target.restore-$release_id" "$target" || return 1
  else rm -f -- "$target" || return 1; fi
}

rollback() {
  local failed_status=$? failed=0 file service pending_job all_active=true
  trap - EXIT
  set +e
  if [[ "$changed" == true ]]; then
    echo "ROLLBACK $expected_host: restoring binaries, companion files and service settings"
    for service in "${services[@]}" "${opposite_services[@]}"; do
      [[ -n "${prior_active[$service]:-}" ]] || continue
      if ! pending_job=$(systemctl show -p Job --value "$service"); then
        echo "ROLLBACK FAILED $expected_host: cannot inspect $service job; backup retained at $backup" >&2
        exit 1
      fi
      [[ -n "$pending_job" ]] || continue
      if ! timeout 30 systemctl stop "$service"; then
        echo "ROLLBACK FAILED $expected_host: cannot stop pending $service job; backup retained at $backup" >&2
        exit 1
      fi
    done
    for file in "${managed_files[@]}"; do
      restore_file "$project_root/$file" "$backup/repo/$file" || failed=1
    done
    for file in "${system_files[@]}"; do restore_file "$file" "$backup/system$file" || failed=1; done
    systemctl daemon-reload || failed=1
    for service in "${!prior_enabled[@]}"; do
      case "${prior_enabled[$service]}" in
        enabled) systemctl enable "$service" >/dev/null || failed=1 ;;
        enabled-runtime) systemctl enable --runtime "$service" >/dev/null || failed=1 ;;
        disabled) systemctl disable "$service" >/dev/null || failed=1 ;;
      esac
    done
    # 원래 중지된 서비스도 그대로 되돌린 뒤, 실행 중이던 서비스만 다시 시작한다.
    for service in "${services[@]}" "${opposite_services[@]}"; do
      [[ "${prior_active[$service]:-}" == inactive || "${prior_active[$service]:-}" == failed ]] || continue
      timeout 30 systemctl stop "$service" || failed=1
    done
    for service in "${services[@]}" "${opposite_services[@]}"; do
      [[ "${prior_active[$service]:-}" == active ]] || continue
      restart_one "$service" && systemctl is-active --quiet "$service" || failed=1
    done
    for service in "${services[@]}"; do
      [[ "${prior_active[$service]:-}" == active ]] || all_active=false
    done
    if [[ "$all_active" == true ]]; then smoke || failed=1; fi
    if [[ $failed == 0 ]]; then echo "ROLLBACK PASS $expected_host";
    else echo "ROLLBACK FAILED $expected_host: backup retained at $backup" >&2; fi
  fi
  rm -f -- "$archive"
  rm -rf -- "$stage"
  [[ $failed_status != 0 ]] || failed_status=1
  exit "$failed_status"
}
trap rollback EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

for service in "${services[@]}"; do
  [[ $(systemctl show -p LoadState --value "$service") == loaded ]] || { echo "Unit missing: $service" >&2; exit 1; }
  service_users[$service]=$(systemctl show -p User --value "$service")
done
for binary in "${binaries[@]}"; do
  [[ -x "$project_root/test/$binary" ]] || { echo "Current binary missing: $binary" >&2; exit 1; }
  managed_files+=("test/$binary")
done
# 전달받은 archive를 root 소유 임시 파일로 고정하고 명시된 경로만 추출한다.
cp "$archive" "$stage/release.tar.gz"
all_files=(witness_aruco accident_aruco witness_filter broadcast_sender broadcast_receiver
  inference video_processor time_sync uploader p2p_go.sh p2p_client.sh RELEASE_SHA)
all_companions=("${common_files[@]}" ai/deploy/systemd/inference.service
  ai/deploy/systemd/video_processor.service filter/deploy/install-service.sh
  filter/deploy/systemd/witness_filter.service p2p/deploy/systemd/p2p-go.service
  p2p/deploy/systemd/p2p-client.service ai/vehicle_detector.onnx)
for file in "${all_companions[@]}"; do all_files+=("companions/$file"); done
tar -tzf "$stage/release.tar.gz" > "$stage/members"
tar -tvzf "$stage/release.tar.gz" | awk 'substr($0,1,1) != "-" && substr($0,1,1) != "d" {exit 1}'
while IFS= read -r member; do
  member=${member#./}
  case "$member" in /*|../*|*/../*|*/..) echo 'Invalid release path' >&2; exit 1;; esac
  [[ -z "$member" || "$member" == companions/ || "$member" == companions/*/ ]] && continue
  [[ " ${all_files[*]} " == *" $member "* ]] || { echo "Unexpected release member: $member" >&2; exit 1; }
done < "$stage/members"
tar -xzf "$stage/release.tar.gz" -C "$stage" --no-same-owner --no-same-permissions
[[ $(cat "$stage/RELEASE_SHA") =~ ^[0-9a-f]{40}$ ]] || { echo 'Release commit missing' >&2; exit 1; }
for binary in "${binaries[@]}"; do
  [[ -x "$stage/$binary" && ! -L "$stage/$binary" ]] || { echo "Artifact missing: $binary" >&2; exit 1; }
done
if [[ "$expected_host" != craft2 && -f "$stage/companions/ai/vehicle_detector.onnx" ]]; then
  companions+=(ai/vehicle_detector.onnx)
fi
for file in "${companions[@]}"; do
  [[ -f "$stage/companions/$file" && ! -L "$stage/companions/$file" ]] || { echo "Companion missing: $file" >&2; exit 1; }
  managed_files+=("$file")
  if ! cmp -s "$stage/companions/$file" "$project_root/$file"; then
    case "$file" in
      time_sync/deploy/install-service.sh|time_sync/deploy/systemd/*) reinstall[time_sync.service]=1 ;;
      uploader/deploy/install-service.sh|uploader/deploy/systemd/*|uploader/deploy/certs/*) reinstall[uploader.service]=1 ;;
      ai/deploy/install-service.sh|ai/deploy/systemd/*) reinstall[${services[2]}]=1 ;;
      filter/deploy/*) reinstall[witness_filter.service]=1 ;;
      p2p/deploy/*) for service in "${services[@]}"; do if [[ "$service" == p2p-* ]]; then reinstall[$service]=1; fi; done ;;
      sensor/sensor@.service) reinstall[sensor]=1 ;;
    esac
  fi
done
mkdir "$backup"
for file in "${managed_files[@]}"; do save_file "$project_root/$file" "$backup/repo/$file"; done
for service in "${services[@]}" "${opposite_services[@]}"; do
  [[ $(systemctl show -p LoadState --value "$service") != not-found ]] || continue
  prior_enabled[$service]=$(systemctl is-enabled "$service" 2>/dev/null || true)
  prior_active[$service]=$(systemctl is-active "$service" 2>/dev/null || true)
  case "${prior_active[$service]}" in
    active|inactive|failed) ;;
    *) echo "Service state changing: $service (${prior_active[$service]})" >&2; exit 1 ;;
  esac
  file="/etc/systemd/system/$service"
  [[ "$service" == sensor@* ]] && file=/etc/systemd/system/sensor@.service
  [[ " ${system_files[*]} " == *" $file "* ]] || system_files+=("$file")
done
system_files+=(/etc/ace/certs/ace-ca.crt)
for file in "${system_files[@]}"; do save_file "$file" "$backup/system$file"; done
cp "$stage/RELEASE_SHA" "$backup/release-commit"
changed=true
for file in "${managed_files[@]}"; do
  source_path="$stage/companions/$file"
  mode=0644
  if [[ "$file" == test/* ]]; then source_path="$stage/${file#test/}"; mode=0755;
  elif [[ "$file" == *.sh ]]; then mode=0755; fi
  install -d -m 0755 -o "$expected_host" -g "$login_group" "$(dirname "$project_root/$file")"
  install -m "$mode" -o "$expected_host" -g "$login_group" "$source_path" "$project_root/$file.new-$release_id"
  mv -Tf "$project_root/$file.new-$release_id" "$project_root/$file"
done
if [[ ${reinstall[sensor]:-0} == 1 ]]; then
  sed "s|@SENSOR_DIR@|$project_root/test|g" "$project_root/sensor/sensor@.service" > "$stage/sensor@.service"
  if grep -q '@[A-Z_][A-Z_]*@' "$stage/sensor@.service"; then echo 'Unresolved sensor unit template' >&2; exit 1; fi
  install -m 0644 "$stage/sensor@.service" /etc/systemd/system/sensor@.service
  systemctl daemon-reload
fi
for service in "${services[@]}"; do
  if [[ ${reinstall[$service]:-0} == 1 ]]; then
    budget=60
    case "$service" in
      time_sync.service) installer=time_sync; export TIME_SYNC_SERVICE_USER="${service_users[$service]:-root}" ;;
      uploader.service) installer=uploader; export UPLOADER_SERVICE_USER="${service_users[$service]:-root}" ;;
      inference.service|video_processor.service) installer=ai; export AI_SERVICE_USER="${service_users[$service]:-root}" ;;
      witness_filter.service) installer=filter ;;
      p2p-*.service) installer=p2p; budget=300 ;;
    esac
    timeout "$budget" /bin/sh "$project_root/$installer/deploy/install-service.sh"
  else restart_one "$service"; fi
done
smoke
ln -s "$(basename "$backup")" "$release_root/previous.new-$release_id"
mv -Tf "$release_root/previous.new-$release_id" "$release_root/previous"
changed=false
trap - EXIT
rm -f -- "$archive"
rm -rf -- "$stage"
echo "DEPLOY PASS $expected_host release=$release_id commit=$(cat "$backup/release-commit") previous=$backup"

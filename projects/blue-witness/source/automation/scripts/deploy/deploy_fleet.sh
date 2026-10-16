#!/usr/bin/env bash
set -euo pipefail

archive=${1:?ARM64 release archive required}
rollout=${2:-false}
release_id=${3:?GitHub run ID and attempt required}
[[ "$release_id" =~ ^[0-9]+-[0-9]+$ ]] || { echo 'Invalid release ID' >&2; exit 2; }
[[ "$rollout" == true || "$rollout" == false ]] || { echo 'rollout must be true or false' >&2; exit 2; }
[[ -s "$archive" ]] || { echo "Missing artifact: $archive" >&2; exit 1; }

# SSH config의 별칭이 승인된 fleet 주소와 사용자로 향하는지 먼저 확인한다.
check_target() {
  local host=$1 expected_ip=$2 actual_ip actual_user
  actual_ip=$(ssh -T -G "$host" | awk '$1 == "hostname" {print $2; exit}')
  actual_user=$(ssh -T -G "$host" | awk '$1 == "user" {print $2; exit}')
  [[ "$actual_ip" == "$expected_ip" && "$actual_user" == "$host" ]] || {
    echo "SSH config mismatch: $host -> $actual_user@$actual_ip (expected $host@$expected_ip)" >&2
    exit 1
  }
}

for entry in craft3:192.168.50.3 craft2:192.168.50.2 craft4:192.168.50.4 seongugi:192.168.50.5 craft6:192.168.50.6; do
  check_target "${entry%%:*}" "${entry#*:}"
done

wait_p2p_fleet_ready() {
  local deadline=$((SECONDS + 300)) stable=0 host service output remaining ready
  local -A states=()
  echo 'Waiting for fleet P2P services to settle (up to 300 seconds)'
  while (( SECONDS < deadline )); do
    ready=true
    for host in craft2 craft3 craft4 seongugi craft6; do
      remaining=$((deadline - SECONDS))
      (( remaining > 0 )) || { ready=false; break; }
      (( remaining <= 10 )) || remaining=10
      service=p2p-client.service
      [[ "$host" != craft2 ]] || service=p2p-go.service
      if output=$(timeout --signal=KILL "$remaining" ssh -o BatchMode=yes \
          -o StrictHostKeyChecking=yes -o ConnectTimeout=10 "$host" \
          systemctl show -p ActiveState -p Job "$service" 2>&1); then
        states[$host]=${output//$'\n'/, }
        if ! grep -qx 'ActiveState=active' <<< "$output" || ! grep -qx 'Job=' <<< "$output"; then
          ready=false
        fi
      else
        states[$host]="SSH query failed: ${output//$'\n'/, }"
        ready=false
      fi
    done
    if [[ "$ready" == true ]]; then stable=$((stable + 1)); else stable=0; fi
    if (( stable >= 2 )); then echo 'Fleet P2P ready'; return 0; fi
    remaining=$((deadline - SECONDS))
    (( remaining > 0 )) || break
    (( remaining <= 2 )) || remaining=2
    sleep "$remaining"
  done
  echo 'Fleet P2P did not settle within 300 seconds' >&2
  for host in craft2 craft3 craft4 seongugi craft6; do
    printf '%s: %s\n' "$host" "${states[$host]:-not checked}" >&2
  done
  return 1
}

apply_node() {
  local host=$1 expected_helper actual_helper
  echo "::group::Deploy $host"
  expected_helper=$(sha256sum scripts/deploy/apply_release.sh | awk '{print $1}')
  actual_helper=$(ssh -o BatchMode=yes -o StrictHostKeyChecking=yes -o ConnectTimeout=10 \
    "$host" sha256sum /usr/local/libexec/ace-fleet-apply | awk '{print $1}')
  [[ "$actual_helper" == "$expected_helper" ]] || { echo "Install the current root-owned fleet helper on $host first" >&2; exit 1; }
  ssh -o BatchMode=yes -o StrictHostKeyChecking=yes -o ConnectTimeout=10 \
    "$host" LC_ALL=C sudo -n -ll /usr/local/libexec/ace-fleet-apply "$release_id" "$host" 2>/dev/null |
    grep -Eq '^[[:space:]]*Options:.*!authenticate' || { echo "Fleet helper permission missing: $host" >&2; exit 1; }
  # 이미 등록된 host key만 수락하고, 비대화식 SSH를 사용한다.
  scp -o BatchMode=yes -o StrictHostKeyChecking=yes -o ConnectTimeout=10 \
    "$archive" "$host:pi-release-$release_id.tar.gz"
  if ssh -o BatchMode=yes -o StrictHostKeyChecking=yes -o ConnectTimeout=10 \
      -o ServerAliveInterval=15 -o ServerAliveCountMax=3 \
      "$host" sudo -n /usr/local/libexec/ace-fleet-apply "$release_id" "$host"; then
    echo "PASS $host: deployed and smoke tested"
    echo '::endgroup::'
  else
    echo "FAIL $host: rollout stopped; see rollback result above" >&2
    echo '::endgroup::'
    exit 1
  fi
}

apply_node craft3
wait_p2p_fleet_ready
if [[ "$rollout" == true ]]; then
  for host in craft2 craft4 seongugi craft6; do
    apply_node "$host"
    wait_p2p_fleet_ready
  done
else
  echo 'Canary complete. Full fleet rollout was not requested.'
fi

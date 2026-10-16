#!/usr/bin/env bash
set -euo pipefail

archive=${1:?ARM64 release archive required}
rollout=${2:-false}
release_id=${3:?GitHub run ID required}
[[ "$release_id" =~ ^[0-9]+$ ]] || { echo 'Invalid release ID' >&2; exit 2; }
[[ "$rollout" == true || "$rollout" == false ]] || { echo 'rollout must be true or false' >&2; exit 2; }
[[ -s "$archive" ]] || { echo "Missing artifact: $archive" >&2; exit 1; }

# SSH config의 별칭이 승인된 fleet 주소와 사용자로 향하는지 먼저 확인한다.
check_target() {
  local host=$1 expected_ip=$2 actual_ip actual_user
  actual_ip=$(ssh -G "$host" | awk '$1 == "hostname" {print $2; exit}')
  actual_user=$(ssh -G "$host" | awk '$1 == "user" {print $2; exit}')
  [[ "$actual_ip" == "$expected_ip" && "$actual_user" == "$host" ]] || {
    echo "SSH config mismatch: $host -> $actual_user@$actual_ip (expected $host@$expected_ip)" >&2
    exit 1
  }
}

for entry in craft3:192.168.50.3 craft2:192.168.50.2 craft4:192.168.50.4 seongugi:192.168.50.5 craft6:192.168.50.6; do
  check_target "${entry%%:*}" "${entry#*:}"
done

apply_node() {
  local host=$1
  echo "::group::Deploy $host"
  # 이미 등록된 host key만 수락하고, 비대화식 SSH를 사용한다.
  scp -o BatchMode=yes -o StrictHostKeyChecking=yes -o ConnectTimeout=10 \
    "$archive" "$host:pi-release-$release_id.tar.gz"
  if ssh -o BatchMode=yes -o StrictHostKeyChecking=yes -o ConnectTimeout=10 \
      "$host" bash -s -- "$release_id" "$host" < scripts/deploy/apply_release.sh; then
    echo "PASS $host: deployed and smoke tested"
    echo '::endgroup::'
  else
    echo "FAIL $host: rollout stopped; see rollback result above" >&2
    echo '::endgroup::'
    exit 1
  fi
}

apply_node craft3
if [[ "$rollout" == true ]]; then
  for host in craft2 craft4 seongugi craft6; do
    apply_node "$host"
  done
else
  echo 'Canary complete. Full fleet rollout was not requested.'
fi

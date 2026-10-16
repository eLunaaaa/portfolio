#!/usr/bin/env bash
set -euo pipefail

certificate_dir=$(mktemp -d)
server_pid=
cleanup() {
  if [[ -n "$server_pid" ]]; then
    kill "$server_pid" 2>/dev/null || :
    wait "$server_pid" 2>/dev/null || :
  fi
  rm -f "$certificate_dir/server.key" "$certificate_dir/server.crt" "$certificate_dir/server.log" "$certificate_dir/health.json"
  rmdir "$certificate_dir"
}
trap cleanup EXIT

openssl req -x509 -newkey rsa:2048 -sha256 -nodes -days 1 \
  -keyout "$certificate_dir/server.key" -out "$certificate_dir/server.crt" \
  -subj /CN=localhost -addext 'subjectAltName=DNS:localhost' \
  > /dev/null 2>&1
export PYTHONPATH="$PWD/server/backend"
export EVIDENCE_MERGE_WORKER_ENABLED=false
export EVIDENCE_COOKIE_SECURE=true
python3 -m uvicorn app.main:app --host 127.0.0.1 --port 18443 \
  --ssl-keyfile "$certificate_dir/server.key" \
  --ssl-certfile "$certificate_dir/server.crt" \
  > "$certificate_dir/server.log" 2>&1 &
server_pid=$!

for attempt in $(seq 1 30); do
  if curl --fail --silent --show-error --max-time 2 \
      --cacert "$certificate_dir/server.crt" \
      https://localhost:18443/api/v1/health \
      -o "$certificate_dir/health.json" 2>/dev/null; then
    python3 -c 'import json,sys; data=json.load(open(sys.argv[1])); assert data["status"] == "ok" and data["database"] == "ok"' \
      "$certificate_dir/health.json"
    echo 'Temporary-certificate TLS startup and /health PASS'
    exit 0
  fi
  sleep 1
done
cat "$certificate_dir/server.log" >&2
exit 1

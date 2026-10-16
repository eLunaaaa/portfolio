#!/usr/bin/env bash
set -euo pipefail
python3 -m compileall -q server/backend/app server/backend/tests
export PYTHONPATH="$PWD/server/backend"
export EVIDENCE_MERGE_WORKER_ENABLED=false
python3 -c 'from app.main import create_app; assert create_app()'
python3 -m uvicorn app.main:app --host 127.0.0.1 --port 18000 >"$RUNNER_TEMP/server-smoke.log" 2>&1 &
server_pid=$!
trap 'kill "$server_pid" 2>/dev/null || :; wait "$server_pid" 2>/dev/null || :' EXIT
python3 - <<'PY'
import json
import time
import urllib.error
import urllib.request

url = 'http://127.0.0.1:18000/api/v1/health'
for attempt in range(30):
    try:
        with urllib.request.urlopen(url, timeout=2) as response:
            body = json.load(response)
            assert response.status == 200, body
            assert body['status'] == 'ok' and body['database'] == 'ok', body
            print(body)
            break
    except (OSError, AssertionError) as error:
        if attempt == 29:
            raise RuntimeError('Server startup/DB/health failed') from error
        time.sleep(1)
PY

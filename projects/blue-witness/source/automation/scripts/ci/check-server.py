"""Server CI: contracts and HTTP/TLS health; integration adds an FFmpeg fixture."""
import argparse
import compileall
from datetime import datetime, timedelta, timezone
import ipaddress
import json
import os
from pathlib import Path
import shutil
import socket
import ssl
import subprocess
import sys
from tempfile import TemporaryDirectory
import time
from unittest.mock import patch
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "server" / "backend"
sys.path.insert(0, str(BACKEND))


# 기능: CI의 HTTPS 검사에 사용할 임시 인증서를 만든다.
# 입력: 임시 작업 디렉터리 root.
# 출력: 개인 키와 인증서의 Path 두 개.
# 구조·이유: localhost·127.0.0.1용 RSA 인증서를 하루 유효 기간으로 생성한다. 운영 인증서와 분리된 테스트 전용 자료다.
def certificate(root):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=1))
            .not_valid_after(now + timedelta(days=1))
            .add_extension(x509.SubjectAlternativeName([
                x509.DNSName("localhost"),
                x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
            ]), critical=False)
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
            .sign(key, hashes.SHA256()))
    key_path, cert_path = root / "server.key", root / "server.crt"
    key_path.write_bytes(key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption()))
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return key_path, cert_path


# 기능: 격리된 DB를 사용하는 검사 서버를 구동한다.
# 입력: DB 경로·포트·선택 TLS 키/인증서를 포함한 args.
# 출력: 서버 종료까지 실행; 반환값 없음.
# 구조·이유: DB 경로를 지정한 뒤 실제 앱을 import하고 루프백 주소에서 uvicorn을 실행한다.
def serve(args):
    # The subprocess uses its own temporary DB while running the actual app lifespan.
    from app.db import database
    database.DATABASE_PATH = Path(args.database)
    database.DATABASE_DIRECTORY = database.DATABASE_PATH.parent
    import uvicorn
    from app.main import app
    uvicorn.run(app, host="127.0.0.1", port=args.port,
                ssl_keyfile=args.key, ssl_certfile=args.cert)


# 기능: 실제 프로세스로 HTTP 또는 HTTPS 상태 확인을 수행한다.
# 입력: TLS 사용 여부 tls.
# 출력: 검사 통과 시 None; 서버 조기 종료·시간 초과·잘못된 상태는 예외.
# 구조·이유: 임시 DB·사용 가능한 포트로 서버를 띄워 health 응답을 확인하고 종료 시 프로세스와 임시 파일을 정리한다.
def health(tls):
    with TemporaryDirectory(prefix="ace-server-ci-") as directory:
        root = Path(directory)
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        command = [sys.executable, str(Path(__file__).resolve()), "serve",
                   "--database", str(root / "server.db"), "--port", str(port)]
        context = None
        if tls:
            key, cert = certificate(root)
            command += ["--key", str(key), "--cert", str(cert)]
            context = ssl.create_default_context(cafile=str(cert))
        env = os.environ.copy()
        env["EVIDENCE_MERGE_WORKER_ENABLED"] = "false"
        env["EVIDENCE_COOKIE_SECURE"] = "true" if tls else "false"
        scheme = "https" if tls else "http"
        with (root / "server.log").open("w+", encoding="utf-8") as log:
            process = subprocess.Popen(command, cwd=ROOT, env=env,
                                       stdout=log, stderr=subprocess.STDOUT)
            try:
                deadline = time.monotonic() + 30
                while True:
                    if process.poll() is not None:
                        raise RuntimeError(f"Server exited: {process.returncode}")
                    try:
                        with urllib.request.urlopen(
                            f"{scheme}://127.0.0.1:{port}/api/v1/health",
                            timeout=2, context=context,
                        ) as response:
                            body = json.load(response)
                            assert response.status == 200, body
                            assert body["status"] == "ok" and body["database"] == "ok", body
                        print(f"{scheme.upper()} startup, DB and health PASS", flush=True)
                        break
                    except OSError:
                        if time.monotonic() >= deadline:
                            raise
                        time.sleep(0.2)
            except BaseException:
                log.flush()
                log.seek(0)
                print(log.read(), file=sys.stderr)
                raise
            finally:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)


# 기능: CI에서 필요한 영상 도구의 실행 가능 여부를 확인한다.
# 입력: PATH 환경의 ffmpeg와 ffprobe.
# 출력: 각 도구 버전 출력; 누락·실행 실패·10초 초과 시 예외.
# 구조·이유: 단순 경로 존재 검사에 그치지 않고 -version 명령의 종료 상태까지 검사한다.
def verify_media_tools():
    tools = {name: shutil.which(name) for name in ("ffmpeg", "ffprobe")}
    missing = [name for name, path in tools.items() if path is None]
    if missing:
        raise RuntimeError(f"Required media tools missing from PATH: {', '.join(missing)}")
    for name, path in tools.items():
        try:
            result = subprocess.run(
                [path, "-version"], check=True, capture_output=True,
                text=True, timeout=10,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"{name} -version timed out after 10 seconds") from exc
        except subprocess.CalledProcessError as exc:
            raise RuntimeError(f"{name} -version failed with exit code {exc.returncode}") from exc
        except OSError as exc:
            raise RuntimeError(f"Cannot execute {name}: {exc}") from exc
        version = result.stdout.splitlines()
        print(version[0] if version else f"{name} executable verified", flush=True)


# 기능: 서버와 프런트엔드 회귀 검사를 실행한다.
# 입력: 저장소의 app·tests와 Python·Node 실행 환경.
# 출력: 검사 출력; 문법 오류·테스트 실패·시간 초과 시 예외.
# 구조·이유: Python 컴파일 → unittest → Node 테스트 순으로 수행하며 병합 워커는 비활성화한다.
def contracts():
    for path in (BACKEND / "app", BACKEND / "tests"):
        if not compileall.compile_dir(path, quiet=1):
            raise RuntimeError(f"Python syntax check failed: {path}")
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(filter(None, (str(BACKEND), env.get("PYTHONPATH"))))
    env["EVIDENCE_MERGE_WORKER_ENABLED"] = "false"
    subprocess.run([sys.executable, "-m", "unittest", "discover", "-v",
                    "-s", str(BACKEND / "tests")],
                   cwd=ROOT, env=env, check=True, timeout=120)
    frontend_tests = sorted(str(path) for path in (BACKEND / "tests").glob("test_*.cjs"))
    subprocess.run(["node", "--test", *frontend_tests],
                   cwd=ROOT, check=True, timeout=60)


# 기능: 생성한 짧은 영상으로 실제 병합 경로를 검사한다.
# 입력: ffmpeg·ffprobe 및 병합 서비스.
# 출력: 검사 결과 출력; 명령 실패·결과 검증 실패 시 예외.
# 구조·이유: 임시 샘플 영상과 증거 레코드를 만들고 실제 병합 및 결과 미디어 검사를 수행한다.
def merge_fixture():
    from app.db.merge_repository import EvidenceRecord
    from app.services import video_merger

    with TemporaryDirectory(prefix="ace-merge-ci-") as directory:
        root = Path(directory)
        source = root / "source.mp4"
        subprocess.run([
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "color=c=blue:s=160x90:r=10",
            "-t", "1", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(source),
        ], check=True, timeout=30)
        start_ns = 1_800_000_000_000_000_000
        record = EvidenceRecord(
            id=1, event_id="CI_MERGE", accident_vehicle_id="CAR_01", vehicle_id="CAR_01",
            event_time_ns=start_ns + 500_000_000, clip_start_time_ns=start_ns,
            clip_end_time_ns=start_ns + 1_000_000_000,
            time_sync_status="SYNCED", chrony_offset_ms=0.0, video_path=str(source),
        )
        with patch.object(video_merger, "list_latest_evidence", return_value=[record]), \
                patch.object(video_merger, "MERGED_ROOT", root / "merged"):
            plan = video_merger.merge_event(
                "CI_MERGE", before_seconds=0.25, after_seconds=0.25,
                mode="common", tile_width=160, tile_height=90, fps=10,
            )
        output = Path(plan["output"]["path"])
        assert output.is_file() and output.stat().st_size > 0
        probe = subprocess.run([
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=codec_name", "-of", "json", str(output),
        ], check=True, capture_output=True, text=True, timeout=30)
        assert json.loads(probe.stdout)["streams"], probe.stdout
        print(f"FFmpeg merge PASS: {output.stat().st_size} bytes", flush=True)


# 기능: CI 서버 검사의 실행 모드를 선택한다.
# 입력: 명령행 quick·integration·serve와 서버용 선택 인자.
# 출력: 검사 실행 결과 또는 서버 구동; 실패는 예외·CLI 오류.
# 구조·이유: serve는 격리 서버를 실행하고 나머지는 공통 검사 후 integration에서 영상 병합 검사를 추가한다.
def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("quick", "integration", "serve"))
    parser.add_argument("--database")
    parser.add_argument("--port", type=int)
    parser.add_argument("--key")
    parser.add_argument("--cert")
    args = parser.parse_args()
    if args.mode == "serve":
        if not args.database or not args.port:
            parser.error("serve requires --database and --port")
        serve(args)
        return
    verify_media_tools()
    contracts()
    health(tls=False)
    health(tls=True)
    if args.mode == "integration":
        merge_fixture()


if __name__ == "__main__":
    main()


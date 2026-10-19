from pathlib import Path
import re
import shutil
import subprocess
import sys
import time

import requests


SERVER_URL = "http://127.0.0.1:8000/upload"


def find_chronyd() -> str:
    """설치된 chronyd 실행 파일의 위치를 찾음."""

    command = shutil.which("chronyd")

    if command:
        return command

    brew = shutil.which("brew")

    if brew:
        brew_prefix = subprocess.check_output(
            [brew, "--prefix"],
            text=True,
        ).strip()

        candidate = Path(brew_prefix) / "sbin" / "chronyd"

        if candidate.exists():
            return str(candidate)

    raise RuntimeError(
        "chronyd를 찾을 수 없음. brew install chrony를 실행해야 함."
    )


def measure_time_offset() -> tuple[str, float | None, str]:
    """
    chrony를 이용해 기준 시각과 현재 맥북 시각의
    차이를 측정함.
    """

    chronyd = find_chronyd()

    command = [
        chronyd,
        "-Q",
        "pool pool.ntp.org iburst maxsources 3",
    ]

    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return "FAILED", None, "chrony 측정 시간 초과"

    report = (
        result.stdout
        + "\n"
        + result.stderr
    ).strip()

    if result.returncode != 0:
        return "FAILED", None, report

    # 예:
    # System clock wrong by 0.002341 seconds (ignored)
    match = re.search(
        r"wrong by\s+([+-]?\d+(?:\.\d+)?)\s+seconds",
        report,
        re.IGNORECASE,
    )

    offset_ms = None

    if match:
        offset_seconds = float(match.group(1))
        offset_ms = offset_seconds * 1000

    return "MEASURED", offset_ms, report


# 기능: 사고 영상 업로드를 명령행에서 실행한다.
# 입력: 명령행 EVENT_ID, VEHICLE_ID, VIDEO_PATH.
# 출력: 시간 오차와 요청 결과를 출력하며, 잘못된 인자·경로는 종료 코드 1.
# 구조·이유: chrony 측정값과 사건 시각을 메타데이터에 묶어 영상과 함께 서버로 전송한다.
def main() -> None:
    if len(sys.argv) != 4:
        print(
            "사용법:\n"
            "python client.py EVENT_ID VEHICLE_ID VIDEO_PATH"
        )
        sys.exit(1)

    event_id = sys.argv[1]
    vehicle_id = sys.argv[2]
    video_path = Path(sys.argv[3])

    if not video_path.exists():
        print(f"영상 파일이 없음: {video_path}")
        sys.exit(1)

    print("1. chrony로 시간 오차를 측정함.")

    chrony_status, chrony_offset_ms, chrony_report = (
        measure_time_offset()
    )

    print(f"chrony 상태: {chrony_status}")
    print(f"chrony 오차: {chrony_offset_ms} ms")
    print(chrony_report)

    print("\n2. 사고 Timestamp를 생성함.")

    event_time_ns = time.time_ns()

    print(f"event_time_ns: {event_time_ns}")

    print("\n3. 영상과 시간 정보를 서버로 전송함.")

    form_data = {
        "event_id": event_id,
        "vehicle_id": vehicle_id,
        "event_time_ns": str(event_time_ns),
        "chrony_status": chrony_status,
        "chrony_offset_ms": (
            ""
            if chrony_offset_ms is None
            else str(chrony_offset_ms)
        ),
        "chrony_report": chrony_report,
    }

    with video_path.open("rb") as video_file:
        response = requests.post(
            SERVER_URL,
            data=form_data,
            files={
                "file": (
                    video_path.name,
                    video_file,
                    "video/mp4",
                )
            },
            timeout=60,
        )

    response.raise_for_status()

    print("\n4. 서버 응답을 확인함.")
    print(response.json())


if __name__ == "__main__":
    main()
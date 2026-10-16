import time
from datetime import datetime, timezone

from fastapi import APIRouter


router = APIRouter(
    prefix="/time",
    tags=["time-sync"],
)


@router.get("")
def get_server_time():
    """
    Mac 서버 운영체제의 현재 시각을 반환함.
    실제 시간 동기화는 chrony/NTP가 담당함.
    """

    server_time_ns = time.time_ns()

    server_time_utc = datetime.fromtimestamp(
        server_time_ns / 1_000_000_000,
        tz=timezone.utc,
    )

    return {
        "server_time_ns": server_time_ns,
        "server_time_utc": server_time_utc.isoformat(
            timespec="milliseconds"
        ),
        "clock_type": "CLOCK_REALTIME",
        "purpose": "diagnostic_only",
    }
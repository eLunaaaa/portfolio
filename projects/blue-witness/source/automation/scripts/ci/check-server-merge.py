"""임시 MP4로 실제 FFmpeg 병합 경로를 실행하는 수동 integration 검사."""
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import subprocess

from app.db.merge_repository import EvidenceRecord
from app.services import video_merger

with TemporaryDirectory() as directory:
    root = Path(directory)
    source = root / "source.mp4"
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "color=c=blue:s=160x90:r=10",
            "-t", "1", "-c:v", "libx264", "-pix_fmt", "yuv420p",
            str(source),
        ],
        check=True,
    )
    start_ns = 1_800_000_000_000_000_000
    record = EvidenceRecord(
        id=1,
        event_id="CI_MERGE",
        accident_vehicle_id="CAR_01",
        vehicle_id="CAR_01",
        event_time_ns=start_ns + 500_000_000,
        clip_start_time_ns=start_ns,
        clip_end_time_ns=start_ns + 1_000_000_000,
        time_sync_status="SYNCED",
        chrony_offset_ms=0.0,
        video_path=str(source),
    )
    with patch.object(video_merger, "list_latest_evidence", return_value=[record]):
        with patch.object(video_merger, "MERGED_ROOT", root / "merged"):
            plan = video_merger.merge_event(
                "CI_MERGE", before_seconds=0.25, after_seconds=0.25,
                mode="common", tile_width=160, tile_height=90, fps=10,
            )
    output = Path(plan["output"]["path"])
    assert output.is_file() and output.stat().st_size > 0
    subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=codec_name", "-of", "default=nw=1",
         str(output)],
        check=True,
    )
    print(f"FFmpeg merge PASS: {output.stat().st_size} bytes")

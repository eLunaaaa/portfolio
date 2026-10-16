from pathlib import Path
import html
import shutil
import sqlite3

from fastapi import FastAPI, File, Form, UploadFile
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles


app = FastAPI(title="ACE Mini Server")

DATA_DIR = Path("data")
DATABASE_PATH = Path("ace.db")

DATA_DIR.mkdir(exist_ok=True)

app.mount(
    "/videos",
    StaticFiles(directory=str(DATA_DIR)),
    name="videos",
)


def connect_database() -> sqlite3.Connection:
    """SQLite 데이터베이스에 연결함."""
    connection = sqlite3.connect(DATABASE_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def initialize_database() -> None:
    """프로그램 시작 시 필요한 테이블을 생성함."""
    with connect_database() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS uploads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT NOT NULL,
                vehicle_id TEXT NOT NULL,
                event_time_ns INTEGER NOT NULL,
                chrony_status TEXT NOT NULL,
                chrony_offset_ms REAL,
                chrony_report TEXT,
                video_path TEXT NOT NULL,
                received_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )


initialize_database()


@app.get("/health")
def health():
    """서버 실행 여부를 확인함."""
    return {"status": "ok"}


@app.post("/upload")
async def upload_video(
    event_id: str = Form(...),
    vehicle_id: str = Form(...),
    event_time_ns: int = Form(...),
    chrony_status: str = Form(...),
    chrony_offset_ms: str = Form(""),
    chrony_report: str = Form(""),
    file: UploadFile = File(...),
):
    """차량으로부터 영상과 시간 정보를 수신함."""

    save_directory = DATA_DIR / event_id / vehicle_id
    save_directory.mkdir(parents=True, exist_ok=True)

    video_path = save_directory / "clip.mp4"

    with video_path.open("wb") as output_file:
        shutil.copyfileobj(file.file, output_file)

    await file.close()

    offset_value = None

    if chrony_offset_ms:
        try:
            offset_value = float(chrony_offset_ms)
        except ValueError:
            offset_value = None

    with connect_database() as connection:
        connection.execute(
            """
            INSERT INTO uploads (
                event_id,
                vehicle_id,
                event_time_ns,
                chrony_status,
                chrony_offset_ms,
                chrony_report,
                video_path
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_id,
                vehicle_id,
                event_time_ns,
                chrony_status,
                offset_value,
                chrony_report,
                str(video_path),
            ),
        )

    return {
        "saved": True,
        "event_id": event_id,
        "vehicle_id": vehicle_id,
        "chrony_status": chrony_status,
        "chrony_offset_ms": offset_value,
    }


@app.get("/uploads", response_class=HTMLResponse)
def show_uploads():
    """SQLite에 저장된 업로드 결과를 브라우저에 표시함."""

    with connect_database() as connection:
        rows = connection.execute(
            """
            SELECT
                id,
                event_id,
                vehicle_id,
                event_time_ns,
                chrony_status,
                chrony_offset_ms,
                video_path,
                received_at
            FROM uploads
            ORDER BY id DESC
            """
        ).fetchall()

    cards = []

    for row in rows:
        relative_path = (
            Path(row["video_path"])
            .relative_to(DATA_DIR)
            .as_posix()
        )

        video_url = f"/videos/{relative_path}"

        offset = row["chrony_offset_ms"]

        if offset is None:
            offset_text = "측정값 없음"
        else:
            offset_text = f"{offset:.3f} ms"

        cards.append(
            f"""
            <div style="
                border: 1px solid #cccccc;
                margin-bottom: 20px;
                padding: 16px;
            ">
                <h2>
                    {html.escape(row["event_id"])}
                    /
                    {html.escape(row["vehicle_id"])}
                </h2>

                <p>사고 Timestamp: {row["event_time_ns"]}</p>
                <p>chrony 상태: {html.escape(row["chrony_status"])}</p>
                <p>chrony 측정 오차: {offset_text}</p>
                <p>서버 수신 시각: {row["received_at"]}</p>

                <video
                    src="{video_url}"
                    width="480"
                    controls>
                </video>
            </div>
            """
        )

    if not cards:
        cards.append("<p>아직 저장된 사고 영상이 없음.</p>")

    return f"""
    <!DOCTYPE html>
    <html lang="ko">
    <head>
        <meta charset="UTF-8">
        <title>ACE Mini Server</title>
    </head>
    <body style="
        max-width: 900px;
        margin: 40px auto;
        font-family: sans-serif;
    ">
        <h1>ACE 사고 영상 수집 결과</h1>

        {''.join(cards)}
    </body>
    </html>
    """
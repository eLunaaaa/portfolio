# 쓰레기통 관제용 Flask 서버입니다.
# 위치 t1/t2의 일반·캔·유리·PET 상태를 저장하고 센서 갱신 API와 화면 조회 API를 제공합니다.
# create_app 내부의 data/lock과 route 함수가 한 앱의 상태를 공유하며 index.html이 /status를 주기적으로 조회합니다.

"""Local prototype dashboard; GET update routes retained for ESP8266 compatibility."""
from threading import Lock
from flask import Flask, jsonify, render_template


# 기능: 독립적인 쓰레기통 상태 저장소와 Flask 경로를 구성합니다.
# 입력: 없음. 앱별 데이터와 lock을 새로 만듭니다.
# 출력: 구성된 Flask 앱 객체.
# 처리 구조: 두 위치·네 종류 상태를 None으로 시작하고 화면, 조회, 변경, reset 경로를 내부 함수로 등록합니다.
def create_app():
    app = Flask(__name__)
    lock = Lock()
    # None means no measurement received, not an empty bin.
    data = {zone: {kind: None for kind in ("ge", "ca", "gl", "pe")}
            for zone in ("t1", "t2")}

    # 기능: 쓰레기통 현황 대시보드의 첫 화면을 제공한다.
    # 입력: GET / 요청; 별도 인자 없음.
    # 출력: index.html을 렌더링한 HTML.
    # 구조·이유: 센서 상태는 별도 API로 받아 화면과 상태 전송을 분리한다.
    @app.get("/")
    # 기능: 관제 대시보드의 첫 화면을 반환합니다.
    # 입력: HTTP GET /.
    # 출력: index.html 렌더링 결과.
    # 처리 구조: Flask template에 정의된 지도·상세창·상태 갱신 화면을 반환합니다.
    def home():
        return render_template("index.html")

    # 기능: 위치별 쓰레기통 적재 상태를 반환한다.
    # 입력: 현재 공유 data와 GET 상태 조회 요청.
    # 출력: 위치별 상태 JSON과 Cache-Control: no-store 헤더.
    # 구조·이유: 잠금 안에서 복사한 스냅샷을 반환해 동시 갱신 충돌과 오래된 캐시 표시를 줄인다.
    @app.get("/status")
    # 기능: 모든 위치·종류의 현재 포화 상태를 조회합니다.
    # 입력: HTTP GET /status.
    # 출력: 위치별 종류 상태 JSON; None=미수신, false=여유, true=포화.
    # 처리 구조: lock 안에서 상태를 복사해 응답하고 no-store를 지정해 이전 응답 cache를 사용하지 않게 합니다.
    def status():
        with lock:
            result = {zone: values.copy() for zone, values in data.items()}
        response = jsonify(result)
        response.headers["Cache-Control"] = "no-store"
        return response

    # 기능: 장치가 보낸 적재·비움 상태를 반영한다.
    # 입력: 위치 bin_id, 동작 action(fill·empty), 분류 type_id.
    # 출력: 성공 안내 또는 잘못된 위치 404·동작/분류 400.
    # 구조·이유: 입력을 검증한 뒤 잠금 안에서 해당 분류를 갱신한다. al은 위치 내 전체 분류를 뜻한다.
    @app.get("/<bin_id>/<action>/<type_id>")
    # 기능: 센서가 보고한 위치·종류별 포화 상태를 저장합니다.
    # 입력: URL의 bin_id: t1/t2, action: fill/empty, type_id: ge/ca/gl/pe/al.
    # 출력: 성공 문자열과 HTTP 200, 잘못된 위치·명령·종류이면 404/400.
    # 처리 구조: 인자를 먼저 검증하고 lock 안에서 선택 종류 또는 al 전체 종류를 변경합니다.
    def control_bin(bin_id, action, type_id):
        if bin_id not in data:
            return "ERROR: Wrong Bin ID", 404
        if type_id not in ("ge", "ca", "gl", "pe", "al"):
            return "ERROR: Wrong Type", 400
        if action not in ("fill", "empty"):
            return "ERROR: Wrong Action", 400
        with lock:
            kinds = list(data[bin_id]) if type_id == "al" else [type_id]
            for kind in kinds:
                data[bin_id][kind] = action == "fill"
        response = app.make_response(f"OK: {bin_id} {type_id} {action.upper()}")
        response.headers["Cache-Control"] = "no-store"
        return response

    # 기능: 전체 적재 상태를 미확인으로 초기화한다.
    # 입력: 공유 쓰레기통 상태 data.
    # 출력: 초기화 안내 문자열.
    # 구조·이유: 잠금 안에서 모든 값을 None으로 바꿔 비어 있음과 아직 측정하지 않음을 구분한다.
    @app.post("/reset")
    # 기능: 저장된 모든 상태를 미확인 상태로 초기화합니다.
    # 입력: HTTP POST /reset.
    # 출력: reset 완료 문자열과 모든 상태 None.
    # 처리 구조: lock 안에서 모든 위치·종류를 순회해 값만 초기화합니다.
    def reset():
        with lock:
            for values in data.values():
                for kind in values:
                    values[kind] = None
        return "RESET ALL TO UNKNOWN"

    return app


app = create_app()

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)

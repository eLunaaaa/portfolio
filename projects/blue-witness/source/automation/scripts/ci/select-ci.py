"""Choose affected checks and keep main's Pi deployment bundle complete."""
import json
import os

PI_COMPONENTS = ("sensor", "filter", "p2p", "ai", "time_sync", "uploader")


# 기능: 변경 경로와 이벤트에 따라 CI 작업 집합을 결정한다.
# 입력: 매칭 구성요소 matches, 이벤트 event, Git ref.
# 출력: 구성요소 JSON과 server·contracts·aruco 실행 여부 문자열의 dict.
# 구조·이유: 수동 실행은 전체 검사를 선택하고 main의 Pi 변경은 배포에 필요한 모든 Pi 바이너리를 빌드한다.
def select_checks(matches, event, ref):
    full = event == "workflow_dispatch"
    selected = [name for name in PI_COMPONENTS if full or name in matches]
    # A main Pi change produces all six binaries for the existing fleet deployer.
    if selected and ref == "refs/heads/main":
        selected = list(PI_COMPONENTS)
    return {
        "components": json.dumps(selected, separators=(",", ":")),
        "server": str(full or "server" in matches or "contracts" in matches).lower(),
        "contracts": str(full or "contracts" in matches).lower(),
        "aruco": str(full or "aruco" in matches).lower(),
    }


if __name__ == "__main__":
    outputs = select_checks(json.loads(os.environ["MATCHES"]),
                            os.environ["CI_EVENT"], os.environ["CI_REF"])
    with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
        for key, value in outputs.items():
            output.write(f"{key}={value}\n")

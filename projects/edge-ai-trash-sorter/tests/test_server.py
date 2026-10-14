# 기능 검산용 테스트 파일입니다.
# 고정·경계·오류 입력 또는 대역 환경에서 함수 결과를 기대값과 비교합니다.

import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))
from app import create_app

class ServerTests(unittest.TestCase):
    # 기능: 각 서버 테스트에 독립 앱과 요청 client를 준비합니다.
    # 입력: unittest가 제공하는 self.
    # 출력: self.client에 새로운 Flask test client.
    # 처리 구조: create_app으로 상태를 새로 만들고 test_client를 연결합니다.
    def setUp(self):
        self.client = create_app().test_client()

    # 기능: 서버 초기 None 상태와 화면·정적 배치도 응답을 확인합니다.
    # 입력: 함수 내부의 고정 표본·대역 환경.
    # 출력: 정상 조건은 assertion 통과, 차이는 assertion/테스트 실패.
    # 처리 구조: 정상·경계·오류 조건을 구성해 기능의 기대 결과와 비교합니다.
    def test_unknown_initial_state_and_assets(self):
        response = self.client.get("/status")
        self.assertIsNone(response.json["t1"]["ca"])
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertEqual(self.client.get("/").status_code, 200)
        with self.client.get("/static/images/demo-layout.svg") as asset:
            self.assertEqual(asset.status_code, 200)

    # 기능: 한 위치·종류의 변경이 다른 위치나 종류를 바꾸지 않는지 확인합니다.
    # 입력: 함수 내부의 고정 표본·대역 환경.
    # 출력: 정상 조건은 assertion 통과, 차이는 assertion/테스트 실패.
    # 처리 구조: 정상·경계·오류 조건을 구성해 기능의 기대 결과와 비교합니다.
    def test_independent_zones_and_types(self):
        self.assertEqual(self.client.get("/t1/fill/ca").status_code, 200)
        data = self.client.get("/status").json
        self.assertTrue(data["t1"]["ca"])
        self.assertIsNone(data["t1"]["ge"])
        self.assertIsNone(data["t2"]["ca"])
        self.client.get("/t1/empty/ca")
        self.assertFalse(self.client.get("/status").json["t1"]["ca"])

    # 기능: al 전체 변경과 POST reset, GET reset 거부를 확인합니다.
    # 입력: 함수 내부의 고정 표본·대역 환경.
    # 출력: 정상 조건은 assertion 통과, 차이는 assertion/테스트 실패.
    # 처리 구조: 정상·경계·오류 조건을 구성해 기능의 기대 결과와 비교합니다.
    def test_bulk_and_reset(self):
        self.client.get("/t2/fill/al")
        self.assertTrue(all(self.client.get("/status").json["t2"].values()))
        self.assertEqual(self.client.get("/reset").status_code, 405)
        self.assertEqual(self.client.post("/reset").status_code, 200)
        self.assertTrue(all(v is None for row in self.client.get("/status").json.values() for v in row.values()))

    # 기능: 잘못된 API 인자에 오류 코드를 반환하고 기존 상태를 보존하는지 확인합니다.
    # 입력: 함수 내부의 고정 표본·대역 환경.
    # 출력: 정상 조건은 assertion 통과, 차이는 assertion/테스트 실패.
    # 처리 구조: 정상·경계·오류 조건을 구성해 기능의 기대 결과와 비교합니다.
    def test_invalid_update_does_not_mutate(self):
        before = self.client.get("/status").json
        for path, code in [("/t3/fill/ca",404),("/t1/garbage/ca",400),("/t1/fill/unknown",400)]:
            self.assertEqual(self.client.get(path).status_code, code)
        self.assertEqual(self.client.get("/status").json, before)

    # 기능: 별개 Flask 앱이 같은 상태 저장소를 공유하지 않는지 확인합니다.
    # 입력: 함수 내부의 고정 표본·대역 환경.
    # 출력: 정상 조건은 assertion 통과, 차이는 assertion/테스트 실패.
    # 처리 구조: 정상·경계·오류 조건을 구성해 기능의 기대 결과와 비교합니다.
    def test_app_instances_do_not_share_state(self):
        self.client.get("/t1/fill/al")
        self.assertIsNone(create_app().test_client().get("/status").json["t1"]["ca"])

if __name__ == "__main__":
    unittest.main()

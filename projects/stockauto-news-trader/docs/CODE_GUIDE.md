# 코드를 처음 읽는 순서

이 프로그램은 뉴스를 주문으로 바로 연결하지 않고 **후보 → 화면 제안 → 사용자 결정 → 재검사 → 원장 저장 → 전송**으로 나눕니다. 금액은 원 단위 정수, 시간은 timezone을 포함한 `datetime`으로 전달합니다.

## 1. 데이터와 순수 판단 함수

| 파일·함수 | 입력 | 출력·역할 |
|---|---|---|
| [models.py](../stockauto/models.py) | 기사·시세·계좌·결정 정보 | Article, Candidate, Quote, Account, Offer, Submission 자료형 |
| [news.parse_feed](../stockauto/news.py) | RSS/Atom bytes·현재시각 | 날짜·크기·XML을 검사한 기사 목록 |
| `news.analyze` | 기사·관심종목 | 기업 문맥·점수·중요도·확정 여부·근거를 가진 후보 |
| `news.same_event` | 두 사건의 특징 JSON | 사건군·금액·핵심 단어가 같은지 판단 |
| [market.order_quantity](../stockauto/market.py) | 현금·예산·가격·수수료 | 예산 안에서 살 수 있는 정수 수량 |
| `market.technical_buy` | 과거→현재 종가 | MA20·MA60·RSI(2)·이틀 변화 조건 |
| [risk.entry_reason](../stockauto/risk.py) | 현재 호가·설정·최초 가격 | 매수 제외 사유 또는 빈 문자열 |
| `risk.exit_reason` | 보유 수량·평균가·호가·보유시각 | 손절·익절·기간·RSI 사유 |

## 2. 승인과 주문 상태

| 파일·함수 | 입력 | 출력·구조 |
|---|---|---|
| [Engine.propose](../stockauto/engine.py) | 뉴스/RSI 후보 | 계좌·호가·정책 검사 후 고정된 Offer, 또는 제안 없음 |
| `Engine.decide` | 현재 제안 ID·BUY/NO | 거절·만료·재검사 또는 단 한 번의 주문 전송 |
| [Store.start_buy](../stockauto/store.py) | 승인한 Offer·현재시각 | 상태 변경과 매수 원장을 같은 SQLite 트랜잭션에 기록 |
| `Store.finish_job` | 로컬 ID·접수 결과 | ACCEPTED/FAILED/UNKNOWN 영구 기록 |
| `Store.reconcile_jobs` | 증권사 상태·현재 계좌 | 명시적 체결·취소·거부 확인 후 원장 정리 |
| [RiskManager.tick](../stockauto/risk.py) | 계좌·미체결·시세 조회 결과 | 취소·매도 전 선기록, 전송 결과 목록 |

```text
OFFERED → REJECTED / EXPIRED / FAILED
        → SUBMITTING → ACCEPTED / FAILED / UNKNOWN
```

`ACCEPTED`는 전송 성공입니다. `UNKNOWN`은 결과 확인이 필요하므로 자동 재전송하지 않습니다. 상태와 매수 원장은 외부 API 호출 전에 함께 commit하며, 원장 저장 실패 시 주문 함수를 호출하지 않습니다.

## 3. PC 실행과 외부 연결

| 파일·함수 | 입력 | 출력·구조 |
|---|---|---|
| [Runner.tick](../stockauto/app.py) | 장치 입력·수집 Future·시계 | 버튼·만료·새 제안·주문 관리 직렬 처리 |
| `Device.messages` | USB 수신 bytes | 검증한 READY/PING/DECIDE 메시지, 과대 줄 폐기 |
| [protocol.offer_frame](../stockauto/protocol.py) | 제안·알람 기준·현재시각 | UNO에 보낼 ASCII·LF bytes |
| [KiwoomBroker](../stockauto/kiwoom.py) | TR 요청·Qt 이벤트·주문 인자 | GUI 스레드에서 계좌/호가 조회·주문 전송 |
| [PaperBroker](../stockauto/broker.py) | 동일한 계좌·호가·주문 요청 | 로컬 금액 예약·체결/취소 재현 |

RSS HTTP만 작업 스레드에서 수집합니다. Qt/ActiveX와 주문 상태 변경은 메인 스레드에서 처리합니다. `Runner.busy`는 TR 대기 중 Qt 이벤트가 재진입해도 두 주문 경로가 겹치지 않도록 합니다.

## 4. UNO 펌웨어

[news_lcd.ino](../firmware/news_lcd/news_lcd.ino)는 다음 순서로 읽습니다.

| 함수 | 입력 | 출력·이유 |
|---|---|---|
| `readSerial` → `handleFrame` | 줄 단위 ASCII | 모든 필드 검증 뒤 화면·현재 제안 교체 |
| `draw` → `printRow` | 제안·뉴스·시간 | 16칸 채움·스크롤·페이지 전환 |
| `pressed` | 버튼 핀·35ms 경과 | 안정된 새 눌림만 반환 |
| `startAlarm` → `updateAlarm` | 중요도·기준·millis | delay 없이 2,200Hz 세 번 알림 |
| `loop` | 통신·버튼·시계 | 만료·동시 누름·중복 방지 후 DECIDE 전송 |

## 5. 검사와 재생

[tests](../tests)는 실제 모듈과 `.ino`를 호출합니다. [회귀 검사](../tests/test_review_regressions.py)는 DB INSERT 실패를 주입해 상태 rollback·브로커 호출 0회를 확인합니다. [replay.py](../stockauto/replay.py)는 해당 시각까지의 뉴스·종가로 신호를 만들고 다음 봉에서 체결을 계산합니다.

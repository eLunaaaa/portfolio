"""파일 역할: 네트워크/계좌/보드 없이 뉴스·승인·실패·재시작 경로를 검사합니다.
구조: 고정 시계와 가짜 브로커를 사용합니다. 이유: 실제 주문을 만들지 않고 같은 상태 머신을 검증합니다.
"""
from dataclasses import replace
from datetime import datetime, timedelta
from email.utils import format_datetime
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from stockauto.app import Device, InstanceLock, Runner, load_settings
from stockauto.broker import PaperBroker
from stockauto.engine import Engine
from stockauto.market import KST, can_buy, estimated_cost, order_quantity, rsi2
from stockauto.models import Article, Account, Quote, Submission
from stockauto.news import analyze, parse_feed
from stockauto.protocol import offer_frame, parse_device
from stockauto.store import Store


class SystemTests(unittest.TestCase):
    """뉴스 후보부터 단 한 번의 주문까지 정상/오류/경계 조건을 확인합니다."""
    def setUp(self):
        """기능: 격리 환경 준비. 입력: 없음. 출력: 시계·DB·설정·브로커·엔진.
        구조·이유: 테스트마다 DB와 금액을 새로 만들어 상태가 섞이지 않게 합니다.
        """
        self.directory=tempfile.TemporaryDirectory()
        self.now=datetime(2026,10,16,10,tzinfo=KST)
        self.clock=lambda:self.now
        self.settings=load_settings('config/settings.json')
        self.settings.update(max_order_won=1000000,max_positions=2,paper_cash_won=1000000)
        self.store=Store(Path(self.directory.name)/'orders.db')
        self.broker=PaperBroker(self.settings,self.clock)
        self.engine=Engine(self.broker,self.store,self.settings,self.clock)
        self.article=Article('news-key','삼성전자 사상 최대 실적과 공급계약 수주','https://example.com/a',self.now)
        self.candidate=analyze([self.article],self.settings['watchlist'])[0]

    def tearDown(self):
        """기능: 임시 자원 정리. 입력: 없음. 출력: 없음. 이유: 파일 잠금과 테스트 잔여물을 없앱니다."""
        self.store.close();self.directory.cleanup()

    def test_news_reason_and_alarm_score(self):
        """입력: 호재 2종 기사. 결과: 설명 가능한 점수 6과 중요도 6."""
        self.assertEqual((self.candidate.score,self.candidate.importance),(6,6))
        self.assertIn('NEW CONTRACT',self.candidate.reason)

    def test_negative_news_never_buys(self):
        """입력: 호재와 리콜이 섞인 기사. 결과: 부정 점수, 제안 없음."""
        article=replace(self.article,title='삼성전자 수주 사상 최대, 리콜 소송')
        candidate=analyze([article],self.settings['watchlist'])[0]
        self.assertLess(candidate.score,0)
        self.assertIsNone(self.engine.propose(candidate))

    def test_company_name_boundary(self):
        """입력: 다른 기업명 안에 포함된 이름. 결과: 잘못된 종목 연결 없음."""
        self.assertEqual(analyze([replace(self.article,title='가짜삼성전자공업 수주')],self.settings['watchlist']),[])

    def test_rss_duplicate_and_old_date(self):
        """입력: 중복 기사·유효 날짜. 결과: 하나만 보존하고 오래된 시각은 제외."""
        xml=f'<rss><channel><item><title>삼성전자 수주</title><link>https://example.com/a</link><pubDate>{format_datetime(self.now)}</pubDate></item></channel></rss>'.encode()
        self.assertEqual(len(parse_feed(xml,self.now)),1)
        self.assertEqual(parse_feed(xml,self.now+timedelta(days=2)),[])

    def test_rss_entity_rejected(self):
        """입력: XML 엔티티. 결과: 파일/외부 엔티티를 확장하지 않고 예외."""
        with self.assertRaises(Exception):parse_feed(b'<!DOCTYPE r [<!ENTITY x SYSTEM "file:///etc/passwd">]><r>&x;</r>',self.now)

    def test_time_boundaries_holidays(self):
        """입력: 평일/주말/휴장일/15시. 결과: 허용 시간만 True."""
        self.assertTrue(can_buy(self.now))
        self.assertFalse(can_buy(self.now.replace(hour=15)))
        self.assertFalse(can_buy(self.now+timedelta(days=1)))
        self.assertFalse(can_buy(self.now,['2026-10-16']))

    def test_quantity_includes_fee(self):
        """입력: 100원·10원·수수료. 결과: 10주 대신 9주, 비용은 예산 이하."""
        qty=order_quantity(100,100,10,0.0035)
        self.assertEqual(qty,9)
        self.assertLessEqual(estimated_cost(qty,10,0.0035),100)
        self.assertEqual(rsi2([10,10,10]),50)

    def test_approval_sends_exactly_once(self):
        """입력: 같은 BUY 2회. 결과: 주문 한 건, 두 번째 입력 무시."""
        offer=self.engine.propose(self.candidate)
        self.assertEqual(self.engine.decide(offer.id,'BUY')[0],'ACCEPTED')
        self.assertEqual(self.engine.decide(offer.id,'BUY')[0],'IGNORED')
        self.assertEqual(len(self.broker.orders),1)

    def test_reject_never_orders_or_reoffers(self):
        """입력: 구매 X. 결과: 잔고 변화·주문·같은 뉴스 재제안 없음."""
        offer=self.engine.propose(self.candidate);cash=self.broker.cash
        self.assertEqual(self.engine.decide(offer.id,'NO')[0],'REJECTED')
        self.assertEqual(self.broker.cash,cash)
        self.assertIsNone(self.engine.propose(self.candidate))

    def test_expired_and_stale_id(self):
        """입력: 다른 ID와 시간 지난 승인. 결과: 모두 무시, 주문 없음."""
        offer=self.engine.propose(self.candidate)
        self.assertEqual(self.engine.decide('0'*16,'BUY')[0],'IGNORED')
        self.now+=timedelta(seconds=91)
        self.assertEqual(self.engine.decide(offer.id,'BUY')[0],'IGNORED')
        self.assertEqual(self.store.state(offer.id),'EXPIRED')

    def test_price_rise_requires_new_decision(self):
        """입력: 승인 도중 호가 상승. 결과: 화면 가격을 넘어선 주문 없음."""
        offer=self.engine.propose(self.candidate)
        self.settings['paper_quotes']['005930']['ask']+=100
        self.assertEqual(self.engine.decide(offer.id,'BUY')[0],'FAILED')
        self.assertFalse(self.broker.orders)

    def test_pending_and_zero_holdings(self):
        """입력: 기존 미체결 종목. 결과: 중복 제안 거부."""
        self.broker.pending.add('005930')
        with self.assertRaises(ValueError):self.engine.propose(self.candidate)

    def test_failed_api_does_not_deduct(self):
        """입력: API 명시적 실패. 결과: FAILED 기록, 잔고·미체결 변화 없음."""
        offer=self.engine.propose(self.candidate);cash=self.broker.cash
        self.broker.buy_limit=lambda *_:Submission('FAILED','API -1')
        self.assertEqual(self.engine.decide(offer.id,'BUY')[0],'FAILED')
        self.assertEqual(self.broker.cash,cash)
        self.assertFalse(self.broker.pending)

    def test_unknown_does_not_retry(self):
        """입력: 전송 중 예외. 결과: UNKNOWN 저장과 종목 잠금, 재주문 없음."""
        offer=self.engine.propose(self.candidate)
        def fail(*_):raise TimeoutError('응답 없음')
        self.broker.buy_limit=fail
        self.assertEqual(self.engine.decide(offer.id,'BUY')[0],'UNKNOWN')
        self.assertIn('005930',self.store.locked_codes())

    def test_restart_recovers_inflight(self):
        """입력: SUBMITTING 상태에서 재시작. 결과: UNKNOWN으로 복구해 자동 재전송 차단."""
        offer=self.engine.propose(self.candidate)
        self.store.transition(offer.id,'OFFERED','SUBMITTING')
        self.store.close();self.store=Store(Path(self.directory.name)/'orders.db')
        self.assertEqual(self.store.state(offer.id),'UNKNOWN')

    def test_disconnect_expires(self):
        """입력: Arduino 연결 초기화. 결과: 기존 제안 만료."""
        offer=self.engine.propose(self.candidate);self.engine.disconnect()
        self.assertEqual(self.store.state(offer.id),'EXPIRED')

    def test_protocol_and_alarm(self):
        """입력: 제안 프레임과 깨진 응답. 결과: 중요도/임계값 전달, 잘못된 응답 거부."""
        offer=self.engine.propose(self.candidate)
        frame=offer_frame(offer,5,self.now)
        self.assertIn(b'|6|5|90|',frame)
        self.assertEqual(parse_device(f'DECIDE|{offer.id}|BUY\n'.encode()),('DECIDE',offer.id,'BUY'))
        for bad in [b'DECIDE|wrong|BUY',b'X'*100,b'\xff',b'DECIDE|0000000000000000|SELL']:
            self.assertIsNone(parse_device(bad))

    def test_stale_quote(self):
        """입력: 11초 지난 호가. 결과: 제안 거부."""
        self.broker.quote=lambda code:Quote(70000,self.now-timedelta(seconds=11))
        with self.assertRaises(ValueError):self.engine.propose(self.candidate)

    def test_recheck_cash(self):
        """입력: 승인 전 주문가능금액 감소. 결과: 주문 생략."""
        offer=self.engine.propose(self.candidate);self.broker.cash=1
        self.assertEqual(self.engine.decide(offer.id,'BUY')[0],'FAILED')

    def test_local_reservation_when_server_lags(self):
        """입력: 서버 잔고가 주문을 아직 반영하지 않음. 결과: DB 예약액만큼 가용 금액 감소."""
        offer=self.engine.propose(self.candidate)
        self.broker.buy_limit=lambda *_:Submission('ACCEPTED','전송 성공')
        self.engine.decide(offer.id,'BUY')
        self.settings['paper_quotes']['000660']['ask']=10000
        account,_=self.engine._checked('000660',self.now)
        self.assertLess(account.cash,self.broker.cash)

    def test_demo_never_live(self):
        """입력: 실전+시간검사 생략 설정. 결과: 시작 전 거부."""
        path=Path(self.directory.name)/'bad.json'
        import json
        path.write_text(json.dumps(dict(self.settings,mode='kiwoom',demo=True)))
        with self.assertRaises(ValueError):load_settings(path)

    def test_single_instance(self):
        """입력: 동일 DB의 두 실행. 결과: 두 번째 잠금 실패."""
        path=Path(self.directory.name)/'lock'
        one=InstanceLock(path)
        try:
            with self.assertRaises(RuntimeError):InstanceLock(path)
        finally:one.close()


    def test_company_name_particle(self):
        """입력: 기업명 뒤 한국어 조사. 결과: 관련 기업을 정상 연결."""
        self.assertEqual(len(analyze([replace(self.article,title='삼성전자가 공급계약 수주')],self.settings['watchlist'])),1)

    def test_runner_from_news_to_button_order(self):
        """입력: RSS 작업 결과→READY→BUY. 결과: LCD 제안 후 정확히 한 주문과 응답."""
        from concurrent.futures import Future
        import time
        class FakeDevice:
            # 입력·출력: 메시지 큐/전송 목록. 이유: 실제 USB 없이 Runner 전체 흐름을 검사합니다.
            ready=False
            incoming=[('READY','','')]
            sent=[]
            def messages(self):
                # 기능: 장치 입력 반환. 출력: 대기 메시지. 구조: 한 번 읽은 입력은 제거.
                result=self.incoming;self.incoming=[];return result
            def send(self,frame):
                # 기능: PC 전송 기록. 입력: bytes. 출력: sent 목록.
                self.sent.append(frame)
            def close(self):
                # 기능: 테스트 장치 정리. 입력·출력 없음.
                pass
        device=FakeDevice();runner=Runner(self.settings,self.engine,device)
        try:
            runner.last_fetch=time.monotonic();runner.future=Future();runner.future.set_result(([self.article],[]))
            runner.tick()
            self.assertTrue(any(frame.startswith(b'OFFER|') for frame in device.sent))
            offer=self.engine.active
            device.incoming=[('DECIDE',offer.id,'BUY'),('DECIDE',offer.id,'BUY')]
            runner.tick()
            self.assertEqual(len(self.broker.orders),1)
            self.assertTrue(any(b'|ACCEPTED' in frame for frame in device.sent))
        finally:runner.close()

    def test_device_discards_overlong_line(self):
        """입력: 과대 수신 줄 뒤 정상 거절. 결과: 과대 줄 전체 폐기, 다음 줄만 인식."""
        class Port:
            # 입력: 고정 수신 bytes. 출력: 순서대로 읽을 bytes. 이유: 버퍼 경계를 검사합니다.
            incoming=b'X'*100+b'\nDECIDE|abcdef0123456789|NO\n'
            @property
            def in_waiting(self):
                # 기능: 대기 바이트 수. 입력 없음. 출력 정수.
                return len(self.incoming)
            def read(self,n):
                # 기능: n바이트 소비. 입력: n. 출력: 잘린 bytes.
                result=self.incoming[:n];self.incoming=self.incoming[n:];return result
        device=object.__new__(Device);device.serial=Port();device.buffer=bytearray();device.dropping=False
        self.assertEqual(device.messages(),[('DECIDE','abcdef0123456789','NO')])

    def test_kiwoom_adapter_cleans_completed_orders(self):
        """입력: 수량 0 잔고와 완료 주문. 결과: 계좌 snapshot에서 제외."""
        from stockauto.kiwoom import KiwoomBroker
        broker=object.__new__(KiwoomBroker);broker.account_number='TEST'
        broker._request=lambda *args:({'주문가능금액':'1000'},[],False)
        broker._pages=lambda tr,*args: ([{'종목번호':'A005930','보유수량':'0'}] if tr=='opw00018' else [{'종목코드':'005930','미체결수량':'0'}])
        result=broker.account()
        self.assertEqual((result.cash,result.holdings,result.pending),(1000,frozenset(),frozenset()))


if __name__=='__main__':unittest.main()

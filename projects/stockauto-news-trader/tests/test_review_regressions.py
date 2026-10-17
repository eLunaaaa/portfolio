"""파일 역할: 주문 선기록의 원자성과 재생 설정·입력 경계의 회귀를 검사합니다.
입력: 임시 SQLite, 가상 뉴스와 봉. 출력: 상태·호출 횟수·거래 결과 검사.
이유: 기존 정상 경로 검사에 더해 DB 실패와 비정상 시세를 실제 처리 경로에 주입합니다.
"""
from datetime import timedelta
from unittest.mock import Mock
from unittest import TestCase
import sqlite3
import test_system as base
from stockauto.replay import replay, validate_dataset


class ReviewRegressionTests(TestCase):
    def setUp(self):
        """입력: 없음. 출력: 고정 시계·임시 DB·PaperBroker. 이유: 계좌 없이 같은 주문 경로를 검사합니다."""
        base.SystemTests.setUp(self)

    def tearDown(self):
        """입력: 테스트 자원. 출력: 없음. 이유: DB를 닫아 검사 간 상태를 분리합니다."""
        base.SystemTests.tearDown(self)

    def falling_market(self):
        """입력: 고정된 시험 시각. 출력: 매수 뒤 손절 조건에 도달하는 세 봉과 기사.
        구조·이유: 두 번째 봉에 매수하고 세 번째 봉에서 매도할 수 있도록 설정 차이를 드러냅니다.
        """
        return dict(synthetic=True, bars=[
            dict(at=self.now.isoformat(), code='005930', open=70000, close=70000, volume_ratio=2),
            dict(at=(self.now+timedelta(minutes=5)).isoformat(), code='005930', open=69000, close=60000, volume_ratio=2),
            dict(at=(self.now+timedelta(minutes=10)).isoformat(), code='005930', open=60000, close=60000, volume_ratio=2),
        ], news=[dict(published_at=self.now.isoformat(), title=self.article.title)])

    def test_buy_state_and_job_roll_back_together(self):
        """입력: 원장 INSERT를 거부하는 DB trigger. 출력: 제안은 OFFERED, 원장은 0건.
        이유: 로컬 저장 실패를 증권사 전송 결과 미확정으로 잘못 남기지 않습니다.
        """
        offer=self.engine.propose(self.candidate)
        self.store.db.execute("CREATE TEMP TRIGGER fail_job BEFORE INSERT ON jobs BEGIN SELECT RAISE(ABORT, 'test disk failure'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.start_buy(offer,self.now)
        self.assertEqual(self.store.state(offer.id),'OFFERED')
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0],0)

    def test_local_ledger_failure_never_calls_broker(self):
        """입력: 승인 후 원장 저장 오류. 출력: FAILED·브로커 0회. 이유: 기록 없는 외부 주문을 막습니다."""
        offer=self.engine.propose(self.candidate)
        self.store.start_buy=Mock(side_effect=sqlite3.OperationalError('test disk failure'))
        self.broker.buy_limit=Mock()
        self.assertEqual(self.engine.decide(offer.id,'BUY')[0],'FAILED')
        self.broker.buy_limit.assert_not_called()
        self.assertEqual(self.store.state(offer.id),'FAILED')

    def test_repeated_start_buy_does_not_duplicate_ledger(self):
        """입력: 동일 승인 선기록 2회. 출력: True·False와 원장 1건. 이유: 조건부 상태 변경을 검사합니다."""
        offer=self.engine.propose(self.candidate)
        self.assertTrue(self.store.start_buy(offer,self.now))
        self.assertFalse(self.store.start_buy(offer,self.now))
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0],1)

    def test_replay_honors_auto_exit(self):
        """입력: 같은 하락 시세, 청산 on/off. 출력: on만 매도. 이유: 실행 설정과 재생 결과를 맞춥니다."""
        data=self.falling_market()
        enabled=replay(data,dict(self.settings,auto_exit=True),'news')
        disabled=replay(data,dict(self.settings,auto_exit=False),'news')
        self.assertEqual([f['side'] for f in enabled['fills']],['BUY','SELL'])
        self.assertEqual([f['side'] for f in disabled['fills']],['BUY'])

    def test_replay_honors_disabled_risk_management(self):
        """입력: 관리 전체 off, auto_exit on. 출력: 매수 잔고 유지. 이유: 실행본의 risk_enabled를 반영합니다."""
        result=replay(self.falling_market(),dict(self.settings,risk_enabled=False,auto_exit=True),'news')
        self.assertEqual([f['side'] for f in result['fills']],['BUY'])
        self.assertEqual(result['final_positions'],1)

    def test_replay_rejects_non_finite_volume(self):
        """입력: NaN·무한대·bool 거래량. 출력: ValueError. 이유: 비교식을 통과해 버리는 NaN을 차단합니다."""
        for value in (float('nan'),float('inf'),True):
            with self.subTest(value=value):
                data=self.falling_market();data['bars'][0]['volume_ratio']=value
                with self.assertRaises(ValueError):validate_dataset(data)

    def test_replay_rejects_invalid_quotes(self):
        """입력: 음수·역전 호가. 출력: ValueError. 이유: 비정상 입력으로 가상 성과가 계산되지 않게 합니다."""
        for quote in (dict(ask=-1),dict(ask=70000,bid=71000)):
            with self.subTest(quote=quote):
                data=self.falling_market();data['bars'][0].update(quote)
                with self.assertRaises(ValueError):validate_dataset(data)

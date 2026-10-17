"""파일 역할: 수량 소유권·부분 체결·늦은 응답·뉴스 취소·중복 이벤트를 통합 검사합니다.
입력: 임시 DB와 가상 브로커. 출력: 실제 전송 수량·원장 상태·LCD 프레임 검증.
이유: 정상 주문뿐 아니라 계좌 혼재와 조회 대기 중 상태 변경을 실행 경로에 주입합니다.
"""
from concurrent.futures import Future
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
import time
from unittest import TestCase
import test_system as base
from stockauto.app import Runner
from stockauto.engine import Engine
from stockauto.models import OrderSnapshot, Submission
from stockauto.orders import OrderBook
from stockauto.replay import replay
from stockauto.store import Store
from stockauto.strategy import build_intent


class Device:
    """입력 큐와 송신 목록으로 실제 Runner 통신 경로를 검사하는 장치입니다."""
    def __init__(self):
        """입력: 없음. 출력: 연결된 빈 장치. 이유: 실제 USB와 계좌가 필요 없습니다."""
        self.ready = True; self.incoming = []; self.sent = []

    def messages(self):
        """입력: 내부 수신 큐. 출력: 한 번만 소비한 메시지 목록."""
        messages, self.incoming = self.incoming, []
        return messages

    def send(self, frame):
        """입력: PC bytes. 출력: 송신 기록. 이유: 취소·응답을 실제 프로토콜로 확인합니다."""
        self.sent.append(frame)

    def close(self):
        """입력·출력: 없음. 이유: Runner 종료 인터페이스를 제공합니다."""
        pass


class LifecycleTests(TestCase):
    def setUp(self):
        """입력: 없음. 출력: 임시 DB·고정 시계·엔진. 이유: 기존 통합 환경을 그대로 사용합니다."""
        base.SystemTests.setUp(self)

    def tearDown(self):
        """입력: 임시 자원. 출력: 없음. 이유: 검사마다 상태를 격리합니다."""
        base.SystemTests.tearDown(self)

    def buy(self, quantity=None):
        """입력: 선택적 체결 수량. 출력: 승인한 Offer. 이유: 실제 승인→브로커 체결 경로를 재사용합니다."""
        offer = self.engine.propose(self.candidate)
        self.engine.decide(offer.id, 'BUY')
        self.broker.fill_buy(offer.id, quantity)
        return offer

    def runner(self):
        """입력: 현재 엔진. 출력: 외부 수집을 예약하지 않는 Runner·장치. 이유: I/O 재진입만 검사합니다."""
        device = Device(); runner = Runner(self.settings, self.engine, device)
        runner.last_fetch = runner.last_risk = time.monotonic()
        return runner, device

    def correction(self, runner):
        """입력: Runner. 출력: 완료된 해지 기사 Future. 이유: 승인 이후 들어온 실제 분석 경로를 사용합니다."""
        runner.future = Future()
        runner.future.set_result(([replace(self.article, key='correction', title='삼성전자 공급계약 해지 공시')], []))

    def test_manual_same_stock_shares_are_never_added_to_exit(self):
        """프로그램 7주+수동 20주에서 자동 매도는 7주까지만 요청합니다."""
        offer = self.buy()
        actual = self.broker.inventory['005930']
        self.broker.inventory['005930'] = replace(actual, quantity=actual.quantity+20, available=actual.available+20)
        self.settings['paper_quotes']['005930']['bid'] = 65000
        self.engine.risk.tick()
        sells = [row for row in self.broker.orders if row['side'] == 'SELL']
        self.assertEqual(sells[0]['quantity'], offer.quantity)
        self.broker.fill_sell(sells[0]['id']); self.engine.risk.tick()
        self.assertEqual(self.broker.inventory['005930'].quantity, 20)
        self.assertNotIn('005930', self.store.managed_codes())

    def test_unknown_order_does_not_own_manual_inventory(self):
        """전송 미확정과 같은 종목의 수동 잔고가 있어도 체결 증거 없이 자동 매도하지 않습니다."""
        offer = self.engine.propose(self.candidate)
        self.broker.buy_limit = lambda *args: Submission('UNKNOWN', '응답 없음')
        self.engine.decide(offer.id, 'BUY')
        from stockauto.models import Position
        self.broker.inventory['005930'] = Position('005930', 20, 100000, 20, self.now)
        self.engine.risk.tick()
        self.assertFalse(self.broker.orders)
        self.assertFalse(self.store.managed_codes())

    def test_late_remote_id_recovers_unknown_and_can_cancel(self):
        """서버에는 접수됐지만 반환은 UNKNOWN인 주문도 늦은 원격 번호를 연결해 잔량 취소합니다."""
        original = self.broker.buy_limit
        def uncertain(*args):
            """입력: 실제 요청. 출력: UNKNOWN. 이유: 서버 접수 뒤 응답 유실을 재현합니다."""
            original(*args); return Submission('UNKNOWN', '응답 유실')
        self.broker.buy_limit = uncertain
        offer = self.engine.propose(self.candidate); self.engine.decide(offer.id, 'BUY')
        self.now += timedelta(seconds=121)
        self.assertTrue(any(row[0] == 'CANCEL' for row in self.engine.risk.tick()))
        self.assertEqual(self.store.db.execute('SELECT remote_id FROM jobs WHERE id=?', (offer.id,)).fetchone()[0], offer.id)
        self.assertEqual(len([row for row in self.broker.orders if row['side'] == 'BUY']), 1)

    def test_partial_cancel_preserves_exact_owned_quantity(self):
        """2주 체결 후 잔량 취소는 CANCELLED와 소유 2주를 각각 보존합니다."""
        offer = self.buy(2)
        self.broker.cancel_buy('005930', offer.id, offer.quantity-2, 'cancel')
        self.store.reconcile_jobs(self.broker, self.broker.account())
        self.assertEqual(self.store.state(offer.id), 'CANCELLED')
        self.assertEqual(self.store.managed_position(self.broker.positions()[0]).quantity, 2)

    def test_duplicate_snapshots_do_not_double_owned_quantity(self):
        """동일한 누계 체결 응답을 두 번 반영해도 소유 수량은 한 번만 증가합니다."""
        offer = self.buy(); self.engine.risk.tick(); self.engine.risk.tick()
        self.assertEqual(self.store.managed_position(self.broker.positions()[0]).quantity, offer.quantity)

    def test_older_snapshot_does_not_reduce_filled_quantity(self):
        """완료 이후 과거 부분 체결 응답이 와도 수량과 상태를 되돌리지 않습니다."""
        offer = self.buy(); self.engine.risk.tick()
        snapshot = self.broker.order_snapshot(offer.id, self.now)
        self.assertFalse(self.store.apply_snapshot(offer.id, replace(snapshot, filled_quantity=1, remaining_quantity=offer.quantity-1, state='PARTIALLY_FILLED')))
        self.assertEqual(self.store.state(offer.id), 'FILLED')

    def test_wrong_day_or_stock_does_not_attach_snapshot(self):
        """재사용된 번호·다른 종목의 snapshot은 원장에 반영하지 않습니다."""
        offer = self.buy(); self.store.link_order(offer.id, offer.id)
        snapshot = self.broker.order_snapshot(offer.id, self.now)
        for changed in (replace(snapshot, order_day='2026-10-15'), replace(snapshot, code='000660')):
            self.assertFalse(self.store.apply_snapshot(offer.id, changed))
        self.assertFalse(self.store.managed_codes())

    def test_restart_preserves_owned_quantities(self):
        """체결 후 재시작해도 소유 수량·원가를 보존합니다."""
        offer = self.buy(); self.engine.risk.tick()
        self.store.close(); self.store = Store(Path(self.directory.name)/'orders.db')
        self.assertEqual(self.store.managed_position(self.broker.positions()[0]).quantity, offer.quantity)

    def test_correction_revokes_lcd_offer(self):
        """새 해지 기사를 소비하면 이전 제안이 REJECTED가 되고 승인해도 주문하지 않습니다."""
        offer = self.engine.propose(self.candidate); runner, device = self.runner()
        try:
            self.correction(runner); runner.service_io()
            self.assertEqual(self.store.state(offer.id), 'REJECTED')
            self.assertIsNone(self.engine.active)
            self.assertIn(f'RESULT|{offer.id}|REJECTED\n'.encode(), device.sent)
            self.assertEqual(self.engine.decide(offer.id, 'BUY')[0], 'IGNORED')
        finally: runner.close()

    def test_correction_during_quote_prevents_order(self):
        """BUY 재조회 중 정정 기사가 들어오면 반환 후에도 이전 승인으로 전송하지 않습니다."""
        offer = self.engine.propose(self.candidate); runner, device = self.runner(); quote = self.broker.quote
        def delayed(code):
            """입력: 코드. 출력: 호가. 이유: TR 대기 중 실행되는 제어 타이머를 재현합니다."""
            self.correction(runner); runner.service_io(); return quote(code)
        self.broker.quote = delayed
        try:
            self.engine.decide(offer.id, 'BUY')
            self.assertFalse(self.broker.orders)
            self.assertEqual(self.store.state(offer.id), 'REJECTED')
        finally: runner.close()

    def test_no_button_during_query_is_immediate(self):
        """주문 루프 busy 중에도 NO는 즉시 DB·LCD에 반영하고 외부 주문하지 않습니다."""
        offer = self.engine.propose(self.candidate); runner, device = self.runner()
        try:
            runner.busy = True; device.incoming = [('DECIDE', offer.id, 'NO')]
            runner.service_io()
            self.assertEqual(self.store.state(offer.id), 'REJECTED')
            self.assertFalse(self.broker.orders)
        finally: runner.close()

    def test_reconnect_during_propose_discards_unpublished_offer(self):
        """호가 조회 중 READY가 오면 조회 전 연결의 제안을 새 장치에 발행하지 않습니다."""
        quote = self.broker.quote
        def reconnect(code):
            """입력: 코드. 출력: 호가. 이유: 조회 도중 연결 세대 변경을 재현합니다."""
            self.engine.disconnect(); return quote(code)
        self.broker.quote = reconnect
        with self.assertRaisesRegex(ValueError, '연결'):
            self.engine.propose(self.candidate)
        self.assertIsNone(self.engine.active)

    def test_negative_block_survives_restart(self):
        """새 악재 기록은 프로세스를 다시 실행해도 오래된 호재를 차단합니다."""
        negative = replace(self.candidate, score=-5, article=replace(self.article, published_at=self.now+timedelta(seconds=1)))
        self.engine.observe_news([negative])
        self.store.close(); self.store = Store(Path(self.directory.name)/'orders.db')
        self.assertTrue(self.store.news_blocked(self.candidate))

    def test_replay_uses_approved_quantity_at_cheaper_next_open(self):
        """다음 봉 가격이 크게 내려가도 승인 수량보다 더 매수하지 않습니다."""
        data = dict(synthetic=True, bars=[
            dict(at=self.now.isoformat(),code='005930',open=70000,close=70000,ask=70000,bid=70000,volume_ratio=2),
            dict(at=(self.now+timedelta(minutes=1)).isoformat(),code='005930',open=40000,close=40000,volume_ratio=2)],
            news=[dict(published_at=self.now.isoformat(),title=self.article.title)])
        result = replay(data, self.settings, 'news')
        self.assertEqual(result['fills'][0]['quantity'], result['signals'][0]['quantity'])
        intent = build_intent(self.candidate, self.broker.account(), self.broker.quote('005930'), self.settings, self.now, set())
        self.assertEqual(result['signals'][0]['quantity'], intent.quantity)

    def test_replay_correction_cancels_pending_buy(self):
        """다음 봉 전에 들어온 계약 해지 기사는 기존 매수 신호의 체결을 취소합니다."""
        data = dict(synthetic=True, bars=[
            dict(at=self.now.isoformat(),code='005930',open=70000,close=70000,volume_ratio=2),
            dict(at=(self.now+timedelta(minutes=1)).isoformat(),code='005930',open=69000,close=69000,volume_ratio=2)],
            news=[dict(published_at=self.now.isoformat(),title=self.article.title),
                  dict(published_at=(self.now+timedelta(seconds=30)).isoformat(),title='삼성전자 공급계약 해지 공시')])
        self.assertEqual(replay(data,self.settings,'news')['fills'], [])


class OrderBookTests(TestCase):
    def event(self, **changes):
        """입력: 필드 변경. 출력: 정상 부분 체결 통보. 이유: 실제 어댑터와 같은 이벤트 계약을 사용합니다."""
        event = dict(kind='CHEJAN',remote_id='123',order_day='2026-10-16',code='005930',side='BUY',status='체결',
                     ordered=7,remaining=5,execution_id='e1',fill_quantity=2,fill_price=70000,filled_value=140000)
        return dict(event, **changes)

    def test_duplicate_execution_and_partial_completion(self):
        """같은 체결번호 중복은 누계 원가에 더해지지 않고 다음 체결로 전량 완료됩니다."""
        book=OrderBook(); event=self.event();book.observe(event);book.observe(event)
        self.assertEqual(book.snapshot('123','2026-10-16').filled_quantity,2)
        book.observe(self.event(remaining=0,execution_id='e2',fill_quantity=5,fill_price=69000,filled_value=485000))
        snap=book.snapshot('123','2026-10-16')
        self.assertEqual(snap.state,'FILLED');self.assertAlmostEqual(snap.average_price,485000/7)

    def test_cancel_does_not_turn_remaining_into_filled(self):
        """취소 확인은 2주 체결을 유지하고 원주문 잔량만 0으로 바꿉니다."""
        book=OrderBook();book.observe(self.event())
        book.observe(self.event(remote_id='124',side='CANCEL',status='확인',original_id='123',remaining=0,fill_quantity=0,execution_id=''))
        snap=book.snapshot('123','2026-10-16')
        self.assertEqual((snap.state,snap.filled_quantity,snap.remaining_quantity),('CANCELLED',2,0))

    def test_replay_of_persisted_events_restores_average(self):
        """원문 재적용으로 주문번호 연결과 누계 원가를 같은 값으로 복구합니다."""
        events=[dict(kind='ORDER_LINK',request_id='local',remote_id='123'),self.event()]
        book=OrderBook()
        for event in events:book.observe(event)
        self.assertEqual(book.links['local'],'123')
        self.assertEqual(book.snapshot('123','2026-10-16').average_price,70000)
        self.assertIsNone(book.snapshot('123','2026-10-17'))


class AdapterTests(TestCase):
    def setUp(self):
        """입력: 없음. 출력: 실제 KiwoomBroker 메서드를 쓰는 가상 ActiveX. 이유: FID 해석 경로를 검사합니다."""
        from stockauto.kiwoom import KiwoomBroker
        from datetime import datetime
        from stockauto.market import KST
        self.broker=object.__new__(KiwoomBroker)
        self.broker.book=OrderBook();self.broker.remote_orders=self.broker.book.links
        self.broker.account_number='TEST';self.broker.healthy=True;self.broker.messages=[]
        self.broker.clock=lambda:datetime(2026,10,16,10,tzinfo=KST)
        self.events=[];self.broker.event_sink=self.events.append;self.broker.unpersisted_events=[]
        self.fields={9201:'TEST',9203:'000123',9001:'A005930',905:'+매수',913:'체결',900:'7',902:'5',909:'e1',910:'70000',911:'2',914:'70000',915:'2',903:'140000'}
        class Ax:
            """GetChejanData와 주문 접수 TR 필드만 제공하는 검사 대체 객체입니다."""
            def __init__(inner,fields):
                """입력: FID 맵. 출력: 대체 ActiveX. 이유: Qt/증권사 없이 원래 콜백을 호출합니다."""
                inner.fields=fields
            def dynamicCall(inner,signature,*args):
                """입력: 원래 COM 서명·인자. 출력: FID 또는 주문번호. 이유: 호출 오타도 검사합니다."""
                if signature=='GetChejanData(int)':return inner.fields.get(args[0],'')
                if signature=='GetCommData(QString, QString, int, QString)':return '000123'
                raise AssertionError(signature)
        self.broker.ax=Ax(self.fields)

    def test_real_callback_uses_cumulative_fill_value(self):
        """실제 콜백은 누계 2주·14만원을 평균 7만원으로 정규화하고 계좌번호를 기록하지 않습니다."""
        self.broker._chejan_event('0',0,'')
        snap=self.broker.order_snapshot('123',self.broker.clock())
        self.assertEqual((snap.filled_quantity,snap.average_price),(2,70000))
        self.assertNotIn('TEST',str(self.events))
        self.assertTrue(self.broker.healthy)

    def test_foreign_account_and_balance_notifications_are_ignored(self):
        """다른 계좌 또는 잔고 통보는 프로그램의 매수 체결로 간주하지 않습니다."""
        self.fields[9201]='OTHER';self.broker._chejan_event('0',0,'')
        self.fields[9201]='TEST';self.broker._chejan_event('1',0,'')
        self.assertFalse(self.events)

    def test_request_reply_links_local_and_remote_id_immediately(self):
        """접수 TR 응답은 로컬 ID와 원격 번호의 연결 이벤트를 즉시 전달합니다."""
        self.broker._tr_event('2002','Nlocal-request','order','',0)
        self.assertEqual(self.events,[dict(kind='ORDER_LINK',request_id='local-request',remote_id='123')])

    def test_callback_storage_failure_stops_session(self):
        """체결 기록 실패가 Qt 콜백 밖으로 전파되지 않고 이후 주문 세션을 중단합니다."""
        def fail(event):
            """입력: 이벤트. 출력: 저장 오류. 이유: 기록할 수 없는 통보를 주입합니다."""
            raise OSError('test storage unavailable')
        self.broker.event_sink=fail
        self.broker._chejan_event('0',0,'')
        self.assertFalse(self.broker.healthy)

    def test_polling_partial_cancel_retains_exact_fill_count(self):
        """당일 조회의 2주 체결·잔량 취소는 CANCELLED·2주·잔량 0으로 해석합니다."""
        rows=[{'주문번호':'123','종목코드':'005930','주문구분':'+매수','주문상태':'체결','주문수량':'7','체결량':'2','미체결수량':'5'},
              {'주문번호':'124','원주문번호':'123','종목코드':'005930','주문구분':'매수취소','주문상태':'확인','주문수량':'5','체결량':'0','미체결수량':'0'}]
        self.broker._cache_order_rows(rows)
        snap=self.broker.order_snapshot('123',self.broker.clock())
        self.assertEqual((snap.state,snap.filled_quantity,snap.remaining_quantity),('CANCELLED',2,0))

    def test_polling_uses_total_fill_instead_of_last_execution(self):
        """마지막 단위 체결 1주와 실제 누계 2주를 혼동하지 않고 누계 금액으로 평균가를 복원합니다."""
        self.broker._cache_order_rows([{'주문번호':'123','종목코드':'005930','주문구분':'+매수',
            '주문상태':'체결','주문수량':'7','미체결수량':'5','체결량':'1','체결누계금액':'140000'}])
        snap=self.broker.order_snapshot('123',self.broker.clock())
        self.assertEqual((snap.filled_quantity,snap.average_price),(2,70000))


class ContextTests(TestCase):
    def test_paper_ledger_cannot_be_bound_to_live_account(self):
        """모의 소유 원장은 실제 계좌 컨텍스트에 재사용하지 않습니다."""
        store=Store(':memory:')
        try:
            store.bind_context('paper')
            with self.assertRaises(ValueError):store.bind_context('kiwoom:account-hash')
        finally:store.close()

    def test_one_live_account_cannot_use_another_account_ledger(self):
        """계좌가 바뀌면 같은 DB를 조용히 사용하지 않고 시작 전에 거부합니다."""
        store=Store(':memory:')
        try:
            store.bind_context('kiwoom:one')
            with self.assertRaises(ValueError):store.bind_context('kiwoom:two')
        finally:store.close()

    def test_news_block_cannot_be_overwritten_by_older_bad_news(self):
        """역순 수집된 과거 악재가 최신 차단 시각을 되돌리지 않습니다."""
        fixture=base.SystemTests(methodName='runTest');fixture.setUp()
        try:
            new=replace(fixture.candidate,score=-5,article=replace(fixture.article,published_at=fixture.now+timedelta(minutes=1)))
            old=replace(fixture.candidate,score=-5,article=replace(fixture.article,published_at=fixture.now-timedelta(minutes=1)))
            fixture.store.observe_news(new);fixture.store.observe_news(old)
            self.assertTrue(fixture.store.news_blocked(fixture.candidate))
        finally:fixture.tearDown()


class FollowupLifecycleTests(TestCase):
    def setUp(self):
        """입력: 없음. 출력: 격리된 계좌·DB. 이유: 추가 경계 검사를 기존 fixture로 실행합니다."""
        base.SystemTests.setUp(self)

    def tearDown(self):
        """입력: 임시 자원. 출력: 없음. 이유: 다음 검사에 영향을 주지 않습니다."""
        base.SystemTests.tearDown(self)

    def test_new_bad_news_cancels_confirmed_pending_buy(self):
        """승인 후 전송한 매수도 새 악재가 오면 120초를 기다리지 않고 확인된 잔량을 취소합니다."""
        offer=self.engine.propose(self.candidate);self.engine.decide(offer.id,'BUY')
        self.engine.observe_news([replace(self.candidate,score=-5,article=replace(self.article,published_at=self.now+timedelta(seconds=1)))])
        results=self.engine.risk.tick()
        self.assertTrue(any(result[0]=='CANCEL' for result in results))
        self.assertFalse(self.broker.pending)

    def test_later_trade_starts_new_holding_period(self):
        """전량 청산 뒤 새 매수는 이전 거래의 보유 시작일을 이어받지 않습니다."""
        offer=self.engine.propose(self.candidate);self.engine.decide(offer.id,'BUY');self.broker.fill_buy(offer.id)
        self.engine.risk.tick()
        self.settings['paper_quotes']['005930']['bid']=65000;self.engine.risk.tick()
        sell=next(row for row in self.broker.orders if row['side']=='SELL')
        self.broker.fill_sell(sell['id']);self.engine.risk.tick()
        self.now+=timedelta(days=7)
        self.settings['paper_quotes']['005930']['bid']=70000
        candidate=replace(self.candidate,key='new-news',event_group='',article=replace(self.article,key='new-article',published_at=self.now))
        offer=self.engine.propose(candidate);self.engine.decide(offer.id,'BUY');self.broker.fill_buy(offer.id)
        self.engine.risk.tick()
        self.assertEqual(self.store.managed_position(self.broker.positions()[0]).opened_at,self.now)


class RestartTests(TestCase):
    def test_full_paper_restart_preserves_cash_pending_and_positions(self):
        """DB와 브로커를 함께 재생성해도 현금·예약금·잔고·미체결을 같은 값으로 복구합니다."""
        from stockauto.broker import PaperBroker
        fixture=base.SystemTests(methodName='runTest');fixture.setUp()
        try:
            offer=fixture.engine.propose(fixture.candidate);fixture.engine.decide(offer.id,'BUY')
            fixture.broker.fill_buy(offer.id,2)
            cash,reserved=fixture.broker.cash,fixture.broker.reserved
            fixture.store.close();fixture.store=Store(Path(fixture.directory.name)/'orders.db')
            broker=PaperBroker(fixture.settings,fixture.clock)
            engine=Engine(broker,fixture.store,fixture.settings,fixture.clock)
            engine.risk.tick()
            self.assertEqual((broker.cash,broker.reserved),(cash,reserved))
            self.assertEqual(broker.positions()[0].quantity,2)
            self.assertEqual(broker.pending_orders()[0]['remaining'],offer.quantity-2)
            self.assertEqual(fixture.store.managed_position(broker.positions()[0]).quantity,2)
        finally:fixture.tearDown()


class ReplayExecutionTests(TestCase):
    def test_sell_does_not_fill_below_submitted_limit(self):
        """손절 신호 후 다음 봉이 매도 지정가보다 낮으면 실제로 체결된 것처럼 계산하지 않습니다."""
        fixture=base.SystemTests(methodName='runTest');fixture.setUp()
        try:
            data=dict(synthetic=True,bars=[
                dict(at=fixture.now.isoformat(),code='005930',open=70000,close=70000,volume_ratio=2),
                dict(at=(fixture.now+timedelta(minutes=1)).isoformat(),code='005930',open=69000,close=60000,volume_ratio=2),
                dict(at=(fixture.now+timedelta(minutes=2)).isoformat(),code='005930',open=50000,close=50000,volume_ratio=2)],
                news=[dict(published_at=fixture.now.isoformat(),title=fixture.article.title)])
            result=replay(data,fixture.settings,'news')
            self.assertEqual([fill['side'] for fill in result['fills']],['BUY'])
            self.assertEqual(result['final_positions'],1)
        finally:fixture.tearDown()

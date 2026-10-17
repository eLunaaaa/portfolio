"""파일 역할: 문맥·사건 통합·시장 필터·청산·재생 비교의 회귀 검사.
입력: 가상 뉴스와 가격. 출력: assert 결과. 이유: 실제 계좌 없이 개선 기준을 독립 검증합니다.
"""
from dataclasses import replace
from datetime import timedelta
import json
from pathlib import Path
from unittest import TestCase
import test_system as base
from stockauto.models import Article, Position, Quote, Account
from stockauto.news import analyze, contract_amount, title_events
from stockauto.risk import entry_reason, exit_reason
from stockauto.replay import replay
from stockauto.app import load_settings


class ImprovementTests(TestCase):
    """기존 공통 환경과 회귀 검사를 포함해 새 판단 조건을 검사합니다."""
    def setUp(self):
        """기능: 공통 격리 환경 준비. 입력: 없음. 출력: 테스트 자원. 구조·이유: 기존 검사 환경을 재사용합니다."""
        base.SystemTests.setUp(self)

    def tearDown(self):
        """기능: 공통 자원 정리. 입력·출력 없음. 구조·이유: 테스트 간 DB를 분리합니다."""
        base.SystemTests.tearDown(self)

    def test_record_loss_is_not_positive(self):
        """입력: 사상 최대 적자. 결과: 호재 점수 없음."""
        candidate=analyze([replace(self.article,title='삼성전자 사상 최대 적자')],self.settings['watchlist'])[0]
        self.assertLess(candidate.score,0)

    def test_unconfirmed_news_is_review_only(self):
        """입력: 수주 전망·검토·부인. 결과: 알림 유지, 매수 제안 없음."""
        for title in ('삼성전자 수주 전망','삼성전자 공급계약 검토','삼성전자 수주 사실무근 부인'):
            candidate=analyze([replace(self.article,title=title)],self.settings['watchlist'])[0]
            self.assertFalse(candidate.confirmed)
            self.assertIsNone(self.engine.propose(candidate))

    def test_maximum_alone_is_not_earnings(self):
        """입력: 사상 최대라는 단어만 있음. 결과: 매수 사건으로 해석하지 않음."""
        self.assertFalse(title_events('삼성전자 사상 최대 관심')[0])

    def test_duplicate_event_survives_restart(self):
        """입력: 제목이 다른 같은 공급계약. 결과: 같은 사건 ID·최초 시각 유지."""
        one=analyze([replace(self.article,title='삼성전자 1000억원 공급계약 체결')],self.settings['watchlist'])[0]
        two=analyze([replace(self.article,title='[종합] 삼성전자 1000억원 수주 확정',published_at=self.now+timedelta(hours=2))],self.settings['watchlist'])[0]
        a=self.engine.normalize(one);b=self.engine.normalize(two)
        self.assertEqual(a.key,b.key)
        self.assertEqual(b.first_report_at,self.article.published_at)
        from stockauto.store import Store
        self.store.close();self.store=Store(Path(self.directory.name)/'orders.db');self.engine.store=self.store
        self.assertEqual(self.engine.normalize(two).key,a.key)

    def test_different_amount_is_different_event(self):
        """입력: 금액이 다른 계약. 결과: 별도 사건으로 보존."""
        one=analyze([replace(self.article,title='삼성전자 1000억원 수주')],self.settings['watchlist'])[0]
        two=analyze([replace(self.article,title='삼성전자 3000억원 수주')],self.settings['watchlist'])[0]
        self.assertNotEqual(self.engine.normalize(one).key,self.engine.normalize(two).key)

    def test_contract_materiality(self):
        """입력: 계약금액과 연매출. 결과: 단위 변환·비율 계산·자료 누락 시 주문 제외."""
        self.assertEqual(contract_amount('1조 2000억원 공급계약'),1200000000000)
        stock=dict(self.settings['watchlist'][0],annual_revenue_won=10000000000000)
        candidate=analyze([replace(self.article,title='삼성전자 1000억원 수주')],[stock])[0]
        self.assertAlmostEqual(candidate.contract_revenue_pct,1.0)
        no_amount=analyze([replace(self.article,title='삼성전자 공급계약 수주')],self.settings['watchlist'])[0]
        with self.assertRaises(ValueError):self.engine.propose(no_amount)

    def test_spread_volume_and_rise_filters(self):
        """입력: 넓은 스프레드·거래량 부족·급등·뉴스 이후 상승. 결과: 각각 다른 제외 사유."""
        quote=self.broker.quote('005930')
        self.assertEqual(entry_reason(quote,self.settings,quote.ask),'')
        self.assertIn('스프레드',entry_reason(replace(quote,bid=60000),self.settings))
        self.assertIn('거래량',entry_reason(replace(quote,volume_ratio=.2),self.settings))
        self.assertIn('당일',entry_reason(replace(quote,last=80000),self.settings))
        self.assertIn('뉴스',entry_reason(quote,self.settings,60000))

    def test_approval_rechecks_volume(self):
        """입력: 제안 이후 거래량 조건 악화. 결과: 버튼 승인해도 주문 없음."""
        offer=self.engine.propose(self.candidate)
        self.settings['paper_quotes']['005930']['volume_ratio']=.1
        self.assertEqual(self.engine.decide(offer.id,'BUY')[0],'FAILED')
        self.assertFalse(self.broker.orders)

    def test_stop_profit_and_holding_period(self):
        """입력: 매입가 대비 손실/수익/장기보유. 결과: 올바른 매도 사유."""
        position=Position('005930',1,70000,1,self.now)
        quote=self.broker.quote('005930')
        self.assertEqual(exit_reason(position,replace(quote,bid=67000),self.settings,self.now,self.now),'STOP_LOSS')
        self.assertEqual(exit_reason(position,replace(quote,bid=75000),self.settings,self.now,self.now),'TAKE_PROFIT')
        self.assertEqual(exit_reason(position,quote,self.settings,self.now+timedelta(days=6),self.now),'HOLDING_PERIOD')

    def test_daily_loss_latches_and_next_day_resets(self):
        """입력: 일일 손실 한도 도달 후 회복·익일. 결과: 당일 유지, 다음날 재설정."""
        self.assertEqual(self.engine.risk.buy_reason(Account(0,frozenset(),frozenset(),1000000)),'')
        self.assertTrue(self.engine.risk.buy_reason(Account(0,frozenset(),frozenset(),970000)))
        self.assertTrue(self.engine.risk.buy_reason(Account(0,frozenset(),frozenset(),1000000)))
        self.now+=timedelta(days=1)
        self.assertEqual(self.engine.risk.buy_reason(Account(0,frozenset(),frozenset(),1000000)),'')

    def test_timeout_cancels_only_owned_order(self):
        """입력: 확인된 본인 매수 미체결 121초. 결과: 취소 1회, 중복 취소 없음."""
        offer=self.engine.propose(self.candidate);self.engine.decide(offer.id,'BUY')
        self.now+=timedelta(seconds=121)
        result=self.engine.risk.tick()
        self.assertTrue(any(r[0]=='CANCEL' for r in result))
        self.assertFalse(self.broker.pending)
        self.assertFalse(any(r[0]=='CANCEL' for r in self.engine.risk.tick()))

    def test_auto_exit_once_and_reconcile(self):
        """입력: 프로그램의 매수 체결 후 손절. 결과: 실제 가능 수량으로 매도 한 번, 체결 뒤 원장 정리."""
        offer=self.engine.propose(self.candidate);self.engine.decide(offer.id,'BUY');self.broker.fill_buy(offer.id)
        self.settings['paper_quotes']['005930']['bid']=67000
        result=self.engine.risk.tick()
        self.assertTrue(any(r[0]=='STOP_LOSS' for r in result))
        sells=[o for o in self.broker.orders if o['side']=='SELL']
        self.assertEqual(len(sells),1)
        self.engine.risk.tick();self.assertEqual(len([o for o in self.broker.orders if o['side']=='SELL']),1)
        self.broker.fill_sell(sells[0]['id']);self.engine.risk.tick()
        self.assertFalse(self.store.has_exit('005930'))

    def test_unrelated_positions_are_not_sold(self):
        """입력: 다른 프로그램의 기존 보유종목. 결과: 기본 자동 청산 대상에서 제외."""
        self.broker.inventory['005930']=Position('005930',1,100000,1,self.now)
        self.engine.risk.tick();self.assertFalse(self.broker.orders)

    def test_market_filters_cannot_be_bypassed_by_rsi(self):
        """입력: RSI 모드의 넓은 스프레드. 결과: 시장 필터도 함께 검사."""
        self.settings['strategy_mode']='rsi'
        quote=self.broker.quote('005930')
        self.assertTrue(entry_reason(replace(quote,bid=1),self.settings))

    def test_serial_frames_have_real_linefeed(self):
        """입력: 실제 PC 프레임. 결과: 역슬래시+n 문자열이 아니라 LF 바이트로 종료."""
        from stockauto.protocol import offer_frame,news_frame
        offer=self.engine.propose(self.candidate)
        for frame in (offer_frame(offer,5,self.now),news_frame(self.candidate,5)):
            self.assertTrue(frame.endswith(b'\n'));self.assertNotIn(b'\\n',frame)

    def test_multiple_company_clauses(self):
        """입력: A사 악재와 B사 호재가 다른 절에 있음. 결과: 사건 방향을 기업별로 구분."""
        article=replace(self.article,title='삼성전자 적자전환, SK하이닉스 공급계약 수주')
        rows={c.code:c for c in analyze([article],self.settings['watchlist'])}
        self.assertLess(rows['005930'].score,0)
        self.assertGreater(rows['000660'].score,0)

    def test_multiple_companies_in_one_clause_are_reviewed(self):
        """입력: 한 절에 여러 기업. 결과: 관계를 임의 추론하여 매수하지 않음."""
        article=replace(self.article,title='삼성전자·SK하이닉스 공급계약 수주')
        self.assertTrue(all(not c.confirmed for c in analyze([article],self.settings['watchlist'])))

    def test_replay_excludes_future_news(self):
        """입력: 미래에 게시할 기사. 결과: 과거 봉에서 매수하지 않음."""
        data={'synthetic':True,'bars':[dict(at=self.now.isoformat(),code='005930',open=70000,close=70000,volume_ratio=2)],'news':[dict(published_at=(self.now+timedelta(days=1)).isoformat(),title=self.article.title)]}
        self.assertEqual(replay(data,self.settings,'news')['fills'],[])

    def test_replay_uses_next_open_and_limit(self):
        """입력: 신호 뒤 시가 급등. 결과: 종가로 즉시 매수하지 않고 지정가 초과분은 미체결."""
        data={'synthetic':True,'bars':[dict(at=self.now.isoformat(),code='005930',open=70000,close=70000,volume_ratio=2),dict(at=(self.now+timedelta(minutes=5)).isoformat(),code='005930',open=90000,close=90000,volume_ratio=2)],'news':[dict(published_at=self.now.isoformat(),title=self.article.title)]}
        self.assertEqual(replay(data,self.settings,'news')['fills'],[])
        data['bars'][1].update(open=69000,close=70000)
        result=replay(data,self.settings,'news')
        self.assertEqual(result['fills'][0]['at'],data['bars'][1]['at'])
        self.assertGreater(result['fills'][0]['price'],69000)

    def test_invalid_risk_settings_rejected(self):
        """입력: 음수 손절 기준. 결과: 시작 전 설정 오류."""
        settings=dict(self.settings,stop_loss_pct=-3)
        path=Path(self.directory.name)/'settings.json';path.write_text(json.dumps(settings))
        with self.assertRaises(ValueError):load_settings(path)

    def test_unidentified_remote_order_is_not_cancelled(self):
        """입력: 원격 주문번호가 없는 미체결. 결과: 종목만 보고 취소하지 않음."""
        offer=self.engine.propose(self.candidate);self.engine.decide(offer.id,'BUY')
        self.broker.order_id=lambda request_id:''
        self.now+=timedelta(seconds=121)
        self.assertFalse(any(row[0]=='CANCEL' for row in self.engine.risk.tick()))
        self.assertIn('005930',self.broker.pending)

    def test_missing_equity_does_not_force_daily_exit(self):
        """입력: 평가자산 누락. 결과: 신규 매수는 중단하지만 일일 손실로 전체 매도하지 않음."""
        offer=self.engine.propose(self.candidate);self.engine.decide(offer.id,'BUY');self.broker.fill_buy(offer.id)
        self.broker.account=lambda:Account(0,frozenset({'005930'}),frozenset(),0)
        self.assertFalse(any(row[0]=='DAILY_LOSS' for row in self.engine.risk.tick()))

    def test_partial_sell_preserves_remaining_quantity(self):
        """입력: 보유 7주 중 매도 가능 3주 체결. 결과: 나머지 4주 보존."""
        self.broker.inventory['005930']=Position('005930',7,70000,3,self.now)
        self.broker.sell_limit('005930',3,70000,'sell-test');self.broker.fill_sell('sell-test')
        self.assertEqual(self.broker.positions()[0].quantity,4)

    def test_partial_buy_cancel_remains_managed(self):
        """입력: 매수 일부 체결 후 잔량 취소. 결과: 체결된 실제 잔고는 계속 관리."""
        offer=self.engine.propose(self.candidate);self.engine.decide(offer.id,'BUY')
        self.broker.fill_buy(offer.id, quantity=2)
        self.broker.cancel_buy('005930',offer.id,offer.quantity-2,'cancel-partial')
        self.store.link_order(offer.id,offer.id)
        self.store.reconcile_jobs(self.broker,self.broker.account())
        self.assertEqual(self.store.managed_position(self.broker.positions()[0]).quantity,2)
        self.assertIn('005930',self.store.managed_codes())

    def test_live_exit_requires_exchange_time(self):
        """입력: PC 수신은 최신이지만 거래소 시각 누락/오래됨. 결과: 청산 시세 거부."""
        from stockauto.risk import fresh_exit_quote
        quote=self.broker.quote('005930');settings=dict(self.settings,mode='kiwoom')
        self.assertTrue(fresh_exit_quote(quote,settings,self.now))
        self.assertFalse(fresh_exit_quote(replace(quote,market_at=None),settings,self.now))
        self.assertFalse(fresh_exit_quote(replace(quote,market_at=self.now-timedelta(seconds=11)),settings,self.now))

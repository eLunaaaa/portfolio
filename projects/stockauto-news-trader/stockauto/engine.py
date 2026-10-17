"""파일 역할: 뉴스 후보를 주문 제안으로 바꾸고 물리 버튼 결정을 처리합니다.
구조: 제안→대기→거절/만료 또는 재검사→전송 중 기록→증권사 결과 기록.
이유: 뉴스 수집과 버튼 입력이 주문 함수를 직접 호출하지 못하도록 경계를 둡니다.
"""
from datetime import timedelta
from dataclasses import replace
import uuid
from .market import can_buy, estimated_cost, order_quantity, technical_buy
from .models import Offer, Account
from .risk import RiskManager, entry_reason


class Engine:
    """브로커·DB를 주입받는 주문 승인 상태 머신입니다."""
    def __init__(self, broker, store, settings, clock):
        """기능: 실행 의존성 구성. 입력: 브로커·DB·설정·시계. 출력: Engine.
        구조·이유: 실제 증권사 대신 테스트 브로커로 같은 주문 경로를 검사할 수 있습니다.
        """
        self.broker, self.store, self.settings, self.clock = broker, store, settings, clock
        self.active = None
        self.risk = RiskManager(broker,store,settings,clock)

    def _checked(self, code, now):
        """기능: 주문 전 공통 조건 확인. 입력: 종목·현재시각. 출력: 계좌·시세.
        구조·이유: 매번 잔고/미체결과 시세를 새로 읽고 시간·보유 수·신선도를 검사합니다.
        """
        if not self.settings.get('demo', False) and not can_buy(now, self.settings.get('holidays', [])):
            raise ValueError('매수 허용 시간이 아닙니다')
        if code in self.store.locked_codes(): raise ValueError('기존 주문 결과 확인이 필요한 종목입니다')
        account = self.broker.account()
        unreflected = sum(estimated_cost(qty,price,self.settings['buy_fee_rate'])
            for reserved_code,qty,price in self.store.reservations()
            if reserved_code not in account.pending and reserved_code not in account.holdings)
        account = Account(max(0,account.cash-unreflected),account.holdings,account.pending,account.equity)
        risk_reason=self.risk.buy_reason(account)
        if risk_reason:raise ValueError(risk_reason)
        if code in account.holdings or code in account.pending: raise ValueError('이미 보유 중이거나 미체결 주문이 있습니다')
        occupied = account.holdings | account.pending | self.store.locked_codes()
        if len(occupied) >= self.settings['max_positions']: raise ValueError('최대 보유 종목 수에 도달했습니다')
        quote = self.broker.quote(code)
        now = self.clock()
        if not self.settings.get('demo', False) and not can_buy(now,self.settings.get('holidays', [])):
            raise ValueError('조회 중 매수 시간이 종료되었습니다')
        age = (now - quote.received_at).total_seconds()
        if not 0 < quote.ask <= 2000000000 or not -2 <= age <= self.settings['quote_max_age_seconds']:
            raise ValueError('유효한 최신 호가가 없습니다')
        if self.settings.get('market_filters',False) and self.settings.get('mode')=='kiwoom':
            if quote.market_at is None or not -2<=(now-quote.market_at).total_seconds()<=self.settings['quote_max_age_seconds']:
                raise ValueError('거래소 호가 시각이 오래되었거나 누락되었습니다')
        if (self.settings.get('require_rsi', False) or self.settings.get('strategy_mode') in ('rsi','combined')) and not technical_buy([*quote.closes, quote.ask]):
            raise ValueError('기존 RSI 매수 조건을 만족하지 않습니다')
        return account, quote

    def normalize(self,candidate):
        """기능: 후보에 대표 사건과 최초 시각 부여. 입력: Candidate. 출력: 정규화 Candidate.
        구조·이유: 언론사/제목이 달라도 같은 사건은 재시작 후 중복 승인하지 않습니다.
        """
        key,first=self.store.observe_event(candidate,self.clock())
        return replace(candidate,key=key,first_report_at=first)

    def _market_check(self,candidate,quote):
        """기능: 뉴스/시장 필터 재검사. 입력: 후보·호가. 출력: 없음 또는 예외.
        구조·이유: 가격/거래량 조건은 승인 직전에도 다시 적용합니다.
        """
        if self.settings.get('strategy_mode','news')!='rsi' and not candidate.confirmed:
            raise ValueError('미확정 뉴스는 알림만 표시합니다')
        first_at=candidate.first_report_at or candidate.article.published_at
        row=self.store.db.execute('SELECT first_at FROM events WHERE id=?',(candidate.key,)).fetchone()
        if row:
            from datetime import datetime
            first_at=datetime.fromisoformat(row[0])
        if (self.clock()-first_at).total_seconds()>self.settings['news_max_age_hours']*3600:
            raise ValueError('동일 사건의 최초 보도가 오래되었습니다')
        if self.settings.get('require_contract_materiality',False) and 'NEW CONTRACT' in candidate.reason and 'EARNINGS UP' not in candidate.reason:
            if candidate.contract_revenue_pct is None or candidate.contract_revenue_pct<self.settings['min_contract_revenue_pct']:
                raise ValueError('계약 금액/연매출 대비 규모의 확인 기준 미달')
        first=self.store.event_price(candidate.key,quote.ask)
        reason=entry_reason(quote,self.settings,first)
        if reason:raise ValueError(reason)

    def propose(self, candidate):
        """기능: 후보를 LCD 승인 제안으로 고정. 입력: Candidate. 출력: Offer 또는 None.
        구조·이유: 악재·기처리 뉴스는 제외하고 수수료 포함 예산으로 수량을 정해 영구 저장합니다.
        """
        self.expire()
        candidate=self.normalize(candidate)
        if self.active or not candidate.confirmed or candidate.score < self.settings['min_news_score'] or self.store.seen(candidate.key): return None
        now = self.clock()
        if not -300 <= (now-(candidate.first_report_at or candidate.article.published_at)).total_seconds() <= self.settings['news_max_age_hours']*3600:
            return None
        account, quote = self._checked(candidate.code, now)
        self._market_check(candidate,quote)
        slots = self.settings['max_positions'] - len(account.holdings | account.pending | self.store.locked_codes())
        budget = min(self.settings['max_order_won'], account.cash // slots)
        qty = order_quantity(account.cash, budget, quote.ask, self.settings['buy_fee_rate'])
        qty = min(qty,1000000)
        if qty < 1: return None
        now = self.clock()
        offer = Offer(uuid.uuid4().hex[:16],candidate,qty,quote.ask,now+timedelta(seconds=self.settings['approval_seconds']))
        if not self.store.add(offer): return None
        self.active = offer
        return offer

    def expire(self):
        """기능: 시간 지난 제안 종료. 입력: 없음. 출력: 만료 ID 또는 None.
        구조·이유: LCD가 오래 켜져 있어도 새 주문 권한으로 사용되지 않게 합니다.
        """
        if self.active and self.clock() >= self.active.expires_at:
            offer_id = self.active.id
            self.store.transition(offer_id,'OFFERED','EXPIRED','승인 시간 초과')
            self.active = None
            return offer_id
        return None

    def disconnect(self):
        """기능: 장치 연결 끊김 처리. 입력: 없음. 출력: 없음.
        구조·이유: 연결 전의 화면/버튼을 다시 승인하지 않도록 현재 제안을 만료시킵니다.
        """
        if self.active:
            self.store.transition(self.active.id,'OFFERED','EXPIRED','Arduino 연결 초기화')
            self.active = None

    def decide(self, offer_id, decision):
        """기능: BUY/NO 버튼 처리. 입력: 현재 제안 ID·결정. 출력: (상태, 설명).
        구조·이유: ID/시간 검증→재조회→승인한 가격·수량 검사→전송 전 기록→결과 저장.
        버튼은 주문 전송을 승인하며, 체결 완료는 별도 증권사 응답입니다.
        """
        self.expire()
        offer = self.active
        if not offer or offer.id != offer_id: return 'IGNORED','현재 제안과 일치하지 않습니다'
        if decision not in ('BUY','NO'): return 'IGNORED','알 수 없는 버튼 입력'
        if decision == 'NO':
            self.store.transition(offer.id,'OFFERED','REJECTED','사용자 구매 X')
            self.active = None
            return 'REJECTED','구매하지 않습니다'
        try:
            account, quote = self._checked(offer.candidate.code,self.clock())
            self._market_check(offer.candidate,quote)
            if self.clock() >= offer.expires_at: raise ValueError('재조회 중 승인 시간이 만료되었습니다')
            if quote.ask > offer.price: raise ValueError('호가가 승인 화면의 가격보다 높아졌습니다')
            cost = estimated_cost(offer.quantity, offer.price, self.settings['buy_fee_rate'])
            if cost > min(account.cash,self.settings['max_order_won']): raise ValueError('수수료 포함 주문액이 예산을 초과했습니다')
        except Exception as error:
            self.store.transition(offer.id,'OFFERED','FAILED',str(error))
            self.active = None
            return 'FAILED',str(error)
        # 외부 API 호출 전 승인 상태와 매수 원장을 함께 commit합니다.
        # 로컬 DB 실패는 외부 전송 실패와 다르므로 UNKNOWN으로 기록하지 않습니다.
        try:
            if not self.store.start_buy(offer, self.clock()):
                return 'IGNORED', '이미 처리한 제안'
        except Exception as error:
            self.store.transition(offer.id, 'OFFERED', 'FAILED', '주문 원장 저장 실패: ' + str(error))
            self.active = None
            return 'FAILED', '주문 원장 저장 실패: 주문을 전송하지 않았습니다'
        self.active = None
        try:
            result = self.broker.buy_limit(offer.candidate.code,offer.quantity,offer.price,offer.id)
            if result.state not in ('ACCEPTED','FAILED','UNKNOWN'): raise ValueError('알 수 없는 주문 응답')
            self.store.finish_job(offer.id,result.state,result.detail)
            return result.state,result.detail
        except Exception as error:
            self.store.finish_job(offer.id,'UNKNOWN',str(error))
            return 'UNKNOWN','전송 결과를 증권사 주문 내역에서 확인하세요'

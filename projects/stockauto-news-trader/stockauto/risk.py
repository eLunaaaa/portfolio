"""파일 역할: 매수의 시장 조건과 보유 포지션의 청산 기준을 계산합니다.
입력: 현재까지 관측한 뉴스·호가·계좌. 출력: 통과/제외 근거 또는 매도 사유.
구조·이유: 판단 함수는 주문을 전송하지 않으며, 실제 전송은 영구 상태 기록 후 별도로 수행합니다.
"""
from datetime import datetime
from .market import KST, rsi2, can_sell


def entry_reason(quote, settings, first_price=None):
    """기능: 시장 조건 검사. 입력: 호가·설정·사건 최초 관측 가격. 출력: 제외 사유 또는 빈 문자열.
    구조·이유: 스프레드·당일 급등·뉴스 관측 후 상승·시간 보정 거래량을 별도로 제한합니다.
    """
    if not settings.get('market_filters',False):return ''
    if not 0<quote.bid<=quote.ask or quote.last<=0 or quote.previous_close<=0:return '시장 판단용 가격 데이터 부족'
    spread=(quote.ask-quote.bid)/quote.ask*100
    if spread>settings['max_spread_pct']:return '호가 스프레드 초과'
    day_change=(quote.last/quote.previous_close-1)*100
    if day_change>settings['max_day_rise_pct']:return '당일 가격 상승 상한 초과'
    if first_price and (quote.ask/first_price-1)*100>settings['max_news_rise_pct']:return '뉴스 최초 관측 이후 가격 상승 상한 초과'
    if quote.volume_ratio<settings['min_volume_ratio']:return '시간 보정 상대 거래량 부족'
    return ''


def exit_reason(position, quote, settings, now, tracked_since):
    """기능: 매도 사유 계산. 입력: 포지션·시세·설정·현재/추적 시작 시각. 출력: 사유 또는 빈 문자열.
    구조·이유: 손절→익절→보유일→RSI 순서로 우선순위를 고정합니다. 평균 매입가 기준 세전 가격 변화입니다.
    """
    if position.average_price<=0 or quote.bid<=0:return ''
    change=(quote.bid/position.average_price-1)*100
    if change<=-settings['stop_loss_pct']:return 'STOP_LOSS'
    if change>=settings['take_profit_pct']:return 'TAKE_PROFIT'
    opened=position.opened_at or tracked_since
    if (now-opened).total_seconds()>=settings['max_holding_days']*86400:return 'HOLDING_PERIOD'
    if settings.get('rsi_exit',True) and len(quote.closes)>=2 and quote.bid>position.average_price:
        if rsi2([*quote.closes,quote.last or quote.bid])>80:return 'RSI_EXIT'
    return ''


def fresh_exit_quote(quote,settings,now):
    """기능: 청산 시세 신선도 확인. 입력: Quote·설정·시각. 출력: bool.
    구조·이유: PC 수신 시각과 키움의 실제 호가 시각을 분리해 지난 시세로 청산하지 않습니다.
    """
    if quote.bid<=0 or not -2<=(now-quote.received_at).total_seconds()<=settings['quote_max_age_seconds']:return False
    if settings.get('mode')=='kiwoom':
        return quote.market_at is not None and -2<=(now-quote.market_at).total_seconds()<=settings['quote_max_age_seconds']
    return True


class RiskManager:
    """DB에 일일 자산 기준과 청산 요청을 남기는 주문 관리 객체입니다."""
    def __init__(self,broker,store,settings,clock):
        """기능: 의존성 연결. 입력: 브로커·DB·설정·시계. 출력: 관리자.
        구조·이유: 매수와 같은 DB/브로커를 공유해 중복 청산을 방지합니다.
        """
        self.broker,self.store,self.settings,self.clock=broker,store,settings,clock

    def buy_reason(self,account):
        """기능: 일일 손실 한도 검사. 입력: 자산 snapshot. 출력: 금지 사유 또는 빈 문자열.
        구조·이유: 당일 첫 정상 자산을 DB에 저장하고 한도 도달을 당일 동안 유지합니다.
        """
        if not self.settings.get('risk_enabled',False):return ''
        if account.equity<=0:return '계좌 평가자산을 확인할 수 없습니다'
        return '일일 자산 손실 한도 도달' if self.store.daily_halt(self.clock(),account.equity,self.settings['daily_loss_limit_pct']) else ''

    def tick(self):
        """기능: 주기적 계좌·주문·청산 관리. 입력: 없음. 출력: 처리 내역 목록.
        구조·이유: 미체결 정리 후 정상 장중에만 보유종목을 재조회하고, 현재 매도 가능 수량으로 한 번 전송합니다.
        """
        if not self.settings.get('risk_enabled',False):return []
        now=self.clock();results=[]
        account=self.broker.account();halted=self.buy_reason(account)=='일일 자산 손실 한도 도달'
        # 이 프로그램의 원격 주문번호가 확인된 미체결 주문만 취소할 수 있습니다.
        for job in self.store.open_jobs():
            if job['state']!='ACCEPTED':continue
            remote=job['remote_id'] or self.broker.order_id(job['id'])
            if remote:self.store.link_order(job['id'],remote)
        pending=self.broker.pending_orders()
        self.store.reconcile_jobs(self.broker,account)
        for job in self.store.open_jobs():
            remote=job['remote_id']
            ticket=next((row for row in pending if row['order_id']==remote),None) if remote else None
            if not ticket:continue
            if ticket['side']!='BUY':continue
            elapsed=(now-datetime.fromisoformat(job['created_at'])).total_seconds()
            if not halted and elapsed<self.settings['cancel_after_seconds']:continue
            if not self.settings.get('demo',False) and not can_sell(self.clock(),self.settings.get('holidays',[])):continue
            cancel_id=self.store.start_cancel(job['id'],now)
            if not cancel_id:continue
            try:result=self.broker.cancel_buy(ticket['code'],remote,ticket['remaining'],cancel_id)
            except Exception as error:
                from .models import Submission
                result=Submission('UNKNOWN',str(error))
            self.store.finish_job(cancel_id,result.state,result.detail)
            results.append(('CANCEL',ticket['code'],result.state))
        if not self.settings.get('auto_exit',False):return results
        if not self.settings.get('demo',False) and not can_sell(self.clock(),self.settings.get('holidays',[])):return results
        # 직전 account 조회 이후 변했을 수 있으므로 포지션과 미체결을 다시 확인합니다.
        for position in self.broker.positions():
            if not self.settings.get("manage_existing_positions",False) and position.code not in self.store.managed_codes():continue
            if position.available<=0 or position.code in account.pending or self.store.has_exit(position.code):continue
            tracked=self.store.tracked_since(position.code,now)
            quote=self.broker.quote(position.code)
            current=self.clock()
            if not fresh_exit_quote(quote,self.settings,current):continue
            reason='DAILY_LOSS' if halted else exit_reason(position,quote,self.settings,current,tracked)
            if not reason:continue
            # 매도 직전 수량과 미체결을 다시 읽고, 익절/RSI 등 조건도 다시 검사합니다.
            fresh=next((p for p in self.broker.positions() if p.code==position.code),None)
            if not fresh or fresh.available<=0 or position.code in self.broker.account().pending:continue
            quote=self.broker.quote(position.code)
            if not fresh_exit_quote(quote,self.settings,self.clock()):continue
            if not self.settings.get('demo',False) and not can_sell(self.clock(),self.settings.get('holidays',[])):continue
            if not halted and not exit_reason(fresh,quote,self.settings,self.clock(),tracked):continue
            job_id=self.store.start_exit(fresh.code,fresh.available,quote.bid,reason,self.clock())
            if not job_id:continue
            try:result=self.broker.sell_limit(fresh.code,fresh.available,quote.bid,job_id)
            except Exception as error:
                from .models import Submission
                result=Submission('UNKNOWN',str(error))
            self.store.finish_job(job_id,result.state,result.detail)
            results.append((reason,fresh.code,result.state))
        return results

"""파일 역할: 실행본과 시계열 재생이 함께 쓰는 매수 의사결정입니다.
입력: 후보·계좌·호가·설정·시각·최초 가격. 출력: 고정된 TradeIntent 또는 거부 사유.
구조·이유: 데이터 공급과 주문 전송을 빼고 순수 판단만 공유하여 두 실행 경로의 규칙을 맞춥니다.
"""
from datetime import timedelta
from .market import can_buy, order_quantity, technical_buy
from .models import TradeIntent
from .risk import entry_reason


def signal_reason(candidate, quote, settings, now, first_price=None):
    """기능: 시간·뉴스·RSI·시장 규칙 검사. 입력: 현재 관측 자료. 출력: 제외 사유 또는 빈 문자열.
    구조: 장 시간→뉴스 확정·유효시각→계약 규모→RSI→시장 필터 순서로 판단합니다.
    이유: 실행본과 재생이 서로 다른 가격(호가/종가)으로 신호를 만들지 않게 합니다.
    """
    if not settings.get('demo', False) and not can_buy(now, settings.get('holidays', [])):
        return '매수 허용 시간이 아닙니다'
    mode = settings.get('strategy_mode', 'news')
    if mode != 'rsi':
        if candidate is None or not candidate.confirmed or candidate.score < settings['min_news_score']:
            return '확정된 뉴스 매수 근거가 없습니다'
        first_at = candidate.first_report_at or candidate.article.published_at
        if not -300 <= (now - first_at).total_seconds() <= settings['news_max_age_hours'] * 3600:
            return '뉴스의 최초 보도 시각이 유효하지 않습니다'
        if settings.get('require_contract_materiality', False) and 'NEW CONTRACT' in candidate.reason and 'EARNINGS UP' not in candidate.reason:
            ratio = candidate.contract_revenue_pct
            if ratio is None or ratio < settings['min_contract_revenue_pct']:
                return '계약 금액/연매출 대비 규모의 확인 기준 미달'
    if (settings.get('require_rsi', False) or mode in ('rsi', 'combined')) and not technical_buy([*quote.closes, quote.ask]):
        return '기존 RSI 매수 조건을 만족하지 않습니다'
    return entry_reason(quote, settings, first_price)


def build_intent(candidate, account, quote, settings, now, occupied, first_price=None):
    """기능: 승인할 수량·가격 고정. 입력: 판단 자료·점유 종목 set. 출력: TradeIntent 또는 ValueError.
    구조: 공통 규칙→중복/슬롯→가용 현금 배분→수수료 포함 수량→유효시간을 계산합니다.
    이유: 다음 봉의 가격이 내려갔다고 재생기가 승인한 수량보다 더 매수하지 못하게 합니다.
    """
    reason = signal_reason(candidate, quote, settings, now, first_price)
    if reason:
        raise ValueError(reason)
    if candidate.code in occupied or len(occupied) >= settings['max_positions']:
        raise ValueError('기존 보유·주문 또는 최대 종목 수 조건')
    slots = settings['max_positions'] - len(occupied)
    budget = min(settings['max_order_won'], account.cash // slots)
    quantity = min(1000000, order_quantity(account.cash, budget, quote.ask, settings['buy_fee_rate']))
    if quantity < 1:
        raise ValueError('예산으로 매수할 수 있는 수량이 없습니다')
    return TradeIntent(candidate.code, quantity, quote.ask,
                       now + timedelta(seconds=settings['approval_seconds']), candidate.reason)

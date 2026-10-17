"""파일 역할: 시간·가격 지표·주문 수량 계산을 외부 API 없이 수행합니다.
구조: 한국 시각 판정과 순수 계산 함수. 이유: 거래 흐름과 분리해 경계값을 검사합니다.
"""
from datetime import datetime, timedelta, timezone, time
from decimal import Decimal, ROUND_CEILING

KST = timezone(timedelta(hours=9))


def now_kst():
    """기능: 현재 한국 시각. 입력: 없음. 출력: timezone 포함 datetime.
    구조·이유: PC의 지역 설정과 무관하게 같은 거래 시간 기준을 사용합니다.
    """
    return datetime.now(KST)


def can_buy(now, holidays=()):
    """기능: 매수 허용 시간 판정. 입력: 시각·휴장일 문자열 목록. 출력: bool.
    구조·이유: 주말/휴장일을 먼저 제외하고 09:00≤시각<15:00를 검사합니다.
    """
    local = now.astimezone(KST)
    return local.weekday() < 5 and local.date().isoformat() not in holidays and time(9) <= local.time().replace(tzinfo=None) < time(15)


def estimated_cost(quantity, price, fee_rate):
    """기능: 수수료 포함 주문액. 입력: 수량·단가·수수료율. 출력: 원 단위 정수.
    구조·이유: Decimal 곱셈 후 올림하여 소수 오차와 예산 초과를 방지합니다.
    """
    return int((Decimal(quantity * price) * (1 + Decimal(str(fee_rate)))).to_integral_value(rounding=ROUND_CEILING))


def order_quantity(cash, cap, price, fee_rate):
    """기능: 예산 내 수량 계산. 입력: 가용금액·종목당 상한·단가·요율. 출력: 정수.
    구조·이유: 수수료를 분모에 먼저 포함하므로 주문 전 잔고를 차감할 필요가 없습니다.
    """
    if price <= 0 or cash <= 0 or cap <= 0: return 0
    unit = Decimal(price) * (1 + Decimal(str(fee_rate)))
    return int(Decimal(min(cash, cap)) // unit)


def rsi2(closes):
    """기능: RSI(2) 계산. 입력: 시간순 종가. 출력: 0~100 실수.
    구조·이유: 최근 두 변화의 상승/하락 합을 이용하고 보합은 50으로 정의합니다.
    """
    if len(closes) < 3: raise ValueError('RSI 계산에는 종가 3개가 필요합니다')
    changes = [closes[-2]-closes[-3], closes[-1]-closes[-2]]
    up = sum(max(x, 0) for x in changes)
    down = sum(max(-x, 0) for x in changes)
    return 50.0 if up + down == 0 else 100.0 * up / (up + down)


def technical_buy(closes):
    """기능: 기존 RSI 매수 조건 판정. 입력: 현재가를 마지막에 더한 종가 목록.
    출력: bool. 구조·이유: 60개 이상 데이터에서 MA20>MA60, RSI<5, 2일 변화<−2%를 확인합니다.
    """
    if len(closes) < 60 or min(closes) <= 0: return False
    return sum(closes[-20:])/20 > sum(closes[-60:])/60 and rsi2(closes) < 5 and (closes[-1]/closes[-3]-1)*100 < -2


def can_sell(now,holidays=()):
    """기능: 프로그램의 청산 허용 시간. 입력: 현재 시각·휴장일. 출력: bool.
    구조·이유: 평일 09:00~15:20에만 청산하며 긴 조회 중 시간이 넘어가면 전송을 중단합니다.
    """
    local=now.astimezone(KST)
    return local.weekday()<5 and local.date().isoformat() not in holidays and time(9)<=local.time().replace(tzinfo=None)<time(15,20)

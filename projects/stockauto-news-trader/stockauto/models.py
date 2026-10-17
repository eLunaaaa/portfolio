"""파일 역할: 모듈 사이에 전달하는 데이터를 명시합니다.
구조: 뉴스→분석 후보→승인 제안→증권사 결과. 금액은 원 단위 정수입니다.
이유: dict 키 오타와 주문 접수/체결 상태 혼동을 줄입니다.
"""
from dataclasses import dataclass
from datetime import datetime
from typing import FrozenSet, Tuple


@dataclass(frozen=True)
class Article:
    """제목·출처·게시시간과 중복 판별용 뉴스 키를 보관합니다."""
    key: str
    title: str
    link: str
    published_at: datetime


@dataclass(frozen=True)
class Candidate:
    """어떤 기사 때문에 해당 종목을 선택했는지와 점수를 함께 전달합니다."""
    key: str
    code: str
    name: str
    score: int
    importance: int
    reason: str
    lcd_text: str
    article: Article
    event_group: str = ""
    confirmed: bool = True
    first_report_at: datetime = None
    contract_value_won: int = 0
    contract_revenue_pct: float = None


@dataclass(frozen=True)
class Quote:
    """호가·현재가·전일종가·거래량 비율·두 종류의 시각을 전달합니다.
    closes는 오늘 이전 일봉 종가, market_at은 거래소 호가 시각입니다.
    volume_ratio는 과거 일평균 거래량을 장 경과 시간으로 보정한 상대 거래량입니다.
    """
    ask: int
    received_at: datetime
    closes: Tuple[int, ...] = ()
    bid: int = 0
    last: int = 0
    previous_close: int = 0
    volume_ratio: float = 0.0
    market_at: datetime = None


@dataclass(frozen=True)
class Account:
    """증권사 주문가능금액·보유/미체결 코드와 평가자산(equity, 원)입니다."""
    cash: int
    holdings: FrozenSet[str]
    pending: FrozenSet[str]
    equity: int = 0


@dataclass(frozen=True)
class Offer:
    """LCD에서 사용자가 승인하는 고정된 종목·수량·지정가·만료시각입니다."""
    id: str
    candidate: Candidate
    quantity: int
    price: int
    expires_at: datetime


@dataclass(frozen=True)
class Submission:
    """접수 요청 결과. ACCEPTED는 전송 성공이며 전량 체결을 뜻하지 않습니다."""
    state: str
    detail: str


@dataclass(frozen=True)
class Position:
    """보유 수량·평균 매입가·매도 가능 수량. opened_at은 증권사가 제공할 때 사용합니다."""
    code: str
    quantity: int
    average_price: int
    available: int
    opened_at: datetime = None


@dataclass(frozen=True)
class OrderSnapshot:
    """확인된 주문 상태. 수량은 누계이며 같은 통보를 다시 적용해도 체결이 중복되지 않습니다.
    order_day는 거래일 식별자여서 다음 날 재사용된 주문번호를 다른 주문으로 혼동하지 않습니다.
    average_price=0은 평균 체결가 미확인입니다. 그 값을 임의의 체결가로 대체하지 않습니다.
    """
    remote_id: str
    code: str
    side: str
    state: str
    filled_quantity: int
    remaining_quantity: int
    average_price: float
    order_day: str


@dataclass(frozen=True)
class TradeIntent:
    """실행본과 재생이 공유하는 승인 대상. 수량·지정가·만료·근거는 생성 뒤 고정합니다."""
    code: str
    quantity: int
    limit_price: int
    expires_at: datetime
    reason: str

"""파일 역할: 실제 주문 없는 로컬 실행용 브로커입니다.
구조: 계좌 스냅샷·호가 조회·지정가 매수 인터페이스. 이유: 실제 브로커와 같은 경로로 시연합니다.
"""
from .market import estimated_cost
from .models import Account, Quote, Submission, Position, OrderSnapshot
from .market import KST
from dataclasses import replace, asdict
from datetime import datetime
import json


class PaperBroker:
    """수수료 포함 금액을 예약하고 계좌·주문 상태를 SQLite에 보존하는 로컬 브로커."""
    def __init__(self, settings, clock):
        """기능: 로컬 계좌 생성. 입력: 설정·시계. 출력: 브로커.
        구조·이유: quotes/cash를 설정에서 읽어 네트워크 없이 실행합니다.
        """
        self.settings, self.clock = settings, clock
        self.identity = 'paper'
        self.cash = settings['paper_cash_won']
        self.pending, self.orders = set(), []
        self.inventory = {}
        self.reserved = 0
        self.store = None

    def bind_store(self, store):
        """기능: 모의 계좌 복구. 입력: 같은 컨텍스트의 Store. 출력: 현금·예약금·잔고·주문 복원.
        구조·이유: 프로그램 재시작 때 DB 원장만 남고 모의 계좌는 초기 자금으로 리셋되는 불일치를 막습니다.
        """
        self.store = store
        row = store.db.execute("SELECT value FROM metadata WHERE key='paper_broker_state'").fetchone()
        if not row:
            self._persist()
            return
        data = json.loads(row[0])
        self.cash, self.reserved = data['cash'], data['reserved']
        self.orders = data['orders']
        for order in self.orders:
            order['created_at'] = datetime.fromisoformat(order['created_at'])
        self.inventory = {code: Position(**dict(value, opened_at=datetime.fromisoformat(value['opened_at']) if value['opened_at'] else None))
                          for code, value in data['inventory'].items()}
        self.pending = set(data['pending'])

    def _persist(self):
        """입력: 모의 계좌 현재 상태. 출력: 같은 SQLite의 JSON snapshot.
        이유: 접수 뒤 프로세스가 종료돼도 주문 ID와 예약금으로 UNKNOWN 상태를 재대조할 수 있습니다.
        """
        if self.store is None:
            return
        data = dict(cash=self.cash, reserved=self.reserved, pending=sorted(self.pending),
                    orders=self.orders, inventory={code: asdict(position) for code, position in self.inventory.items()})
        with self.store.db:
            self.store.db.execute("INSERT OR REPLACE INTO metadata VALUES('paper_broker_state',?)",
                                  (json.dumps(data, default=str),))

    def account(self):
        """기능: 로컬 계좌 조회. 입력: 없음. 출력: Account.
        구조·이유: 주문 성공 후에만 예약금과 미체결 종목을 반영합니다.
        """
        equity=self.cash+self.reserved+sum(p.quantity*self.settings['paper_quotes'][code]['ask'] for code,p in self.inventory.items())
        return Account(self.cash,frozenset(self.inventory),frozenset(self.pending),equity)

    def quote(self, code):
        """기능: 예제 호가 조회. 입력: 종목코드. 출력: Quote.
        구조·이유: 설정한 가격을 현재시각의 로컬 시세로 제공합니다.
        """
        entry = self.settings['paper_quotes'][code]
        ask=entry['ask']
        return Quote(ask,self.clock(),tuple(entry.get('closes',[ask]*60)),entry.get('bid',ask),
            entry.get('last',ask),entry.get('previous_close',ask),entry.get('volume_ratio',1.0),self.clock())

    def buy_limit(self, code, quantity, price, request_id):
        """기능: 로컬 지정가 주문 기록. 입력: 종목·수량·단가·ID. 출력: Submission.
        구조·이유: 이미 있는 ID/종목과 잔액 부족을 검사한 뒤 한 번만 금액을 예약합니다.
        """
        cost = estimated_cost(quantity,price,self.settings['buy_fee_rate'])
        if code in self.pending or any(o['id']==request_id for o in self.orders) or cost>self.cash:
            return Submission('FAILED','로컬 주문 거절')
        self.cash -= cost
        self.reserved += cost
        self.pending.add(code)
        self.orders.append(dict(id=request_id,code=code,quantity=quantity,price=price,side='BUY',remaining=quantity,state='PENDING',filled=0,created_at=self.clock()))
        self._persist()
        return Submission('ACCEPTED','로컬 주문 기록 완료 (PAPER)')

    def positions(self):
        """기능: 로컬 보유종목 조회. 입력: 없음. 출력: Position 목록.
        구조·이유: 주문 전송과 실제 보유를 구분하며, fill_buy가 체결을 시뮬레이션합니다.
        """
        return list(self.inventory.values())

    def order_id(self,request_id):
        """기능: 원격 번호 대체. 입력: 요청 ID. 출력: 로컬 ID 또는 빈 문자열.
        구조·이유: 로컬 모드도 동일한 소유 주문 취소 경로를 검사합니다.
        """
        return request_id if any(o['id']==request_id for o in self.orders) else ''

    def pending_orders(self):
        """기능: 미체결 상세. 입력: 없음. 출력: 주문번호·코드·잔량·방향 목록.
        구조·이유: 이미 체결/취소된 주문을 미체결로 계산하지 않습니다.
        """
        return [dict(order_id=o['id'],code=o['code'],remaining=o['remaining'],side=o['side']) for o in self.orders if o['remaining']>0]

    def order_status(self,remote_id):
        """기능: 로컬 주문의 확정 상태. 입력: 주문 ID. 출력: PENDING/FILLED/CANCELLED/UNKNOWN.
        구조·이유: 미체결 목록에서 사라졌다는 이유만으로 체결로 간주하지 않습니다.
        """
        order=next((o for o in self.orders if o['id']==remote_id),None)
        return order['state'] if order else 'UNKNOWN'

    def cancel_buy(self,code,remote_id,quantity,request_id):
        """기능: 미체결 매수 취소. 입력: 코드·원주문·잔량·요청 ID. 출력: 접수 결과.
        구조·이유: 원주문의 실제 남은 수량만 취소하고 예약금만 반환합니다.
        """
        order=next((o for o in self.orders if o['id']==remote_id and o['code']==code and o['side']=='BUY'),None)
        if not order or quantity <= 0 or order['remaining']!=quantity:return Submission('FAILED','취소 수량 불일치')
        refund=estimated_cost(order['quantity'],order['price'],self.settings['buy_fee_rate'])-estimated_cost(order.get('filled',0),order['price'],self.settings['buy_fee_rate'])
        self.cash+=refund;self.reserved-=refund;order['remaining']=0;order['state']='CANCELLED';self.pending.discard(code)
        self.orders.append(dict(id=request_id,code=code,quantity=quantity,price=0,side='CANCEL',remaining=0,state='FILLED',filled=0,created_at=self.clock()))
        self._persist()
        return Submission('ACCEPTED','로컬 미체결 취소')

    def order_snapshot(self, remote_id, submitted_at):
        """기능: 확인된 로컬 주문 누계 반환.
        입력: 주문번호·제출시각. 출력: OrderSnapshot 또는 None.
        구조·이유: 체결 수량·잔량·거래일을 명시하여 수동 잔고를 체결로 추측하지 않습니다.
        """
        order = next((row for row in self.orders if row['id'] == remote_id), None)
        if not order or order['created_at'].astimezone(KST).date() != submitted_at.astimezone(KST).date():
            return None
        filled = order.get('filled', 0)
        state = order['state']
        if state == 'PENDING':
            state = 'PARTIALLY_FILLED' if filled else 'ACCEPTED'
        return OrderSnapshot(remote_id, order['code'], order['side'], state,
                             filled, order['remaining'], order['price'] if filled else 0,
                             order['created_at'].astimezone(KST).date().isoformat())

    def fill_buy(self, request_id, quantity=None):
        """기능: 매수의 일부 또는 전량 체결 재현. 입력: 요청 ID·이번 체결 수량(생략 시 잔량).
        출력: 없음. 구조: 누계 체결 비용 차이만 예약금에서 빼고 기존 잔고에 수량을 더합니다.
        이유: 부분 체결 후 취소와 수동 매수 혼재를 실제 수량으로 검사합니다.
        """
        order = next(row for row in self.orders if row['id'] == request_id and row['side'] == 'BUY')
        quantity = order['remaining'] if quantity is None else quantity
        if quantity == 0:
            return
        if type(quantity) is not int or not 0 < quantity <= order['remaining']:
            raise ValueError('체결 수량은 남은 수량 이하의 양수여야 합니다')
        previous = order.get('filled', 0)
        fee = self.settings['buy_fee_rate']
        self.reserved -= estimated_cost(previous + quantity, order['price'], fee) - estimated_cost(previous, order['price'], fee)
        actual = self.inventory.get(order['code'])
        old_qty, old_cost, available = (actual.quantity, actual.quantity * actual.average_price, actual.available) if actual else (0, 0, 0)
        self.inventory[order['code']] = Position(order['code'], old_qty + quantity,
            (old_cost + quantity * order['price']) / (old_qty + quantity), available + quantity,
            actual.opened_at if actual else self.clock())
        order['filled'] = previous + quantity
        order['remaining'] -= quantity
        if order['remaining'] == 0:
            self.pending.discard(order['code']); order['state'] = 'FILLED'
        self._persist()

    def sell_limit(self,code,quantity,price,request_id):
        """기능: 로컬 지정가 매도 접수. 입력: 코드·수량·가격·ID. 출력: Submission.
        구조·이유: 현재 매도 가능 수량과 중복 주문을 확인하며 즉시 체결로 간주하지 않습니다.
        """
        position=self.inventory.get(code)
        if not position or position.available<quantity or code in self.pending:return Submission('FAILED','매도 가능 수량 부족')
        self.orders.append(dict(id=request_id,code=code,quantity=quantity,price=price,side='SELL',remaining=quantity,state='PENDING',filled=0,created_at=self.clock()))
        self.pending.add(code)
        self._persist()
        return Submission('ACCEPTED','로컬 매도 전송')

    def fill_sell(self,request_id):
        """기능: 로컬 매도 체결 재현. 입력: 매도 요청 ID. 출력: 없음.
        구조·이유: 청산 접수와 체결을 분리하고 체결 때만 현금·잔고를 변경합니다.
        """
        order=next(o for o in self.orders if o['id']==request_id and o['side']=='SELL')
        if order['remaining']<=0:return
        fee=self.settings.get('sell_fee_rate',0)+self.settings.get('sell_tax_rate',0)
        self.cash+=int(order['quantity']*order['price']*(1-fee))
        position=self.inventory[order['code']]
        remaining=position.quantity-order['quantity']
        if remaining>0:
            self.inventory[order['code']]=replace(position,quantity=remaining,available=max(0,position.available-order['quantity']))
        else:self.inventory.pop(order['code'],None)
        self.pending.discard(order['code'])
        order['filled']=order['quantity'];order['remaining']=0;order['state']='FILLED'
        self._persist()

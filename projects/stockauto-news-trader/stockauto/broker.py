"""파일 역할: 실제 주문 없는 로컬 실행용 브로커입니다.
구조: 계좌 스냅샷·호가 조회·지정가 매수 인터페이스. 이유: 실제 브로커와 같은 경로로 시연합니다.
"""
from .market import estimated_cost
from .models import Account, Quote, Submission, Position


class PaperBroker:
    """PC 메모리에 주문을 기록하고 수수료 포함 금액을 예약하는 로컬 브로커."""
    def __init__(self, settings, clock):
        """기능: 로컬 계좌 생성. 입력: 설정·시계. 출력: 브로커.
        구조·이유: quotes/cash를 설정에서 읽어 네트워크 없이 실행합니다.
        """
        self.settings, self.clock = settings, clock
        self.cash = settings['paper_cash_won']
        self.pending, self.orders = set(), []
        self.inventory = {}
        self.reserved = 0

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
        self.orders.append(dict(id=request_id,code=code,quantity=quantity,price=price,side='BUY',remaining=quantity,state='PENDING'))
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
        if not order or order['remaining']!=quantity:return Submission('FAILED','취소 수량 불일치')
        refund=estimated_cost(quantity,order['price'],self.settings['buy_fee_rate'])
        self.cash+=refund;self.reserved-=refund;order['remaining']=0;order['state']='CANCELLED';self.pending.discard(code)
        self.orders.append(dict(id=request_id,code=code,quantity=quantity,price=0,side='CANCEL',remaining=0,state='FILLED'))
        return Submission('ACCEPTED','로컬 미체결 취소')

    def fill_buy(self,request_id):
        """기능: 로컬 매수 체결 재현. 입력: 요청 ID. 출력: 없음.
        구조·이유: 테스트에서만 명시적으로 호출하여 주문 접수와 체결 상태를 분리합니다.
        """
        order=next(o for o in self.orders if o['id']==request_id)
        if order['remaining']<=0:return
        self.reserved-=estimated_cost(order['remaining'],order['price'],self.settings['buy_fee_rate'])
        self.inventory[order['code']]=Position(order['code'],order['quantity'],order['price'],order['quantity'],self.clock())
        self.pending.discard(order['code']);order['remaining']=0;order['state']='FILLED'

    def sell_limit(self,code,quantity,price,request_id):
        """기능: 로컬 지정가 매도 접수. 입력: 코드·수량·가격·ID. 출력: Submission.
        구조·이유: 현재 매도 가능 수량과 중복 주문을 확인하며 즉시 체결로 간주하지 않습니다.
        """
        position=self.inventory.get(code)
        if not position or position.available<quantity or code in self.pending:return Submission('FAILED','매도 가능 수량 부족')
        self.orders.append(dict(id=request_id,code=code,quantity=quantity,price=price,side='SELL',remaining=quantity,state='PENDING'))
        self.pending.add(code)
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
            from dataclasses import replace
            self.inventory[order['code']]=replace(position,quantity=remaining,available=max(0,position.available-order['quantity']))
        else:self.inventory.pop(order['code'],None)
        self.pending.discard(order['code'])
        order['remaining']=0;order['state']='FILLED'

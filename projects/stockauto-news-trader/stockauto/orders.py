"""파일 역할: 키움 주문 접수·체결 통보를 검증된 누계 snapshot으로 정규화합니다.
입력: 계좌 정보를 제거한 ORDER_LINK/CHEJAN dict. 출력: 로컬 번호 연결·OrderSnapshot.
구조·이유: Qt/COM 없이 중복 통보·부분 체결·취소·거래일을 검사할 수 있도록 순수 객체로 분리했습니다.
"""
from dataclasses import replace
from .models import OrderSnapshot


class OrderBook:
    """주문일·원격 번호별 체결 누계와 체결번호 중복 집합을 관리합니다."""
    def __init__(self):
        """입력: 없음. 출력: 빈 주문 캐시. 이유: 모든 원문은 Store에 따로 영속 보존합니다."""
        self.links = {}
        self.snapshots = {}
        self.executions = {}

    def merge(self, snapshot):
        """입력: OrderSnapshot. 출력: 병합한 snapshot.
        구조·이유: 과거 조회 결과로 체결 누계를 줄이거나 확정 상태를 접수로 되돌리지 않습니다.
        """
        key = (snapshot.order_day, snapshot.remote_id)
        old = self.snapshots.get(key)
        if old:
            if snapshot.filled_quantity < old.filled_quantity:
                return old
            if old.state == 'FILLED' or (old.state in ('CANCELLED', 'FAILED') and snapshot.state in ('ACCEPTED', 'PARTIALLY_FILLED')):
                snapshot = replace(snapshot, state=old.state, remaining_quantity=old.remaining_quantity)
            if snapshot.filled_quantity == old.filled_quantity and snapshot.average_price == 0:
                snapshot = replace(snapshot, average_price=old.average_price)
        self.snapshots[key] = snapshot
        return snapshot

    def observe(self, event):
        """기능: 통보 원문을 해석합니다. 입력: 정규화 이벤트 dict. 출력: 없음.
        구조: 접수 번호 연결 또는 체결번호 중복 제거→누계→취소 원주문 갱신.
        이유: 취소 주문의 수량을 원주문의 체결 수량에 더하지 않습니다.
        """
        if event['kind'] == 'ORDER_LINK':
            self.links[event['request_id']] = event['remote_id']
            return
        if event['kind'] != 'CHEJAN':
            return
        remote, day, code = event['remote_id'], event['order_day'], event['code']
        side, status = event['side'], event['status']
        key = (day, remote)
        old = self.snapshots.get(key)
        if side == 'CANCEL':
            state = 'FAILED' if '거부' in status else 'FILLED' if status == '확인' else 'ACCEPTED'
            self.merge(OrderSnapshot(remote, code, side, state, 0, 0, 0, day))
            if state == 'FILLED' and event.get('original_id'):
                original = self.snapshots.get((day, event['original_id']))
                if original and original.state != 'FILLED':
                    self.merge(replace(original, state='CANCELLED', remaining_quantity=0))
            return
        filled = old.filled_quantity if old else 0
        remaining = event['remaining']
        average = old.average_price if old else 0
        executions = self.executions.setdefault(key, {})
        if status == '체결':
            # 신규 주문의 주문수량−미체결은 확인된 누계입니다. 취소 통보에는 이 식을 사용하지 않습니다.
            filled = max(filled, event['ordered'] - remaining)
            execution = event.get('execution_id')
            if execution and event['fill_quantity'] > 0 and event['fill_price'] > 0:
                executions.setdefault(execution, (event['fill_quantity'], event['fill_price']))
            known_quantity = sum(quantity for quantity, _ in executions.values())
            average = (event['filled_value'] / filled if filled and event.get('filled_value', 0) > 0
                       else sum(quantity * price for quantity, price in executions.values()) / filled if filled and known_quantity == filled else 0)
        if '거부' in status:
            state = 'FAILED'
            remaining = 0
        elif filled == event['ordered'] and filled:
            state = 'FILLED'
        else:
            state = 'PARTIALLY_FILLED' if filled else 'ACCEPTED'
        self.merge(OrderSnapshot(remote, code, side, state, filled, remaining, average, day))

    def snapshot(self, remote_id, order_day):
        """입력: 원격 번호·거래일. 출력: 확인된 snapshot 또는 None. 이유: 날짜가 다른 같은 번호를 격리합니다."""
        return self.snapshots.get((order_day, remote_id))

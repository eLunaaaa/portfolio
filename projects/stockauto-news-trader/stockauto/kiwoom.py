"""파일 역할: 기존 키움 ActiveX 연동을 명확한 요청/응답 경계로 정리한 브로커입니다.
구조: GUI 스레드의 QAxWidget→TR 조회→계좌/호가 변환→지정가 SendOrder.
이유: 기존 QThread에서 GUI 객체를 직접 호출하던 구조와 무한 응답 대기, 호출 문자열 오타를 제거합니다.
"""
import os
import time
from datetime import datetime
from .models import Account, Quote, Submission, Position
from .market import KST


class KiwoomBroker:
    """Windows 키움 OpenAPI+를 감싼 어댑터. 모든 메서드는 Qt GUI 스레드에서 호출합니다."""
    def __init__(self, settings, clock):
        """기능: ActiveX 생성·로그인. 입력: 설정·시계. 출력: 로그인된 브로커.
        구조·이유: 계좌는 환경변수에서 읽고 본인 로그인 계좌 목록에 있는지 검사합니다.
        """
        from PyQt5.QAxContainer import QAxWidget
        from PyQt5.QtCore import QEventLoop, QTimer, QThread
        from PyQt5.QtWidgets import QApplication
        self.clock, self.settings = clock, settings
        self.EventLoop, self.Timer = QEventLoop, QTimer
        self.Thread, self.app = QThread, QApplication.instance()
        self.ax = QAxWidget('KHOPENAPI.KHOpenAPICtrl.1')
        if self.ax.isNull(): raise RuntimeError('키움 OpenAPI+ ActiveX를 등록한 Windows 환경이 필요합니다')
        self.login_code, self.waiting, self.response, self.error = None, None, None, None
        self.last_request, self.sequence = 0.0, 0
        self.ax.OnEventConnect.connect(self._login_event)
        self.ax.OnReceiveTrData.connect(self._tr_event)
        self.ax.OnReceiveMsg.connect(self._message_event)
        self.messages = []
        self.remote_orders = {}
        self.history_cache = {}
        loop, timer = self._waiter(120000)
        self.loop = loop
        if int(self.ax.dynamicCall('CommConnect()')) != 0: raise RuntimeError('로그인 요청 실패')

        if self.login_code is None: loop.exec_()
        timer.stop()
        if self.login_code != 0: raise RuntimeError('키움 로그인 실패 또는 시간 초과')
        accounts = str(self.ax.dynamicCall('GetLoginInfo(QString)','ACCNO')).split(';')
        self.account_number = os.environ.get('KIWOOM_ACCOUNT','').strip()
        if not self.account_number or self.account_number not in accounts:
            raise ValueError('KIWOOM_ACCOUNT를 로그인된 계좌로 설정하세요')
        # 응답 시간 초과 후에는 다음 요청에 늦은 응답을 섞지 않도록 세션을 중단합니다.
        self.healthy = True

    def _assert_gui(self):
        """기능: 스레드 확인. 입력: 없음. 출력: 없음 또는 예외.
        구조·이유: QAxWidget은 생성한 GUI 스레드에서만 접근합니다.
        """
        if self.Thread.currentThread() != self.app.thread(): raise RuntimeError('키움 호출은 GUI 스레드에서 수행해야 합니다')

    def _waiter(self, timeout_ms):
        """기능: 제한시간 있는 Qt 대기 객체 생성. 입력: ms. 출력: loop·timer.
        구조·이유: 응답이 없어도 전체 프로그램이 무기한 멈추지 않습니다.
        """
        loop, timer = self.EventLoop(), self.Timer()
        timer.setSingleShot(True); timer.timeout.connect(loop.quit); timer.start(timeout_ms)
        return loop, timer

    def _login_event(self, code):
        """기능: 로그인 콜백. 입력: 오류 코드. 출력: 내부 결과 갱신.
        구조·이유: 실패도 대기를 종료시켜 호출자가 처리합니다.
        """
        self.login_code = int(code)
        self.loop.quit()

    def _message_event(self, screen, request, tr, message):
        """기능: 증권사 메시지 수신. 입력: 이벤트 인자. 출력: 최근 메시지 저장.
        구조·이유: 문자열 메시지를 주문 체결 완료로 오해하지 않고 확인 자료로 보관합니다.
        """
        self.messages.append(str(message)); self.messages = self.messages[-20:]

    def _tr_event(self, screen, request, tr, record, next_page, *unused):
        """기능: 현재 요청의 TR 응답 수신. 입력: 키움 콜백. 출력: (헤더·행·다음 페이지).
        구조·이유: 요청 ID가 일치할 때만 처리하고 파싱 오류도 대기 종료 후 전달합니다.
        """
        if str(request).startswith('N'):
            remote=str(self.ax.dynamicCall('GetCommData(QString, QString, int, QString)',tr,request,0,'주문번호')).strip()
            if remote:self.remote_orders[str(request)[1:]]=remote.lstrip('0') or '0'
            return
        if request != self.waiting: return
        try:
            def value(index, field):
                """기능: TR 필드 추출. 입력: 행·필드명. 출력: 정리된 문자열.
                구조·이유: GetCommData 호출 서명을 한 곳에 고정합니다.
                """
                return str(self.ax.dynamicCall('GetCommData(QString, QString, int, QString)',tr,request,index,field)).strip()
            count = int(self.ax.dynamicCall('GetRepeatCnt(QString, QString)',tr,request))
            fields = self.current_fields
            rows = [{field:value(i,field) for field in fields} for i in range(count)]
            header = {field:value(0,field) for field in fields}
            self.response = (header,rows,str(next_page)=='2')
        except Exception as error: self.error = error
        finally: self.loop.quit()

    def _request(self, tr, inputs, fields, page=0):
        """기능: TR 한 페이지 조회. 입력: TR 코드·필드·입력값·페이지. 출력: 응답 tuple.
        구조·이유: 요청 간격을 Qt 이벤트로 기다리고, 고유 ID·시간 제한·반환 코드로 요청을 구분합니다.
        """
        self._assert_gui()
        if not self.healthy: raise RuntimeError('조회 세션을 재시작해야 합니다')
        remaining = 0.4-(time.monotonic()-self.last_request)
        if remaining>0:
            pause, timer = self._waiter(max(1,int(remaining*1000))); pause.exec_(); timer.stop()
        self.sequence += 1
        name = f'news_{self.sequence}'
        self.waiting, self.current_fields, self.response, self.error = name, fields, None, None
        for key,value in inputs.items(): self.ax.dynamicCall('SetInputValue(QString, QString)',key,str(value))
        self.loop, timer = self._waiter(15000)
        self.last_request = time.monotonic()
        result = self.ax.dynamicCall('CommRqData(QString, QString, int, QString)',name,tr,page,'2001')
        if int(result) != 0:
            timer.stop(); self.waiting = None
            raise RuntimeError(f'TR 요청 실패: {result}')
        if self.response is None and self.error is None: self.loop.exec_()
        timer.stop(); self.waiting = None
        if self.error: raise self.error
        if self.response is None:
            self.healthy = False
            raise TimeoutError('TR 응답 시간 초과: 자동 주문 재시도를 하지 않습니다')
        return self.response

    def _pages(self, tr, inputs, fields):
        """기능: 연속 조회 통합. 입력: TR·입력·필드. 출력: 전체 행 목록.
        구조·이유: 잔고/미체결의 두 번째 페이지도 포함하여 중복 주문 판단이 빠지지 않습니다.
        """
        rows, page = [], 0
        for _ in range(100):
            _, chunk, more = self._request(tr,inputs,fields,page)
            rows.extend(chunk)
            if not more: return rows
            page = 2
        raise RuntimeError('TR 페이지 한도 초과')

    @staticmethod
    def _integer(value):
        """기능: 키움 숫자 변환. 입력: 부호/쉼표 포함 문자열. 출력: 정수.
        구조·이유: 가격의 방향 부호는 절댓값으로 정규화하고 빈 수치는 0으로 처리합니다.
        """
        return abs(int(str(value).replace(',','').strip() or '0'))

    def account(self):
        """기능: 계좌 현황 재조회. 입력: 없음. 출력: Account.
        구조·이유: 예수금·잔고·미체결을 API 원본으로 갱신하고 수량 0/완료 주문을 제외합니다.
        """
        common = {'계좌번호':self.account_number,'비밀번호':'','비밀번호입력매체구분':'00','조회구분':'2'}
        header,_,_ = self._request('opw00001',common,['주문가능금액'])
        equity,_,_=self._request('opw00018',common,['추정예탁자산'])
        holdings=self.positions()
        pending=self.pending_orders()
        return Account(self._integer(header['주문가능금액']),
            frozenset(row.code for row in holdings if row.quantity>0),
            frozenset(row['code'] for row in pending if row['remaining']>0),self._integer(equity.get('추정예탁자산','0')))

    def positions(self):
        """기능: 현재 잔고 수량/평균단가/매도가능 수량. 입력: 없음. 출력: Position 목록.
        구조·이유: 청산 직전에도 같은 TR로 다시 조회하며, 수량 0은 제거합니다.
        """
        common={'계좌번호':self.account_number,'비밀번호':'','비밀번호입력매체구분':'00','조회구분':'2'}
        rows=self._pages('opw00018',common,['종목번호','보유수량','매입가','매매가능수량'])
        return [Position(r['종목번호'].removeprefix('A'),self._integer(r['보유수량']),self._integer(r.get('매입가',0)),self._integer(r.get('매매가능수량',0))) for r in rows if self._integer(r['보유수량'])>0]

    def pending_orders(self):
        """기능: 미체결 주문 상세 조회. 입력: 없음. 출력: 원격 주문번호·코드·잔량·방향.
        구조·이유: 취소는 별도로 확인한 본 프로그램의 원격 번호에만 허용합니다.
        """
        rows=self._pages('opt10075',{'계좌번호':self.account_number,'전체종목구분':'0','매매구분':'0','체결구분':'0'},['종목코드','주문번호','미체결수량','주문구분','주문상태','체결량','원주문번호'])
        self.recent_orders=rows
        return [dict(order_id=str(self._integer(r.get('주문번호',0))),code=r['종목코드'].removeprefix('A'),remaining=self._integer(r['미체결수량']),side='BUY' if '매수' in r.get('주문구분','') else 'SELL') for r in rows if self._integer(r['미체결수량'])>0]

    def order_status(self,remote_id):
        """기능: 전체 주문 snapshot의 명시적 완료 상태. 입력: 원격 번호. 출력: 주문 상태.
        구조·이유: 취소 확인·거부·체결 수량을 확인하며, 조회 목록 누락은 UNKNOWN으로 유지합니다.
        """
        rows=getattr(self,'recent_orders',[])
        normalized=str(remote_id).lstrip('0') or '0'
        row=next((r for r in rows if str(self._integer(r.get('주문번호',0)))==normalized),None)
        if not row:return 'UNKNOWN'
        if '거부' in row.get('주문상태',''):return 'FAILED'
        if '취소' in row.get('주문구분','') and row.get('주문상태')=='확인':return 'FILLED'
        if self._integer(row['미체결수량'])==0:
            cancellations=[r for r in rows if str(self._integer(r.get('원주문번호',0)))==normalized and '취소' in r.get('주문구분','') and r.get('주문상태')=='확인']
            if cancellations:return 'CANCELLED'
            if self._integer(row.get('체결량',0))>0:return 'FILLED'
        return 'PENDING'

    def order_id(self,request_id):
        """기능: 비동기 접수 응답에서 원격 번호 조회. 입력: 로컬 ID. 출력: 번호 또는 빈 문자열.
        구조·이유: 원격 번호가 확인되지 않은 주문은 자동 취소 대상을 추측하지 않습니다.
        """
        return self.remote_orders.get(request_id,'')

    def quote(self, code):
        """기능: 최신 호가와 선택적 일봉 조회. 입력: 종목. 출력: Quote.
        구조·이유: 지정가 주문에는 최우선 매도호가를 사용하고 RSI 옵션은 시간순 종가를 제공합니다.
        """
        now=self.clock();today=now.strftime('%Y%m%d')
        # 과거 종가/거래량은 당일 캐시로 조회 부하를 줄입니다. 오늘의 진행 중 일봉은 제외합니다.
        cached=self.history_cache.get(code)
        if not cached or cached[0]!=today:
            _,rows,_=self._request('opt10081',{'종목코드':code,'기준일자':today,'수정주가구분':'1'},['일자','현재가','거래량'])
            rows=[r for r in sorted(rows,key=lambda r:r['일자']) if r['일자']<today]
            cached=(today,tuple(self._integer(r['현재가']) for r in rows),tuple(self._integer(r['거래량']) for r in rows))
            self.history_cache[code]=cached
        _,closes,volumes=cached
        price,_,_=self._request('opt10001',{'종목코드':code},['현재가','거래량'])
        header,_,_=self._request('opt10004',{'종목코드':code},['매도최우선호가','매수최우선호가','호가잔량기준시간'])
        received=self.clock();market_at=None
        stamp=header.get('호가잔량기준시간','').strip()
        if len(stamp)==6 and stamp.isdigit():
            try:market_at=received.replace(hour=int(stamp[:2]),minute=int(stamp[2:4]),second=int(stamp[4:6]),microsecond=0)
            except ValueError:pass
        elapsed=max(0,(received.hour*60+received.minute)-9*60)
        fraction=max(0.05,min(1.0,elapsed/390))
        average=sum(volumes[-20:])/len(volumes[-20:]) if len(volumes)>=20 else 0
        ratio=self._integer(price['거래량'])/(average*fraction) if average else 0
        return Quote(self._integer(header['매도최우선호가']),received,closes,
            self._integer(header['매수최우선호가']),self._integer(price['현재가']),closes[-1] if closes else 0,ratio,market_at)

    def buy_limit(self, code, quantity, price, request_id):
        """기능: 승인된 지정가 매수 한 번 전송. 입력: 종목·수량·가격·고유 ID.
        출력: Submission. 구조·이유: API 0은 전송 성공으로만 기록하며 실패/예외를 자동 재전송하지 않습니다.
        """
        return self._send(1,code,quantity,price,request_id)

    def _send(self,side,code,quantity,price,request_id,original=''):
        """기능: 공통 주문 전송. 입력: 유형·코드·수량·가격·로컬/원격 ID. 출력: Submission.
        구조·이유: 신규 매수(1), 신규 매도(2), 매수 취소(3)를 같은 서명으로 한 번 전송합니다.
        """
        self._assert_gui()
        if not self.healthy:return Submission('FAILED','키움 세션 종료')
        if int(self.ax.dynamicCall('GetConnectState()'))!=1:return Submission('FAILED','키움 연결 끊김')
        result=self.ax.dynamicCall('SendOrder(QString, QString, QString, int, QString, int, int, QString, QString)',
            ['N'+request_id,'2002',self.account_number,side,code,quantity,price,'00',original])
        if result is None:return Submission('UNKNOWN','주문 반환값 없음: 주문 내역 확인')
        code_result=int(result)
        return Submission('ACCEPTED' if code_result==0 else 'FAILED',f'SendOrder 반환 코드 {code_result}; 전송과 체결을 구분합니다')

    def sell_limit(self,code,quantity,price,request_id):
        """기능: 지정가 매도. 입력: 종목·수량·가격·ID. 출력: Submission.
        구조·이유: 위험 관리기가 재조회한 매도 가능 수량을 사용합니다.
        """
        return self._send(2,code,quantity,price,request_id)

    def cancel_buy(self,code,remote_id,quantity,request_id):
        """기능: 소유 미체결 매수 취소. 입력: 코드·원주문·잔량·ID. 출력: Submission.
        구조·이유: 원주문 번호를 제공하여 새 매도가 아닌 취소 주문을 전송합니다.
        """
        return self._send(3,code,quantity,0,request_id,remote_id)

    def close(self):
        """기능: ActiveX 정리. 입력: 없음. 출력: 없음. 구조·이유: 실행 종료 시 COM 객체를 해제합니다."""
        self.ax.clear()

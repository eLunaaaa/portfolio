"""파일 역할: PC 프로그램의 실행 진입점과 주기적 작업을 조정합니다.
구조: 백그라운드 RSS 수집 + 메인 스레드의 버튼/브로커 처리 + SQLite 기록.
이유: 네트워크 뉴스 대기 중에도 LCD 버튼을 처리하고 ActiveX는 GUI 스레드에서만 호출합니다.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import json
import logging
import math
import os
from pathlib import Path
import re
import sys
import time
from .broker import PaperBroker
from .engine import Engine
from .market import now_kst
from .models import Article, Candidate
from .news import analyze, fetch_news
from .protocol import news_frame, offer_frame, parse_device
from .store import Store

LOG = logging.getLogger('stockauto')


def load_settings(path):
    """기능: 설정 읽기·검증. 입력: JSON 경로. 출력: 검증된 dict.
    구조·이유: 금액·유효시간·핀 프로토콜 범위를 시작 전에 검사해 잘못된 설정의 주문을 막습니다.
    """
    settings = json.loads(Path(path).read_text(encoding='utf-8'))
    for field in ['max_positions','max_order_won','approval_seconds','quote_max_age_seconds','news_max_age_hours','min_news_score','alarm_threshold','poll_seconds','paper_cash_won']:
        if type(settings.get(field)) is not int or settings[field]<=0: raise ValueError(f'{field}: 양의 정수 필요')
    if not 1<=settings['alarm_threshold']<=10 or not 5<=settings['approval_seconds']<=300: raise ValueError('알람 기준은 1~10, 승인시간은 5~300초')
    fee = settings.get('buy_fee_rate')
    if not isinstance(fee,(int,float)) or not math.isfinite(fee) or not 0<=fee<=0.1: raise ValueError('매수 수수료율 범위 오류')
    if settings['mode'] not in ('paper','kiwoom'): raise ValueError('mode는 paper 또는 kiwoom')
    if settings.get('demo',False) and settings['mode']!='paper': raise ValueError('시간 조건을 생략하는 demo는 paper에서만 사용합니다')
    codes = set()
    for stock in settings['watchlist']:
        code = stock['code']
        if not re.fullmatch(r'[0-9]{6}',code) or code in codes: raise ValueError('종목코드는 중복 없는 6자리 문자열')
        if not stock['name'] or not re.fullmatch(r'[A-Za-z0-9 ._-]{1,16}',stock['lcd_name']): raise ValueError('기업명과 LCD용 영문 이름 필요')
        codes.add(code)
    if not codes: raise ValueError('관심종목을 한 개 이상 지정하세요')
    if not isinstance(settings.get('rss_urls'),list) or not 1<=len(settings['rss_urls'])<=5:
        raise ValueError('RSS 주소는 1~5개 설정하세요')
    from urllib.parse import urlsplit
    if any(not isinstance(url,str) or urlsplit(url).scheme!='https' or not urlsplit(url).hostname for url in settings['rss_urls']):
        raise ValueError('RSS 주소는 HTTPS 형식이어야 합니다')
    from datetime import date
    for holiday in settings.get('holidays',[]): date.fromisoformat(holiday)
    for field in ('require_rsi','demo'):
        if field in settings and type(settings[field]) is not bool: raise ValueError(f'{field}: bool 필요')
    if settings.get('strategy_mode') not in ('news','rsi','combined'):raise ValueError('strategy_mode: news/rsi/combined 중 선택')
    for field in ('max_spread_pct','max_day_rise_pct','max_news_rise_pct','min_volume_ratio','stop_loss_pct','take_profit_pct','daily_loss_limit_pct','min_contract_revenue_pct'):
        value=settings.get(field)
        if not isinstance(value,(int,float)) or isinstance(value,bool) or not math.isfinite(value) or value<=0:raise ValueError(field+': 양수 필요')
    for field in ('max_holding_days','cancel_after_seconds','risk_poll_seconds'):
        if type(settings.get(field)) is not int or settings[field]<=0:raise ValueError(field+': 양의 정수 필요')
    for field in ('market_filters','risk_enabled','auto_exit','rsi_exit','require_contract_materiality','manage_existing_positions'):
        if type(settings.get(field)) is not bool:raise ValueError(field+': bool 필요')
    if not settings['stop_loss_pct']<100 or not settings['daily_loss_limit_pct']<100:raise ValueError('손실 비율은 100% 미만이어야 합니다')
    for field in ('sell_fee_rate','sell_tax_rate'):
        value=settings.get(field,0)
        if not isinstance(value,(int,float)) or isinstance(value,bool) or not math.isfinite(value) or not 0<=value<=.1:raise ValueError(field+': 0~0.1 범위')
    for stock in settings['watchlist']:
        value=stock.get('annual_revenue_won',0)
        if type(value) is not int or value<0:raise ValueError('annual_revenue_won: 0 이상 정수')
    return settings


class InstanceLock:
    """동일 DB를 사용하는 프로그램 두 개가 동시에 주문하지 못하게 하는 OS 파일 잠금입니다."""
    def __init__(self, path):
        """기능: 단일 실행 잠금 획득. 입력: 잠금 경로. 출력: 잠금 객체.
        구조·이유: 프로세스가 종료되면 OS가 잠금을 해제하므로 낡은 파일도 재사용 가능합니다.
        """
        self.handle = open(path,'a+b')
        try:
            if os.name=='nt':
                import msvcrt
                self.handle.seek(0); self.handle.write(b'0'); self.handle.flush(); self.handle.seek(0)
                msvcrt.locking(self.handle.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(self.handle.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError:
            self.handle.close(); raise RuntimeError('같은 DB를 사용하는 프로그램이 이미 실행 중입니다')

    def close(self):
        """기능: 실행 잠금 해제. 입력: 없음. 출력: 없음. 이유: 정상 종료의 자원을 반환합니다."""
        self.handle.close()


class Device:
    """USB 시리얼 장치. 연결 직후 READY를 받아야 제안을 표시합니다."""
    def __init__(self, port):
        """기능: USB 연결. 입력: 포트 이름. 출력: 장치 객체.
        구조·이유: 논블로킹 읽기와 길이 제한 버퍼로 PC 루프의 정지를 방지합니다.
        """
        import serial
        self.serial = serial.Serial(port,115200,timeout=0,write_timeout=1)
        self.buffer, self.dropping, self.ready = bytearray(), False, False

    def send(self, frame):
        """기능: 프레임 전송. 입력: ASCII bytes. 출력: 없음 또는 예외.
        구조·이유: 일부만 전송된 경우에도 제안 성공으로 처리하지 않습니다.
        """
        if self.serial.write(frame)!=len(frame): raise IOError('시리얼 프레임 일부만 전송됨')

    def messages(self):
        """기능: 수신 줄 분리. 입력: 시리얼 버퍼. 출력: 검증된 메시지 목록.
        구조·이유: 최대 4KB씩 읽고 과대 프레임은 줄 끝까지 버립니다.
        """
        result=[]
        for byte in self.serial.read(min(self.serial.in_waiting,4096)):
            if byte==10:
                if not self.dropping:
                    value = parse_device(bytes(self.buffer))
                    if value: result.append(value)
                self.buffer.clear(); self.dropping=False
            elif not self.dropping:
                if len(self.buffer)>=80: self.buffer.clear(); self.dropping=True
                else: self.buffer.append(byte)
        return result

    def close(self):
        """기능: USB 연결 해제. 입력: 없음. 출력: 없음. 이유: 다음 실행이 포트를 다시 열 수 있게 합니다."""
        self.serial.close()


class Runner:
    """뉴스 수집 결과와 Arduino 입력을 주기적으로 연결하는 실행 조정자."""
    def __init__(self, settings, engine, device, demo=False):
        """기능: 작업 상태 초기화. 입력: 설정·엔진·장치·예제 여부. 출력: Runner.
        구조·이유: 수집 worker는 뉴스만 처리하고 주문은 tick에서 직렬 처리합니다.
        """
        self.settings,self.engine,self.device,self.demo=settings,engine,device,demo
        self.pool=ThreadPoolExecutor(max_workers=1)
        self.future=None; self.last_fetch=-float('inf'); self.queue=[]; self.notified=set()
        self.next_candidate=0; self.busy=False; self.failed=False; self.last_ping=time.monotonic()
        self.last_risk=-float("inf")
        self.attempt_after={}

    def _collect(self):
        """기능: 뉴스 수집 작업. 입력: 실행 설정. 출력: 기사·오류 목록.
        구조·이유: 예제는 명확히 가상 기사로 만들고 운영은 설정한 RSS를 읽습니다.
        """
        now=self.engine.clock()
        if self.demo:
            title='가상전자 사상 최대 실적, 신규 공급계약 수주'
            return [Article('demo-news-001',title,'https://example.com/demo-news',now-timedelta(minutes=1))],[]
        if self.settings['strategy_mode']=='rsi':return [],[]
        return fetch_news(self.settings['rss_urls'],now,self.settings['news_max_age_hours'])

    def tick(self):
        """기능: 한 번의 주기 처리. 입력: 없음. 출력: LCD/DB/콘솔 갱신.
        구조·이유: 중첩 Qt 이벤트로 재진입해도 busy가 주문 경로의 동시 실행을 차단합니다.
        """
        if self.busy or self.failed:return
        self.busy=True
        try:self._tick()
        except (OSError, IOError):
            self.engine.disconnect()
            self.device.ready=False
            self.failed=True
            LOG.exception('장치 연결 오류: 현재 제안 만료, 프로그램 종료')
        except Exception:LOG.exception('작업 실패: 주문은 자동 재전송하지 않습니다')
        finally:self.busy=False

    def _tick(self):
        """기능: 수집·버튼·표시 순서 제어. 입력: 내부 상태. 출력: 다음 상태.
        구조·이유: 버튼/만료를 먼저 처리한 후 새 제안을 표시해 이전 제안 승인을 방지합니다.
        """
        if self.settings.get('risk_enabled',False) and time.monotonic()-self.last_risk>=self.settings['risk_poll_seconds']:
            self.last_risk=time.monotonic()
            try:
                for reason,code,state in self.engine.risk.tick():LOG.info('주문 관리: %s %s %s',reason,code,state)
            except Exception:LOG.exception('위험 관리 조회 실패: 다음 주기에 재조회')
        for kind,offer_id,decision in self.device.messages():
            if kind=='READY':
                self.engine.disconnect(); self.device.ready=True
                self.device.send(b'RESET\n'); self.last_ping=time.monotonic()
            elif kind=='PING':
                self.last_ping=time.monotonic()
            elif kind=='DECIDE' and self.device.ready:
                state,detail=self.engine.decide(offer_id,decision)
                LOG.info('버튼 처리: %s / %s',state,detail)
                # 낡은/잡음 프레임으로 현재 화면을 덮어쓰지 않습니다.
                if state!='IGNORED':self.device.send(f'RESULT|{offer_id}|{state}\n'.encode('ascii'))
        expired=self.engine.expire()
        if expired:self.device.send(f'RESULT|{expired}|EXPIRED\n'.encode('ascii'))
        if self.device.ready and time.monotonic()-self.last_ping>8:
            self.engine.disconnect(); self.device.ready=False
            LOG.error('Arduino 응답 중단: 재시작이 필요합니다')
        if self.future and self.future.done():
            articles,errors=self.future.result();self.future=None
            for error in errors:LOG.warning('RSS 수집: %s',error)
            if self.settings['strategy_mode']=='rsi':
                # 뉴스 전략과 독립적으로 RSI 후보를 생성합니다. 하루 한 번의 종목별 승인 단위입니다.
                now=self.engine.clock()
                self.queue=[Candidate('rsi:'+stock['code']+':'+now.date().isoformat(),stock['code'],stock['name'],self.settings['min_news_score'],0,'RSI(2) TECHNICAL',stock['lcd_name']+' RSI REVIEW',Article('rsi', 'RSI 기술 조건 검사','urn:stockauto:rsi',now)) for stock in self.settings['watchlist']]
            else:self.queue=analyze(articles,self.settings['watchlist'],self.settings['min_news_score'])
            self.queue=[self.engine.normalize(candidate) for candidate in self.queue]
        if self.future is None and time.monotonic()-self.last_fetch>=self.settings['poll_seconds']:
            self.last_fetch=time.monotonic();self.future=self.pool.submit(self._collect)
        if not self.device.ready or self.engine.active or time.monotonic()<self.next_candidate:return
        while self.queue:
            candidate=self.engine.normalize(self.queue.pop(0))
            if self.engine.store.seen(candidate.key):continue
            already_notified=candidate.key in self.notified
            if already_notified and (not candidate.confirmed or candidate.score<self.settings['min_news_score']):continue
            if time.monotonic()<self.attempt_after.get(candidate.key,0):continue
            LOG.info('뉴스: %s | %s | 점수=%d 중요도=%d | %s | %s',candidate.name,candidate.article.title,candidate.score,candidate.importance,candidate.reason,candidate.article.link)
            self.notified.add(candidate.key)
            self.attempt_after[candidate.key]=time.monotonic()+self.settings['poll_seconds']
            try:offer=self.engine.propose(candidate)
            except Exception as error:
                LOG.info('매수 제안 생략: %s',error);offer=None
            if offer:
                self.device.send(offer_frame(offer,self.settings['alarm_threshold'],self.engine.clock()))
                LOG.info('승인 대기: %s %d주 × %d원 (ID %s)',candidate.code,offer.quantity,offer.price,offer.id)
            elif not already_notified:self.device.send(news_frame(candidate,self.settings['alarm_threshold']))
            self.next_candidate=time.monotonic()+8
            return

    def close(self):
        """기능: 작업 종료. 입력: 없음. 출력: 없음.
        구조·이유: 대기 제안을 만료하고 수집 작업과 USB 연결을 정리합니다.
        """
        self.engine.disconnect();self.pool.shutdown(wait=True,cancel_futures=True);self.device.close()


def main(argv=None):
    """기능: CLI 실행. 입력: 설정/포트/예제 옵션. 출력: 종료 코드.
    구조·이유: paper와 kiwoom의 연결 환경을 분리하고 한 프로세스만 DB를 사용합니다.
    """
    parser=argparse.ArgumentParser(description='뉴스→LCD→버튼 승인 주식 주문')
    parser.add_argument('--config',default='config/settings.json')
    parser.add_argument('--port',required=True)
    parser.add_argument('--strategy',choices=['news','rsi','combined'],help='뉴스/RSI/결합 조건 선택')
    parser.add_argument('--demo',action='store_true',help='가상 뉴스·종목과 paper 주문 사용')
    args=parser.parse_args(argv)
    settings=load_settings(args.config)
    if args.strategy:settings=dict(settings,strategy_mode=args.strategy,require_rsi=False)
    if args.demo:
        if settings['mode']!='paper':raise ValueError('--demo는 paper 모드에서만 사용합니다')
        settings=dict(settings,demo=True,watchlist=[dict(code='000001',name='가상전자',lcd_name='DEMO ELEC',aliases=[])],paper_quotes={'000001':{'ask':10000}})
    Path('runtime').mkdir(exist_ok=True)
    logging.basicConfig(level=logging.INFO,format='%(asctime)s %(levelname)s %(message)s',handlers=[logging.StreamHandler(),logging.FileHandler('runtime/app.log',encoding='utf-8')])
    db_path=Path(settings.get('database','runtime/orders.db')).resolve();db_path.parent.mkdir(parents=True,exist_ok=True)
    lock=InstanceLock(str(db_path)+'.lock')
    store=None;device=None;broker=None;runner=None;qt_app=None
    try:
        store=Store(db_path)
        if settings['mode']=='kiwoom':
            from PyQt5.QtWidgets import QApplication
            from PyQt5.QtCore import QTimer
            from .kiwoom import KiwoomBroker
            qt_app=QApplication(sys.argv)
            broker=KiwoomBroker(settings,now_kst)
        else:broker=PaperBroker(settings,now_kst)
        device=Device(args.port)
        runner=Runner(settings,Engine(broker,store,settings,now_kst),device,args.demo)
        LOG.info('실행 모드: %s / 매수 승인 버튼 필요 / 알람 기준: %d',settings['mode'],settings['alarm_threshold'])
        if qt_app:
            timer=QTimer();timer.timeout.connect(runner.tick)
            timer.timeout.connect(lambda: qt_app.exit(1) if runner.failed else None);timer.start(50)
            return qt_app.exec_()
        while not runner.failed:runner.tick();time.sleep(0.05)
        return 1
    except KeyboardInterrupt:return 0
    finally:
        if runner:runner.close()
        elif device:device.close()
        if broker and hasattr(broker,'close'):broker.close()
        if store:store.close()
        lock.close()

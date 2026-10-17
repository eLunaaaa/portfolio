"""파일 역할: 승인·거절·주문 상태를 SQLite에 영구 기록합니다.
구조: 후보 키는 UNIQUE, 제안 ID는 PRIMARY KEY, 상태 변경은 조건부 UPDATE입니다.
이유: 버튼 중복, 프로세스 재시작, 주문 도중 중단에도 같은 제안을 재주문하지 않습니다.
"""
import json
import sqlite3
import uuid
from datetime import datetime, timedelta
from dataclasses import asdict


class Store:
    """단일 PC 프로세스의 제안 상태와 감사 기록을 관리합니다."""
    def __init__(self, path):
        """기능: DB 초기화. 입력: 파일 경로. 출력: 연결 객체.
        구조·이유: WAL과 즉시 커밋으로 주문 전 상태 기록을 디스크에 남깁니다.
        """
        self.db = sqlite3.connect(path, timeout=5)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('CREATE TABLE IF NOT EXISTS offers (id TEXT PRIMARY KEY, candidate_key TEXT UNIQUE NOT NULL, code TEXT NOT NULL, state TEXT NOT NULL, payload TEXT NOT NULL, detail TEXT NOT NULL DEFAULT "")')
        self.db.execute('CREATE TABLE IF NOT EXISTS events (id TEXT PRIMARY KEY, code TEXT NOT NULL, signature TEXT NOT NULL, first_at TEXT NOT NULL, last_at TEXT NOT NULL, first_price INTEGER NOT NULL DEFAULT 0)')
        self.db.execute('CREATE TABLE IF NOT EXISTS days (day TEXT PRIMARY KEY, baseline INTEGER NOT NULL, halted INTEGER NOT NULL DEFAULT 0)')
        self.db.execute('CREATE TABLE IF NOT EXISTS positions (code TEXT PRIMARY KEY, tracked_at TEXT NOT NULL)')
        self.db.execute('CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, kind TEXT NOT NULL, code TEXT NOT NULL, quantity INTEGER NOT NULL, price INTEGER NOT NULL, reason TEXT NOT NULL, created_at TEXT NOT NULL, state TEXT NOT NULL, remote_id TEXT NOT NULL DEFAULT "", parent TEXT NOT NULL DEFAULT "")')
        if 'detail' not in {row[1] for row in self.db.execute('PRAGMA table_info(jobs)')}:
            self.db.execute('ALTER TABLE jobs ADD COLUMN detail TEXT NOT NULL DEFAULT ""')
        self.db.execute("UPDATE jobs SET state='UNKNOWN' WHERE state='SUBMITTING'")
        # 이전 실행에서 전송 도중 종료했다면 결과 미확정으로 남겨 자동 재전송을 막습니다.
        self.db.execute("UPDATE offers SET state='UNKNOWN' WHERE state='SUBMITTING'")
        self.db.execute("UPDATE offers SET state='EXPIRED' WHERE state='OFFERED'")
        self.db.commit()

    def seen(self, key):
        """기능: 처리한 후보 조회. 입력: 후보 키. 출력: bool.
        구조·이유: 뉴스 재수집으로 거절·승인한 항목을 다시 제시하지 않습니다.
        """
        return self.db.execute('SELECT 1 FROM offers WHERE candidate_key=?', (key,)).fetchone() is not None

    def add(self, offer):
        """기능: 제안 저장. 입력: Offer. 출력: 중복 없이 저장했는지 bool.
        구조·이유: UNIQUE 제약으로 동시에 같은 기사를 제시하는 경우도 차단합니다.
        """
        try:
            with self.db:
                self.db.execute('INSERT INTO offers(id,candidate_key,code,state,payload) VALUES(?,?,?,"OFFERED",?)',
                    (offer.id, offer.candidate.key, offer.candidate.code, json.dumps(asdict(offer), default=str, ensure_ascii=False)))
            return True
        except sqlite3.IntegrityError: return False

    def transition(self, offer_id, previous, state, detail=''):
        """기능: 상태 비교 후 변경. 입력: ID·기대 상태·다음 상태·사유. 출력: bool.
        구조·이유: 이미 처리한 버튼은 기대 상태가 달라 두 번 주문되지 않습니다.
        """
        with self.db:
            result = self.db.execute('UPDATE offers SET state=?,detail=? WHERE id=? AND state=?', (state,detail,offer_id,previous))
        return result.rowcount == 1

    def locked_codes(self):
        """기능: 주문 결과 확인이 필요한 종목 조회. 입력: 없음. 출력: 코드 set.
        구조·이유: 전송 중·전송 성공·결과 미확정은 다른 뉴스에서도 중복 매수를 막습니다.
        """
        return {r[0] for r in self.db.execute("SELECT code FROM offers WHERE state IN ('SUBMITTING','ACCEPTED','UNKNOWN')")}

    def reservations(self):
        """기능: 아직 주문 확인이 필요한 금액 조회. 입력: 없음. 출력: (종목, 수량, 가격) 목록.
        구조·이유: 주문 직후 서버 잔고 반영이 늦어도 다른 종목에 같은 돈을 다시 쓰지 않습니다.
        """
        rows = self.db.execute("SELECT code,payload FROM offers WHERE state IN ('SUBMITTING','ACCEPTED','UNKNOWN')")
        return [(code,json.loads(payload)['quantity'],json.loads(payload)['price']) for code,payload in rows]

    def observe_event(self,candidate,now):
        """기능: 같은 사건을 통합. 입력: 후보·관측시각. 출력: 대표 사건 ID·최초 보도시각.
        구조·이유: 48시간 내 같은 종목/사건 특징을 비교하고 재시작 후에도 최초 시각을 보존합니다.
        """
        from .news import same_event
        signature=candidate.event_group
        if not signature:return candidate.key,candidate.article.published_at
        rows=self.db.execute('SELECT id,signature,first_at,last_at FROM events WHERE code=?',(candidate.code,)).fetchall()
        for event_id,other,first,last in rows:
            # 최신 정정/악재는 labels가 다르므로 호재 사건에 합쳐지지 않습니다.
            if abs((candidate.article.published_at-datetime.fromisoformat(first)).total_seconds())<=48*3600 and same_event(signature,other):
                first=min(datetime.fromisoformat(first),candidate.article.published_at)
                with self.db:self.db.execute('UPDATE events SET first_at=?,last_at=? WHERE id=?',(first.isoformat(),now.isoformat(),event_id))
                return event_id,first
        event_id=uuid.uuid4().hex
        with self.db:self.db.execute('INSERT INTO events(id,code,signature,first_at,last_at) VALUES(?,?,?,?,?)',(event_id,candidate.code,signature,candidate.article.published_at.isoformat(),now.isoformat()))
        return event_id,candidate.article.published_at

    def event_price(self,event_id,price):
        """기능: 사건 최초 관측 호가 유지. 입력: 사건 ID·현재 호가. 출력: 최초 호가.
        구조·이유: 과거 게시 시각의 가격을 추측하지 않고 실제 처음 수집한 가격과 비교합니다.
        """
        with self.db:self.db.execute('UPDATE events SET first_price=? WHERE id=? AND first_price=0',(price,event_id))
        row=self.db.execute('SELECT first_price FROM events WHERE id=?',(event_id,)).fetchone()
        return row[0] if row else price

    def daily_halt(self,now,equity,limit_pct):
        """기능: 당일 자산 손실 한도 유지. 입력: 시각·평가자산·비율. 출력: 중단 bool.
        구조·이유: 재시작해도 기준이 초기화되지 않으며 한도 도달은 그날 계속 유지됩니다.
        """
        from .market import KST
        day=now.astimezone(KST).date().isoformat()
        with self.db:
            self.db.execute('INSERT OR IGNORE INTO days(day,baseline) VALUES(?,?)',(day,equity))
            baseline,halted=self.db.execute('SELECT baseline,halted FROM days WHERE day=?',(day,)).fetchone()
            halted=bool(halted) or equity<=baseline*(1-limit_pct/100)
            if halted:self.db.execute('UPDATE days SET halted=1 WHERE day=?',(day,))
        return halted

    def tracked_since(self,code,now):
        """기능: 포지션 최초 추적시각. 입력: 종목·시각. 출력: 저장된 datetime.
        구조·이유: 실제 매입일이 없는 기존 잔고도 재시작 시 보유기간이 매번 초기화되지 않습니다.
        """
        with self.db:self.db.execute('INSERT OR IGNORE INTO positions VALUES(?,?)',(code,now.isoformat()))
        return datetime.fromisoformat(self.db.execute('SELECT tracked_at FROM positions WHERE code=?',(code,)).fetchone()[0])

    def open_jobs(self):
        """기능: 원격 주문 확인 대상 조회. 입력: 없음. 출력: 작업 dict 목록.
        구조·이유: 전송 성공과 미확정 상태를 구분하고 증권사 주문번호를 연결합니다.
        """
        rows=self.db.execute("SELECT id,kind,code,quantity,price,reason,created_at,state,remote_id,parent FROM jobs WHERE state IN ('SUBMITTING','ACCEPTED','UNKNOWN')").fetchall()
        keys=('id','kind','code','quantity','price','reason','created_at','state','remote_id','parent')
        return [dict(zip(keys,row)) for row in rows]

    def link_order(self,job_id,remote_id):
        """기능: 원격 주문번호 저장. 입력: 로컬/원격 ID. 출력: 없음.
        구조·이유: 재시작 후에도 다른 수동 주문을 취소하지 않고 본인 요청을 식별합니다.
        """
        with self.db:self.db.execute('UPDATE jobs SET remote_id=? WHERE id=?',(remote_id,job_id))

    def start_buy(self, offer, now):
        """기능: 승인 상태와 매수 원장을 한 트랜잭션으로 선기록합니다.
        입력: 화면에서 승인한 Offer·현재시각. 출력: 새 전송 권한을 얻었으면 True.
        구조: OFFERED→SUBMITTING 조건부 변경 후 jobs INSERT를 같은 commit에 묶습니다.
        이유: 원장 저장 실패 시 상태 변경도 rollback하고, 이미 처리한 승인은 재전송하지 않습니다.
        실제 증권사 호출은 이 commit이 성공한 뒤 Engine에서 수행합니다.
        """
        with self.db:
            changed = self.db.execute(
                "UPDATE offers SET state='SUBMITTING',detail=? WHERE id=? AND state='OFFERED'",
                ('사용자 매수 승인', offer.id),
            )
            if changed.rowcount != 1:
                return False
            self.db.execute(
                'INSERT INTO jobs(id,kind,code,quantity,price,reason,created_at,state) VALUES(?,?,?,?,?,?,?,?)',
                (offer.id, 'BUY', offer.candidate.code, offer.quantity, offer.price,
                 'USER_APPROVED', now.isoformat(), 'SUBMITTING'),
            )
        return True

    def reconcile_jobs(self,broker,account):
        """기능: 명시적 증권사 완료 응답으로 원장 정리. 입력: 브로커·현재 계좌. 출력: 없음.
        구조·이유: 단순한 목록 누락으로는 완료 처리하지 않고, 매수 체결은 잔고 반영도 확인합니다.
        """
        for job in self.open_jobs():
            if not job['remote_id']:continue
            status=broker.order_status(job['remote_id'])
            if status not in ('FILLED','CANCELLED','FAILED'):continue
            if job['kind']=='BUY' and status=='FILLED' and job['code'] not in account.holdings:continue
            if job['kind']=='BUY' and status=='CANCELLED' and job['code'] in account.holdings:
                status='FILLED'  # 일부 체결 후 잔량 취소: 실제 잔고 수량은 계속 관리합니다.
            with self.db:
                self.db.execute('UPDATE jobs SET state=? WHERE id=?',(status,job['id']))
                self.db.execute('UPDATE offers SET state=? WHERE id=?',(status,job['id']))
        # 한 번 잔고로 확인한 종목이 전량 사라진 뒤에만 추적/매수 잠금을 닫습니다.
        for (code,) in self.db.execute('SELECT code FROM positions').fetchall():
            if code not in account.holdings and code not in account.pending:
                with self.db:
                    self.db.execute("UPDATE jobs SET state='CLOSED' WHERE code=? AND kind='BUY' AND state='FILLED'",(code,))
                    self.db.execute('DELETE FROM positions WHERE code=?',(code,))

    def managed_codes(self):
        """기능: 이 프로그램에서 매수한 종목 조회. 입력: 없음. 출력: 종목 set.
        구조·이유: 자동 청산이 사용자의 다른 수동 보유종목을 관리 대상으로 삼지 않습니다.
        """
        return {row[0] for row in self.db.execute("SELECT code FROM jobs WHERE kind='BUY' AND state IN ('ACCEPTED','UNKNOWN','FILLED')")}

    def has_exit(self,code):
        """기능: 기존 매도 작업 조회. 입력: 종목. 출력: bool.
        구조·이유: 체결 여부를 모르는 매도 요청을 반복 전송하지 않습니다.
        """
        return any(job['kind']=='SELL' and job['code']==code for job in self.open_jobs())

    def start_exit(self,code,quantity,price,reason,now):
        """기능: 청산 요청 선기록. 입력: 종목·수량·단가·사유·시각. 출력: ID 또는 None.
        구조·이유: 중복 검사 후 SUBMITTING을 커밋하고 실제 주문은 이후 전송합니다.
        """
        if self.has_exit(code):return None
        job_id=uuid.uuid4().hex[:16]
        with self.db:self.db.execute('INSERT INTO jobs(id,kind,code,quantity,price,reason,created_at,state) VALUES(?,?,?,?,?,?,?,?)',(job_id,'SELL',code,quantity,price,reason,now.isoformat(),'SUBMITTING'))
        return job_id

    def start_cancel(self,parent,now):
        """기능: 미체결 취소 선기록. 입력: 매수 작업 ID·시각. 출력: 취소 ID 또는 None.
        구조·이유: 취소 응답 미확정 때 원주문을 다시 취소하지 않습니다.
        """
        if self.db.execute("SELECT 1 FROM jobs WHERE parent=? AND state!='FAILED'",(parent,)).fetchone():return None
        row=self.db.execute('SELECT code FROM jobs WHERE id=?',(parent,)).fetchone()
        job_id=uuid.uuid4().hex[:16]
        with self.db:self.db.execute('INSERT INTO jobs(id,kind,code,quantity,price,reason,created_at,state,parent) VALUES(?,?,?,?,?,?,?,?,?)',(job_id,'CANCEL',row[0],0,0,'TIMEOUT_OR_DAILY_LOSS',now.isoformat(),'SUBMITTING',parent))
        return job_id

    def finish_job(self,job_id,state,detail=''):
        """기능: 주문 작업 결과 저장. 입력: ID·상태·설명. 출력: 없음.
        구조·이유: 매수 제안의 상태와 관리 원장의 상태를 함께 갱신합니다.
        """
        if state not in ('ACCEPTED','FAILED','UNKNOWN'):state='UNKNOWN'
        with self.db:
            self.db.execute('UPDATE jobs SET state=?,detail=? WHERE id=?',(state,detail,job_id))
            self.db.execute("UPDATE offers SET state=?,detail=? WHERE id=? AND state='SUBMITTING'",(state,detail,job_id))

    def state(self, offer_id):
        """기능: 상태 확인. 입력: 제안 ID. 출력: 상태 문자열 또는 None.
        구조·이유: 화면 표시와 테스트가 DB를 통해 동일한 상태를 확인합니다.
        """
        row = self.db.execute('SELECT state FROM offers WHERE id=?',(offer_id,)).fetchone()
        return row[0] if row else None

    def close(self):
        """기능: DB 닫기. 입력: 없음. 출력: 없음. 이유: 종료 시 파일 핸들을 정리합니다."""
        self.db.close()

"""파일 역할: 실제 Qt 타이머·중첩 이벤트 루프에서 조회 중 거절/정정/만료를 검사합니다.
입력: 임시 계좌·가상 USB·QCoreApplication. 출력: 3개 통합 PASS.
이유: busy 플래그만 검사하는 대신 실제 QEventLoop 대기 중 제어 타이머 실행을 확인합니다.
"""
from pathlib import Path
import sys
import time
from concurrent.futures import Future
from dataclasses import replace
from datetime import timedelta
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PyQt5.QtCore import QCoreApplication, QEventLoop, QTimer
import test_system as base
from test_lifecycle import Device
from stockauto.app import Runner


def check_scenario(kind):
    """입력: NO/NEWS/EXPIRE. 출력: 검증 또는 예외.
    구조: 승인 후 호가 함수를 150ms Qt 대기로 대체→10ms I/O timer→30ms 상태 변경.
    이유: 조회가 끝나기 전에 취소가 반영되고 반환 뒤 매수가 발생하지 않는지 확인합니다.
    """
    fixture=base.SystemTests(methodName='runTest');fixture.setUp()
    offer=fixture.engine.propose(fixture.candidate)
    device=Device();runner=Runner(fixture.settings,fixture.engine,device)
    runner.last_fetch=runner.last_risk=time.monotonic()
    original=fixture.broker.quote;handled=[]
    io=QTimer();io.timeout.connect(runner.service_io);io.start(10)
    def mutate():
        """입력: Qt 시간 경과. 출력: 거절/새 뉴스/시계 만료 주입. 이유: 실제 이벤트 순서를 재현합니다."""
        if kind=='NO':device.incoming=[('DECIDE',offer.id,'NO')]
        elif kind=='NEWS':
            runner.future=Future();runner.future.set_result(([replace(fixture.article,title='삼성전자 공급계약 해지 공시')],[]))
        else:fixture.now+=timedelta(seconds=91)
    def delayed_quote(code):
        """입력: 종목. 출력: 지연된 호가. 이유: 실제 Qt 대기 중에도 I/O가 계속 실행되어야 합니다."""
        loop=QEventLoop();QTimer.singleShot(150,loop.quit);QTimer.singleShot(30,mutate)
        loop.exec_()
        handled.append(fixture.store.state(offer.id))
        return original(code)
    fixture.broker.quote=delayed_quote
    try:
        runner.busy=True  # 실제 주문 재검사 동안의 재진입 보호 상태
        fixture.engine.decide(offer.id,'BUY')
        expected='EXPIRED' if kind=='EXPIRE' else 'REJECTED'
        assert handled==[expected],(kind,handled)
        assert not fixture.broker.orders
        assert any(expected.encode() in frame for frame in device.sent)
    finally:
        io.stop();runner.close();fixture.tearDown()


def main():
    """입력: 없음. 출력: Qt 통합 PASS. 이유: QApplication 화면·증권사 없이 실제 이벤트 루프를 검사합니다."""
    app=QCoreApplication.instance() or QCoreApplication([])
    for kind in ('NO','NEWS','EXPIRE'):check_scenario(kind)
    print('Qt TR wait: NO / news revocation / expiry 3 scenarios PASS')
    return app


if __name__=='__main__':main()

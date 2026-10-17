"""파일 역할: Arduino 없이 뉴스→제안→선택→로컬 주문 결과를 확인하는 예제입니다.
입력: --decision BUY 또는 NO. 출력: 뉴스 근거·LCD 프레임·처리 상태.
구조·이유: 가상 종목과 PaperBroker만 생성하여 화면에 전달할 데이터를 바로 확인합니다.
"""
import argparse
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from stockauto.app import load_settings
from stockauto.broker import PaperBroker
from stockauto.engine import Engine
from stockauto.market import now_kst
from stockauto.models import Article
from stockauto.news import analyze
from stockauto.protocol import offer_frame
from stockauto.store import Store


def main():
    """기능: 로컬 통합 시연. 입력: 선택 인자. 출력: 상태와 로컬 주문 수.
    구조·이유: 같은 뉴스 분석·상태 머신을 사용하면서 외부 계좌와 USB는 생성하지 않습니다.
    """
    parser=argparse.ArgumentParser();parser.add_argument('--decision',choices=['BUY','NO'],default='NO');args=parser.parse_args()
    root=Path(__file__).resolve().parents[1]
    settings=load_settings(root/'config/settings.json')
    settings.update(mode='paper',demo=True,watchlist=[dict(code='000001',name='가상전자',lcd_name='DEMO ELEC',aliases=[])],paper_quotes={'000001':{'ask':10000}})
    store=Store(':memory:')
    try:
        broker=PaperBroker(settings,now_kst);engine=Engine(broker,store,settings,now_kst)
        article=Article('demo','가상전자 사상 최대 실적, 공급계약 수주','https://example.com/demo',now_kst())
        candidate=analyze([article],settings['watchlist'])[0]
        offer=engine.propose(candidate)
        print('가상 뉴스:',article.title)
        print('분석 근거:',candidate.reason,'/ 중요도:',candidate.importance)
        print('LCD 전송:',offer_frame(offer,settings['alarm_threshold'],now_kst()).decode().strip())
        print('선택 결과:',engine.decide(offer.id,args.decision))
        print('로컬 주문 건수:',len(broker.orders))
    finally:store.close()


if __name__=='__main__':main()

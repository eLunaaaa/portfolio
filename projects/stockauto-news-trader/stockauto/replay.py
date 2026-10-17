"""파일 역할: 과거 시계열을 시간순 재생해 news/rsi/combined 기준을 같은 조건에서 비교합니다.
입력: bars/news JSON. 출력: 수수료·슬리피지 포함 비교 지표.
구조·이유: 신호는 현재 종가까지로 계산하고 체결은 다음 봉 시가로 처리해 미래 가격 참조를 막습니다.
"""
import argparse
from dataclasses import replace
from datetime import datetime, timedelta
import json
import math
from pathlib import Path
from .market import estimated_cost, can_sell, KST
from .models import Article, Position, Quote, Account, Candidate
from .news import analyze
from .store import Store
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
from .risk import exit_reason
from .strategy import build_intent


def validate_dataset(data):
    """기능: 재생 데이터 검사. 입력: dict. 출력: 시간순 봉·뉴스.
    구조·이유: 날짜·코드 중복과 비정상 가격을 시작 전에 거부하며 timezone 없는 날짜를 허용하지 않습니다.
    """
    bars=[];seen=set()
    for raw in data['bars']:
        row=dict(raw);stamp=datetime.fromisoformat(row['at'])
        if stamp.tzinfo is None:raise ValueError('bars.at에는 timezone이 필요합니다')
        row['at']=stamp
        key=(stamp,row['code'])
        if key in seen:raise ValueError('같은 시각/코드의 중복 봉')
        seen.add(key)
        for field in ('open','close'):
            if type(row[field]) is not int or row[field]<=0:raise ValueError('봉 가격은 양의 정수')
        ratio = row.get('volume_ratio', 1)
        if isinstance(ratio, bool) or not isinstance(ratio, (int, float)) or not math.isfinite(ratio) or ratio <= 0:
            raise ValueError('volume_ratio는 유한한 양수')
        for field in ('ask', 'bid'):
            if field in row and (type(row[field]) is not int or row[field] <= 0):
                raise ValueError('호가는 양의 정수')
        if 'ask' in row and 'bid' in row and row['bid'] > row['ask']:
            raise ValueError('매수호가는 매도호가 이하여야 합니다')
        bars.append(row)
    news=[]
    for i,raw in enumerate(data.get('news',[])):
        stamp=datetime.fromisoformat(raw['published_at'])
        if stamp.tzinfo is None:raise ValueError('뉴스 시각에는 timezone이 필요합니다')
        news.append(Article(str(i),raw['title'],raw.get('link','https://example.com/replay'),stamp))
    return sorted(bars,key=lambda r:(r['at'],r['code'])),news


def replay(data,settings,mode):
    """기능: 단일 전략 재생. 입력: 봉/뉴스·설정·모드. 출력: 지표 dict.
    구조·이유: 버튼 승인은 즉시 수락한다고 가정하고, 매수·매도 모두 다음 봉 시가에 비용을 적용합니다.
    데이터의 synthetic 표시는 결과에 유지하므로 예제 결과가 실제 성과로 혼동되지 않습니다.
    """
    bars,articles=validate_dataset(data)
    settings = dict(settings, strategy_mode=mode, require_rsi=False)
    history={};positions={};pending={};used_news=set();last_prices={}
    events=Store(":memory:")
    cash=settings['paper_cash_won'];start=cash;curve=[];trades=[];fills=[];signals=[]
    fee=settings['buy_fee_rate'];sell_fee=settings.get('sell_fee_rate',0)+settings.get('sell_tax_rate',0)
    slip=settings.get('replay_slippage_bps',10)/10000
    daily={};halted=set()
    risk_enabled = settings.get('risk_enabled', False)
    exit_enabled = risk_enabled and settings.get('auto_exit', False)
    groups={}
    for row in bars:groups.setdefault(row['at'],[]).append(row)
    for at,rows in groups.items():
        # 같은 시각의 모든 시가를 먼저 반영한 뒤 이전 봉에서 예약한 주문을 체결합니다.
        for row in rows:last_prices[row['code']]=row['open']
        day=at.astimezone(KST).date().isoformat()
        daily.setdefault(day,cash+sum(p['qty']*last_prices[code] for code,p in positions.items()))
        for row in rows:
            code=row['code'];job=pending.pop(code,None)
            if not settings.get('demo', False) and not can_sell(at, settings.get('holidays', [])):
                if job: pending[code] = job
                continue
            visible = [article for article in articles if article.published_at <= at]
            for candidate in analyze(visible, settings['watchlist'], settings['min_news_score']):
                events.observe_news(candidate)
            if job and job['side'] == 'BUY' and mode != 'rsi' and events.news_blocked(job['candidate']):
                continue
            if job and job['side']=='SELL' and code in positions:
                price=max(1,int((Decimal(row['open'])*(1-Decimal(str(slip)))).to_integral_value(rounding=ROUND_FLOOR)));p=positions.pop(code)
                if price < job['limit']:
                    positions[code] = p; pending[code] = job
                    continue
                proceeds=int(p['qty']*price*(1-sell_fee));cash+=proceeds
                trades.append(dict(code=code,pnl_won=proceeds-p['cost'],reason=job['reason']))
                fills.append(dict(at=at.isoformat(),code=code,side='SELL',quantity=p['qty'],price=price,signal_at=job['at']))
            elif job and job['side']=='BUY' and code not in positions and day not in halted:
                price=max(1,int((Decimal(row['open'])*(1+Decimal(str(slip)))).to_integral_value(rounding=ROUND_CEILING)))
                if price>job['limit']:continue
                if len(positions)>=settings['max_positions']:continue
                # 승인 시 고정한 수량을 유지합니다. 더 싼 다음 봉 가격으로 수량을 늘리지 않습니다.
                qty = job['quantity']
                cost = estimated_cost(qty, price, fee)
                if cost <= cash:
                    cash -= cost
                    positions[code] = dict(qty=qty, price=price, cost=cost, opened=at)
                    fills.append(dict(at=at.isoformat(), code=code, side='BUY', quantity=qty,
                                      price=price, signal_at=job['at']))
        for row in rows:last_prices[row['code']]=row['close']
        equity=cash+sum(p['qty']*last_prices[code] for code,p in positions.items());curve.append(equity)
        if risk_enabled and equity<=daily[day]*(1-settings['daily_loss_limit_pct']/100):halted.add(day)
        for row in rows:
            code=row['code'];previous=history.setdefault(code,[])
            quote=Quote(row.get('ask',max(1,int(row['close']*1.001))),at,tuple(previous),row.get('bid',max(1,int(row['close']*.999))),row['close'],previous[-1] if previous else row['close'],row.get('volume_ratio',1.0),at)
            if exit_enabled and code in positions and code not in pending and (settings.get('demo', False) or can_sell(at, settings.get('holidays', []))):
                p=positions[code];pos=Position(code,p['qty'],p['price'],p['qty'],p['opened'])
                reason='DAILY_LOSS' if day in halted else exit_reason(pos,quote,settings,at,p['opened'])
                if reason:pending[code]=dict(side='SELL',reason=reason,at=at.isoformat(),limit=quote.bid)
            if code not in positions and code not in pending and day not in halted:
                visible = [article for article in articles if at-timedelta(hours=settings['news_max_age_hours']) <= article.published_at <= at]
                candidates = analyze(visible, settings['watchlist'], settings['min_news_score'])
                fresh = []
                for candidate in candidates:
                    events.observe_news(candidate)
                    event_id, first = events.observe_event(candidate, at)
                    candidate = replace(candidate, key=event_id, first_report_at=first)
                    if candidate.confirmed and candidate.score >= settings['min_news_score'] and event_id not in used_news and not events.news_blocked(candidate):
                        fresh.append(candidate)
                if mode == 'rsi':
                    candidate = Candidate('rsi:'+code+':'+day, code, code, settings['min_news_score'], 0,
                                          'RSI(2) TECHNICAL', 'RSI', Article('rsi', 'RSI', 'urn:stockauto:rsi', at))
                else:
                    candidate = fresh[0] if fresh else None
                if candidate is not None and candidate.key not in used_news:
                    first = events.event_price(candidate.key, quote.ask)
                    account = Account(cash, frozenset(positions), frozenset(pending), equity)
                    try:
                        intent = build_intent(candidate, account, quote, settings, at, set(positions) | set(pending), first)
                    except ValueError:
                        pass
                    else:
                        pending[code] = dict(side='BUY', at=at.isoformat(), limit=intent.limit_price,
                                             quantity=intent.quantity, candidate=candidate)
                        signals.append(dict(at=at.isoformat(), code=code, quantity=intent.quantity,
                                            limit_price=intent.limit_price, expires_at=intent.expires_at.isoformat()))
                        used_news.add(candidate.key)
            previous.append(row['close'])
    peak=start;drawdown=0
    for value in curve:
        peak=max(peak,value);drawdown=max(drawdown,(peak-value)/peak*100)
    events.close()
    return dict(strategy=mode,synthetic=bool(data.get('synthetic',False)),approval_assumption='신호 시 승인 가정, 다음 봉 시가 체결',
        total_return_pct=round(((curve[-1] if curve else start)/start-1)*100,4),max_drawdown_pct=round(drawdown,4),
        completed_trades=len(trades),win_rate_pct=round(sum(t['pnl_won']>0 for t in trades)/len(trades)*100,2) if trades else None,
        final_positions=len(positions),signals=signals,fills=fills,trades=trades)


def main():
    """기능: 세 전략 비교 CLI. 입력: 데이터/설정/출력 경로. 출력: JSON.
    구조·이유: 같은 데이터와 비용을 고정하고 전략 조건만 바꾸어 비교합니다.
    """
    parser=argparse.ArgumentParser();parser.add_argument('dataset');parser.add_argument('--config',default='config/settings.json');parser.add_argument('--output')
    args=parser.parse_args()
    from .app import load_settings
    settings=load_settings(args.config);data=json.loads(Path(args.dataset).read_text())
    result=[replay(data,settings,mode) for mode in ('news','rsi','combined')]
    text=json.dumps(result,ensure_ascii=False,indent=2)
    if args.output:Path(args.output).write_text(text+'\n',encoding='utf-8')
    print(text)


if __name__=='__main__':main()

"""파일 역할: RSS 뉴스 제목·링크·게시시간을 수집하고 관심종목별 규칙 점수를 계산합니다.
구조: HTTP 수집→XML 검증→기사 정규화→기업명 매칭→이벤트 점수→후보 정렬.
이유: 제목의 특정 단어가 점수를 만든 근거를 사용자에게 그대로 설명할 수 있게 합니다.
"""
import hashlib
import html
import re
import ssl
import truststore
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
from defusedxml import ElementTree
from .models import Article, Candidate

MAX_FEED_BYTES = 1024 * 1024
# (검색 단어, 매수 방향 점수, 중요도, LCD 영문 설명). 같은 사건군은 한 번만 합산합니다.
RULES = (
    (('수주', '공급계약', '계약 체결'), 3, 3, 'NEW CONTRACT'),
    (('실적 개선', '영업이익 증가', '흑자전환', '사상 최대'), 3, 3, 'EARNINGS UP'),
    (('투자 확대', '신규 투자', '증설'), 2, 2, 'EXPANSION'),
    (('배당 확대', '자사주 소각'), 2, 2, 'SHAREHOLDER'),
    (('소송', '횡령', '거래정지', '상장폐지', '리콜'), -5, 5, 'RISK ALERT'),
    (('적자전환', '실적 악화', '영업손실', '계약 해지'), -4, 4, 'EARNINGS RISK'),
)


def parse_feed(payload, now, max_age_hours=24):
    """기능: RSS/Atom을 기사 목록으로 변환. 입력: XML bytes·현재시각·유효시간.
    출력: 중복 없는 Article 목록. 구조·이유: 날짜 누락/미래/오래된 기사와 XML 엔티티를 제외합니다.
    """
    if len(payload) > MAX_FEED_BYTES: raise ValueError('RSS 크기 초과')
    root = ElementTree.fromstring(payload)
    articles = {}
    for item in list(root.findall('.//item')) + list(root.findall('{http://www.w3.org/2005/Atom}entry')):
        def text(tag):
            """기능: RSS 또는 Atom 필드 추출. 입력: 필드 이름. 출력: 공백 정리 문자열.
            구조·이유: 두 형식의 namespace 차이를 이 위치에서 처리합니다.
            """
            node = item.find(tag)
            if node is None: node = item.find('{http://www.w3.org/2005/Atom}' + tag)
            return '' if node is None else ''.join(node.itertext()).strip()
        title = re.sub(r'\s+', ' ', html.unescape(re.sub('<[^>]+>', '', text('title'))))[:500]
        link = text('link')
        if not link:
            node = item.find('{http://www.w3.org/2005/Atom}link')
            link = node.get('href', '') if node is not None else ''
        if urlsplit(link).scheme not in ('http', 'https') or not title: continue
        stamp = text('pubDate') or text('published') or text('updated')
        try:
            try: published = parsedate_to_datetime(stamp)
            except (ValueError, TypeError): published = datetime.fromisoformat(stamp.replace('Z', '+00:00'))
            if published.tzinfo is None: continue
            if not now-timedelta(hours=max_age_hours) <= published <= now+timedelta(minutes=5): continue
        except (ValueError, TypeError, OverflowError): continue
        # 제목이 같은 재배포 기사가 피드를 바꿔 반복 주문을 만들지 않도록 제목으로 식별합니다.
        key = hashlib.sha256(title.casefold().encode()).hexdigest()
        articles[key] = Article(key, title, link, published.astimezone(timezone.utc))
    return list(articles.values())


def fetch_news(urls, now, max_age_hours=24):
    """기능: 설정한 RSS 수집. 입력: HTTPS URL 목록·현재시각·유효시간.
    출력: (기사 목록, 피드별 오류). 구조·이유: timeout과 최대 크기로 수집 지연을 제한하고 다른 피드는 계속 읽습니다.
    """
    articles, errors = {}, []
    for url in urls:
        try:
            if urlsplit(url).scheme != 'https': raise ValueError('RSS는 HTTPS 주소를 사용합니다')
            request = Request(url, headers={'User-Agent':'StockAutoNews/1.0 RSS reader'})
            with urlopen(request, timeout=8, context=truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)) as response:
                payload = response.read(MAX_FEED_BYTES + 1)
            for article in parse_feed(payload, now, max_age_hours): articles[article.key] = article
        except Exception as error:
            errors.append(f'{urlsplit(url).hostname}: {type(error).__name__}: {error}')
    return list(articles.values()), errors


def title_events(title):
    """기능: 제목의 사건·방향·확정성 분석. 입력: 제목. 출력: (사건 목록, 확정 여부).
    구조·이유: 부정·부인·전망을 먼저 판별하여 '사상 최대 적자'를 호재로 세지 않습니다.
    """
    negative = ('적자', '손실', '실적 악화', '계약 해지', '수주 취소', '리콜', '소송', '횡령', '거래정지', '상장폐지')
    uncertain = ('전망', '예상', '기대', '가능성', '검토', '추진', '협의', '미확정', '사실무근', '부인', '아니다', '아니라고', '체결 안', '수주 안', '않', '못', '불발', '무산', '불확실', '루머', '무관', '가능')
    confirmed = not any(word in title for word in uncertain)
    if any(word in title for word in negative):
        # 소송 취하/적자 축소도 즉시 호재 매수로 뒤집지 않고 검토 뉴스로 남깁니다.
        return [(-5,5,'RISK ALERT')], confirmed
    events = [(score,importance,label) for words,score,importance,label in RULES[:4]
              if any(word in title for word in words)]
    if '사상 최대' in title and not any(word in title for word in ('매출','영업이익','순이익','실적','수주')):
        events = [event for event in events if event[2]!='EARNINGS UP']
    if not confirmed and events:
        return [(0,importance,label+' REVIEW') for _,importance,label in events],False
    return events,confirmed


def event_signature(title, stock, labels):
    """기능: 사건 비교용 특징 생성. 입력: 제목·기업·사건군. 출력: JSON 문자열.
    구조·이유: 기업/사건군 외에 금액·상대방 등 토큰을 보존해 다른 기사를 같은 사건으로 합치지 않습니다.
    """
    text=title.casefold()
    for name in [stock['name'],*stock.get('aliases',[])]: text=text.replace(name.casefold(),' ')
    for words,_,_,_ in RULES[:4]:
        for word in words:text=text.replace(word,' ')
    stop={'공시','종합','속보','단독','체결','발표','확정','관련','규모','계약','신규','및','대한','사상','최대','실적'}
    tokens=sorted({token for token in re.findall(r'[a-z0-9가-힣]+',text) if token not in stop and len(token)>1})
    import json
    return json.dumps({'labels':sorted(labels),'tokens':tokens},ensure_ascii=False,sort_keys=True)


def same_event(left, right):
    """기능: 사건 특징 유사도. 입력: 두 특징 JSON. 출력: bool.
    구조·이유: 사건군과 금액이 같고 핵심 단어가 유사할 때만 묶으며, 정정/취소는 별도 사건입니다.
    """
    import json
    a,b=json.loads(left),json.loads(right)
    if a['labels']!=b['labels']:return False
    x,y=set(a['tokens']),set(b['tokens'])
    amounts=lambda tokens:{t for t in tokens if any(c.isdigit() for c in t)}
    if amounts(x)!=amounts(y):return False
    if not x or not y:return x==y
    return len(x&y)/len(x|y)>=0.6


def contract_amount(title):
    """기능: 제목의 계약 금액 추출. 입력: 기사 제목. 출력: 원 단위 정수 또는 0.
    구조·이유: '1조 2000억원' 같은 단위를 Decimal로 합산하며 본문에 없는 금액은 추정하지 않습니다.
    """
    from decimal import Decimal
    matches=re.findall(r'((?:[0-9,]+(?:\.[0-9]+)?\s*[조억만]\s*)+)원',title)
    if len(matches)!=1:return 0
    units={'조':10**12,'억':10**8,'만':10**4}
    return int(sum(Decimal(number.replace(',',''))*units[unit] for number,unit in re.findall(r'([0-9,]+(?:\.[0-9]+)?)\s*([조억만])',matches[0])))


def mentions(title,stock):
    """기능: 기업명 경계 검사. 입력: 제목·기업 설정. 출력: bool.
    구조·이유: 한국어 조사는 허용하되 다른 회사 이름의 일부를 종목으로 연결하지 않습니다.
    """
    return any(re.search(r'(?<![A-Za-z0-9가-힣])'+re.escape(name.casefold())+r'(?:에서|에는|은|는|이|가|의|와|과|도|에)?(?![A-Za-z0-9가-힣])',title)
               for name in [stock['name'],*stock.get('aliases',[])])


def company_context(title,stock,watchlist):
    """기능: 복수 기업 제목의 사건 범위 분리. 입력: 제목·해당 기업·관심종목. 출력: 문맥·모호성.
    구조·이유: 쉼표/세미콜론/반면으로 나뉜 절의 주체를 유지하고 같은 절에 기업이 섞이면 검토로 남깁니다.
    """
    if sum(mentions(title,other) for other in watchlist)<=1:return title,False
    selected=[];active=False;ambiguous=False
    for part in re.split(r'(?<![0-9]),(?![0-9])|[;…]|반면',title):
        named=[other for other in watchlist if mentions(part,other)]
        if named:
            active=any(other['code']==stock['code'] for other in named)
            if active and len(named)>1:ambiguous=True
        if active:selected.append(part)
    return ' '.join(selected),ambiguous


def analyze(articles, watchlist, min_score=3):
    """기능: 기업별 뉴스 후보 구성. 입력: 기사·관심종목·최소점수. 출력: Candidate 목록.
    구조·이유: 확인된 호재만 매수 후보로 만들고, 부정/전망 뉴스도 알림 대상으로 유지합니다.
    """
    candidates=[]
    for article in articles:
        title=article.title.casefold()
        events,confirmed=title_events(title)
        if not events:continue
        score=sum(e[0] for e in events);importance=min(10,sum(e[1] for e in events))
        if 0<score<min_score:continue
        for stock in watchlist:
            if not mentions(title,stock):continue
            context,ambiguous=company_context(title,stock,watchlist)
            events,confirmed=title_events(context)
            confirmed=confirmed and not ambiguous
            if not events:continue
            score=sum(e[0] for e in events);importance=min(10,sum(e[1] for e in events))
            if 0<score<min_score:continue
            label=max(events,key=lambda e:e[1])[2]
            group=event_signature(context,stock,[e[2] for e in events])
            key=hashlib.sha256((stock['code']+article.key).encode()).hexdigest()
            amount=contract_amount(context) if 'NEW CONTRACT' in label else 0
            revenue=stock.get('annual_revenue_won',0)
            ratio=amount/revenue*100 if amount>0 and revenue>0 else None
            candidates.append(Candidate(key,stock['code'],stock['name'],score,importance,
                ', '.join(e[2] for e in events),f"{stock['lcd_name']} {label}",article,group,confirmed,None,amount,ratio))
    return sorted(candidates,key=lambda c:(c.importance,c.score,c.article.published_at),reverse=True)

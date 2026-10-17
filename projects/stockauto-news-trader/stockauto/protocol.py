"""파일 역할: PC↔UNO ASCII 줄 단위 메시지를 생성·검증합니다.
구조: OFFER/NEWS/RESULT와 DECIDE/READY/PING. 이유: 한글 UTF-8 바이트가 LCD 표시/버퍼를 깨지 않게 합니다.
"""
import re

ID = re.compile(r'^[a-f0-9]{16}$')


def safe_text(text, limit=60):
    """기능: LCD 전송 문자열 정리. 입력: 텍스트·최대 길이. 출력: ASCII 문자열.
    구조·이유: 구분자/개행/비 ASCII 문자를 제거해 메시지 경계를 보존합니다.
    """
    return ''.join(c if 32<=ord(c)<=126 and c!='|' else ' ' for c in text)[:limit]


def offer_frame(offer, alarm_threshold, now):
    """기능: 승인 화면 프레임 생성. 입력: Offer·알람 기준·현재 시각. 출력: bytes.
    구조·이유: ID·코드·수량·가격·중요도·임계값·TTL·요약을 함께 보내 같은 대상을 승인합니다.
    """
    ttl = max(1,min(300,int((offer.expires_at-now).total_seconds())))
    return f'OFFER|{offer.id}|{offer.candidate.code}|{offer.quantity}|{offer.price}|{offer.candidate.importance}|{alarm_threshold}|{ttl}|{safe_text(offer.candidate.lcd_text)}\n'.encode('ascii')


def news_frame(candidate, alarm_threshold):
    """기능: 매수 대상이 아닌 중요 뉴스 표시. 입력: Candidate·알람 기준. 출력: bytes.
    구조·이유: 악재도 알리되 승인 버튼으로 주문할 수 없는 NEWS 유형을 사용합니다.
    """
    return f'NEWS|{candidate.key[:16]}|{candidate.importance}|{alarm_threshold}|{safe_text(candidate.lcd_text)}\n'.encode('ascii')


def parse_device(line):
    """기능: 장치 응답 해석. 입력: 최대 80바이트 줄. 출력: (유형, ID, 선택) 또는 None.
    구조·이유: 형식과 ID를 엄격히 검사하여 잡음/이전 장치 메시지를 주문으로 해석하지 않습니다.
    """
    if len(line)>80: return None
    try: fields = line.decode('ascii').strip().split('|')
    except UnicodeDecodeError: return None
    if fields==['READY']: return ('READY','','')
    if len(fields)==2 and fields[0]=='PING' and ID.fullmatch(fields[1]): return ('PING',fields[1],'')
    if len(fields)==3 and fields[0]=='DECIDE' and ID.fullmatch(fields[1]) and fields[2] in ('BUY','NO'):
        return tuple(fields)
    return None

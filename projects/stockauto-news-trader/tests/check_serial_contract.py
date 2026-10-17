"""파일 역할: Python 송신 데이터가 실제 .ino의 수신 함수를 통과하는지 검사합니다."""
from datetime import timedelta
import subprocess
from stockauto.market import now_kst
from stockauto.models import Article,Candidate,Offer
from stockauto.protocol import offer_frame


def main():
    """기능: 양쪽 프로토콜 통합 검사. 입력: 가상 제안. 출력: PASS 또는 예외.
    구조·이유: 실제 LF를 포함한 RESET/OFFER 바이트를 C++ 펌웨어 검사기로 전달합니다.
    """
    now=now_kst();article=Article('test','가상 기사','https://example.com',now)
    candidate=Candidate('test','005930','가상전자',6,6,'TEST','TEST NEWS',article)
    offer=Offer('abcdef0123456789',candidate,2,70000,now+timedelta(seconds=90))
    subprocess.run(['build/test_firmware','--pc-frame'],input=b'RESET\n'+offer_frame(offer,5,now),check=True)


if __name__=='__main__':main()

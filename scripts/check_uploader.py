"""업로더 C 코어 회귀와 로컬 HTTP 전송을 임시 파일·루프백으로 검사합니다."""
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading

ROOT=Path(__file__).resolve().parents[1]/'projects/blue-witness/source'


class Handler(BaseHTTPRequestHandler):
    """테스트 서버: 실제 업로더 transport의 health/status/multipart 요청을 받습니다."""
    protocol_version='HTTP/1.1'
    def respond(self,status,payload):
        """입력: 상태·응답 객체. 출력: 길이가 정해진 JSON HTTP 응답. 이유: 연결 대기를 제거합니다."""
        data=json.dumps(payload).encode();self.send_response(status)
        self.send_header('Content-Length',str(len(data)));self.send_header('Content-Type','application/json');self.send_header('Connection','close');self.end_headers();self.wfile.write(data)
    def do_GET(self):
        """입력: GET 경로. 출력: health/status. 구조: 테스트 대상 경로만 성공시킵니다."""
        if self.path=='/api/v1/health':self.respond(200,dict(status='ok',database='ok'))
        elif self.path.startswith('/api/v1/events/EVT_TEST/evidence/status'):self.respond(200,dict(stored=True))
        else:self.respond(404,{})
    def do_POST(self):
        """입력: multipart 본문. 출력: 완전히 받은 경우 201. 이유: 파일 스트리밍을 끝까지 검사합니다."""
        length=int(self.headers.get('Content-Length',0));payload=self.rfile.read(length)
        self.respond(201 if len(payload)==length and b'fixture-video' in payload else 400,dict(stored=True))
    def log_message(self,*args):
        """테스트 로그의 요청 본문/인증 헤더를 출력하지 않습니다."""
        pass


def main():
    """입력: 포함된 C 코드. 출력: 회귀/HTTP PASS 또는 실패 종료.
    구조·이유: 실제 큐·ACK·HTTP/TLS 구현을 컴파일하고 임시 서버·DB에서 실행합니다.
    """
    with tempfile.TemporaryDirectory(prefix='portfolio-uploader-') as directory:
        temp=Path(directory);include=['-I'+str(ROOT/'uploader/include'),'-I'+str(ROOT/'ipc')]
        compiler=os.environ.get('CC','cc')
        core=[ROOT/'uploader/tests/core_regression.c',ROOT/'uploader/src/queue/uploader_queue.c',ROOT/'uploader/src/worker/uploader_ack.c',ROOT/'uploader/src/worker/uploader_metadata.c']
        subprocess.run([compiler,'-std=c11','-Wall','-Wextra',*include,*map(str,core),'-lsqlite3','-o',str(temp/'core')],check=True)
        subprocess.run([str(temp/'core'),str(temp/'queue.db')],check=True)
        sources=[ROOT/'uploader/tests/http_transport_smoke.c',*(ROOT/'uploader/src/worker'/name for name in ['uploader_http.c','uploader_tls.c','uploader_ack.c','uploader_metadata.c'])]
        flags=[]
        if Path('/opt/homebrew/opt/openssl@3').exists():flags=['-I/opt/homebrew/opt/openssl@3/include','-L/opt/homebrew/opt/openssl@3/lib']
        subprocess.run([compiler,'-std=c11','-D_POSIX_C_SOURCE=200809L',*include,*flags,*map(str,sources),'-lssl','-lcrypto','-o',str(temp/'transport')],check=True)
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            video=temp/'fixture.mp4';video.write_bytes(b'fixture-video'*1024)
            subprocess.run([str(temp/'transport'),f'http://127.0.0.1:{server.server_port}','',str(video)],check=True,timeout=20)
        finally:server.shutdown();server.server_close();thread.join(timeout=3)


if __name__=='__main__':main()

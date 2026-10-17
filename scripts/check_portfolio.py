"""포트폴리오의 내부 문서 링크·소스 manifest·민감 설정 혼입을 검사합니다."""
from pathlib import Path
import hashlib
import json
import re
import sys
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'projects/blue-witness/source'


def main():
    """입력: 저장소 파일. 출력: 오류 목록과 종료 코드.
    구조·이유: 링크 대상을 실제 파일로 해석하고 manifest hash를 대조해,
    리뷰어가 문서에서 코드까지 이동할 수 있고 포함 파일이 기록과 일치하는지 검사합니다.
    """
    failures = []
    for page in ROOT.rglob('*.md'):
        if any(part in {'third_party', '.git', '__pycache__'} for part in page.parts):
            continue
        for match in re.finditer(r'!?\[[^\]\n]*\]\(([^)\n]+)\)', page.read_text()):
            target = match.group(1).split(' "', 1)[0].strip('<>')
            if re.match(r'^[a-zA-Z][\w+.-]*:', target) or target.startswith('#'):
                continue
            target = unquote(target.split('#', 1)[0])
            if target and not (page.parent / target).exists():
                failures.append(f'{page.relative_to(ROOT)}: 링크 없음: {target}')
    recorded = 0
    for manifest_path in (SOURCE / 'PROVENANCE.json', ROOT / 'projects/stockauto-news-trader/PROVENANCE.json'):
        manifest = json.loads(manifest_path.read_text())
        for entry in manifest['files']:
            path = manifest_path.parent / entry['path']
            if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != entry['sha256']:
                failures.append(f'manifest 불일치: {path.relative_to(ROOT)}')
            recorded += 1
    # 운영 인증 값이 들어가는 파일은 example만 허용합니다. 내용은 로그에 출력하지 않습니다.
    for path in SOURCE.rglob('*'):
        if not path.is_file() or '__pycache__' in path.parts:
            continue
        if path.name == 'uploader.env' or path.suffix in {'.pem', '.key', '.sqlite3', '.db'}:
            failures.append(f'운영 설정·데이터 혼입: {path.relative_to(ROOT)}')
        if path.suffix in {'.c', '.h', '.py', '.yaml', '.yml', '.env', '.example'}:
            content = path.read_text(errors='replace')
            if re.search(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----', content):
                failures.append(f'개인키 혼입: {path.relative_to(ROOT)}')
    if failures:
        print('\n'.join(failures)); return 1
    print(f'내부 문서 링크·출처 hash {recorded}개·운영 설정 분리 PASS')
    return 0


if __name__ == '__main__':
    sys.exit(main())

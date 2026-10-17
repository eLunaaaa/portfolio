"""Blue Witness의 개인 작성 확인 LOC를 담당 범위와 원본 Git 이력으로 집계합니다.

입력: 전체 이력이 있는 원본 Git 저장소, cloc 2.10, 포트폴리오 LOC·출처 manifest.
출력: 개인 작성 LOC 요약 JSON, 파일·줄별 CSV, 개인 LOC 설명 문서.
구조: cloc 코드행 분리 → 원본과 현재 소스의 동일 행 대응 → Git blame 작성자 대조
→ 개인 구현 담당 영역 확인. 후속 팀 수정과 공통 모듈은 개인 작성에 합산하지 않습니다.
"""
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import argparse
import csv
import difflib
import hashlib
import json
import re
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
BASE = '514880419f84a5fc0488652f0f2cb873d04e33b8'
PERSONAL_EMAILS = {'horang511@gmail.com', '162854157+elunaaaa@users.noreply.github.com'}
PERSONAL_MODULES = {'server', 'model_training', 'time_sync', 'automation', 'uploader'}
LABELS = {'personal_verified': '개인 작성 확인', 'collaboration': '개인 담당 모듈 내 협업',
          'team_implementation': '팀 공통 구현', 'integration': '개발본 추가·통합 변경'}


def git(repository, *args):
    """입력: 원본 저장소 경로·Git 인자. 출력: UTF-8 출력 문자열.
    실패 시 예외로 중단해 누락된 이력을 개인 기여로 추정하지 않습니다.
    """
    return subprocess.check_output(['git', '-C', str(repository), *args], text=True)


def matching_lines(original, current):
    """입력: 원본·현재 행 목록. 출력: 현재 행 번호 → 원본 행 번호.
    동일한 문장이 이어지는 구간만 대응하고 변경된 행은 임의로 원본에 귀속하지 않습니다.
    """
    result = {}
    for match in difflib.SequenceMatcher(None, original, current, autojunk=False).get_matching_blocks():
        result.update({match.b + i + 1: match.a + i + 1 for i in range(match.size)})
    return result


def blame(repository, source):
    """입력: 기준 commit의 원본 파일 경로. 출력: 최종 행 번호별 작성자·commit.
    -w는 공백 수정, -M은 파일 내부 이동을 추적해 형식 변경이 기여를 덮지 않게 합니다.
    """
    text = git(repository, 'blame', '--line-porcelain', '-w', '-M', BASE, '--', source)
    result = {}
    line_number = None
    commit = email = None
    for line in text.splitlines():
        match = re.match(r'^([0-9a-f]{40}) \d+ (\d+)(?: \d+)?$', line)
        if match:
            commit, line_number = match[1], int(match[2])
        elif line.startswith('author-mail '):
            email = line.removeprefix('author-mail ').strip('<>').lower()
        elif line.startswith('\t'):
            result[line_number] = {'commit': commit, 'personal_account': email in PERSONAL_EMAILS}
    return result


def analyze_file(row, provenance, repository, prepared):
    """입력: 파일 LOC·출처·Git 경로·cloc 임시 파일. 출력: 파일별 합계와 행별 근거.
    실제로 대응한 코드행만 작성자를 확인하며, 팀 구현은 본인 계정 커밋이어도 별도로 둡니다.
    """
    path = row['path']
    relative = Path(path).relative_to('projects/blue-witness/source').as_posix()
    entry = provenance.get(relative, {})
    source = entry.get('source')
    current = (ROOT / path).read_text().splitlines()
    stripped = Path(str(prepared / path) + '.loc-code').read_text().splitlines()
    assert len(stripped) == int(row['code']), path
    code_map = matching_lines(current, stripped)
    module = relative.split('/')[0]
    original_map, authors = {}, {}
    if source:
        original = git(repository, 'show', f'{BASE}:{source}').splitlines()
        original_map = matching_lines(original, current)
        authors = blame(repository, source)
    totals = {key: 0 for key in LABELS}
    lines = []
    for index in range(1, len(stripped) + 1):
        current_line = code_map.get(index)
        original_line = original_map.get(current_line)
        author = authors.get(original_line, {})
        if module not in PERSONAL_MODULES:
            kind = 'team_implementation'
        elif not author:
            kind = 'integration'
        elif author['personal_account']:
            kind = 'personal_verified'
        else:
            kind = 'collaboration'
        totals[kind] += 1
        lines.append({'path': path, 'code_index': index, 'current_line': current_line or '',
                      'source_path': source or '', 'source_line': original_line or '',
                      'commit': author.get('commit', ''), 'personal_account': author.get('personal_account', ''),
                      'classification': kind})
    return {'path': path, 'module': module, 'language': row['language'], 'category': row['category'],
            'code': int(row['code']), **totals, 'sha256': hashlib.sha256((ROOT / path).read_bytes()).hexdigest()}, lines


def save_csv(path, rows):
    """입력: CSV 경로·딕셔너리 목록. 출력: UTF-8·LF 파일.
    열 이름을 함께 보존해 파일별 합계와 줄별 작성 근거를 외부에서도 검산할 수 있게 합니다.
    """
    with path.open('w', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]), lineterminator='\n')
        writer.writeheader()
        writer.writerows(rows)


def main():
    """입력: CLI의 원본 저장소·cloc 경로. 출력: 개인 LOC 근거 파일·종합 문서.
    파일 복사본에서만 주석을 제거하고 원본 코드·Git 작성 이력은 변경하지 않습니다.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--upstream', required=True, type=Path)
    parser.add_argument('--cloc', required=True, type=Path)
    args = parser.parse_args()
    repository = args.upstream.resolve()
    if git(repository, 'rev-parse', '--is-shallow-repository').strip() != 'false':
        parser.error('전체 Git 이력이 있는 원본 저장소가 필요합니다.')
    command = ['perl', str(args.cloc.resolve())]
    if subprocess.check_output(command + ['--version'], text=True).strip() != '2.10':
        parser.error('cloc 2.10을 사용하세요.')
    rows = [r for r in csv.DictReader((ROOT / 'metrics/loc-files.csv').open())
            if r['project'] == 'blue-witness' and r['category'] not in {'external', 'generated'}]
    provenance = {e['path']: e for e in json.loads((ROOT / 'projects/blue-witness/source/PROVENANCE.json').read_text())['files']}
    with tempfile.TemporaryDirectory() as temp:
        prepared = Path(temp)
        for row in rows:
            target = prepared / row['path']
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / row['path'], target)
        listing = prepared / 'files.txt'
        listing.write_text('\n'.join(str(prepared / r['path']) for r in rows) + '\n')
        subprocess.run(command + ['--quiet', '--json', '--by-file', '--skip-uniqueness',
            '--strip-comments=loc-code', '--original-dir', f'--list-file={listing}',
            f'--out={prepared / "cloc.json"}'], check=True)
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda r: analyze_file(r, provenance, repository, prepared), rows))
    files = [result[0] for result in results]
    lines = [line for result in results for line in result[1]]
    total = {key: sum(r[key] for r in files) for key in ['code', *LABELS]}
    assert total['code'] == sum(total[k] for k in LABELS)
    assert total['code'] == json.loads((ROOT / 'metrics/loc.json').read_text())['projects']['blue-witness']['total']['code']
    modules = {m: {k: sum(r[k] for r in files if r['module'] == m) for k in ['code', *LABELS]}
               for m in sorted({r['module'] for r in files})}
    summary = {'baseline_commit': BASE, 'tool': 'cloc 2.10 + git blame -w -M',
               'personal_module_scope': sorted(PERSONAL_MODULES), 'total': total, 'modules': modules}
    (ROOT / 'metrics/blue-witness-personal-loc.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n')
    save_csv(ROOT / 'metrics/blue-witness-personal-loc-files.csv', files)
    save_csv(ROOT / 'metrics/blue-witness-personal-loc-lines.csv', lines)
    text = ['# Blue Witness · 개인 작성 LOC', '',
            f'**프로젝트 전체 {total["code"]:,} LOC 중 개인 작성 확인 {total["personal_verified"]:,} LOC**입니다. 개인 구현 담당 범위 안에서 본인 Git 계정이 작성자로 남아 있고 현재 포함 코드와 줄 대응이 확인된 수치입니다.', '',
            '| 구분 | LOC | 집계 의미 |', '|---|---:|---|',
            f'| **개인 작성 확인** | **{total["personal_verified"]:,}** | 서버·웹·학습·시간 동기화·Uploader·CI/CD의 개인 계정 작성 이력 |',
            f'| 개인 담당 모듈 내 협업 | {total["collaboration"]:,} | 같은 모듈에서 다른 팀원 계정의 작성 이력이 남은 코드 |',
            f'| 팀 공통 구현 | {total["team_implementation"]:,} | 촬영/추론 런타임·공통 IPC |',
            f'| 개발본 추가·통합 변경 | {total["integration"]:,} | 원본 기준 버전과 행 대응이 확정되지 않은 추가·수정분 |', '',
            '## 모듈별 구분', '',
            '| 모듈 | 전체 | 개인 작성 확인 | 담당 모듈 내 협업 | 팀 구현·공통 | 개발본·통합 |',
            '|---|---:|---:|---:|---:|---:|']
    for module, counts in modules.items():
        text.append('| ' + module + ' | ' + ' | '.join(f'{counts[k]:,}' for k in ['code', *LABELS]) + ' |')
    text += ['', '## 기준', '',
             '- 원본 기준 commit: `'+BASE+'`. 본인 계정 두 가지 표기인 `elunaaaa`·`eLunaaaa`의 Git 작성자 식별자를 사용합니다.',
             '- 누적 추가 줄 수가 아니라 **현재 포트폴리오에 남아 있는 코드행**을 집계합니다. 주석·빈 줄·외부·자동 생성 코드는 전체 LOC와 같은 기준으로 제외합니다.',
             '- `git blame -w -M`으로 공백 변경과 파일 내부 이동을 추적합니다. cloc의 코드행과 원본의 동일 행 대응이 확인된 줄만 개인 작성 확인에 포함합니다.',
             '- Uploader는 개인 전체 개발 범위에 포함하고 이후 CD 연동 등의 팀 수정은 작성자 이력으로 분리합니다. 설계·통합·모델 학습·실험 기여는 LOC와 함께 별도 담당 설명으로 제시합니다.', '',
             '## 검산과 재집계', '',
             '[파일별 집계·해시](../../../metrics/blue-witness-personal-loc-files.csv) · [코드행별 commit 근거](../../../metrics/blue-witness-personal-loc-lines.csv) · [집계 JSON](../../../metrics/blue-witness-personal-loc.json)', '',
             '```bash', 'python3 scripts/analyze_personal_loc.py --upstream /path/to/original-repository --cloc /path/to/cloc-2.10.pl',
             'python3 scripts/analyze_loc.py --cloc /path/to/cloc-2.10.pl', '```', '',
             '[개인 담당과 협업](CODE_GUIDE.md) · [전체 LOC](LOC.md)', '']
    (ROOT / 'projects/blue-witness/docs/PERSONAL_LOC.md').write_text('\n'.join(text))
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()

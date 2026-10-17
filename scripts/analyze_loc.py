"""프로젝트별 LOC를 cloc으로 집계해 README·상세 문서·파일별 근거를 갱신합니다.

입력: Git이 추적하는 프로젝트 파일과 cloc 2.10 실행 파일.
출력: metrics/loc.json·loc-files.csv, 전체·프로젝트별 LOC 문서와 README 요약.
구조: 대상 선택 → cloc의 코드/주석/빈 줄 분리 → 범주·언어별 합산 → 문서 출력.
외부·생성 코드를 별도로 집계해 프로젝트 구현 규모를 일관되게 비교합니다.
"""
from pathlib import Path
import argparse
import csv
import hashlib
import json
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
PROJECTS = {
    'blue-witness': 'Blue Witness',
    'configurable-cnn-rtl': 'CNN 학습·RTL 자동화',
    'edge-ai-trash-sorter': '엣지 AI 쓰레기 분류',
    'arduino-load-balancer': 'Arduino 동적 부하 분산',
    'embedded-linux-system-programming': '실시간 Linux 실험 환경',
    'stockauto-news-trader': '뉴스·물리 버튼 주식 주문',
    'smart-umbrella-stand': 'Smart Umbrella Stand',
}
EXTENSIONS = {'.c', '.h', '.cc', '.cpp', '.hpp', '.ino', '.py', '.v', '.sv',
              '.js', '.cjs', '.html', '.css', '.sql', '.sh', '.ys', '.xdc',
              '.yaml', '.yml', '.cmake'}
OMIT_DIRS = {'docs', 'analysis', 'media', 'reference', 'data', '__pycache__'}
EXTERNAL_DIRS = {'third_party', 'vendor', 'external', 'node_modules'}
GENERATED = {
    'projects/configurable-cnn-rtl/rtl/cnn_top.v',
    'projects/smart-umbrella-stand/firmware/master/Core/Inc/wet_model_params.h',
}
CATEGORIES = ['implementation', 'tests_examples', 'build_ci_config', 'generated', 'external']
LABELS = dict(zip(CATEGORIES, ['구현', '테스트·예제', '빌드·CI·설정', '자동 생성·모델 계수', '외부 코드']))


def classify(path):
    """입력: 저장소 상대 경로. 출력: 범주 또는 None(집계 제외).
    외부·생성 여부를 먼저 구분하고 테스트를 구현과 분리해 중복 합산을 막습니다.
    """
    p = Path(path)
    if p.suffix not in EXTENSIONS and p.name not in {'Makefile', 'CMakeLists.txt'}:
        return None
    if EXTERNAL_DIRS.intersection(p.parts):
        return 'external'
    if OMIT_DIRS.intersection(p.parts[2:]):
        return None
    if path in GENERATED:
        return 'generated'
    if p.name in {'Makefile', 'CMakeLists.txt'} or p.suffix in {'.sh', '.ys', '.xdc', '.yaml', '.yml', '.cmake'}:
        return 'build_ci_config'
    if {'test', 'tests', 'testbench', 'examples'}.intersection(p.parts) or p.name.startswith(('test_', 'tb_')):
        return 'tests_examples'
    return 'implementation'


def totals(rows):
    """입력: cloc 파일 집계 목록. 출력: 파일 수·코드·주석·빈 줄 합계.
    코드와 주석이 한 줄에 같이 있으면 cloc의 코드 줄 판정을 그대로 사용합니다.
    """
    return {'files': len(rows), **{key: sum(r[key] for r in rows) for key in ('code', 'comment', 'blank')}}


def section(text, block, before=None):
    """입력: 기존 README·생성 요약·삽입 위치. 출력: 갱신된 README 문자열.
    표식 안만 교체해 재집계 시 설명이나 프로젝트 순서를 보존합니다.
    """
    start, end = '<!-- LOC:START -->', '<!-- LOC:END -->'
    wrapped = start + '\n' + block.rstrip() + '\n' + end + '\n\n'
    if start in text:
        a = text.index(start)
        b = text.index(end, a) + len(end)
        return text[:a] + wrapped.rstrip('\n') + text[b:]
    at = text.index(before) if before else text.index('\n## ')
    return text[:at] + wrapped + text[at:]



def write_challenge_report(challenges, reports):
    """입력: 프로젝트별 문제·설계·근거와 LOC. 출력: 기술적 과제 종합 문서.
    LOC는 구현 규모로, 기술적 과제는 제약·설계·검증으로 제시해 평가 기준을 구분합니다.
    """
    lines = ['# 프로젝트별 기술적 과제와 해결', '',
             '각 프로젝트에서 해결한 문제를 **제약 → 설계 선택 → 구현·검증**으로 연결했습니다. LOC는 코드 규모이고, 기술적 깊이는 상태 관리·동시성·수치 정합성·자원 제약·측정 설계에서 확인할 수 있습니다.', '',
             '[프로젝트별 LOC 비교](LOC.md) · [포트폴리오](../README.md)', '']
    for slug, name in PROJECTS.items():
        c = challenges[slug]
        lines += [f'<a id="{slug}"></a>', '', f'## {name}', '',
                  f'**{reports[slug]["total"]["code"]:,} LOC** · ' + c['summary'] + '.', '',
                  '**개인 담당:** ' + c['role'], '',
                  '| 해결해야 할 문제 | 기술적 제약 | 설계·구현 | 확인 근거 |', '|---|---|---|---|']
        for row in c['challenges']:
            lines.append('| ' + ' | '.join(row[k] for k in ['problem', 'constraint', 'solution', 'validation']) + ' |')
        lines += ['', f'[프로젝트 코드·담당 범위](../projects/{slug}/README.md) · [언어별 LOC](../projects/{slug}/docs/LOC.md)', '']
    (ROOT / 'docs/TECHNICAL_CHALLENGES.md').write_text('\n'.join(lines))


def publish(rows, version):
    """입력: 파일별 수치·도구 버전. 출력: JSON·CSV·README·상세 LOC 문서.
    같은 파일 집계에서 모든 표를 생성해 요약과 언어별 수치가 어긋나지 않게 합니다.
    """
    challenges = json.loads((ROOT / 'metrics/technical-challenges.json').read_text())
    personal_path = ROOT / 'metrics/blue-witness-personal-loc.json'
    personal = json.loads(personal_path.read_text()) if personal_path.exists() else None
    if personal:
        # 소스가 바뀌면 과거 개인 LOC를 그대로 노출하지 않고 먼저 재집계하도록 요구합니다.
        recorded = {r['path']: r['sha256'] for r in csv.DictReader(
            (ROOT / 'metrics/blue-witness-personal-loc-files.csv').open())}
        current = {r['path']: r['sha256'] for r in rows if r['project'] == 'blue-witness'
                   and r['category'] not in {'external', 'generated'}}
        if recorded != current:
            raise RuntimeError('Blue Witness 소스가 변경됐습니다. analyze_personal_loc.py를 먼저 실행하세요.')
    reports = {}
    for slug in PROJECTS:
        group = [r for r in rows if r['project'] == slug]
        included = [r for r in group if r['category'] not in {'external', 'generated'}]
        reports[slug] = {'total': totals(included), 'categories': {
            c: totals([r for r in group if r['category'] == c]) for c in CATEGORIES},
            'languages': {lang: totals([r for r in included if r['language'] == lang])
                          for lang in sorted({r['language'] for r in included})}}
    (ROOT / 'metrics/loc.json').write_text(json.dumps({
        'tool': f'cloc {version}', 'scope': 'Git tracked project files; physical code lines',
        'projects': reports}, ensure_ascii=False, indent=2) + '\n')
    with (ROOT / 'metrics/loc-files.csv').open('w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]), lineterminator='\n')
        writer.writeheader()
        writer.writerows(rows)
    table = '| 프로젝트 | 구현 | 테스트·예제 | 빌드·CI·설정 | 합계 LOC |\n|---|---:|---:|---:|---:|\n'
    for slug, name in PROJECTS.items():
        r = reports[slug]
        nums = [r['categories'][c]['code'] for c in CATEGORIES[:3]] + [r['total']['code']]
        table += f'| [{name}](projects/{slug}/docs/LOC.md) | ' + ' | '.join(f'{n:,}' for n in nums) + ' |\n'
        lines = [f'# {name} · 코드 규모', '',
                 f'**{r["total"]["code"]:,} LOC · {r["total"]["files"]}개 파일**. 주석·빈 줄을 제외한 물리적 코드 줄 수이며, 테스트·예제와 빌드·CI·설정을 포함합니다.', '',
                 '| 구분 | 파일 | 코드 | 주석 | 빈 줄 |', '|---|---:|---:|---:|---:|']
        for c in CATEGORIES:
            t = r['categories'][c]
            lines.append(f'| {LABELS[c]} | {t["files"]} | {t["code"]:,} | {t["comment"]:,} | {t["blank"]:,} |')
        lines += ['', '외부 코드와 자동 생성·모델 계수는 합계 LOC에서 제외했습니다.', '',
                  '## 언어별 집계', '', '| 언어 | 파일 | 코드 | 주석 | 빈 줄 |', '|---|---:|---:|---:|---:|']
        for lang, t in sorted(r['languages'].items(), key=lambda x: -x[1]['code']):
            lines.append(f'| {lang} | {t["files"]} | {t["code"]:,} | {t["comment"]:,} | {t["blank"]:,} |')
        lines += ['', '프로젝트에 포함된 코드 규모이며 개인 단독 작성량을 뜻하지 않습니다. 담당 범위는 프로젝트 README에서 구분했습니다.', '',
                  '[전체 비교·집계 기준](../../../docs/LOC.md) · [파일별 수치·SHA-256](../../../metrics/loc-files.csv)', '']
        challenge = challenges[slug]
        if slug == 'blue-witness' and personal:
            verified = personal['total']['personal_verified']
            lines += ['## 개인 작성 LOC', '',
                      f'**개인 작성 확인 {verified:,} LOC / 프로젝트 전체 {r["total"]["code"]:,} LOC**. 개인 구현 담당 영역과 원본 Git 작성자 이력을 함께 적용했습니다.', '',
                      '[모듈별 개인·팀 수정 구분과 줄별 근거](PERSONAL_LOC.md)', '']
        lines += ['## 기술적 과제', '', challenge['summary'] + '.', '',
                  '**해결 접근:** ' + challenge['decision'] + '.', '',
                  '**확인 근거:** ' + challenge['evidence'] + '.', '',
                  f'[문제·제약·설계·검증 상세](../../../docs/TECHNICAL_CHALLENGES.md#{slug})', '']
        (ROOT / f'projects/{slug}/docs/LOC.md').write_text('\n'.join(lines))
        readme = ROOT / f'projects/{slug}/README.md'
        short = f'## 코드 규모와 기술적 과제\n\n**{r["total"]["code"]:,} LOC · {r["total"]["files"]}개 파일** · 구현 {nums[0]:,} / 테스트·예제 {nums[1]:,} / 빌드·CI·설정 {nums[2]:,}.\n\n주석·빈 줄·외부 코드·자동 생성 파일을 제외한 프로젝트 규모입니다. [언어별·범주별 분석](docs/LOC.md)'
        if slug == 'blue-witness' and personal:
            short += (f'\n\n**개인 작성 확인 {personal["total"]["personal_verified"]:,} LOC** · 개인 구현 담당 영역의 원본 Git 작성 이력 기준. '
                      + '[개인·팀 수정·통합분 구분](docs/PERSONAL_LOC.md)')
        short += ('\n\n- **기술적 과제:** ' + challenge['summary'] + '.'
                  + '\n- **설계 선택:** ' + challenge['decision'] + '.'
                  + '\n- **확인 근거:** ' + challenge['evidence'] + '.'
                  + f' [문제·해결·검증](../../docs/TECHNICAL_CHALLENGES.md#{slug})')
        readme.write_text(section(readme.read_text(), short))
    challenge_table = '| 프로젝트 | 합계 LOC | 핵심 기술적 과제 | 설계·검증 근거 |\n|---|---:|---|---|\n'
    for slug, name in PROJECTS.items():
        c = challenges[slug]
        loc_display = f'{reports[slug]["total"]["code"]:,}'
        if slug == 'blue-witness' and personal:
            loc_display += (f'<br>개인 작성 확인 **{personal["total"]["personal_verified"]:,}** '
                            + '[근거](projects/blue-witness/docs/PERSONAL_LOC.md)')
        challenge_table += (f'| [{name}](projects/{slug}/README.md) | {loc_display} | '
                            + c['summary'] + ' | ' + c['evidence']
                            + f' · [상세](docs/TECHNICAL_CHALLENGES.md#{slug}) |\n')
    write_challenge_report(challenges, reports)
    block = '## 프로젝트별 코드 규모와 기술적 과제\n\n' + challenge_table + '\n주석·빈 줄·외부 코드·자동 생성 파일을 제외한 프로젝트 LOC입니다. 공동 구현을 포함하며 개인 단독 작성량과는 구분합니다. [언어별 분석·집계 기준](docs/LOC.md)'
    readme = ROOT / 'README.md'
    readme.write_text(section(readme.read_text(), block, '## 대표 구현과 검증'))
    overview = '# 프로젝트별 LOC 분석\n\n[문제·제약·설계·검증으로 보는 기술적 과제](TECHNICAL_CHALLENGES.md)\n\n' + table.replace('](projects/', '](../projects/')
    overview += '''
## 집계 기준

- **지표:** cloc이 분류한 물리적 코드 줄 수입니다. 주석·빈 줄은 코드에서 제외하고 별도 집계합니다. 한 줄의 문장 수나 파일 byte 수를 LOC로 환산하지 않습니다.
- **대상:** Git으로 추적하는 프로젝트의 C/C++·헤더·Arduino·Python·Verilog·JavaScript·HTML·CSS·SQL과 빌드 스크립트·CI YAML 등입니다. 동일 내용도 서로 다른 파일 경로로 관리하면 각각 셉니다.
- **구분:** 구현, 테스트·예제, 빌드·CI·설정을 분리합니다. 합계는 이 세 범주의 합입니다. 프로그램의 웹 화면 HTML은 포함하고 결과 보고용 HTML은 제외합니다.
- **제외:** 사진·영상·문서·평가 데이터·모델 가중치·로그·실행 파일·의존성 목록, docs/analysis/media/reference/data의 자료는 제외합니다. root의 포트폴리오 관리 코드는 각 프로젝트에 합산하지 않습니다.
- **별도 표시:** YOLOX 등의 third_party/vendor 코드, CNN 생성 top RTL과 우산꽂이 모델 계수 헤더는 합계에서 제외하고 프로젝트 상세 표에서 따로 보여줍니다.
- **기여 범위:** 팀 코드·공통 의존성과 검증 코드를 포함한 현재 포트폴리오의 프로젝트 규모입니다. 개인 담당·설계·공동 구현은 각 프로젝트 README의 기여 설명으로 확인합니다.

## 재집계

[cloc 공식 배포](https://github.com/AlDanial/cloc/releases/tag/v2.10)의 `cloc-2.10.pl`을 준비한 뒤 저장소 루트에서 실행합니다.

```bash
python3 scripts/analyze_loc.py --cloc /path/to/cloc-2.10.pl
python3 scripts/check_portfolio.py
```

[집계 스크립트](../scripts/analyze_loc.py) · [프로젝트·언어별 JSON](../metrics/loc.json) · [파일별 코드·주석·빈 줄·SHA-256](../metrics/loc-files.csv)
'''
    (ROOT / 'docs/LOC.md').write_text(overview)
    # StockAuto 문서는 출처 manifest에도 등록돼 있어 README 갱신 후 hash를 동기화합니다.
    # 원본 source_sha256은 유지해 원본과 포트폴리오 문서 변경을 구분합니다.
    manifest_path = ROOT / 'projects/stockauto-news-trader/PROVENANCE.json'
    manifest = json.loads(manifest_path.read_text())
    for entry in manifest['files']:
        if entry['path'] == 'README.md':
            content = (manifest_path.parent / entry['path']).read_bytes()
            entry.update(sha256=hashlib.sha256(content).hexdigest(), bytes=len(content),
                         modified_during_review=True)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
    print(table)


def main():
    """입력: --cloc 도구 경로. 출력: 재현 가능한 집계 산출물과 터미널 요약.
    Git 목록만 전달하고 도구 버전을 고정해 로컬 빌드 산출물이 수치에 섞이지 않게 합니다.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cloc', required=True, type=Path)
    args = parser.parse_args()
    command = ['perl', str(args.cloc.resolve())]
    version = subprocess.check_output(command + ['--version'], text=True).strip()
    if version != '2.10':
        parser.error('동일한 기준으로 비교하려면 cloc 2.10을 사용하세요.')
    tracked = subprocess.check_output(['git', 'ls-files', 'projects'], cwd=ROOT, text=True).splitlines()
    selected = {p: classify(p) for p in tracked if classify(p) is not None}
    with tempfile.TemporaryDirectory() as temp:
        listing = Path(temp) / 'files.txt'
        listing.write_text('\n'.join(selected) + '\n')
        raw = subprocess.check_output(command + ['--json', '--by-file', '--quiet',
            '--skip-uniqueness', '--force-lang=Tcl/Tk,ys', '--force-lang=Tcl/Tk,xdc',
            f'--list-file={listing}'], cwd=ROOT, text=True)
    counts = json.loads(raw)
    missing = [p for p in selected if p not in counts and (ROOT / p).read_text().strip()]
    if missing:
        raise RuntimeError(f'cloc에서 누락된 비어 있지 않은 파일: {missing}')
    rows = [{'project': Path(p).parts[1], 'path': p, 'category': selected[p],
             **{k: counts[p][k] for k in ('language', 'code', 'comment', 'blank')},
             'sha256': hashlib.sha256((ROOT / p).read_bytes()).hexdigest()}
            for p in sorted(selected) if p in counts]
    publish(rows, version)


if __name__ == '__main__':
    main()

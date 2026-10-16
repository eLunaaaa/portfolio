"""공개 CSV에서 Confidence 선정 그래프와 프레임 평가 화면을 생성한다."""
from pathlib import Path
import collections
import csv
import hashlib
import json

HERE = Path(__file__).resolve().parent
ASSET = HERE.parents[1] / 'assets' / 'ai-confidence-selection.png'
SELECTED = .60
LABELS = {'all': '전체 BBox 검증', 'testbed': '테스트베드 촬영 검증', 'val': '검증 묶음 val', 'test': '시험 묶음 test'}


def read(name):
    with (HERE / name).open(encoding='utf-8', newline='') as f:
        return list(csv.DictReader(f))


def reconcile():
    rows = read('threshold_metrics.csv')
    presence = [r for r in rows if r['evaluation_type'] == 'ioniq_presence']
    frames = read('frame_results.csv')
    assert len(frames) == len({r['frame_id'] for r in frames}) == 12873
    for row in frames:
        a = int(row['actual_ioniq'])
        p = int(float(row['maximum_ioniq_score']) >= SELECTED)
        outcome = ('TP' if p else 'FN') if a else ('FP' if p else 'TN')
        assert outcome == row['presence_outcome']
        assert p == int(row['predicted_ioniq']) and float(row['threshold']) == SELECTED
    for group in LABELS:
        counts = collections.Counter(r['presence_outcome'] for r in frames if group == 'all' or r['evaluation_group'] == group)
        summary = next(r for r in presence if r['group'] == group and float(r['threshold']) == SELECTED)
        assert all(counts[k] == int(summary[k]) for k in ['TP', 'FN', 'FP', 'TN'])
        assert sum(counts.values()) == int(summary['total_count'])
    overall = [r for r in presence if r['group'] == 'all']
    assert float(max(overall, key=lambda r: float(r['accuracy_pct']))['threshold']) == SELECTED
    return presence


def plot(rows):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib import font_manager
    for family in ['Malgun Gothic', 'Noto Sans CJK KR', 'NanumGothic']:
        try:
            font_manager.findfont(family, fallback_to_default=False)
        except ValueError:
            continue
        plt.rcParams['font.family'] = family
        break
    else:
        raise RuntimeError('Malgun Gothic, Noto Sans CJK KR, NanumGothic 중 한글 글꼴을 설치하세요.')
    plt.rcParams.update({'axes.unicode_minus': False, 'font.size': 12, 'svg.fonttype': 'none'})
    data = sorted((r for r in rows if r['group'] == 'all' and .30 <= float(r['threshold']) <= .90), key=lambda r: float(r['threshold']))
    selected = next(r for r in data if float(r['threshold']) == SELECTED)
    fig, axes = plt.subplots(1, 2, figsize=(11.6, 4.1), constrained_layout=True)
    for ax, key, title, color in zip(axes, ['accuracy_pct', 'false_positive_rate_pct'], ['대상 차종 유무 판정 정답률', '대상 차량 부재 시 오탐률'], ['#16796C', '#BD553F']):
        x = [float(r['threshold']) for r in data]
        y = [float(r[key]) for r in data]
        ax.set_facecolor('#F4F7F9')
        ax.plot(x, y, color=color, linewidth=2.5)
        ax.axvline(SELECTED, color='#526879', linestyle='--', linewidth=1.15)
        v = float(selected[key])
        ax.scatter([SELECTED], [v], color=color, s=54, zorder=3)
        ax.annotate(f'{v:.2f}%', (SELECTED, v), xytext=(10, 13), textcoords='offset points', color=color, weight='bold', fontsize=13)
        ax.set_title(title, loc='left', color='#142E43', pad=16, weight='bold')
        ax.set_xlabel('Confidence')
        ax.set_ylabel('%', rotation=0, labelpad=14)
        ax.set_xlim(.30, .90)
        ax.set_xticks([.3, .4, .5, .6, .7, .8, .9])
        ax.set_ylim((96, 99) if key == 'accuracy_pct' else (0, max(3, int(max(y)) + 1)))
        ax.grid(axis='y', color='#D9E1E8', linewidth=.8)
        for spine in ['top', 'right']:
            ax.spines[spine].set_visible(False)
        for spine in ['left', 'bottom']:
            ax.spines[spine].set_color('#CBD7E0')
        ax.tick_params(colors='#526879')
    ASSET.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(ASSET, dpi=160, facecolor='white', metadata={'Software': 'Matplotlib'})
    plt.close(fig)


HTML = r'''<!doctype html>
<html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>AI 프레임 검증 · Confidence 0.60</title>
<style>
*{box-sizing:border-box}body{margin:0;background:#f3f6f8;color:#142e43;font-family:"Malgun Gothic",system-ui,sans-serif}main{max-width:1120px;margin:auto;padding:42px 32px 60px}a{color:#226ba8}h1{font-size:32px;margin:10px 0 16px}h2{font-size:21px;margin:30px 0 16px}p{line-height:1.7;margin:8px 0}.eyebrow{color:#16796c;font-size:14px;font-weight:700}.lead{font-size:17px}.controls{margin:26px 0 0;padding:22px 0;border-top:1px solid #cbd7e0;border-bottom:1px solid #cbd7e0;display:flex;gap:24px;align-items:center;flex-wrap:wrap}select,button{padding:9px 12px;font:inherit;border:1px solid #b8c8d2;border-radius:4px;background:white;color:#142e43}label{font-size:15px;font-weight:700}input{width:230px;accent-color:#16796c;vertical-align:middle}output{font-size:23px;font-weight:700;color:#16796c;margin-left:12px}.stats{display:grid;grid-template-columns:repeat(3,1fr);gap:30px;padding:26px 0}.metric{font-size:38px;color:#16796c;font-weight:700;margin:8px 0}.metric.red{color:#bd553f}.detail{color:#526879;font-size:14px}.plots{display:grid;grid-template-columns:1fr 1fr;gap:24px}.plot{background:white;padding:20px 16px 10px;border:1px solid #dce5eb;border-radius:4px}.plot h2{margin:0 0 6px;font-size:19px}svg{width:100%;height:auto;overflow:visible}table{border-collapse:collapse;width:100%;background:white;font-size:15px}th,td{padding:13px 18px;border-bottom:1px solid #dce5eb;text-align:right}th:first-child,td:first-child{text-align:left}th{background:#eaf1f5}.two{display:grid;grid-template-columns:1fr 1fr;gap:28px}.note{font-size:14px;color:#526879}.links{margin-top:30px;font-size:14px}@media(max-width:720px){main{padding:24px 18px}.plots,.two{grid-template-columns:1fr}.stats{gap:12px}.metric{font-size:28px}h1{font-size:27px}.controls{gap:14px}}
</style>
<main><div class="eyebrow">ACE · 테스트베드 BBox 검증</div><h1>AI 프레임 검증과 Confidence 선정</h1>
<p class="lead">검증용 12,873프레임에서 임계값별 정답률을 비교해, 가장 높은 정답률을 보인 <strong>Confidence 0.60</strong>을 선정했습니다.</p>
<p>검증용 영상에 표시한 BBox 정답을 기준으로 대상 차종의 유무를 비교합니다.</p>
<div class="controls"><label>평가 묶음 <select id="group"><option value="all">전체 BBox 검증 · 12,873장</option><option value="test">시험 묶음 test · 1,633장</option><option value="val">검증 묶음 val · 1,358장</option><option value="testbed">테스트베드 촬영 검증 · 3,314장</option></select></label><label>Confidence <input id="threshold" type="range" min="0.01" max="1" step="0.01" value="0.60"><output id="value">0.60</output></label><button id="reset" type="button">선정값 0.60</button></div>
<div class="stats"><div><strong>유무 판정 정답률</strong><div id="accuracy" class="metric"></div><div id="accuracy-detail" class="detail"></div></div><div><strong>대상 차량 검출률</strong><div id="recall" class="metric"></div><div id="recall-detail" class="detail"></div></div><div><strong>부재 시 오탐률</strong><div id="fpr" class="metric red"></div><div id="fpr-detail" class="detail"></div></div></div>
<div class="plots"><section class="plot"><h2>대상 차종 유무 판정 정답률</h2><svg id="accuracy-plot" viewBox="0 0 480 250" role="img" aria-label="Confidence별 대상 차종 유무 판정 정답률"></svg></section><section class="plot"><h2>대상 차량 부재 시 오탐률</h2><svg id="fpr-plot" viewBox="0 0 480 250" role="img" aria-label="Confidence별 대상 차량 부재 시 오탐률"></svg></section></div>
<div class="two"><section><h2>프레임 유무 판정</h2><table><thead><tr><th>정답 / 예측</th><th>대상 있음</th><th>대상 없음</th></tr></thead><tbody><tr><th>대상 있음</th><td id="tp"></td><td id="fn"></td></tr><tr><th>대상 없음</th><td id="fp"></td><td id="tn"></td></tr></tbody></table></section><section><h2>모델·판정 설정</h2><p>YOLOX-Tiny 2-class · 640×480<br>검출 점수: objectness × class score<br>class별 NMS IoU: 0.65<br>장치 추론: 6프레임 간격, 최근 3회 중 2회 검출</p></section></div>
<p class="note">프레임 정답률 = (검출 정답 + 부재 정답) / 전체. 검출률 = 검출 정답 / 대상 차량이 있는 프레임. 부재 시 오탐률 = 오탐 / 대상 차량이 없는 프레임.</p>
<p class="links"><a href="README.md">평가 설명</a> · <a href="threshold_metrics.csv">임계값별 CSV</a> · <a href="frame_results.csv">프레임 상세 CSV</a> · <a href="evaluation_conditions.csv">평가 조건</a> · <a href="source_manifest.json">자료 연결 기록</a></p></main>
<script id="data" type="application/json">__DATA__</script>
<script>
const data=JSON.parse(document.getElementById('data').textContent),$=id=>document.getElementById(id),fmt=n=>Number(n).toLocaleString('ko-KR');
function graph(id,rows,key,color,t){
  const w=480,h=250,l=51,r=18,top=18,b=43,ph=h-top-b,pw=w-l-r;
  const minX=t<.3?0:.3,maxX=t>.9?1:.9,visible=rows.filter(o=>+o.threshold>=minX&&+o.threshold<=maxX);
  const values=visible.map(o=>+o[key]),minY=key==='accuracy_pct'?Math.max(0,Math.floor(Math.min(...values)*2)/2-.5):0;
  const maxY=key==='accuracy_pct'?Math.min(100,Math.ceil(Math.max(...values)*2)/2+.5):Math.max(1,Math.ceil(Math.max(...values)*2)/2);
  const x=v=>l+(v-minX)/(maxX-minX)*pw,y=v=>top+(maxY-v)/(maxY-minY)*ph;
  let parts=[];
  for(let i=0;i<=4;i++){const v=minY+(maxY-minY)*i/4;parts.push(`<line x1="${l}" y1="${y(v)}" x2="${w-r}" y2="${y(v)}" stroke="#dce5eb"/><text x="${l-9}" y="${y(v)+4}" text-anchor="end" fill="#526879" font-size="12">${v.toFixed(1)}</text>`)}
  for(let i=0;i<=6;i++){const v=minX+(maxX-minX)*i/6;parts.push(`<text x="${x(v)}" y="${h-b+23}" text-anchor="middle" fill="#526879" font-size="12">${v.toFixed(2)}</text>`)}
  parts.push(`<text x="10" y="13" fill="#526879" font-size="12">%</text><text x="${l+pw/2}" y="${h-4}" text-anchor="middle" fill="#526879" font-size="12">Confidence</text>`);
  const points=visible.map(o=>`${x(+o.threshold)},${y(+o[key])}`).join(' ');
  parts.push(`<polyline points="${points}" fill="none" stroke="${color}" stroke-width="2.7"/>`);
  const selected=rows.find(o=>Math.abs(+o.threshold-t)<1e-7);
  parts.push(`<line x1="${x(t)}" y1="${top}" x2="${x(t)}" y2="${h-b}" stroke="#526879" stroke-dasharray="5 4"/>`);
  if(selected)parts.push(`<circle cx="${x(t)}" cy="${y(+selected[key])}" r="4.5" fill="${color}"/>`);
  $(id).innerHTML=parts.join('');
}
function update(){const rows=data.filter(r=>r.group===$('group').value),t=+$('threshold').value,r=rows.find(r=>Math.abs(+r.threshold-t)<1e-7);$('value').textContent=t.toFixed(2);$('accuracy').textContent=(+r.accuracy_pct).toFixed(2)+'%';$('recall').textContent=(+r.recall_pct).toFixed(2)+'%';$('fpr').textContent=(+r.false_positive_rate_pct).toFixed(2)+'%';$('accuracy-detail').textContent=`정답 ${fmt(+r.TP + +r.TN)} / 전체 ${fmt(r.total_count)}`;$('recall-detail').textContent=`검출 ${fmt(r.TP)} / 대상 있음 ${fmt(r.positive_count)}`;$('fpr-detail').textContent=`오탐 ${fmt(r.FP)} / 대상 없음 ${fmt(r.negative_count)}`;for(const k of ['TP','FN','FP','TN'])$(k.toLowerCase()).textContent=fmt(r[k]);graph('accuracy-plot',rows,'accuracy_pct','#16796c',t);graph('fpr-plot',rows,'false_positive_rate_pct','#bd553f',t)}
$('group').addEventListener('change',update);$('threshold').addEventListener('input',update);$('reset').addEventListener('click',()=>{$('threshold').value=.60;update()});update();
</script></html>'''


def main():
    rows = reconcile()
    plot(rows)
    (HERE / 'index.html').write_text(HTML.replace('__DATA__', json.dumps(rows, ensure_ascii=False, separators=(',', ':'))), encoding='utf-8', newline='\n')
    manifest_path = HERE / 'source_manifest.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    files = [HERE / name for name in ['evaluation_conditions.csv', 'threshold_metrics.csv', 'frame_results.csv', 'selected_threshold_metrics.csv', 'index.html', 'README.md', 'render.py']]
    files.append(ASSET)
    manifest['published_files'] = [{'name': p.relative_to(HERE).as_posix() if p.is_relative_to(HERE) else '../../assets/' + p.name, 'sha256': hashlib.sha256(p.read_bytes()).hexdigest()} for p in files]
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8', newline='\n')
    print('12,873 frames reconciled; confidence chart and evaluation page generated.')


if __name__ == '__main__':
    main()

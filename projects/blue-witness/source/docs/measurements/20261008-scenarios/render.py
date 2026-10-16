"""Render the scenario summary from published CSV (Matplotlib)."""
from pathlib import Path
import csv,json
import matplotlib.pyplot as plt
from matplotlib import font_manager
ROOT=Path(__file__).resolve().parent
font=Path('C:/Windows/Fonts/malgun.ttf')
if font.exists():font_manager.fontManager.addfont(str(font));plt.rcParams['font.family']='Malgun Gothic'
else:plt.rcParams['font.family']='Noto Sans CJK KR'
plt.rcParams['axes.unicode_minus']=False
s=json.loads((ROOT/'summary.json').read_text(encoding='utf8'))
rows=list(csv.DictReader((ROOT/'scenarios.csv').open(encoding='utf8')))
groups=[('모두 가시',range(1,4)),('관측차별 화각 이탈',range(4,13)),('모두 화각 밖',range(13,16)),('관측차별 시야 가림',range(16,25)),('인터넷 단절·복구',range(25,28))]
fig=plt.figure(figsize=(10,5.1),facecolor='white')
fig.text(.05,.92,'실제 충돌 27회 · 시나리오 검증',fontsize=20,weight='bold',color='#142E43')
fig.text(.05,.853,'2026.10.08  |  9조건 × 3회  |  Confidence 0.60 · 6프레임 간격',fontsize=11,color='#506C81')
ax=fig.add_axes([.255,.16,.52,.61]);ax.set_xlim(0,110);ax.set_ylim(-.5,4.5)
for i,(label,ids) in enumerate(groups):
 subset=[r for r in rows if int(r['trial']) in ids];n=len(subset);passed=sum(r['field_result']=='pass' for r in subset)
 ax.barh(4-i,100,height=.54,color='#EAF1F6');ax.barh(4-i,100*passed/n,height=.54,color='#16796C')
 ax.text(-3,4-i,label,ha='right',va='center',fontsize=11,color='#142E43')
 ax.text(104,4-i,f'{passed} / {n}',ha='left',va='center',fontsize=12,weight='bold',color='#16796C')
ax.set_xticks([0,50,100],['0%','50%','100%']);ax.tick_params(axis='x',length=0,labelcolor='#506C81',labelsize=10);ax.set_yticks([])
for sp in ax.spines.values():sp.set_visible(False)
fig.text(.05,.065,f"시나리오 {s['field_scenario_pass']} / {s['valid_trials']}회   ·   영상 저장 {s['server_stored_videos']} / {s['recorded_videos']}건   ·   단절 후 전송 {s['recovery_stored_videos']} / 12건",fontsize=14,weight='bold',color='#142E43')
fig.savefig(ROOT/'scenario-results.png',dpi=160,facecolor='white');plt.close(fig)
print('scenario-results.png')

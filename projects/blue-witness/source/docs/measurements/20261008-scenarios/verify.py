"""Verify the published event/clip evidence using only Python's standard library."""
from pathlib import Path
import csv,hashlib,json,statistics,math
ROOT=Path(__file__).resolve().parent
def rows(name):
 with (ROOT/name).open(encoding='utf8',newline='') as f:return list(csv.DictReader(f))
def truth(x):return str(x).lower()=='true'
scenarios=rows('scenarios.csv');clips=rows('clips.csv');recovery=rows('recovery.csv')
summary=json.loads((ROOT/'summary.json').read_text(encoding='utf8'));versions=json.loads((ROOT/'versions.json').read_text(encoding='utf8'));manifest=json.loads((ROOT/'source_manifest.json').read_text(encoding='utf8'))
evidence=[json.loads(l) for l in (ROOT/'evidence.jsonl').read_text(encoding='utf8').splitlines()]
assert len(scenarios)==27 and len({r['event_id'] for r in scenarios})==27
assert len(clips)==81 and len({(r['event_id'],r['vehicle_id']) for r in clips})==81
assert len(recovery)==12 and len({r['event_id'] for r in recovery})==3
for r in scenarios:
 assert r['field_result']=='pass' and truth(r['video_count_match'])
 expected=set(filter(None,r['expected_vehicles'].split(';')));actual={c['vehicle_id'] for c in clips if c['event_id']==r['event_id']}
 assert actual==set(r['recorded_vehicles'].split(';'))
 assert actual==expected,(r['event_id'],expected,actual)
 assert len(actual)==int(r['expected_videos'])==int(r['stored_videos']) and truth(r['all_stored'])
for c in clips:
 assert truth(c['storage_ack_confirmed'])
 start,event,end=[int(c[k]) for k in ['clip_start_time_ns','event_time_ns','clip_end_time_ns']]
 assert start<event<end
 assert 7.96<=(event-start)/1e9<=8.001 and 1.96<=(end-event)/1e9<=2.001
 ready,stored=[int(c[k]) for k in ['ready_ns','stored_ack_ns']]
 # The uploader worker and producer acknowledgement run concurrently.
 assert event<ready and event<stored
 assert math.isclose((ready-event)/1e9,float(c['ready_elapsed_s']),abs_tol=1e-6)
 assert math.isclose((stored-event)/1e9,float(c['stored_elapsed_s']),abs_tol=1e-6)
 ack=[e for e in evidence if e['event_id']==c['event_id'] and e['vehicle_id']==c['vehicle_id'] and e['event']=='UPLOAD_SERVER_ACK']
 assert len(ack)==1 and ack[0]['status']=='STORED' and ack[0]['storage_ack_confirmed']=='true'
 assert c['sha256'].startswith(ack[0]['sha256_prefix'])
assert summary['video_count_matches']==summary['field_scenario_pass']==27
assert summary['vehicle_selection_matches']==27
assert summary['server_stored_videos']==81 and summary['recovery_stored_videos']==12
assert math.isclose(statistics.mean(float(c['ready_elapsed_s']) for c in clips),summary['ready_latency']['mean_s'],abs_tol=1e-6)
assert versions['confidence']==0.6 and versions['inference_frame_step']==6
observer_binaries=set()
for v in versions['vehicles']:
 assert v['release_commit']==versions['code_commit']
 if v['role']=='observer':
  assert v['model_sha256']==versions['model_sha256'];observer_binaries.add(v['binaries']['inference'])
assert len(observer_binaries)==1
for name,expected in manifest['published_files'].items():assert hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==expected,name
for f in json.loads((ROOT/'frames.json').read_text(encoding='utf8')):
 assert hashlib.sha256((ROOT/f['frame_file']).read_bytes()).hexdigest()==f['frame_sha256']
 assert next(c for c in clips if c['event_id']==f['event_id'] and c['vehicle_id']==f['vehicle_id'])['sha256']==f['source_video_sha256']
print('PASS: 27 scenarios, 81 stored videos, 12 recovery videos, version/model hashes and published checksums.')

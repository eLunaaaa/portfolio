const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../frontend/static/video-views.js'), 'utf8');
const playbackSource = fs.readFileSync(path.join(__dirname, '../frontend/static/incident-playback.js'), 'utf8');

test('관리자 영상 모드는 열 때 초기화하고 닫을 때 다시 요청하지 않음', () => {
  const adminSource = fs.readFileSync(path.join(__dirname, '../frontend/static/admin.js'), 'utf8');
  const functions = adminSource.slice(adminSource.indexOf('function closeAdminVideo()'),
    adminSource.indexOf('async function deleteEvent('));
  const calls = [];
  vm.runInNewContext(functions + '\nopenAdminVideo("EVT_TEST"); closeAdminVideo();', {
    window: {initializeVideoViews: (_video, url) => calls.push(url)},
    document: {activeElement: null},
    adminMergedVideo: {load() {}, pause() {}, removeAttribute() {}},
    adminVideoPanel: {classList: {add() {}, remove() {}}},
    adminVideoEventId: {}, adminMessage: {}, setMessage() {}, Date,
  });
  assert.deepEqual(calls, ['/api/v1/admin/events/EVT_TEST/video']);
});

function harness() {
  const buttons = ['all', 'witness', 'own'].map(view => ({dataset: {view},
    setAttribute(name, value) { this[name] = value; }}));
  const group = {querySelectorAll: () => buttons, getAttribute: () => 'message'};
  const message = {};
  const playbackButtons = ['incident', 'original'].map(mode => Object.assign(new EventTarget(), {
    dataset: {videoTarget: 'merged-video', playbackMode: mode},
  }));
  const video = Object.assign(new EventTarget(), {
    id: 'merged-video', src: '/video', readyState: 1,
    currentTime: 0, playbackRate: 1, duration: 10, paused: true, plays: 0,
    getAttribute(name) { return this[name]; },
    pause() { this.paused = true; },
    play() { this.paused = false; this.plays++; return Promise.resolve(); },
    load() { this.currentTime = 0; this.readyState = 0; this.dispatchEvent(new Event('emptied')); },
  });
  const pending = [];
  const window = {};
  const context = vm.createContext({window, AbortController, Date, Event, CustomEvent,
    document: {querySelector: () => group, getElementById: () => message,
      querySelectorAll: selector => selector === 'video' ? [video] : playbackButtons},
    fetch: () => new Promise(resolve => pending.push(resolve)),
  });
  vm.runInContext(playbackSource, context);
  vm.runInContext(source, context);
  const download = {};
  const init = url => window.initializeVideoViews(video, url, download);
  const respond = async views => {
    pending.shift()({ok: true, json: async () => ({views})});
    await new Promise(setImmediate);
  };
  const metadata = () => {video.readyState = 1; video.dispatchEvent(new Event('loadedmetadata'));};
  const playMode = mode => playbackButtons.find(button => button.dataset.playbackMode === mode)
    .dispatchEvent(new Event('click'));
  return {video, buttons, message, download, pending, init, respond, metadata, playMode};
}

test('모드 전환은 현재 위치·배속·재생 여부와 다운로드 모드를 유지함', async () => {
  const h = harness();
  h.init('/events/A/video');
  await h.respond({all: true, witness: true, own: true});
  Object.assign(h.video, {currentTime: 7.5, playbackRate: 0.25, paused: false});
  h.buttons[1].onclick();
  assert.equal(h.buttons[1]['aria-pressed'], 'true');
  assert.match(h.message.textContent, /목격 차량 영상은/);
  h.metadata();
  assert.equal(h.video.currentTime, 7.5);
  assert.equal(h.video.playbackRate, 0.25);
  assert.equal(h.video.paused, false);
  assert.equal(h.download.href, '/events/A/video/download?view=witness');
});

test('내 차량 영상이 제외되면 선택 불가이고 사고를 바꾸면 전체 보기로 초기화함', async () => {
  const h = harness();
  h.init('/events/A/video');
  await h.respond({all: true, witness: true, own: false});
  assert.equal(h.buttons[2].disabled, true);
  assert.match(h.message.textContent, /전체 영상은/);
  h.buttons[1].onclick();
  h.init('/events/B/video');
  assert.equal(h.buttons[0]['aria-pressed'], 'true');
  assert.match(h.message.textContent, /전체 영상은/);
});

test('창을 닫으면 로딩 중 모드 전환이 뒤늦게 재생되지 않음', async () => {
  const h = harness();
  h.init('/events/A/video');
  await h.respond({all: true, witness: true, own: true});
  h.video.paused = false;
  h.buttons[2].onclick();
  assert.match(h.message.textContent, /내 차량 영상은/);
  h.video.dispatchEvent(new Event('cancelpendingplayback'));
  h.metadata();
  assert.equal(h.video.plays, 0);
  assert.equal(h.video.paused, true);
});

test('이전 사고의 늦은 모드 응답은 현재 사고의 선택 상태를 변경하지 않음', async () => {
  const h = harness();
  h.init('/events/A/video');
  h.init('/events/B/video');
  await h.respond({all: true, witness: true, own: true});
  assert.equal(h.buttons[0].disabled, true);
  await h.respond({all: true, witness: false, own: false});
  assert.equal(h.buttons[1].disabled, true);
  assert.equal(h.buttons[2].disabled, true);
  h.buttons[2].onclick();
  assert.equal(h.buttons[0]['aria-pressed'], 'true');
});

test('로딩 중 연속으로 모드를 바꿔도 원래 위치·배속·재생 상태를 유지함', async () => {
  const h = harness();
  h.init('/events/A/video');
  await h.respond({all: true, witness: true, own: true});
  Object.assign(h.video, {currentTime: 5.5, playbackRate: 0.25, paused: false});
  h.buttons[1].onclick();
  h.buttons[2].onclick();
  h.metadata();
  assert.equal(h.video.currentTime, 5.5);
  assert.equal(h.video.playbackRate, 0.25);
  assert.equal(h.video.paused, false);
  assert.equal(h.video.plays, 1);
  assert.equal(h.download.href, '/events/A/video/download?view=own');
});

test('보기 전환 중 누른 최신 재생 버튼을 새 영상에 한 번 적용함', async () => {
  for (const mode of ['incident', 'original']) {
    for (const duration of [6, 10]) {
      const h = harness();
      h.init('/events/A/video');
      await h.respond({all: true, witness: true, own: true});
      Object.assign(h.video, {currentTime: 4, playbackRate: 1, paused: true, duration});
      h.buttons[1].onclick();
      h.playMode(mode === 'incident' ? 'original' : 'incident');
      h.playMode(mode);
      h.buttons[2].onclick();
      h.metadata();
      assert.equal(h.video.currentTime, mode === 'incident' && duration > 7 ? 7 : 0);
      assert.equal(h.video.playbackRate, mode === 'incident' ? 0.25 : 1);
      assert.equal(h.video.paused, false);
      assert.equal(h.video.plays, 1);
    }
  }
});

test('보기 전환 중 재생을 요청한 뒤 창을 닫거나 사고를 바꾸면 예약을 취소함', async () => {
  for (const action of ['close', 'new-event']) {
    const h = harness();
    h.init('/events/A/video');
    await h.respond({all: true, witness: true, own: true});
    h.buttons[1].onclick();
    h.playMode('incident');
    if (action === 'close') h.video.dispatchEvent(new Event('cancelpendingplayback'));
    else h.init('/events/B/video');
    h.metadata();
    assert.equal(h.video.paused, true);
    assert.equal(h.video.plays, 0);
  }
});

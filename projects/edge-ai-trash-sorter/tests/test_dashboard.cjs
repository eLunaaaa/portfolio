const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const html = fs.readFileSync(path.join(__dirname, '../server/templates/index.html'), 'utf8');
const script = html.match(/<script>([\s\S]*?)<\/script>/)[1];
const nodes = {};
for (const id of ['bin-t1', 'bin-t2', 'detail-modal', 'modal-title', 'modal-list', 'connection-status']) {
  const classes = new Set();
  nodes[id] = {style:{}, textContent:'', innerText:'', innerHTML:'', classes,
    classList:{add: c => classes.add(c), remove: (...cs) => cs.forEach(c => classes.delete(c))}};
}
let response = {t1:{ge:null,ca:null,gl:null,pe:null},t2:{ge:null,ca:null,gl:null,pe:null}};
let fail = false;
const context = vm.createContext({
  document:{getElementById:id => nodes[id]}, window:{}, setInterval:() => {},
  console:{error:() => {}},
  fetch:async () => {
    if (fail) throw new Error('Offline');
    return {ok:true, json:async () => response};
  }
});
vm.runInContext(script, context);
const flush = () => new Promise(resolve => setImmediate(resolve));
(async () => {
  context.updateDashboard(); await flush();
  context.openModal('t1');
  assert(nodes['modal-list'].innerHTML.includes('확인 중'));
  response.t1 = {ge:true,ca:true,gl:true,pe:false};
  context.updateDashboard(); await flush();
  assert(nodes['bin-t1'].classes.has('level-3'));
  assert(!nodes['bin-t2'].classes.has('level-3'));
  assert(nodes['modal-list'].innerHTML.includes('수거 필요'));
  assert(nodes['modal-list'].innerHTML.includes('여유 있음'));
  response.t1 = {ge:false,ca:false,gl:false,pe:false};
  context.updateDashboard(); await flush();
  assert(!nodes['bin-t1'].classes.has('level-3'));
  context.closeModal(); assert.equal(nodes['detail-modal'].style.display,'none');
  fail = true; context.updateDashboard(); await flush();
  assert(nodes['connection-status'].textContent.includes('서버 연결 실패'));
  console.log('Dashboard unknown/full/empty, modal and offline checks passed');
})().catch(error => {console.error(error);process.exitCode=1;});

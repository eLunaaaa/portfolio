const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "../frontend/static/portal.js"), "utf8");
const common = fs.readFileSync(path.join(__dirname, "../frontend/static/common.js"), "utf8");
function functionSource(text, name) {
  const match = text.match(new RegExp(`^(?:async )?function ${name}\\([^]*?^\\}`, "m"));
  assert.ok(match, `production function ${name}`);
  return match[0];
}
const selectionState = source.slice(source.indexOf("let selectedEventId ="), source.indexOf("let issuedAccessCodeInMemory ="));
const submitHandler = source.slice(source.indexOf('accessCodeForm.addEventListener('), source.indexOf('downloadMergedVideoButton.addEventListener('));
const flush = () => new Promise(setImmediate);

function harness() {
  const nodes = new Map();
  const requests = [];
  const opened = [];
  const viewed = [];
  const alerts = [];
  const timers = [];
  let cookie = null;
  function element(id) {
    if (!nodes.has(id)) {
      const classes = new Set();
      const attributes = new Map();
      const listeners = new Map();
      nodes.set(id, {
        textContent: "", value: "", hidden: true, disabled: false, src: "", focusCount: 0,
        classList: {
          add: (value) => classes.add(value), remove: (value) => classes.delete(value),
          contains: (value) => classes.has(value),
        },
        setAttribute: (name, value) => attributes.set(name, value),
        getAttribute: (name) => attributes.get(name),
        removeAttribute(name) { attributes.delete(name); if (name === "src") this.src = ""; },
        pause() {}, load() {}, focus() { this.focusCount++; }, select() {}, dispatchEvent() {},
        addEventListener: (name, listener) => listeners.set(name, listener),
        dispatch: (name) => listeners.get(name)?.({ preventDefault() {} }),
      });
    }
    return nodes.get(id);
  }
  const context = {
    document: { querySelector: element, body: element("body") },
    console: { log() {}, error() {}, warn() {} },
    FormData: class {}, Event: class {}, currentPortalVehicles: [],
    setDetailFlip() {}, latestIncidentTime: (value) => value,
    setMessage: (node, message, type) => { node.textContent = message; node.type = type; },
    getErrorMessage: (error) => error.message,
    getApiErrorMessage: (data, fallback) => data.detail || fallback,
    stopAccessCodeCountdown() {}, startAccessCodeCountdown() {}, copyTextWithTemporaryInput: () => true,
    markEventViewed: (id) => viewed.push(id),
    navigator: { clipboard: { writeText: async () => {} } },
    window: {
      isSecureContext: true, alert: (message) => alerts.push(message),
      openEvidenceViewer: (id, button) => opened.push({ id, button }),
      initializeVideoViews(video, base) { context.videoBase = base; },
      revealEvidenceVideo() {}, setTimeout: (callback) => timers.push(callback),
    },
    fetch(url, options = {}) {
      let resolve, reject;
      const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
      requests.push({ url, options, reject, done: false, respond(data = {}, status = 200) {
        this.done = true;
        if (status < 400 && url === "/api/v1/access/logout") cookie = null;
        if (status < 400 && url === "/api/v1/access/verify") cookie = JSON.parse(options.body).event_id;
        resolve({ ok: status < 400, status, headers: { get: () => "application/json" },
          json: async () => data, text: async () => String(data) });
      } });
      return promise;
    },
  };
  for (const name of ["accessCodeInput", "unlockArea", "videoArea", "detailPanel", "portalMessage", "accessCodeModal",
    "mergedVideo", "downloadMergedVideoButton", "openAccessCodeModalButton", "confirmIdentityVerificationButton",
    "popupAccessCode", "popupAccessCodeCountdown", "popupAccessCodeStatus", "copyPopupAccessCodeButton",
    "identityVerificationStage", "issuedCodeStage", "accessCodeForm"]) context[name] = element(name);
  const sandbox = vm.createContext(context);
  vm.runInContext(selectionState + '\nlet issuedAccessCodeInMemory = ""; let accessCodeExpiresAtMilliseconds = 0;', sandbox);
  vm.runInContext(functionSource(common, "apiRequest"), sandbox);
  for (const name of ["resetAccessCodeModal", "openAccessCodeModal", "closeAccessCodeModal", "resetMergedVideoAccess",
    "enableMergedVideoDownload", "clearAccidentAccessSession", "selectEvent", "confirmMockIdentityVerification",
    "copyPopupAccessCode", "updateVideoAccess"]) vm.runInContext(functionSource(source, name), sandbox);
  vm.runInContext(submitHandler, sandbox);
  const run = (script) => vm.runInContext(script, sandbox);
  return {
    nodes, requests, opened, viewed, alerts, timers, context, element, run, cookie: () => cookie,
    select: (id) => context.selectEvent(id, id),
    submit: () => element("accessCodeForm").dispatch("submit"),
    async respond(request, data = {}, status = 200) { request.respond(data, status); await flush(); },
  };
}

function detail(id) {
  return { event_id: id, accident_vehicle_id: "CAR_02", event_time_kst: "2026-10-10 12:00:00", evidence_count: 4, merge_status: "MERGED" };
}
function pending(h, suffix) {
  const request = h.requests.find((item) => !item.done && item.url.endsWith(suffix));
  assert.ok(request, `pending ${suffix}`);
  return request;
}
async function finishDetail(h, id) {
  await h.respond(pending(h, `/events/${id}`), detail(id));
  await h.respond(pending(h, `/events/${id}/access-status`), { merged_video_available: true, unlocked: false });
}
async function selectReady(h, id) {
  const operation = h.select(id);
  await flush();
  await h.respond(pending(h, "/access/logout"), {}, 204);
  await finishDetail(h, id);
  await operation;
}

test("normal selection, access-code issuance and unlock retain the same event and video options", async () => {
  const h = harness();
  await selectReady(h, "A");
  assert.equal(h.element("#detail-event-id").textContent, "A");
  assert.equal(h.element("unlockArea").classList.contains("hidden"), false);
  assert.equal(h.element("videoArea").classList.contains("hidden"), true);
  h.context.openAccessCodeModal();
  const issue = h.context.confirmMockIdentityVerification();
  await h.respond(pending(h, "/events/A/access-code"), { access_code: "A-CODE", valid_for_seconds: 300 });
  await issue;
  assert.equal(h.element("popupAccessCode").textContent, "A-CODE");
  h.element("accessCodeInput").value = "A-CODE";
  const unlock = h.submit();
  await flush();
  const verify = pending(h, "/access/verify");
  assert.deepEqual(JSON.parse(verify.options.body), { event_id: "A", access_code: "A-CODE" });
  await h.respond(verify);
  await h.respond(pending(h, "/events/A/access-status"), { merged_video_available: true, unlocked: true });
  await unlock;
  assert.equal(h.cookie(), "A");
  assert.match(h.element("mergedVideo").src, /^\/api\/v1\/portal\/events\/A\/video\?cache=/);
  assert.equal(h.context.videoBase, "/api/v1/portal/events/A/video");
  assert.equal(h.element("downloadMergedVideoButton").download, "A_merged.mp4");
  assert.equal(h.element("videoArea").classList.contains("hidden"), false);
  assert.deepEqual(h.viewed, ["A"]);
});

for (const failed of [false, true]) test(`late detail ${failed ? "failure" : "success"} cannot replace a newer selection`, async () => {
  const h = harness();
  const older = h.select("A");
  await flush();
  await h.respond(pending(h, "/access/logout"), {}, 204);
  await selectReady(h, "B");
  const message = h.element("portalMessage").textContent;
  await h.respond(pending(h, "/events/A"), failed ? { detail: "old error" } : detail("A"), failed ? 500 : 200);
  await h.respond(pending(h, "/events/A/access-status"), { merged_video_available: true, unlocked: true });
  await older;
  assert.equal(h.element("#detail-event-id").textContent, "B");
  assert.equal(h.element("portalMessage").textContent, message);
  assert.equal(h.run("selectedEventId"), "B");
  assert.deepEqual(h.viewed, ["B"]);
  assert.equal(h.opened.length, 1);
  assert.equal(h.element("videoArea").classList.contains("hidden"), true);
});

for (const failed of [false, true]) test(`pending session reset ${failed ? "failure" : "success"} completes before the latest selection reset`, async () => {
  const h = harness();
  const older = h.select("A");
  await flush();
  const oldReset = pending(h, "/access/logout");
  const newer = h.select("B");
  await flush();
  assert.equal(h.requests.length, 1);
  await h.respond(oldReset, {}, failed ? 500 : 204);
  await h.respond(pending(h, "/access/logout"), {}, 204);
  await finishDetail(h, "B");
  await Promise.all([older, newer]);
  assert.equal(h.requests.some((request) => request.url.includes("/events/A")), false);
  assert.equal(h.element("#detail-event-id").textContent, "B");
  assert.deepEqual(h.alerts, []);
});

test("new selection clears a pending old verification cookie before showing its own access form", async () => {
  const h = harness();
  await selectReady(h, "A");
  h.element("accessCodeInput").value = "A-CODE";
  const verifyA = h.submit();
  await flush();
  const verification = pending(h, "/access/verify");
  const newer = h.select("B");
  await flush();
  assert.equal(h.requests.filter((request) => request.url.endsWith("/access/logout")).length, 1);
  await h.respond(verification);
  await verifyA;
  assert.equal(h.cookie(), "A");
  await h.respond(pending(h, "/access/logout"), {}, 204);
  await finishDetail(h, "B");
  await newer;
  assert.equal(h.cookie(), null);
  assert.equal(h.requests.filter((request) => request.url.endsWith("/events/A/access-status")).length, 1);
  assert.equal(h.element("mergedVideo").src, "");
  assert.equal(h.element("unlockArea").classList.contains("hidden"), false);
});

for (const failed of [false, true]) test(`old verification-status ${failed ? "failure" : "success"} cannot change the new video state`, async () => {
  const h = harness();
  await selectReady(h, "A");
  h.element("accessCodeInput").value = "A-CODE";
  const verification = h.submit();
  await flush();
  await h.respond(pending(h, "/access/verify"));
  const oldStatus = pending(h, "/events/A/access-status");
  await selectReady(h, "B");
  const message = h.element("portalMessage").textContent;
  await h.respond(oldStatus, failed ? { detail: "old verification error" } : { merged_video_available: true, unlocked: true }, failed ? 403 : 200);
  await verification;
  assert.equal(h.element("mergedVideo").src, "");
  assert.equal(h.element("portalMessage").textContent, message);
  assert.equal(h.element("videoArea").classList.contains("hidden"), true);
});

for (const failed of [false, true]) test(`old code issuance ${failed ? "failure" : "success"} cannot change the new popup`, async () => {
  const h = harness();
  await selectReady(h, "A");
  h.context.openAccessCodeModal();
  const oldIssue = h.context.confirmMockIdentityVerification();
  const request = pending(h, "/events/A/access-code");
  await selectReady(h, "B");
  h.context.openAccessCodeModal();
  assert.equal(h.element("confirmIdentityVerificationButton").disabled, false);
  const newIssue = h.context.confirmMockIdentityVerification();
  await h.respond(request, failed ? { detail: "old issuance error" } : { access_code: "A-CODE" }, failed ? 500 : 200);
  await oldIssue;
  assert.equal(h.element("popupAccessCode").textContent, "-");
  assert.equal(h.element("confirmIdentityVerificationButton").disabled, true);
  assert.deepEqual(h.alerts, []);
  await h.respond(pending(h, "/events/B/access-code"), { access_code: "B-CODE" });
  await newIssue;
  assert.equal(h.element("popupAccessCode").textContent, "B-CODE");
  assert.equal(h.element("confirmIdentityVerificationButton").disabled, false);
});

test("copy completion and its delayed close cannot touch a different event popup", async () => {
  const h = harness();
  await selectReady(h, "A");
  h.run('issuedAccessCodeInMemory = "A-CODE"');
  await h.context.copyPopupAccessCode();
  assert.equal(h.timers.length, 1);
  await selectReady(h, "B");
  h.context.openAccessCodeModal();
  h.timers[0]();
  assert.equal(h.element("accessCodeModal").hidden, false);
  assert.equal(h.element("accessCodeInput").value, "");
});

test("code and video actions stay unavailable until the selected detail has loaded", async () => {
  const h = harness();
  const selection = h.select("A");
  h.context.openAccessCodeModal();
  await h.context.confirmMockIdentityVerification();
  h.element("accessCodeInput").value = "A-CODE";
  await h.submit();
  await flush();
  assert.equal(h.requests.length, 1);
  assert.equal(h.requests[0].url, "/api/v1/access/logout");
  assert.equal(h.element("accessCodeModal").hidden, true);
  await h.respond(h.requests[0], {}, 204);
  await finishDetail(h, "A");
  await selection;
});

test("latest detail errors keep actions locked and the next selection recovers", async () => {
  const h = harness();
  const selection = h.select("A");
  await flush();
  await h.respond(pending(h, "/access/logout"), {}, 204);
  await h.respond(pending(h, "/events/A"), { detail: "detail failed" }, 500);
  await selection;
  assert.equal(h.run("selectedEventReady"), false);
  assert.equal(h.element("portalMessage").textContent, "detail failed");
  assert.equal(h.element("videoArea").classList.contains("hidden"), true);
  await selectReady(h, "B");
  assert.equal(h.run("selectedEventReady"), true);
  assert.equal(h.element("#detail-event-id").textContent, "B");
});

test("a rejected old verification does not overwrite the new selection error or stop its reset", async () => {
  const h = harness();
  await selectReady(h, "A");
  h.element("accessCodeInput").value = "A-CODE";
  const verification = h.submit();
  await flush();
  const request = pending(h, "/access/verify");
  const selection = h.select("B");
  request.done = true;
  request.reject(new Error("old network error"));
  await verification;
  await flush();
  await h.respond(pending(h, "/access/logout"), {}, 204);
  await finishDetail(h, "B");
  await selection;
  assert.equal(h.element("#detail-event-id").textContent, "B");
  assert.doesNotMatch(h.element("portalMessage").textContent, /old network error/);
});

test("render errors also keep authentication disabled", async () => {
  const h = harness();
  h.context.setDetailFlip = () => { throw new Error("render failed"); };
  const selection = h.select("A");
  await flush();
  await h.respond(pending(h, "/access/logout"), {}, 204);
  await finishDetail(h, "A");
  await selection;
  assert.equal(h.run("selectedEventReady"), false);
  assert.equal(h.element("portalMessage").textContent, "render failed");
});

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const sources = ["incident-playback.js", "evidence-viewer.js"].map((file) =>
  fs.readFileSync(path.join(__dirname, "../frontend/static", file), "utf8"),
);
const portalSource = fs.readFileSync(path.join(__dirname, "../frontend/static/portal.js"), "utf8");
const portalEscapeSource = portalSource.match(
  /document\.addEventListener\(\r?\n\s*"keydown",[\s\S]*?\r?\n\);/,
)[0];

function harness({ duration = 30, readyState = 1 } = {}) {
  class Element extends EventTarget {
    constructor(id = "") {
      super();
      Object.assign(this, {
        id, hidden: false, dataset: {}, style: {}, children: [],
        classList: { add() {}, remove() {} },
      });
    }
    append(...nodes) {
      this.children.push(...nodes);
      for (const node of nodes) node.parentElement = this;
    }
    setAttribute() {}
    focus() {}
    querySelector(selector) {
      return selector === ".evidence-viewer-content"
        ? this.children.find((node) => node.className === "evidence-viewer-content")
        : null;
    }
    querySelectorAll(selector) { return selector === "video" ? [video] : []; }
  }
  const video = new Element("admin-merged-video");
  Object.assign(video, {
    src: "test.mp4", readyState, duration, currentTime: 0, playbackRate: 1,
    paused: true, playCalls: 0,
    getAttribute(name) { return name === "src" ? this.src : null; },
    play() { this.paused = false; this.playCalls += 1; return Promise.resolve(); },
    pause() { this.paused = true; },
  });
  const buttons = ["incident", "original"].map((mode) => {
    const button = new Element();
    button.dataset = { videoTarget: video.id, playbackMode: mode };
    return button;
  });
  const panel = new Element("admin-video-panel");
  const accessCodeModal = new Element("access-code-modal");
  accessCodeModal.hidden = true;
  const document = Object.assign(new EventTarget(), {
    body: new Element(), documentElement: new Element(), activeElement: null,
    querySelectorAll: (selector) => selector === "video" ? [video] : buttons,
    getElementById: (id) => id === panel.id ? panel : null,
    createElement: () => new Element(),
    querySelector: (selector) => selector === '.modal-backdrop:not([hidden])'
      && !accessCodeModal.hidden ? accessCodeModal : null,
  });
  const window = {
    scrollX: 0, scrollY: 0, matchMedia: () => ({ matches: false }),
    addEventListener() {}, scrollTo() {},
  };
  const context = vm.createContext({
    document, window, HTMLElement: Element, Event, CustomEvent, fitFrozenModalBackground() {},
    accessCodeModal, closeAccessCodeModal() { accessCodeModal.hidden = true; },
  });
  // portal.html loads the access modal's key listener before the evidence viewer.
  vm.runInContext(portalEscapeSource, context);
  for (const source of sources) vm.runInContext(source, context);
  return {
    video, panel, accessCodeModal,
    click: (mode) => buttons.find((button) => button.dataset.playbackMode === mode)
      .dispatchEvent(new Event("click")),
    open: () => window.openEvidenceViewer(panel.id),
    close: () => window.closeEvidenceViewer(panel.id),
    metadata() { video.readyState = 1; video.dispatchEvent(new Event("loadedmetadata")); },
    escape() {
      const event = new Event("keydown", { cancelable: true });
      event.key = "Escape";
      document.dispatchEvent(event);
    },
  };
}

test("Escape closes the access modal first and the evidence viewer on the next press", () => {
  const h = harness();
  h.open();
  h.accessCodeModal.hidden = false;
  h.escape();
  assert.equal(h.accessCodeModal.hidden, true);
  assert.equal(h.panel.parentElement.hidden, false);
  h.escape();
  assert.equal(h.panel.parentElement.hidden, true);
});

test("closing before metadata cancels queued playback, including after reopening", () => {
  const h = harness({ readyState: 0 });
  h.open();
  h.click("incident");
  assert.equal(h.video.playCalls, 0);
  h.close();
  h.metadata();
  assert.equal(h.panel.parentElement.hidden, true);
  assert.equal(h.video.paused, true);
  assert.equal(h.video.playCalls, 0);
  h.open();
  h.metadata();
  assert.equal(h.video.playCalls, 0);
  h.click("incident");
  assert.equal(h.video.playCalls, 1);
  assert.equal(h.video.currentTime, 7);
  assert.equal(h.video.playbackRate, 0.25);
});

test("closing pauses an already playing video", () => {
  const h = harness();
  h.open();
  h.click("original");
  assert.equal(h.video.paused, false);
  h.close();
  assert.equal(h.video.paused, true);
});

test("incident and original modes reset position and speed on repeated clicks", () => {
  for (const duration of [6, 7, 7.01, 30]) {
    const h = harness({ duration });
    for (let repeat = 0; repeat < 2; repeat += 1) {
      h.click("incident");
      assert.equal(h.video.currentTime, duration > 7 ? 7 : 0);
      assert.equal(h.video.playbackRate, 0.25);
      h.click("original");
      assert.equal(h.video.currentTime, 0);
      assert.equal(h.video.playbackRate, 1);
    }
    assert.equal(h.video.playCalls, 4);
  }
});

test("the latest mode clicked before metadata is played once", () => {
  for (const mode of ["incident", "original"]) {
    const h = harness({ readyState: 0 });
    h.click(mode === "incident" ? "original" : "incident");
    h.click(mode);
    h.metadata();
    assert.equal(h.video.playCalls, 1);
    assert.equal(h.video.currentTime, mode === "incident" ? 7 : 0);
    assert.equal(h.video.playbackRate, mode === "incident" ? 0.25 : 1);
  }
});

test("changing the video clears pending playback and resets speed", () => {
  const h = harness();
  h.click("incident");
  h.video.pause();
  h.video.readyState = 0;
  h.click("incident");
  h.video.dispatchEvent(new Event("emptied"));
  assert.equal(h.video.playbackRate, 1);
  h.metadata();
  assert.equal(h.video.playCalls, 1);
  assert.equal(h.video.paused, true);
});

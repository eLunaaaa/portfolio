const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const source = fs.readFileSync(
  path.join(__dirname, "../frontend/static/vehicle-registration.js"), "utf8",
);
const portalSource = fs.readFileSync(path.join(__dirname, "../frontend/static/portal.js"), "utf8");
// Execute the production loader so its handled network failures are covered, too.
const overviewSource = portalSource.match(/async function loadOverview\(\) \{[\s\S]*?\n\}/)[0];
const commonSource = fs.readFileSync(path.join(__dirname, "../frontend/static/common.js"), "utf8");
const backgroundSource = commonSource.match(/function fitFrozenModalBackground\(scrollY\) \{[\s\S]*?\n\}/)[0];

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}

function harness(apiRequest, loadOverview = async () => {}, useProductionOverview = false) {
  const nodes = new Map();
  const timers = [];
  function element(id) {
    if (!nodes.has(id)) nodes.set(id, {
      id, hidden: true, disabled: false, textContent: "", value: "", checked: false,
      style: { position: "", top: "", left: "", right: "", width: "", overflow: "", minHeight: "" },
      offsetHeight: 2000,
      addEventListener() {}, focus() {}, reset() {}, classList: { add() {}, remove() {}, toggle() {} },
    });
    return nodes.get(id);
  }
  const context = vm.createContext({
    document: {
      getElementById: element, querySelectorAll: () => [], querySelector: () => null,
      addEventListener() {}, body: element("body"), documentElement: element("html"),
    },
    vehicleList: element("vehicle-list"), portalMessage: element("portal-message"),
    currentPortalVehicles: [], apiRequest, loadOverview, Error,
    userName: element("user-name"), vehicleCount: element("vehicle-count"), eventCount: element("event-count"),
    renderVehicles() {}, renderEvents() {}, loadViewedEvents() {}, getErrorMessage: (error) => error.message,
    setMessage: (node, text, type) => { node.textContent = text; node.type = type; },
    console: { error() {} }, window: {
      scrollX: 0, scrollY: 400, innerHeight: 800,
      addEventListener() {}, scrollTo() {},
      setTimeout(callback) { timers.push(callback); },
    },
  });
  vm.runInContext(backgroundSource, context);
  if (useProductionOverview) vm.runInContext(overviewSource, context);
  vm.runInContext(source, context);
  return { nodes, timers, run: (script) => vm.runInContext(script, context) };
}

function begin(h, purpose, id) {
  h.run(`beginVehicleVerification(${JSON.stringify(purpose)}, {
    vehicle_id: ${JSON.stringify(id)}, license_plate: "12가3456",
  })`);
}

function openForm(h, id = "A") {
  h.run(`openVehicleRegisterModal("grant-${id}", {
    vehicle_id: "${id}", license_plate: "12가3456", vehicle_model: "sedan",
    icon_key: "sedan", display_name: "old nickname",
  });
  vehicleLicensePlateInput.value = "12가9999";
  vehicleModelInput.value = "sedan";
  vehicleDisplayNameInput.value = "new nickname";`);
}

function submit(h) {
  return h.run("submitVehicleRegistration({ preventDefault() {} })");
}

// 실제 배경 위치 보정 함수로 모바일 하단 여백과 닫힘 후 위치 복원을 검증함.
test("frozen background keeps a normal scroll position", () => {
  const h = harness(async () => ({}));
  assert.equal(h.run("fitFrozenModalBackground(400)"), 400);
  assert.equal(h.nodes.get("body").style.top, "-400px");
});

test("frozen background clamps only the bottom gap after viewport growth", () => {
  const h = harness(async () => ({}));
  h.run("window.visualViewport = { height: 900, offsetTop: 0 }");
  assert.equal(h.run("fitFrozenModalBackground(1200)"), 1100);
  assert.equal(h.nodes.get("body").style.minHeight, "");
  h.run("window.visualViewport.height = 1000");
  assert.equal(h.run("fitFrozenModalBackground(1200)"), 1000);
});

test("closing a vehicle dialog restores original background styles and scroll", () => {
  const h = harness(async () => ({}));
  h.run("window.scrollTo = (value) => { window.restoredPosition = value; }");
  openForm(h);
  assert.equal(h.nodes.get("body").style.position, "fixed");
  assert.equal(h.nodes.get("body").style.top, "-400px");
  h.run("closeVehicleRegisterModal()");
  assert.equal(h.nodes.get("body").style.position, "");
  assert.equal(h.nodes.get("body").style.top, "");
  assert.equal(h.run("window.restoredPosition.top"), 400);
});

test("closing A verification prevents its late response from deleting A or closing B", async () => {
  const pending = deferred();
  const calls = [];
  const h = harness((url, options) => { calls.push({ url, options }); return pending.promise; });
  begin(h, "delete", "A");
  const operation = h.run("finishVehicleVerification()");
  h.run("closeVehicleVerificationModal()");
  begin(h, "update", "B");
  pending.resolve({ verification_token: "old-grant" });
  await operation;
  assert.equal(calls.length, 1);
  assert.equal(h.nodes.get("vehicle-verification-modal").hidden, false);
  assert.equal(h.run("pendingVehicleOperation.vehicle.vehicle_id"), "B");
  assert.equal(h.nodes.get("vehicle-register-modal").hidden, true);
});

test("a stale verification failure cannot overwrite or re-enable B's pending verification", async () => {
  const oldRequest = deferred();
  const newRequest = deferred();
  let requests = 0;
  const h = harness(() => ++requests === 1 ? oldRequest.promise : newRequest.promise);
  begin(h, "update", "A");
  const oldOperation = h.run("finishVehicleVerification()");
  h.run("closeVehicleVerificationModal()");
  begin(h, "update", "B");
  const newOperation = h.run("finishVehicleVerification()");
  oldRequest.reject(new Error("old request failed"));
  await oldOperation;
  assert.equal(h.nodes.get("vehicle-verification-message").textContent, "");
  assert.equal(h.nodes.get("confirm-vehicle-ownership-button").disabled, true);
  newRequest.resolve({ verification_token: "B-grant" });
  await newOperation;
  assert.equal(h.run("editingVehicle.vehicle_id"), "B");
});

test("a closed registration verification cannot reopen a form after its response arrives", async () => {
  const pending = deferred();
  const h = harness(() => pending.promise);
  h.run('beginVehicleVerification("register")');
  const operation = h.run("finishVehicleVerification()");
  h.run("closeVehicleVerificationModal()");
  pending.resolve({ verification_token: "old-grant" });
  await operation;
  assert.equal(h.nodes.get("vehicle-register-modal").hidden, true);
  assert.equal(h.run("pendingVehicleOperation"), null);
});

test("delete failure is visible in the still-open confirmation and allows a fresh verification", async () => {
  const calls = [];
  const h = harness(async (url, options) => {
    calls.push({ url, method: options.method });
    if (url.endsWith("/verification")) return { verification_token: "grant" };
    throw new Error("DELETE connection lost");
  });
  begin(h, "delete", "A");
  await h.run("finishVehicleVerification()");
  assert.equal(h.nodes.get("vehicle-verification-modal").hidden, false);
  assert.match(h.nodes.get("vehicle-verification-message").textContent, /DELETE connection lost/);
  assert.equal(h.nodes.get("confirm-vehicle-ownership-button").disabled, false);
  await h.run("finishVehicleVerification()");
  assert.deepEqual(calls.map((call) => call.method), ["POST", "DELETE", "POST", "DELETE"]);
});

test("delete failure after closing A is shown on the portal without altering B", async () => {
  const deletion = deferred();
  const started = deferred();
  const h = harness((url) => {
    if (url.endsWith("/verification")) return Promise.resolve({ verification_token: "grant" });
    started.resolve();
    return deletion.promise;
  });
  begin(h, "delete", "A");
  const operation = h.run("finishVehicleVerification()");
  await started.promise;
  h.run("closeVehicleVerificationModal()");
  begin(h, "update", "B");
  deletion.reject(new Error("DELETE connection lost"));
  await operation;
  assert.match(h.nodes.get("portal-message").textContent, /DELETE connection lost/);
  assert.equal(h.nodes.get("vehicle-verification-modal").hidden, false);
  assert.equal(h.run("pendingVehicleOperation.vehicle.vehicle_id"), "B");
  assert.equal(h.nodes.get("vehicle-verification-message").textContent, "");
});

test("a completed in-flight deletion does not close a newly opened verification", async () => {
  const deletion = deferred();
  const started = deferred();
  const h = harness((url) => {
    if (url.endsWith("/verification")) return Promise.resolve({ verification_token: "grant" });
    started.resolve();
    return deletion.promise;
  });
  begin(h, "delete", "A");
  const operation = h.run("finishVehicleVerification()");
  await started.promise;
  h.run("closeVehicleVerificationModal()");
  begin(h, "update", "B");
  deletion.resolve({});
  await operation;
  assert.equal(h.nodes.get("vehicle-verification-modal").hidden, false);
  assert.equal(h.run("pendingVehicleOperation.vehicle.vehicle_id"), "B");
  assert.equal(h.nodes.get("portal-message").type, "success");
});

test("vehicle details and nickname use one PATCH; a saved token cannot be submitted again", async () => {
  const calls = [];
  const h = harness(async (url, options) => {
    calls.push({ url, options });
    return { vehicle_id: "A", license_plate: "12가9999" };
  });
  openForm(h);
  await submit(h);
  await submit(h);
  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, "/api/v1/portal/vehicles/A");
  assert.equal(calls[0].options.method, "PATCH");
  assert.equal(calls[0].options.headers["X-Vehicle-Verification"], "grant-A");
  assert.deepEqual(JSON.parse(calls[0].options.body), {
    license_plate: "12가9999", vehicle_model: "sedan", display_name: "new nickname", icon_key: "sedan",
  });
  assert.equal(h.run("vehicleVerificationToken"), "");
  assert.equal(h.nodes.get("submit-vehicle-register-button").disabled, true);
});

test("production overview failure after a save reports saved state and never permits token reuse", async () => {
  let requests = 0;
  let overviewRequests = 0;
  const h = harness(async (url) => {
    if (url.endsWith("/overview")) {
      overviewRequests++;
      throw new Error("overview offline");
    }
    requests++;
    return { vehicle_id: "A", license_plate: "12가9999" };
  }, undefined, true);
  openForm(h);
  await submit(h);
  await submit(h);
  assert.equal(requests, 1);
  assert.equal(overviewRequests, 1);
  assert.equal(h.run("vehicleVerificationToken"), "");
  assert.equal(h.nodes.get("submit-vehicle-register-button").disabled, true);
  assert.match(h.nodes.get("portal-message").textContent, /저장됐지만.*overview offline/);
  h.timers[0]();
  assert.equal(h.nodes.get("vehicle-register-modal").hidden, true);
});

test("production overview failure after deletion reports completion instead of retrying deletion", async () => {
  let deletions = 0;
  const h = harness(async (url, options) => {
    if (url.endsWith("/verification")) return { verification_token: "grant" };
    if (url.endsWith("/overview")) throw new Error("overview offline");
    assert.equal(options.method, "DELETE");
    deletions++;
    return {};
  }, undefined, true);
  begin(h, "delete", "A");
  await h.run("finishVehicleVerification()");
  await h.run("finishVehicleVerification()");
  assert.equal(deletions, 1);
  assert.equal(h.nodes.get("vehicle-verification-modal").hidden, true);
  assert.match(h.nodes.get("portal-message").textContent, /등록 해제는 완료됐지만.*overview offline/);
});

test("production overview loader returns success after rendering the response", async () => {
  const h = harness(async () => ({ full_name: "Driver", vehicles: [], events: [] }), undefined, true);
  assert.equal(await h.run("loadOverview()"), true);
  assert.equal(h.nodes.get("user-name").textContent, "Driver");
  assert.equal(h.nodes.get("vehicle-count").textContent, 0);
});

test("failed atomic save keeps the verification token and permits a retry", async () => {
  let requests = 0;
  const h = harness(async () => {
    if (++requests === 1) throw new Error("save unavailable");
    return { vehicle_id: "A", license_plate: "12가9999" };
  });
  openForm(h);
  await submit(h);
  assert.equal(h.run("vehicleVerificationToken"), "grant-A");
  assert.equal(h.nodes.get("submit-vehicle-register-button").disabled, false);
  assert.match(h.nodes.get("vehicle-register-message").textContent, /save unavailable/);
  await submit(h);
  assert.equal(requests, 2);
  assert.equal(h.run("vehicleVerificationToken"), "");
});

test("A's delayed close timer cannot close a newer B edit form", async () => {
  const h = harness(async () => ({ vehicle_id: "A", license_plate: "12가9999" }));
  openForm(h);
  await submit(h);
  h.run("closeVehicleRegisterModal()");
  openForm(h, "B");
  h.timers[0]();
  assert.equal(h.nodes.get("vehicle-register-modal").hidden, false);
  assert.equal(h.run("editingVehicle.vehicle_id"), "B");
  assert.equal(h.run("vehicleVerificationToken"), "grant-B");
});

test("A's late save cannot clear B's token or enable B's pending submit", async () => {
  const oldSave = deferred();
  const newSave = deferred();
  let requests = 0;
  const h = harness(() => ++requests === 1 ? oldSave.promise : newSave.promise);
  openForm(h);
  const oldOperation = submit(h);
  h.run("closeVehicleRegisterModal()");
  openForm(h, "B");
  const newOperation = submit(h);
  oldSave.resolve({ vehicle_id: "A", license_plate: "12가9999" });
  await oldOperation;
  assert.equal(h.run("vehicleVerificationToken"), "grant-B");
  assert.equal(h.run("editingVehicle.vehicle_id"), "B");
  assert.equal(h.nodes.get("submit-vehicle-register-button").disabled, true);
  assert.equal(h.nodes.get("vehicle-register-message").textContent, "");
  assert.equal(h.timers.length, 0);
  newSave.resolve({ vehicle_id: "B", license_plate: "12가9999" });
  await newOperation;
});

test("duplicate submit and verification clicks issue only one request each", async () => {
  const pending = deferred();
  let requests = 0;
  const h = harness(() => { requests++; return pending.promise; });
  begin(h, "update", "A");
  const verification = h.run("finishVehicleVerification()");
  await h.run("finishVehicleVerification()");
  assert.equal(requests, 1);
  pending.resolve({ verification_token: "grant" });
  await verification;

  const save = deferred();
  let saves = 0;
  const edit = harness(() => { saves++; return save.promise; });
  openForm(edit);
  const operation = submit(edit);
  await submit(edit);
  assert.equal(saves, 1);
  save.resolve({ vehicle_id: "A", license_plate: "12가9999" });
  await operation;
});

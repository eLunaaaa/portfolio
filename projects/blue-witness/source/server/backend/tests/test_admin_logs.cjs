const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const source = fs.readFileSync(
  path.join(__dirname, "../frontend/static/admin-logs.js"), "utf8",
);

function harness() {
  const nodes = new Map();
  const requests = [];
  const redirects = [];
  let poll;
  function element(selector) {
    if (!nodes.has(selector)) {
      let html = "";
      const listeners = new Map();
      nodes.set(selector, {
        value: "", checked: false, textContent: "",
        get innerHTML() { return html; },
        set innerHTML(value) {
          html = value;
          // Replacing a select's options resets its selection in the browser.
          if (selector === "#log-vehicle") this.value = "";
        },
        addEventListener: (name, callback) => listeners.set(name, callback),
        dispatch: (name, event = {}) => listeners.get(name)?.(event),
      });
    }
    return nodes.get(selector);
  }
  const context = vm.createContext({
    document: { querySelector: element, hidden: false },
    window: { location: { assign: (url) => redirects.push(url) } },
    URLSearchParams,
    fetch(url) {
      let resolve, reject;
      const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
      requests.push({ url, resolve, reject });
      return promise;
    },
    setInterval(callback) { poll = callback; },
    escapeHtml: (value) => String(value ?? ""),
    setMessage(node, message) { node.textContent = message; },
    logoutAndRedirect() {},
  });
  vm.runInContext(source, context);
  return {
    element, requests, redirects,
    poll: () => poll(),
    async respond(index, data, status = 200) {
      requests[index].resolve({ ok: status < 400, status, json: async () => data });
      await new Promise(setImmediate);
    },
  };
}

function logData(vehicleIds = [], marker = "current response") {
  const timestamp = "2026-10-07T14:00:00+00:00";
  return {
    server_time: timestamp, online_window_seconds: 30,
    retained_count: 1, matching_count: 1,
    peers: vehicleIds.map((vehicleId) => ({
      vehicle_id: vehicleId, node_id: `NODE_${vehicleId}`, client_ip: "192.0.2.1",
      last_seen_at: timestamp, last_health_at: timestamp, recent_requests: [],
    })),
    entries: [{
      received_at: timestamp, kind: "health", status_code: 200, completed: true,
      authenticated: true, vehicle_id: vehicleIds[0], node_id: "NODE_TEST",
      client_ip: "192.0.2.1", duration_ms: 1, result: marker,
    }],
  };
}

function change(h, selector, value) {
  const node = h.element(selector);
  if (typeof value === "boolean") node.checked = value;
  else node.value = value;
  return node.dispatch("change");
}

function query(h, index) {
  return new URL(h.requests[index].url, "https://testserver").searchParams;
}

test("changes during consecutive pending requests keep only the latest filters", async () => {
  const h = harness();
  await change(h, "#log-vehicle", "CAR_01");
  await change(h, "#log-vehicle", "CAR_02");
  assert.equal(h.requests.length, 1);

  await h.respond(0, logData(["CAR_01"], "stale all vehicles"));
  assert.equal(h.element("#log-vehicle").value, "CAR_02");
  assert.equal(h.requests.length, 2);
  assert.equal(query(h, 1).get("vehicle_id"), "CAR_02");
  assert.doesNotMatch(h.element("#communication-log-body").innerHTML, /stale all vehicles/);

  await change(h, "#log-vehicle", "CAR_03");
  await change(h, "#log-kind", "heartbeat");
  await change(h, "#failures-only", true);
  await h.respond(1, logData(["CAR_02"], "stale CAR_02"));
  assert.equal(h.requests.length, 3);
  assert.equal(query(h, 2).get("vehicle_id"), "CAR_03");
  assert.equal(query(h, 2).get("kind"), "heartbeat");
  assert.equal(query(h, 2).get("failures_only"), "true");
  assert.equal(h.element("#log-vehicle").value, "CAR_03");

  await h.respond(2, logData(["CAR_03"], "latest filters"));
  assert.match(h.element("#communication-log-body").innerHTML, /latest filters/);
  assert.doesNotMatch(h.element("#communication-log-body").innerHTML, /stale/);
  assert.equal(h.requests.length, 3);
});

test("polling retains the selected vehicle after it drops out of retained peers", async () => {
  const h = harness();
  await h.respond(0, logData(["CAR_01"]));
  change(h, "#log-vehicle", "CAR_01");
  await h.respond(1, logData([]));
  assert.equal(h.element("#log-vehicle").value, "CAR_01");
  assert.match(h.element("#log-vehicle").innerHTML, /value="CAR_01"/);

  h.poll();
  assert.equal(query(h, 2).get("vehicle_id"), "CAR_01");
  await h.respond(2, logData([]));
  assert.equal(h.element("#log-vehicle").value, "CAR_01");
});

test("all-logs reset during a request clears every filter and re-queries", async () => {
  const h = harness();
  await h.respond(0, logData(["CAR_01"]));
  h.element("#log-kind").value = "heartbeat";
  h.element("#failures-only").checked = true;
  change(h, "#log-vehicle", "CAR_01");
  h.element("#all-logs-button").dispatch("click");
  await h.respond(1, logData(["CAR_01"], "stale filtered response"));

  assert.equal(h.requests.length, 3);
  assert.equal(query(h, 2).has("vehicle_id"), false);
  assert.equal(query(h, 2).has("kind"), false);
  assert.equal(query(h, 2).get("failures_only"), "false");
  assert.equal(h.element("#log-vehicle").value, "");
  assert.doesNotMatch(h.element("#communication-log-body").innerHTML, /stale filtered response/);
  await h.respond(2, logData(["CAR_01"], "all vehicles"));
  assert.match(h.element("#communication-log-body").innerHTML, /all vehicles/);
});

test("a failed pending request still refreshes the latest filter", async () => {
  const h = harness();
  await change(h, "#log-vehicle", "CAR_02");
  h.requests[0].reject(new Error("network failed"));
  await new Promise(setImmediate);
  assert.equal(h.requests.length, 2);
  assert.equal(query(h, 1).get("vehicle_id"), "CAR_02");
  await h.respond(1, logData(["CAR_02"]));
  assert.equal(h.element("#log-vehicle").value, "CAR_02");
});

test("an authentication failure stops queued and periodic refreshes", async () => {
  const h = harness();
  await change(h, "#log-vehicle", "CAR_02");
  await h.respond(0, null, 401);
  h.poll();
  assert.deepEqual(h.redirects, ["/login"]);
  assert.equal(h.requests.length, 1);
});

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const project = path.resolve(__dirname, "..");

function pageHarness({ failRequests = false } = {}) {
  const listeners = new Map();
  const captured = [];
  class MockFormData {
    constructor(form) { this.values = form ? form.fields : []; }
    *entries() { yield* this.values; }
  }
  class MockForm {
    constructor(action, fields = []) {
      this.action = action;
      this.method = "POST";
      this.fields = fields;
    }
    submit() { this.nativeSubmitted = true; }
  }
  class MockXHR {
    constructor() { this.listeners = new Map(); }
    addEventListener(type, callback) {
      const rows = this.listeners.get(type) || [];
      rows.push(callback);
      this.listeners.set(type, rows);
    }
    dispatchEvent(event) {
      for (const callback of this.listeners.get(event.type) || []) callback(event);
      const handler = this[`on${event.type}`];
      if (typeof handler === "function") handler(event);
      return true;
    }
    open() { this.nativeOpened = true; }
    send() { this.nativeSent = true; }
    setRequestHeader() {}
    get readyState() { return 0; }
    get status() { return 0; }
    get responseText() { return ""; }
    get response() { return ""; }
  }
  class MockEvent { constructor(type) { this.type = type; } }
  class MockProgressEvent extends MockEvent {}
  const window = {
    addEventListener(type, callback) {
      const rows = listeners.get(type) || [];
      rows.push(callback);
      listeners.set(type, rows);
    },
    postMessage(data) {
      if (data.marker !== "omajdownload-cnl-request-v1") return;
      captured.push(data);
      const body = data.url.endsWith("jdcheck.js") ? "jdownloader=true;" : "success\r\n";
      queueMicrotask(() => {
        for (const callback of listeners.get("message") || []) {
          callback({
            source: window,
            data: failRequests
              ? { marker: "omajdownload-cnl-response-v1", id: data.id, ok: false, status: 0, message: "listener stopped" }
              : { marker: "omajdownload-cnl-response-v1", id: data.id, ok: true, status: 200, body }
          });
        }
      });
    },
    fetch: (...args) => Promise.resolve({ native: true, args }),
    XMLHttpRequest: MockXHR
  };
  const documentListeners = new Map();
  const document = {
    baseURI: "https://downloads.example/page",
    addEventListener(type, callback) { documentListeners.set(type, callback); }
  };
  const context = vm.createContext({
    window, document, URL, URLSearchParams, Request, Response,
    FormData: MockFormData, HTMLFormElement: MockForm,
    XMLHttpRequest: MockXHR, Event: MockEvent, ProgressEvent: MockProgressEvent,
    Promise, Map, String, Date, TypeError, setTimeout, clearTimeout, queueMicrotask
  });
  vm.runInContext(fs.readFileSync(path.join(project, "browser-extension/page-bridge.js"), "utf8"), context);
  return { window, captured, documentListeners, MockForm };
}

test("page bridge preserves fetch(Request) bodies and original usability", async () => {
  const harness = pageHarness();
  const request = new Request("http://127.0.0.1:9666/flash/add", {
    method: "POST",
    body: new URLSearchParams({ urls: "https://files.example/file" })
  });
  const response = await harness.window.fetch(request);
  assert.equal(response.status, 200);
  const submission = harness.captured.find(row => row.method === "POST");
  assert.equal(submission.body, "urls=https%3A%2F%2Ffiles.example%2Ffile");
  assert.equal(await request.text(), "urls=https%3A%2F%2Ffiles.example%2Ffile");

  await harness.window.fetch("http://127.0.0.1:9666/flash/add", {
    method: "POST", body: new URLSearchParams({ urls: "https://string.example/file" })
  });
  assert.equal(harness.captured.filter(row => row.body.includes("string.example")).length, 1);

  const overridden = new Request("http://127.0.0.1:9666/flash/add", {
    method: "POST", body: new URLSearchParams({ urls: "https://original.example/file" })
  });
  await harness.window.fetch(overridden, {
    method: "POST", body: new URLSearchParams({ urls: "https://override.example/file" })
  });
  assert.equal(harness.captured.filter(row => row.body.includes("override.example")).length, 1);
  assert.equal(harness.captured.filter(row => row.body.includes("original.example")).length, 0);

  const consumed = new Request("http://127.0.0.1:9666/flash/add", {
    method: "POST", body: new URLSearchParams({ urls: "https://consumed.example/file" })
  });
  await consumed.text();
  await assert.rejects(harness.window.fetch(consumed), TypeError);
  assert.equal(harness.captured.filter(row => row.body.includes("consumed.example")).length, 0);
});

test("page bridge executes probe, form, and XHR transports", async () => {
  const harness = pageHarness();
  await new Promise(resolve => queueMicrotask(resolve));
  assert.ok(harness.captured.some(row => row.method === "GET" && row.url.includes("/flash/")));

  const form = new harness.MockForm("http://localhost:9666/flash/add", [["urls", "https://form.example/file"]]);
  form.submit();
  const formPost = harness.captured.find(row => row.body.includes("form.example"));
  assert.equal(formPost.method, "POST");

  const submittedForm = new harness.MockForm("http://127.0.0.1:9666/flash/add", [["urls", "https://submit-event.example/file"]]);
  let prevented = false;
  harness.documentListeners.get("submit")({
    target: submittedForm,
    preventDefault() { prevented = true; }
  });
  assert.equal(prevented, true);
  assert.ok(harness.captured.some(row => row.body.includes("submit-event.example")));

  const xhr = new harness.window.XMLHttpRequest();
  const events = [];
  for (const name of ["readystatechange", "loadstart", "load", "loadend"]) xhr.addEventListener(name, () => events.push(name));
  const finished = new Promise(resolve => xhr.addEventListener("loadend", resolve));
  xhr.open("POST", "http://127.0.0.1:9666/flash/add");
  xhr.send(new URLSearchParams({ urls: "https://xhr.example/file" }));
  await finished;
  assert.equal(xhr.status, 200);
  assert.deepEqual(events, ["readystatechange", "loadstart", "readystatechange", "readystatechange", "readystatechange", "load", "loadend"]);
});

test("page bridge reports the listener unavailable when its probe fails", async () => {
  const harness = pageHarness({ failRequests: true });
  await new Promise(resolve => setTimeout(resolve, 0));
  assert.equal(harness.window.jdownloader, false);
});

test("service worker authenticates provenance and supports GET probes", async () => {
  let listener;
  const calls = [];
  let targetStatus = 200;
  let targetFailure = false;
  const chrome = { runtime: { onMessage: { addListener(callback) { listener = callback; } } } };
  const fetch = async (url, options = {}) => {
    calls.push({ url: String(url), options });
    if (String(url).endsWith("/omajdownload/extension-token")) return new Response("token-123", { status: 200 });
    if (String(url).endsWith("/flash/")) return new Response("JDownloader\r\n", { status: 200 });
    if (targetFailure) throw new Error("listener stopped");
    return new Response(targetStatus === 200 ? "success\r\n" : "inbox full", { status: targetStatus });
  };
  const context = vm.createContext({ chrome, fetch, URL, TextEncoder, String, Error, Response });
  vm.runInContext(fs.readFileSync(path.join(project, "browser-extension/service-worker.js"), "utf8"), context);

  const invoke = (message) => new Promise(resolve => {
    assert.equal(listener(message, { url: "https://source.example/page" }, resolve), true);
  });
  const post = await invoke({
    type: "omajdownload-cnl", method: "POST",
    url: "http://127.0.0.1:9666/flash/add", body: "urls=https%3A%2F%2Ffiles.example"
  });
  assert.equal(post.ok, true);
  const forwarded = calls.find(call => call.options.method === "POST");
  assert.equal(forwarded.options.headers["X-OmaJDownLoad-Token"], "token-123");
  assert.equal(forwarded.options.headers["X-OmaJDownLoad-Origin"], "https://source.example/page");
  assert.equal(calls.filter(call => call.url.endsWith("/flash/add")).length, 1);

  const encrypted = await invoke({
    type: "omajdownload-cnl", method: "POST",
    url: "http://127.0.0.1:9666/flash/addcrypted2", body: "jk=literal&crypted=data"
  });
  assert.equal(encrypted.ok, true);
  assert.equal(calls.filter(call => call.url.endsWith("/flash/addcrypted2")).length, 1);

  const probe = await invoke({ type: "omajdownload-cnl", method: "GET", url: "http://127.0.0.1:9666/flash/", body: "" });
  assert.equal(probe.ok, true);
  assert.ok(calls.some(call => call.options.method === "GET"));

  let oversize;
  assert.equal(listener({
    type: "omajdownload-cnl", method: "POST",
    url: "http://127.0.0.1:9666/flash/add", body: "x".repeat(1024 * 1024 + 1)
  }, { url: "https://source.example/page" }, value => { oversize = value; }), false);
  assert.equal(oversize.status, 413);

  targetStatus = 507;
  const full = await invoke({
    type: "omajdownload-cnl", method: "POST",
    url: "http://127.0.0.1:9666/flash/add", body: "urls=full"
  });
  assert.equal(full.ok, false);
  assert.equal(full.status, 507);

  targetStatus = 200;
  targetFailure = true;
  const stopped = await invoke({
    type: "omajdownload-cnl", method: "POST",
    url: "http://127.0.0.1:9666/flash/add", body: "urls=stopped"
  });
  assert.equal(stopped.ok, false);
  assert.equal(stopped.status, 0);
  assert.match(stopped.message, /listener stopped/);
});

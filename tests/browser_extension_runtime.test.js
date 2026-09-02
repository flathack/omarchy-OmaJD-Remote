const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const project = path.resolve(__dirname, "..");

function pageHarness({ failRequests = false } = {}) {
  const listeners = new Map();
  const captured = [];
  const timerDelays = [];
  class MockFormData {
    constructor(form, submitter) {
      this.values = form ? [...form.fields] : [];
      if (submitter && submitter.name) this.values.push([submitter.name, submitter.value]);
    }
    *entries() { yield* this.values; }
  }
  class MockForm {
    constructor(action, fields = []) {
      this.action = action;
      this.method = "POST";
      this.fields = fields;
      this.target = "";
      this.listeners = new Map();
    }
    submit() { this.nativeSubmitted = true; }
    addEventListener(type, callback) { this.listeners.set(type, callback); }
    dispatchEvent(event) {
      const callback = this.listeners.get(event.type);
      if (callback) callback(event);
      return true;
    }
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
  class MockScript {
    constructor() { this.attributes = new Map(); this.listeners = new Map(); }
    setAttribute(name, value) { this.attributes.set(name, String(value)); }
    getAttribute(name) { return this.attributes.get(name) || ""; }
    removeAttribute(name) { this.attributes.delete(name); }
    addEventListener(type, callback) { this.listeners.set(type, callback); }
    dispatchEvent(event) {
      const callback = this.listeners.get(event.type);
      if (callback) callback(event);
      return true;
    }
    set src(value) { this.setAttribute("src", value); }
    get src() { return this.getAttribute("src"); }
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
      if (data.type === "cancel") return;
      const body = new URL(data.url).pathname === "/jdcheck.js" ? "jdownloader=true;" : "success\r\n";
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
    addEventListener(type, callback) { documentListeners.set(type, callback); },
    querySelector() { return null; }
  };
  class MockCustomEvent extends MockEvent { constructor(type, options = {}) { super(type); this.detail = options.detail; } }
  const context = vm.createContext({
    window, document, URL, URLSearchParams, Request, Response,
    FormData: MockFormData, HTMLFormElement: MockForm, HTMLScriptElement: MockScript,
    XMLHttpRequest: MockXHR, Event: MockEvent, ProgressEvent: MockProgressEvent, CustomEvent: MockCustomEvent,
    AbortController, DOMException, Error, TextEncoder,
    Promise, Map, WeakSet, String, Date, Math, TypeError,
    setTimeout(callback, delay) { timerDelays.push(delay); return setTimeout(callback, delay); },
    clearTimeout, queueMicrotask
  });
  vm.runInContext(fs.readFileSync(path.join(project, "browser-extension/page-bridge.js"), "utf8"), context);
  return { window, captured, timerDelays, documentListeners, MockForm, MockScript };
}

function contentScriptHarness() {
  const listeners = new Map();
  const sent = [];
  const responses = [];
  const window = {
    addEventListener(type, callback) {
      const rows = listeners.get(type) || [];
      rows.push(callback);
      listeners.set(type, rows);
    },
    postMessage(data) { responses.push(data); }
  };
  const document = {
    getElementById() { return null; },
    createElement() { return { setAttribute() {}, style: {}, remove() {}, textContent: "" }; },
    body: { appendChild() {} },
    documentElement: { appendChild() {} }
  };
  let lastError = null;
  const chrome = {
    runtime: {
      get lastError() { return lastError; },
      sendMessage(message, callback) { sent.push({ message, callback }); }
    }
  };
  const context = vm.createContext({
    window, document, chrome, URL, TextEncoder, String, Error,
    setTimeout, clearTimeout, Map, Set, Array, Object, TypeError
  });
  vm.runInContext(fs.readFileSync(path.join(project, "browser-extension/content-script.js"), "utf8"), context);
  return {
    sent,
    responses,
    dispatch(data) {
      for (const callback of listeners.get("message") || []) callback({ source: window, data });
    },
    complete(index, result = { ok: true, status: 200, body: "success\r\n" }) {
      sent[index].callback(result);
    }
  };
}

test("content script validates direct page messages and bounds pending forwards", () => {
  const harness = contentScriptHarness();
  harness.dispatch({
    marker: "omajdownload-cnl-request-v1", type: "request", id: "oversize",
    url: "http://127.0.0.1:9666/flash/add", method: "POST", probe: false,
    body: "x".repeat(1024 * 1024 + 1)
  });
  assert.equal(harness.sent.length, 0);
  assert.equal(harness.responses.at(-1).status, 413);

  for (let index = 0; index < 32; index++) {
    harness.dispatch({
      marker: "omajdownload-cnl-request-v1", type: "request", id: `request-${index}`,
      url: "http://127.0.0.1:9666/flash/add", method: "POST", probe: false, body: "urls=ok"
    });
  }
  assert.equal(harness.sent.length, 32);
  harness.dispatch({
    marker: "omajdownload-cnl-request-v1", type: "request", id: "overflow",
    url: "http://127.0.0.1:9666/flash/add", method: "POST", probe: false, body: "urls=overflow"
  });
  assert.equal(harness.sent.length, 32);
  assert.equal(harness.responses.at(-1).status, 429);
  harness.dispatch({ marker: "omajdownload-cnl-request-v1", type: "cancel", id: "request-0" });
  harness.dispatch({
    marker: "omajdownload-cnl-request-v1", type: "request", id: "replacement",
    url: "http://127.0.0.1:9666/flash/add", method: "POST", probe: false, body: "urls=replacement"
  });
  assert.equal(harness.sent.length, 34);
});

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
  const formCompleted = new Promise(resolve => submittedForm.addEventListener("omajdownload:success", resolve));
  let prevented = false;
  harness.documentListeners.get("submit")({
    target: submittedForm,
    submitter: { name: "action", value: "clicknload" },
    preventDefault() { prevented = true; }
  });
  assert.equal(prevented, true);
  await formCompleted;
  assert.ok(harness.captured.some(row => row.body.includes("submit-event.example")));
  assert.ok(harness.captured.some(row => row.body.includes("action=clicknload")));

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

test("page bridge cancels Fetch and XHR without late load events", async () => {
  const harness = pageHarness();
  const controller = new AbortController();
  const pendingFetch = harness.window.fetch("http://127.0.0.1:9666/flash/add", {
    method: "POST", body: "urls=cancelled", signal: controller.signal
  });
  controller.abort();
  await assert.rejects(pendingFetch, error => error.name === "AbortError");

  const xhr = new harness.window.XMLHttpRequest();
  const events = [];
  for (const name of ["load", "abort", "loadend"]) xhr.addEventListener(name, () => events.push(name));
  const finished = new Promise(resolve => xhr.addEventListener("loadend", resolve));
  xhr.open("POST", "http://127.0.0.1:9666/flash/add");
  xhr.send("urls=cancelled-xhr");
  xhr.abort();
  await finished;
  assert.deepEqual(events, ["abort", "loadend"]);
  assert.equal(xhr.status, 0);
  assert.equal(xhr.readyState, 0);
  assert.ok(harness.captured.some(row => row.type === "cancel"));
});

test("page bridge reports the listener unavailable when its probe fails", async () => {
  const harness = pageHarness({ failRequests: true });
  await new Promise(resolve => setTimeout(resolve, 0));
  assert.equal(harness.window.jdownloader, false);
});

test("page bridge handles programmatic jdcheck.js script probes", async () => {
  const harness = pageHarness();
  const script = new harness.MockScript();
  const loaded = new Promise(resolve => script.addEventListener("load", resolve));
  script.src = "http://127.0.0.1:9666/jdcheck.js";
  await loaded;
  assert.equal(harness.window.jdownloader, true);
  assert.ok(harness.captured.some(row => row.url && row.url.endsWith("/jdcheck.js")));
  assert.equal(script.getAttribute("src"), "");
});

test("page bridge handles cache-busted script probes", async () => {
  const harness = pageHarness();
  const script = new harness.MockScript();
  const loaded = new Promise(resolve => script.addEventListener("load", resolve));
  script.src = "http://127.0.0.1:9666/jdcheck.js?cache=123#probe";
  await loaded;
  assert.equal(harness.window.jdownloader, true);
  assert.ok(harness.captured.some(row => new URL(row.url).pathname === "/jdcheck.js" && row.url.includes("cache=123")));
  assert.equal(script.getAttribute("src"), "");
});

test("XHR uses its own timeout without the bridge watchdog", async () => {
  const harness = pageHarness();
  await new Promise(resolve => queueMicrotask(resolve));
  harness.timerDelays.length = 0;
  const xhr = new harness.window.XMLHttpRequest();
  xhr.timeout = 20000;
  const finished = new Promise(resolve => xhr.addEventListener("loadend", resolve));
  xhr.open("POST", "http://127.0.0.1:9666/flash/add");
  xhr.send("urls=slow");
  await finished;
  assert.deepEqual(harness.timerDelays, [20000]);

  harness.timerDelays.length = 0;
  const unlimited = new harness.window.XMLHttpRequest();
  const unlimitedFinished = new Promise(resolve => unlimited.addEventListener("loadend", resolve));
  unlimited.open("POST", "http://127.0.0.1:9666/flash/add");
  unlimited.send("urls=unlimited");
  await unlimitedFinished;
  assert.deepEqual(harness.timerDelays, []);
});

test("XHR rejects send() while a request is still in flight", async () => {
  const harness = pageHarness();
  await new Promise(resolve => queueMicrotask(resolve));
  const xhr = new harness.window.XMLHttpRequest();
  xhr.open("POST", "http://127.0.0.1:9666/flash/add");
  xhr.send("urls=first");
  let threw = null;
  try { xhr.send("urls=second"); } catch (error) { threw = error; }
  assert.ok(threw, "second send() must throw while the previous request is still active");
  assert.match(String(threw && threw.message || ""), /invalid state/i);
});

test("XHR second open() rejects a stale in-flight callback", async () => {
  const harness = pageHarness();
  // The page bridge probes the listener availability on load; wait for
  // that single captured row so this test only inspects the forwards
  // triggered by the XHR it constructs.
  await new Promise(resolve => queueMicrotask(resolve));
  await new Promise(resolve => setTimeout(resolve, 0));
  const xhr = new harness.window.XMLHttpRequest();
  xhr.open("POST", "http://127.0.0.1:9666/flash/add");
  xhr.send("urls=first");
  // Exactly one additional captured row must be the XHR's own forward.
  const baseline = harness.captured.length;
  // Replace the request before the first response resolves. The second
  // open() must ask the bridge to cancel the in-flight forward.
  xhr.open("POST", "http://127.0.0.1:9666/flash/add");
  xhr.send("urls=second");
  await new Promise(resolve => queueMicrotask(resolve));
  await new Promise(resolve => setTimeout(resolve, 0));
  await new Promise(resolve => setTimeout(resolve, 0));
  const tail = harness.captured.slice(baseline - 1);
  const types = tail.map(row => row.type);
  assert.deepEqual(
    types,
    ["request", "cancel", "request"],
    "stale first forward must be cancelled, replacement must be forwarded once",
  );
  assert.equal(xhr.readyState, 4);
  assert.equal(xhr.status, 200);
});

test("service worker authenticates provenance and supports GET probes", async () => {
  let listener;
  const calls = [];
  let targetStatus = 200;
  let targetFailure = false;
  let targetBody = "";
  const chrome = { runtime: { onMessage: { addListener(callback) { listener = callback; } } } };
  const fetch = async (url, options = {}) => {
    calls.push({ url: String(url), options });
    if (String(url).endsWith("/omajdownload/extension-token")) return new Response("0123456789abcdef0123456789abcdef", { status: 200 });
    if (String(url).endsWith("/flash/")) return new Response("JDownloader\r\n", { status: 200 });
    if (targetFailure) throw new Error("listener stopped");
    return new Response(targetBody || (targetStatus === 200 ? "success\r\n" : "inbox full"), { status: targetStatus });
  };
  const context = vm.createContext({
    chrome, fetch, URL, TextEncoder, TextDecoder, String, Error, Response, AbortController, Map,
    setTimeout, clearTimeout
  });
  vm.runInContext(fs.readFileSync(path.join(project, "browser-extension/service-worker.js"), "utf8"), context);

  let requestSequence = 0;
  const invoke = (message) => new Promise(resolve => {
    assert.equal(listener({ requestId: `request-${++requestSequence}`, ...message }, { url: "https://source.example/page" }, resolve), true);
  });
  const post = await invoke({
    type: "omajdownload-cnl", method: "POST",
    url: "http://127.0.0.1:9666/flash/add", body: "urls=https%3A%2F%2Ffiles.example"
  });
  assert.equal(post.ok, true);
  const forwarded = calls.find(call => call.options.method === "POST");
  assert.equal(forwarded.options.headers["X-OmaJDownLoad-Token"], "0123456789abcdef0123456789abcdef");
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
    type: "omajdownload-cnl", requestId: "oversize", method: "POST",
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
  targetBody = "x".repeat(64 * 1024 + 1);
  const oversizedResponse = await invoke({
    type: "omajdownload-cnl", method: "POST", url: "http://127.0.0.1:9666/flash/add", body: "urls=large"
  });
  assert.equal(oversizedResponse.ok, false);
  assert.match(oversizedResponse.message, /too large/);

  targetBody = "";
  targetFailure = true;
  const stopped = await invoke({
    type: "omajdownload-cnl", method: "POST",
    url: "http://127.0.0.1:9666/flash/add", body: "urls=stopped"
  });
  assert.equal(stopped.ok, false);
  assert.equal(stopped.status, 0);
  assert.match(stopped.message, /listener stopped/);
});

test("service worker caps concurrent request controllers", async () => {
  let listener;
  const chrome = { runtime: { onMessage: { addListener(callback) { listener = callback; } } } };
  const fetch = (url, options = {}) => {
    if (String(url).endsWith("/omajdownload/extension-token"))
      return Promise.resolve(new Response("0123456789abcdef0123456789abcdef", { status: 200 }));
    return new Promise((_resolve, reject) => {
      options.signal.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")), { once: true });
    });
  };
  const context = vm.createContext({
    chrome, fetch, URL, TextEncoder, TextDecoder, String, Error, Response, AbortController, Map,
    DOMException, setTimeout, clearTimeout
  });
  vm.runInContext(fs.readFileSync(path.join(project, "browser-extension/service-worker.js"), "utf8"), context);
  const sender = { tab: { id: 1 }, frameId: 0, documentId: "capacity", url: "https://source.example" };
  const pending = [];
  for (let index = 0; index < 32; index++) {
    pending.push(new Promise(resolve => {
      assert.equal(listener({
        type: "omajdownload-cnl", requestId: `request-${index}`, method: "POST",
        url: "http://127.0.0.1:9666/flash/add", body: "urls=one"
      }, sender, resolve), true);
    }));
  }
  let rejected;
  assert.equal(listener({
    type: "omajdownload-cnl", requestId: "request-overflow", method: "POST",
    url: "http://127.0.0.1:9666/flash/add", body: "urls=overflow"
  }, sender, value => { rejected = value; }), false);
  assert.equal(rejected.status, 429);
  await new Promise(resolve => setTimeout(resolve, 0));
  for (let index = 0; index < 32; index++)
    listener({ type: "omajdownload-cnl-cancel", requestId: `request-${index}` }, sender, () => {});
  await Promise.all(pending);
});

test("service worker isolates equal request IDs across senders", async () => {
  let listener;
  const targetRequests = [];
  const chrome = { runtime: { onMessage: { addListener(callback) { listener = callback; } } } };
  const fetch = async (url, options = {}) => {
    if (String(url).endsWith("/omajdownload/extension-token")) return new Response("0123456789abcdef0123456789abcdef", { status: 200 });
    return new Promise((resolve, reject) => {
      const pending = { options, resolve };
      targetRequests.push(pending);
      options.signal.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")), { once: true });
    });
  };
  const context = vm.createContext({
    chrome, fetch, URL, TextEncoder, TextDecoder, String, Error, Response, AbortController, Map,
    setTimeout, clearTimeout
  });
  vm.runInContext(fs.readFileSync(path.join(project, "browser-extension/service-worker.js"), "utf8"), context);
  const firstSender = { tab: { id: 1 }, frameId: 0, documentId: "doc-a", url: "https://a.example" };
  const secondSender = { tab: { id: 2 }, frameId: 0, documentId: "doc-b", url: "https://b.example" };
  const invoke = (sender) => new Promise(resolve => {
    listener({
      type: "omajdownload-cnl", requestId: "same-id", method: "POST",
      url: "http://127.0.0.1:9666/flash/add", body: "urls=https%3A%2F%2Ffiles.example"
    }, sender, resolve);
  });
  const first = invoke(firstSender);
  const second = invoke(secondSender);
  await new Promise(resolve => setTimeout(resolve, 0));
  assert.equal(targetRequests.length, 2);
  listener({ type: "omajdownload-cnl-cancel", requestId: "same-id" }, firstSender, () => {});
  assert.equal(targetRequests[0].options.signal.aborted, true);
  assert.equal(targetRequests[1].options.signal.aborted, false);
  targetRequests[1].resolve(new Response("success\r\n", { status: 200 }));
  const [firstResult, secondResult] = await Promise.all([first, second]);
  assert.equal(firstResult.ok, false);
  assert.equal(secondResult.ok, true);
});

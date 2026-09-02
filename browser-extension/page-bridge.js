(() => {
  const REQUEST_MARKER = "omajdownload-cnl-request-v1";
  const RESPONSE_MARKER = "omajdownload-cnl-response-v1";
  const MAX_PENDING_REQUESTS = 32;
  const REQUEST_BODY_BYTE_LIMIT = 1024 * 1024;
  const pending = new Map();
  let sequence = 0;

  function cnlRoute(value, method) {
    try {
      const url = new URL(String(value), document.baseURI);
      if (url.protocol !== "http:") return null;
      if (url.hostname !== "127.0.0.1" && url.hostname !== "localhost") return null;
      if (url.port !== "9666") return null;
      const normalizedMethod = String(method || "GET").toUpperCase();
      if (normalizedMethod === "POST" && (url.pathname === "/flash/add" || url.pathname === "/flash/addcrypted2"))
        return { url: url.href, path: url.pathname, method: "POST", probe: false };
      if (normalizedMethod === "GET" && (url.pathname.replace(/\/$/, "") === "/flash" || url.pathname === "/jdcheck.js"))
        return { url: url.href, path: url.pathname, method: "GET", probe: true };
      return null;
    } catch (_error) {
      return null;
    }
  }

  function encodeBody(body) {
    if (body instanceof URLSearchParams) return body.toString();
    if (body instanceof FormData) {
      const params = new URLSearchParams();
      for (const [key, value] of body.entries()) params.append(key, typeof value === "string" ? value : value.name);
      return params.toString();
    }
    return String(body || "");
  }

  function abortError(message = "The operation was aborted") {
    if (typeof DOMException === "function") return new DOMException(message, "AbortError");
    const error = new Error(message);
    error.name = "AbortError";
    return error;
  }

  function forward(route, body, { signal = null, timeoutMs = 10000 } = {}) {
    const id = typeof crypto !== "undefined" && typeof crypto.randomUUID === "function"
      ? crypto.randomUUID()
      : `${Date.now()}-${Math.random().toString(36).slice(2)}-${++sequence}`;
    if (signal && signal.aborted) return Promise.reject(abortError());
    if (pending.size >= MAX_PENDING_REQUESTS)
      return Promise.reject(new TypeError("Too many concurrent Click'n'Load requests"));
    const encodedBody = route.method === "POST" ? encodeBody(body) : "";
    if (new TextEncoder().encode(encodedBody).byteLength > REQUEST_BODY_BYTE_LIMIT)
      return Promise.reject(new TypeError("Click'n'Load request is too large"));
    return new Promise((resolve, reject) => {
      let timeout;
      const cleanup = () => {
        if (timeout) clearTimeout(timeout);
        if (signal) signal.removeEventListener("abort", onAbort);
        pending.delete(id);
      };
      const cancel = error => {
        if (!pending.has(id)) return;
        cleanup();
        window.postMessage({ marker: REQUEST_MARKER, type: "cancel", id }, "*");
        reject(error);
      };
      const onAbort = () => cancel(abortError());
      if (timeoutMs > 0)
        timeout = setTimeout(() => cancel(new TypeError("OmaJD-Remote did not answer")), timeoutMs);
      if (signal) signal.addEventListener("abort", onAbort, { once: true });
      pending.set(id, {
        resolve(value) { cleanup(); resolve(value); },
        reject(error) { cleanup(); reject(error); }
      });
      window.postMessage({
        marker: REQUEST_MARKER,
        type: "request",
        id,
        url: route.url,
        method: route.method,
        probe: route.probe,
        body: encodedBody
      }, "*");
    });
  }

  window.addEventListener("message", event => {
    if (event.source !== window || !event.data || event.data.marker !== RESPONSE_MARKER) return;
    const entry = pending.get(event.data.id);
    if (!entry) return;
    if (event.data.ok) entry.resolve(event.data);
    else entry.reject(new TypeError(event.data.message || `OmaJD-Remote returned ${event.data.status}`));
  });

  const nativeFetch = window.fetch.bind(window);
  window.fetch = function(input, init = {}) {
    const method = init.method || (input instanceof Request ? input.method : "GET");
    const route = cnlRoute(typeof input === "string" || input instanceof URL ? input : input.url, method);
    if (!route) return nativeFetch(input, init);
    const signal = init.signal || (input instanceof Request ? input.signal : null);
    let bodyPromise = Promise.resolve("");
    if (route.method === "POST") {
      if (init.body !== undefined) bodyPromise = Promise.resolve(init.body);
      else if (input instanceof Request) {
        try {
          bodyPromise = input.clone().text();
        } catch (error) {
          bodyPromise = Promise.reject(error);
        }
      }
    }
    return bodyPromise.then(body => forward(route, body, { signal })).then(result => new Response(result.body || "success\r\n", {
      status: result.status || 200,
      headers: { "Content-Type": route.path === "/jdcheck.js" ? "application/javascript" : "text/plain; charset=utf-8" }
    }));
  };

  function formData(form, submitter) {
    if (submitter) {
      try { return new FormData(form, submitter); } catch (_error) { /* older browser fallback */ }
    }
    return new FormData(form);
  }

  function formResult(form, ok, detail) {
    const type = ok ? "omajdownload:success" : "omajdownload:error";
    form.dispatchEvent(new CustomEvent(type, { detail }));
    const target = String(form.target || "");
    if (!target) return;
    const escaped = typeof CSS !== "undefined" && CSS.escape ? CSS.escape(target) : target.replace(/["\\]/g, "\\$&");
    const frame = document.querySelector(`iframe[name="${escaped}"]`);
    if (frame && ok) frame.srcdoc = detail.body || "success\r\n";
  }

  function submitForm(form, submitter = null) {
    const route = cnlRoute(form.action, form.method || "GET");
    if (!route || route.method !== "POST") return false;
    forward(route, formData(form, submitter)).then(
      result => formResult(form, true, result),
      error => formResult(form, false, { message: String(error && error.message || error) })
    );
    return true;
  }

  document.addEventListener("submit", event => {
    const form = event.target;
    if (!(form instanceof HTMLFormElement)) return;
    if (!submitForm(form, event.submitter || null)) return;
    event.preventDefault();
  }, true);

  const nativeSubmit = HTMLFormElement.prototype.submit;
  HTMLFormElement.prototype.submit = function() {
    if (!submitForm(this)) return nativeSubmit.call(this);
  };

  const NativeXHR = window.XMLHttpRequest;
  class OmaJDownLoadXHR extends NativeXHR {
    open(method, url, ...rest) {
      this.__omajRoute = cnlRoute(url, method);
      if (!this.__omajRoute) return super.open(method, url, ...rest);
      this.__omajReadyState = 1;
      this.__omajStatus = 0;
      this.__omajResponseText = "";
      this.__omajAborted = false;
      this.__omajFinished = false;
      this.dispatchEvent(new Event("readystatechange"));
    }
    setRequestHeader(name, value) {
      if (!this.__omajRoute) return super.setRequestHeader(name, value);
    }
    send(body) {
      if (!this.__omajRoute) return super.send(body);
      this.__omajController = new AbortController();
      this.__omajAborted = false;
      this.__omajFinished = false;
      let timedOut = false;
      let timeout;
      if (this.timeout > 0) {
        timeout = setTimeout(() => {
          timedOut = true;
          this.__omajController.abort();
        }, this.timeout);
      }
      this.dispatchEvent(new ProgressEvent("loadstart"));
      forward(this.__omajRoute, body, { signal: this.__omajController.signal, timeoutMs: 0 }).then(result => {
        if (timeout) clearTimeout(timeout);
        if (this.__omajFinished) return;
        this.__omajReadyState = 2;
        this.dispatchEvent(new Event("readystatechange"));
        this.__omajReadyState = 3;
        this.dispatchEvent(new Event("readystatechange"));
        this.__omajStatus = result.status || 200;
        this.__omajResponseText = result.body || "success\r\n";
        this.__omajReadyState = 4;
        this.__omajFinished = true;
        this.dispatchEvent(new Event("readystatechange"));
        this.dispatchEvent(new ProgressEvent("load"));
        this.dispatchEvent(new ProgressEvent("loadend"));
      }).catch(() => {
        if (timeout) clearTimeout(timeout);
        if (this.__omajFinished) return;
        this.__omajStatus = 0;
        this.__omajReadyState = this.__omajAborted ? 0 : 4;
        this.__omajFinished = true;
        this.dispatchEvent(new Event("readystatechange"));
        this.dispatchEvent(new ProgressEvent(timedOut ? "timeout" : (this.__omajAborted ? "abort" : "error")));
        this.dispatchEvent(new ProgressEvent("loadend"));
      });
    }
    abort() {
      if (!this.__omajRoute) return super.abort();
      if (!this.__omajController || this.__omajFinished) return;
      this.__omajAborted = true;
      this.__omajController.abort();
    }
    get readyState() { return this.__omajRoute ? (this.__omajReadyState || 0) : super.readyState; }
    get status() { return this.__omajRoute ? (this.__omajStatus || 0) : super.status; }
    get statusText() { return this.__omajRoute ? (this.status === 200 ? "OK" : "") : super.statusText; }
    get responseText() { return this.__omajRoute ? (this.__omajResponseText || "") : super.responseText; }
    get response() { return this.__omajRoute ? (this.__omajResponseText || "") : super.response; }
    get responseURL() { return this.__omajRoute ? this.__omajRoute.url : super.responseURL; }
    getAllResponseHeaders() { return this.__omajRoute && this.status === 200 ? "content-type: text/plain; charset=utf-8\r\n" : super.getAllResponseHeaders(); }
    getResponseHeader(name) {
      if (!this.__omajRoute) return super.getResponseHeader(name);
      return this.status === 200 && String(name).toLowerCase() === "content-type" ? "text/plain; charset=utf-8" : null;
    }
  }
  window.XMLHttpRequest = OmaJDownLoadXHR;

  const bridgedScripts = new WeakSet();
  function bridgeProbeScript(script, value) {
    const route = cnlRoute(value, "GET");
    if (!route || route.path !== "/jdcheck.js" || bridgedScripts.has(script)) return false;
    bridgedScripts.add(script);
    script.removeAttribute("src");
    forward(route, "").then(() => {
      window.jdownloader = true;
      script.dispatchEvent(new Event("load"));
    }).catch(() => {
      window.jdownloader = false;
      script.dispatchEvent(new Event("error"));
    }).finally(() => {
      bridgedScripts.delete(script);
    });
    return true;
  }

  if (typeof HTMLScriptElement !== "undefined") {
    const nativeSetAttribute = HTMLScriptElement.prototype.setAttribute;
    HTMLScriptElement.prototype.setAttribute = function(name, value) {
      if (String(name).toLowerCase() === "src" && bridgeProbeScript(this, value)) return;
      return nativeSetAttribute.call(this, name, value);
    };
    const srcDescriptor = Object.getOwnPropertyDescriptor(HTMLScriptElement.prototype, "src");
    if (srcDescriptor && srcDescriptor.set) {
      Object.defineProperty(HTMLScriptElement.prototype, "src", {
        configurable: srcDescriptor.configurable,
        enumerable: srcDescriptor.enumerable,
        get: srcDescriptor.get,
        set(value) {
          if (!bridgeProbeScript(this, value)) srcDescriptor.set.call(this, value);
        }
      });
    }
    if (typeof MutationObserver === "function") {
      new MutationObserver(records => {
        for (const record of records) {
          for (const node of record.addedNodes || []) {
            if (node instanceof HTMLScriptElement) bridgeProbeScript(node, node.getAttribute("src"));
            if (node.querySelectorAll) {
              for (const script of node.querySelectorAll("script[src]")) bridgeProbeScript(script, script.getAttribute("src"));
            }
          }
        }
      }).observe(document, { childList: true, subtree: true });
    }
  }

  const availabilityRoute = cnlRoute("http://127.0.0.1:9666/flash/", "GET");
  forward(availabilityRoute, "").then(() => {
    window.jdownloader = true;
  }).catch(() => {
    window.jdownloader = false;
  });
})();

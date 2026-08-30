(() => {
  const REQUEST_MARKER = "omajdownload-cnl-request-v1";
  const RESPONSE_MARKER = "omajdownload-cnl-response-v1";
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
        return { url: url.href, method: "POST", probe: false };
      if (normalizedMethod === "GET" && (url.pathname.replace(/\/$/, "") === "/flash" || url.pathname === "/jdcheck.js"))
        return { url: url.href, method: "GET", probe: true };
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

  function forward(route, body) {
    const id = `${Date.now()}-${++sequence}`;
    return new Promise((resolve, reject) => {
      const timeout = setTimeout(() => {
        pending.delete(id);
        reject(new TypeError("OmaJDownLoad did not answer"));
      }, 10000);
      pending.set(id, { resolve, reject, timeout });
      window.postMessage({
        marker: REQUEST_MARKER,
        id,
        url: route.url,
        method: route.method,
        probe: route.probe,
        body: route.method === "POST" ? encodeBody(body) : ""
      }, "*");
    });
  }

  window.addEventListener("message", event => {
    if (event.source !== window || !event.data || event.data.marker !== RESPONSE_MARKER) return;
    const entry = pending.get(event.data.id);
    if (!entry) return;
    clearTimeout(entry.timeout);
    pending.delete(event.data.id);
    if (event.data.ok) entry.resolve(event.data);
    else entry.reject(new TypeError(event.data.message || `OmaJDownLoad returned ${event.data.status}`));
  });

  const nativeFetch = window.fetch.bind(window);
  window.fetch = function(input, init = {}) {
    const method = init.method || (input instanceof Request ? input.method : "GET");
    const route = cnlRoute(typeof input === "string" || input instanceof URL ? input : input.url, method);
    if (!route) return nativeFetch(input, init);
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
    return bodyPromise.then(body => forward(route, body)).then(result => new Response(result.body || "success\r\n", {
      status: result.status || 200,
      headers: { "Content-Type": route.url.endsWith("jdcheck.js") ? "application/javascript" : "text/plain; charset=utf-8" }
    }));
  };

  document.addEventListener("submit", event => {
    const form = event.target;
    if (!(form instanceof HTMLFormElement)) return;
    const route = cnlRoute(form.action, form.method || "GET");
    if (!route || route.method !== "POST") return;
    event.preventDefault();
    forward(route, new FormData(form)).catch(() => {});
  }, true);

  const nativeSubmit = HTMLFormElement.prototype.submit;
  HTMLFormElement.prototype.submit = function() {
    const route = cnlRoute(this.action, this.method || "GET");
    if (!route || route.method !== "POST") return nativeSubmit.call(this);
    forward(route, new FormData(this)).catch(() => {});
  };

  const NativeXHR = window.XMLHttpRequest;
  class OmaJDownLoadXHR extends NativeXHR {
    open(method, url, ...rest) {
      this.__omajRoute = cnlRoute(url, method);
      if (!this.__omajRoute) return super.open(method, url, ...rest);
      this.__omajReadyState = 1;
      this.dispatchEvent(new Event("readystatechange"));
    }
    setRequestHeader(name, value) {
      if (!this.__omajRoute) return super.setRequestHeader(name, value);
    }
    send(body) {
      if (!this.__omajRoute) return super.send(body);
      this.dispatchEvent(new ProgressEvent("loadstart"));
      forward(this.__omajRoute, body).then(result => {
        this.__omajReadyState = 2;
        this.dispatchEvent(new Event("readystatechange"));
        this.__omajReadyState = 3;
        this.dispatchEvent(new Event("readystatechange"));
        this.__omajStatus = result.status || 200;
        this.__omajResponseText = result.body || "success\r\n";
        this.__omajReadyState = 4;
        this.dispatchEvent(new Event("readystatechange"));
        this.dispatchEvent(new ProgressEvent("load"));
        this.dispatchEvent(new ProgressEvent("loadend"));
      }).catch(() => {
        this.__omajStatus = 0;
        this.__omajReadyState = 4;
        this.dispatchEvent(new Event("readystatechange"));
        this.dispatchEvent(new ProgressEvent("error"));
        this.dispatchEvent(new ProgressEvent("loadend"));
      });
    }
    get readyState() { return this.__omajRoute ? (this.__omajReadyState || 0) : super.readyState; }
    get status() { return this.__omajRoute ? (this.__omajStatus || 0) : super.status; }
    get responseText() { return this.__omajRoute ? (this.__omajResponseText || "") : super.responseText; }
    get response() { return this.__omajRoute ? (this.__omajResponseText || "") : super.response; }
  }
  window.XMLHttpRequest = OmaJDownLoadXHR;

  const availabilityRoute = cnlRoute("http://127.0.0.1:9666/flash/", "GET");
  forward(availabilityRoute, "").then(() => {
    window.jdownloader = true;
  }).catch(() => {
    window.jdownloader = false;
  });
})();

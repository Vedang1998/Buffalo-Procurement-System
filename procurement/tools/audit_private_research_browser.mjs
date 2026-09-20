#!/usr/bin/env node
// Browser-level, all-target CDP acceptance for one sealed private workspace.

import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import {fileURLToPath} from "node:url";

const [PHASE, BASE, CDP, EVIDENCE, DOWNLOADS, SECRET_PATH, EXPECTATIONS_PATH, STATE_PATH] =
  process.argv.slice(2);

if (
  ![PHASE, BASE, CDP, EVIDENCE, DOWNLOADS, SECRET_PATH, EXPECTATIONS_PATH, STATE_PATH].every(Boolean) ||
  !["initial", "restart"].includes(PHASE)
) {
  throw new Error("private-research browser audit arguments are incomplete");
}

const CONTRACT = "BUFFALO_PRIVATE_RESEARCH_BROWSER_ACCEPTANCE_V2";
const ALLOWED_ORIGIN = new URL(BASE).origin;
const SELF_PATH = fileURLToPath(import.meta.url);
const SCRIPT_SHA256 = crypto.createHash("sha256").update(fs.readFileSync(SELF_PATH)).digest("hex");
const SECRET = fs.readFileSync(SECRET_PATH).toString("utf8").replace(/\n$/, "");
const AUTHORIZATION = `Basic ${Buffer.from(`private:${SECRET}`, "utf8").toString("base64")}`;
const WRONG_AUTHORIZATION = `Basic ${Buffer.from(
  "private:deliberately-wrong-browser-audit-credential",
  "utf8",
).toString("base64")}`;
const EXPECTED = JSON.parse(fs.readFileSync(EXPECTATIONS_PATH, "utf8"));
const AUTH_MODE_HEADER = "x-buffalo-browser-audit-auth-mode";
const GUARDED_TARGET_TYPES = new Set([
  "page",
  "iframe",
  "worker",
  "shared_worker",
  "service_worker",
  "webview",
  "background_page",
  "browser_ui",
]);
const FETCH_TARGET_TYPES = new Set([
  "page",
  "iframe",
  "webview",
  "background_page",
  "browser_ui",
]);
const WORKER_TARGET_TYPES = new Set(["worker", "shared_worker", "service_worker"]);
const APPLICATION_TARGET_TYPES = new Set(["page", "iframe", "worker", "shared_worker", "service_worker", "webview"]);
const SOCKET_GUARD_BINDING = "__buffaloPrivateBrowserSocketAttempt";
const SOCKET_GUARD_SOURCE = `(() => {
  const report = globalThis[${JSON.stringify(SOCKET_GUARD_BINDING)}];
  if (typeof report !== "function") return false;
  const BlockedWebSocket = function(rawUrl) {
    report(String(rawUrl));
    throw new Error("WebSocket is disabled by the private browser audit");
  };
  Object.defineProperties(BlockedWebSocket, {
    CONNECTING: {value: 0}, OPEN: {value: 1}, CLOSING: {value: 2}, CLOSED: {value: 3}
  });
  Object.defineProperty(globalThis, "WebSocket", {
    value: BlockedWebSocket, configurable: false, writable: false
  });
  return globalThis.WebSocket === BlockedWebSocket;
})()`;
const ARTIFACT_NAMES = [
  "owner-preview.html",
  "owner-worksheet.csv",
  "coverage.json",
  "projection.json",
];
const sleep = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));

function safeUrl(raw) {
  try {
    const value = new URL(raw);
    return `${value.protocol}//${value.host}${value.pathname}`;
  } catch {
    return "unparseable-url";
  }
}

function stable(value) {
  if (Array.isArray(value)) return `[${value.map(stable).join(",")}]`;
  if (value !== null && typeof value === "object") {
    return `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${stable(value[key])}`).join(",")}}`;
  }
  return JSON.stringify(value);
}

function sha256Bytes(value) {
  return crypto.createHash("sha256").update(value).digest("hex");
}

function semanticSha256(value) {
  return sha256Bytes(Buffer.from(stable(value), "utf8"));
}

function uniquePush(items, record) {
  const key = stable(record);
  if (!items.some((item) => stable(item) === key)) items.push(record);
}

class BrowserCdpClient {
  constructor(url) {
    this.socket = new WebSocket(url);
    this.nextId = 1;
    this.pending = new Map();
    this.waiters = new Map();
    this.inflight = new Map();
    this.sessions = new Map();
    this.guardTasks = new Set();
    this.mainSessionId = null;
    this.browserVersion = null;
    this.autoAttachEnabled = false;
    this.requests = [];
    this.responses = [];
    this.externalRequests = [];
    this.blockedExternalRequests = [];
    this.externalWebSocketRequests = [];
    this.unexpectedSchemeRequests = [];
    this.runtimeExceptions = [];
    this.consoleErrors = [];
    this.eventErrors = [];
  }

  async open() {
    await new Promise((resolve, reject) => {
      this.socket.onopen = resolve;
      this.socket.onerror = reject;
    });
    this.socket.onmessage = (event) => this.onMessage(event);
    this.socket.onclose = () => {
      for (const pending of this.pending.values()) {
        pending.reject(new Error("Chromium CDP socket closed"));
      }
      this.pending.clear();
      for (const queue of this.waiters.values()) {
        for (const waiter of queue) {
          clearTimeout(waiter.timer);
          waiter.reject(new Error("Chromium CDP socket closed"));
        }
      }
      this.waiters.clear();
    };
  }

  onMessage(event) {
    let message;
    try {
      message = JSON.parse(event.data);
    } catch (error) {
      this.eventErrors.push(`malformed CDP message: ${String(error)}`);
      return;
    }
    if (message.id) {
      const pending = this.pending.get(message.id);
      if (!pending) return;
      this.pending.delete(message.id);
      if (message.error) pending.reject(new Error(message.error.message));
      else pending.resolve(message.result);
      return;
    }

    const sessionId = message.sessionId || null;
    const params = message.params || {};
    if (message.method === "Target.attachedToTarget") {
      const task = this.guardTarget(params);
      this.guardTasks.add(task);
      void task
        .catch((error) => this.eventErrors.push(`target guard task failed: ${String(error)}`))
        .finally(() => this.guardTasks.delete(task));
    }
    if (message.method === "Fetch.requestPaused") {
      const task = this.handlePausedRequest(sessionId, params);
      this.guardTasks.add(task);
      void task
        .catch((error) => this.eventErrors.push(`request guard task failed: ${String(error)}`))
        .finally(() => this.guardTasks.delete(task));
    }
    if (message.method === "Fetch.authRequired") {
      const task = this.send(
        "Fetch.continueWithAuth",
        {
          requestId: params.requestId,
          authChallengeResponse: {response: "CancelAuth"},
        },
        sessionId,
      );
      this.guardTasks.add(task);
      void task
        .catch((error) => this.eventErrors.push(`auth challenge cancellation failed: ${String(error)}`))
        .finally(() => this.guardTasks.delete(task));
    }
    if (message.method === "Network.requestWillBeSent") {
      this.recordRequest(sessionId, params);
    }
    if (message.method === "Network.responseReceived") {
      this.recordResponse(sessionId, params);
    }
    if (message.method === "Network.webSocketCreated") {
      uniquePush(this.externalWebSocketRequests, {
        targetType: this.sessions.get(sessionId)?.type || "unknown",
        url: safeUrl(params.url),
      });
    }
    if (
      message.method === "Runtime.bindingCalled" &&
      params.name === SOCKET_GUARD_BINDING
    ) {
      const record = {
        method: "WEBSOCKET",
        targetType: this.sessions.get(sessionId)?.type || "unknown",
        url: safeUrl(params.payload),
      };
      uniquePush(this.externalWebSocketRequests, record);
      uniquePush(this.blockedExternalRequests, record);
    }
    if (
      message.method === "Network.webTransportCreated" ||
      message.method === "Network.directTCPSocketCreated"
    ) {
      uniquePush(this.unexpectedSchemeRequests, {
        targetType: this.sessions.get(sessionId)?.type || "unknown",
        scheme: message.method,
        url: params.url ? safeUrl(params.url) : null,
      });
    }
    if (message.method === "Runtime.exceptionThrown") {
      this.runtimeExceptions.push(params.exceptionDetails?.text || "runtime exception");
    }
    if (
      message.method === "Runtime.consoleAPICalled" &&
      ["error", "assert"].includes(params.type)
    ) {
      this.consoleErrors.push(params.type);
    }

    const key = `${sessionId || "browser"}:${message.method}`;
    const queue = this.waiters.get(key) || [];
    if (queue.length) {
      const waiter = queue.shift();
      clearTimeout(waiter.timer);
      waiter.resolve(params);
    }
  }

  send(method, params = {}, sessionId = null) {
    const id = this.nextId++;
    return new Promise((resolve, reject) => {
      this.pending.set(id, {resolve, reject});
      const message = {id, method, params};
      if (sessionId) message.sessionId = sessionId;
      this.socket.send(JSON.stringify(message));
    });
  }

  waitEvent(method, sessionId, timeoutMs = 30000) {
    return new Promise((resolve, reject) => {
      const key = `${sessionId || "browser"}:${method}`;
      const queue = this.waiters.get(key) || [];
      const timer = setTimeout(() => {
        const current = this.waiters.get(key) || [];
        this.waiters.set(key, current.filter((entry) => entry.resolve !== resolve));
        reject(new Error(`timed out waiting for ${method}`));
      }, timeoutMs);
      queue.push({resolve, reject, timer});
      this.waiters.set(key, queue);
    });
  }

  async initialize() {
    const version = await this.send("Browser.getVersion");
    this.browserVersion = {
      product: version.product,
      protocol_version: version.protocolVersion,
      js_version: version.jsVersion,
    };
    await this.send("Target.setDiscoverTargets", {discover: true});
    await this.send("Target.setAutoAttach", {
      autoAttach: true,
      waitForDebuggerOnStart: true,
      flatten: true,
    });
    this.autoAttachEnabled = true;
    const deadline = Date.now() + 30000;
    while (Date.now() < deadline) {
      await this.flushGuardTasks();
      const main = this.sessions.get(this.mainSessionId);
      if (main?.guarded && main?.resumed) return;
      await sleep(25);
    }
    throw new Error("no guarded Chromium page target became available");
  }

  async guardTarget(params) {
    const sessionId = params.sessionId;
    const targetInfo = params.targetInfo || {};
    if (!sessionId || !targetInfo.targetId) {
      this.eventErrors.push("CDP attached target identity is incomplete");
      return;
    }
    const session = {
      targetId: targetInfo.targetId,
      type: targetInfo.type || "unknown",
      guarded: false,
      resumed: false,
    };
    this.sessions.set(sessionId, session);
    if (!GUARDED_TARGET_TYPES.has(session.type)) {
      this.eventErrors.push(`unexpected attached target type: ${session.type}`);
      try {
        await this.send("Target.closeTarget", {targetId: session.targetId});
      } catch (error) {
        this.eventErrors.push(`could not close unexpected target: ${String(error)}`);
      }
      return;
    }
    try {
      await this.send("Network.enable", {}, sessionId);
      const blockedSchemes = WORKER_TARGET_TYPES.has(session.type)
        ? ["http", "https", "ws", "wss", "ftp"]
        : ["ws", "wss", "ftp"];
      await this.send(
        "Network.setBlockedURLs",
        {
          urlPatterns: blockedSchemes.map((scheme) => ({
            urlPattern: `${scheme}://*:*/*`,
            block: true,
          })),
        },
        sessionId,
      );
      if (FETCH_TARGET_TYPES.has(session.type)) {
        await this.send(
          "Fetch.enable",
          {patterns: [{urlPattern: "*", requestStage: "Request"}], handleAuthRequests: true},
          sessionId,
        );
      }
      await this.send("Runtime.enable", {}, sessionId);
      if (APPLICATION_TARGET_TYPES.has(session.type)) {
        await this.send("Runtime.addBinding", {name: SOCKET_GUARD_BINDING}, sessionId);
        if (FETCH_TARGET_TYPES.has(session.type)) {
          await this.send(
            "Page.addScriptToEvaluateOnNewDocument",
            {source: SOCKET_GUARD_SOURCE},
            sessionId,
          );
        }
        const installed = await this.send(
          "Runtime.evaluate",
          {expression: SOCKET_GUARD_SOURCE, returnByValue: true},
          sessionId,
        );
        if (installed.exceptionDetails || installed.result?.value !== true) {
          throw new Error("WebSocket pre-network guard installation failed");
        }
      }
      if (FETCH_TARGET_TYPES.has(session.type)) {
        await this.send("Page.enable", {}, sessionId);
        await this.send(
          "Target.setAutoAttach",
          {autoAttach: true, waitForDebuggerOnStart: true, flatten: true},
          sessionId,
        );
      }
      session.guarded = true;
      if (session.type === "page" && this.mainSessionId === null) {
        this.mainSessionId = sessionId;
      }
      await this.send("Runtime.runIfWaitingForDebugger", {}, sessionId);
      session.resumed = true;
    } catch (error) {
      this.eventErrors.push(`target guard failed (${session.type}): ${String(error)}`);
      try {
        await this.send("Target.closeTarget", {targetId: session.targetId});
      } catch (closeError) {
        this.eventErrors.push(`guard-failed target close failed: ${String(closeError)}`);
      }
    }
  }

  async handlePausedRequest(sessionId, params) {
    const request = params.request || {};
    let target;
    try {
      target = new URL(request.url);
    } catch {
      target = null;
    }
    const record = {
      method: request.method || null,
      targetType: this.sessions.get(sessionId)?.type || "unknown",
      url: safeUrl(request.url),
    };
    if (!target) {
      uniquePush(this.unexpectedSchemeRequests, {...record, scheme: "unparseable"});
      await this.send(
        "Fetch.failRequest",
        {requestId: params.requestId, errorReason: "BlockedByClient"},
        sessionId,
      );
      return;
    }
    if (["ws:", "wss:"].includes(target.protocol)) {
      uniquePush(this.externalWebSocketRequests, record);
      uniquePush(this.blockedExternalRequests, record);
      await this.send(
        "Fetch.failRequest",
        {requestId: params.requestId, errorReason: "BlockedByClient"},
        sessionId,
      );
      return;
    }
    if (!["http:", "https:"].includes(target.protocol)) {
      uniquePush(this.unexpectedSchemeRequests, {...record, scheme: target.protocol});
      await this.send(
        "Fetch.failRequest",
        {requestId: params.requestId, errorReason: "BlockedByClient"},
        sessionId,
      );
      return;
    }
    if (target.origin !== ALLOWED_ORIGIN) {
      uniquePush(this.externalRequests, record);
      uniquePush(this.blockedExternalRequests, record);
      await this.send(
        "Fetch.failRequest",
        {requestId: params.requestId, errorReason: "BlockedByClient"},
        sessionId,
      );
      return;
    }

    const sourceHeaders = Object.entries(request.headers || {});
    const modeEntry = sourceHeaders.find(([name]) => name.toLowerCase() === AUTH_MODE_HEADER);
    const mode = modeEntry ? String(modeEntry[1]).toLowerCase() : "normal";
    if (!["normal", "missing", "wrong"].includes(mode)) {
      uniquePush(this.unexpectedSchemeRequests, {...record, scheme: "invalid-auth-mode"});
      await this.send(
        "Fetch.failRequest",
        {requestId: params.requestId, errorReason: "BlockedByClient"},
        sessionId,
      );
      return;
    }
    const headers = sourceHeaders
      .filter(([name]) => !["authorization", AUTH_MODE_HEADER].includes(name.toLowerCase()))
      .map(([name, value]) => ({name, value: String(value)}));
    if (mode === "normal") headers.push({name: "Authorization", value: AUTHORIZATION});
    if (mode === "wrong") headers.push({name: "Authorization", value: WRONG_AUTHORIZATION});
    await this.send(
      "Fetch.continueRequest",
      {requestId: params.requestId, headers},
      sessionId,
    );
  }

  recordRequest(sessionId, params) {
    const request = params.request || {};
    let target;
    try {
      target = new URL(request.url);
    } catch {
      return;
    }
    const targetType = this.sessions.get(sessionId)?.type || "unknown";
    const record = {
      requestId: params.requestId,
      method: request.method,
      targetType,
      url: safeUrl(request.url),
      origin: target.origin,
    };
    if (["http:", "https:"].includes(target.protocol)) {
      this.inflight.set(`${sessionId}:${params.requestId}`, record);
      this.requests.push(record);
      if (target.origin !== ALLOWED_ORIGIN) uniquePush(this.externalRequests, record);
      if (WORKER_TARGET_TYPES.has(targetType)) {
        uniquePush(this.unexpectedSchemeRequests, {...record, scheme: "worker-network"});
      }
    } else if (
      !["about:", "data:", "blob:"].includes(target.protocol) &&
      !(
        ["browser_ui", "background_page"].includes(targetType) &&
        ["chrome:", "chrome-extension:"].includes(target.protocol)
      )
    ) {
      uniquePush(this.unexpectedSchemeRequests, {...record, scheme: target.protocol});
    }
  }

  recordResponse(sessionId, params) {
    const response = params.response || {};
    if (!/^https?:/i.test(response.url || "")) return;
    const headers = Object.fromEntries(
      Object.entries(response.headers || {}).map(([key, value]) => [key.toLowerCase(), value]),
    );
    const request = this.inflight.get(`${sessionId}:${params.requestId}`);
    this.responses.push({
      requestId: params.requestId,
      method: request?.method || null,
      url: safeUrl(response.url),
      origin: new URL(response.url).origin,
      status: response.status,
      cacheControl: headers["cache-control"] || null,
      contentDisposition: headers["content-disposition"] || null,
      contentType: headers["content-type"] || null,
      mimeType: response.mimeType,
    });
  }

  async flushGuardTasks() {
    while (this.guardTasks.size) {
      await Promise.allSettled([...this.guardTasks]);
    }
  }

  targetSummary() {
    const sessions = [...this.sessions.values()];
    const byType = {};
    for (const session of sessions) {
      byType[session.type] = (byType[session.type] || 0) + 1;
    }
    return {
      observed_types: Object.keys(byType).sort(),
      by_type: Object.fromEntries(Object.entries(byType).sort(([left], [right]) => left.localeCompare(right))),
      attached: sessions.length,
      guarded: sessions.filter((session) => session.guarded).length,
      resumed: sessions.filter((session) => session.resumed).length,
    };
  }

  allTargetsGuarded() {
    const sessions = [...this.sessions.values()];
    return (
      this.autoAttachEnabled &&
      sessions.length > 0 &&
      sessions.every(
        (session) =>
          GUARDED_TARGET_TYPES.has(session.type) && session.guarded === true && session.resumed === true,
      )
    );
  }

  async closeBrowser() {
    try {
      await Promise.race([this.send("Browser.close"), sleep(500)]);
    } catch {
      // Python independently terminates and verifies the entire Chromium session.
    }
    try {
      this.socket.close();
    } catch {
      // The Browser.close command may already have closed the socket.
    }
  }
}

const results = {
  contract: CONTRACT,
  phase: PHASE,
  base_url: BASE,
  passed: false,
  assertions: [],
  semantic_hashes: null,
  workspace_hashes: null,
  counts: null,
  artifact_hashes: null,
  tooling: null,
  target_summary: null,
  external_requests: [],
  blocked_external_requests: [],
  external_websocket_requests: [],
  unexpected_scheme_requests: [],
};

function check(condition, id, detail = null) {
  if (results.assertions.some((item) => item.id === id)) {
    throw new Error(`duplicate assertion identifier: ${id}`);
  }
  if (!condition) {
    throw new Error(`ASSERTION FAILED [${id}]: ${JSON.stringify(detail)}`);
  }
  results.assertions.push({id, passed: true});
}

async function connect() {
  const versionResponse = await fetch(`${CDP}/json/version`);
  const version = await versionResponse.json();
  if (typeof version.webSocketDebuggerUrl !== "string") {
    throw new Error("Chromium browser CDP websocket is unavailable");
  }
  const client = new BrowserCdpClient(version.webSocketDebuggerUrl);
  await client.open();
  await client.initialize();
  await client.send("Browser.setDownloadBehavior", {
    behavior: "allow",
    downloadPath: DOWNLOADS,
    eventsEnabled: true,
  });
  return client;
}

async function audit(client) {
  const sessionId = client.mainSessionId;
  const evaluate = async (expression, options = {}) => {
    const response = await client.send(
      "Runtime.evaluate",
      {
        expression,
        returnByValue: true,
        awaitPromise: true,
        ...options,
      },
      sessionId,
    );
    if (response.exceptionDetails) {
      throw new Error(
        response.exceptionDetails.exception?.description ||
          response.exceptionDetails.text ||
          "Runtime.evaluate failed",
      );
    }
    return response.result.value;
  };

  const navigate = async (url) => {
    const loaded = client.waitEvent("Page.loadEventFired", sessionId);
    const response = await client.send("Page.navigate", {url}, sessionId);
    if (response.errorText) throw new Error(`navigation failed: ${response.errorText}`);
    await loaded;
    await sleep(80);
  };

  const fetchPayload = (requestPath, authMode = "normal", method = "GET") =>
    evaluate(`fetch(${JSON.stringify(requestPath)}, {
      cache:"no-store",
      method:${JSON.stringify(method)},
      headers:{${JSON.stringify(AUTH_MODE_HEADER)}:${JSON.stringify(authMode)}}
    }).then(async (response) => ({
      status: response.status,
      body: await response.text(),
      cacheControl: response.headers.get("cache-control"),
      contentType: response.headers.get("content-type"),
      authenticate: response.headers.get("www-authenticate")
    }))`);

  const pageSnapshot = () => evaluate(`(() => ({
    url: location.href,
    text: document.body.innerText,
    articles: [...document.querySelectorAll("article.record")].map((article) => ({
      hasDetails: Boolean(article.querySelector("details")),
      detail: JSON.parse(article.querySelector("pre").textContent)
    })),
    forms: [...document.forms].map((form) => ({method: form.method.toUpperCase(), action: form.action})),
    buttons: [...document.querySelectorAll("button")].map((button) => ({
      text: button.textContent.trim(), type: button.type
    })),
    links: [...document.querySelectorAll("a[href]")].map((anchor) => anchor.href)
  }))()`);

  const readOnlySnapshot = (snapshot) => {
    const forbiddenPath = /(monday-runs|price-books|supplier-mapping|pricing|reconciliation|vendor-rules|economics|draft|purchase|orders|po)/i;
    return (
      snapshot.forms.length === 1 &&
      snapshot.forms[0].method === "GET" &&
      new URL(snapshot.forms[0].action).pathname === "/private-research" &&
      stable(snapshot.buttons) === stable([{text: "Filter", type: "submit"}]) &&
      snapshot.links.every((raw) => {
        const value = new URL(raw);
        return value.origin === ALLOWED_ORIGIN && !forbiddenPath.test(value.pathname);
      })
    );
  };

  const assertRows = async (expected, prefix) => {
    const snapshot = await pageSnapshot();
    check(
      stable(snapshot.articles.map((article) => article.detail)) === stable(expected.first_page_rows),
      `${prefix}.rows`,
      {expected: expected.first_page_rows.length, actual: snapshot.articles.length},
    );
    check(
      snapshot.articles.every((article) => article.hasDetails),
      `${prefix}.details`,
      {articles: snapshot.articles.length},
    );
    check(
      snapshot.text.includes(`Showing ${expected.first_page_rows.length} of ${expected.total} matched rows`),
      `${prefix}.count`,
      {total: expected.total, displayed: expected.first_page_rows.length},
    );
    check(readOnlySnapshot(snapshot), `${prefix}.read_only`, {
      forms: snapshot.forms.length,
      buttons: snapshot.buttons.length,
      links: snapshot.links.length,
    });
    return snapshot;
  };

  const submitFilter = async (parameters) => {
    const loaded = client.waitEvent("Page.loadEventFired", sessionId);
    try {
      await evaluate(`(() => {
        const form = document.querySelector('form[action="/private-research"]');
        if (!form) throw new Error("private research filter form is absent");
        const supplied = ${JSON.stringify(parameters)};
        for (const name of ["q", "vendor", "status", "stockout"]) {
          const control = form.elements.namedItem(name);
          if (!control) throw new Error("filter control is absent: " + name);
          control.value = supplied[name] || "";
          control.dispatchEvent(new Event("input", {bubbles: true}));
          control.dispatchEvent(new Event("change", {bubbles: true}));
        }
        form.requestSubmit();
        return true;
      })()`);
    } catch (error) {
      if (!/context|navigat|destroyed/i.test(String(error))) throw error;
    }
    await loaded;
    await sleep(80);
  };

  const waitForDownload = async (before) => {
    const deadline = Date.now() + 60000;
    while (Date.now() < deadline) {
      const names = fs.readdirSync(DOWNLOADS);
      const complete = names.filter((name) => !name.endsWith(".crdownload"));
      const added = complete.filter((name) => !before.has(name));
      if (added.length === 1 && !names.some((name) => name.endsWith(".crdownload"))) {
        return added[0];
      }
      await sleep(100);
    }
    throw new Error("browser artifact download timed out");
  };

  const downloadArtifact = async (expected) => {
    const slug = expected.name.replaceAll(".", "_").replaceAll("-", "_");
    const before = new Set(fs.readdirSync(DOWNLOADS));
    const responseMark = client.responses.length;
    await evaluate(`(() => {
      const name = ${JSON.stringify(expected.name)};
      const anchor = [...document.querySelectorAll('a[href]')].find(
        (item) => new URL(item.href).pathname === "/private-research/artifacts/" + name
      );
      if (!anchor) throw new Error("artifact link is absent: " + name);
      anchor.click();
      return true;
    })()`);
    const receivedName = await waitForDownload(before);
    const data = fs.readFileSync(path.join(DOWNLOADS, receivedName));
    const deadline = Date.now() + 10000;
    let response = null;
    while (Date.now() < deadline && !response) {
      response = client.responses.slice(responseMark).find(
        (item) => new URL(item.url).pathname === `/private-research/artifacts/${expected.name}`,
      );
      if (!response) await sleep(25);
    }
    check(receivedName === expected.name, `v2.artifact.${slug}.filename`, {
      expected: expected.name,
      received: receivedName,
    });
    check(
      data.length === expected.bytes && sha256Bytes(data) === expected.sha256,
      `v2.artifact.${slug}.bytes`,
      {name: expected.name, bytes: data.length, sha256: sha256Bytes(data)},
    );
    const headerMime = String(response?.contentType || "").split(";", 1)[0].trim().toLowerCase();
    check(
      response?.status === 200 &&
        response.cacheControl === "no-store" &&
        String(response.contentDisposition).toLowerCase().includes("attachment") &&
        String(response.mimeType).toLowerCase() === expected.browser_mime_type &&
        headerMime === expected.browser_mime_type,
      `v2.artifact.${slug}.response`,
      {
        name: expected.name,
        status: response?.status || null,
        mimeType: response?.mimeType || null,
        contentType: headerMime || null,
      },
    );
    return expected.sha256;
  };

  check(
    Number(process.versions.node.split(".", 1)[0]) >= 22 &&
      typeof fetch === "function" &&
      typeof WebSocket === "function" &&
      SCRIPT_SHA256 === EXPECTED.tooling.script_sha256,
    "v2.cdp.node_prerequisites",
    {version: process.version, scriptSha256: SCRIPT_SHA256},
  );
  check(
    client.browserVersion && Object.values(client.browserVersion).every((value) => typeof value === "string" && value),
    "v2.cdp.browser_version",
    client.browserVersion,
  );

  await navigate(`${BASE}/private-research`);
  const initialSnapshot = await pageSnapshot();
  check(
    stable(initialSnapshot.articles.map((article) => article.detail)) ===
      stable(EXPECTED.initial.first_page_rows),
    "v2.page.initial.rows",
    {expected: EXPECTED.initial.first_page_rows.length, actual: initialSnapshot.articles.length},
  );
  check(
    initialSnapshot.articles.every((article) => article.hasDetails),
    "v2.page.initial.details",
    {articles: initialSnapshot.articles.length},
  );
  check(
    initialSnapshot.text.includes(
      `Showing ${EXPECTED.initial.first_page_rows.length} of ${EXPECTED.initial.total} matched rows`,
    ),
    "v2.page.initial.count",
    {total: EXPECTED.initial.total, displayed: EXPECTED.initial.first_page_rows.length},
  );
  check(readOnlySnapshot(initialSnapshot), "v2.page.initial.read_only", {
    forms: initialSnapshot.forms.length,
    buttons: initialSnapshot.buttons.length,
    links: initialSnapshot.links.length,
  });

  const healthResponse = await fetchPayload("/health");
  const health = JSON.parse(healthResponse.body);
  check(
    healthResponse.status === 200 &&
      healthResponse.cacheControl === "no-store" &&
      stable(Object.keys(health).sort()) ===
        stable(["mode", "ok", "operational_authority", "service"].sort()) &&
      health.ok === true &&
      health.operational_authority === false,
    "v2.health.contract",
    {status: healthResponse.status, keys: Object.keys(health).sort()},
  );
  check(!/\b[0-9a-f]{64}\b/i.test(healthResponse.body), "v2.health.no_hashes");

  const manifestResponse = await fetchPayload("/private-research/manifest");
  const projectionResponse = await fetchPayload("/private-research/projection");
  const manifest = JSON.parse(manifestResponse.body);
  const projection = JSON.parse(projectionResponse.body);
  check(
    manifestResponse.status === 200 && stable(manifest) === stable(EXPECTED.manifest),
    "v2.readback.manifest",
    {expectedSha256: EXPECTED.semantic_hashes.manifest_sha256, actualSha256: semanticSha256(manifest)},
  );
  check(
    projectionResponse.status === 200 && stable(projection) === stable(EXPECTED.projection),
    "v2.readback.projection",
    {expectedSha256: EXPECTED.semantic_hashes.projection_sha256, actualSha256: semanticSha256(projection)},
  );
  const actualCounts = {
    coverage_rows: projection.coverage_rows.length,
    research_rows: projection.research_rows.length,
    owner_worksheet: projection.owner_worksheet.length,
    unjoined_supplier_hypotheses: projection.unjoined_supplier_hypotheses.length,
    vendor_names: projection.vendor_names.length,
  };
  check(
    stable(actualCounts) === stable(EXPECTED.counts),
    "v2.readback.counts",
    actualCounts,
  );

  const authSurfaces = [
    ["index", "/private-research"],
    ["manifest", "/private-research/manifest"],
    ["projection", "/private-research/projection"],
    ["artifact", `/private-research/artifacts/${EXPECTED.artifacts[0].name}`],
  ];
  const privateMarkers = [
    EXPECTED.workspace_hashes.workspace_id,
    EXPECTED.workspace_hashes.projection_sha256,
    EXPECTED.evidence.source_occurrence_ref,
  ];
  for (const mode of ["missing", "wrong"]) {
    for (const [surface, requestPath] of authSurfaces) {
      const response = await fetchPayload(requestPath, mode);
      check(
          response.status === 401 &&
          response.cacheControl === "no-store" &&
          response.authenticate === 'Basic realm="Buffalo private research", charset="UTF-8"' &&
          response.body === "Private review authentication failed" &&
          privateMarkers.every((marker) => !response.body.includes(marker)),
        `v2.auth.${mode}.${surface}`,
        {
          path: requestPath,
          status: response.status,
          challengePresent:
            response.authenticate === 'Basic realm="Buffalo private research", charset="UTF-8"',
        },
      );
    }
  }

  const operationalProbes = [
    "/openapi.json",
    "/monday-runs",
    "/price-books/template.csv",
    "/supplier-mapping/intake",
    "/pricing/rollover",
    "/vendor-rules/fixture",
    "/reconciliation/catalog",
    "/economics/target-cost",
  ];
  const probeStatuses = [];
  for (const requestPath of operationalProbes) {
    const response = await fetchPayload(requestPath);
    probeStatuses.push({path: requestPath, status: response.status});
  }
  check(
    probeStatuses.every((item) => item.status === 404),
    "v2.routes.operational_absent",
    probeStatuses,
  );
  const writeProbeStatuses = [];
  for (const method of ["POST", "PUT", "PATCH", "DELETE"]) {
    const response = await fetchPayload("/__browser-audit-write-probe__", "normal", method);
    writeProbeStatuses.push({method, status: response.status});
    if (
      response.body !== "Route is not authorized" ||
      response.cacheControl !== "no-store"
    ) {
      writeProbeStatuses.push({method, responseContractDiffers: true});
    }
  }
  check(
    writeProbeStatuses.length === 4 &&
      writeProbeStatuses.every((item) => item.status === 403),
    "v2.routes.write_methods_denied",
    writeProbeStatuses,
  );

  let evidenceDetail = null;
  for (const filter of EXPECTED.filters) {
    await navigate(`${BASE}/private-research`);
    await submitFilter(filter.parameters);
    const current = new URL(await evaluate("location.href"));
    check(
      Object.entries(filter.parameters).every(([key, value]) => current.searchParams.get(key) === value),
      `v2.filter.${filter.name}.submit`,
      {url: safeUrl(current.href), parameterCount: Object.keys(filter.parameters).length},
    );
    const snapshot = await assertRows(filter, `v2.filter.${filter.name}`);
    if (filter.name === "search") {
      evidenceDetail = snapshot.articles.find(
        (article) => article.detail.source_occurrence_ref === EXPECTED.evidence.source_occurrence_ref,
      )?.detail;
    }
  }
  check(
    stable(evidenceDetail) === stable(EXPECTED.initial.detail_row),
    "v2.detail.exact",
    {present: Boolean(evidenceDetail)},
  );
  check(
    ["source_ref", "forecast", "abc", "economics", "missing_data_reasons"].every(
      (field) => Object.hasOwn(evidenceDetail || {}, field),
    ),
    "v2.detail.core_evidence",
  );
  check(
    evidenceDetail?.source_occurrence_ref === EXPECTED.evidence.source_occurrence_ref,
    "v2.detail.source_occurrence",
  );
  check(
    stable(evidenceDetail?.unapproved_mapping_evidence) === stable(EXPECTED.evidence.mapping_evidence),
    "v2.detail.mapping_evidence",
  );
  check(
    stable(evidenceDetail?.unapproved_mapping_blocker_reasons) ===
      stable(EXPECTED.evidence.mapping_blocker_reasons) &&
      EXPECTED.evidence.mapping_blocker_reasons.every((reason) =>
        evidenceDetail?.missing_data_reasons?.includes(reason),
      ),
    "v2.detail.mapping_blockers",
  );
  check(Boolean(evidenceDetail), "v2.search.source_occurrence");

  await navigate(`${BASE}/private-research`);
  const artifactHashes = {};
  for (const artifact of EXPECTED.artifacts) {
    artifactHashes[artifact.name] = await downloadArtifact(artifact);
  }
  check(
    stable(Object.keys(artifactHashes).sort()) === stable([...ARTIFACT_NAMES].sort()),
    "v2.artifacts.complete",
    {count: Object.keys(artifactHashes).length},
  );

  const cookies = (await client.send("Network.getAllCookies", {}, sessionId)).cookies;
  check(
    !cookies.some((cookie) => cookie.name === "buffalo_local_session"),
    "v2.auth.no_ambient_cookie",
  );

  const semanticHashes = {
    manifest_sha256: semanticSha256(manifest),
    projection_sha256: semanticSha256(projection),
  };
  const restartState = {
    contract: CONTRACT,
    base_url: BASE,
    workspace_hashes: EXPECTED.workspace_hashes,
    semantic_hashes: semanticHashes,
    counts: actualCounts,
    artifact_hashes: artifactHashes,
  };
  if (PHASE === "initial") {
    fs.writeFileSync(STATE_PATH, `${JSON.stringify(restartState, null, 2)}\n`, {
      encoding: "utf8",
      mode: 0o600,
      flag: "wx",
    });
    check(true, "v2.restart.identity");
  } else {
    const prior = JSON.parse(fs.readFileSync(STATE_PATH, "utf8"));
    check(stable(prior) === stable(restartState), "v2.restart.identity", {
      workspaceMatches: prior.workspace_hashes?.workspace_id === restartState.workspace_hashes.workspace_id,
    });
  }

  await sleep(500);
  await client.flushGuardTasks();
  check(client.allTargetsGuarded(), "v2.cdp.all_targets_guarded", {
    ...client.targetSummary(),
    guardErrors: client.eventErrors.slice(0, 5),
  });
  check(client.externalRequests.length === 0, "v2.network.no_external_http", {
    count: client.externalRequests.length,
  });
  check(client.externalWebSocketRequests.length === 0, "v2.network.no_external_websocket", {
    count: client.externalWebSocketRequests.length,
  });
  check(client.unexpectedSchemeRequests.length === 0, "v2.network.no_unexpected_scheme", {
    count: client.unexpectedSchemeRequests.length,
    schemes: [...new Set(client.unexpectedSchemeRequests.map((item) => item.scheme))].sort(),
  });
  check(client.runtimeExceptions.length === 0, "v2.browser.no_runtime_exceptions", {
    count: client.runtimeExceptions.length,
  });
  check(client.consoleErrors.length === 0, "v2.browser.no_console_errors", {
    count: client.consoleErrors.length,
  });
  check(client.eventErrors.length === 0, "v2.cdp.no_event_errors", {
    count: client.eventErrors.length,
  });
  const applicationResponses = client.responses.filter((response) => response.origin === ALLOWED_ORIGIN);
  check(
    applicationResponses.length > 0 && applicationResponses.every((response) => response.cacheControl === "no-store"),
    "v2.responses.no_store",
    {responses: applicationResponses.length},
  );

  const actualAssertionIds = results.assertions.map((item) => item.id).sort();
  const expectedAssertionIds = [...EXPECTED.assertion_ids].sort();
  if (stable(actualAssertionIds) !== stable(expectedAssertionIds)) {
    throw new Error("versioned browser assertion catalog differs");
  }
  results.semantic_hashes = semanticHashes;
  results.workspace_hashes = EXPECTED.workspace_hashes;
  results.counts = actualCounts;
  results.artifact_hashes = artifactHashes;
  results.tooling = {
    node_version: process.version,
    script_sha256: SCRIPT_SHA256,
    browser: client.browserVersion,
  };
  results.target_summary = client.targetSummary();
  results.external_requests = client.externalRequests;
  results.blocked_external_requests = client.blockedExternalRequests;
  results.external_websocket_requests = client.externalWebSocketRequests;
  results.unexpected_scheme_requests = client.unexpectedSchemeRequests;
  results.request_count = client.requests.length;
  results.response_count = client.responses.length;
  results.passed = true;
}

let client = null;
let failure = null;
try {
  client = await connect();
  await audit(client);
} catch (error) {
  failure = error;
  if (client) {
    results.external_requests = client.externalRequests;
    results.blocked_external_requests = client.blockedExternalRequests;
    results.external_websocket_requests = client.externalWebSocketRequests;
    results.unexpected_scheme_requests = client.unexpectedSchemeRequests;
    results.target_summary = client.targetSummary();
  }
  results.error = String(error?.stack || error);
}

const resultPath = path.join(EVIDENCE, `${PHASE}-browser-results.json`);
fs.writeFileSync(resultPath, `${JSON.stringify(results, null, 2)}\n`, {mode: 0o600, flag: "wx"});
if (client) await client.closeBrowser();
if (failure) throw failure;
console.log(JSON.stringify({phase: PHASE, assertionCount: results.assertions.length, passed: true}));

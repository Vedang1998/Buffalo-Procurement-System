#!/usr/bin/env node
// TEST DATA — NOT FOR ORDERING: real staging-gateway Chromium phase.

import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import {fileURLToPath} from "node:url";

const CONTRACT = "BUFFALO_STAGING_PURCHASING_BROWSER_PHASE_V1";
const LOCAL_TLS_ORIGIN = "https://staging.example.test";
const EXPECTED_ASSERTION_COUNT = 62;
const EXPECTED_ASSERTION_MANIFEST_SHA256 =
  "64a5063520cbe378502b7930f8b51ba784b05c0e9c2ea986de9adeda991efb50";
const EXPECTED_RAW_BYTES = 1590;
const EXPECTED_RAW_SHA256 = "00071443ea8c54b57fc6014c3b1daf204081714a2ff09b98bed6c56a0dd3862c";
const SESSION_COOKIE = "__Host-buffalo_staging_session";
const CSRF_COOKIE = "__Host-buffalo_staging_csrf";
const LOGIN_COOKIE = "__Host-buffalo_staging_login";
const INJECTED_SELF_SHA256 = globalThis.__BUFFALO_STAGING_BROWSER_DRIVER_SHA256__;
const SELF_PATH = INJECTED_SELF_SHA256 ? null : fileURLToPath(import.meta.url);
const SELF_SHA256 = INJECTED_SELF_SHA256 ||
  crypto.createHash("sha256").update(fs.readFileSync(SELF_PATH)).digest("hex");
const GUARDED_TARGET_TYPES = new Set([
  "page", "iframe", "worker", "shared_worker", "service_worker", "webview", "background_page", "browser_ui",
]);
const FETCH_TARGET_TYPES = new Set(["page", "iframe", "webview", "background_page", "browser_ui"]);
const WORKER_TARGET_TYPES = new Set(["worker", "shared_worker", "service_worker"]);
const INERT_TARGET_TYPES = new Set(["browser", "tab"]);
const BUILTIN_EXTENSION_ID = "nkeimhogjdpnpccoofpliimaahmaaome";
const BUILTIN_EXTENSION_ORIGIN = `chrome-extension://${BUILTIN_EXTENSION_ID}`;
const EXPECTED_INITIAL_TARGET_INVENTORY = [
  ["background_page", `${BUILTIN_EXTENSION_ORIGIN}/background.html`],
  ["background_page", `${BUILTIN_EXTENSION_ORIGIN}/background.html`],
  ["browser_ui", "chrome://omnibox-popup.top-chrome/"],
  ["browser_ui", "chrome://omnibox-popup.top-chrome/omnibox_popup_aim.html"],
  ["page", "about:blank"],
];
const HOST_RESOLVER_RULES = "--host-resolver-rules=MAP staging.example.test 127.0.0.1, MAP * 0.0.0.0";
const REQUIRED_BROWSER_ARGUMENTS = new Set([
  "--disable-background-networking",
  "--disable-component-update",
  "--disable-default-apps",
  "--disable-extensions",
  "--disable-gpu",
  "--disable-sync",
  "--dns-prefetch-disable",
  "--enable-automation",
  "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
  "--headless=new",
  HOST_RESOLVER_RULES,
  "--incognito",
  "--metrics-recording-only",
  "--no-default-browser-check",
  "--no-first-run",
  "--proxy-bypass-list=*",
  "--proxy-server=direct://",
]);
const EGRESS_GUARD_BINDING = "__buffaloStagingBrowserEgressAttempt";
const EGRESS_GUARD_SOURCE = `(() => {
  const report = globalThis[${JSON.stringify(EGRESS_GUARD_BINDING)}];
  if (typeof report !== "function") return false;
  const BlockedWebSocket = function(rawUrl) {
    report(JSON.stringify({kind: "websocket", value: String(rawUrl)}));
    throw new Error("WebSocket is disabled by the staging browser audit");
  };
  Object.defineProperties(BlockedWebSocket, {
    CONNECTING: {value: 0}, OPEN: {value: 1}, CLOSING: {value: 2}, CLOSED: {value: 3}
  });
  Object.defineProperty(globalThis, "WebSocket", {
    value: BlockedWebSocket, configurable: false, writable: false
  });
  const blockedConstructor = (kind) => function(...args) {
    report(JSON.stringify({kind, value: String(args[0] ?? "")}));
    throw new Error(kind + " is disabled by the staging browser audit");
  };
  const blocked = {
    RTCPeerConnection: blockedConstructor("webrtc"),
    webkitRTCPeerConnection: blockedConstructor("webrtc"),
    WebTransport: blockedConstructor("webtransport"),
    TCPSocket: blockedConstructor("direct-tcp"),
    UDPSocket: blockedConstructor("direct-udp"),
  };
  for (const [name, value] of Object.entries(blocked)) {
    Object.defineProperty(globalThis, name, {value, configurable: false, writable: false});
  }
  return globalThis.WebSocket === BlockedWebSocket &&
    Object.entries(blocked).every(([name, value]) => globalThis[name] === value);
})()`;
const sleep = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));
const CONFIRMATION_REQUEST_FIELDS = [
  "_buffalo_staging_csrf",
  "actor",
  "confirm",
  "confirmation_idempotency_key",
  "expected_preview_sha256",
  "warning_review_reason",
];

function confirmationRequestMatches(request, batchId, expected) {
  try {
    const requested = new URL(request.url);
    if (
      request.method !== "POST" || requested.origin !== LOCAL_TLS_ORIGIN ||
      requested.pathname !== `/price-books/${batchId}/confirm` || requested.search ||
      typeof request.postData !== "string" || !expected
    ) return false;
    const fields = new URLSearchParams(request.postData);
    const names = [...fields.keys()].sort();
    if (
      JSON.stringify(names) !== JSON.stringify(CONFIRMATION_REQUEST_FIELDS) ||
      CONFIRMATION_REQUEST_FIELDS.some((name) => fields.getAll(name).length !== 1)
    ) return false;
    const csrf = fields.get("_buffalo_staging_csrf");
    return /^[A-Za-z0-9_-]{43}$/.test(csrf || "") &&
      crypto.createHash("sha256").update(Buffer.from(csrf, "ascii")).digest("hex") === expected.csrfSha256 &&
      fields.get("actor") === expected.actor &&
      fields.get("confirm") === "CONFIRM" &&
      fields.get("confirmation_idempotency_key") === expected.idempotencyKey &&
      fields.get("expected_preview_sha256") === expected.previewSha256 &&
      fields.get("warning_review_reason") === expected.warningReason;
  } catch {
    return false;
  }
}

if (process.argv[2] === "--self-test-confirmation-request") {
  if (process.argv.length !== 6) process.exit(64);
  try {
    const body = Buffer.from(process.argv[4], "base64url").toString("utf8");
    const expected = JSON.parse(Buffer.from(process.argv[5], "base64url").toString("utf8"));
    const matches = confirmationRequestMatches({
      method: "POST",
      postData: body,
      url: `${LOCAL_TLS_ORIGIN}/price-books/${process.argv[3]}/confirm`,
    }, process.argv[3], expected);
    process.stdout.write(matches ? "MATCH\n" : "DIFF\n");
    process.exit(matches ? 0 : 1);
  } catch {
    process.exit(64);
  }
}

const [
  phase, BASE, CDP, EVIDENCE, PROOF_PATH, BATCH_ID, EXPECTED_COMMIT, EXPECTED_TREE,
  EXPECTED_DRIVER_SHA256, EXPECTED_NODE_SHA256, EXPECTED_NODE_VERSION,
  OPERATOR_PROOF_SHA256, EXPECTED_TLS_CERTIFICATE_SHA256, EXPECTED_BROWSER_PID_TEXT,
  EXPECTED_BROWSER_START_TIME, SECRET_FD_TEXT,
] =
  process.argv.slice(2);

if (
  phase !== "price-confirm" ||
  BASE !== LOCAL_TLS_ORIGIN ||
  !/^http:\/\/127\.0\.0\.1:[1-9][0-9]{0,4}$/.test(CDP || "") ||
  !EVIDENCE ||
  !PROOF_PATH ||
  path.dirname(PROOF_PATH) !== EVIDENCE ||
  path.basename(PROOF_PATH) !== "staging-price-confirmation-proof.json" ||
  !/^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/.test(BATCH_ID || "") ||
  !/^[0-9a-f]{40}$/.test(EXPECTED_COMMIT || "") ||
  !/^[0-9a-f]{40}$/.test(EXPECTED_TREE || "") ||
  !/^[0-9a-f]{64}$/.test(EXPECTED_DRIVER_SHA256 || "") ||
  SELF_SHA256 !== EXPECTED_DRIVER_SHA256 ||
  !/^[0-9a-f]{64}$/.test(EXPECTED_NODE_SHA256 || "") ||
  process.version !== EXPECTED_NODE_VERSION ||
  !/^[0-9a-f]{64}$/.test(OPERATOR_PROOF_SHA256 || "") ||
  !/^[0-9a-f]{64}$/.test(EXPECTED_TLS_CERTIFICATE_SHA256 || "") ||
  !/^[1-9][0-9]*$/.test(EXPECTED_BROWSER_PID_TEXT || "") ||
  !/^[1-9][0-9]*$/.test(EXPECTED_BROWSER_START_TIME || "") ||
  !/^(?:[3-9]|[1-9][0-9]+)$/.test(SECRET_FD_TEXT || "")
) {
  throw new Error("staging browser arguments differ");
}

function readPassphrase(descriptor) {
  const info = fs.fstatSync(descriptor);
  const endpoint = fs.readlinkSync(`/proc/self/fd/${descriptor}`);
  const fdinfo = fs.readFileSync(`/proc/self/fdinfo/${descriptor}`, "ascii");
  const flagsMatch = /^flags:\s+([0-7]+)$/m.exec(fdinfo);
  if (
    !info.isFIFO() || endpoint !== `pipe:[${info.ino}]` || !flagsMatch ||
    (Number.parseInt(flagsMatch[1], 8) & 3) !== 0
  ) {
    throw new Error("staging browser credential channel differs");
  }
  const value = Buffer.alloc(257);
  let observed = 0;
  try {
    while (observed <= 256) {
      const count = fs.readSync(descriptor, value, observed, Math.min(128, 257 - observed));
      if (count === 0) break;
      observed += count;
    }
  } finally {
    fs.closeSync(descriptor);
  }
  try {
    const bytes = value.subarray(0, observed);
    const canonical = observed === 43 && [...bytes].every((item) =>
      (item >= 0x41 && item <= 0x5a) ||
      (item >= 0x61 && item <= 0x7a) ||
      (item >= 0x30 && item <= 0x39) || item === 0x5f || item === 0x2d);
    if (!canonical) {
      throw new Error("staging browser credential differs");
    }
    return bytes.toString("ascii");
  } finally {
    value.fill(0);
  }
}

let ownerPassphrase = readPassphrase(Number(SECRET_FD_TEXT));

class CdpClient {
  constructor(url, allowedOrigin) {
    this.socket = new WebSocket(url);
    this.allowedOrigin = allowedOrigin;
    this.nextId = 1;
    this.pending = new Map();
    this.waiters = new Map();
    this.inflight = new Map();
    this.sessions = new Map();
    this.targets = new Map();
    this.guardTasks = new Set();
    this.mainSessionId = null;
    this.discoveryEnabled = false;
    this.autoAttachEnabled = false;
    this.browserVersion = null;
    this.browserProcessId = null;
    this.browserArguments = [];
    this.baselineTargets = null;
    this.responses = [];
    this.requests = [];
    this.confirmationRequests = [];
    this.expectedConfirmationRequest = null;
    this.redirects = [];
    this.blockedRequests = [];
    this.externalRealtimeRequests = [];
    this.unexpectedSchemeRequests = [];
    this.exceptions = [];
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
      for (const pending of this.pending.values()) pending.reject(new Error("Chromium CDP socket closed"));
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
      this.eventErrors.push(`malformed CDP event: ${String(error)}`);
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
    if (message.method === "Target.targetCreated") this.recordTarget(params.targetInfo || {}, "created");
    if (message.method === "Target.targetInfoChanged") this.recordTarget(params.targetInfo || {}, "changed");
    if (message.method === "Target.targetDestroyed") this.destroyTarget(params.targetId);
    if (message.method === "Target.detachedFromTarget") this.detachTarget(params.targetId, params.sessionId);
    if (message.method === "Target.attachedToTarget") {
      this.trackTask(this.guardTarget(params), "target guard");
    }
    if (message.method === "Fetch.requestPaused") {
      this.trackTask(this.handlePausedRequest(sessionId, params), "request guard");
    }
    if (message.method === "Fetch.authRequired") {
      this.trackTask(
        this.send("Fetch.continueWithAuth", {
          requestId: params.requestId,
          authChallengeResponse: {response: "CancelAuth"},
        }, sessionId),
        "auth challenge guard",
      );
    }
    if (message.method === "Network.requestWillBeSent") this.recordRequest(sessionId, params);
    if (message.method === "Network.responseReceived") this.recordResponse(sessionId, params);
    if (message.method === "Network.webSocketCreated") {
      this.externalRealtimeRequests.push({
        targetType: this.sessions.get(sessionId)?.type || "unknown",
        scheme: "websocket",
      });
    }
    if (message.method === "Runtime.bindingCalled" && params.name === EGRESS_GUARD_BINDING) {
      let reported = {kind: "realtime", value: "malformed"};
      try {
        const parsed = JSON.parse(params.payload);
        if (typeof parsed.kind === "string" && typeof parsed.value === "string") reported = parsed;
      } catch {}
      this.externalRealtimeRequests.push({
        targetType: this.sessions.get(sessionId)?.type || "unknown",
        scheme: reported.kind,
      });
      this.blockedRequests.push({method: reported.kind.toUpperCase(), targetType: this.sessions.get(sessionId)?.type || "unknown"});
    }
    if (
      message.method === "Network.webTransportCreated" ||
      message.method === "Network.directTCPSocketCreated" ||
      message.method === "Network.directUDPSocketCreated"
    ) {
      this.unexpectedSchemeRequests.push({
        targetType: this.sessions.get(sessionId)?.type || "unknown",
        scheme: message.method,
      });
    }
    if (message.method === "Runtime.exceptionThrown") {
      this.exceptions.push(params.exceptionDetails?.text || "exception");
    }
    if (message.method === "Runtime.consoleAPICalled" && ["error", "assert"].includes(params.type)) {
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

  trackTask(task, label) {
    this.guardTasks.add(task);
    void task
      .catch((error) => this.eventErrors.push(`${label} failed: ${String(error)}`))
      .finally(() => this.guardTasks.delete(task));
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

  waitEvent(method, sessionId = null, timeoutMs = 20000) {
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

  ensureTarget(targetInfo) {
    if (!targetInfo.targetId) return null;
    let target = this.targets.get(targetInfo.targetId);
    if (!target) {
      target = {
        targetId: targetInfo.targetId,
        type: targetInfo.type || "unknown",
        url: targetInfo.url || "",
        created: false,
        attached: false,
        guarded: false,
        resumed: false,
        destroyed: false,
        activeSessions: new Set(),
      };
      this.targets.set(targetInfo.targetId, target);
    } else if (targetInfo.type) {
      target.type = targetInfo.type;
    }
    if (typeof targetInfo.url === "string") target.url = targetInfo.url;
    return target;
  }

  recordTarget(targetInfo, event) {
    const target = this.ensureTarget(targetInfo);
    if (!target) {
      this.eventErrors.push(`target ${event} identity is incomplete`);
      return;
    }
    if (event === "created") target.created = true;
    if (!GUARDED_TARGET_TYPES.has(target.type) && !INERT_TARGET_TYPES.has(target.type)) {
      this.eventErrors.push(`unsupported target type: ${target.type}`);
    }
  }

  destroyTarget(targetId) {
    const target = this.targets.get(targetId);
    if (!target) {
      this.eventErrors.push("destroyed target identity is unavailable");
      return;
    }
    target.destroyed = true;
    target.activeSessions.clear();
    if (this.baselineTargets === null) {
      for (const [sessionId, session] of this.sessions) {
        if (session.targetId === targetId) this.sessions.delete(sessionId);
      }
      this.targets.delete(targetId);
    }
  }

  detachTarget(targetId, sessionId) {
    const session = this.sessions.get(sessionId);
    const target = this.targets.get(targetId || session?.targetId);
    if (!target) {
      this.eventErrors.push("detached target identity is unavailable");
      return;
    }
    target.activeSessions.delete(sessionId);
    if (session) session.detached = true;
  }

  async guardTarget(params) {
    const sessionId = params.sessionId;
    const targetInfo = params.targetInfo || {};
    const target = this.ensureTarget(targetInfo);
    if (!sessionId || !target || this.sessions.has(sessionId)) {
      throw new Error("attached target identity differs");
    }
    target.attached = true;
    target.activeSessions.add(sessionId);
    const session = {
      targetId: target.targetId,
      type: target.type,
      guarded: false,
      resumed: false,
      detached: false,
    };
    this.sessions.set(sessionId, session);
    if (!GUARDED_TARGET_TYPES.has(session.type)) {
      await this.send("Target.closeTarget", {targetId: target.targetId});
      throw new Error(`unexpected attached target type: ${session.type}`);
    }
    await this.send("Network.enable", {}, sessionId);
    const blockedSchemes = WORKER_TARGET_TYPES.has(session.type)
      ? ["http", "https", "ws", "wss", "ftp"]
      : ["ws", "wss", "ftp"];
    await this.send("Network.setBlockedURLs", {
      urlPatterns: blockedSchemes.map((scheme) => ({urlPattern: `${scheme}://*:*/*`, block: true})),
    }, sessionId);
    if (FETCH_TARGET_TYPES.has(session.type)) {
      await this.send("Fetch.enable", {
        patterns: [{urlPattern: "*", requestStage: "Request"}],
        handleAuthRequests: true,
      }, sessionId);
    }
    await this.send("Runtime.enable", {}, sessionId);
    await this.send("Runtime.addBinding", {name: EGRESS_GUARD_BINDING}, sessionId);
    if (FETCH_TARGET_TYPES.has(session.type)) {
      await this.send("Page.addScriptToEvaluateOnNewDocument", {source: EGRESS_GUARD_SOURCE}, sessionId);
    }
    const installed = await this.send("Runtime.evaluate", {
      expression: EGRESS_GUARD_SOURCE,
      returnByValue: true,
    }, sessionId);
    if (installed.exceptionDetails || installed.result?.value !== true) {
      throw new Error("socket guard installation failed");
    }
    if (FETCH_TARGET_TYPES.has(session.type)) await this.send("Page.enable", {}, sessionId);
    await this.send("Target.setAutoAttach", {
      autoAttach: true,
      waitForDebuggerOnStart: true,
      flatten: true,
    }, sessionId);
    session.guarded = true;
    target.guarded = true;
    if (session.type === "page" && this.mainSessionId === null) this.mainSessionId = sessionId;
    await this.send("Runtime.runIfWaitingForDebugger", {}, sessionId);
    session.resumed = true;
    target.resumed = true;
  }

  async handlePausedRequest(sessionId, params) {
    const request = params.request || {};
    let target = null;
    try {
      target = new URL(request.url);
    } catch {
      this.unexpectedSchemeRequests.push({targetType: this.sessions.get(sessionId)?.type || "unknown", scheme: "unparseable"});
    }
    const targetType = this.sessions.get(sessionId)?.type || "unknown";
    const allowedInternal = this.allowedInternalRequest(targetType, target);
    const allowed = target && (
      (["http:", "https:"].includes(target.protocol) && target.origin === this.allowedOrigin) ||
      allowedInternal
    );
    if (!allowed) {
      const record = {
        method: request.method || null,
        targetType,
        scheme: target?.protocol || "unparseable",
        origin: target?.origin || null,
      };
      this.blockedRequests.push(record);
      if (target && ["ws:", "wss:"].includes(target.protocol)) this.externalRealtimeRequests.push(record);
      if (target && !allowedInternal && !["http:", "https:", "ws:", "wss:"].includes(target.protocol)) {
        this.unexpectedSchemeRequests.push(record);
      }
      await this.send("Fetch.failRequest", {requestId: params.requestId, errorReason: "BlockedByClient"}, sessionId);
      return;
    }
    await this.send("Fetch.continueRequest", {requestId: params.requestId}, sessionId);
  }

  recordRequest(sessionId, params) {
    const request = params.request || {};
    let requested = null;
    try {
      requested = new URL(request.url);
    } catch {
      this.unexpectedSchemeRequests.push({
        targetType: this.sessions.get(sessionId)?.type || "unknown",
        scheme: "unparseable",
      });
    }
    const targetType = this.sessions.get(sessionId)?.type || "unknown";
    const allowedInternal = this.allowedInternalRequest(targetType, requested);
    this.requests.push({
      method: request.method || null,
      origin: requested?.origin || null,
      pathname: requested?.pathname || null,
      query: requested?.search || null,
      targetType,
    });
    if (
      request.method === "POST" &&
      requested?.origin === this.allowedOrigin &&
      requested.pathname === `/price-books/${BATCH_ID}/confirm`
    ) {
      this.confirmationRequests.push({
        matches: confirmationRequestMatches(
          request,
          BATCH_ID,
          this.expectedConfirmationRequest,
        ),
      });
    }
    if (
      requested &&
      (
        !["http:", "https:"].includes(requested.protocol) ||
        requested.origin !== this.allowedOrigin ||
        WORKER_TARGET_TYPES.has(targetType)
      ) && !allowedInternal
    ) {
      this.blockedRequests.push({
        method: request.method || null,
        targetType,
        scheme: requested.protocol,
        origin: requested.origin,
      });
    }
    const key = `${sessionId || "browser"}:${params.requestId}`;
    const previous = this.inflight.get(key);
    if (params.redirectResponse && previous) {
      const headers = Object.fromEntries(
        Object.entries(params.redirectResponse.headers || {}).map(([name, value]) => [name.toLowerCase(), value]),
      );
      this.redirects.push({
        method: previous.method,
        pathname: new URL(previous.url).pathname,
        status: params.redirectResponse.status,
        location: headers.location || null,
      });
    }
    this.inflight.set(key, {url: request.url, method: request.method});
  }

  allowedInternalRequest(targetType, requested) {
    if (!requested) return false;
    if (targetType === "browser_ui") return ["chrome:", "data:"].includes(requested.protocol);
    if (targetType === "background_page") {
      return requested.protocol === "data:" ||
        (requested.protocol === "chrome-extension:" && requested.host === BUILTIN_EXTENSION_ID);
    }
    return false;
  }

  recordResponse(sessionId, params) {
    const response = params.response || {};
    let target;
    try {
      target = new URL(response.url);
    } catch {
      return;
    }
    if (!["http:", "https:"].includes(target.protocol)) return;
    const key = `${sessionId || "browser"}:${params.requestId}`;
    const headers = Object.fromEntries(
      Object.entries(response.headers || {}).map(([name, value]) => [name.toLowerCase(), value]),
    );
    this.responses.push({
      method: this.inflight.get(key)?.method || null,
      pathname: target.pathname,
      status: response.status,
      cacheControl: headers["cache-control"] || null,
      contentDisposition: headers["content-disposition"] || null,
      contentType: headers["content-type"] || null,
      securityState: response.securityState || null,
      securityProtocol: response.securityDetails?.protocol || null,
      strictTransportSecurity: headers["strict-transport-security"] || null,
    });
  }

  async flushGuardTasks() {
    while (this.guardTasks.size) await Promise.all([...this.guardTasks]);
  }

  async initialize() {
    const version = await this.send("Browser.getVersion");
    this.browserVersion = {
      product: version.product,
      protocolVersion: version.protocolVersion,
      jsVersion: version.jsVersion,
    };
    const processInfo = await this.send("SystemInfo.getProcessInfo");
    const browserProcess = (processInfo.processInfo || []).find((item) => item.type === "browser");
    this.browserProcessId = browserProcess?.id || null;
    const commandLine = await this.send("Browser.getBrowserCommandLine");
    this.browserArguments = commandLine.arguments || [];
    await this.send("Target.setDiscoverTargets", {discover: true});
    this.discoveryEnabled = true;
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
      const settled = main?.guarded && main?.resumed && this.currentTargetsGuarded();
      if (settled) {
        const first = this.targetIdentity();
        await sleep(1000);
        await this.flushGuardTasks();
        if (this.currentTargetsGuarded() && this.targetIdentity() === first) {
          this.captureTargetBaseline();
          return;
        }
      }
      await sleep(25);
    }
    throw new Error("no guarded Chromium page target became available");
  }

  targetIdentity() {
    return JSON.stringify([...this.targets.values()]
      .map((item) => [item.targetId, item.type])
      .sort((left, right) => left[0].localeCompare(right[0])));
  }

  currentTargetsGuarded() {
    const values = [...this.targets.values()];
    const guarded = values.filter((item) => GUARDED_TARGET_TYPES.has(item.type));
    return values.length > 0 && guarded.length > 0 &&
      values.every((item) => GUARDED_TARGET_TYPES.has(item.type) || INERT_TARGET_TYPES.has(item.type)) &&
      guarded.every((item) => !item.destroyed && item.attached && item.guarded &&
        item.resumed && item.activeSessions.size === 1) &&
      [...this.sessions.values()].every((session) => session.guarded && session.resumed && !session.detached);
  }

  captureTargetBaseline() {
    const values = [...this.targets.values()];
    const inventory = values.map((item) => [item.type, item.url]).sort((left, right) =>
      JSON.stringify(left).localeCompare(JSON.stringify(right)));
    if (
      JSON.stringify(inventory) !== JSON.stringify(EXPECTED_INITIAL_TARGET_INVENTORY) ||
      !this.currentTargetsGuarded()
    ) {
      throw new Error("fresh Chromium target inventory differs");
    }
    this.baselineTargets = new Map(values.map((item) => [item.targetId, {
      type: item.type,
      url: item.type === "page" ? null : item.url,
    }]));
  }

  targetSummary() {
    const values = [...this.targets.values()];
    const guarded = values.filter((item) => GUARDED_TARGET_TYPES.has(item.type));
    const liveDetached = [...this.sessions.values()].filter((session) => {
      const target = this.targets.get(session.targetId);
      return session.detached && target && !target.destroyed;
    }).length;
    return {
      active_guarded: guarded.filter((item) => !item.destroyed && item.activeSessions.size > 0).length,
      guarded: guarded.length,
      inert: values.filter((item) => INERT_TARGET_TYPES.has(item.type)).length,
      live_detached: liveDetached,
      tracked: values.length,
      unattached: guarded.filter((item) => !item.attached).length,
      unguarded: guarded.filter((item) => !item.guarded).length,
      unresumed: guarded.filter((item) => !item.resumed).length,
      unsupported: values.filter((item) => !GUARDED_TARGET_TYPES.has(item.type) && !INERT_TARGET_TYPES.has(item.type)).length,
    };
  }

  allTargetsGuarded() {
    const summary = this.targetSummary();
    const baselineMatches = this.baselineTargets instanceof Map &&
      this.baselineTargets.size === this.targets.size &&
      [...this.baselineTargets].every(([targetId, expected]) => {
        const current = this.targets.get(targetId);
        return current?.type === expected.type && !current.destroyed &&
          (expected.url === null || current.url === expected.url);
      });
    return this.discoveryEnabled && this.autoAttachEnabled && baselineMatches &&
      summary.guarded >= 1 && summary.active_guarded === summary.guarded &&
      summary.live_detached === 0 &&
      summary.tracked === summary.guarded + summary.inert &&
      summary.unattached === 0 && summary.unguarded === 0 &&
      summary.unresumed === 0 && summary.unsupported === 0 &&
      this.currentTargetsGuarded() && this.sessions.size > 0 && [...this.sessions.values()].every(
        (session) => GUARDED_TARGET_TYPES.has(session.type) && session.guarded &&
          session.resumed && !session.detached,
      );
  }

  async terminateMainTarget() {
    const session = this.sessions.get(this.mainSessionId);
    const target = session ? this.targets.get(session.targetId) : null;
    if (!session || !target || target.destroyed) return false;
    await this.send("Target.setDiscoverTargets", {discover: false});
    this.discoveryEnabled = false;
    const detached = this.waitEvent("Target.detachedFromTarget", null, 10000);
    const closed = await this.send("Target.closeTarget", {targetId: target.targetId});
    const event = await detached;
    await this.flushGuardTasks();
    await sleep(100);
    const terminalTargets = await this.send("Target.getTargets");
    const inventory = (terminalTargets.targetInfos || [])
      .map((item) => [item.type, item.url])
      .sort((left, right) => JSON.stringify(left).localeCompare(JSON.stringify(right)));
    return closed.success === true && event.targetId === target.targetId &&
      event.sessionId === this.mainSessionId && target.activeSessions.size === 0 &&
      !(terminalTargets.targetInfos || []).some((item) => item.targetId === target.targetId) &&
      JSON.stringify(inventory) === JSON.stringify([
        ["background_page", `${BUILTIN_EXTENSION_ORIGIN}/background.html`],
      ]);
  }

  close() {
    this.socket.close();
  }
}

const assertions = [];
function check(condition, name, detail = null) {
  if (assertions.includes(name)) throw new Error(`duplicate assertion identifier: ${name}`);
  if (!condition) throw new Error(`ASSERTION FAILED: ${name}: ${JSON.stringify(detail)}`);
  assertions.push(name);
}

let proofDescriptor = null;
let screenshotDescriptor = null;
const screenshotPath = path.join(EVIDENCE, "staging-price-confirmed-test-data.png");
const downloadsPath = path.join(EVIDENCE, "downloads");

function reserveEvidence() {
  const existing = fs.readdirSync(EVIDENCE);
  if (existing.length !== 1 || existing[0] !== "staging-price-confirmation-browser.log") {
    throw new Error("staging browser evidence root is not fresh");
  }
  proofDescriptor = fs.openSync(PROOF_PATH, "wx", 0o600);
  screenshotDescriptor = fs.openSync(screenshotPath, "wx", 0o600);
  fs.mkdirSync(downloadsPath, {mode: 0o700});
}

function writeProof(value) {
  if (proofDescriptor === null) throw new Error("staging browser proof was not reserved");
  fs.ftruncateSync(proofDescriptor, 0);
  fs.writeFileSync(proofDescriptor, JSON.stringify(value, null, 2) + "\n", "utf8");
  fs.fsyncSync(proofDescriptor);
  fs.closeSync(proofDescriptor);
  proofDescriptor = null;
}

async function run() {
  reserveEvidence();
  const versionResponse = await fetch(`${CDP}/json/version`, {redirect: "error"});
  const versionText = await versionResponse.text();
  if (
    versionResponse.status !== 200 ||
    versionResponse.url !== `${CDP}/json/version` ||
    Buffer.byteLength(versionText, "utf8") > 16 * 1024
  ) {
    throw new Error("Chromium CDP discovery differs");
  }
  const version = JSON.parse(versionText);
  const websocket = new URL(version.webSocketDebuggerUrl);
  const cdp = new URL(CDP);
  if (
    websocket.protocol !== "ws:" ||
    websocket.hostname !== "127.0.0.1" ||
    websocket.port !== cdp.port ||
    websocket.username || websocket.password || websocket.search || websocket.hash ||
    !/^\/devtools\/browser\/[A-Za-z0-9_-]{16,128}$/.test(websocket.pathname)
  ) {
    throw new Error("Chromium CDP websocket identity differs");
  }
  const client = new CdpClient(websocket.href, new URL(BASE).origin);
  await client.open();
  try {
    await client.initialize();
    const sessionId = client.mainSessionId;
    const exactBrowserArgument = (prefix, expected) =>
      client.browserArguments.filter((item) => item.startsWith(prefix)).length === 1 &&
      client.browserArguments.filter((item) => item.startsWith(prefix))[0] === expected;
    check(
      client.browserProcessId === Number(EXPECTED_BROWSER_PID_TEXT),
      "Chromium browser PID is bound to the owned process",
    );
    check(
      (() => {
        const profileArguments = client.browserArguments.filter((item) => item.startsWith("--user-data-dir="));
        if (profileArguments.length !== 1) return false;
        const expected = new Set([
          ...REQUIRED_BROWSER_ARGUMENTS,
          "--remote-debugging-address=127.0.0.1",
          `--remote-debugging-port=${cdp.port}`,
          profileArguments[0],
          "about:blank",
        ]);
        const actual = client.browserArguments.slice(1);
        return actual.length === expected.size && actual.every((item) => expected.has(item));
      })() &&
        exactBrowserArgument("--remote-debugging-address=", "--remote-debugging-address=127.0.0.1") &&
        exactBrowserArgument("--remote-debugging-port=", `--remote-debugging-port=${cdp.port}`) &&
        exactBrowserArgument("--host-resolver-rules=", HOST_RESOLVER_RULES) &&
        exactBrowserArgument("--proxy-server=", "--proxy-server=direct://") &&
        exactBrowserArgument("--proxy-bypass-list=", "--proxy-bypass-list=*") &&
        client.browserArguments.filter((item) => item.startsWith("--user-data-dir=")).length === 1 &&
        !client.browserArguments.some((item) =>
          item === "--proxy-auto-detect" || item === "--no-proxy-server" ||
          item.startsWith("--proxy-pac-url=")),
      "Chromium command line binds CDP, DNS, proxy, profile, and realtime egress locally",
    );
    let tlsCertificateSha256 = null;
    await client.send("Browser.setDownloadBehavior", {
      behavior: "allow",
      downloadPath: downloadsPath,
      eventsEnabled: true,
    });

    const evaluate = async (expression) => {
      const response = await client.send("Runtime.evaluate", {
        expression,
        returnByValue: true,
        awaitPromise: true,
      }, sessionId);
      if (response.exceptionDetails) {
        throw new Error(response.exceptionDetails.exception?.description || "browser evaluation failed");
      }
      return response.result.value;
    };
    const navigate = async (url) => {
      const loaded = client.waitEvent("Page.loadEventFired", sessionId);
      await client.send("Page.navigate", {url}, sessionId);
      await loaded;
      await sleep(120);
    };
    const submit = async (expression) => {
      const loaded = client.waitEvent("Page.loadEventFired", sessionId);
      try {
        await evaluate(expression);
      } catch (error) {
        if (!/context|navigat|destroyed/i.test(String(error))) throw error;
      }
      await loaded;
      await sleep(150);
    };
    const body = () => evaluate("document.body.innerText");
    const currentUrl = () => evaluate("location.href");
    const mark = () => ({responses: client.responses.length, redirects: client.redirects.length});
    const responseAfter = (pathname, method, ledger) =>
      client.responses.slice(ledger.responses).find((item) => item.pathname === pathname && item.method === method);
    const redirectAfter = (pathname, method, ledger) =>
      client.redirects.slice(ledger.redirects).find((item) => item.pathname === pathname && item.method === method);
    const formScript = (selector, values = {}) => `(() => {
      const form = ${selector};
      if (!form) throw new Error("required form is absent");
      const values = ${JSON.stringify(values)};
      for (const [name, value] of Object.entries(values)) {
        const field = form.elements.namedItem(name);
        if (!field) throw new Error("required form field is absent");
        field.value = value;
      }
      form.requestSubmit();
      return true;
    })()`;

    let ledger = mark();
    await navigate(`${BASE}/health`);
    const healthResponse = responseAfter("/health", "GET", ledger);
    check(healthResponse?.status === 200, "public health returns HTTP 200");
    const certificate = await client.send("Network.getCertificate", {origin: BASE}, sessionId);
    tlsCertificateSha256 = Array.isArray(certificate.tableNames) && certificate.tableNames.length
      ? crypto.createHash("sha256").update(Buffer.from(certificate.tableNames[0], "base64")).digest("hex")
      : null;
    check(
      tlsCertificateSha256 === EXPECTED_TLS_CERTIFICATE_SHA256,
      "Chromium TLS certificate identity is exact after guarded navigation",
    );
    check(
      healthResponse?.cacheControl === "no-store" &&
        healthResponse?.strictTransportSecurity === "max-age=31536000" &&
        String(healthResponse?.contentType || "").split(";", 1)[0] === "application/json" &&
        healthResponse?.securityState === "secure" &&
        ["TLS 1.2", "TLS 1.3"].includes(healthResponse?.securityProtocol),
      "public health has exact no-store and transport security headers",
    );
    check(
      JSON.stringify(JSON.parse(await body())) ===
        JSON.stringify({ok: true, service: "buffalo-procurement-staging-gateway"}),
      "public health body is minimal and exact",
    );

    ledger = mark();
    await navigate(`${BASE}/price-books`);
    check(responseAfter("/price-books", "GET", ledger)?.status === 401, "unauthenticated price list is HTTP 401");
    check((await body()).includes("Unauthorized"), "unauthenticated price list is refused");

    ledger = mark();
    await navigate(`${BASE}/auth/login`);
    check(responseAfter("/auth/login", "GET", ledger)?.status === 200, "login challenge returns HTTP 200");
    check(
      await evaluate(`(() => {
        const form = document.querySelector('form[action="/auth/login"]');
        return Boolean(form && form.elements.namedItem("csrf_token") && form.elements.namedItem("passphrase"));
      })()`),
      "login exposes challenge-bound passphrase form",
    );
    ledger = mark();
    await submit(formScript("document.querySelector('form[action=\"/auth/login\"]')", {passphrase: ownerPassphrase}));
    ownerPassphrase = "";
    check((await currentUrl()) === `${BASE}/`, "login redirects to the exact staging root");
    const loginRedirect = redirectAfter("/auth/login", "POST", ledger);
    check(loginRedirect?.status === 303 && new URL(loginRedirect.location, BASE).pathname === "/", "login POST returns exact HTTP 303");
    const cookies = (await client.send("Network.getAllCookies", {}, sessionId)).cookies;
    const session = cookies.filter((cookie) => cookie.name === SESSION_COOKIE);
    const csrf = cookies.filter((cookie) => cookie.name === CSRF_COOKIE);
    check(session.length === 1 && session[0].httpOnly && session[0].secure && session[0].sameSite === "Strict", "session cookie is Secure HttpOnly SameSite Strict");
    check(csrf.length === 1 && !csrf[0].httpOnly && csrf[0].secure && csrf[0].sameSite === "Strict", "CSRF cookie is Secure script-readable SameSite Strict");
    check(cookies.every((cookie) => cookie.name !== LOGIN_COOKIE), "one-time login challenge cookie is removed");
    check(!(await evaluate("document.cookie")).includes(SESSION_COOKIE), "session token is unavailable to page script");
    const authenticatedResponseStart = client.responses.length;

    ledger = mark();
    await navigate(`${BASE}/ready`);
    check(responseAfter("/ready", "GET", ledger)?.status === 200, "authenticated readiness returns HTTP 200");
    const readiness = JSON.parse(await evaluate("document.body.innerText"));
    check(readiness.ok === true && readiness.source_commit === EXPECTED_COMMIT, "readiness is source-bound");
    check(readiness.components?.synthetic === "ready", "synthetic worker is ready");

    await navigate(`${BASE}/price-books`);
    check(
      await evaluate(`(() => {
        return !document.querySelector('form[action$="/price-books/import"]') &&
          !document.querySelector('input[type="file"]') &&
          document.querySelector('#synthetic-price-input-authority')?.textContent.includes("operator-staged input, browser-approved workflow");
      })()`),
      "staging price list has no bulk-upload authority",
    );
    const listed = await evaluate(`(() => {
      const anchor = [...document.querySelectorAll('tbody a')].find((item) => new URL(item.href).pathname === "/price-books/${BATCH_ID}");
      if (!anchor) return null;
      const row = anchor.closest("tr");
      return {
        durable: row.querySelector('.price-book-durable-status')?.textContent.trim(),
        operational: row.querySelector('.price-book-operational-status')?.textContent.trim(),
        basis: row.querySelector('.price-book-operational-status')?.dataset.temporalBasis,
      };
    })()`);
    check(listed?.durable === "VALIDATED" && listed?.operational === "VALIDATED", "operator batch is VALIDATED in list");
    check(listed?.basis === "REGISTERED_OBSERVATION", "list uses registered observation");

    await navigate(`${BASE}/price-books/${BATCH_ID}`);
    const before = await evaluate(`({
      durable: document.querySelector('#price-book-durable-status')?.textContent.trim(),
      operational: document.querySelector('#price-book-operational-status')?.textContent.trim(),
      basis: document.querySelector('#price-book-operational-status')?.dataset.temporalBasis,
      tiers: [...document.querySelectorAll('#declared-price-evidence tbody tr')].map(
        (row) => [...row.querySelectorAll('td')].map((cell) => cell.textContent.trim()),
      ),
      hasConfirmation: Boolean(document.querySelector('form[action$="/confirmation-preview"]')),
      hasApply: Boolean(document.querySelector('form[action$="/apply-preview"]')),
      hasReviewToken: Boolean(document.querySelector('input[name="review_token"]')),
    })`);
    check(before.durable === "VALIDATED" && before.operational === "VALIDATED", "detail begins VALIDATED/VALIDATED");
    check(before.basis === "REGISTERED_OBSERVATION", "detail uses registered observation");
    check(
      JSON.stringify(before.tiers) === JSON.stringify([
        ["2", "SUP-001", "1001", "BASE", "—", "—", "36.0000", "6.0000"],
        ["3", "SUP-001", "1001", "BREAK", "2.0000", "CS", "30.0000", "5.0000"],
        ["4", "SUP-ALT", "1001", "BASE", "—", "—", "72.0000", "12.0000"],
        ["5", "SUP-ALT", "1001", "BREAK", "2.0000", "CS", "60.0000", "10.0000"],
      ]),
      "detail exposes the exact four registered tiers",
    );
    check(before.hasConfirmation && !before.hasApply, "only confirmation control is available before approval");
    check(!before.hasReviewToken, "gateway removes legacy price token input");
    ledger = mark();
    const downloadStarted = client.waitEvent("Browser.downloadWillBegin");
    await evaluate(`document.querySelector('a[href$="/raw.csv"]').click(); true`);
    const download = await downloadStarted;
    let completed = null;
    for (let attempt = 0; attempt < 100; attempt += 1) {
      const progress = await client.waitEvent("Browser.downloadProgress");
      if (progress.guid !== download.guid) continue;
      if (progress.state === "canceled") throw new Error("raw evidence download was canceled");
      if (progress.state === "completed") {
        completed = progress;
        break;
      }
    }
    check(completed !== null, "raw evidence download completes");
    const expectedRawFilename = `price-book-${BATCH_ID}.csv`;
    check(download.suggestedFilename === expectedRawFilename, "raw evidence filename is exact");
    const rawPath = path.join(downloadsPath, expectedRawFilename);
    check(
      path.dirname(path.resolve(rawPath)) === path.resolve(downloadsPath) &&
        (!completed.filePath || path.resolve(completed.filePath) === path.resolve(rawPath)),
      "raw evidence path remains inside the dedicated download directory",
    );
    const rawInfo = fs.lstatSync(rawPath);
    check(
      rawInfo.isFile() && !rawInfo.isSymbolicLink() && rawInfo.nlink === 1 &&
        rawInfo.uid === process.geteuid() && (rawInfo.mode & 0o022) === 0 &&
        rawInfo.size === EXPECTED_RAW_BYTES,
      "raw evidence inode is fresh and immutable to other users",
    );
    const rawData = fs.readFileSync(rawPath);
    const rawResponse = responseAfter(`/price-books/${BATCH_ID}/raw.csv`, "GET", ledger);
    const raw = {
      status: rawResponse?.status || null,
      bytes: rawData.length,
      sha256: crypto.createHash("sha256").update(rawData).digest("hex"),
    };
    rawData.fill(0);
    fs.unlinkSync(rawPath);
    fs.rmdirSync(downloadsPath);
    check(
      raw.status === 200 && raw.bytes === EXPECTED_RAW_BYTES && raw.sha256 === EXPECTED_RAW_SHA256 &&
        rawResponse?.contentDisposition === `attachment; filename=price-book-${BATCH_ID}.csv` &&
        String(rawResponse?.contentType || "").split(";", 1)[0] === "text/csv",
      "raw evidence has exact registered response and identity",
    );

    ledger = mark();
    await submit(formScript("document.querySelector('form[action$=\"/confirmation-preview\"]')", {
      confirmation_idempotency_key: "staging-browser-price-confirmation-v1",
      actor: "browser-supplied-actor-is-not-authority",
      warning_review_reason: "Reviewed exact operator-staged synthetic price changes.",
    }));
    check(responseAfter(`/price-books/${BATCH_ID}/confirmation-preview`, "POST", ledger)?.status === 200, "confirmation preview returns HTTP 200");
    const confirmation = await evaluate("JSON.parse(document.querySelector('#declared-price-confirmation-json').textContent)");
    check(confirmation.contract === "BUFFALO_SYNTHETIC_PRICE_CONFIRMATION_PREVIEW_V1", "confirmation preview contract is exact");
    check(confirmation.price_book_batch_id === BATCH_ID, "confirmation preview binds the operator batch");
    check(confirmation.observation_at === "2026-09-16T14:00:00+00:00", "confirmation uses registered observation instant");
    check(confirmation.application_at === "2026-10-01T14:00:00+00:00", "confirmation binds application instant");
    check(confirmation.monday_evaluation_at === "2026-10-05T14:00:00+00:00", "confirmation binds Monday instant");
    check(confirmation.policy_timezone === "America/New_York", "confirmation binds the registered policy timezone");
    check(confirmation.temporal_basis === "REGISTERED_OBSERVATION", "confirmation uses the registered temporal basis");
    check(confirmation.principal_ref === "owner:railway-staging:01", "confirmation uses the server-owned principal");
    check(confirmation.role_ref === "procurement.price.approve", "confirmation uses the routed price approval role");
    check(confirmation.raw_content_sha256 === EXPECTED_RAW_SHA256, "confirmation binds exact raw evidence");
    check(
      JSON.stringify(confirmation.candidate_tiers.map((tier) => [
        tier.source_row_number,
        tier.supplier_sku,
        tier.variant_id,
        tier.level_type,
        tier.break_quantity,
        tier.break_unit,
        tier.case_price,
        tier.unit_price,
      ])) === JSON.stringify([
        [2, "SUP-001", "1001", "BASE", null, null, "36.00", "6.00"],
        [3, "SUP-001", "1001", "BREAK", "2", "CS", "30.00", "5.00"],
        [4, "SUP-ALT", "1001", "BASE", null, null, "72.00", "12.00"],
        [5, "SUP-ALT", "1001", "BREAK", "2", "CS", "60.00", "10.00"],
      ]) &&
        confirmation.candidate_tiers.every((tier) =>
          tier.package_type === "STANDARD" &&
          tier.raw_pack === "6x750ML" &&
          tier.shopify_units_per_case === "6" &&
          tier.qualifying_units_per_case === "6" &&
          tier.extraction_confidence === "VERIFIED" &&
          tier.source_evidence === "fabricated exact replacement source") &&
        Array.isArray(confirmation.current_tiers) &&
        confirmation.current_tiers.length === 4 &&
        JSON.stringify(confirmation.current_tiers.map((tier) => tier.slice(0, 6))) === JSON.stringify([
          ["SUP-001", "BASE", null, null, "12.0000", "2.0000"],
          ["SUP-001", "BREAK", "2.0000", "CS", "9.5000", "1.5833"],
          ["SUP-ALT", "BASE", null, null, "10.0100", "1.6683"],
          ["SUP-ALT", "BREAK", "2.0000", "CS", "10.0000", "1.6667"],
        ]) &&
        confirmation.current_tiers.every((tier) => Number.isInteger(tier[6]) && tier[6] > 0),
      "confirmation previews the exact registered tiers and current comparison",
    );
    check(confirmation.commercial_authority === false && confirmation.real_price_approval === false, "confirmation grants no real authority");
    const confirmationForm = await evaluate(`(async () => {
        const form = document.querySelector('form[action$="/confirm"]');
        if (!form || form.elements.namedItem("review_token")) return {valid: false};
        const value = (name) => form.elements.namedItem(name)?.value;
        const csrf = value("_buffalo_staging_csrf");
        const digest = csrf ? await crypto.subtle.digest(
          "SHA-256", new TextEncoder().encode(csrf),
        ) : null;
        const csrfSha256 = digest ? [...new Uint8Array(digest)]
          .map((item) => item.toString(16).padStart(2, "0")).join("") : null;
        return {valid: Boolean(csrf) &&
          value("confirmation_idempotency_key") === "staging-browser-price-confirmation-v1" &&
          value("expected_preview_sha256") === ${JSON.stringify(confirmation.preview_sha256)} &&
          value("actor") === ${JSON.stringify(confirmation.principal_ref)} &&
          value("warning_review_reason") === "Reviewed exact operator-staged synthetic price changes." &&
          value("confirm") === "CONFIRM", csrfSha256};
      })()`);
    check(
      confirmationForm.valid && /^[0-9a-f]{64}$/.test(confirmationForm.csrfSha256 || ""),
      "confirmation form binds the reviewed preview and gateway CSRF without legacy token",
    );
    client.expectedConfirmationRequest = {
      actor: confirmation.principal_ref,
      csrfSha256: confirmationForm.csrfSha256,
      idempotencyKey: "staging-browser-price-confirmation-v1",
      previewSha256: confirmation.preview_sha256,
      warningReason: "Reviewed exact operator-staged synthetic price changes.",
    };
    ledger = mark();
    const confirmationRequestStart = client.confirmationRequests.length;
    await submit(formScript("document.querySelector('form[action$=\"/confirm\"]')"));
    const confirmRedirect = redirectAfter(`/price-books/${BATCH_ID}/confirm`, "POST", ledger);
    check(confirmRedirect?.status === 303 && new URL(confirmRedirect.location, BASE).pathname === `/price-books/${BATCH_ID}`, "confirmation POST returns exact HTTP 303");
    check(
      JSON.stringify(client.confirmationRequests.slice(confirmationRequestStart)) ===
        JSON.stringify([{matches: true}]),
      "actual confirmation POST body binds the reviewed preview",
    );

    const after = await evaluate(`({
      durable: document.querySelector('#price-book-durable-status')?.textContent.trim(),
      operational: document.querySelector('#price-book-operational-status')?.textContent.trim(),
      basis: document.querySelector('#price-book-operational-status')?.dataset.temporalBasis,
      hasConfirmation: Boolean(document.querySelector('form[action$="/confirmation-preview"]')),
      hasApply: Boolean(document.querySelector('form[action$="/apply-preview"]')),
    })`);
    check(after.durable === "VERIFIED_FUTURE" && after.operational === "VERIFIED_FUTURE", "browser confirmation reaches VERIFIED_FUTURE/VERIFIED_FUTURE");
    check(after.basis === "REGISTERED_OBSERVATION", "confirmed detail retains registered observation");
    check(!after.hasConfirmation && after.hasApply, "only guarded APPLY is exposed after confirmation");

    const screenshot = await client.send("Page.captureScreenshot", {
      format: "png",
      captureBeyondViewport: false,
    }, sessionId);
    const screenshotBytes = Buffer.from(screenshot.data, "base64");
    check(
      screenshotBytes.length > 8 && screenshotBytes.length <= 10 * 1024 * 1024 &&
        screenshotBytes.subarray(0, 8).equals(Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a])),
      "confirmed screenshot is a bounded PNG",
    );
    if (screenshotDescriptor === null) throw new Error("staging browser screenshot was not reserved");
    fs.ftruncateSync(screenshotDescriptor, 0);
    fs.writeFileSync(screenshotDescriptor, screenshotBytes);
    fs.fsyncSync(screenshotDescriptor);
    fs.closeSync(screenshotDescriptor);
    screenshotDescriptor = null;
    const screenshotSha256 = crypto.createHash("sha256").update(screenshotBytes).digest("hex");
    const scriptDisabled = await client.send(
      "Emulation.setScriptExecutionDisabled", {value: true}, sessionId,
    );
    check(
      scriptDisabled && Object.keys(scriptDisabled).length === 0,
      "confirmed page script execution is disabled before evidence freeze",
    );
    await client.send("Page.stopLoading", {}, sessionId);
    await sleep(250);
    await client.flushGuardTasks();
    await sleep(50);
    await client.flushGuardTasks();
    const unsafeRequests = client.requests.filter((item) => !["GET", "HEAD"].includes(item.method));
    check(
      JSON.stringify(unsafeRequests.map((item) => [item.method, item.origin, item.pathname, item.query, item.targetType])) ===
        JSON.stringify([
          ["POST", BASE, "/auth/login", "", "page"],
          ["POST", BASE, `/price-books/${BATCH_ID}/confirmation-preview`, "", "page"],
          ["POST", BASE, `/price-books/${BATCH_ID}/confirm`, "", "page"],
        ]),
      "browser issued only the three exact reviewed unsafe requests",
      unsafeRequests,
    );
    check(
      client.responses.slice(authenticatedResponseStart).every((item) => item.cacheControl === "no-store"),
      "every authenticated response is non-cacheable",
    );
    check(client.allTargetsGuarded(), "the one fresh Chromium page remained attached and guarded", client.targetSummary());
    check(client.blockedRequests.length === 0, "browser attempted no blocked or external request", client.blockedRequests);
    check(client.externalRealtimeRequests.length === 0, "browser attempted no WebSocket, WebRTC, or direct realtime request", client.externalRealtimeRequests);
    check(client.unexpectedSchemeRequests.length === 0, "browser attempted no WebTransport, direct socket, or unexpected scheme", client.unexpectedSchemeRequests);
    check(client.eventErrors.length === 0, "Chromium target and network guards emitted no error", client.eventErrors);
    check(client.exceptions.length === 0 && client.consoleErrors.length === 0, "browser emitted no runtime error");
    const targetSummary = client.targetSummary();
    const frozenActivity = JSON.stringify({
      blockedRequests: client.blockedRequests,
      consoleErrors: client.consoleErrors,
      eventErrors: client.eventErrors,
      exceptions: client.exceptions,
      externalRealtimeRequests: client.externalRealtimeRequests,
      redirects: client.redirects,
      requests: client.requests,
      responses: client.responses,
      unexpectedSchemeRequests: client.unexpectedSchemeRequests,
    });
    const mainTargetTerminated = await client.terminateMainTarget();
    await sleep(100);
    await client.flushGuardTasks();
    check(
      mainTargetTerminated && frozenActivity === JSON.stringify({
        blockedRequests: client.blockedRequests,
        consoleErrors: client.consoleErrors,
        eventErrors: client.eventErrors,
        exceptions: client.exceptions,
        externalRealtimeRequests: client.externalRealtimeRequests,
        redirects: client.redirects,
        requests: client.requests,
        responses: client.responses,
        unexpectedSchemeRequests: client.unexpectedSchemeRequests,
      }),
      "authenticated Chromium page is destroyed before proof without late activity",
    );
    const assertionManifestSha256 = crypto.createHash("sha256")
      .update(Buffer.from(JSON.stringify(assertions), "utf8"))
      .digest("hex");
    if (
      assertions.length !== EXPECTED_ASSERTION_COUNT ||
      assertionManifestSha256 !== EXPECTED_ASSERTION_MANIFEST_SHA256
    ) {
      throw new Error("staging browser assertion manifest differs");
    }
    writeProof({
      assertion_count: assertions.length,
      assertion_manifest_sha256: assertionManifestSha256,
      batch_id: BATCH_ID,
      browser_js_version: client.browserVersion.jsVersion,
      browser_pid: Number(EXPECTED_BROWSER_PID_TEXT),
      browser_product: client.browserVersion.product,
      browser_protocol_version: client.browserVersion.protocolVersion,
      browser_start_time: EXPECTED_BROWSER_START_TIME,
      confirmation_preview_sha256: confirmation.preview_sha256,
      contract: CONTRACT,
      driver_sha256: SELF_SHA256,
      node_sha256: EXPECTED_NODE_SHA256,
      node_version: EXPECTED_NODE_VERSION,
      operational_status_after: after.operational,
      operational_status_before: before.operational,
      operator_proof_sha256: OPERATOR_PROOF_SHA256,
      phase,
      raw_bytes: raw.bytes,
      raw_sha256: raw.sha256,
      screenshot_bytes: screenshotBytes.length,
      screenshot_sha256: screenshotSha256,
      source_commit: EXPECTED_COMMIT,
      source_tree: EXPECTED_TREE,
      status_after: after.durable,
      status_before: before.durable,
      target_summary: targetSummary,
      temporal_basis: after.basis,
      tls_certificate_sha256: tlsCertificateSha256,
    });
  } finally {
    ownerPassphrase = "";
    if (proofDescriptor !== null) fs.closeSync(proofDescriptor);
    if (screenshotDescriptor !== null) fs.closeSync(screenshotDescriptor);
    client.close();
  }
}

await run();

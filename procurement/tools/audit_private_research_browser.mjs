#!/usr/bin/env node
// Real Chromium/CDP acceptance for one sealed private-research workspace.

import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";

const [PHASE, BASE, CDP, EVIDENCE, DOWNLOADS, SECRET_PATH, EXPECTATIONS_PATH, STATE_PATH] =
  process.argv.slice(2);

if (
  ![PHASE, BASE, CDP, EVIDENCE, DOWNLOADS, SECRET_PATH, EXPECTATIONS_PATH, STATE_PATH].every(Boolean) ||
  !["initial", "restart"].includes(PHASE)
) {
  throw new Error("private-research browser audit arguments are incomplete");
}

const CONTRACT = "BUFFALO_PRIVATE_RESEARCH_BROWSER_ACCEPTANCE_V1";
const ALLOWED_ORIGIN = new URL(BASE).origin;
const SECRET = fs.readFileSync(SECRET_PATH).toString("utf8").replace(/\n$/, "");
const AUTHORIZATION = `Basic ${Buffer.from(`private:${SECRET}`, "utf8").toString("base64")}`;
const EXPECTED = JSON.parse(fs.readFileSync(EXPECTATIONS_PATH, "utf8"));
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

class CdpClient {
  constructor(url) {
    this.socket = new WebSocket(url);
    this.nextId = 1;
    this.pending = new Map();
    this.waiters = new Map();
    this.inflight = new Map();
    this.requests = [];
    this.responses = [];
    this.externalRequests = [];
    this.blockedExternalRequests = [];
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
  }

  onMessage(event) {
    const message = JSON.parse(event.data);
    if (message.id) {
      const pending = this.pending.get(message.id);
      if (!pending) return;
      this.pending.delete(message.id);
      if (message.error) pending.reject(new Error(message.error.message));
      else pending.resolve(message.result);
      return;
    }
    if (message.method === "Network.requestWillBeSent") {
      const request = message.params.request;
      if (/^https?:/i.test(request.url)) {
        const record = {
          requestId: message.params.requestId,
          method: request.method,
          url: safeUrl(request.url),
          origin: new URL(request.url).origin,
        };
        this.inflight.set(message.params.requestId, record);
        this.requests.push(record);
        if (record.origin !== ALLOWED_ORIGIN) this.externalRequests.push(record);
      }
    }
    if (message.method === "Fetch.requestPaused") {
      const request = message.params.request;
      let target;
      try {
        target = new URL(request.url);
      } catch {
        target = null;
      }
      if (target && ["http:", "https:"].includes(target.protocol) && target.origin !== ALLOWED_ORIGIN) {
        this.blockedExternalRequests.push({method: request.method, url: safeUrl(request.url)});
        void this.send("Fetch.failRequest", {
          requestId: message.params.requestId,
          errorReason: "BlockedByClient",
        }).catch((error) => this.eventErrors.push(String(error)));
      } else {
        void this.send("Fetch.continueRequest", {requestId: message.params.requestId})
          .catch((error) => this.eventErrors.push(String(error)));
      }
    }
    if (message.method === "Network.responseReceived") {
      const response = message.params.response;
      if (/^https?:/i.test(response.url)) {
        const headers = Object.fromEntries(
          Object.entries(response.headers || {}).map(([key, value]) => [key.toLowerCase(), value]),
        );
        this.responses.push({
          requestId: message.params.requestId,
          method: this.inflight.get(message.params.requestId)?.method || null,
          url: safeUrl(response.url),
          origin: new URL(response.url).origin,
          status: response.status,
          cacheControl: headers["cache-control"] || null,
          contentDisposition: headers["content-disposition"] || null,
          mimeType: response.mimeType,
        });
      }
    }
    if (message.method === "Runtime.exceptionThrown") {
      this.runtimeExceptions.push(message.params.exceptionDetails.text || "runtime exception");
    }
    if (
      message.method === "Runtime.consoleAPICalled" &&
      ["error", "assert"].includes(message.params.type)
    ) {
      this.consoleErrors.push(message.params.type);
    }
    const queue = this.waiters.get(message.method) || [];
    if (queue.length) {
      const waiter = queue.shift();
      clearTimeout(waiter.timer);
      waiter.resolve(message.params);
    }
  }

  send(method, params = {}) {
    const id = this.nextId++;
    return new Promise((resolve, reject) => {
      this.pending.set(id, {resolve, reject});
      this.socket.send(JSON.stringify({id, method, params}));
    });
  }

  waitEvent(method, timeoutMs = 30000) {
    return new Promise((resolve, reject) => {
      const queue = this.waiters.get(method) || [];
      const timer = setTimeout(() => {
        const current = this.waiters.get(method) || [];
        this.waiters.set(method, current.filter((entry) => entry.resolve !== resolve));
        reject(new Error(`timed out waiting for ${method}`));
      }, timeoutMs);
      queue.push({resolve, reject, timer});
      this.waiters.set(method, queue);
    });
  }

  close() {
    this.socket.close();
  }
}

const results = {
  contract: CONTRACT,
  phase: PHASE,
  base_url: BASE,
  passed: false,
  assertions: [],
  semantic_hashes: null,
  counts: null,
  artifact_hashes: null,
  external_requests: [],
  blocked_external_requests: [],
};

function check(condition, name, detail = null) {
  if (!condition) {
    throw new Error(`ASSERTION FAILED: ${name}: ${JSON.stringify(detail)}`);
  }
  results.assertions.push({name, passed: true, detail});
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

async function connect() {
  const listResponse = await fetch(`${CDP}/json/list`);
  const targets = await listResponse.json();
  const target = targets.find((item) => item.type === "page");
  if (!target) throw new Error("no Chromium page target is available");
  const client = new CdpClient(target.webSocketDebuggerUrl);
  await client.open();
  await client.send("Page.enable");
  await client.send("Runtime.enable");
  await client.send("Network.enable");
  await client.send("Network.setExtraHTTPHeaders", {
    headers: {Authorization: AUTHORIZATION},
  });
  await client.send("Fetch.enable", {
    patterns: [
      {urlPattern: "http://*/*", requestStage: "Request"},
      {urlPattern: "https://*/*", requestStage: "Request"},
    ],
  });
  await client.send("Browser.setDownloadBehavior", {
    behavior: "allow",
    downloadPath: DOWNLOADS,
    eventsEnabled: true,
  });
  return client;
}

async function audit(client) {
  const evaluate = async (expression) => {
    const response = await client.send("Runtime.evaluate", {
      expression,
      returnByValue: true,
      awaitPromise: true,
    });
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
    const loaded = client.waitEvent("Page.loadEventFired");
    const response = await client.send("Page.navigate", {url});
    if (response.errorText) throw new Error(`navigation failed: ${response.errorText}`);
    await loaded;
    await sleep(80);
  };

  const fetchPayload = (requestPath) =>
    evaluate(`fetch(${JSON.stringify(requestPath)}, {cache:"no-store"}).then(async (response) => ({
      status: response.status,
      body: await response.text(),
      cacheControl: response.headers.get("cache-control"),
      contentType: response.headers.get("content-type")
    }))`);

  const pageSnapshot = () => evaluate(`(() => ({
    url: location.href,
    text: document.body.innerText,
    articles: [...document.querySelectorAll("article.record")].map((article) => ({
      variantId: article.getAttribute("data-variant-id"),
      hasDetails: Boolean(article.querySelector("details")),
      detail: JSON.parse(article.querySelector("pre").textContent)
    })),
    forms: [...document.forms].map((form) => ({method: form.method.toUpperCase(), action: form.action})),
    buttons: [...document.querySelectorAll("button")].map((button) => ({
      text: button.textContent.trim(), type: button.type
    })),
    links: [...document.querySelectorAll("a[href]")].map((anchor) => anchor.href)
  }))()`);

  const assertRows = async (expected, label) => {
    const snapshot = await pageSnapshot();
    check(
      stable(snapshot.articles.map((article) => article.detail)) === stable(expected.first_page_rows),
      `${label} renders the exact expected first-page rows`,
      {expected: expected.first_page_rows.length, actual: snapshot.articles.length},
    );
    check(
      snapshot.articles.every((article) => article.hasDetails),
      `${label} retains evidence detail controls`,
    );
    check(
      snapshot.text.includes(`Showing ${expected.first_page_rows.length} of ${expected.total} matched rows`),
      `${label} reports the exact result count`,
      {total: expected.total, displayed: expected.first_page_rows.length},
    );
    return snapshot;
  };

  const submitFilter = async (parameters) => {
    const loaded = client.waitEvent("Page.loadEventFired");
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
    const receivedPath = path.join(DOWNLOADS, receivedName);
    const data = fs.readFileSync(receivedPath);
    const deadline = Date.now() + 10000;
    let response = null;
    while (Date.now() < deadline && !response) {
      response = client.responses.slice(responseMark).find(
        (item) => new URL(item.url).pathname === `/private-research/artifacts/${expected.name}`,
      );
      if (!response) await sleep(25);
    }
    check(receivedName === expected.name, "artifact download filename is exact", {
      expected: expected.name,
      received: receivedName,
    });
    check(
      data.length === expected.bytes && sha256Bytes(data) === expected.sha256,
      "artifact download bytes and SHA-256 match the sealed workspace",
      {name: expected.name, bytes: data.length, sha256: sha256Bytes(data)},
    );
    check(
      response?.status === 200 &&
        response.cacheControl === "no-store" &&
        String(response.contentDisposition).toLowerCase().includes("attachment"),
      "artifact response is a no-store attachment",
      {name: expected.name, response},
    );
    return expected.sha256;
  };

  await navigate(`${BASE}/private-research`);
  const initialSnapshot = await assertRows(EXPECTED.initial, "unfiltered workspace");
  check(
    stable(initialSnapshot.articles[0]?.detail) === stable(EXPECTED.initial.detail_row),
    "first evidence detail is the exact canonical research row",
  );
  const detail = initialSnapshot.articles[0]?.detail || {};
  check(
    ["source_ref", "forecast", "abc", "economics", "missing_data_reasons"].every(
      (field) => Object.hasOwn(detail, field),
    ),
    "detail exposes source, forecast, ABC, economics, and missing-evidence fields",
  );

  const healthResponse = await fetchPayload("/health");
  const health = JSON.parse(healthResponse.body);
  check(
    healthResponse.status === 200 &&
      healthResponse.cacheControl === "no-store" &&
      stable(Object.keys(health).sort()) ===
        stable(["mode", "ok", "operational_authority", "service"].sort()) &&
      health.ok === true &&
      health.operational_authority === false,
    "health is ready, bounded, and non-operational",
    {status: healthResponse.status, keys: Object.keys(health).sort()},
  );
  check(
    !/\b[0-9a-f]{64}\b/i.test(healthResponse.body),
    "health exposes no content hashes",
  );

  const manifestResponse = await fetchPayload("/private-research/manifest");
  const projectionResponse = await fetchPayload("/private-research/projection");
  const manifest = JSON.parse(manifestResponse.body);
  const projection = JSON.parse(projectionResponse.body);
  check(
    manifestResponse.status === 200 && stable(manifest) === stable(EXPECTED.manifest),
    "manifest endpoint exactly reads back the canonical manifest",
    {expectedSha256: EXPECTED.semantic_hashes.manifest_sha256, actualSha256: semanticSha256(manifest)},
  );
  check(
    projectionResponse.status === 200 && stable(projection) === stable(EXPECTED.projection),
    "projection endpoint exactly reads back the canonical projection",
    {expectedSha256: EXPECTED.semantic_hashes.projection_sha256, actualSha256: semanticSha256(projection)},
  );
  const actualCounts = {
    coverage_rows: projection.coverage_rows.length,
    research_rows: projection.research_rows.length,
    owner_worksheet: projection.owner_worksheet.length,
    unjoined_supplier_hypotheses: projection.unjoined_supplier_hypotheses.length,
    vendor_names: projection.vendor_names.length,
  };
  check(stable(actualCounts) === stable(EXPECTED.counts), "workspace counts match canonical replay", actualCounts);

  check(
    initialSnapshot.forms.length === 1 &&
      initialSnapshot.forms[0].method === "GET" &&
      new URL(initialSnapshot.forms[0].action).pathname === "/private-research",
    "viewer exposes only the read-only filter form",
    initialSnapshot.forms,
  );
  check(
    stable(initialSnapshot.buttons) === stable([{text: "Filter", type: "submit"}]),
    "viewer exposes no operational action buttons",
    initialSnapshot.buttons,
  );
  const forbiddenPath = /(monday-runs|price-books|supplier-mapping|pricing|reconciliation|vendor-rules|economics|draft|purchase|orders|po)/i;
  check(
    initialSnapshot.links.every((raw) => {
      const value = new URL(raw);
      return value.origin === ALLOWED_ORIGIN && !forbiddenPath.test(value.pathname);
    }),
    "viewer links remain exact-origin and non-operational",
    initialSnapshot.links.map(safeUrl),
  );
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
    "operational route probes are absent from the private composition",
    probeStatuses,
  );

  for (const filter of EXPECTED.filters) {
    await navigate(`${BASE}/private-research`);
    await submitFilter(filter.parameters);
    const current = new URL(await evaluate("location.href"));
    check(
      Object.entries(filter.parameters).every(([key, value]) => current.searchParams.get(key) === value),
      `${filter.name} filter submits through the browser form`,
      {url: safeUrl(current.href), parameters: filter.parameters},
    );
    await assertRows(filter, `${filter.name} filter`);
  }

  await navigate(`${BASE}/private-research`);
  const artifactHashes = {};
  for (const artifact of EXPECTED.artifacts) {
    artifactHashes[artifact.name] = await downloadArtifact(artifact);
  }
  check(
    stable(Object.keys(artifactHashes).sort()) === stable([...ARTIFACT_NAMES].sort()),
    "all four bounded artifacts download through Chromium",
    Object.keys(artifactHashes).sort(),
  );

  const cookies = (await client.send("Network.getAllCookies")).cookies;
  check(
    !cookies.some((cookie) => cookie.name === "buffalo_local_session"),
    "private viewer authentication creates no ambient local-session cookie",
  );

  const semanticHashes = {
    manifest_sha256: semanticSha256(manifest),
    projection_sha256: semanticSha256(projection),
  };
  const restartState = {
    contract: CONTRACT,
    base_url: BASE,
    workspace_id: manifest.workspace_id,
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
    check(true, "initial workspace identity is sealed for restart comparison");
  } else {
    const prior = JSON.parse(fs.readFileSync(STATE_PATH, "utf8"));
    check(
      stable(prior) === stable(restartState),
      "same-origin restart preserves workspace, counts, and all artifact hashes",
      {workspaceId: restartState.workspace_id},
    );
  }

  await sleep(100);
  check(client.externalRequests.length === 0, "no browser request escaped the exact loopback origin", client.externalRequests);
  check(client.blockedExternalRequests.length === 0, "no external browser request was attempted", client.blockedExternalRequests);
  check(client.runtimeExceptions.length === 0, "browser raised no runtime exceptions", client.runtimeExceptions);
  check(client.consoleErrors.length === 0, "browser raised no console errors", client.consoleErrors);
  check(client.eventErrors.length === 0, "CDP interception raised no event errors", client.eventErrors);
  const applicationResponses = client.responses.filter((response) => response.origin === ALLOWED_ORIGIN);
  check(
    applicationResponses.length > 0 && applicationResponses.every((response) => response.cacheControl === "no-store"),
    "every observed viewer response is no-store",
    applicationResponses.filter((response) => response.cacheControl !== "no-store"),
  );

  results.semantic_hashes = semanticHashes;
  results.counts = actualCounts;
  results.artifact_hashes = artifactHashes;
  results.external_requests = client.externalRequests;
  results.blocked_external_requests = client.blockedExternalRequests;
  results.request_count = client.requests.length;
  results.response_count = client.responses.length;
  results.passed = true;
}

const client = await connect();
try {
  await audit(client);
  const resultPath = path.join(EVIDENCE, `${PHASE}-browser-results.json`);
  fs.writeFileSync(resultPath, `${JSON.stringify(results, null, 2)}\n`, {mode: 0o600, flag: "wx"});
  console.log(JSON.stringify({phase: PHASE, assertionCount: results.assertions.length, passed: true}));
} catch (error) {
  results.error = String(error?.stack || error);
  results.external_requests = client.externalRequests;
  results.blocked_external_requests = client.blockedExternalRequests;
  const resultPath = path.join(EVIDENCE, `${PHASE}-browser-results.json`);
  fs.writeFileSync(resultPath, `${JSON.stringify(results, null, 2)}\n`, {mode: 0o600, flag: "wx"});
  throw error;
} finally {
  client.close();
}

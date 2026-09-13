#!/usr/bin/env node
// TEST DATA — NOT FOR ORDERING: real Chromium/CDP acceptance harness.

import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";

const [phase, BASE, CDP, EVIDENCE, DOWNLOADS, AUTH_SECRET_PATH, REVIEW_TOKEN_PATH, STATE_PATH] =
  process.argv.slice(2);

if (!phase || !BASE || !CDP || !EVIDENCE || !DOWNLOADS || !AUTH_SECRET_PATH || !REVIEW_TOKEN_PATH || !STATE_PATH) {
  throw new Error("browser audit arguments are incomplete");
}

const AUTH_SECRET = fs.readFileSync(AUTH_SECRET_PATH, "utf8").replace(/\n$/, "");
const REVIEW_TOKEN = fs.readFileSync(REVIEW_TOKEN_PATH, "utf8").replace(/\n$/, "");
const SPOOF_ACTOR = "spoofed-browser-actor-must-be-ignored";
const sleep = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));

class CdpClient {
  constructor(url, allowedOrigin) {
    this.socket = new WebSocket(url);
    this.allowedOrigin = allowedOrigin;
    this.nextId = 1;
    this.pending = new Map();
    this.waiters = new Map();
    this.inflight = new Map();
    this.requests = [];
    this.responses = [];
    this.redirects = [];
    this.exceptions = [];
    this.consoleErrors = [];
    this.originOverridePath = null;
    this.blockedRequests = [];
  }

  async open() {
    await new Promise((resolve, reject) => {
      this.socket.onopen = resolve;
      this.socket.onerror = reject;
    });
    this.socket.onmessage = (event) => {
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
        const previous = this.inflight.get(message.params.requestId);
        if (message.params.redirectResponse && previous) {
          const response = message.params.redirectResponse;
          const headers = Object.fromEntries(
            Object.entries(response.headers || {}).map(([key, value]) => [key.toLowerCase(), value]),
          );
          this.redirects.push({
            requestId: message.params.requestId,
            url: previous.url,
            method: previous.method,
            status: response.status,
            location: headers.location || null,
            cacheControl: headers["cache-control"] || null,
          });
        }
        this.inflight.set(message.params.requestId, {url: request.url, method: request.method});
        if (/^https?:/i.test(request.url)) {
          const headers = Object.fromEntries(
            Object.entries(request.headers || {})
              .filter(([key]) => ["host", "origin", "content-type"].includes(key.toLowerCase()))
              .map(([key, value]) => [key.toLowerCase(), value]),
          );
          this.requests.push({requestId: message.params.requestId, url: request.url, method: request.method, headers});
        }
      }
      if (message.method === "Fetch.requestPaused") {
        const request = message.params.request;
        const target = new URL(request.url);
        if (["http:", "https:"].includes(target.protocol) && target.origin !== this.allowedOrigin) {
          this.blockedRequests.push({url: request.url, method: request.method});
          void this.send("Fetch.failRequest", {
            requestId: message.params.requestId,
            errorReason: "BlockedByClient",
          });
          return;
        }
        if (target.pathname === this.originOverridePath) {
          this.originOverridePath = null;
          const headers = Object.entries(request.headers || {})
            .filter(([key]) => key.toLowerCase() !== "origin")
            .map(([name, value]) => ({name, value: String(value)}));
          headers.push({name: "Origin", value: "http://cross-origin.invalid"});
          void this.send("Fetch.continueRequest", {
            requestId: message.params.requestId,
            headers,
          });
          return;
        }
        void this.send("Fetch.continueRequest", {requestId: message.params.requestId});
        return;
      }
      if (message.method === "Network.responseReceived") {
        const response = message.params.response;
        if (/^https?:/i.test(response.url)) {
          const headers = Object.fromEntries(
            Object.entries(response.headers || {}).map(([key, value]) => [key.toLowerCase(), value]),
          );
          this.responses.push({
            requestId: message.params.requestId,
            url: response.url,
            method: this.inflight.get(message.params.requestId)?.method || null,
            status: response.status,
            mimeType: response.mimeType,
            cacheControl: headers["cache-control"] || null,
            contentDisposition: headers["content-disposition"] || null,
          });
        }
      }
      if (message.method === "Runtime.exceptionThrown") {
        this.exceptions.push(message.params.exceptionDetails.text || "exception");
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
    };
  }

  send(method, params = {}) {
    const id = this.nextId++;
    return new Promise((resolve, reject) => {
      this.pending.set(id, {resolve, reject});
      this.socket.send(JSON.stringify({id, method, params}));
    });
  }

  waitEvent(method, timeoutMs = 20000) {
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
  format: "BUFFALO_LOCAL_PURCHASING_BROWSER_ACCEPTANCE_V1",
  phase,
  safetyLabel: "TEST DATA — NOT FOR ORDERING",
  baseUrl: BASE,
  assertions: [],
};

function check(condition, name, detail = null) {
  if (!condition) throw new Error(`ASSERTION FAILED: ${name}: ${JSON.stringify(detail)}`);
  results.assertions.push({name, detail, passed: true});
}

function sha256File(filename) {
  return crypto.createHash("sha256").update(fs.readFileSync(filename)).digest("hex");
}

async function connect() {
  const targets = await (await fetch(`${CDP}/json/list`)).json();
  const target = targets.find((item) => item.type === "page");
  if (!target) throw new Error("no Chromium page target is available");
  const client = new CdpClient(target.webSocketDebuggerUrl, new URL(BASE).origin);
  await client.open();
  await client.send("Page.enable");
  await client.send("Runtime.enable");
  await client.send("Network.enable");
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
    await client.send("Page.navigate", {url});
    await loaded;
    await sleep(150);
  };

  const submit = async (expression) => {
    const loaded = client.waitEvent("Page.loadEventFired");
    try {
      await evaluate(expression);
    } catch (error) {
      if (!/context|navigat|destroyed/i.test(String(error))) throw error;
    }
    await loaded;
    await sleep(180);
  };

  const body = () => evaluate("document.body.innerText");
  const textContent = () => evaluate("document.body.textContent");
  const currentUrl = () => evaluate("location.href");
  const saveHtml = async (name) => {
    const content = await evaluate("document.documentElement.outerHTML");
    fs.writeFileSync(path.join(EVIDENCE, name), content, {encoding: "utf8", mode: 0o600});
  };
  const screenshot = async (name) => {
    const response = await client.send("Page.captureScreenshot", {
      format: "png",
      // Evidence screenshots are intentionally viewport-bounded.  A complete
      // frozen run can be taller than Chromium's safe full-page bitmap limit;
      // the companion saved HTML and structured result ledger retain the full
      // page while this prevents an oversized CDP frame from aborting review.
      captureBeyondViewport: false,
      fromSurface: true,
    });
    fs.writeFileSync(path.join(EVIDENCE, name), Buffer.from(response.data, "base64"), {mode: 0o600});
  };
  const jsonFetch = (requestPath) =>
    evaluate(`fetch(${JSON.stringify(requestPath)}, {credentials:"same-origin"}).then(async (response) => ({status:response.status, body:await response.json()}))`);
  const statusFetch = (requestPath, init = {}) =>
    evaluate(`fetch(${JSON.stringify(requestPath)}, ${JSON.stringify({credentials: "same-origin", ...init})}).then(async (response) => ({status:response.status, body:await response.text()}))`);
  const formScript = (selectorExpression, values = {}) => `(() => {
    const form = ${selectorExpression};
    if (!form) throw new Error("form not found");
    const values = ${JSON.stringify(values)};
    for (const [name, value] of Object.entries(values)) {
      const input = form.elements.namedItem(name);
      if (!input) throw new Error("missing field " + name);
      input.value = value;
      input.dispatchEvent(new Event("input", {bubbles:true}));
      input.dispatchEvent(new Event("change", {bubbles:true}));
    }
    form.requestSubmit();
    return true;
  })()`;
  const waitForDownloads = async (expected) => {
    const deadline = Date.now() + 20000;
    while (Date.now() < deadline) {
      const names = fs.readdirSync(DOWNLOADS);
      const complete = names.filter((name) => !name.endsWith(".crdownload"));
      if (complete.length >= expected && !names.some((name) => name.endsWith(".crdownload"))) {
        return complete.sort();
      }
      await sleep(100);
    }
    throw new Error(`download timeout at ${expected} files`);
  };
  const ledgerMark = () => ({responses: client.responses.length, redirects: client.redirects.length});
  const latestResponse = (pathname, method = null, mark = {responses: 0}) =>
    [...client.responses.slice(mark.responses)]
      .reverse()
      .find(
        (response) =>
          new URL(response.url).pathname === pathname && (!method || response.method === method),
      );
  const latestRedirect = (pathname, method = null, mark = {redirects: 0}) =>
    [...client.redirects.slice(mark.redirects)]
      .reverse()
      .find(
        (response) =>
          new URL(response.url).pathname === pathname && (!method || response.method === method),
      );
  const waitForResponse = async (pathname, method, mark) => {
    const deadline = Date.now() + 15000;
    while (Date.now() < deadline) {
      const record = latestResponse(pathname, method, mark);
      if (record) return record;
      await sleep(25);
    }
    return null;
  };
  const waitForRedirect = async (pathname, method, mark) => {
    const deadline = Date.now() + 15000;
    while (Date.now() < deadline) {
      const record = latestRedirect(pathname, method, mark);
      if (record) return record;
      await sleep(25);
    }
    return null;
  };
  const assertRedirect = async (mark, method, pathname, expectedTarget, name) => {
    const record = await waitForRedirect(pathname, method, mark);
    const normalized = record?.location ? new URL(record.location, record.url).href : null;
    check(record?.status === 303 && normalized === new URL(expectedTarget, BASE).href, name, record);
  };
  const snapshotDownloads = () =>
    new Set(fs.readdirSync(DOWNLOADS).filter((name) => !name.endsWith(".crdownload")));
  const downloadFromClick = async (clickExpression, metadata) => {
    const before = snapshotDownloads();
    await evaluate(clickExpression);
    await waitForDownloads(before.size + 1);
    const added = [...snapshotDownloads()].filter((name) => !before.has(name));
    check(added.length === 1, "one browser click creates exactly one completed download", {metadata, added});
    const name = added[0];
    const filename = path.join(DOWNLOADS, name);
    return {...metadata, name, bytes: fs.statSync(filename).size, sha256: sha256File(filename)};
  };
  const login = async () => {
    await navigate(`${BASE}/auth/login`);
    let text = await body();
    check(text.includes("SYNTHETIC_DEMO"), "login identifies the explicit synthetic mode");
    const mark = ledgerMark();
    await submit(formScript("document.querySelector('form[method=\"post\"]')", {secret: AUTH_SECRET}));
    const loginUrl = await currentUrl();
    check(
      loginUrl === `${BASE}/`,
      "login redirects through the real local session boundary",
      {url: loginUrl, body: await body()},
    );
    await assertRedirect(mark, "POST", "/auth/login", "/", "login POST returns the exact HTTP 303 transition");
    check((await waitForResponse("/", "GET", mark))?.status === 200, "login redirect target returns HTTP 200");
    text = await body();
    check(
      text.includes("TEST DATA — NOT FOR ORDERING") &&
        text.includes("PO generation: BLOCKED — SYNTHETIC DEMO / INTERNAL DRAFT ONLY"),
      "post-login readiness is visibly synthetic and operationally blocked",
    );
  };

  if (phase === "phase1") {
    await navigate(`${BASE}/supplier-mapping`);
    check((await body()).includes("Authentication required"), "unauthenticated mapping list is refused");
    check(latestResponse("/supplier-mapping")?.status === 401, "unauthenticated list returns HTTP 401", latestResponse("/supplier-mapping"));

    await login();
    const cookies = (await client.send("Network.getAllCookies")).cookies.filter(
      (cookie) => cookie.name === "buffalo_local_session",
    );
    check(cookies.length === 1, "login creates exactly one opaque local session cookie");
    check(cookies[0].httpOnly === true, "session cookie is HttpOnly");
    check(cookies[0].sameSite === "Strict", "session cookie is SameSite Strict", cookies[0].sameSite);
    check(cookies[0].domain === "127.0.0.1", "session cookie is bound to the exact loopback host", cookies[0].domain);
    check(cookies[0].path === "/", "session cookie is scoped to the application root", cookies[0].path);
    check(cookies[0].secure === false, "loopback session cookie does not claim HTTPS transport", cookies[0].secure);
    check(cookies[0].expires > Date.now() / 1000, "session cookie has a future bounded expiry", cookies[0].expires);
    check(!(await evaluate("document.cookie")).includes("buffalo_local_session"), "session token is unavailable to page script");

    await navigate(`${BASE}/historical-sales/review`);
    check(
      latestResponse("/assets/historical-sales-catalog-picker.js")?.status === 200,
      "the CSP-compatible same-origin catalog picker script loads",
      latestResponse("/assets/historical-sales-catalog-picker.js"),
    );
    await evaluate(`document.body.insertAdjacentHTML("beforeend", ` +
      JSON.stringify(`<section class="historical-sales-review-card"><form data-historical-sales-map><input name="canonical_variant_id"></form><div class="catalog-picker"><input class="catalog-search-input" value="Synthetic"><button type="button" class="catalog-search-button">SEARCH</button><p class="catalog-search-status"></p><div class="catalog-search-results"></div></div></section>`) + `); true`);
    await evaluate("document.querySelector('.catalog-search-button').click(); true");
    for (let attempt = 0; attempt < 100; attempt += 1) {
      if ((await body()).includes("Local catalog result — Variant ID 1001")) break;
      await sleep(25);
    }
    check((await body()).includes("Local catalog result — Variant ID 1001"), "catalog picker performs its real bounded same-origin fetch");
    await evaluate("document.querySelector('.catalog-select-button').click(); true");
    check(
      (await evaluate("document.querySelector('input[name=canonical_variant_id]').value")) === "1001",
      "catalog picker fills the exact card-local Variant ID without submitting",
    );

    await navigate(`${BASE}/supplier-mapping`);
    let text = await body();
    check(text.includes("TEST DATA — NOT FOR ORDERING"), "mapping page has the synthetic safety banner");
    check(text.includes("Batches: 0 · candidates: 0 · decisions: 0"), "mapping registry begins empty");

    const statusBeforeCsrf = await jsonFetch("/supplier-mapping/status");
    client.originOverridePath = "/supplier-mapping/intake";
    const csrfResponse = await statusFetch("/supplier-mapping/intake", {method: "POST"});
    check(csrfResponse.status === 403, "cross-origin browser write is rejected", csrfResponse.status);
    check(csrfResponse.body.includes("same-origin"), "cross-origin rejection identifies the origin boundary");
    const statusAfterCsrf = await jsonFetch("/supplier-mapping/status");
    check(
      JSON.stringify(statusAfterCsrf.body) === JSON.stringify(statusBeforeCsrf.body),
      "cross-origin refusal occurs without a mapping write",
      {before: statusBeforeCsrf.body, after: statusAfterCsrf.body},
    );

    let transitionMark = ledgerMark();
    await submit(formScript("document.querySelector('form[action=\"/supplier-mapping/intake\"]')"));
    await assertRedirect(transitionMark, "POST", "/supplier-mapping/intake", "/supplier-mapping", "first intake POST returns HTTP 303");
    text = await body();
    check(text.includes("Batches: 2 · candidates: 2 · decisions: 0"), "verified fixed packet intake creates exactly two batches and candidates");
    transitionMark = ledgerMark();
    await submit(formScript("document.querySelector('form[action=\"/supplier-mapping/intake\"]')"));
    await assertRedirect(transitionMark, "POST", "/supplier-mapping/intake", "/supplier-mapping", "intake replay POST returns HTTP 303");
    text = await body();
    check(text.includes("Batches: 2 · candidates: 2 · decisions: 0"), "exact packet replay creates no duplicate rows");
    await saveHtml("01-mapping-intake-test-data.html");
    await screenshot("01-mapping-intake-test-data.png");

    await navigate(`${BASE}/supplier-mapping?limit=1&offset=0`);
    const firstPage = await evaluate(`(() => ({
      rows:[...document.querySelectorAll("tbody a[href^='/supplier-mapping/']")].map((a) => ({text:a.textContent,href:a.getAttribute("href")})),
      next:document.querySelector("a[rel=next]")?.getAttribute("href") || null,
      previous:document.querySelector("a[rel=prev]")?.getAttribute("href") || null,
      body:document.body.innerText,
    }))()`);
    check(firstPage.rows.length === 1 && firstPage.next && !firstPage.previous, "first one-row server page is bounded and links forward", firstPage);
    await navigate(new URL(firstPage.next, BASE).href);
    const secondPage = await evaluate(`(() => ({
      rows:[...document.querySelectorAll("tbody a[href^='/supplier-mapping/']")].map((a) => ({text:a.textContent,href:a.getAttribute("href")})),
      next:document.querySelector("a[rel=next]")?.getAttribute("href") || null,
      previous:document.querySelector("a[rel=prev]")?.getAttribute("href") || null,
      body:document.body.innerText,
    }))()`);
    check(secondPage.rows.length === 1 && !secondPage.next && secondPage.previous, "second one-row server page is bounded and links backward", secondPage);
    const candidateRows = [...firstPage.rows, ...secondPage.rows];
    check(new Set(candidateRows.map((item) => item.href)).size === 2, "pagination reaches both unique candidate records", candidateRows);
    const unresolved = candidateRows.find((item) => item.text.includes("synthetic-unresolved"));
    const valid = candidateRows.find((item) => item.text.includes("fabricated-authoritative"));
    check(Boolean(unresolved && valid), "both unresolved and exact-match scenarios are visible", candidateRows);

    await navigate(new URL(unresolved.href, BASE).href);
    text = await textContent();
    check(text.includes("ABSENT") && text.includes("EXPLICIT_NULL"), "unresolved candidate preserves typed absence and explicit null");
    check(text.includes("IDENTITY_UNRESOLVED"), "unresolved blocker is visible");
    check(text.includes("No candidate Variant/vendor offers"), "unresolved candidate has no invented offer comparison");
    const unresolvedSourceHref = await evaluate("document.querySelector('a[href$=\"/source\"]')?.href");
    check(Boolean(unresolvedSourceHref), "bounded unresolved source-evidence metadata download is exposed");
    const sourceDownloads = [await downloadFromClick(
      "document.querySelector('a[href$=\"/source\"]')?.click()",
      {url: unresolvedSourceHref, kind: "unresolved-source"},
    )];

    transitionMark = ledgerMark();
    await submit(
      formScript("document.querySelector('form[action$=\"/decision\"]')", {
        action: "DEFER",
        reason: "Synthetic unresolved identity remains deferred",
        existing_offer_id: "",
        offer_link_kind: "",
      }),
    );
    check((await waitForResponse(new URL(unresolved.href, BASE).pathname + "/decision", "POST", transitionMark))?.status === 200, "DEFER preview POST returns HTTP 200");
    text = await body();
    check(text.includes("Confirm mapping disposition"), "DEFER first request is a write-free confirmation preview");
    check(text.includes("Synthetic unresolved identity remains deferred"), "DEFER preview shows the exact reason");
    let status = await jsonFetch("/supplier-mapping/status");
    check(status.status === 200 && status.body.decision_count === 0, "DEFER preview writes no decision", status);
    await saveHtml("02-defer-preview-test-data.html");
    await screenshot("02-defer-preview-test-data.png");
    transitionMark = ledgerMark();
    await submit(formScript("document.querySelector('form[method=\"post\"]')"));
    await assertRedirect(transitionMark, "POST", new URL(unresolved.href, BASE).pathname + "/decision", new URL(unresolved.href, BASE).pathname, "confirmed DEFER POST returns HTTP 303");
    text = await body();
    check(text.includes("DEFER") && text.includes("Synthetic unresolved identity remains deferred"), "confirmed DEFER is durable and visible");
    status = await jsonFetch("/supplier-mapping/status");
    check(status.body.decision_count === 1 && status.body.selection_head_count === 0, "DEFER has no routine-selection side effect", status.body);

    await navigate(new URL(valid.href, BASE).href);
    text = await textContent();
    check(text.includes("VALUE: SUP-001") && text.includes("VALUE: 6.0000"), "exact candidate fields preserve source values and numeric scale");
    check(text.includes("EXPLICIT_NULL") && text.includes("ABSENT"), "exact candidate retains three-state evidence");
    check(text.includes("EXACT"), "existing active legacy offer is compared as an exact contract match");
    const validSourceHref = await evaluate("document.querySelector('a[href$=\"/source\"]')?.href");
    check(Boolean(validSourceHref), "bounded valid source-evidence metadata download is exposed");
    sourceDownloads.push(await downloadFromClick(
      "document.querySelector('a[href$=\"/source\"]')?.click()",
      {url: validSourceHref, kind: "valid-source"},
    ));
    const offerId = await evaluate("document.querySelector('form[action$=\"/decision\"] select[name=\"existing_offer_id\"] option[value]:not([value=\"\"])')?.value");
    check(/^\d+$/.test(offerId), "exact legacy offer identifier is server-rendered");
    transitionMark = ledgerMark();
    await submit(
      formScript("document.querySelector('form[action$=\"/decision\"]')", {
        action: "APPROVE_MAPPING",
        existing_offer_id: offerId,
        offer_link_kind: "LINKED_EXISTING",
        reason: "Synthetic exact legacy offer linkage",
      }),
    );
    check((await waitForResponse(new URL(valid.href, BASE).pathname + "/decision", "POST", transitionMark))?.status === 200, "approval preview POST returns HTTP 200");
    text = await body();
    check(text.includes("APPROVE_MAPPING") && text.includes(offerId), "approval preview visibly binds action and exact offer");
    status = await jsonFetch("/supplier-mapping/status");
    check(status.body.decision_count === 1, "approval preview writes no decision", status.body);
    await saveHtml("03-approval-preview-test-data.html");
    await screenshot("03-approval-preview-test-data.png");
    transitionMark = ledgerMark();
    await submit(formScript("document.querySelector('form[method=\"post\"]')"));
    await assertRedirect(transitionMark, "POST", new URL(valid.href, BASE).pathname + "/decision", new URL(valid.href, BASE).pathname, "confirmed approval POST returns HTTP 303");
    text = await body();
    check(text.includes("APPROVE_MAPPING") && text.includes("Separate routine selection"), "confirmed mapping approval exposes a separate selection action");

    transitionMark = ledgerMark();
    await submit(
      formScript("document.querySelector('form[action$=\"/selection\"]')", {
        effective_from: "2026-09-13",
        reason: "Synthetic routine offer shadow selection",
      }),
    );
    check((await waitForResponse(new URL(valid.href, BASE).pathname + "/selection", "POST", transitionMark))?.status === 200, "selection preview POST returns HTTP 200");
    text = await body();
    check(text.includes("Confirm routine offer selection"), "selection first request is a separate confirmation preview");
    check(text.includes("2026-09-13") && text.includes("Synthetic routine offer shadow selection"), "selection preview visibly binds date and reason");
    status = await jsonFetch("/supplier-mapping/status");
    check(status.body.selection_event_count === 0 && status.body.selection_head_count === 0, "selection preview writes no event or head", status.body);
    await saveHtml("04-selection-preview-test-data.html");
    await screenshot("04-selection-preview-test-data.png");
    transitionMark = ledgerMark();
    await submit(formScript("document.querySelector('form[method=\"post\"]')"));
    await assertRedirect(transitionMark, "POST", new URL(valid.href, BASE).pathname + "/selection", new URL(valid.href, BASE).pathname, "confirmed selection POST returns HTTP 303");
    text = await body();
    check(text.includes("legacy comparison MATCH"), "confirmed selection reaches exact legacy shadow MATCH");
    status = await jsonFetch("/supplier-mapping/status");
    check(
      status.body.decision_count === 2 &&
        status.body.selection_event_count === 1 &&
        status.body.selection_head_count === 1 &&
        status.body.shadow_match_count === 1 &&
        status.body.recommendation_cutover_enabled === false &&
        status.body.offer_activation_enabled === false,
      "mapping and selection remain one-event SHADOW ONLY with activation and cutover disabled",
      status.body,
    );
    await saveHtml("05-shadow-match-test-data.html");
    await screenshot("05-shadow-match-test-data.png");

    const inventory = await jsonFetch("/inventory-snapshots/status?as_of=2026-09-13");
    check(inventory.status === 200 && inventory.body.status === "PASS", "same-day synthetic inventory status is PASS", inventory.body);

    await navigate(`${BASE}/monday-runs`);
    text = await body();
    check(text.includes("TEST DATA — NOT FOR ORDERING") && text.includes("No release or Shopify action is available"), "Monday page is visibly DRAFT-only");
    transitionMark = ledgerMark();
    await submit(
      formScript("document.querySelector('form[action=\"monday-runs/prepare\"]')", {
        business_date: "2026-09-13",
        idempotency_key: "browser-synthetic-20260913-v1",
        variant_ids: "1001,2002",
        actor: SPOOF_ACTOR,
        review_token: REVIEW_TOKEN,
      }),
    );
    const preparedUrl = await currentUrl();
    const runMatch = preparedUrl.match(/\/monday-runs\/([0-9a-f-]{36})$/);
    check(Boolean(runMatch), "prepare redirects to a concrete frozen run", preparedUrl);
    const runId = runMatch[1];
    await assertRedirect(transitionMark, "POST", "/monday-runs/prepare", `/monday-runs/${runId}`, "prepare POST returns HTTP 303");
    text = await textContent();
    check(text.includes("2002") && text.includes("1001"), "mixed eligible and blocked variants are retained");
    check(!text.includes(SPOOF_ACTOR), "browser actor input is not authoritative or displayed");
    await saveHtml("06-mixed-run-test-data.html");
    await screenshot("06-mixed-run-test-data.png");

    const exclusionPath = new URL(await evaluate("[...document.forms].find((form) => (form.getAttribute('action') || '').includes('/blockers/') && (form.getAttribute('action') || '').endsWith('/exclude')).action"), BASE).pathname;
    transitionMark = ledgerMark();
    await submit(
      formScript("[...document.forms].find((form) => (form.getAttribute('action') || '').includes('/blockers/') && (form.getAttribute('action') || '').endsWith('/exclude'))", {
        expected_input_fingerprint: "0".repeat(64),
        reason: "Synthetic stale exclusion must fail",
        actor: SPOOF_ACTOR,
        review_token: REVIEW_TOKEN,
      }),
    );
    const staleExclusionResponse = await waitForResponse(exclusionPath, "POST", transitionMark);
    check(staleExclusionResponse?.status === 409, "stale blocker exclusion returns HTTP 409", staleExclusionResponse);
    await navigate(`${BASE}/monday-runs/${runId}`);
    text = await body();
    check(text.includes("Acknowledge and exclude from this run only") && !text.includes("ACKNOWLEDGE_AND_EXCLUDE — RUN_ONLY"), "stale exclusion leaves the blocker OPEN and unexcluded");
    transitionMark = ledgerMark();
    await submit(
      formScript("[...document.forms].find((form) => (form.getAttribute('action') || '').includes('/blockers/') && (form.getAttribute('action') || '').endsWith('/exclude'))", {
        reason: "Synthetic missing variant excluded from this run only",
        actor: SPOOF_ACTOR,
        review_token: REVIEW_TOKEN,
      }),
    );
    await assertRedirect(transitionMark, "POST", exclusionPath, `/monday-runs/${runId}`, "valid blocker exclusion POST returns HTTP 303");
    text = await body();
    check(text.includes("ACKNOWLEDGE_AND_EXCLUDE — RUN_ONLY") && text.includes("Original blocker retained"), "valid RUN_ONLY exclusion preserves the source blocker");
    await evaluate("[...document.querySelectorAll('details')].forEach((item) => { item.open = true; })");
    text = await textContent();
    for (const required of [
      "SUP-001",
      "750ML",
      "6x750ML",
      "2026-09-13",
      "synthetic-location-001",
      "1.6683",
      "4.99",
      "3.3217",
      "66.57",
      "1.000000",
      "baseline need 3",
      "1 case(s) + 0 loose",
    ]) {
      check(text.includes(required), `structured frozen evidence includes ${required}`);
    }
    await saveHtml("07-frozen-evidence-test-data.html");
    await screenshot("07-frozen-evidence-test-data.png");

    const renderedReviewPath = new URL(
      await evaluate("[...document.forms].find((form) => (form.getAttribute('action') || '').includes('/recommendations/') && (form.getAttribute('action') || '').endsWith('/review')).getAttribute('action')"),
      await currentUrl(),
    ).pathname;
    const reviewPath = `/monday-runs/${runId}/recommendations/1/review`;
    check(renderedReviewPath === reviewPath, "review form targets the exact frozen recommendation", {renderedReviewPath, reviewPath});
    transitionMark = ledgerMark();
    await submit(
      formScript("[...document.forms].find((form) => (form.getAttribute('action') || '').includes('/recommendations/') && (form.getAttribute('action') || '').endsWith('/review'))", {
        action: "EDIT_QUANTITY",
        approved_cases: "1",
        approved_loose_units: "0",
        comment: "Synthetic normal preview only",
        actor: SPOOF_ACTOR,
        review_token: REVIEW_TOKEN,
      }),
    );
    check((await waitForResponse(reviewPath, "POST", transitionMark))?.status === 200, "normal recommendation preview POST returns HTTP 200");
    text = await body();
    check(text.includes("NORMAL") && text.includes("Recalculated line total\n$10.01"), "one-case quantity preview is NORMAL and recalculates to $10.01");
    await navigate(`${BASE}/monday-runs/${runId}`);
    check((await body()).includes("Record immutable decision"), "normal preview is cancelled without a decision");

    transitionMark = ledgerMark();
    await submit(
      formScript("[...document.forms].find((form) => (form.getAttribute('action') || '').includes('/recommendations/') && (form.getAttribute('action') || '').endsWith('/review'))", {
        action: "EDIT_QUANTITY",
        approved_cases: "2",
        approved_loose_units: "0",
        comment: "Synthetic material quantity acceptance",
        actor: SPOOF_ACTOR,
        review_token: REVIEW_TOKEN,
      }),
    );
    check((await waitForResponse(reviewPath, "POST", transitionMark))?.status === 200, "material recommendation preview POST returns HTTP 200");
    text = await body();
    check(text.includes("MATERIAL") && text.includes("Ordered units\n12"), "two-case edit is MATERIAL and yields 12 units");
    check(text.includes("Merchandise total\n$20.02") && text.includes("Incremental line cash\n$10.01"), "material preview recalculates exact cash");
    await saveHtml("08-material-preview-test-data.html");
    await screenshot("08-material-preview-test-data.png");
    transitionMark = ledgerMark();
    await submit(
      formScript("document.querySelector('form[method=\"post\"]')", {
        actor: SPOOF_ACTOR,
        review_token: REVIEW_TOKEN,
        material_confirmation_reason: "Synthetic confirmation of material quantity and cash exposure",
      }),
    );
    check((await waitForResponse(reviewPath, "POST", transitionMark))?.status === 200, "distinct material-risk confirmation POST returns HTTP 200");
    text = await body();
    check(text.includes("Distinct MATERIAL-risk confirmation recorded"), "material risk confirmation is a distinct durable action");
    const materialConfirmationId = await evaluate("document.querySelector('input[name=\"material_edit_confirmation_id\"]')?.value");
    check(/^\d+$/.test(materialConfirmationId), "material confirmation returns an identifier");
    transitionMark = ledgerMark();
    await submit(
      formScript("document.querySelector('form[method=\"post\"]')", {actor: SPOOF_ACTOR, review_token: REVIEW_TOKEN}),
    );
    await assertRedirect(transitionMark, "POST", reviewPath, `/monday-runs/${runId}`, "final recommendation review POST returns HTTP 303");
    text = await body();
    check(text.includes("EDIT_QUANTITY") && text.includes("12 unit(s)") && text.includes("reviewed line total $20.02"), "immutable reviewed quantity and economics are visible");
    await saveHtml("09-reviewed-test-data.html");
    await screenshot("09-reviewed-test-data.png");

    const buildPath = `/monday-runs/${runId}/build`;
    transitionMark = ledgerMark();
    await submit(
      formScript("[...document.forms].find((form) => (form.getAttribute('action') || '').endsWith('/build'))", {actor: SPOOF_ACTOR, review_token: REVIEW_TOKEN}),
    );
    check((await waitForResponse(buildPath, "POST", transitionMark))?.status === 200, "DRAFT build preview POST returns HTTP 200");
    text = await body();
    check(text.includes("Confirm vendor DRAFT economics") && text.includes("$20.02"), "vendor DRAFT preview shows the exact merchandise and total");
    check(text.includes("NOT_APPLICABLE"), "met synthetic minimum needs no fee disposition");
    const preBuildRun = await statusFetch(`/monday-runs/${runId}`);
    check(preBuildRun.status === 200 && preBuildRun.body.includes("Stage: <b>REVIEWED</b>") && !preBuildRun.body.includes("/artifacts/"), "DRAFT preview leaves the run REVIEWED with no artifacts");
    await saveHtml("10-draft-preview-test-data.html");
    await screenshot("10-draft-preview-test-data.png");
    transitionMark = ledgerMark();
    await submit(
      formScript("document.querySelector('form[method=\"post\"]')", {actor: SPOOF_ACTOR, review_token: REVIEW_TOKEN}),
    );
    await assertRedirect(transitionMark, "POST", buildPath, `/monday-runs/${runId}`, "confirmed DRAFT build POST returns HTTP 303");
    text = await body();
    check(text.includes("Stage: PACKET_BUILT") && text.includes("Synthetic Southern — DRAFT"), "one internal vendor DRAFT and packet reach terminal stage");
    check(text.includes("Grand totals:") && text.includes("internal DRAFT total $20.02"), "vendor and grand totals reconcile visibly");
    check(!text.includes("Release PO"), "no PO-release control is rendered");
    await saveHtml("11-built-test-data.html");
    await screenshot("11-built-test-data.png");

    const artifactLinks = await evaluate(`[
      ...document.querySelectorAll('a[href*="/artifacts/"]')
    ].map((anchor) => ({href:anchor.href,row:anchor.closest("tr").innerText}))`);
    check(artifactLinks.length === 2, "exactly one vendor CSV and one review packet are exposed", artifactLinks);
    const artifactDownloads = [];
    for (let index = 0; index < artifactLinks.length; index += 1) {
      artifactDownloads.push(await downloadFromClick(
        `document.querySelectorAll('a[href*="/artifacts/"]')[${index}].click()`,
        {url: artifactLinks[index].href, row: artifactLinks[index].row, kind: "monday-artifact"},
      ));
    }
    const downloadedFiles = (await waitForDownloads(4)).sort();
    check(downloadedFiles.some((name) => name.endsWith(".csv")), "Chromium downloaded the vendor CSV");
    check(downloadedFiles.some((name) => name.endsWith(".zip")), "Chromium downloaded the review ZIP");
    check(downloadedFiles.filter((name) => name.endsWith(".json")).length === 2, "Chromium downloaded both bounded mapping evidence records");

    const linksBeforeReplay = artifactLinks.map((item) => item.href);
    await navigate(`${BASE}/monday-runs/${runId}`);
    transitionMark = ledgerMark();
    await submit(
      formScript("[...document.forms].find((form) => (form.getAttribute('action') || '').endsWith('/build'))", {actor: SPOOF_ACTOR, review_token: REVIEW_TOKEN}),
    );
    await assertRedirect(transitionMark, "POST", buildPath, `/monday-runs/${runId}`, "DRAFT replay POST returns HTTP 303");
    const linksAfterReplay = await evaluate(`[
      ...document.querySelectorAll('a[href*="/artifacts/"]')
    ].map((anchor) => anchor.href)`);
    check(JSON.stringify(linksAfterReplay) === JSON.stringify(linksBeforeReplay), "DRAFT replay preserves artifact identities without duplication");
    await saveHtml("12-replay-test-data.html");

    const state = {
      unresolvedCandidateUrl: new URL(unresolved.href, BASE).href,
      validCandidateUrl: new URL(valid.href, BASE).href,
      sourceUrls: [unresolvedSourceHref, validSourceHref],
      runUrl: `${BASE}/monday-runs/${runId}`,
      runId,
      artifactUrls: linksAfterReplay,
      sourceDownloads,
      artifactDownloads,
      downloadedFiles: downloadedFiles.map((name) => ({
        name,
        bytes: fs.statSync(path.join(DOWNLOADS, name)).size,
        sha256: sha256File(path.join(DOWNLOADS, name)),
      })),
    };
    fs.writeFileSync(STATE_PATH, JSON.stringify(state, null, 2) + "\n", {mode: 0o600});
    Object.assign(results, state);
  } else if (phase === "phase2") {
    const state = JSON.parse(fs.readFileSync(STATE_PATH, "utf8"));
    const staleCookies = (await client.send("Network.getAllCookies")).cookies.filter(
      (cookie) => cookie.name === "buffalo_local_session",
    );
    check(staleCookies.length === 1, "the phase-one session cookie still exists before restart validation");
    const staleCookieValue = staleCookies[0].value;
    await navigate(state.runUrl);
    check((await body()).includes("Authentication required"), "process restart invalidates the old in-memory session");
    check(latestResponse(new URL(state.runUrl).pathname)?.status === 401, "stale pre-restart session returns HTTP 401", latestResponse(new URL(state.runUrl).pathname));
    await login();
    const replacementCookies = (await client.send("Network.getAllCookies")).cookies.filter(
      (cookie) => cookie.name === "buffalo_local_session",
    );
    check(replacementCookies.length === 1 && replacementCookies[0].value !== staleCookieValue, "post-restart login replaces the invalidated session token");

    const status = await jsonFetch("/supplier-mapping/status");
    check(
      status.body.review_batch_count === 2 &&
        status.body.candidate_count === 2 &&
        status.body.decision_count === 2 &&
        status.body.selection_event_count === 1 &&
        status.body.selection_head_count === 1 &&
        status.body.shadow_match_count === 1,
      "mapping decisions and one shadow selection survive restart",
      status.body,
    );
    await navigate(state.runUrl);
    let text = await body();
    check(text.includes("Stage: PACKET_BUILT") && text.includes("internal DRAFT total $20.02"), "reviewed run and DRAFT survive restart");
    const artifactLinks = await evaluate(`[
      ...document.querySelectorAll('a[href*="/artifacts/"]')
    ].map((anchor) => anchor.href)`);
    check(JSON.stringify(artifactLinks) === JSON.stringify(state.artifactUrls), "artifact identities survive restart");
    const artifactRechecks = [];
    for (const record of state.artifactDownloads) {
      const checked = await evaluate(`fetch(${JSON.stringify(record.url)}, {credentials:"same-origin"}).then(async (response) => {
        const bytes = await response.arrayBuffer();
        const digest = await crypto.subtle.digest("SHA-256", bytes);
        return {
          status: response.status,
          cacheControl: response.headers.get("cache-control"),
          contentDisposition: response.headers.get("content-disposition"),
          sha256: [...new Uint8Array(digest)].map((value) => value.toString(16).padStart(2, "0")).join(""),
        };
      })`);
      check(
        checked.status === 200 &&
          checked.cacheControl === "no-store" &&
          String(checked.contentDisposition).toLowerCase().includes("attachment") &&
          checked.sha256 === record.sha256,
        "browser re-fetches the same artifact bytes after restart",
        {url: record.url, expectedSha256: record.sha256, checked},
      );
      artifactRechecks.push({url: record.url, ...checked});
    }
    await saveHtml("13-after-restart-test-data.html");
    await screenshot("13-after-restart-test-data.png");

    await navigate(`${BASE}/supplier-mapping`);
    const logoutMark = ledgerMark();
    await submit(formScript("document.querySelector('form[action=\"/auth/logout\"]')"));
    await assertRedirect(logoutMark, "POST", "/auth/logout", "/auth/login", "logout POST returns HTTP 303");
    check((await currentUrl()) === `${BASE}/auth/login`, "logout returns to the local sign-in page");
    const loggedOutCookies = (await client.send("Network.getAllCookies")).cookies.filter(
      (cookie) => cookie.name === "buffalo_local_session",
    );
    check(loggedOutCookies.length === 0, "logout removes the local session cookie");
    const unauthenticated = [];
    for (const request of [
      {path: "/supplier-mapping", method: "GET"},
      {path: new URL(state.validCandidateUrl).pathname, method: "GET"},
      ...state.sourceUrls.map((url) => ({path: new URL(url).pathname, method: "GET"})),
      {path: new URL(state.artifactUrls[0]).pathname, method: "GET"},
      {path: "/supplier-mapping/intake", method: "POST"},
    ]) {
      const response = await statusFetch(request.path, {method: request.method});
      unauthenticated.push({...request, status: response.status});
    }
    check(unauthenticated.every((item) => item.status === 401), "logged-out list, detail, source, artifact, and write endpoints all refuse", unauthenticated);
    results.state = state;
    results.unauthenticated = unauthenticated;
    results.artifactRechecks = artifactRechecks;
  } else {
    throw new Error(`unknown browser audit phase: ${phase}`);
  }

  const allowed = new URL(BASE);
  const external = client.requests.filter((request) => {
    const target = new URL(request.url);
    return target.protocol !== allowed.protocol || target.hostname !== allowed.hostname || target.port !== allowed.port;
  });
  check(external.length === 0, "no browser HTTP request escaped the exact loopback origin", external);
  check(client.blockedRequests.length === 0, "no non-loopback request reached the network", client.blockedRequests);
  check(client.exceptions.length === 0, "no browser runtime exceptions", client.exceptions);
  check(client.consoleErrors.length === 0, "no browser console errors", client.consoleErrors);
  const protectedResponses = [
    ...client.responses.filter((response) => response.url.startsWith(BASE)),
    ...client.redirects.filter((response) => response.url.startsWith(BASE)),
  ];
  check(
    protectedResponses.every((response) => response.cacheControl === "no-store"),
    "every observed application response is no-store",
    protectedResponses.filter((response) => response.cacheControl !== "no-store"),
  );
  results.requestCount = client.requests.length;
  results.responseCount = client.responses.length;
  results.redirectCount = client.redirects.length;
  results.runtimeExceptions = client.exceptions.length;
  results.consoleErrors = client.consoleErrors.length;
  results.completedAt = new Date().toISOString();
}

const client = await connect();
try {
  await audit(client);
  const filename = path.join(EVIDENCE, `${phase}-browser-results-test-data.json`);
  fs.writeFileSync(filename, JSON.stringify(results, null, 2) + "\n", {mode: 0o600});
  console.log(JSON.stringify({phase, assertionCount: results.assertions.length, passed: true}));
} catch (error) {
  results.error = String(error.stack || error);
  results.safeRecentRequests = client.requests.slice(-12);
  results.safeRecentResponses = client.responses.slice(-12);
  const filename = path.join(EVIDENCE, `${phase}-browser-results-test-data.json`);
  fs.writeFileSync(filename, JSON.stringify(results, null, 2) + "\n", {mode: 0o600});
  throw error;
} finally {
  client.close();
}

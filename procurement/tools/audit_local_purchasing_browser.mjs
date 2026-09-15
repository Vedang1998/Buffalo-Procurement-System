#!/usr/bin/env node
// TEST DATA — NOT FOR ORDERING: real Chromium/CDP acceptance harness.

import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";

const [phase, BASE, CDP, EVIDENCE, DOWNLOADS, AUTH_SECRET_PATH, REVIEW_TOKEN_PATH, PRICE_TOKEN_PATH, PRICE_BOOK_PATH, STATE_PATH] =
  process.argv.slice(2);

if (!phase || !BASE || !CDP || !EVIDENCE || !DOWNLOADS || !AUTH_SECRET_PATH || !REVIEW_TOKEN_PATH || !PRICE_TOKEN_PATH || !PRICE_BOOK_PATH || !STATE_PATH) {
  throw new Error("browser audit arguments are incomplete");
}

const AUTH_SECRET = fs.readFileSync(AUTH_SECRET_PATH, "utf8").replace(/\n$/, "");
const REVIEW_TOKEN = fs.readFileSync(REVIEW_TOKEN_PATH, "utf8").replace(/\n$/, "");
const PRICE_TOKEN = fs.readFileSync(PRICE_TOKEN_PATH, "utf8").replace(/\n$/, "");
const SPOOF_ACTOR = "spoofed-browser-actor-must-be-ignored";
const STALE_V1_RUN_ID = "00000000-0000-4000-8000-000000000901";
const RETIREMENT_REASON = "Synthetic retired forecast method requires V2 re-preparation";
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
  const setFileInput = async (selector, filename) => {
    await client.send("DOM.enable");
    const document = await client.send("DOM.getDocument", {depth: 2});
    const found = await client.send("DOM.querySelector", {
      nodeId: document.root.nodeId,
      selector,
    });
    if (!found.nodeId) throw new Error(`file input not found: ${selector}`);
    await client.send("DOM.setFileInputFiles", {nodeId: found.nodeId, files: [filename]});
  };
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

  if (phase === "price") {
    await navigate(`${BASE}/price-books`);
    check((await body()).includes("Authentication required"), "unauthenticated price-book list is refused");
    await login();
    await navigate(`${BASE}/price-books`);
    let text = await body();
    check(
      text.includes("SYNTHETIC REPLACEMENT PRICE BOOK") &&
        text.includes("TEST DATA / NO REAL AUTHORITY"),
      "price-book page exposes only the registered synthetic replacement form",
    );
    await setFileInput("#synthetic-price-replacement input[type=file]", PRICE_BOOK_PATH);
    let transitionMark = ledgerMark();
    await submit(
      formScript("document.querySelector('#synthetic-price-replacement form')", {
        actor: SPOOF_ACTOR,
        review_token: PRICE_TOKEN,
      }),
    );
    const batchUrl = await currentUrl();
    const batchId = new URL(batchUrl).pathname.split("/").pop();
    check(/^[0-9a-f-]{36}$/.test(batchId), "browser upload creates one server-issued price-book batch", batchId);
    await assertRedirect(transitionMark, "POST", "/price-books/import", `/price-books/${batchId}`, "declared upload returns the exact batch redirect");
    text = await body();
    check(
      text.includes("SYNTHETIC DECLARED PRICE EVIDENCE") &&
        text.includes("36.0000") && text.includes("6.0000") &&
        text.includes("30.0000") && text.includes("5.0000") &&
        text.includes("72.0000") && text.includes("12.0000") &&
        text.includes("60.0000") && text.includes("10.0000"),
      "browser detail exposes all four distinctive uploaded BASE/BREAK tiers",
    );
    await saveHtml("00-price-upload-detail-test-data.html");
    await screenshot("00-price-upload-detail-test-data.png");

    transitionMark = ledgerMark();
    await submit(
      formScript("document.querySelector('form[action$=\"/confirmation-preview\"]')", {
        confirmation_idempotency_key: "browser-price-confirmation-v1",
        actor: SPOOF_ACTOR,
        warning_review_reason: "Reviewed four fabricated synthetic price changes.",
        review_token: PRICE_TOKEN,
      }),
    );
    check((await waitForResponse(`/price-books/${batchId}/confirmation-preview`, "POST", transitionMark))?.status === 200, "price confirmation preview returns HTTP 200");
    const confirmation = await evaluate(
      "JSON.parse(document.querySelector('#declared-price-confirmation-json').textContent)",
    );
    check(
      confirmation.contract === "BUFFALO_SYNTHETIC_PRICE_CONFIRMATION_PREVIEW_V1" &&
        confirmation.price_book_batch_id === batchId &&
        confirmation.observation_at === "2026-09-16T14:00:00+00:00" &&
        confirmation.application_at === "2026-10-01T14:00:00+00:00" &&
        confirmation.monday_evaluation_at === "2026-10-05T14:00:00+00:00" &&
        confirmation.candidate_tiers.length === 4 &&
        confirmation.current_tiers.length === 4 &&
        confirmation.commercial_authority === false &&
        confirmation.real_price_approval === false,
      "separate confirmation binds source, current diff, clocks, and zero real authority",
      confirmation,
    );
    await saveHtml("00-price-confirmation-preview-test-data.html");
    await screenshot("00-price-confirmation-preview-test-data.png");
    transitionMark = ledgerMark();
    await submit(
      formScript("document.querySelector('form[action$=\"/confirm\"]')", {
        review_token: PRICE_TOKEN,
      }),
    );
    await assertRedirect(transitionMark, "POST", `/price-books/${batchId}/confirm`, `/price-books/${batchId}`, "separate price confirmation returns the exact batch redirect");
    text = await body();
    check(
      text.includes("VERIFIED_FUTURE") && text.includes("Guarded effective-boundary APPLY"),
      "confirmed upload remains FUTURE and exposes a separately guarded APPLY",
    );
    const price = {
      batchId,
      batchUrl,
      confirmationPreviewSha256: confirmation.preview_sha256,
      rawContentSha256: confirmation.raw_content_sha256,
    };
    fs.writeFileSync(STATE_PATH, JSON.stringify({price}, null, 2) + "\n", {mode: 0o600});
    results.price = price;
  } else if (phase === "phase1") {
    const priorState = JSON.parse(fs.readFileSync(STATE_PATH, "utf8"));
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

    await navigate(priorState.price.batchUrl);
    let text = await body();
    check(text.includes("VERIFIED_FUTURE"), "confirmed FUTURE price survives the backup boundary");
    let transitionMark = ledgerMark();
    await submit(
      formScript("document.querySelector('form[action$=\"/apply-preview\"]')", {
        apply_idempotency_key: "browser-price-apply-v1",
        actor: SPOOF_ACTOR,
        review_token: PRICE_TOKEN,
      }),
    );
    check((await waitForResponse(`/price-books/${priorState.price.batchId}/apply-preview`, "POST", transitionMark))?.status === 200, "guarded APPLY preview returns HTTP 200");
    const applyPreview = await evaluate(
      "JSON.parse(document.querySelector('#synthetic-price-apply-json').textContent)",
    );
    check(
      applyPreview.contract === "BUFFALO_SYNTHETIC_PRICE_APPLY_PREVIEW_V1" &&
        applyPreview.price_book_batch_id === priorState.price.batchId &&
        applyPreview.expected_current_row_count === 4 &&
        applyPreview.resulting_current_row_count === 4 &&
        applyPreview.application_at === "2026-10-01T14:00:00+00:00" &&
        applyPreview.commercial_authority === false &&
        applyPreview.real_price_approval === false,
      "APPLY preview binds the V2 recovery proof, exact scope counts, and zero real authority",
      applyPreview,
    );
    await saveHtml("00-price-apply-preview-test-data.html");
    await screenshot("00-price-apply-preview-test-data.png");
    transitionMark = ledgerMark();
    await submit(
      formScript("document.querySelector('form[action$=\"/apply\"]')", {
        review_token: PRICE_TOKEN,
      }),
    );
    await assertRedirect(transitionMark, "POST", `/price-books/${priorState.price.batchId}/apply`, `/price-books/${priorState.price.batchId}`, "guarded APPLY returns the exact batch redirect");
    text = await body();
    check(text.includes("APPLIED_CURRENT"), "browser APPLY makes only the confirmed synthetic batch CURRENT");
    priorState.price.applyPreview = applyPreview;

    for (const [pathname, label] of [
      ["/vendor-rules", "vendor-rule operator page"],
      ["/price-books", "price-book operator page"],
      ["/reconciliation/investigation", "identity-investigation page"],
    ]) {
      await navigate(`${BASE}${pathname}`);
      check(
        (await body()).includes("TEST DATA — NOT FOR ORDERING"),
        `${label} retains the synthetic/internal-DRAFT boundary`,
      );
      check(latestResponse(pathname)?.status === 200, `${label} returns HTTP 200`);
    }
    for (const endpoint of ["/health/full", "/foundation/status"]) {
      const status = await jsonFetch(endpoint);
      check(status.status === 200, `${endpoint} returns HTTP 200`);
      check(
        status.body.runtime_mode === "SYNTHETIC_DEMO" &&
          status.body.safety_label === "TEST DATA — NOT FOR ORDERING" &&
          status.body.po_generation_enabled === false &&
          status.body.operational_authority?.production_release_authorized === false &&
          status.body.operational_authority?.shopify_actions_authorized === false &&
          status.body.operational_authority?.order_actions_authorized === false,
        `${endpoint} distinguishes canonical facts from zero synthetic operational authority`,
        status.body,
      );
    }

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
    text = await body();
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

    transitionMark = ledgerMark();
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
    const offerId = await evaluate("[...document.querySelectorAll('form[action$=\"/decision\"] select[name=\"existing_offer_id\"] option[value]:not([value=\"\"])')].find((option) => option.textContent.includes('SUP-001'))?.value");
    check(/^\d+$/.test(offerId), "exact SUP-001 offer identifier is server-rendered");
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
        effective_from: "2026-10-05",
        reason: "Synthetic routine offer shadow selection",
      }),
    );
    check((await waitForResponse(new URL(valid.href, BASE).pathname + "/selection", "POST", transitionMark))?.status === 200, "selection preview POST returns HTTP 200");
    text = await body();
    check(text.includes("Confirm routine offer selection"), "selection first request is a separate confirmation preview");
    check(text.includes("2026-10-05") && text.includes("Synthetic routine offer shadow selection"), "selection preview visibly binds date and reason");
    status = await jsonFetch("/supplier-mapping/status");
    check(status.body.selection_event_count === 0 && status.body.selection_head_count === 0, "selection preview writes no event or head", status.body);
    await saveHtml("04-selection-preview-test-data.html");
    await screenshot("04-selection-preview-test-data.png");
    transitionMark = ledgerMark();
    await submit(formScript("document.querySelector('form[method=\"post\"]')"));
    await assertRedirect(transitionMark, "POST", new URL(valid.href, BASE).pathname + "/selection", new URL(valid.href, BASE).pathname, "confirmed selection POST returns HTTP 303");
    text = await body();
    check(text.includes("legacy comparison HEAD_STALE_OR_INELIGIBLE"), "current-date shadow diagnostics keep the future-effective selection non-operational");
    status = await jsonFetch("/supplier-mapping/status");
    check(
      status.body.decision_count === 2 &&
        status.body.selection_event_count === 1 &&
        status.body.selection_head_count === 1 &&
        status.body.shadow_match_count === 0 &&
        status.body.shadow_nonmatch_count === 1 &&
        status.body.recommendation_cutover_enabled === false &&
        status.body.offer_activation_enabled === false,
      "mapping stays SHADOW_ONLY while the distinct synthetic Monday consumer is isolated and real activation/cutover stay disabled",
      status.body,
    );
    await saveHtml("05-shadow-match-test-data.html");
    await screenshot("05-shadow-match-test-data.png");

    const inventory = await jsonFetch("/inventory-snapshots/status?as_of=2026-10-05");
    check(inventory.status === 200 && inventory.body.status === "PASS", "same-day synthetic inventory status is PASS", inventory.body);

    await navigate(`${BASE}/monday-runs`);
    text = await body();
    check(text.includes("TEST DATA — NOT FOR ORDERING") && text.includes("No release or Shopify action is available"), "Monday page is visibly DRAFT-only");
    check(
      text.includes(STALE_V1_RUN_ID) &&
        text.includes("EMERGENCY_TRANSPARENT_V1") &&
        text.includes("PREPARING"),
      "initializer exposes exactly one active fabricated V1 run requiring retirement",
    );
    check(
      (await evaluate(`[
        ...document.querySelectorAll("tbody tr")
      ].filter((row) => row.textContent.includes("EMERGENCY_TRANSPARENT_V1")).length`)) === 1,
      "Monday list contains exactly one fabricated V1 lifecycle row",
    );
    const staleV1Path = `/monday-runs/${STALE_V1_RUN_ID}`;
    const staleRetirementPath = `${staleV1Path}/retire-stale-forecast`;
    await navigate(`${BASE}${staleV1Path}`);
    text = await body();
    check(
      text.includes("FORECAST_METHOD_RETIRED_REPREPARATION_REQUIRED") &&
        text.includes("EMERGENCY_TRANSPARENT_V1") &&
        text.includes("Status: RUNNING") &&
        text.includes("stage: PREPARING"),
      "stale V1 detail is typed, visible, and still active before confirmation",
    );
    check(
      (await evaluate("Boolean(document.querySelector('form[action$=\"/retire-stale-forecast\"]'))")) &&
        !(await evaluate("document.body.innerHTML.includes('/recommendations/')")) &&
        !(await evaluate("document.body.innerHTML.includes('/blockers/')")) &&
        !(await evaluate("document.body.innerHTML.includes('/build')")),
      "stale V1 exposes only the bounded retirement mutation",
    );
    const formHeaders = {"Content-Type": "application/x-www-form-urlencoded"};
    const staleBuild = await statusFetch(`${staleV1Path}/build`, {
      method: "POST",
      headers: formHeaders,
      body: new URLSearchParams({actor: SPOOF_ACTOR, review_token: REVIEW_TOKEN}).toString(),
    });
    check(
      staleBuild.status === 409 && staleBuild.body.includes("FORECAST_METHOD_RETIRED_REPREPARATION_REQUIRED"),
      "stale V1 DRAFT write is refused with the exact retired-method state",
      staleBuild,
    );
    const stalePrepare = await statusFetch("/monday-runs/prepare", {
      method: "POST",
      headers: formHeaders,
      body: new URLSearchParams({
        business_date: "2026-10-05",
        idempotency_key: "browser-stale-v1-must-block",
        variant_ids: "1001,2002",
        actor: SPOOF_ACTOR,
        review_token: REVIEW_TOKEN,
      }).toString(),
    });
    check(
      stalePrepare.status === 409 && stalePrepare.body.includes("FORECAST_METHOD_RETIRED_REPREPARATION_REQUIRED"),
      "active V1 date claim blocks V2 preparation without recomputation",
      stalePrepare,
    );

    transitionMark = ledgerMark();
    await submit(
      formScript("document.querySelector('form[action$=\"/retire-stale-forecast\"]')", {
        reason: RETIREMENT_REASON,
        review_token: REVIEW_TOKEN,
      }),
    );
    check(
      (await waitForResponse(staleRetirementPath, "POST", transitionMark))?.status === 200,
      "retirement preview POST returns HTTP 200",
    );
    text = await body();
    const retirementPreview = await evaluate(`Object.fromEntries(
      [...document.querySelectorAll("dl dt")].map((item) => [
        item.textContent.trim(), item.nextElementSibling?.textContent.trim() || ""
      ])
    )`);
    check(
      text.includes("Confirm exact V1 retirement") &&
        text.includes(RETIREMENT_REASON) &&
        text.includes("RUNNING/PREPARING → FAILED/FAILED") &&
        text.includes("0 / 0") &&
        text.includes("synthetic:owner-browser:01"),
      "retirement preview visibly binds the exact actor, reason, transition, and zero effects",
    );
    check(
      retirementPreview.Run === STALE_V1_RUN_ID &&
        retirementPreview["Business date"] === "2026-10-05" &&
        /^[0-9a-f]{64}$/.test(retirementPreview["Frozen fingerprint"]) &&
        retirementPreview["Retired method"] === "EMERGENCY_TRANSPARENT_V1" &&
        retirementPreview.Transition === "RUNNING/PREPARING → FAILED/FAILED" &&
        retirementPreview["Purchase orders / artifacts"] === "0 / 0" &&
        retirementPreview["Authenticated actor"] === "synthetic:owner-browser:01" &&
        retirementPreview.Reason === RETIREMENT_REASON,
      "retirement preview definition list binds every exact confirmation fact",
      retirementPreview,
    );
    const retirementInputFingerprint = retirementPreview["Frozen fingerprint"];
    const retirementConfirmationSha = await evaluate(
      "document.querySelector('input[name=expected_confirmation_sha256]')?.value",
    );
    check(/^[0-9a-f]{64}$/.test(retirementConfirmationSha), "retirement preview supplies one exact confirmation SHA-256");
    check(
      retirementPreview["Confirmation SHA-256"] === retirementConfirmationSha,
      "visible and submitted retirement confirmation hashes are identical",
    );
    const wrongRetirementHash = await statusFetch(staleRetirementPath, {
      method: "POST",
      headers: formHeaders,
      body: new URLSearchParams({
        reason: RETIREMENT_REASON,
        expected_confirmation_sha256: "f".repeat(64),
        review_token: REVIEW_TOKEN,
        actor: SPOOF_ACTOR,
      }).toString(),
    });
    check(
      wrongRetirementHash.status === 409 &&
        wrongRetirementHash.body.includes("retirement preview changed; review and confirm again"),
      "wrong well-shaped retirement confirmation is refused without fallback",
      wrongRetirementHash,
    );
    const stillActiveV1 = await statusFetch(staleV1Path);
    check(
      stillActiveV1.status === 200 &&
        stillActiveV1.body.includes("Status: <b>RUNNING</b>") &&
        stillActiveV1.body.includes("stage: <b>PREPARING</b>") &&
        !stillActiveV1.body.includes("/artifacts/"),
      "retirement preview is write-free and leaves the V1 run active with no artifacts",
    );
    await saveHtml("05a-v1-retirement-preview-test-data.html");
    await screenshot("05a-v1-retirement-preview-test-data.png");
    transitionMark = ledgerMark();
    await submit(formScript("document.querySelector('form[method=\"post\"]')", {review_token: REVIEW_TOKEN}));
    await assertRedirect(
      transitionMark,
      "POST",
      staleRetirementPath,
      staleV1Path,
      "confirmed immutable V1 retirement POST returns HTTP 303",
    );
    text = await body();
    check(
      text.includes("Status: FAILED") &&
        text.includes("stage: FAILED") &&
        text.includes("EMERGENCY_TRANSPARENT_V1") &&
        !text.includes("retire-stale-forecast") &&
        !text.includes("/build"),
      "confirmed V1 remains visible, immutable, and mutation-free after the date is released",
    );
    check(
      !(await evaluate("Boolean(document.querySelector('form[action$=\"/retire-stale-forecast\"]'))")) &&
        !(await evaluate("Boolean(document.querySelector('form[action*=\"/recommendations/\"]'))")) &&
        !(await evaluate("Boolean(document.querySelector('form[action*=\"/blockers/\"]'))")) &&
        !(await evaluate("Boolean(document.querySelector('form[action$=\"/build\"]'))")) &&
        !(await evaluate("Boolean(document.querySelector('a[href*=\"/artifacts/\"]'))")),
      "retired V1 detail exposes no mutation control",
    );
    await saveHtml("05b-v1-retired-test-data.html");
    await screenshot("05b-v1-retired-test-data.png");

    transitionMark = ledgerMark();
    await submit(`(() => {
      const form = document.createElement("form");
      form.method = "post";
      form.action = ${JSON.stringify(staleRetirementPath)};
      const values = ${JSON.stringify({
        reason: RETIREMENT_REASON,
        expected_confirmation_sha256: retirementConfirmationSha,
        review_token: REVIEW_TOKEN,
        actor: SPOOF_ACTOR,
      })};
      for (const [name, value] of Object.entries(values)) {
        const input = document.createElement("input");
        input.name = name;
        input.value = value;
        form.appendChild(input);
      }
      document.body.appendChild(form);
      form.requestSubmit();
      return true;
    })()`);
    await assertRedirect(
      transitionMark,
      "POST",
      staleRetirementPath,
      staleV1Path,
      "exact V1 retirement replay returns the same HTTP 303 transition",
    );
    check((await body()).includes("Status: FAILED"), "retirement replay creates no replacement state");

    await navigate(`${BASE}/monday-runs`);
    transitionMark = ledgerMark();
    await submit(
      formScript("document.querySelector('form[action=\"monday-runs/prepare\"]')", {
        business_date: "2026-10-05",
        idempotency_key: "browser-synthetic-20261005-v2",
        variant_ids: "1001,2002",
        actor: SPOOF_ACTOR,
        review_token: REVIEW_TOKEN,
      }),
    );
    const preparedUrl = await currentUrl();
    const runMatch = preparedUrl.match(/\/monday-runs\/([0-9a-f-]{36})$/);
    check(Boolean(runMatch), "prepare redirects to a concrete frozen run", preparedUrl);
    const runId = runMatch[1];
    check(runId !== STALE_V1_RUN_ID, "V2 preparation creates a distinct immutable run identity");
    await assertRedirect(transitionMark, "POST", "/monday-runs/prepare", `/monday-runs/${runId}`, "prepare POST returns HTTP 303");
    text = await textContent();
    check(
      text.includes("2002") && text.includes("1001") &&
        text.includes("EMERGENCY_TRANSPARENT_V2") && text.includes("stage: AWAITING_REVIEW"),
      "distinct V2 preparation retains mixed eligible and blocked variants",
    );
    check(
      text.includes("SYNTHETIC SELECTED OFFER — TEST DATA / NO REAL AUTHORITY"),
      "selected-input run carries the exact local synthetic authority warning",
    );
    const selectedOfferInputEvidence = await evaluate(
      "JSON.parse(document.querySelector('pre.selected-offer-input-json').textContent)",
    );
    check(
      selectedOfferInputEvidence.contract === "SYNTHETIC_CONFIRMED_SELECTION_V1" &&
        selectedOfferInputEvidence.authority === "SYNTHETIC_TEST_ONLY" &&
        selectedOfferInputEvidence.historical_reconstruction === false &&
        selectedOfferInputEvidence.selected_offer.offer_id === Number(offerId) &&
        selectedOfferInputEvidence.selected_offer.supplier_sku === "SUP-001" &&
        selectedOfferInputEvidence.selected_offer.raw_pack === "6x750ML" &&
        selectedOfferInputEvidence.legacy_active_standard_offer_comparison.active_standard_offer_count === 2 &&
        selectedOfferInputEvidence.legacy_active_standard_offer_comparison.authority === "COMPARISON_ONLY_NO_AUTHORITY" &&
        selectedOfferInputEvidence.applicable_price_ladder.rows.length === 2 &&
        /^[0-9a-f]{64}$/.test(selectedOfferInputEvidence.applicable_price_ladder.sha256),
      "browser parses exact selected head, offer, comparison, and frozen ladder evidence",
      selectedOfferInputEvidence,
    );
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
    const demandEvidence = await evaluate(
      "JSON.parse(document.querySelector('pre.demand-evidence-json').textContent)",
    );
    check(
      demandEvidence.contract === "BUFFALO_EMERGENCY_DEMAND_EVIDENCE_V2" &&
        demandEvidence.method_version === "EMERGENCY_TRANSPARENT_V2" &&
        demandEvidence.history_start === "2026-07-13" &&
        demandEvidence.history_end === "2026-10-04" &&
        demandEvidence.calendar_days === 84 &&
        demandEvidence.daily.length === 84 &&
        demandEvidence.daily.every((item) => item.inventory_state === "UNKNOWN") &&
        demandEvidence.snapshot_groups.length === 1 &&
        demandEvidence.snapshot_groups[0].compatibility_status ===
          "COMPATIBLE_POINT_IN_TIME_EVIDENCE" &&
        demandEvidence.snapshot_groups[0].aggregate_available_quantity === "3.0000" &&
        demandEvidence.availability_summary.unknown_days === 84 &&
        demandEvidence.availability_summary.proven_full_day_stockout_days === 0 &&
        demandEvidence.statuses.model_selection_status === "NOT_VALIDATED" &&
        demandEvidence.statuses.safety_stock_status === "NOT_CALCULATED",
      "browser parses the exact evidence-correct V2 demand object",
      demandEvidence,
    );
    for (const required of [
      "SUP-001",
      "750ML",
      "6x750ML",
      "2026-10-05",
      "synthetic-location-001",
      "2.0000",
      "4.99",
      "2.99",
      "59.92",
      "1.000000",
      "baseline need 3",
      "1 case(s) + 0 loose",
      "BUFFALO_EMERGENCY_DEMAND_EVIDENCE_V2",
      "POINT_IN_TIME_SNAPSHOT_ONLY",
      "STOCKOUT_CENSORING_EVIDENCE_UNAVAILABLE",
      "EVIDENCE_UNAVAILABLE",
      "NOT_CALCULATED",
      "UNKNOWN",
      "COMPATIBLE_POINT_IN_TIME_EVIDENCE",
      "synthetic-location-002",
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
    check(text.includes("NORMAL") && text.includes("Recalculated line total\n$36.00"), "one-case quantity preview is NORMAL and recalculates to uploaded BASE $36.00");
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
    check(text.includes("Merchandise total\n$60.00") && text.includes("Incremental line cash\n$24.00"), "material preview crosses to uploaded BREAK and recalculates exact cash");
    const finalPriceTier = await evaluate(
      "JSON.parse(document.querySelector('pre.final-price-tier-json').textContent)",
    );
    check(
      Number.isInteger(finalPriceTier.price_id) &&
        Number.isInteger(finalPriceTier.run_price_snapshot_id) &&
        finalPriceTier.level_type === "BREAK" &&
        finalPriceTier.break_qty === "2.0000" &&
        finalPriceTier.break_unit === "CS" &&
        finalPriceTier.case_price === "30.0000" &&
        finalPriceTier.unit_price === "5.0000" &&
        finalPriceTier.source_price_book_batch_id === priorState.price.batchId &&
        Number.isInteger(finalPriceTier.source_price_book_row_number) &&
        typeof finalPriceTier.supplier_price_authority_event_id === "string" &&
        finalPriceTier.price_ladder_sha256 === selectedOfferInputEvidence.applicable_price_ladder.sha256,
      "material preview binds the exact selected BREAK source and run snapshot identities",
      finalPriceTier,
    );
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
    check(text.includes("EDIT_QUANTITY") && text.includes("12 unit(s)") && text.includes("reviewed line total $60.00"), "immutable reviewed quantity and uploaded BREAK economics are visible");
    const reviewedFinalPriceTier = await evaluate(
      "JSON.parse(document.querySelector('pre.final-price-tier-json').textContent)",
    );
    check(
      JSON.stringify(reviewedFinalPriceTier) === JSON.stringify(finalPriceTier),
      "immutable review preserves the exact final price-tier identity",
      reviewedFinalPriceTier,
    );
    await saveHtml("09-reviewed-test-data.html");
    await screenshot("09-reviewed-test-data.png");

    const buildPath = `/monday-runs/${runId}/build`;
    transitionMark = ledgerMark();
    await submit(
      formScript("[...document.forms].find((form) => (form.getAttribute('action') || '').endsWith('/build'))", {actor: SPOOF_ACTOR, review_token: REVIEW_TOKEN}),
    );
    check((await waitForResponse(buildPath, "POST", transitionMark))?.status === 200, "DRAFT build preview POST returns HTTP 200");
    text = await body();
    check(text.includes("Confirm vendor DRAFT economics") && text.includes("$60.00"), "vendor DRAFT preview shows uploaded BREAK merchandise and exact total");
    check(text.includes("NOT_APPLICABLE"), "above-minimum synthetic DRAFT has no invented fee disposition");
    const previewTierLines = await evaluate(
      "JSON.parse(document.querySelector('pre.draft-preview-final-price-tiers-json').textContent)",
    );
    check(
      previewTierLines.length === 1 &&
        previewTierLines[0].supplier_sku === "SUP-001" &&
        JSON.stringify(previewTierLines[0].final_price_tier) === JSON.stringify(finalPriceTier),
      "DRAFT preview preserves selected offer and final tier evidence",
      previewTierLines,
    );
    const preBuildRun = await statusFetch(`/monday-runs/${runId}`);
    check(preBuildRun.status === 200 && preBuildRun.body.includes("stage: <b>REVIEWED</b>") && !preBuildRun.body.includes("/artifacts/"), "DRAFT preview leaves the run REVIEWED with no artifacts");
    await saveHtml("10-draft-preview-test-data.html");
    await screenshot("10-draft-preview-test-data.png");
    transitionMark = ledgerMark();
    await submit(
      formScript("document.querySelector('form[method=\"post\"]')", {actor: SPOOF_ACTOR, review_token: REVIEW_TOKEN}),
    );
    await assertRedirect(transitionMark, "POST", buildPath, `/monday-runs/${runId}`, "confirmed DRAFT build POST returns HTTP 303");
    text = await body();
    check(text.includes("stage: PACKET_BUILT") && text.includes("Synthetic Southern — DRAFT"), "one internal vendor DRAFT and packet reach terminal stage");
    check(text.includes("Grand totals:") && text.includes("internal DRAFT total $60.00"), "vendor and grand totals reconcile visibly");
    check(!text.includes("Release PO"), "no PO-release control is rendered");
    const builtFinalPriceTier = await evaluate(
      "JSON.parse(document.querySelector('pre.draft-final-price-tier-json').textContent)",
    );
    check(
      JSON.stringify(builtFinalPriceTier) === JSON.stringify(finalPriceTier),
      "built internal DRAFT preserves the exact selected final tier",
      builtFinalPriceTier,
    );
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
      price: priorState.price,
      unresolvedCandidateUrl: new URL(unresolved.href, BASE).href,
      validCandidateUrl: new URL(valid.href, BASE).href,
      sourceUrls: [unresolvedSourceHref, validSourceHref],
      runUrl: `${BASE}/monday-runs/${runId}`,
      runId,
      selectedOfferId: Number(offerId),
      selectedOfferInputEvidence,
      finalPriceTier,
      retiredRunId: STALE_V1_RUN_ID,
      retirementConfirmationSha,
      retirementInputFingerprint,
      retirementReason: RETIREMENT_REASON,
      demandEvidence,
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
        status.body.shadow_match_count === 0 &&
        status.body.shadow_nonmatch_count === 1,
      "mapping decisions and one ambiguous-legacy selection survive restart",
      status.body,
    );
    await navigate(state.runUrl);
    let text = await body();
    check(text.includes("stage: PACKET_BUILT") && text.includes("internal DRAFT total $60.00"), "reviewed uploaded-price run and DRAFT survive restart");
    const restartedSelectedEvidence = await evaluate(
      "JSON.parse(document.querySelector('pre.selected-offer-input-json').textContent)",
    );
    const restartedFinalTier = await evaluate(
      "JSON.parse(document.querySelector('pre.draft-final-price-tier-json').textContent)",
    );
    check(
      JSON.stringify(restartedSelectedEvidence) === JSON.stringify(state.selectedOfferInputEvidence) &&
        JSON.stringify(restartedFinalTier) === JSON.stringify(state.finalPriceTier),
      "selected offer lineage and final tier survive restart byte-semantically unchanged",
    );
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

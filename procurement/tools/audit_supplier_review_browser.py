#!/usr/bin/env python3
"""Run a synthetic, network-blocked Chromium audit of the A1 review browser."""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from urllib.request import urlopen

from procurement_os.supplier_mapping_review import render_review_html
from procurement_os.supplier_review_real_v5 import A1_PACKAGE_KIND, A1_REVIEW_LABEL


_CDP_AUDIT = r'''
const [endpoint, reportUrl] = process.argv.slice(1);
const sleep = milliseconds => new Promise(resolve => setTimeout(resolve, milliseconds));

class Client {
  constructor(url) {
    this.socket = new WebSocket(url);
    this.nextId = 1;
    this.pending = new Map();
    this.waiters = new Map();
    this.httpRequests = [];
    this.exceptions = [];
    this.consoleErrors = [];
  }
  async open() {
    await new Promise((resolve, reject) => {
      this.socket.onopen = resolve;
      this.socket.onerror = reject;
    });
    this.socket.onmessage = event => {
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
        const url = message.params.request.url;
        if (/^https?:/i.test(url)) this.httpRequests.push(url);
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
  wait(method, timeout = 15000) {
    return new Promise((resolve, reject) => {
      const queue = this.waiters.get(method) || [];
      const timer = setTimeout(() => reject(new Error(`timeout waiting for ${method}`)), timeout);
      queue.push({resolve, reject, timer});
      this.waiters.set(method, queue);
    });
  }
  close() { this.socket.close(); }
}

const targetList = await (await fetch(`${endpoint}/json/list`)).json();
const target = targetList.find(item => item.type === "page");
if (!target) throw new Error("no Chromium page target");
const client = new Client(target.webSocketDebuggerUrl);
await client.open();
await client.send("Page.enable");
await client.send("Runtime.enable");
await client.send("Network.enable");

const evaluate = async expression => {
  const response = await client.send("Runtime.evaluate", {
    expression,
    returnByValue: true,
    awaitPromise: true,
  });
  if (response.exceptionDetails) {
    throw new Error(response.exceptionDetails.exception?.description || response.exceptionDetails.text);
  }
  return response.result.value;
};
const checks = [];
const check = (condition, name, detail = null) => {
  if (!condition) throw new Error(`ASSERTION FAILED: ${name}: ${JSON.stringify(detail)}`);
  checks.push({name, detail, passed: true});
};
const loaded = client.wait("Page.loadEventFired");
await client.send("Page.navigate", {url: reportUrl});
await loaded;
await sleep(150);

const setSearch = async value => {
  await evaluate(`(() => {
    const input = document.getElementById("catalog-search");
    input.value = ${JSON.stringify(value)};
    input.dispatchEvent(new Event("input", {bubbles: true}));
  })()`);
  await sleep(30);
};
const pageState = () => evaluate(`(() => ({
  ids: [...document.querySelectorAll("#catalog-results button")].map(
    button => button.textContent.split(" — ")[0]
  ),
  count: document.getElementById("catalog-count").textContent,
  previousDisabled: document.getElementById("catalog-previous").disabled,
  nextDisabled: document.getElementById("catalog-next").disabled
}))()`);

await setSearch("variant-0000");
await evaluate('document.querySelector("#catalog-results button").click()');
await sleep(30);
const hostile = await evaluate(`(() => ({
  cards: document.querySelectorAll("#catalog-detail .offer-card").length,
  hrefs: [...document.querySelectorAll("#catalog-detail a")].map(a => a.getAttribute("href")),
  sentinel: globalThis.__hrefExecuted === true,
  scripts: document.scripts.length
}))()`);
check(hostile.cards === 5, "all hostile-link offers passed through detail rendering", hostile);
check(
  JSON.stringify(hostile.hrefs) === JSON.stringify(["../original_sources/Fixture%20Book.pdf#page=2"]),
  "only the canonical verified local PDF href became an anchor",
  hostile.hrefs,
);
check(hostile.sentinel === false, "javascript href did not execute");
check(hostile.scripts === 2, "embedded hostile markup created no script element", hostile.scripts);

await setSearch("fixture-shared");
const visited = [];
let state = await pageState();
check(state.ids.length === 100, "first page is bounded to 100", state);
check(state.count === "250 matching Variants; showing 1-100; page 1/3", "first-page count is honest", state.count);
check(state.previousDisabled && !state.nextDisabled, "first-page controls are clamped", state);
visited.push(...state.ids);
await evaluate('document.getElementById("catalog-next").click()');
state = await pageState();
check(state.ids.length === 100, "second page is reachable", state);
check(state.count === "250 matching Variants; showing 101-200; page 2/3", "second-page count is honest", state.count);
visited.push(...state.ids);
await evaluate('document.getElementById("catalog-next").click()');
state = await pageState();
check(state.ids.length === 50, "last partial page is reachable", state);
check(state.count === "250 matching Variants; showing 201-250; page 3/3", "last-page count is honest", state.count);
check(!state.previousDisabled && state.nextDisabled, "last-page controls are clamped", state);
visited.push(...state.ids);
check(new Set(visited).size === 250, "every broad-search result is reachable", visited.length);
check(visited.includes("100249"), "the final result is reachable");
await evaluate('document.getElementById("catalog-previous").click()');
state = await pageState();
check(state.count.endsWith("page 2/3"), "previous navigation works", state.count);
await setSearch("variant-0249");
state = await pageState();
check(
  state.ids.length === 1 && state.ids[0] === "100249" && state.count.endsWith("page 1/1"),
  "changing search resets pagination and preserves the final result",
  state,
);
await evaluate('document.querySelector("#catalog-results button").click()');
check(
  (await evaluate('document.getElementById("catalog-detail").innerText')).includes("Variant ID 100249"),
  "the final broad-search result opens",
);
check(client.httpRequests.length === 0, "no HTTP(S) requests escaped", client.httpRequests);
check(client.exceptions.length === 0, "no runtime exceptions", client.exceptions);
check(client.consoleErrors.length === 0, "no console errors", client.consoleErrors);

console.log(JSON.stringify({
  format: "BUFFALO_A1_SYNTHETIC_BROWSER_AUDIT_V1",
  assertions: checks,
  assertion_count: checks.length,
  broad_search_unique_results: new Set(visited).size,
  accepted_pdf_links: hostile.hrefs,
  external_http_requests: client.httpRequests.length,
  runtime_exceptions: client.exceptions.length,
  console_errors: client.consoleErrors.length,
}));
client.close();
'''


def _fixture_report() -> dict[str, object]:
    batches: list[dict[str, object]] = []
    for index in range(250):
        variant_id = str(100_000 + index)
        batch: dict[str, object] = {
            "variant_id": variant_id,
            "search_text": f"fixture-shared variant-{index:04d} {variant_id}",
            "catalog_review": {
                "captured_product_title": (
                    "</script><script>globalThis.__hrefExecuted=true</script>"
                    if index == 249
                    else f"Fixture Variant {index:04d}"
                ),
                "captured_variant_options": "750ML",
            },
            "offers": [],
        }
        batches.append(batch)

    hrefs = (
        "../original_sources/Fixture%20Book.pdf#page=2",
        "javascript:globalThis.__hrefExecuted=true",
        "../../escape.pdf#page=1",
        "../original_sources/../escape.pdf#page=1",
        "../original_sources/%2E%2E%2Fescape.pdf#page=1",
    )
    batches[0]["offers"] = [
        {
            "vendor": "Fixture Supplier",
            "supplier_code": f"CODE-{index}",
            "source_occurrence_id": f"offer-{index}",
            "candidate_disposition": "SEARCH_LEAD_ONLY",
            "source": {
                "file": "Fixture Book.pdf",
                "page": 2,
                "sha256": "a" * 64,
                "availability": "VERIFIED_ORIGINAL_BYTES",
                "local_pdf_href": href,
            },
            "price_ladder": [],
            "blockers": {},
            "authority": {
                "mapping_approved": False,
                "price_approved": False,
                "import_ready": False,
            },
        }
        for index, href in enumerate(hrefs)
    ]
    return {
        "label": A1_REVIEW_LABEL,
        "status": "REVIEW_ONLY_VALIDATED",
        "package": {"package_kind": A1_PACKAGE_KIND, "readiness": {}},
        "offer_family": {"summary": {}, "invariants": {}},
        "review_batches": batches,
        "operational_effects": {},
        "owner_decision_ledger": [],
        "owner_decision_display_scopes": [],
        "prior_owner_answers_do_not_reask": [],
        "retained_owner_questions_policy_check": [],
        "source_review_overlays": [],
        "combo_review_ledger": {},
    }


def _free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def main() -> int:
    chromium = next(
        (
            candidate
            for name in ("chromium", "chromium-browser", "google-chrome")
            if (candidate := shutil.which(name)) is not None
        ),
        None,
    )
    node = shutil.which("node")
    if chromium is None or node is None:
        print("Chromium and Node.js are required", file=sys.stderr)
        return 2

    with tempfile.TemporaryDirectory(prefix="buffalo-a1-browser-") as temp:
        root = Path(temp)
        report_path = root / "report.html"
        report_path.write_text(render_review_html(_fixture_report()), encoding="utf-8")
        port = _free_port()
        stderr_path = root / "chromium.stderr"
        with stderr_path.open("wb") as chromium_stderr:
            process = subprocess.Popen(
                [
                    chromium,
                    "--headless=new",
                    "--no-sandbox",
                    "--disable-gpu",
                    "--disable-background-networking",
                    "--disable-component-update",
                    "--disable-default-apps",
                    "--disable-sync",
                    "--metrics-recording-only",
                    "--no-first-run",
                    "--host-resolver-rules=MAP * 0.0.0.0, EXCLUDE 127.0.0.1",
                    f"--remote-debugging-port={port}",
                    f"--user-data-dir={root / 'profile'}",
                    "about:blank",
                ],
                stdout=subprocess.DEVNULL,
                stderr=chromium_stderr,
            )
            try:
                endpoint = f"http://127.0.0.1:{port}"
                for _ in range(100):
                    try:
                        with urlopen(f"{endpoint}/json/version", timeout=0.2):
                            break
                    except OSError:
                        if process.poll() is not None:
                            raise RuntimeError("Chromium exited before CDP was ready")
                        time.sleep(0.05)
                else:
                    raise RuntimeError("Chromium CDP endpoint did not become ready")
                audit = subprocess.run(
                    [
                        node,
                        "--input-type=module",
                        "-e",
                        _CDP_AUDIT,
                        endpoint,
                        report_path.as_uri(),
                    ],
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                result = json.loads(audit.stdout)
                print(json.dumps(result, sort_keys=True, separators=(",", ":")))
            finally:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

/**
 * Capture the D2/D3 feature screenshots against the full local evidence store.
 *
 * Two scenarios, one browser session, driven over CDP the same way as
 * scripts/capture_public_screenshots.cjs:
 *
 * 1. parallel-team — the D2 card-reader team: one click reads every evidence
 *    card in parallel (real model calls through the visitor-key path); the
 *    per-card quote sections (.insight-specifics / .insight-rejected) are
 *    removed first, so no verbatim review text can enter the repository.
 * 2. chat-memory — the D3 history restore: one real chat turn, then a page
 *    reload reattaches the run and rehydrates the conversation from the local
 *    memory store; restored turns carry no verbatim quotes by construction.
 *
 * Every saved image passes the path / ASIN / hash guard first.
 *
 * Usage: BYO_TEST_KEY=... node scripts/capture_feature_screenshots.cjs <baseUrl> <outputDir>
 */
const { spawn } = require("child_process");
const fs = require("fs");
const os = require("os");
const path = require("path");

const BASE = process.argv[2] || "http://127.0.0.1:8765";
const OUT_DIR = path.resolve(process.argv[3] || "docs/assets");
const CHROME =
  process.env.CHROME_PATH || "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe";
const PORT = 9347;

const PROHIBITED = [
  /[A-Za-z]:[\\/]/,
  /(?:^|[\s('"=])\/(?!\/)[^\s'"<>]+/,
  /(?<![A-Za-z0-9])B(?=[A-Za-z0-9]{9}(?![A-Za-z0-9]))(?=[A-Za-z0-9]*[0-9])[A-Za-z0-9]{9}/i,
  /(?<![0-9a-f])[0-9a-f]{64}(?![0-9a-f])/i,
];

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

async function waitForJson(url, tries = 80) {
  for (let i = 0; i < tries; i += 1) {
    try {
      const response = await fetch(url);
      if (response.ok) return await response.json();
    } catch (error) {
      /* not up yet */
    }
    await sleep(250);
  }
  throw new Error(`timed out waiting for ${url}`);
}

class Cdp {
  constructor(ws) {
    this.ws = ws;
    this.nextId = 1;
    this.pending = new Map();
    ws.addEventListener("message", (event) => {
      const message = JSON.parse(event.data);
      if (message.id && this.pending.has(message.id)) {
        const { resolve, reject } = this.pending.get(message.id);
        this.pending.delete(message.id);
        if (message.error) reject(new Error(JSON.stringify(message.error)));
        else resolve(message.result);
      }
    });
  }

  static async connect(url) {
    const ws = new WebSocket(url);
    await new Promise((resolve, reject) => {
      ws.addEventListener("open", resolve, { once: true });
      ws.addEventListener("error", reject, { once: true });
    });
    return new Cdp(ws);
  }

  send(method, params = {}) {
    const id = this.nextId++;
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
      this.ws.send(JSON.stringify({ id, method, params }));
    });
  }

  async evaluate(expression) {
    const result = await this.send("Runtime.evaluate", {
      expression,
      awaitPromise: true,
      returnByValue: true,
    });
    if (result.exceptionDetails) {
      throw new Error(
        `page threw: ${result.exceptionDetails.exception?.description || "unknown"}`,
      );
    }
    return result.result.value;
  }

  async waitFor(expression, label, timeoutMs = 90000) {
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
      if (await this.evaluate(expression)) return true;
      await sleep(300);
    }
    throw new Error(`timed out waiting for ${label}`);
  }
}

async function main() {
  const key = process.env.BYO_TEST_KEY || "";
  assert(key, "BYO_TEST_KEY is required: both scenarios make real model calls.");
  const profile = fs.mkdtempSync(path.join(os.tmpdir(), "workbench-feature-"));
  const chrome = spawn(
    CHROME,
    [
      `--remote-debugging-port=${PORT}`,
      `--user-data-dir=${profile}`,
      "--headless=new",
      "--no-first-run",
      "--no-default-browser-check",
      "--disable-gpu",
      "--hide-scrollbars",
      "about:blank",
    ],
    { stdio: "ignore" },
  );
  fs.mkdirSync(OUT_DIR, { recursive: true });
  try {
    const version = await waitForJson(`http://127.0.0.1:${PORT}/json/version`);
    console.log("browser:", version.Browser);
    const target = await (
      await fetch(`http://127.0.0.1:${PORT}/json/new?${encodeURIComponent(BASE + "/")}`, {
        method: "PUT",
      })
    ).json();
    const page = await Cdp.connect(target.webSocketDebuggerUrl);
    await page.send("Page.enable");
    await page.send("Runtime.enable");

    const setViewport = (width, height, mobile) =>
      page.send("Emulation.setDeviceMetricsOverride", {
        width,
        height,
        deviceScaleFactor: 1.5,
        mobile,
      });

    const shot = async (name) => {
      const text = await page.evaluate("document.body.innerText");
      PROHIBITED.forEach((pattern) =>
        assert(!pattern.test(text), `${name}: page text contains a prohibited value shape`),
      );
      const image = await page.send("Page.captureScreenshot", {
        format: "png",
        captureBeyondViewport: true,
      });
      const targetFile = path.join(OUT_DIR, `p7-workbench-${name}.png`);
      fs.writeFileSync(targetFile, Buffer.from(image.data, "base64"));
      console.log("saved:", targetFile, fs.statSync(targetFile).size, "bytes");
    };

    const load = async () => {
      await page.evaluate("window.scrollTo(0, 0)");
      await page.waitFor("document.readyState === 'complete'", "page load", 30000);
      await page.waitFor(
        "document.querySelector('#catalog-count') && document.querySelector('#catalog-count').innerText.trim().length > 0",
        "catalog count",
        60000,
      );
    };

    const runComparison = async () => {
      // The team strip appears when a run carries more than one card; the
      // local P6 service exposes one card per product, so a two-product
      // comparison is the smallest team (two parallel readers).
      await page.evaluate(`(() => {
        document.querySelector('[data-workflow="comparison"]').click();
        for (const value of ['frostline', 'northstar']) {
          const box = document.querySelector('input[name="product"][value="' + value + '"]');
          if (!box) throw new Error(value + ' option missing');
          if (!box.checked) box.click();
        }
        document.querySelector('#aspect').value = 'noise';
        document.querySelector('#question').value =
          '两台制冰机的运行噪音反馈有什么差异值得优先核查？';
        return true;
      })()`);
      await page.evaluate("document.querySelector('#run-form button[type=submit]').click()");
      await page.waitFor("!!document.querySelector('.evidence-item')", "evidence cards", 90000);
      await sleep(600);
    };

    await setViewport(1440, 980, false);
    await load();
    await page.evaluate(`(() => {
      window.__errs = [];
      window.addEventListener('error', (event) => window.__errs.push(String(event.message)));
      window.addEventListener('unhandledrejection', (event) => window.__errs.push('rejection: ' + String(event.reason)));
      return true;
    })()`);
    await runComparison();

    // 1. parallel-team — one click, every card read in parallel.
    await page.evaluate(`(() => {
      sessionStorage.setItem('wb-insight-key', ${JSON.stringify(key)});
      localStorage.setItem('wb-insight-key-remember', 'false');
      const button = document.querySelector('.evidence-team button.insight-toggle');
      if (!button) {
        const debug = {
          cards: document.querySelectorAll('.evidence-item').length,
          team: Boolean(document.querySelector('.evidence-team')),
          listChildren: Array.from(document.querySelector('#evidence-list')?.children || [])
            .slice(0, 3)
            .map((node) => node.className),
          errors: window.__errs,
        };
        throw new Error('team toggle missing: ' + JSON.stringify(debug));
      }
      return true;
    })()`);
    await page.evaluate(
      "document.querySelector('.evidence-team button.insight-toggle').click()",
    );
    await page.waitFor(
      "(() => { const box = document.querySelector('.evidence-team'); const note = box && box.querySelector('.insight-footnote'); return !!note && note.innerText.trim().length > 10; })()",
      "team summary",
      300000,
    );
    await page.evaluate(`(() => {
      // Verbatim review text must not enter the repository: strip the quote
      // sections from every per-card reading before the shot.
      document.querySelectorAll('.insight-specifics, .insight-rejected').forEach((node) => node.remove());
      const team = document.querySelector('.evidence-team');
      team.scrollIntoView({ block: 'start' });
      return true;
    })()`);
    await sleep(500);
    await shot("parallel-team");

    // 2. chat-memory — one real turn, then reload: the run reattaches and the
    // conversation is restored from the local memory store, quote-free.
    await page.evaluate(`(() => {
      const input = document.querySelector('#qa-input');
      input.focus();
      input.value = '这款的运行噪音普遍吗？请给出能核对原文的说法。';
      input.dispatchEvent(new Event('input', { bubbles: true }));
      document.querySelector('#chat-send').click();
      return true;
    })()`);
    await page.waitFor(
      "(() => { const a = document.querySelector('.chat-answer'); return !!a && a.innerText.trim().length > 20; })()",
      "chat answer",
      240000,
    );
    await page.evaluate("location.reload()");
    await load();
    await page.waitFor(
      "(() => { return !!document.querySelector('.chat-answer'); })()",
      "restored history",
      90000,
    );
    await page.evaluate(`(() => {
      const panel = document.querySelector('#chat-scroll');
      if (panel) panel.scrollTop = 0;
      return true;
    })()`);
    await sleep(500);
    await shot("chat-memory");
  } finally {
    chrome.kill();
  }
}

main().catch((error) => {
  console.error("FAILED:", error.message);
  process.exit(1);
});

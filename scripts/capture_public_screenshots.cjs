/**
 * Capture the public README screenshots against the full local evidence store.
 *
 * Seven scenarios, one browser session, driven over CDP with no automation
 * library: overview / desktop / settings / export / mobile / insight /
 * chat-trace. Every saved image passes the path / ASIN / hash guard first, and
 * the two regions that can carry verbatim review text (`.insight-rejected`,
 * `.chat-answer`) are hidden before their shot, so no review text can enter
 * the repository through a screenshot.
 *
 * The insight and chat scenarios call the real model once each through the
 * visitor-key path (BYO_TEST_KEY, session storage only, never written to
 * disk); every other scenario is deterministic and costs nothing.
 *
 * Usage: node scripts/capture_public_screenshots.cjs <baseUrl> <outputDir>
 */
const { spawn } = require("child_process");
const fs = require("fs");
const os = require("os");
const path = require("path");

const BASE = process.argv[2] || "http://127.0.0.1:8765";
const OUT_DIR = path.resolve(process.argv[3] || "docs/assets");
const CHROME =
  process.env.CHROME_PATH || "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe";
const PORT = 9345;

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
  const profile = fs.mkdtempSync(path.join(os.tmpdir(), "workbench-public-"));
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

    // 1. overview — the full-store catalog on first paint (244 A-tier products).
    await setViewport(1440, 980, false);
    await load();
    await sleep(500);
    await shot("overview");

    // 2. desktop — a deterministic diagnosis run on the most-reviewed product.
    const runDiagnosis = async () => {
      await page.evaluate(`(() => {
        const box = document.querySelector('input[name="product"][value="frostline"]');
        if (!box) throw new Error('frostline option missing');
        // The catalog auto-selects the most-reviewed product on load; only
        // click when that did not happen, or the click would deselect it.
        if (!box.checked) box.click();
        document.querySelector('#aspect').value = 'noise';
        document.querySelector('#question').value =
          '评论中有哪些关于运行噪音的问题值得优先处理？';
        return true;
      })()`);
      await page.evaluate("document.querySelector('#run-form button[type=submit]').click()");
      await page.waitFor("!!document.querySelector('.evidence-item')", "evidence cards", 90000);
      await sleep(600);
    };
    await runDiagnosis();
    await shot("desktop");

    // 3. export — the CSV export panel with its relative path and copy button.
    // The panel is a collapsed <details>; expand it first (collapsed content
    // reports an empty innerText, which is how the first run hung here).
    await page.evaluate("(() => { const block = document.querySelector('#export-block'); block.open = true; block.querySelector('#export-button').scrollIntoView({block:'center'}); return true; })()");
    await page.evaluate("document.querySelector('#export-button').click()");
    await page.waitFor(
      "(() => { const out = document.querySelector('#export-output'); return !!out && out.innerText.trim().length > 0; })()",
      "export output",
      30000,
    );
    await sleep(400);
    await shot("export");

    // 4. settings — the model-settings dialog with an empty key field.
    await page.evaluate("document.querySelector('#settings-toggle').click()");
    await page.waitFor(
      "!document.querySelector('#settings-dialog').hidden",
      "settings dialog",
      15000,
    );
    await sleep(400);
    await shot("settings");
    await page.evaluate("document.querySelector('#settings-close').click()");

    // 5. mobile — the same store at phone width, back on the catalog screen.
    await setViewport(390, 844, true);
    await page.evaluate("location.reload()");
    await load();
    await sleep(500);
    await shot("mobile");

    if (!key) {
      console.log("BYO_TEST_KEY not set; skipping the two model-backed scenarios");
      return;
    }

    // 6. insight — one real model call; the verbatim-quote section is hidden.
    await setViewport(1440, 980, false);
    await page.evaluate("window.scrollTo(0, 0)");
    await runDiagnosis(); // the mobile reload dropped the client-side run state
    await page.evaluate(`(() => {
      sessionStorage.setItem('wb-insight-key', ${JSON.stringify(key)});
      localStorage.setItem('wb-insight-key-remember', 'false');
      const button = document.querySelector('button.insight-toggle');
      if (!button) throw new Error('insight toggle missing');
      button.scrollIntoView({ block: 'center' });
      button.click();
      return true;
    })()`);
    await page.waitFor(
      "(() => { const box = document.querySelector('.insight-result'); return !!box && !!box.querySelector('.insight-supported'); })()",
      "insight result",
      150000,
    );
    await page.evaluate(`(() => {
      const rejected = document.querySelector('.insight-rejected');
      if (rejected) rejected.remove();
      document.querySelector('.insight-result').scrollIntoView({ block: 'start' });
      return true;
    })()`);
    await sleep(500);
    await shot("insight");

    // 7. chat-trace — one real chat turn; the answer body is hidden so only the
    // question and the step trace stay on screen.
    await page.evaluate("(() => { document.querySelector('#qa-input').focus(); return true; })()");
    await page.evaluate(`(() => {
      const input = document.querySelector('#qa-input');
      input.value = '全量匹配的评论里，反对和冲突各有多少条？';
      input.dispatchEvent(new Event('input', { bubbles: true }));
      document.querySelector('#chat-send').click();
      return true;
    })()`);
    await page.waitFor(
      "(() => { const a = document.querySelector('.chat-answer'); return !!a && a.innerText.trim().length > 20; })()",
      "chat answer",
      180000,
    );
    await page.evaluate(`(() => {
      document.querySelector('.chat-answer').remove();
      const trace = [...document.querySelectorAll('details')].find((item) => {
        const label = item.querySelector('summary');
        return label && label.innerText.includes('执行过程');
      });
      if (trace) trace.open = true;
      const panel = document.querySelector('#chat-scroll');
      if (panel) panel.scrollIntoView({ block: 'end' });
      return true;
    })()`);
    await sleep(500);
    await shot("chat-trace");
  } finally {
    chrome.kill();
    await new Promise((resolve) => {
      if (chrome.exitCode !== null) resolve();
      else chrome.once("exit", resolve);
      setTimeout(resolve, 5000).unref?.();
    });
    try {
      fs.rmSync(profile, { recursive: true, force: true, maxRetries: 5, retryDelay: 300 });
    } catch (error) {
      console.log("note: left temp profile behind:", error.code || error.message);
    }
  }
}

main().catch((error) => {
  console.error("FAILED:", error.message);
  process.exit(1);
});

/**
 * Capture the workbench screenshots the README shows.
 *
 * Two scenarios, both publishable:
 *   overview  synthetic mode; the page carries authored demo content only
 *   ai-key    bundled mode with no key, so the visitor-key panel is on screen and
 *             no review text has been produced yet
 *
 * It drives a real Chrome over CDP (no playwright/puppeteer here) and refuses to
 * save an image whose page text contains path, ASIN, or hash shapes.
 *
 * Usage: node scripts/capture_workbench_screenshots.cjs <baseUrl> <outputDir> [overview|ai-key]
 */
const { spawn } = require("child_process");
const fs = require("fs");
const os = require("os");
const path = require("path");

const BASE = process.argv[2] || "http://127.0.0.1:8000";
const OUT_DIR = path.resolve(process.argv[3] || "docs/assets");
const SCENARIO = process.argv[4] || "overview";
const CHROME = process.env.CHROME_PATH || "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe";
const PORT = 9344;

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

async function capture() {
  const profile = fs.mkdtempSync(path.join(os.tmpdir(), "workbench-shot-"));
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
    await page.send("Emulation.setDeviceMetricsOverride", {
      width: 1440,
      height: 980,
      deviceScaleFactor: 1.5,
      mobile: false,
    });

    await page.waitFor("document.readyState === 'complete'", "page load", 30000);
    await page.waitFor("!!document.querySelector('#run-form button[type=submit]')", "form", 30000);

    await page.evaluate("document.querySelector('#run-form button[type=submit]').click()");
    await page.waitFor("!!document.querySelector('.evidence-item')", "evidence card", 90000);
    await sleep(600);

    if (SCENARIO === "ai-key") {
      await page.evaluate(`(() => {
        const button = [...document.querySelectorAll('button.insight-toggle')]
          .find((item) => item.textContent.includes('AI 深入分析'));
        if (!button) throw new Error('insight button missing');
        button.scrollIntoView({ block: 'center' });
        button.click();
        return true;
      })()`);
      await page.waitFor(
        "(() => { const p = document.querySelector('.insight-key'); return !!p && !p.hidden; })()",
        "visitor key panel",
        15000,
      );
      await page.evaluate(
        "document.querySelector('.insight-key').scrollIntoView({ block: 'center' })",
      );
      await sleep(400);
    } else {
      await page.evaluate("window.scrollTo(0, 0)");
      await sleep(300);
    }

    const text = await page.evaluate("document.body.innerText");
    PROHIBITED.forEach((pattern) =>
      assert(!pattern.test(text), "page text contains a prohibited value shape"),
    );

    const shot = await page.send("Page.captureScreenshot", {
      format: "png",
      captureBeyondViewport: true,
    });
    const target_file = path.join(OUT_DIR, `p7-workbench-${SCENARIO}.png`);
    fs.mkdirSync(OUT_DIR, { recursive: true });
    fs.writeFileSync(target_file, Buffer.from(shot.data, "base64"));
    console.log("saved:", target_file, fs.statSync(target_file).size, "bytes");
    console.log("page text sample:", text.replace(/\s+/g, " ").slice(0, 120));
  } finally {
    chrome.kill();
  }
}

capture().catch((error) => {
  console.error("FAILED:", error.message);
  process.exit(1);
});

const fs = require("fs");
const path = require("path");

const baseUrl = process.argv[2] || "http://127.0.0.1:8765";
const outputDir = path.resolve(process.argv[3] || "docs/project-handbook/assets");
const projectRoot = path.resolve(__dirname, "..");
const names = [
  "p7-tour-01-initial.png",
  "p7-tour-02-diagnosis.png",
  "p7-tour-03-comparison.png",
  "p7-tour-04-clarification.png",
];
const prohibited = [
  /[A-Za-z]:[\\/]|(?:\\\\|\/\/)[^\\/]+[\\/]|(?:^|[\s('"=])\/(?!\/)[^\s'"<>]+/,
  /(?<![A-Z0-9])B[A-Z0-9]{9}(?![A-Z0-9])/i,
  /(?<![0-9a-f])[0-9a-f]{64}(?![0-9a-f])/i,
  /DEEPSEEK_API_KEY|Bearer\s+\S+|sk-[A-Za-z0-9_-]+/i,
];

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

function removeIfPresent(target) {
  try {
    fs.unlinkSync(target);
  } catch (error) {
    if (error.code !== "ENOENT") throw error;
  }
}

function temporaryPath(target) {
  return target.replace(/\.png$/i, `.tmp-${process.pid}.png`);
}

async function waitForResult(page, expectedStatus) {
  const responsePromise = page.waitForResponse((response) =>
    response.url().includes("/api/v1/runs") &&
    response.request().method() === "POST"
  );
  await page.locator("#run-form button[type='submit']").click();
  const response = await responsePromise;
  assert(response.ok(), `API returned ${response.status()}`);
  const payloadText = await response.text();
  prohibited.forEach((pattern) =>
    assert(!pattern.test(payloadText), "API response contains a prohibited value shape")
  );
  await page.locator("#result-status").getByText(expectedStatus, { exact: true }).waitFor();
  return JSON.parse(payloadText);
}

async function assertSyntheticPage(page, label) {
  const dimensions = await page.evaluate(() => ({
    width: window.innerWidth,
    height: window.innerHeight,
    scrollWidth: document.documentElement.scrollWidth,
    text: document.body.innerText,
  }));
  assert(dimensions.width === 1440 && dimensions.height === 900, `${label}: wrong viewport`);
  assert(dimensions.scrollWidth <= dimensions.width + 1, `${label}: horizontal overflow`);
  assert(dimensions.text.trim().length > 250, `${label}: page is unexpectedly blank`);
  prohibited.forEach((pattern) =>
    assert(!pattern.test(dimensions.text), `${label}: page contains a prohibited value shape`)
  );
  assert(dimensions.text.includes("合成回放"), `${label}: synthetic mode marker missing`);
}

async function run() {
  fs.mkdirSync(outputDir, { recursive: true });
  const targets = names.map((name) => path.join(outputDir, name));
  const temporary = targets.map(temporaryPath);
  temporary.forEach(removeIfPresent);
  const { chromium } = require("playwright");
  const launchOptions = { headless: true };
  if (process.env.PLAYWRIGHT_EXECUTABLE_PATH) {
    launchOptions.executablePath = process.env.PLAYWRIGHT_EXECUTABLE_PATH;
  } else {
    launchOptions.executablePath = chromium.executablePath();
  }
  const browser = await chromium.launch(launchOptions);
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
    await page.goto(baseUrl, { waitUntil: "networkidle" });
    await assertSyntheticPage(page, "initial");
    await page.screenshot({ path: temporary[0], fullPage: false });

    const diagnosis = await waitForResult(page, "VERIFIED_RESULT");
    assert(diagnosis.evidence_count > 0, "diagnosis evidence missing");
    assert(await page.locator(".evidence-item").count() > 0, "diagnosis cards missing");
    await page.screenshot({ path: temporary[1], fullPage: false });

    await page.locator('[data-workflow="comparison"]').click();
    await page.locator('input[value="northstar"]').check();
    const comparison = await waitForResult(page, "VERIFIED_RESULT");
    assert(comparison.evidence_count > 0, "comparison evidence missing");
    assert(await page.locator(".output-table tbody tr").count() === 2, "comparison rows missing");
    await page.screenshot({ path: temporary[2], fullPage: false });

    await page.locator('[data-workflow="diagnosis"]').click();
    for (const checkbox of await page.locator('input[name="product"]').all()) {
      await checkbox.uncheck();
    }
    const clarification = await waitForResult(page, "CLARIFICATION_REQUIRED");
    assert(clarification.clarification, "clarification payload missing");
    assert(await page.locator(".clarification-choices button").count() > 0, "clarification choices missing");
    await page.screenshot({ path: temporary[3], fullPage: false });

    temporary.forEach((source, index) => {
      assert(fs.statSync(source).size > 0, `${names[index]} was not written`);
    });
    targets.forEach((target, index) => {
      removeIfPresent(target);
      fs.renameSync(temporary[index], target);
    });
    process.stdout.write(JSON.stringify({
      status: "PASSED",
      mode: "synthetic",
      viewport: "1440x900",
      screenshots: targets.map((target) => path.relative(projectRoot, target)),
    }) + "\n");
  } finally {
    await browser.close();
    temporary.forEach(removeIfPresent);
  }
}

run().catch((error) => {
  process.stderr.write(JSON.stringify({ status: "FAILED", error: error.message }) + "\n");
  process.exitCode = 1;
});

const fs = require("fs");
const path = require("path");
const crypto = require("crypto");
const childProcess = require("child_process");

const baseUrl = process.argv[2] || "http://127.0.0.1:8765";
const outputDir = path.resolve(process.argv[3] || "docs/assets");
const summaryPath = path.resolve(
  process.argv[4] || "artifacts/evaluation-summaries/phase-7-workbench.json"
);
const projectRoot = path.resolve(__dirname, "..");
const desktopPath = path.join(outputDir, "p7-workbench-desktop.png");
const mobilePath = path.join(outputDir, "p7-workbench-mobile.png");
const desktopTemp = `${desktopPath}.tmp.png`;
const mobileTemp = `${mobilePath}.tmp.png`;
const summaryTemp = `${summaryPath}.tmp`;
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
  try { fs.unlinkSync(target); } catch (error) {
    if (error.code !== "ENOENT") throw error;
  }
}

function invalidateArtifacts() {
  [summaryPath, summaryTemp, desktopPath, mobilePath, desktopTemp, mobileTemp]
    .forEach(removeIfPresent);
}

function sha256(target) {
  return crypto.createHash("sha256").update(fs.readFileSync(target)).digest("hex");
}

function sourceIdentity() {
  const sources = [
    "pyproject.toml",
    "scripts/run_phase7_workbench.py",
    "scripts/run_phase7_local_regression.py",
    "scripts/validate_phase7_workbench.cjs",
    "src/ecom_review_intelligence/agent_cli.py",
    "src/ecom_review_intelligence/agent_contracts.py",
    "src/ecom_review_intelligence/agent_graph.py",
    "src/ecom_review_intelligence/agent_policy.py",
    "src/ecom_review_intelligence/agent_tools.py",
    "src/ecom_review_intelligence/engine.py",
    "src/ecom_review_intelligence/model_gateway.py",
    "src/ecom_review_intelligence/workbench_cli.py",
    "src/ecom_review_intelligence/workbench_contracts.py",
    "src/ecom_review_intelligence/workbench_service.py",
    "src/ecom_review_intelligence/workbench_regression.py",
    "src/ecom_review_intelligence/workbench_resources.py",
    "src/ecom_review_intelligence/workbench_web.py",
    "src/ecom_review_intelligence/templates/workbench.html",
    "src/ecom_review_intelligence/static/workbench.css",
    "src/ecom_review_intelligence/static/workbench.js",
    "tests/test_workbench_cli.py",
    "tests/test_workbench_local.py",
    "tests/test_workbench_web.py",
  ];
  const digest = crypto.createHash("sha256");
  sources.forEach((relative) => {
    digest.update(relative);
    digest.update("\0");
    digest.update(fs.readFileSync(path.join(projectRoot, relative)));
    digest.update("\0");
  });
  const gitRevision = childProcess.execFileSync(
    "git", ["-C", projectRoot, "rev-parse", "HEAD"], { encoding: "utf8" }
  ).trim();
  const dirty = childProcess.execFileSync(
    "git", ["-C", projectRoot, "status", "--porcelain", "--", ...sources],
    { encoding: "utf8" }
  ).trim();
  return {
    git_revision: gitRevision,
    source_sha256: digest.digest("hex"),
    source_dirty: dirty.length > 0,
    source_paths: sources,
  };
}

async function assertLayoutRelationships(page, label) {
  const result = await page.evaluate(() => {
    const box = (selector) => {
      const value = document.querySelector(selector)?.getBoundingClientRect();
      return value && { top: value.top, right: value.right, bottom: value.bottom, left: value.left };
    };
    return {
      width: window.innerWidth,
      topbar: box(".topbar"),
      workspace: box(".workspace"),
      navigation: box(".workflow-nav"),
      task: box(".task-column"),
      result: box(".result-column"),
      resultTitle: box("#result-title"),
      resultStatus: box("#result-status"),
    };
  });
  const separated = (first, second, axis) => (
    axis === "horizontal" ? first.right <= second.left + 1 : first.bottom <= second.top + 1
  );
  assert(separated(result.topbar, result.workspace, "vertical"), `${label}: topbar overlaps workspace`);
  if (result.width > 1040) {
    assert(separated(result.navigation, result.task, "horizontal"), `${label}: navigation overlaps task`);
    assert(separated(result.task, result.result, "horizontal"), `${label}: task overlaps result`);
  } else if (result.width <= 700) {
    assert(separated(result.navigation, result.task, "vertical"), `${label}: navigation overlaps task`);
    assert(separated(result.task, result.result, "vertical"), `${label}: task overlaps result`);
  }
  const headingOverlap = !(
    result.resultTitle.right <= result.resultStatus.left + 1
    || result.resultStatus.right <= result.resultTitle.left + 1
    || result.resultTitle.bottom <= result.resultStatus.top + 1
    || result.resultStatus.bottom <= result.resultTitle.top + 1
  );
  assert(!headingOverlap, `${label}: result title overlaps status`);
}

async function assertPageSafe(page, label) {
  const dimensions = await page.evaluate(() => ({
    innerWidth: window.innerWidth,
    scrollWidth: document.documentElement.scrollWidth,
    bodyHeight: document.body.getBoundingClientRect().height,
    textLength: document.body.innerText.trim().length,
  }));
  assert(dimensions.scrollWidth <= dimensions.innerWidth + 1, `${label}: horizontal overflow`);
  assert(dimensions.bodyHeight > 400, `${label}: page is unexpectedly short`);
  assert(dimensions.textLength > 250, `${label}: page is unexpectedly blank`);
  const content = await page.locator("body").innerText();
  prohibited.forEach((pattern) => assert(!pattern.test(content), `${label}: sensitive value shape`));
  await assertLayoutRelationships(page, label);
}

async function submitAndWait(page, expectedStatus) {
  const started = Date.now();
  const responsePromise = page.waitForResponse((response) =>
    response.url().includes("/api/v1/runs") && response.request().method() === "POST"
  );
  await page.locator("#run-form button[type='submit']").click();
  const response = await responsePromise;
  assert(response.ok(), `API returned ${response.status()}`);
  const text = await response.text();
  prohibited.forEach((pattern) => assert(!pattern.test(text), "API leaked a sensitive value shape"));
  await page.locator("#result-status").getByText(expectedStatus, { exact: true }).waitFor();
  return { elapsedMs: Date.now() - started, payload: JSON.parse(text) };
}

async function run() {
  invalidateArtifacts();
  fs.mkdirSync(outputDir, { recursive: true });
  fs.mkdirSync(path.dirname(summaryPath), { recursive: true });
  const { chromium } = require("playwright");
  const launchOptions = { headless: true };
  if (process.env.PLAYWRIGHT_EXECUTABLE_PATH) {
    launchOptions.executablePath = process.env.PLAYWRIGHT_EXECUTABLE_PATH;
  }
  const browser = await chromium.launch(launchOptions);
  const consoleErrors = [];
  const failedResponses = [];
  const observations = [];
  try {
    const desktop = await browser.newPage({ viewport: { width: 1440, height: 900 } });
    desktop.on("console", (message) => {
      if (message.type() === "error") consoleErrors.push(message.text());
    });
    desktop.on("response", (response) => {
      if (response.status() >= 400) failedResponses.push(`${response.status()} ${response.url()}`);
    });
    await desktop.goto(baseUrl, { waitUntil: "networkidle" });
    await assertPageSafe(desktop, "desktop-initial");
    const diagnosis = await submitAndWait(desktop, "VERIFIED_RESULT");
    assert(await desktop.locator(".evidence-item").count() > 0, "diagnosis evidence missing");
    observations.push({
      task: "SKU diagnosis",
      workflow: "diagnosis",
      completion_ms: diagnosis.elapsedMs,
      first_evidence_ms: diagnosis.elapsedMs,
      status: diagnosis.payload.status,
      evidence_count: diagnosis.payload.evidence_count,
      adopted: "not_assessed",
      failure_reason: null,
    });
    await desktop.screenshot({
      path: desktopTemp,
      fullPage: true,
    });

    await desktop.locator('[data-workflow="comparison"]').click();
    await desktop.locator('input[value="northstar"]').check();
    const comparison = await submitAndWait(desktop, "VERIFIED_RESULT");
    assert(await desktop.locator(".output-table tbody tr").count() === 2, "comparison rows missing");
    observations.push({
      task: "Competitive evidence comparison",
      workflow: "comparison",
      completion_ms: comparison.elapsedMs,
      first_evidence_ms: comparison.elapsedMs,
      status: comparison.payload.status,
      evidence_count: comparison.payload.evidence_count,
      adopted: "not_assessed",
      failure_reason: null,
    });

    await desktop.locator('[data-workflow="diagnosis"]').click();
    for (const checkbox of await desktop.locator('input[name="product"]').all()) {
      await checkbox.uncheck();
    }
    await submitAndWait(desktop, "CLARIFICATION_REQUIRED");
    const resumeResponse = desktop.waitForResponse((response) => response.url().endsWith("/resume"));
    await desktop.locator(".clarification-choices button").first().click();
    assert((await resumeResponse).ok(), "clarification resume failed");
    await desktop.locator("#result-status").getByText("VERIFIED_RESULT", { exact: true }).waitFor();

    const mobile = await browser.newPage({ viewport: { width: 390, height: 844 } });
    mobile.on("console", (message) => {
      if (message.type() === "error") consoleErrors.push(message.text());
    });
    mobile.on("response", (response) => {
      if (response.status() >= 400) failedResponses.push(`${response.status()} ${response.url()}`);
    });
    await mobile.goto(baseUrl, { waitUntil: "networkidle" });
    await mobile.locator('[data-workflow="triage"]').click();
    await mobile.locator('input[value="northstar"]').check();
    const triage = await submitAndWait(mobile, "VERIFIED_RESULT");
    assert(await mobile.locator(".triage-row").count() > 0, "triage rows missing");
    observations.push({
      task: "Operations issue triage",
      workflow: "triage",
      completion_ms: triage.elapsedMs,
      first_evidence_ms: triage.elapsedMs,
      status: triage.payload.status,
      evidence_count: triage.payload.evidence_count,
      adopted: "not_assessed",
      failure_reason: null,
    });
    await assertPageSafe(mobile, "mobile-triage");
    await mobile.screenshot({
      path: mobileTemp,
      fullPage: true,
    });

    assert(consoleErrors.length === 0, `console errors: ${consoleErrors.join(" | ")}`);
    assert(failedResponses.length === 0, `failed responses: ${failedResponses.join(" | ")}`);
    const identity = sourceIdentity();
    const summary = {
      schema_version: "p7-browser-validation-v1",
      status: "PASSED",
      observed_at: new Date().toISOString(),
      ...identity,
      mode: "synthetic",
      observer: "developer_automated_browser_check",
      adoption_assessed: false,
      business_impact_assessed: false,
      workflows: ["diagnosis", "comparison", "triage", "clarification_resume"],
      viewports: ["1440x900", "390x844"],
      screenshots: [
        { path: "docs/assets/p7-workbench-desktop.png", sha256: sha256(desktopTemp) },
        { path: "docs/assets/p7-workbench-mobile.png", sha256: sha256(mobileTemp) },
      ],
      observations,
      gates: {
        all_three_workflows_completed: observations.length === 3,
        evidence_returned_for_each_task: observations.every((item) => item.evidence_count > 0),
        no_console_errors: consoleErrors.length === 0,
        no_failed_network_responses: failedResponses.length === 0,
        desktop_mobile_layout_checked: true,
        element_relationships_checked: true,
        clarification_resume_completed: true,
      },
      limitations: [
        "Synthetic replay does not measure target-user adoption or business impact.",
        "Developer browser timing is not a production latency benchmark.",
      ],
    };
    fs.writeFileSync(summaryTemp, JSON.stringify(summary, null, 2) + "\n", "utf8");
    fs.renameSync(desktopTemp, desktopPath);
    fs.renameSync(mobileTemp, mobilePath);
    fs.renameSync(summaryTemp, summaryPath);
    process.stdout.write(JSON.stringify(summary) + "\n");
  } finally {
    await browser.close();
  }
}

run().catch((error) => {
  const failed = {
    schema_version: "p7-browser-validation-v1",
    status: "FAILED",
  };
  try {
    invalidateArtifacts();
    fs.mkdirSync(path.dirname(summaryPath), { recursive: true });
    fs.writeFileSync(summaryTemp, JSON.stringify(failed, null, 2) + "\n", "utf8");
    fs.renameSync(summaryTemp, summaryPath);
  } catch (_) {
    // The fixed stderr remains safe even when the output location is unavailable.
  }
  process.stderr.write(JSON.stringify(failed) + "\n");
  process.exitCode = 1;
});

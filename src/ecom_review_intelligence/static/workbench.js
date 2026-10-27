const workflowCopy = {
  diagnosis: { title: "商品评价诊断", code: "商品诊断" },
  comparison: { title: "商品评价对比", code: "商品对比" },
  triage: { title: "问题优先级", code: "问题排序" },
};

const polarityLabels = {
  supporting: "支持",
  opposing: "反对",
  conflicting: "冲突",
  insufficient: "证据不足",
};

const strengthLabels = {
  STRONG: "强",
  MODERATE: "中",
  WEAK: "弱",
  INSUFFICIENT: "不足",
};

const directionLabels = {
  advantage: "正向信号较多",
  watch: "需重点核查",
  mixed: "信号交错",
  insufficient: "证据不足",
};

const priorityLabels = {
  high: "建议优先核查",
  medium: "建议关注",
};

const ownerLabels = {
  Product: "产品团队",
  Quality: "质量团队",
  "Customer support": "客服团队",
};

function displayLabel(labels, code) {
  return labels[code] || code;
}

const aspectQuestions = {
  noise: "评论中有哪些关于运行噪音的问题值得优先处理？",
  reliability: "评论中有哪些可靠性问题值得优先处理？",
  cleaning_maintenance: "评论中有哪些清洁维护问题值得优先处理？",
  ice_quality: "评论中有哪些制冰质量问题值得优先处理？",
  capacity_speed: "评论中有哪些容量或速度问题值得优先处理？",
  ease_setup: "评论中有哪些安装使用问题值得优先处理？",
  leakage: "评论中有哪些漏水问题值得优先处理？",
  size_portability: "评论中有哪些尺寸或便携问题值得优先处理？",
};

const state = {
  workflow: "diagnosis",
  runToken: null,
  productLabels: {},
  insightAvailable: document.querySelector("main")?.dataset.insightAvailable === "true",
  insightByo: document.querySelector("main")?.dataset.insightByo === "true",
  runQuestion: "",
  runQuestionToken: null,
};
const form = document.querySelector("#run-form");
const formError = document.querySelector("#form-error");
const submitButton = form.querySelector("button[type='submit']");

document.querySelectorAll("input[name='product']").forEach((input) => {
  state.productLabels[input.value] = input.dataset.label || input.value;
});

function textElement(tag, className, value) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  element.textContent = value;
  return element;
}

function selectedProducts() {
  return [...document.querySelectorAll("input[name='product']:checked")].map((item) => item.value);
}

function selectWorkflow(workflow) {
  state.workflow = workflow;
  document.querySelectorAll(".workflow-button").forEach((button) => {
    button.classList.toggle("is-active", button.dataset.workflow === workflow);
  });
  document.querySelector("#task-title").textContent = workflowCopy[workflow].title;
  document.querySelector("#task-code").textContent = workflowCopy[workflow].code;
}

document.querySelectorAll(".workflow-button").forEach((button) => {
  button.addEventListener("click", () => selectWorkflow(button.dataset.workflow));
});

const statusLabels = {
  READY: "等待运行",
  VERIFIED_RESULT: "结构与证据引用校验通过",
  DEGRADED_SAFE_RESULT: "安全降级:复核未通过,结果受限",
  CLARIFICATION_REQUIRED: "需要澄清:请先完成商品选择",
  PROVIDER_ERROR: "运行出错:请调整后重试",
};

function setStatus(status) {
  const badge = document.querySelector("#result-status");
  badge.textContent = status;
  badge.className = "status-badge " + (
    status === "VERIFIED_RESULT" ? "status-good" :
    status === "CLARIFICATION_REQUIRED" || status === "DEGRADED_SAFE_RESULT" ? "status-warn" :
    "status-error"
  );
  document.querySelector("#result-status-hint").textContent = displayLabel(statusLabels, status);
}

function productLabel(code) {
  return state.productLabels[code] || code;
}

function renderBlindSpot(blindSpot) {
  const note = document.querySelector("#blind-spot-note");
  if (blindSpot && blindSpot.note) {
    note.textContent = blindSpot.note;
    note.hidden = false;
  } else {
    note.hidden = true;
  }
}

function renderLexiconTotals(payload) {
  const block = document.querySelector("#lexicon-block");
  if (!block) return;
  const totals = payload.lexicon_totals;
  if (!totals || !totals.matched_count) {
    block.hidden = true;
    return;
  }
  block.hidden = false;
  const fields = [
    ["supporting", "#lexicon-supporting"],
    ["opposing", "#lexicon-opposing"],
    ["conflicting", "#lexicon-conflicting"],
    ["insufficient", "#lexicon-insufficient"],
  ];
  fields.forEach(([key, selector]) => {
    const node = document.querySelector(selector);
    if (node) node.textContent = Number(totals[key] || 0).toLocaleString();
  });
  const conflictShare = Math.round(((totals.conflicting || 0) / totals.matched_count) * 100);
  const note = document.querySelector("#lexicon-note");
  if (note) {
    note.textContent =
      "按同一套词表判定全部 " + totals.matched_count.toLocaleString()
      + " 条匹配评论, 只作初步参考: 词表会误判"
      + (conflictShare ? "、且 " + conflictShare + "% 的评论同时出现正反词" : "")
      + "; 不能替代逐条核对, 也不能当结论。";
  }
}

function renderBars(payload) {
  const fallback = { supporting: 0, opposing: 0, conflicting: 0, insufficient: 0 };
  payload.evidence.forEach((item) => { fallback[item.polarity] += 1; });
  const totals = payload.signal_totals && Object.keys(payload.signal_totals).length
    ? payload.signal_totals
    : fallback;
  const scope = document.querySelector("#signal-scope-label");
  if (scope) {
    scope.textContent = "仅 " + (payload.evidence_count || 0).toLocaleString()
      + " 条核查样本, 非全部 " + (payload.matched_count || 0).toLocaleString() + " 条";
  }
  const keys = [["supporting", "支持"], ["opposing", "反对"], ["conflicting", "冲突"], ["insufficient", "信息不足"]];
  const total = keys.reduce((sum, [key]) => sum + (totals[key] || 0), 0);
  keys.forEach(([key, name]) => {
    const value = totals[key] || 0;
    const percentage = total ? Math.round((value / total) * 100) : 0;
    document.querySelector(`#bar-${key}`).style.width = `${percentage}%`;
    document.querySelector(`#label-${key}`).textContent = `${name} · ${value} (${percentage}%)`;
  });
}

function renderCoverage(payload) {
  const matched = payload.matched_count || 0;
  const sampled = payload.evidence_count || 0;
  const percentage = matched ? Math.min(100, (sampled / matched) * 100) : 0;
  document.querySelector("#matched-count").textContent = matched.toLocaleString();
  document.querySelector("#coverage-label").textContent = `匹配评论 ${matched.toLocaleString()} · 展示样本 ${sampled.toLocaleString()}`;
  document.querySelector("#coverage-bar").style.width = `${percentage}%`;
  document.querySelector("#coverage-note").textContent = matched
    ? `覆盖率 ${Math.round(percentage)}% = 展示样本 / 匹配评论, 用于说明抽样范围, 不是置信度。`
    : "当前维度没有匹配评论, 覆盖率为 0%, 不是置信度判断。";
}

function renderProductSignals(payload) {
  const list = document.querySelector("#product-signal-visual");
  list.replaceChildren();
  payload.product_summaries.forEach((summary) => {
    const totals = summary.signal_totals || {};
    const keys = [
      ["supporting", "支持"],
      ["opposing", "反对"],
      ["conflicting", "冲突"],
      ["insufficient", "信息不足"],
    ];
    const total = keys.reduce((sum, [key]) => sum + (totals[key] || 0), 0);
    const row = document.createElement("div");
    row.className = "product-signal-row";
    const header = document.createElement("div");
    header.className = "product-signal-meta";
    header.append(
      textElement("strong", "", productLabel(summary.product_code)),
      textElement("span", "", `${summary.matched_count.toLocaleString()} 条匹配 · ${summary.evidence_count.toLocaleString()} 条样本`),
    );
    const track = document.createElement("div");
    track.className = "product-signal-track";
    keys.forEach(([key, label]) => {
      const value = totals[key] || 0;
      const segment = document.createElement("i");
      segment.className = `product-signal-segment ${key}`;
      segment.style.flexBasis = total ? `${(value / total) * 100}%` : "0%";
      segment.title = `${label} ${value}`;
      track.append(segment);
    });
    const legend = document.createElement("div");
    legend.className = "product-signal-legend";
    keys.forEach(([key, label]) => {
      legend.append(textElement("span", key, `${label} ${totals[key] || 0}`));
    });
    row.append(header, track, legend);
    list.append(row);
  });
}

function renderWorkflowOutput(payload) {
  const output = document.querySelector("#workflow-output");
  output.replaceChildren();
  if (payload.comparison_rows.length) {
    const table = document.createElement("table");
    table.className = "output-table";
    const head = document.createElement("thead");
    const headRow = document.createElement("tr");
    ["商品", "信号", "方向", "强度", "匹配评论", "核查样本"].forEach((value) => headRow.append(textElement("th", "", value)));
    head.append(headRow);
    const body = document.createElement("tbody");
    payload.comparison_rows.forEach((item) => {
      const row = document.createElement("tr");
      [
        productLabel(item.product_code),
        item.signal,
        displayLabel(directionLabels, item.direction),
        displayLabel(strengthLabels, item.strength),
        item.matched_count.toLocaleString(),
        item.evidence_count.toLocaleString(),
      ].forEach((value) => row.append(textElement("td", "", value)));
      body.append(row);
    });
    table.append(head, body);
    output.append(table);
  }
  payload.triage_items.forEach((item) => {
    const row = document.createElement("div");
    row.className = "triage-row";
    row.append(textElement("span", "", String(item.rank).padStart(2, "0")));
    const detail = document.createElement("div");
    detail.append(
      textElement("strong", "", item.issue),
      textElement(
        "small",
        "",
        `${displayLabel(ownerLabels, item.owner)} · ${item.evidence_count} 条证据`,
      ),
    );
    row.append(detail, textElement("b", "priority", displayLabel(priorityLabels, item.priority)));
    output.append(row);
  });
}

const rowPolarityLabels = {
  supporting: "支持",
  opposing: "反对",
  conflicting: "冲突",
  insufficient: "信息不足",
};

async function loadEvidenceDetail(runToken, alias, container, button) {
  button.disabled = true;
  button.textContent = "加载原文…";
  try {
    const payload = await requestJson(`/api/v1/runs/${runToken}/evidence/${alias}`);
    container.replaceChildren();
    payload.rows.forEach((row) => {
      const rowBox = document.createElement("div");
      rowBox.className = "evidence-row";
      const meta = document.createElement("div");
      meta.className = "evidence-row-meta";
      const rating = row.rating === null ? "无评分" : `${row.rating} 星`;
      meta.append(
        textElement("span", `stamp ${row.polarity}`, displayLabel(rowPolarityLabels, row.polarity)),
        textElement("span", "", `${rating} · 第 ${row.locator_line} 行 · ${row.evidence_ref}`),
      );
      rowBox.append(meta, textElement("p", "", row.text));
      container.append(rowBox);
    });
    button.textContent = "收起原文 ▴";
    button.dataset.open = "true";
  } catch (error) {
    button.textContent = "查看原文 ▾";
    button.dataset.open = "false";
    container.replaceChildren(textElement("p", "detail-error", error.message));
  } finally {
    button.disabled = false;
  }
}

const insightStatusCopy = {
  MODEL_NOT_CONFIGURED: "未配置模型密钥,这一步不可用;其他分析与展示不受影响。",
  MODEL_UNAVAILABLE: "模型暂时不可用,结果未生成;其他分析与展示不受影响。",
  API_UNAVAILABLE: "模型暂时不可用,结果未生成;其他分析与展示不受影响。",
  KEY_REJECTED: "DeepSeek 拒绝了这把密钥(无效、过期或没有权限);请在右上角「模型设置」里换一个。",
  MODEL_RATE_LIMITED: "模型服务现在限流,等几十秒再点一次。",
  MODEL_REQUEST_REJECTED: "模型服务不接受这次请求(模型或参数不被接受);请在右上角「模型设置」换一个模型再试。",
  MODEL_OUTPUT_INVALID: "模型这次没有按约定格式返回(偶尔发生),再点一次「分析」重试。",
  NO_REVIEW_EVIDENCE: "这张证据卡没有可读取的评论样本。",
};

const insightViolationCopy = {
  UNKNOWN_ALIAS: "模型没有沿用给定的评论编号",
  EVIDENCE_NOT_COVERED: "有评论没有被逐条说明",
  DUPLICATE_ALIAS: "同一条评论被写了两次",
  EVIDENCE_NOT_CITED: "有评论被判为相关,却没有被引用",
  CITED_NOT_RELEVANT: "引用了被判为无关的评论",
  QUOTE_NOT_VERBATIM: "引用的原句在这条评论里找不到",
};

function renderInsightViolations(reason) {
  const codes = String(reason)
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
  const list = document.createElement("ul");
  list.className = "insight-list";
  codes.forEach((code) => {
    list.append(textElement("li", "", insightViolationCopy[code] || code));
  });
  return list;
}

function renderInsight(payload) {
  const box = document.createElement("div");
  box.className = "insight-result";
  box.append(
    textElement(
      "p",
      "insight-heading",
      `AI 分析 · 读取 ${payload.review_count} 条评论原文 · 调用 ${payload.model_calls} 次`,
    ),
  );
  if (payload.status !== "READY") {
    const failed = payload.status === "VALIDATION_FAILED" && payload.reason;
    box.append(
      textElement(
        "p",
        "insight-degraded",
        insightStatusCopy[payload.reason] ||
          (failed
            ? "模型这次的输出没有通过引用校验,整段丢弃,页面上不展示任何内容;可以再点一次重试。"
            : `AI 结果未能生成(${payload.reason || "UNKNOWN"});可以再点一次重试。`),
      ),
    );
    if (failed) {
      box.append(textElement("p", "insight-label", "没通过的地方"));
      box.append(renderInsightViolations(payload.reason));
      box.append(textElement("code", "insight-code", `错误码: ${payload.reason}`));
    }
    return box;
  }
  const line = (value) => textElement("p", "insight-line", value);
  const list = (items) => {
    const ul = document.createElement("ul");
    ul.className = "insight-list";
    items.forEach((item) => ul.append(textElement("li", "", item)));
    return ul;
  };
  const section = (title, className, children) => {
    const wrap = document.createElement("div");
    wrap.className = `insight-section ${className}`;
    wrap.append(textElement("strong", "insight-label", title));
    children.forEach((child) => wrap.append(child));
    return wrap;
  };

  const sections = [
    section("能说什么", "insight-supported", [line(payload.supported)]),
  ];
  if (payload.specifics.length) {
    sections.push(
      section(
        "具体的点",
        "insight-specifics",
        [
          list(
            payload.specifics.map(
              (item) => `${item.text}(${item.evidence_aliases.join("、")})`,
            ),
          ),
        ],
      ),
    );
  }
  if (payload.rejected.length) {
    sections.push(
      section(
        "读作无关的评论",
        "insight-rejected",
        [list(payload.rejected.map((item) => `${item.evidence_alias}: ${item.quote}`))],
      ),
    );
  }
  sections.push(section("不能说什么", "insight-not-supported", [line(payload.not_supported)]));
  sections.forEach((item) => box.append(item));
  box.append(
    textElement(
      "p",
      "insight-footnote",
      "上面每句引用都已核对到评论原句;AI 只做归纳,证据够不够仍由代码规则决定。",
    ),
  );
  return box;
}

const INSIGHT_KEY_STORAGE = "wb-insight-key";
const INSIGHT_KEY_REMEMBER_STORAGE = "wb-insight-key-remember";
const INSIGHT_MODEL_STORAGE = "wb-insight-model";

function sanitizeInsightKey(value) {
  // HTTP header values must be Latin-1. Keys copied from a chat or a web page
  // sometimes carry invisible characters (zero-width space, full-width
  // punctuation); keeping printable ASCII makes the header sent to the server
  // valid and recovers a key that picked up one stray character.
  return String(value || "").replace(/[^\x21-\x7e]/g, "");
}

function readInsightKey() {
  try {
    for (const storage of [sessionStorage, localStorage]) {
      const stored = storage.getItem(INSIGHT_KEY_STORAGE) || "";
      if (!stored) continue;
      const clean = sanitizeInsightKey(stored);
      if (clean !== stored) {
        if (clean) {
          storage.setItem(INSIGHT_KEY_STORAGE, clean);
        } else {
          storage.removeItem(INSIGHT_KEY_STORAGE);
        }
      }
      if (clean) return clean;
    }
    return "";
  } catch (error) {
    return "";
  }
}

function writeInsightKey(value, remember = false) {
  const clean = sanitizeInsightKey(value);
  const target = remember ? localStorage : sessionStorage;
  const other = remember ? sessionStorage : localStorage;
  try {
    if (clean) {
      target.setItem(INSIGHT_KEY_STORAGE, clean);
    } else {
      target.removeItem(INSIGHT_KEY_STORAGE);
    }
  } catch (error) {
    /* storage unavailable: the key then only lives for this call */
  }
  try {
    other.removeItem(INSIGHT_KEY_STORAGE);
  } catch (error) {
    /* ignore a storage this browser blocks */
  }
  try {
    localStorage.setItem(INSIGHT_KEY_REMEMBER_STORAGE, remember ? "true" : "false");
  } catch (error) {
    /* ignore a storage this browser blocks */
  }
}

function readInsightModel() {
  try {
    const stored = String(localStorage.getItem(INSIGHT_MODEL_STORAGE) || "").trim();
    if (!stored) return "";
    const select = document.querySelector("#settings-model");
    if (select && [...select.options].some((option) => option.value === stored)) {
      return stored;
    }
    return "";
  } catch (error) {
    return "";
  }
}

function writeInsightModel(value) {
  try {
    const clean = String(value || "").trim();
    if (clean) {
      localStorage.setItem(INSIGHT_MODEL_STORAGE, clean);
    } else {
      localStorage.removeItem(INSIGHT_MODEL_STORAGE);
    }
  } catch (error) {
    /* ignore a storage this browser blocks */
  }
}

function insightRequestHeaders() {
  const headers = { "Content-Type": "application/json" };
  const userKey = readInsightKey();
  if (userKey) headers["X-Insight-Key"] = userKey;
  const model = readInsightModel();
  if (model) headers["X-Insight-Model"] = model;
  return headers;
}

const settingsToggle = document.querySelector("#settings-toggle");
const settingsDialog = document.querySelector("#settings-dialog");
const settingsKey = document.querySelector("#settings-key");
const settingsRemember = document.querySelector("#settings-remember");
const settingsModel = document.querySelector("#settings-model");
const settingsStatus = document.querySelector("#settings-status");
const settingsNote = document.querySelector("#settings-note");
const settingsModelLabel = document.querySelector("#settings-model-label");

function storedKeyIsRemembered() {
  try {
    return Boolean(localStorage.getItem(INSIGHT_KEY_STORAGE));
  } catch (error) {
    return false;
  }
}

function modelLabelFor(value) {
  if (!settingsModel || !value) return "";
  const option = [...settingsModel.options].find((item) => item.value === value);
  return option ? option.textContent : "";
}

function applySettingsLabel() {
  if (!settingsModelLabel) return;
  const model = readInsightModel() || settingsModel?.value || "";
  const label = modelLabelFor(model);
  settingsModelLabel.textContent = label ? ` · ${label}` : "";
}

function openSettings() {
  if (!settingsDialog) return;
  const userKey = readInsightKey();
  if (settingsKey) settingsKey.value = userKey;
  if (settingsRemember) settingsRemember.checked = userKey ? storedKeyIsRemembered() : true;
  if (settingsModel) {
    settingsModel.value = readInsightModel() || settingsModel.options[0]?.value || "";
  }
  if (settingsNote) settingsNote.textContent = "";
  if (settingsStatus) {
    settingsStatus.textContent = userKey
      ? "密钥已保存在这个浏览器里;换模型只影响这台电脑上的这次使用。"
      : "还没有保存密钥。填一把 DeepSeek API Key 后,AI 分析与问答才可用。";
  }
  if (typeof settingsDialog.showModal === "function") {
    settingsDialog.showModal();
  } else {
    settingsDialog.setAttribute("open", "");
  }
  settingsKey?.focus();
}

function saveSettings() {
  if (!settingsKey) return;
  const raw = settingsKey.value.trim();
  const clean = sanitizeInsightKey(raw);
  if (raw && clean.length < 8) {
    if (settingsNote) {
      settingsNote.textContent =
        "密钥只能包含英文字母、数字和 - _ 等符号;刚才的内容里有中文或不可见字符,请重新粘贴。";
    }
    settingsKey.focus();
    settingsKey.select();
    return;
  }
  writeInsightKey(clean, Boolean(settingsRemember?.checked));
  writeInsightModel(settingsModel?.value);
  applySettingsLabel();
  if (settingsDialog?.open) settingsDialog.close();
}

if (settingsToggle && settingsDialog) {
  applySettingsLabel();
  settingsToggle.addEventListener("click", openSettings);
  settingsDialog.querySelector("#settings-save")?.addEventListener("click", saveSettings);
  settingsDialog.querySelector("#settings-clear")?.addEventListener("click", () => {
    writeInsightKey("");
    if (settingsKey) settingsKey.value = "";
    if (settingsNote) settingsNote.textContent = "密钥已从浏览器里清除。";
    applySettingsLabel();
  });
  settingsDialog
    .querySelector("#settings-close")
    ?.addEventListener("click", () => settingsDialog.close());
  settingsDialog.addEventListener("close", () => {
    if (settingsNote) settingsNote.textContent = "";
  });
}

function openInsightKeyPanel(panel, run) {
  panel.replaceChildren();
  const label = textElement("p", "insight-key-label", "用你自己的 DeepSeek API Key 跑这一步");
  const input = document.createElement("input");
  input.type = "password";
  input.autocomplete = "off";
  input.spellcheck = false;
  input.className = "insight-key-input";
  input.placeholder = "sk-...";
  const submit = textElement("button", "insight-key-submit", "用这个密钥分析");
  submit.type = "button";
  const note = textElement(
    "p",
    "insight-key-note",
    "密钥只存在这个标签页里,关掉即清除;它只用于这次分析,不会写进日志或磁盘。",
  );
  submit.addEventListener("click", () => {
    const value = input.value.trim();
    const clean = sanitizeInsightKey(value);
    if (clean.length < 8) {
      note.textContent =
        "密钥只能包含英文字母、数字和 - _ 等符号;刚才的内容里有中文或不可见字符,请重新粘贴。";
      input.focus();
      input.select();
      return;
    }
    writeInsightKey(clean, storedKeyIsRemembered());
    panel.hidden = true;
    run();
  });
  panel.append(label, input, submit, note);
  panel.hidden = false;
  input.focus();
}

async function loadInsight(runToken, cardAlias, container, button) {
  button.disabled = true;
  button.textContent = "分析中…";
  const headers = insightRequestHeaders();
  try {
    const question =
      state.runQuestionToken === runToken ? state.runQuestion.slice(0, 300) : "";
    const payload = await requestJson("/api/v1/insights", {
      method: "POST",
      headers,
      body: JSON.stringify({
        run_token: runToken,
        card_alias: cardAlias,
        ...(question ? { question } : {}),
      }),
    });
    container.replaceChildren(renderInsight(payload));
    container.hidden = false;
    if (payload.status === "READY") {
      button.textContent = "AI 分析结果 ▾";
    } else if (payload.reason === "MODEL_NOT_CONFIGURED" && !state.insightAvailable) {
      writeInsightKey("");
      button.textContent = "AI 深入分析 ▸";
      button.disabled = false;
    } else {
      button.textContent = "再试一次 ▸";
      button.disabled = false;
    }
  } catch (error) {
    container.replaceChildren(textElement("p", "insight-degraded", error.message));
    container.hidden = false;
    button.textContent = "再试一次 ▸";
    button.disabled = false;
  }
}

const reviewMarks = [
  ["confirmed", "review-confirmed", "确认"],
  ["doubtful", "review-doubtful", "存疑"],
  ["rejected", "review-rejected", "驳回"],
];
const reviewProgressLabels = { confirmed: "已确认", doubtful: "存疑", rejected: "驳回" };

function setCardVerdict(card, value) {
  if (value) {
    card.dataset.review = value;
  } else {
    delete card.dataset.review;
  }
  card.querySelectorAll("button[data-verdict]").forEach((button) => {
    button.setAttribute("aria-pressed", String(button.dataset.verdict === value));
  });
}

function setVerdictNotice(message) {
  const notice = document.querySelector("#verdict-notice");
  if (!notice) {
    return;
  }
  notice.textContent = message || "";
  notice.hidden = !message;
}

async function loadVerdicts(runToken) {
  if (!runToken) {
    return;
  }
  try {
    const payload = await requestJson(`/api/v1/runs/${runToken}/verdicts`);
    const byAlias = new Map(
      (payload.records || []).map((record) => [record.card_alias, record.verdict]),
    );
    document.querySelectorAll(".evidence-item[data-alias]").forEach((card) => {
      const value = byAlias.get(card.dataset.alias);
      if (value) {
        setCardVerdict(card, value);
      }
    });
    updateReviewProgress();
  } catch (error) {
    setVerdictNotice(`读取已有核对记录失败: ${error.message}`);
  }
}

function updateReviewProgress() {
  const line = document.querySelector("#review-progress");
  const counts = { confirmed: 0, doubtful: 0, rejected: 0 };
  document.querySelectorAll(".evidence-item[data-review]").forEach((card) => {
    counts[card.dataset.review] += 1;
  });
  const marked = counts.confirmed + counts.doubtful + counts.rejected;
  if (!marked) {
    line.hidden = true;
    return;
  }
  line.textContent =
    `核对进度 ${marked}/${document.querySelectorAll(".evidence-item").length} · ` +
    `确认 ${counts.confirmed} · 存疑 ${counts.doubtful} · 驳回 ${counts.rejected}`;
  line.hidden = false;
}

function renderEvidence(evidence) {
  const list = document.querySelector("#evidence-list");
  list.replaceChildren();
  document.querySelector("#review-progress").hidden = true;
  evidence.forEach((item) => {
    const card = document.createElement("article");
    card.className = "evidence-item";
    card.tabIndex = -1;
    card.dataset.alias = item.evidence_alias;
    card.dataset.productCode = item.product_code;
    const meta = document.createElement("div");
    meta.className = "evidence-meta";
    meta.append(textElement("span", "", `证据卡 ${item.evidence_alias.replace("EV-", "")} · ${productLabel(item.product_code)}`));
    const metaRight = document.createElement("span");
    metaRight.className = "evidence-meta-right";
    metaRight.append(
      textElement("span", `polarity-stamp stamp ${item.polarity}`, displayLabel(polarityLabels, item.polarity)),
      textElement("span", "meta-strength", displayLabel(strengthLabels, item.strength)),
    );
    meta.append(metaRight);
    card.append(meta, textElement("p", "", item.excerpt));
    const reviewStrip = document.createElement("div");
    reviewStrip.className = "review-strip";
    reviewStrip.setAttribute("role", "group");
    reviewStrip.setAttribute("aria-label", `人工核对 证据卡 ${item.evidence_alias}`);
    reviewMarks.forEach(([value, className, label]) => {
      const mark = textElement("button", className, label);
      mark.type = "button";
      mark.setAttribute("aria-pressed", "false");
      mark.dataset.verdict = value;
      mark.addEventListener("click", async () => {
        if (card.dataset.review === value) {
          return;
        }
        const previous = card.dataset.review;
        setCardVerdict(card, value);
        updateReviewProgress();
        try {
          await requestJson("/api/v1/verdicts", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              run_token: state.runToken,
              card_alias: card.dataset.alias,
              verdict: value,
            }),
          });
        } catch (error) {
          if (previous) {
            setCardVerdict(card, previous);
          } else {
            setCardVerdict(card, "");
          }
          updateReviewProgress();
          setVerdictNotice(`判定没有记录成功: ${error.message}`);
        }
      });
      reviewStrip.append(mark);
    });
    card.append(reviewStrip);
    const localMode = document.querySelector("#workbench-mode").textContent === "LOCAL";
    if (state.runToken && localMode) {
      const toggle = textElement("button", "detail-toggle", "查看原文 ▾");
      toggle.type = "button";
      toggle.dataset.open = "false";
      const detailBox = document.createElement("div");
      detailBox.className = "evidence-detail";
      toggle.addEventListener("click", () => {
        if (toggle.dataset.open === "true") {
          detailBox.replaceChildren();
          toggle.textContent = "查看原文 ▾";
          toggle.dataset.open = "false";
        } else {
          loadEvidenceDetail(state.runToken, item.evidence_alias, detailBox, toggle);
        }
      });
      card.append(toggle, detailBox);
      if (state.insightAvailable || state.insightByo) {
        const insightToggle = textElement("button", "detail-toggle insight-toggle", "AI 深入分析 ▸");
        insightToggle.type = "button";
        const insightBox = document.createElement("div");
        insightBox.className = "insight-output";
        insightBox.hidden = true;
        const keyPanel = document.createElement("div");
        keyPanel.className = "insight-key";
        keyPanel.hidden = true;
        insightToggle.addEventListener("click", () => {
          if (state.insightAvailable || readInsightKey()) {
            loadInsight(
              state.runToken,
              item.evidence_alias,
              insightBox,
              insightToggle,
            );
            return;
          }
          openInsightKeyPanel(keyPanel, () =>
            loadInsight(
              state.runToken,
              item.evidence_alias,
              insightBox,
              insightToggle,
            ),
          );
        });
        card.append(insightToggle, keyPanel, insightBox);
      } else {
        const unavailable = textElement(
          "button",
          "detail-toggle insight-toggle",
          "AI 深入分析(需配置密钥)",
        );
        unavailable.type = "button";
        unavailable.disabled = true;
        card.append(unavailable);
      }
    }
    list.append(card);
  });
  updateReviewProgress();
}

function focusEvidenceCard(cards, index) {
  cards.forEach((card) => card.classList.remove("is-focused"));
  const card = cards[index];
  if (!card) return;
  card.classList.add("is-focused");
  card.focus({ preventScroll: true });
  card.scrollIntoView({
    block: "nearest",
    behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth",
  });
}

document.addEventListener("keydown", (event) => {
  if (event.altKey || event.ctrlKey || event.metaKey) return;
  if (["INPUT", "TEXTAREA", "SELECT"].includes(event.target.tagName)) return;
  const cards = [...document.querySelectorAll(".evidence-item")];
  if (!cards.length) return;
  const current = cards.findIndex((card) => card.classList.contains("is-focused"));
  if (event.key === "j" || event.key === "J") {
    event.preventDefault();
    focusEvidenceCard(cards, Math.min(cards.length - 1, current + 1));
  } else if (event.key === "k" || event.key === "K") {
    event.preventDefault();
    focusEvidenceCard(cards, Math.max(0, current));
  } else if (event.key === "Enter") {
    const index = current >= 0 ? current : 0;
    event.preventDefault();
    if (current < 0) {
      focusEvidenceCard(cards, index);
      return;
    }
    const toggle = cards[index].querySelector(".detail-toggle");
    if (toggle) toggle.click();
  }
});

function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  const button = document.querySelector("#theme-toggle");
  if (button) button.textContent = theme === "dark" ? "浅色" : "深色";
  try { localStorage.setItem("wb-theme", theme); } catch (error) { /* storage unavailable */ }
}

document.addEventListener("DOMContentLoaded", () => {
  const button = document.querySelector("#theme-toggle");
  if (button) {
    button.textContent = document.documentElement.dataset.theme === "dark" ? "浅色" : "深色";
    button.addEventListener("click", () => {
      applyTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark");
    });
  }
});

function renderClarification(payload) {
  const output = document.querySelector("#workflow-output");
  output.replaceChildren();
  const choices = document.createElement("div");
  choices.className = "clarification-choices";
  payload.clarification.choices.forEach((choice) => {
    const button = textElement("button", "", choice.label);
    button.type = "button";
    button.addEventListener("click", () => resumeRun(choice.code));
    choices.append(button);
  });
  output.append(choices);
}

function renderResult(payload, options = {}) {
  state.runToken = payload.run_token;
  payload.products.forEach((product) => { state.productLabels[product.code] = product.label; });
  document.querySelector("#run-alias").textContent = payload.run_alias;
  document.querySelector("#result-title").textContent = payload.title;
  document.querySelector("#result-summary").textContent = payload.summary;
  document.querySelector("#matched-count").textContent = payload.matched_count.toLocaleString();
  document.querySelector("#evidence-count").textContent = String(payload.evidence_count);
  document.querySelector("#degraded-value").textContent = payload.degraded ? "已触发" : "未触发";
  document.querySelector("#next-action").textContent = payload.next_action;
  renderBlindSpot(payload.lexicon_blind_spot);
  setStatus(payload.status);
  renderBars(payload);
  renderLexiconTotals(payload);
  renderCoverage(payload);
  renderProductSignals(payload);
  renderEvidence(payload.evidence);
  if (!options.keepChat) resetQa();
  if (chatRunHint) chatRunHint.textContent = `当前运行: ${payload.run_alias}`;
  setVerdictNotice("");
  loadVerdicts(payload.run_token);
  if (payload.clarification) renderClarification(payload);
  else renderWorkflowOutput(payload);
}

async function requestJson(url, options) {
  const response = await fetch(url, options);
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error?.message || "Request could not be completed.");
  return payload;
}

async function resumeRun(productCode) {
  try {
    const payload = await requestJson(`/api/v1/runs/${state.runToken}/resume`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ selected_product_codes: [productCode] }),
    });
    renderResult(payload);
  } catch (error) {
    formError.textContent = error.message;
  }
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  formError.textContent = "";
  submitButton.disabled = true;
  try {
    const question = document.querySelector("#question").value.trim();
    const payload = await requestJson("/api/v1/runs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        workflow: state.workflow,
        product_codes: selectedProducts(),
        product_query: selectedProducts().length ? null : "countertop",
        aspect: document.querySelector("#aspect").value,
        question,
      }),
    });
    state.runQuestion = question;
    state.runQuestionToken = payload.run_token;
    renderResult(payload);
  } catch (error) {
    setStatus("PROVIDER_ERROR");
    formError.textContent = error.message;
  } finally {
    submitButton.disabled = false;
  }
});

document.querySelector("#aspect").addEventListener("change", (event) => {
  document.querySelector("#question").value = aspectQuestions[event.target.value] || "请记录需要运营优先核查的评论信号。";
});

const chatScroll = document.querySelector("#chat-scroll");
const chatEmpty = document.querySelector("#chat-empty");
const chatChips = document.querySelector("#chat-chips");
const chatRunHint = document.querySelector("#chat-run-hint");
const chatSend = document.querySelector("#chat-send");
const qaForm = document.querySelector("#qa-form");
const qaInput = document.querySelector("#qa-input");

const CHAT_SUGGESTIONS = [
  "对比 GE Profile 和 EUHOMY 的清洁维护问题",
  "买家对噪音的具体抱怨有哪些?给原话例子",
  "1星和2星的差评大概占多少?",
  "这批评论能不能说明噪音是普遍问题?",
];

function qaMessage(className, value) {
  if (!chatScroll) return null;
  chatEmpty.hidden = true;
  const bubble = textElement("div", `qa-bubble ${className}`, value);
  chatScroll.append(bubble);
  chatScroll.scrollTop = chatScroll.scrollHeight;
  return bubble;
}

function qaTyping() {
  const bubble = qaMessage("qa-system", "");
  if (!bubble) return null;
  bubble.classList.add("chat-typing");
  bubble.textContent = "";
  bubble.append(document.createElement("i"), document.createElement("i"), document.createElement("i"));
  return bubble;
}

function formatStepDuration(value) {
  const ms = Number(value) || 0;
  if (ms <= 0) return "";
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${ms}ms`;
}

function qaTrace(steps) {
  if (!chatScroll || !steps.length) return;
  const details = document.createElement("details");
  details.className = "chat-trace";
  details.open = true;
  details.append(textElement("summary", "", `执行过程 · ${steps.length} 步`));
  const list = document.createElement("ol");
  list.className = "trace-list";
  steps.forEach((step) => {
    const item = document.createElement("li");
    item.className = step.status === "error" ? "trace-step trace-error" : "trace-step";
    const head = document.createElement("div");
    head.className = "trace-head";
    head.append(
      textElement("span", "trace-index", String(step.index)),
      textElement("span", "trace-title", step.title),
    );
    const duration = formatStepDuration(step.duration_ms);
    if (duration) head.append(textElement("span", "trace-time", duration));
    if (step.status === "error") head.append(textElement("span", "trace-badge", "失败"));
    item.append(head);
    if (Array.isArray(step.detail) && step.detail.length) {
      const detail = document.createElement("ul");
      detail.className = "trace-detail";
      step.detail.forEach((line) => detail.append(textElement("li", "", line)));
      item.append(detail);
    }
    list.append(item);
  });
  details.append(list);
  chatScroll.append(details);
  chatScroll.scrollTop = chatScroll.scrollHeight;
}

function resetQa() {
  if (!chatScroll || !chatEmpty) return;
  chatScroll.replaceChildren(chatEmpty);
  chatEmpty.hidden = false;
}

function renderChatChips() {
  if (!chatChips) return;
  chatChips.replaceChildren();
  CHAT_SUGGESTIONS.forEach((text) => {
    const chip = textElement("button", "", text);
    chip.type = "button";
    chip.addEventListener("click", () => {
      if (!state.runToken) {
        qaMessage("qa-system", "请先点「开始分析」跑一次,再回到这里提问。");
        return;
      }
      qaMessage("chat-user", text);
      qaAsk(text);
    });
    chatChips.append(chip);
  });
}
renderChatChips();

const chatViolationCopy = {
  UNKNOWN_CITATION: "回答引用了没有读过的评论编号",
  QUOTE_NOT_VERBATIM: "引用的原句在评论原文里找不到",
  UNREFERENCED_ANSWER: "回答没有任何依据(既没引用评论也没用统计)",
  STATS_MISMATCH: "回答里的统计数字和查询结果不一致",
  UNKNOWN_STAT_REF: "回答引用了不存在的统计项",
  ALIAS_IN_TEXT: "回答正文里混入了内部编号",
  NUMBER_NOT_TRACEABLE:
    "回答里的数字在当次读到的评论或查询结果里找不到（模型不许自己算合计或占比）;改成直接问某个具体数字更容易通过。",
  CHAT_SESSION_LIMIT: "这个运行的对话轮次已用完;重新「开始分析」即可开启新对话。",
  MODEL_OUTPUT_INVALID: "模型这次没有按约定格式返回,这一轮丢弃;再发一次试试。",
  MODEL_ROUNDS_EXCEEDED: "模型查询次数超出上限,这一轮丢弃;可以重新提问。",
  API_UNAVAILABLE: "模型暂时不可用,结果未生成;可以稍后重试。",
  KEY_REJECTED: "DeepSeek 拒绝了这把密钥(无效、过期或没有权限);点右上角「模型设置」换一个再试。",
  MODEL_RATE_LIMITED: "模型服务现在限流,等几十秒再试。",
  MODEL_REQUEST_REJECTED: "模型服务不接受这次请求(模型或参数不被接受);点右上角「模型设置」换一个模型再试。",
};

function describeViolations(reason) {
  const codes = String(reason)
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
  const counts = new Map();
  codes.forEach((code) => counts.set(code, (counts.get(code) || 0) + 1));
  return [...counts.entries()]
    .map(([code, count]) => {
      const label = chatViolationCopy[code] || code;
      return count > 1 ? `${label} ×${count}` : label;
    })
    .join("; ");
}

function renderChatAnswer(payload) {
  const box = document.createElement("div");
  box.className = "insight-result";
  box.append(
    textElement(
      "p",
      "insight-heading",
      `智能体回答 · 调用 ${payload.model_calls} 次模型 · 对话第 ${payload.session_turns} 轮`,
    ),
  );
  const line = (value) => textElement("p", "insight-line", value);
  const section = (title, className, children) => {
    const wrap = document.createElement("div");
    wrap.className = `insight-section ${className}`;
    wrap.append(textElement("strong", "insight-label", title));
    children.forEach((child) => wrap.append(child));
    return wrap;
  };
  const sections = [section("能说什么", "insight-supported", [line(payload.supported)])];
  const points = document.createElement("ul");
  points.className = "insight-list";
  payload.review_points.forEach((item) => {
    const li = document.createElement("li");
    li.textContent = item.text;
    if (item.citation) {
      li.append(textElement("span", "qa-cite", `〔${item.citation}:${item.quote}〕`));
    }
    points.append(li);
  });
  payload.stats_points.forEach((item) => {
    const li = document.createElement("li");
    li.textContent = item.text;
    li.append(
      textElement(
        "span",
        "qa-cite",
        `〔统计 ${item.stat_key}·${item.product}·${item.metric}=${item.value}〕`,
      ),
    );
    points.append(li);
  });
  if (payload.review_points.length || payload.stats_points.length) {
    sections.push(section("依据(引用已逐字核对,数字均可追溯)", "insight-specifics", [points]));
  }
  sections.push(section("不能说什么", "insight-not-supported", [line(payload.not_supported)]));
  sections.forEach((item) => box.append(item));
  box.append(
    textElement(
      "p",
      "insight-footnote",
      "回答里的每条引用都逐字核对到评论原文,正文里的阿拉伯数字逐个对照当次读到的评论和查询结果;核对不过的内容整条丢弃,不会出现在这里。",
    ),
  );
  return box;
}

async function qaAsk(message) {
  const headers = insightRequestHeaders();
  if (chatSend) chatSend.disabled = true;
  const pending = qaTyping();
  const scrollToBottom = () => {
    chatScroll.scrollTop = chatScroll.scrollHeight;
  };
  try {
    const payload = await requestJson("/api/v1/chat", {
      method: "POST",
      headers,
      body: JSON.stringify({ run_token: state.runToken, message }),
    });
    if (pending) pending.remove();
    qaTrace(payload.steps || []);
    if (payload.created_run_token) {
      try {
        const runPayload = await requestJson(`/api/v1/runs/${payload.created_run_token}`);
        renderResult(runPayload, { keepChat: true });
      } catch (error) {
        qaMessage("qa-system", `新运行的结果加载失败: ${error.message}`);
      }
    }
    if (payload.status === "READY") {
      const answer = document.createElement("div");
      answer.className = "chat-answer";
      answer.append(renderChatAnswer(payload));
      chatScroll.append(answer);
      scrollToBottom();
      return;
    }
    if (payload.reason === "MODEL_NOT_CONFIGURED" && state.insightByo) {
      const keyPanel = document.createElement("div");
      keyPanel.className = "insight-key";
      openInsightKeyPanel(keyPanel, () => qaAsk(message));
      chatScroll.append(keyPanel);
      scrollToBottom();
      return;
    }
    qaMessage(
      "qa-system",
      payload.status === "VALIDATION_FAILED"
        ? `回答未通过校验: ${describeViolations(payload.reason)};整条丢弃,可以换个问法重试。`
        : chatViolationCopy[payload.reason] ||
          `这一轮没有生成回答(${payload.reason || "UNKNOWN"});可以重试。`,
    );
  } catch (error) {
    if (pending) pending.remove();
    qaMessage("qa-system", `提问失败: ${error.message}`);
  } finally {
    if (chatSend) chatSend.disabled = false;
  }
}

if (qaForm) {
  qaForm.addEventListener("submit", (event) => {
    event.preventDefault();
    const message = qaInput.value.trim();
    if (!message) return;
    if (!state.runToken) {
      qaMessage("qa-system", "请先点「开始分析」跑一次,再回到这里提问。");
      return;
    }
    qaInput.value = "";
    qaInput.style.height = "auto";
    qaMessage("chat-user", message);
    qaAsk(message);
  });
  qaInput.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      qaForm.requestSubmit();
    }
  });
  qaInput.addEventListener("input", () => {
    qaInput.style.height = "auto";
    qaInput.style.height = `${Math.min(qaInput.scrollHeight, 120)}px`;
  });
}

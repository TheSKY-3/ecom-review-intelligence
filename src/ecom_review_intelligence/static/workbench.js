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

function renderBars(payload) {
  const fallback = { supporting: 0, opposing: 0, conflicting: 0, insufficient: 0 };
  payload.evidence.forEach((item) => { fallback[item.polarity] += 1; });
  const totals = payload.signal_totals && Object.keys(payload.signal_totals).length
    ? payload.signal_totals
    : fallback;
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
            : "AI 结果未能生成;可以再点一次重试。"),
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

function readInsightKey() {
  try {
    return sessionStorage.getItem(INSIGHT_KEY_STORAGE) || "";
  } catch (error) {
    return "";
  }
}

function writeInsightKey(value) {
  try {
    if (value) {
      sessionStorage.setItem(INSIGHT_KEY_STORAGE, value);
    } else {
      sessionStorage.removeItem(INSIGHT_KEY_STORAGE);
    }
  } catch (error) {
    /* storage unavailable: the key then only lives for this call */
  }
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
    if (!value) {
      input.focus();
      return;
    }
    writeInsightKey(value);
    panel.hidden = true;
    run();
  });
  panel.append(label, input, submit, note);
  panel.hidden = false;
  input.focus();
}

async function loadInsight(runToken, cardAlias, container, button, userKey = "") {
  button.disabled = true;
  button.textContent = "分析中…";
  const headers = { "Content-Type": "application/json" };
  if (userKey) {
    headers["X-Insight-Key"] = userKey;
  }
  try {
    const payload = await requestJson("/api/v1/insights", {
      method: "POST",
      headers,
      body: JSON.stringify({ run_token: runToken, card_alias: cardAlias }),
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
              readInsightKey(),
            );
            return;
          }
          openInsightKeyPanel(keyPanel, () =>
            loadInsight(
              state.runToken,
              item.evidence_alias,
              insightBox,
              insightToggle,
              readInsightKey(),
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

function renderResult(payload) {
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
  renderCoverage(payload);
  renderProductSignals(payload);
  renderEvidence(payload.evidence);
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
    const payload = await requestJson("/api/v1/runs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        workflow: state.workflow,
        product_codes: selectedProducts(),
        product_query: selectedProducts().length ? null : "countertop",
        aspect: document.querySelector("#aspect").value,
        question: document.querySelector("#question").value,
      }),
    });
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

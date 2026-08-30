const workflowCopy = {
  diagnosis: { title: "SKU 体验诊断", code: "DIAGNOSIS" },
  comparison: { title: "竞品证据比较", code: "COMPARISON" },
  triage: { title: "运营问题分诊", code: "TRIAGE" },
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
  advantage: "占优",
  watch: "观察",
  mixed: "交错",
  insufficient: "证据不足",
};

const priorityLabels = {
  high: "高",
  medium: "中",
};

const ownerLabels = {
  Product: "产品团队",
  Quality: "质量团队",
  "Customer support": "客服团队",
};

function displayLabel(labels, code) {
  return labels[code] || code;
}

const state = { workflow: "diagnosis", runToken: null };
const form = document.querySelector("#run-form");
const formError = document.querySelector("#form-error");
const submitButton = form.querySelector("button[type='submit']");

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
  VERIFIED_RESULT: "已通过本地校验,结果可继续查看",
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

function renderBars(payload) {
  const fallback = { supporting: 0, opposing: 0, conflicting: 0 };
  payload.evidence.forEach((item) => { fallback[item.polarity] += 1; });
  const totals = payload.signal_totals && Object.keys(payload.signal_totals).length
    ? payload.signal_totals
    : fallback;
  const maximum = Math.max(1, totals.supporting, totals.opposing, totals.conflicting);
  [["supporting", "支持"], ["opposing", "反对"], ["conflicting", "冲突"]].forEach(([key, name]) => {
    const value = totals[key] || 0;
    document.querySelector(`#bar-${key}`).style.width = `${8 + (value / maximum) * 92}%`;
    document.querySelector(`#label-${key}`).textContent = `${name} · ${value}`;
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
    ["商品", "信号", "方向", "强度"].forEach((value) => headRow.append(textElement("th", "", value)));
    head.append(headRow);
    const body = document.createElement("tbody");
    payload.comparison_rows.forEach((item) => {
      const row = document.createElement("tr");
      [
        item.product_code,
        item.signal,
        displayLabel(directionLabels, item.direction),
        displayLabel(strengthLabels, item.strength),
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
        textElement("span", "row-polarity", displayLabel(rowPolarityLabels, row.polarity)),
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

function renderEvidence(evidence) {
  const list = document.querySelector("#evidence-list");
  list.replaceChildren();
  evidence.forEach((item) => {
    const card = document.createElement("article");
    card.className = "evidence-item";
    const meta = document.createElement("div");
    meta.className = "evidence-meta";
    meta.append(textElement("span", "", `${item.evidence_alias} · ${item.product_code}`));
    meta.append(
      textElement(
        "span",
        "",
        `${displayLabel(polarityLabels, item.polarity)} · ${displayLabel(strengthLabels, item.strength)}`,
      ),
    );
    card.append(meta, textElement("p", "", item.excerpt));
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
    }
    list.append(card);
  });
}

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
  document.querySelector("#run-alias").textContent = payload.run_alias;
  document.querySelector("#result-title").textContent = payload.title;
  document.querySelector("#result-summary").textContent = payload.summary;
  document.querySelector("#evidence-count").textContent = String(payload.evidence_count);
  document.querySelector("#degraded-value").textContent = payload.degraded ? "已触发" : "未触发";
  document.querySelector("#next-action").textContent = payload.next_action;
  setStatus(payload.status);
  renderBars(payload);
  renderEvidence(payload.evidence);
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

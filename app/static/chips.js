"use strict";

const $ = (id) => document.getElementById(id);
let summary = null;
let currentSubject = {type: "human", id: null, name: "我"};
let subjectRequestSequence = 0;
let currentExchange = null;
let currentLoanView = null;

function apiPath(path) {
  const pathname = window.location.pathname;
  const underDuel = pathname === "/duel" || pathname.startsWith("/duel/");
  if (!underDuel || !path.startsWith("/") || path === "/duel" || path.startsWith("/duel/")) {
    return path;
  }
  return `/duel${path}`;
}

async function requestJson(url, options = {}) {
  const response = await fetch(apiPath(url), {
    credentials: "same-origin",
    headers: {"Content-Type": "application/json", ...(options.headers || {})},
    ...options,
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok || payload.ok === false) {
    throw new Error(payload.message || `请求失败（${response.status}）`);
  }
  return payload;
}

function unreadCount(category) {
  return Number(summary?.unread?.categories?.[category] || 0);
}

function renderUnreadBadges() {
  const specs = [
    ["achievementUnreadBadge", "achievement", "成就"],
    ["exchangeUnreadBadge", "exchange", "兑换"],
    ["loanUnreadBadge", "loan", "借款"],
  ];
  for (const [elementId, category, label] of specs) {
    const count = unreadCount(category);
    const badge = $(elementId);
    if (!badge) continue;
    badge.textContent = String(count);
    badge.classList.toggle("hidden", count <= 0);
    badge.setAttribute("aria-label", `${label}（未读${count}）`);
  }
}

async function ackUnreadCategory(category) {
  if (!summary || document.hidden) return;
  try {
    const payload = await requestJson("/api/notifications/read", {
      method: "POST",
      body: JSON.stringify({category}),
    });
    summary.unread = payload.unread;
    renderUnreadBadges();
  } catch (_error) {
    // Keep the selected module usable; a later visit retries the explicit ack.
  }
}

function showNotice(message, isError = false) {
  const notice = $("notice");
  notice.textContent = message;
  notice.classList.toggle("error", isError);
  notice.classList.remove("hidden");
}

function validationError(message, field = null) {
  const error = new Error(message);
  error.field = field;
  return error;
}

function clearFormError(errorId, form) {
  const errorBox = $(errorId);
  errorBox.textContent = "";
  errorBox.classList.add("hidden");
  for (const field of form.querySelectorAll('[aria-invalid="true"]')) {
    field.removeAttribute("aria-invalid");
  }
}

function showFormError(errorId, error) {
  const errorBox = $(errorId);
  errorBox.textContent = error.message || "表单内容有误，请检查后重试";
  errorBox.classList.remove("hidden");
  if (error.field) {
    error.field.setAttribute("aria-invalid", "true");
    error.field.focus();
  }
}

function selectedBoundMachine(select, label) {
  if (!summary) throw validationError("筹码资料仍在加载，请稍候再试", select);
  const machineId = String(select.value || "").trim();
  if (!machineId) throw validationError(`请选择${label}`, select);
  if (!summary.machines.some((machine) => machine.id === machineId)) {
    throw validationError(`所选${label}不在当前账号的绑定清单中`, select);
  }
  return machineId;
}

function trimmedTextInput(field, label, minimum, maximum) {
  const value = String(field.value || "").trim();
  if (value.length < minimum || value.length > maximum) {
    throw validationError(`${label}需为 ${minimum}-${maximum} 字`, field);
  }
  return value;
}

function positiveSafeIntegerField(field, label, maximum = null) {
  let value;
  try {
    value = positiveSafeIntegerInput(field.value, label);
  } catch (error) {
    throw validationError(error.message, field);
  }
  if (maximum !== null && value > maximum) {
    throw validationError(`${label}必须在 1-${maximum} 之间`, field);
  }
  return value;
}

function microPercentField(field) {
  if (!String(field.value || "").trim()) {
    throw validationError("请填写日利率；无利息请填 0", field);
  }
  try {
    return microPercentFromInput(field.value);
  } catch (error) {
    throw validationError(error.message, field);
  }
}

function dueDateField(field) {
  const value = String(field.value || "").trim();
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) {
    throw validationError("请选择有效的到期日", field);
  }
  const parsed = new Date(`${value}T00:00:00Z`);
  if (Number.isNaN(parsed.getTime()) || parsed.toISOString().slice(0, 10) !== value) {
    throw validationError("请选择有效的到期日", field);
  }
  const minimum = field.min || shanghaiDateOffset(1);
  const maximum = field.max || shanghaiDateOffset(30);
  if (value < minimum) {
    throw validationError("到期日至少应为上海时区的次日", field);
  }
  if (value > maximum) {
    throw validationError("到期日不得晚于 30 天后", field);
  }
  return value;
}

function activateModuleTab(selectedTab, moveFocus = false) {
  const tabs = Array.from(document.querySelectorAll('[role="tab"][data-panel]'));
  const panels = Array.from(document.querySelectorAll('[role="tabpanel"]'));
  for (const tab of tabs) {
    const isSelected = tab === selectedTab;
    tab.setAttribute("aria-selected", String(isSelected));
    tab.tabIndex = isSelected ? 0 : -1;
  }
  for (const panel of panels) {
    panel.hidden = panel.id !== selectedTab.dataset.panel;
  }
  if (moveFocus) selectedTab.focus();
  const category = {
    "panel-achievements": "achievement",
    "panel-shop": "exchange",
    "panel-loans": "loan",
  }[selectedTab.dataset.panel];
  if (category && typeof ackUnreadCategory === "function") {
    void ackUnreadCategory(category);
  }
}

function handleModuleTabKeydown(event) {
  const tabs = Array.from(document.querySelectorAll('[role="tab"][data-panel]'));
  const currentIndex = tabs.indexOf(event.currentTarget);
  let nextIndex = currentIndex;
  if (event.key === "ArrowRight") nextIndex = (currentIndex + 1) % tabs.length;
  if (event.key === "ArrowLeft") nextIndex = (currentIndex - 1 + tabs.length) % tabs.length;
  if (event.key === "Home") nextIndex = 0;
  if (event.key === "End") nextIndex = tabs.length - 1;
  if (nextIndex === currentIndex) return;
  event.preventDefault();
  activateModuleTab(tabs[nextIndex], true);
}

function initModuleTabs() {
  const tabs = Array.from(document.querySelectorAll('[role="tab"][data-panel]'));
  for (const tab of tabs) {
    tab.addEventListener("click", () => activateModuleTab(tab));
    tab.addEventListener("keydown", handleModuleTabKeydown);
  }
  const initialTab = tabs.find((tab) => tab.getAttribute("aria-selected") === "true") || tabs[0];
  if (initialTab) activateModuleTab(initialTab);
}

function activateExchangeView(selectedButton) {
  const buttons = Array.from(document.querySelectorAll("[data-exchange-view]"));
  for (const button of buttons) {
    button.setAttribute("aria-selected", String(button === selectedButton));
    const view = $(button.dataset.exchangeView);
    if (view) view.hidden = button !== selectedButton;
  }
}

function initExchangeTabs() {
  const buttons = Array.from(document.querySelectorAll("[data-exchange-view]"));
  for (const button of buttons) {
    button.addEventListener("click", () => activateExchangeView(button));
  }
}

function showLoanView(view, readOnly = currentSubject.type === "ai") {
  const selected = view === "list" ? "list" : "create";
  const createButton = $("loanViewCreateButton");
  const listButton = $("loanViewListButton");
  createButton.setAttribute("aria-pressed", String(selected === "create"));
  listButton.setAttribute("aria-pressed", String(selected === "list"));
  createButton.disabled = readOnly;
  createButton.title = readOnly ? "切回“我”后可发起借款" : "";
  $("loanViewCreate").hidden = selected !== "create";
  $("loanViewList").hidden = selected !== "list";
}

function syncLoanView(loans, readOnly = currentSubject.type === "ai") {
  if (currentLoanView === null) {
    currentLoanView = loans.length ? "list" : "create";
  }
  showLoanView(readOnly ? "list" : currentLoanView, readOnly);
}

function initLoanViews() {
  $("loanViewCreateButton").addEventListener("click", () => {
    currentLoanView = "create";
    showLoanView(currentLoanView, false);
  });
  $("loanViewListButton").addEventListener("click", () => {
    currentLoanView = "list";
    showLoanView(currentLoanView, currentSubject.type === "ai");
  });
}

function bankruptcyText(wallet) {
  return wallet.bankruptcy_active
    ? wallet.bankruptcy_badge.name
    : "正常营业";
}

function renderSubject(subject, wallet, ledger, achievements = null, loans = null, exchange = null) {
  const readOnly = subject.type === "ai";
  currentSubject = subject;
  $("balanceTitle").textContent = readOnly ? `${subject.name} 的筹码` : "我的筹码";
  $("myBalance").textContent = String(wallet.balance);
  $("checkInState").textContent = wallet.checked_in_today ? "今日已签到" : "今日未签到";
  $("myBankruptcyState").textContent = bankruptcyText(wallet);
  $("myBankruptcyState").classList.toggle("active", wallet.bankruptcy_active);
  $("myBankruptcyCount").textContent = `破产 ${wallet.bankruptcy_count} 次`;
  $("readOnlyTag").classList.toggle("hidden", !readOnly);
  $("checkInButton").classList.toggle("hidden", readOnly);
  $("checkInButton").disabled = readOnly || wallet.checked_in_today;
  $("checkInButton").textContent = wallet.checked_in_today ? "今日已签到" : "立即签到";
  $("bankruptcyButton").classList.toggle("hidden", readOnly);
  $("loanCreateForm").classList.toggle("hidden", readOnly);
  $("bankruptcyButton").disabled = readOnly || !wallet.can_declare_bankruptcy;
  $("checkInDescription").textContent = readOnly
    ? `${subject.name} 的今日签到状态；只能由小机自己操作`
    : "固定奖励，不连签、不补签";
  $("bankruptcyDescription").textContent = readOnly
    ? `${subject.name} 的破产信息只读；人类不能代为宣布`
    : "余额 ≤ -500 时可自愿宣布，重置为 50 枚。";
  $("achievementDescription").textContent = "永久成就 · 解锁即到账";
  $("exchangeTitle").textContent = readOnly ? `与 ${subject.name} 的互动商店` : "互动商店";
  $("exchangeDescription").textContent = "先完成约定，再由对方确认并支付筹码";
  $("socialTitle").textContent = readOnly ? `与 ${subject.name} 的欠条` : "欠条";
  $("socialDescription").textContent = readOnly
    ? `你与 ${subject.name} 的欠条；人类只能执行自己角色允许的操作`
    : "借款提案、协商与还款";
  $("ledgerDescription").textContent = readOnly
    ? `${subject.name} 的统一账本 · 最近流水`
    : "我的统一账本 · 最近流水";
  renderLedger(ledger);
  renderAchievements(achievements);
  renderLoans(loans || []);
  renderExchange(exchange);
}

function renderAchievements(payload) {
  const container = $("achievementSections");
  const summaryElement = $("achievementSummary");
  if (!container || !summaryElement) return;
  container.replaceChildren();
  if (!payload) {
    summaryElement.textContent = "读取中";
    const loading = document.createElement("p");
    loading.className = "achievement-empty";
    loading.textContent = "正在读取成就资料…";
    container.append(loading);
    return;
  }
  const summary = payload.summary || {unlocked: 0, total: 0, hidden_unlocked: 0};
  summaryElement.textContent = `${summary.unlocked} / ${summary.total}`;
  for (const section of payload.sections || []) {
    const group = document.createElement("section");
    group.className = `achievement-group achievement-group-${section.id}`;
    const heading = document.createElement("h3");
    heading.textContent = section.name;
    const grid = document.createElement("div");
    grid.className = "achievement-grid";
    for (const achievement of section.items || []) {
      const card = document.createElement("article");
      card.className = `achievement-item ${achievement.unlocked ? "unlocked" : "locked"}`;
      const titleRow = document.createElement("div");
      titleRow.className = "achievement-title-row";
      const icon = document.createElement("span");
      icon.className = "achievement-state-icon";
      icon.setAttribute("aria-hidden", "true");
      icon.textContent = achievement.unlocked ? "★" : "◇";
      const name = document.createElement("h4");
      name.textContent = achievement.name;
      titleRow.append(icon, name);
      if (achievement.reward > 0) {
        const reward = document.createElement("span");
        reward.className = "achievement-reward";
        reward.textContent = `+${achievement.reward}`;
        titleRow.append(reward);
      }
      const condition = document.createElement("p");
      condition.className = "achievement-condition";
      condition.textContent = achievement.condition;
      const progress = achievement.progress || {current: 0, target: 1};
      const progressRow = document.createElement("div");
      progressRow.className = "achievement-progress-row";
      const track = document.createElement("span");
      track.className = "achievement-progress-track";
      const fill = document.createElement("span");
      fill.className = "achievement-progress-fill";
      fill.style.width = `${Math.min(100, Math.round(100 * progress.current / Math.max(1, progress.target)))}%`;
      track.append(fill);
      const count = document.createElement("span");
      count.className = "achievement-progress-count";
      count.textContent = `${progress.current} / ${progress.target}`;
      progressRow.append(track, count);
      card.append(titleRow, condition, progressRow);
      if (achievement.unlocked && achievement.unlocked_at) {
        const unlockedAt = document.createElement("time");
        unlockedAt.className = "achievement-unlocked-at";
        unlockedAt.dateTime = achievement.unlocked_at;
        unlockedAt.textContent = `解锁于 ${formatLedgerCreatedAt(achievement.unlocked_at)}`;
        card.append(unlockedAt);
      }
      grid.append(card);
    }
    group.append(heading, grid);
    container.append(group);
  }
  if (!container.children.length) {
    const empty = document.createElement("p");
    empty.className = "achievement-empty";
    empty.textContent = "当前对象暂无适用成就。";
    container.append(empty);
  }
}

function renderMachines(machines) {
  const select = $("subjectSelect");
  select.replaceChildren();
  const humanOption = document.createElement("option");
  humanOption.value = "human";
  humanOption.textContent = "我";
  select.append(humanOption);
  for (const machine of machines) {
    const option = document.createElement("option");
    option.value = `ai:${machine.id}`;
    option.textContent = machine.name;
    select.append(option);
  }
  select.value = "human";
  select.disabled = false;
  const loanSelect = $("loanMachineSelect");
  if (loanSelect) {
    loanSelect.replaceChildren();
    for (const machine of machines) {
      const option = document.createElement("option");
      option.value = machine.id;
      option.textContent = machine.name;
      loanSelect.append(option);
    }
    loanSelect.disabled = machines.length === 0;
  }
  const exchangeSelect = $("exchangeMachineSelect");
  if (exchangeSelect) {
    exchangeSelect.replaceChildren();
    for (const machine of machines) {
      const option = document.createElement("option");
      option.value = machine.id;
      option.textContent = machine.name;
      exchangeSelect.append(option);
    }
    exchangeSelect.disabled = machines.length === 0;
  }
}

function formatLedgerCreatedAt(createdAt) {
  const parsed = new Date(createdAt);
  if (Number.isNaN(parsed.getTime())) return String(createdAt || "—");
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }).format(parsed);
}

function renderLedger(entries) {
  const list = $("ledgerList");
  list.replaceChildren();
  if (!entries.length) {
    const empty = document.createElement("li");
    empty.className = "ledger-empty";
    empty.textContent = "还没有筹码流水。";
    list.append(empty);
    return;
  }
  for (const entry of entries) {
    const item = document.createElement("li");
    const label = document.createElement("span");
    label.className = "ledger-label";
    label.textContent = entry.label;
    const amount = document.createElement("span");
    amount.className = `ledger-amount ${entry.amount >= 0 ? "positive" : "negative"}`;
    amount.textContent = `${entry.amount >= 0 ? "+" : ""}${entry.amount}`;
    const meta = document.createElement("span");
    meta.className = "ledger-meta";
    meta.textContent = `${formatLedgerCreatedAt(entry.created_at)} · 余额 ${entry.balance_after}`;
    item.append(label, amount, meta);
    list.append(item);
  }
}

const loanStatusLabels = {
  negotiating: "协商中", active: "已生效", overdue: "已逾期", repaid: "已还清",
  rejected: "已拒绝", withdrawn: "已撤销", expired: "提案过期",
};

function machineName(machineId) {
  return summary?.machines?.find((item) => item.id === machineId)?.name || machineId;
}

function appendLoanTerm(list, label, value, className = "") {
  const wrapper = document.createElement("div");
  const term = document.createElement("dt");
  const detail = document.createElement("dd");
  term.textContent = label;
  detail.textContent = String(value);
  if (className) detail.className = className;
  wrapper.append(term, detail);
  list.append(wrapper);
}

function nonNegativeLoanAmount(value) {
  const amount = Number(value);
  return Number.isFinite(amount) ? Math.max(0, Math.trunc(amount)) : 0;
}

function currentLoanDue(loan) {
  return nonNegativeLoanAmount(loan.remaining_principal)
    + nonNegativeLoanAmount(loan.accrued_interest);
}

function loanCounterpartyName(loan) {
  return summary?.machines?.find((item) => item.id === loan.ai_id)?.name || "对方小机";
}

function loanNaturalTitle(loan) {
  const name = loanCounterpartyName(loan);
  const principal = nonNegativeLoanAmount(loan.principal);
  return loan.direction === "lending"
    ? `${name} 向你借 ${principal}`
    : `向 ${name} 借 ${principal}`;
}

function loanCapDescription(loan) {
  if (!loan.interest_cap_enabled) return "未封顶";
  const prefix = loan.interest_cap_reached ? "已触达上限" : "已开启";
  return `${prefix} · 上限 ${nonNegativeLoanAmount(loan.interest_cap_amount)}`;
}

function appendLoanHeader(card, loan) {
  const title = document.createElement("div");
  title.className = "loan-title-row";
  const heading = document.createElement("h4");
  heading.textContent = loanNaturalTitle(loan);
  const status = document.createElement("span");
  status.className = "loan-status";
  status.textContent = loanStatusLabels[loan.status] || loan.status;
  title.append(heading, status);
  card.append(title);
}

function appendLoanCounterMeta(card, loan) {
  const count = nonNegativeLoanAmount(loan.counter_count);
  if (!count) return;
  const meta = document.createElement("p");
  meta.className = "loan-counter-meta";
  const revision = nonNegativeLoanAmount(loan.accepted_revision || loan.revision) || count + 1;
  meta.textContent = `第 ${revision} 版方案 · 已改条件 ${count} 次`;
  card.append(meta);
}

function renderNegotiatingLoan(loan, card) {
  const waiting = document.createElement("p");
  waiting.className = `loan-waiting${loan.awaiting?.you ? " waiting-you" : ""}`;
  waiting.textContent = loan.awaiting?.you
    ? "等待你回应"
    : `等待 ${loan.awaiting?.type === "ai" ? loanCounterpartyName(loan) : "对方"} 回应`;
  card.append(waiting);
  appendLoanCounterMeta(card, loan);

  const terms = document.createElement("dl");
  terms.className = "loan-proposal-details";
  appendLoanTerm(terms, "申请本金", nonNegativeLoanAmount(loan.principal));
  appendLoanTerm(terms, "日利率", `${loan.daily_rate_percent}%`);
  appendLoanTerm(terms, "到期日", loan.due_date);
  appendLoanTerm(terms, "利息上限", loan.interest_cap_enabled
    ? nonNegativeLoanAmount(loan.interest_cap_amount)
    : "不封顶", loan.interest_cap_enabled ? "" : "loan-cap-off");
  appendLoanTerm(
    terms,
    "提案失效",
    loan.proposal_expires_at ? formatLedgerCreatedAt(loan.proposal_expires_at) : "—",
  );
  card.append(terms);
}

function renderCurrentLoan(loan, card) {
  const due = document.createElement("div");
  due.className = "loan-current-due";
  const dueLabel = document.createElement("span");
  dueLabel.textContent = "当前应还";
  const dueAmount = document.createElement("strong");
  dueAmount.textContent = String(currentLoanDue(loan));
  const dueUnit = document.createElement("small");
  dueUnit.textContent = "枚";
  due.append(dueLabel, dueAmount, dueUnit);
  card.append(due);

  const split = document.createElement("div");
  split.className = "loan-debt-split";
  for (const [labelText, amount] of [
    ["剩余本金", loan.remaining_principal],
    ["当前利息", loan.accrued_interest],
  ]) {
    const item = document.createElement("div");
    const label = document.createElement("span");
    const value = document.createElement("strong");
    label.textContent = labelText;
    value.textContent = String(nonNegativeLoanAmount(amount));
    item.append(label, value);
    split.append(item);
  }
  card.append(split);

  if (loan.status === "overdue") {
    const overdue = document.createElement("p");
    overdue.className = "loan-overdue-callout";
    overdue.textContent = `已逾期 ${nonNegativeLoanAmount(loan.overdue_days)} 天`;
    card.append(overdue);
  }

  const terms = document.createElement("dl");
  terms.className = "loan-compact-terms";
  appendLoanTerm(terms, "日利率", `${loan.daily_rate_percent}%`);
  appendLoanTerm(
    terms,
    "到期情况",
    loan.status === "overdue"
      ? `${loan.due_date} · 逾期 ${nonNegativeLoanAmount(loan.overdue_days)} 天`
      : loan.due_date,
  );
  appendLoanTerm(
    terms,
    "利息封顶",
    loanCapDescription(loan),
    loan.interest_cap_enabled ? "" : "loan-cap-off",
  );
  const totalRepaid = nonNegativeLoanAmount(loan.total_repaid);
  if (totalRepaid > 0) appendLoanTerm(terms, "已还总额", totalRepaid);
  card.append(terms);
  appendLoanCounterMeta(card, loan);
}

function renderRepaidLoan(loan, card) {
  const summaryBox = document.createElement("div");
  summaryBox.className = "loan-repaid-summary";
  const state = document.createElement("strong");
  state.textContent = "已还清";
  const amount = document.createElement("p");
  amount.textContent = `实际已还总额 ${nonNegativeLoanAmount(loan.total_repaid)} 枚`;
  summaryBox.append(state, amount);
  card.append(summaryBox);

  const terms = document.createElement("p");
  terms.className = "loan-repaid-terms";
  terms.textContent = `本金 ${nonNegativeLoanAmount(loan.principal)} · 日利率 ${loan.daily_rate_percent}% · 到期日 ${loan.due_date}`;
  card.append(terms);
  if (loan.repaid_at) {
    const date = document.createElement("p");
    date.className = "loan-key-date";
    date.textContent = `还清于 ${formatLedgerCreatedAt(loan.repaid_at)}`;
    card.append(date);
  }
  appendLoanCounterMeta(card, loan);
}

function renderHistoricalLoan(loan, card) {
  const labels = {rejected: "拒绝", withdrawn: "撤销", expired: "失效"};
  const meta = document.createElement("p");
  meta.className = "loan-history-meta";
  const created = loan.created_at ? `申请于 ${formatLedgerCreatedAt(loan.created_at)}` : "";
  const closed = loan.updated_at
    ? `${labels[loan.status] || "结束"}于 ${formatLedgerCreatedAt(loan.updated_at)}`
    : "";
  meta.textContent = [created, closed].filter(Boolean).join(" · ");
  card.append(meta);
}

function microPercentFromInput(value) {
  const match = String(value).trim().match(/^(\d+)(?:\.(\d{1,6}))?$/);
  if (!match) throw new Error("日利率最多保留 6 位小数，且不能为负");
  const units = BigInt(match[1]) * 1000000n
    + BigInt((match[2] || "").padEnd(6, "0") || "0");
  if (units > BigInt(Number.MAX_SAFE_INTEGER)) {
    throw new Error("日利率超过浏览器可安全提交的技术上限");
  }
  return Number(units);
}

function positiveSafeIntegerInput(value, label) {
  const normalized = String(value).trim();
  if (!/^\d+$/.test(normalized)) throw new Error(`${label}必须是正整数`);
  const parsed = Number(normalized);
  if (parsed <= 0) throw new Error(`${label}必须是正整数`);
  if (!Number.isSafeInteger(parsed)) {
    throw new Error(`${label}超过浏览器可安全提交的范围`);
  }
  return parsed;
}

function newIdempotencyKey(prefix) {
  const suffix = globalThis.crypto?.randomUUID?.()
    || `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  return `${prefix}:${suffix}`;
}

function shanghaiDateOffset(days) {
  const parts = Object.fromEntries(
    new Intl.DateTimeFormat("en-CA", {
      timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit",
    }).formatToParts(new Date()).filter((part) => part.type !== "literal")
      .map((part) => [part.type, part.value])
  );
  return new Date(Date.UTC(
    Number(parts.year), Number(parts.month) - 1, Number(parts.day) + days,
  )).toISOString().slice(0, 10);
}

function loanBaseBody(loan) {
  return {
    revision: loan.revision,
    idempotency_key: newIdempotencyKey(`web:${loan.loan_id}`),
  };
}

async function runLoanAction(loan, action, body) {
  try {
    const payload = await requestJson(
      `/api/chips/loans/${encodeURIComponent(loan.loan_id)}/${action}`,
      {method: "POST", body: JSON.stringify(body)},
    );
    if (summary) {
      summary.wallet = payload.wallet;
      summary.ledger = payload.ledger;
      summary.loans = payload.loans;
      summary.achievements = payload.achievements;
    }
    if (currentSubject.type === "human") {
      renderSubject(
        currentSubject, payload.wallet, payload.ledger,
        payload.achievements, payload.loans, currentExchange,
      );
    } else {
      await selectSubject(`ai:${currentSubject.id}`);
    }
    showNotice(payload.message);
  } catch (error) {
    showNotice(error.message, true);
  }
}

function renderLoanCounterForm(loan, actionBox) {
  const form = document.createElement("form");
  form.className = "loan-counter-form";
  const grid = document.createElement("div");
  grid.className = "loan-counter-grid";
  const specs = [
    ["本金", "number", String(loan.principal)],
    ["日利率（%）", "text", loan.daily_rate_percent],
    ["到期日", "date", loan.due_date],
  ];
  const inputs = specs.map(([text, type, value]) => {
    const label = document.createElement("label");
    const input = document.createElement("input");
    label.textContent = text;
    input.type = type;
    input.value = value;
    input.required = true;
    if (type === "number") { input.min = "1"; input.step = "1"; }
    if (type === "date") {
      input.min = shanghaiDateOffset(1);
      input.max = shanghaiDateOffset(30);
    }
    label.append(input);
    grid.append(label);
    return input;
  });
  const cap = document.createElement("label");
  cap.className = "cap-choice";
  const capInput = document.createElement("input");
  capInput.type = "checkbox";
  capInput.checked = loan.interest_cap_enabled;
  const capText = document.createElement("span");
  capText.textContent = "利息封顶保护";
  cap.append(capInput, capText);
  const submit = document.createElement("button");
  submit.className = "button primary";
  submit.type = "submit";
  submit.textContent = "提交改条件";
  form.append(grid, cap, submit);
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    submit.disabled = true;
    try {
      await runLoanAction(loan, "counter", {
        revision: loan.revision,
        principal: positiveSafeIntegerInput(inputs[0].value, "本金"),
        daily_rate_micro_percent: microPercentFromInput(inputs[1].value),
        due_date: inputs[2].value,
        interest_cap_enabled: capInput.checked,
        idempotency_key: newIdempotencyKey(`web:${loan.loan_id}:counter`),
      });
    } catch (error) {
      showNotice(error.message, true);
      submit.disabled = false;
    }
  });
  actionBox.replaceChildren(form);
}

function renderLoanActions(loan, card) {
  if (!loan.allowed_actions?.length) return;
  const box = document.createElement("div");
  box.className = "loan-actions";
  const labels = {accept: "接受", reject: "拒绝", counter: "改条件", withdraw: "撤销", repay: "还款"};
  for (const action of loan.allowed_actions) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `button ${action === "accept" || action === "repay" ? "primary" : "ghost"}`;
    button.textContent = labels[action];
    button.addEventListener("click", async () => {
      if (action === "counter") return renderLoanCounterForm(loan, box);
      if (action === "repay") {
        const form = document.createElement("form");
        form.className = "loan-repay-form";
        const label = document.createElement("label");
        const totalDue = currentLoanDue(loan);
        label.textContent = `还款额（最多 ${totalDue}）`;
        const input = document.createElement("input");
        input.type = "number"; input.min = "1"; input.max = String(totalDue);
        input.step = "1"; input.required = true;
        const submit = document.createElement("button");
        submit.type = "submit"; submit.className = "button primary";
        submit.textContent = "确认还款";
        label.append(input); form.append(label, submit);
        form.addEventListener("submit", async (event) => {
          event.preventDefault();
          submit.disabled = true;
          await runLoanAction(loan, "repay", {
            amount: positiveSafeIntegerInput(input.value, "还款额"),
            idempotency_key: newIdempotencyKey(`web:${loan.loan_id}:repay`),
          });
        });
        box.replaceChildren(form);
        return;
      }
      button.disabled = true;
      await runLoanAction(loan, action, loanBaseBody(loan));
    });
    box.append(button);
  }
  card.append(box);
}

function renderLoans(loans) {
  const container = $("loanList");
  const count = $("loanCount");
  if (!container || !count) return;
  container.replaceChildren();
  count.textContent = String(loans.length);
  syncLoanView(loans);
  if (!loans.length) {
    const empty = document.createElement("p");
    empty.className = "loan-empty";
    empty.textContent = "还没有欠条或借款提案。";
    container.append(empty);
    return;
  }
  for (const loan of loans) {
    const card = document.createElement("article");
    card.className = `loan-item ${loan.status}`;
    card.dataset.loanStatus = loan.status;
    appendLoanHeader(card, loan);
    if (loan.status === "negotiating") renderNegotiatingLoan(loan, card);
    else if (loan.status === "active" || loan.status === "overdue") {
      renderCurrentLoan(loan, card);
    } else if (loan.status === "repaid") renderRepaidLoan(loan, card);
    else renderHistoricalLoan(loan, card);

    renderLoanActions(loan, card);
    container.append(card);
  }
}

const exchangeStatusLabels = {
  pending: "待处理", completed: "已完成", rejected: "已拒绝",
  withdrawn: "已撤回", expired: "已失效",
};

function syncExchangeTarget() {
  const select = $("exchangeMachineSelect");
  if (!select || !summary) return;
  if (currentSubject.type === "ai") {
    select.value = currentSubject.id;
    select.disabled = true;
  } else {
    select.disabled = !summary.machines.length;
    if (!select.value && summary.machines.length) select.value = summary.machines[0].id;
  }
  updateExchangeFormSummary();
}

function updateExchangeFormSummary() {
  const itemKey = String($("exchangeItemKey")?.value || "");
  const item = currentExchange?.catalog?.find((entry) => entry.key === itemKey);
  if (!item) return;
  const customTitle = item.key === "custom"
    ? String($("exchangeCustomTitle")?.value || "").trim()
    : "";
  const displayTitle = customTitle || item.title;
  const machineId = String($("exchangeMachineSelect")?.value || "");
  const targetName = machineId ? machineName(machineId) : "绑定小机";
  const amount = Number($("exchangeAmount")?.value);
  const hasAmount = Number.isInteger(amount) && amount >= 1 && amount <= 100;
  $("exchangeFormTitle").textContent = hasAmount
    ? `你用「${displayTitle}」向 ${targetName} 换 ${amount} 筹码`
    : `你用「${displayTitle}」向 ${targetName} 换筹码`;
  $("exchangeFormDescription").textContent =
    `请先在常用聊天中完成约定，再由 ${targetName} 确认并支付筹码；确认后筹码由你收取。`;
}

function openExchangeForm(item) {
  if (!summary?.machines?.length) {
    showNotice("还没有可选择的绑定小机", true);
    return;
  }
  $("exchangeItemKey").value = item.key;
  $("exchangeFormSymbol").textContent = item.symbol || "♡";
  const custom = item.key === "custom";
  $("exchangeCustomTitleWrap").classList.toggle("hidden", !custom);
  $("exchangeCustomTitle").required = custom;
  if (!custom) $("exchangeCustomTitle").value = "";
  $("exchangeCreateForm").classList.remove("hidden");
  syncExchangeTarget();
  $(custom ? "exchangeCustomTitle" : "exchangeRequestNote").focus();
}

function renderExchangeArt(art, item) {
  const fallback = document.createElement("span");
  fallback.className = "exchange-art-fallback";
  fallback.textContent = item.symbol || "♡";
  art.append(fallback);
  if (!item.image_key) return;

  const image = document.createElement("img");
  image.alt = "";
  image.loading = "lazy";
  image.decoding = "async";
  image.addEventListener("load", () => {
    art.classList.add("has-image");
  });
  image.addEventListener("error", () => {
    image.remove();
    art.classList.remove("has-image");
  });
  art.append(image);
  image.src = apiPath(item.image_key);
}

function renderExchangeCatalog(items) {
  const container = $("exchangeCatalog");
  container.replaceChildren();
  if (!items?.length) {
    const empty = document.createElement("p");
    empty.className = "exchange-empty";
    empty.textContent = "当前没有可发起的商品。";
    container.append(empty);
    return;
  }
  for (const item of items) {
    const card = document.createElement("button");
    card.type = "button";
    card.className = "exchange-product";
    card.dataset.itemKey = item.key;
    const art = document.createElement("span");
    art.className = "exchange-art";
    art.setAttribute("aria-hidden", "true");
    renderExchangeArt(art, item);
    const copy = document.createElement("span");
    copy.className = "exchange-copy";
    const title = document.createElement("strong");
    title.textContent = item.title;
    const description = document.createElement("span");
    description.textContent = item.description;
    copy.append(title, description);
    card.append(art, copy);
    card.addEventListener("click", () => openExchangeForm(item));
    container.append(card);
  }
}

function renderExchangeRequest(item) {
  const card = document.createElement("article");
  card.className = `exchange-request ${item.status}`;
  const art = document.createElement("div");
  art.className = "exchange-art exchange-request-art";
  art.setAttribute("aria-hidden", "true");
  renderExchangeArt(art, item.item);
  const content = document.createElement("div");
  content.className = "exchange-request-content";
  const titleRow = document.createElement("div");
  titleRow.className = "exchange-request-title";
  const sentence = document.createElement("p");
  sentence.className = "exchange-request-summary";
  const machine = document.createElement("strong");
  machine.className = "exchange-request-machine";
  machine.textContent = item.machine_name || machineName(item.ai_id);
  const amount = document.createElement("strong");
  amount.className = "exchange-request-amount";
  amount.textContent = `${item.chip_amount} 筹码`;
  if (item.initiator.type === "human") {
    sentence.append(
      document.createTextNode(`你用「${item.display_title}」向 `),
      machine,
      document.createTextNode(" 换 "),
      amount,
    );
  } else {
    sentence.append(
      machine,
      document.createTextNode(` 用「${item.display_title}」向你换 `),
      amount,
    );
  }
  const status = document.createElement("span");
  status.className = "exchange-status";
  status.textContent = exchangeStatusLabels[item.status] || item.status;
  titleRow.append(sentence, status);
  const agreement = document.createElement("div");
  agreement.className = "exchange-request-detail";
  const agreementLabel = document.createElement("span");
  agreementLabel.className = "exchange-request-label";
  agreementLabel.textContent = "约定";
  const agreementText = document.createElement("p");
  agreementText.textContent = item.item.description;
  agreement.append(agreementLabel, agreementText);
  const note = document.createElement("div");
  note.className = "exchange-request-detail";
  const noteLabel = document.createElement("span");
  noteLabel.className = "exchange-request-label";
  noteLabel.textContent = "本次说明";
  const noteText = document.createElement("p");
  noteText.textContent = item.request_note;
  note.append(noteLabel, noteText);
  const time = document.createElement("p");
  time.className = "exchange-request-time";
  time.textContent = item.status === "pending"
    ? `有效期至 ${formatLedgerCreatedAt(item.expires_at)}`
    : `创建于 ${formatLedgerCreatedAt(item.created_at)}`;
  content.append(titleRow, agreement, note, time);
  if (item.allowed_actions?.length) {
    const actions = document.createElement("div");
    actions.className = "exchange-actions";
    const labels = {confirm: "确认并支付筹码", reject: "拒绝", withdraw: "撤回"};
    for (const action of item.allowed_actions) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = `button ${action === "confirm" ? "primary" : "ghost"}`;
      button.textContent = labels[action];
      button.addEventListener("click", async () => {
        button.disabled = true;
        await runExchangeAction(item, action);
      });
      actions.append(button);
    }
    content.append(actions);
  }
  card.append(art, content);
  return card;
}

function renderExchangeList(containerId, items, emptyText) {
  const container = $(containerId);
  container.replaceChildren();
  if (!items.length) {
    const empty = document.createElement("p");
    empty.className = "exchange-empty";
    empty.textContent = emptyText;
    container.append(empty);
    return;
  }
  for (const item of items) container.append(renderExchangeRequest(item));
}

function renderExchange(exchange) {
  currentExchange = exchange;
  const pending = exchange?.pending_for_me || [];
  const waiting = exchange?.waiting_for_other || [];
  const history = exchange?.history || [];
  renderExchangeCatalog(exchange?.catalog || []);
  $("exchangePendingCount").textContent = `${pending.length} 张`;
  $("exchangeWaitingCount").textContent = `${waiting.length} 张`;
  $("exchangeHistoryCount").textContent = `${history.length} 张`;
  const badge = $("exchangePendingBadge");
  badge.textContent = String(pending.length);
  badge.classList.toggle("hidden", pending.length === 0);
  renderExchangeList("exchangePendingList", pending, "没有需要你确认付款的申请。");
  renderExchangeList("exchangeWaitingList", waiting, "没有等待小机确认付款的申请。");
  renderExchangeList("exchangeHistoryList", history, "还没有兑换记录。");
  syncExchangeTarget();
}

async function runExchangeAction(item, action) {
  try {
    const payload = await requestJson(
      `/api/chips/exchanges/${encodeURIComponent(item.request_id)}/${action}`,
      {
        method: "POST",
        body: JSON.stringify({
          idempotency_key: newIdempotencyKey(`web:exchange:${item.request_id}:${action}`),
        }),
      },
    );
    summary.wallet = payload.wallet;
    summary.ledger = payload.ledger;
    summary.exchange = payload.exchange;
    if (currentSubject.type === "human") {
      renderSubject(
        currentSubject, summary.wallet, summary.ledger,
        summary.achievements, summary.loans, summary.exchange,
      );
    } else {
      await selectSubject(`ai:${currentSubject.id}`);
    }
    showNotice(payload.message);
  } catch (error) {
    showNotice(error.message, true);
    if (currentExchange) renderExchange(currentExchange);
  }
}

async function loadSummary() {
  try {
    summary = await requestJson("/api/chips");
    renderUnreadBadges();
    renderMachines(summary.machines);
    renderSubject(
      {type: "human", id: null, name: summary.human_name || "我"},
      summary.wallet,
      summary.ledger,
      summary.achievements,
      summary.loans,
      summary.exchange,
    );
  } catch (error) {
    showNotice(error.message, true);
    $("subjectSelect").disabled = true;
  }
}

async function runHumanAction(url) {
  if (!summary || currentSubject.type !== "human") return;
  $("checkInButton").disabled = true;
  $("bankruptcyButton").disabled = true;
  try {
    const payload = await requestJson(url, {method: "POST", body: "{}"});
    summary.wallet = payload.wallet;
    summary.ledger = payload.ledger;
    summary.loans = payload.loans || summary.loans;
    summary.achievements = payload.achievements || summary.achievements;
    if (currentSubject.type === "human") {
      renderSubject(
        currentSubject, payload.wallet, payload.ledger,
        summary.achievements, summary.loans, summary.exchange,
      );
    }
    showNotice(payload.message);
  } catch (error) {
    showNotice(error.message, true);
    if (summary && currentSubject.type === "human") {
      renderSubject(
        currentSubject, summary.wallet, summary.ledger,
        summary.achievements, summary.loans, summary.exchange,
      );
    }
  }
}

async function selectSubject(value) {
  const requestSequence = ++subjectRequestSequence;
  if (value === "human") {
    renderSubject(
      {type: "human", id: null, name: summary.human_name || "我"},
      summary.wallet,
      summary.ledger,
      summary.achievements,
      summary.loans,
      summary.exchange,
    );
    return;
  }
  const machineId = value.slice(3);
  const machine = summary.machines.find((item) => item.id === machineId);
  if (!machine) {
    showNotice("这只小机不在当前账号的绑定清单中", true);
    $("subjectSelect").value = "human";
    await selectSubject("human");
    return;
  }
  const subject = {type: "ai", id: machineId, name: machine.name};
  renderSubject(subject, {
    balance: "--",
    checked_in_today: false,
    bankruptcy_active: false,
    bankruptcy_badge: null,
    bankruptcy_count: "--",
    can_declare_bankruptcy: false,
  }, [], null, [], null);
  $("checkInState").textContent = "读取中";
  $("myBankruptcyState").textContent = "读取中";
  try {
    const payload = await requestJson(`/api/chips/machines/${encodeURIComponent(machineId)}`);
    if (requestSequence !== subjectRequestSequence) return;
    renderSubject(
      subject, payload.wallet, payload.ledger,
      payload.achievements, payload.loans, payload.exchange,
    );
  } catch (error) {
    if (requestSequence !== subjectRequestSequence) return;
    showNotice(error.message, true);
    $("subjectSelect").value = "human";
    await selectSubject("human");
  }
}

$("checkInButton").addEventListener("click", () => runHumanAction("/api/chips/check-in"));
$("bankruptcyButton").addEventListener("click", () => runHumanAction("/api/chips/bankruptcy"));
$("subjectSelect").addEventListener("change", (event) => selectSubject(event.target.value));

function validateExchangeCreateForm() {
  const itemKeyField = $("exchangeItemKey");
  const itemKey = String(itemKeyField.value || "").trim();
  const item = currentExchange?.catalog?.find((entry) => entry.key === itemKey);
  if (!item) throw validationError("请先从商店选择一个你愿意完成的小约定", itemKeyField);
  const machineId = selectedBoundMachine($("exchangeMachineSelect"), "兑换目标小机");
  const requestNote = trimmedTextInput($("exchangeRequestNote"), "完成方式 / 补充说明", 1, 120);
  const chipAmount = positiveSafeIntegerField($("exchangeAmount"), "筹码数", 100);
  const customTitle = itemKey === "custom"
    ? trimmedTextInput($("exchangeCustomTitle"), "自定义小约定标题", 1, 30)
    : null;
  return {
    machine_id: machineId,
    item_key: itemKey,
    request_note: requestNote,
    chip_amount: chipAmount,
    custom_title: customTitle,
  };
}

function validateLoanCreateForm() {
  const capField = $("loanCapEnabled");
  if (!capField || capField.type !== "checkbox") {
    throw validationError("无法读取利息封顶保护选项，请刷新页面后重试");
  }
  return {
    machine_id: selectedBoundMachine($("loanMachineSelect"), "借款目标小机"),
    principal: positiveSafeIntegerField($("loanPrincipal"), "本金"),
    daily_rate_micro_percent: microPercentField($("loanRate")),
    due_date: dueDateField($("loanDueDate")),
    interest_cap_enabled: capField.checked,
  };
}

async function handleExchangeCreateSubmit(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const button = $("exchangeCreateButton");
  clearFormError("exchangeFormError", form);
  let body;
  try {
    body = validateExchangeCreateForm();
  } catch (error) {
    showFormError("exchangeFormError", error);
    return;
  }
  button.disabled = true;
  try {
    const payload = await requestJson("/api/chips/exchanges", {
      method: "POST",
      body: JSON.stringify({
        ...body,
        idempotency_key: newIdempotencyKey("web:exchange:create"),
      }),
    });
    summary.wallet = payload.wallet;
    summary.ledger = payload.ledger;
    summary.exchange = payload.exchange;
    $("exchangeCreateForm").reset();
    $("exchangeCreateForm").classList.add("hidden");
    if (currentSubject.type === "human") {
      renderSubject(
        currentSubject, summary.wallet, summary.ledger,
        summary.achievements, summary.loans, summary.exchange,
      );
    } else {
      await selectSubject(`ai:${currentSubject.id}`);
    }
    showNotice(payload.message);
  } catch (error) {
    showFormError("exchangeFormError", error);
    showNotice(error.message, true);
  } finally {
    button.disabled = false;
  }
}

async function handleLoanCreateSubmit(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const button = $("loanCreateButton");
  clearFormError("loanFormError", form);
  let body;
  try {
    body = validateLoanCreateForm();
  } catch (error) {
    showFormError("loanFormError", error);
    return;
  }
  button.disabled = true;
  try {
    const payload = await requestJson("/api/chips/loans", {
      method: "POST",
      body: JSON.stringify({
        ...body,
        idempotency_key: newIdempotencyKey("web:create-loan"),
      }),
    });
    summary.wallet = payload.wallet;
    summary.ledger = payload.ledger;
    summary.loans = payload.loans;
    summary.achievements = payload.achievements;
    renderSubject(
      currentSubject, payload.wallet, payload.ledger,
      payload.achievements, payload.loans, summary.exchange,
    );
    $("loanPrincipal").value = "";
    showNotice(payload.message);
  } catch (error) {
    showFormError("loanFormError", error);
    showNotice(error.message, true);
  } finally {
    button.disabled = false;
  }
}

function initCreateForms() {
  const exchangeForm = $("exchangeCreateForm");
  exchangeForm.noValidate = true;
  exchangeForm.addEventListener("submit", handleExchangeCreateSubmit);
  exchangeForm.addEventListener("input", () => {
    clearFormError("exchangeFormError", exchangeForm);
    updateExchangeFormSummary();
  });
  exchangeForm.addEventListener("change", () => {
    clearFormError("exchangeFormError", exchangeForm);
    updateExchangeFormSummary();
  });
  $("exchangeCancelButton").addEventListener("click", () => {
    clearFormError("exchangeFormError", exchangeForm);
    exchangeForm.classList.add("hidden");
  });

  const loanForm = $("loanCreateForm");
  loanForm.noValidate = true;
  loanForm.addEventListener("submit", handleLoanCreateSubmit);
  loanForm.addEventListener("input", () => clearFormError("loanFormError", loanForm));
  loanForm.addEventListener("change", () => clearFormError("loanFormError", loanForm));
  $("loanCapEnabled").addEventListener("change", (event) => {
    $("loanCapWarning").classList.toggle("hidden", event.target.checked);
  });
  const loanDueDate = $("loanDueDate");
  loanDueDate.min = shanghaiDateOffset(1);
  loanDueDate.max = shanghaiDateOffset(30);
  loanDueDate.value = shanghaiDateOffset(7);
}

initModuleTabs();
initExchangeTabs();
initLoanViews();
initCreateForms();
loadSummary();

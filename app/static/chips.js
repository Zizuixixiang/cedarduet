"use strict";

const $ = (id) => document.getElementById(id);
let summary = null;
let currentSubject = {type: "human", id: null, name: "我"};
let subjectRequestSequence = 0;

function apiPath(path) {
  return window.location.pathname.startsWith("/duel") ? `/duel${path}` : path;
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

function showNotice(message, isError = false) {
  const notice = $("notice");
  notice.textContent = message;
  notice.classList.toggle("error", isError);
  notice.classList.remove("hidden");
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

function bankruptcyText(wallet) {
  return wallet.bankruptcy_active
    ? wallet.bankruptcy_badge.name
    : "正常营业";
}

function renderSubject(subject, wallet, ledger, achievements = null) {
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
  $("bankruptcyButton").disabled = readOnly || !wallet.can_declare_bankruptcy;
  $("checkInDescription").textContent = readOnly
    ? `${subject.name} 的今日签到状态；只能由小机自己操作`
    : "固定奖励，不连签、不补签";
  $("bankruptcyDescription").textContent = readOnly
    ? `${subject.name} 的破产信息只读；人类不能代为宣布`
    : "余额 ≤ -500 时可自愿宣布，重置为 50 枚。";
  $("achievementDescription").textContent = readOnly
    ? `${subject.name} 的永久成就；含你们之间的配对进度`
    : "我的永久成就；奖励在解锁时自动到账";
  $("socialTitle").textContent = readOnly ? `与 ${subject.name} 的互动与借款` : "互动与借款";
  $("socialDescription").textContent = readOnly
    ? `你与 ${subject.name} 的互动、借款与欠条规则筹备中`
    : "选择一只绑定小机后查看关系功能；规则筹备中";
  $("ledgerDescription").textContent = readOnly
    ? `${subject.name} 的统一账本 · 最近流水`
    : "我的统一账本 · 最近流水";
  renderLedger(ledger);
  renderAchievements(achievements);
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
      const reward = document.createElement("span");
      reward.className = "achievement-reward";
      reward.textContent = `+${achievement.reward}`;
      titleRow.append(icon, name, reward);
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

async function loadSummary() {
  try {
    summary = await requestJson("/api/chips");
    renderMachines(summary.machines);
    renderSubject(
      {type: "human", id: null, name: summary.human_name || "我"},
      summary.wallet,
      summary.ledger,
      summary.achievements,
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
    summary.achievements = payload.achievements || summary.achievements;
    if (currentSubject.type === "human") {
      renderSubject(currentSubject, payload.wallet, payload.ledger, summary.achievements);
    }
    showNotice(payload.message);
  } catch (error) {
    showNotice(error.message, true);
    if (summary && currentSubject.type === "human") {
      renderSubject(currentSubject, summary.wallet, summary.ledger);
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
  }, [], null);
  $("checkInState").textContent = "读取中";
  $("myBankruptcyState").textContent = "读取中";
  try {
    const payload = await requestJson(`/api/chips/machines/${encodeURIComponent(machineId)}`);
    if (requestSequence !== subjectRequestSequence) return;
    renderSubject(subject, payload.wallet, payload.ledger, payload.achievements);
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

initModuleTabs();
loadSummary();

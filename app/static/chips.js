"use strict";

const $ = (id) => document.getElementById(id);
let summary = null;

async function requestJson(url, options = {}) {
  const response = await fetch(url, {
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

function bankruptcyText(wallet) {
  return wallet.bankruptcy_active
    ? wallet.bankruptcy_badge.name
    : "正常营业";
}

function renderWallet(wallet) {
  $("myBalance").textContent = String(wallet.balance);
  $("walletBalance").textContent = `${wallet.balance} 枚`;
  $("checkInState").textContent = wallet.checked_in_today ? "今日已签到" : "今日未签到";
  $("walletBankruptcy").textContent = bankruptcyText(wallet);
  $("walletBankruptcyCount").textContent = `${wallet.bankruptcy_count} 次`;
  $("myBankruptcyState").textContent = bankruptcyText(wallet);
  $("myBankruptcyState").classList.toggle("active", wallet.bankruptcy_active);
  $("myBankruptcyCount").textContent = `破产 ${wallet.bankruptcy_count} 次`;
  $("checkInButton").disabled = wallet.checked_in_today;
  $("checkInButton").textContent = wallet.checked_in_today ? "今日已签到" : "立即签到";
  $("bankruptcyButton").disabled = !wallet.can_declare_bankruptcy;
}

function renderMachines(machines) {
  const select = $("machineSelect");
  select.replaceChildren();
  const emptyOption = document.createElement("option");
  emptyOption.value = "";
  emptyOption.textContent = machines.length ? "选择一只绑定小机" : "暂时没有绑定小机";
  select.append(emptyOption);
  for (const machine of machines) {
    const option = document.createElement("option");
    option.value = machine.id;
    option.textContent = machine.name;
    select.append(option);
  }
  select.disabled = machines.length === 0;
  $("machineEmpty").textContent = machines.length
    ? "选择后可只读查看小机钱包。"
    : "当前账号没有绑定小机；绑定后会在这里出现。";
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
    meta.textContent = `${entry.created_at.replace("T", " ")} · 余额 ${entry.balance_after}`;
    item.append(label, amount, meta);
    list.append(item);
  }
}

async function loadSummary() {
  try {
    summary = await requestJson("/api/chips");
    renderWallet(summary.wallet);
    renderMachines(summary.machines);
    renderLedger(summary.ledger);
  } catch (error) {
    showNotice(error.message, true);
    $("machineSelect").disabled = true;
  }
}

async function runHumanAction(url) {
  $("checkInButton").disabled = true;
  $("bankruptcyButton").disabled = true;
  try {
    const payload = await requestJson(url, {method: "POST", body: "{}"});
    summary.wallet = payload.wallet;
    summary.ledger = payload.ledger;
    renderWallet(payload.wallet);
    renderLedger(payload.ledger);
    showNotice(payload.message);
  } catch (error) {
    showNotice(error.message, true);
    if (summary) renderWallet(summary.wallet);
  }
}

async function loadMachine(machineId) {
  if (!machineId) {
    $("machineWallet").classList.add("hidden");
    $("machineEmpty").classList.remove("hidden");
    return;
  }
  $("machineEmpty").textContent = "正在读取小机钱包…";
  $("machineEmpty").classList.remove("hidden");
  $("machineWallet").classList.add("hidden");
  try {
    const payload = await requestJson(`/api/chips/machines/${encodeURIComponent(machineId)}`);
    $("machineName").textContent = payload.machine.name;
    $("machineBalance").textContent = String(payload.wallet.balance);
    $("machineState").textContent = bankruptcyText(payload.wallet);
    $("machineEmpty").classList.add("hidden");
    $("machineWallet").classList.remove("hidden");
  } catch (error) {
    $("machineEmpty").textContent = error.message;
    showNotice(error.message, true);
  }
}

$("checkInButton").addEventListener("click", () => runHumanAction("/api/chips/check-in"));
$("bankruptcyButton").addEventListener("click", () => runHumanAction("/api/chips/bankruptcy"));
$("machineSelect").addEventListener("change", (event) => loadMachine(event.target.value));

loadSummary();

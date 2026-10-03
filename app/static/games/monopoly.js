(function registerMonopolyRenderer() {
  "use strict";
  const PHASES = {roll: "掷骰出发", purchase: "购入地产", manage: "经营时间", auction: "地产拍卖", debt: "偿还欠款", finished: "本局结束"};
  const LABELS = {roll: "掷骰子", buy: "买下地产", auction: "放弃购买 · 拍卖", end_turn: "结束回合", pay_bail: "支付保释金", use_jail_card: "使用出狱卡", bankrupt: "宣告破产", build: "建房升级", sell_building: "出售一级建筑", mortgage: "抵押", redeem: "赎回", pass_bid: "退出本次拍卖"};
  const ASSET_ACTIONS = ["build", "sell_building", "mortgage", "redeem"];
  const PIPS = {1: [5], 2: [1, 9], 3: [1, 5, 9], 4: [1, 3, 7, 9], 5: [1, 3, 5, 7, 9], 6: [1, 3, 4, 6, 7, 9]};
  const KINDS = {start: "起点", chance: "机会", chest: "公益", tax: "税费", jail: "监狱", parking: "停车", go_to_jail: "入狱", railroad: "车站", utility: "设施", property: "地产"};
  const el = (tag, cls, text) => {const e = document.createElement(tag); if (cls) e.className = cls; if (text !== undefined) e.textContent = text; return e;};
  const money = value => Number(value || 0).toLocaleString("zh-CN");
  const players = c => c.state.players || [];
  const tiles = c => c.state.tiles || [];
  const name = (c, id) => {
    const p = (c.participants || []).find(p => p.player_id === id) || players(c).find(p => p.player_id === id);
    return p && (p.display_name || p.name) || `玩家 ${Math.max(1, players(c).findIndex(p => p.player_id === id) + 1)}`;
  };
  const seat = (c, id) => {
    const p = (c.participants || []).find(p => p.player_id === id);
    return p && Number.isInteger(p.seat_index) ? p.seat_index : Math.max(0, players(c).findIndex(p => p.player_id === id));
  };
  const actions = c => Array.isArray(c.legalActions) ? c.legalActions : [];
  const canAct = c => c.canMove && !c.isTerminal && c.helpers.canMove();
  // Public actions describe the actor; private actions authorize only the viewer.
  // A pending offer alone never interrupts the current turn.
  const pendingTrades = state => state.trades || (state.trade ? [state.trade] : []);
  const incomingTrade = c => pendingTrades(c.state).find(t => t.to === c.state.turn_player_id);
  const tradeResponseDue = c => !c.isTerminal && !!incomingTrade(c)
    && (Array.isArray(c.state.legal_actions) ? c.state.legal_actions : actions(c)).some(a => a.action === "respond_trade");
  const phaseLabel = c => tradeResponseDue(c) ? "交易待回应" : PHASES[c.state.phase] || "等待开始";
  const tradeOffer = (c, cash, ids, compact = false) => {
    const lands = (ids || []).map(id => tiles(c).find(t => t.id === id)?.name || "地产");
    return [cash ? `${compact ? "" : "现金 "}${money(cash)}` : "", compact && lands.length > 1 ? `${lands.length} 处地产` : lands.join("、")].filter(Boolean).join(" · ") || "—";
  };
  const tradePrompt = c => c.viewer?.player_id === incomingTrade(c).to
    ? `${name(c, incomingTrade(c).from)}向你提出交易` : `等待 ${name(c, incomingTrade(c).to)} 决定`;

  function pendingTradeCard(c, t) {
    const due = tradeResponseDue(c) && t.to === c.state.turn_player_id;
    const card = el("section", "monopoly-pending-trade"); card.setAttribute("aria-label", "挂起的交易报价");
    card.classList.toggle("is-due", due && c.viewer?.player_id === t.to);
    card.append(el("strong", "monopoly-trade-parties", `${name(c, t.from)} ⇄ ${name(c, t.to)}`));
    const columns = el("div", "monopoly-offer-columns");
    for (const [id, cash, ids] of [[t.from, t.give_cash, t.give_tiles], [t.to, t.take_cash, t.take_tiles]]) {
      const side = el("div", "monopoly-offer-side");
      side.append(el("small", "", `${name(c, id)}给出`), el("p", "", tradeOffer(c, cash, ids))); columns.append(side);
    }
    const status = el("p", "monopoly-help", due ? tradePrompt(c) : `等待 ${name(c, t.to)} 在自己的回合处理`);
    status.setAttribute("role", "status"); card.append(columns, status);
    if (due && c.viewer?.player_id === t.to && canAct(c)) {
      const footer = el("div", "monopoly-trade-response");
      actions(c).filter(a => a.action === "respond_trade").sort((a, b) => Number(a.accept) - Number(b.accept)).forEach(a => {
        const b = actionButton(c, a, a.accept ? "接受交易" : "拒绝交易");
        b.classList.toggle("primary", !!a.accept); footer.append(b);
      });
      if (footer.childElementCount) card.append(footer);
    }
    return card;
  }
  function pendingTradeList(c) {
    const trades = pendingTrades(c.state);
    const due = tradeResponseDue(c) && c.viewer?.player_id === c.state.turn_player_id;
    const key = JSON.stringify([c.room.room_id, c.viewer?.player_id, due ? [c.state.turn_number, incomingTrade(c)] : null]);
    if (c.uiState.monopolyTrades?.key !== key) c.uiState.monopolyTrades = {key, open: due};
    const list = el("details", "monopoly-pending-trades");
    list.open = c.uiState.monopolyTrades.open;
    list.classList.toggle("is-due", due);
    const summary = el("summary", "", `待处理交易 ${trades.length} 笔`);
    if (due) summary.append(el("span", "monopoly-help", "有交易等你回应"));
    list.append(summary);
    // Put the viewer's required decision before unrelated pending offers.
    const ordered = due ? [incomingTrade(c), ...trades.filter(t => t !== incomingTrade(c))] : trades;
    ordered.forEach(t => list.append(pendingTradeCard(c, t)));
    list.addEventListener("toggle", () => {
      if (list.isConnected && c.uiState.monopolyTrades?.key === key) c.uiState.monopolyTrades.open = list.open;
    });
    return list;
  }
  const eventCopy = event => typeof event === "string" ? event : event && (event.text || event.description || event.message || event.name) || "";

  function ensureStyles() {
    if (document.getElementById("duel-game-monopoly-styles")) return;
    const link = el("link"); link.id = "duel-game-monopoly-styles"; link.rel = "stylesheet";
    link.href = "/static/games/monopoly.css?v=10"; link.dataset.duelGameStyle = "monopoly"; document.head.append(link);
  }
  function button(label, handler, disabled = false, primary = false) {
    const b = el("button", `pixel-btn monopoly-button${primary ? " primary" : ""}`, label);
    b.type = "button"; b.disabled = disabled; b.addEventListener("click", handler); return b;
  }
  async function submit(c, action, source) {
    if (!canAct(c) || c.uiState.monopolySubmitting) return false;
    c.uiState.monopolySubmitting = true;
    if (source) source.disabled = true;
    try {
      return await c.helpers.submitMove({...action, action_seq: c.state.action_seq});
    } catch (_) {
      c.helpers.announce("操作未完成，请检查连接后重试。", {error: true}); return false;
    } finally {
      c.uiState.monopolySubmitting = false;
      if (source && source.isConnected) source.disabled = !canAct(c);
    }
  }
  function actionButton(c, action, label) {
    let b;
    b = button(label || LABELS[action.action] || action.action, async () => {
      if (action.action === "bankrupt") return bankruptcyDialog(c, action);
      await submit(c, action, b);
    }, !canAct(c), ["roll", "buy", "end_turn"].includes(action.action));
    b.dataset.action = action.action; return b;
  }
  function dialog(c, title) {
    const previousFocus = c.board.querySelector("dialog")?.monopolyReturnFocus || document.activeElement;
    c.board.querySelectorAll("dialog").forEach(d => {d.close(); d.remove();});
    const d = el("dialog", "monopoly-dialog");
    const heading = el("header", "monopoly-dialog-heading");
    const titleNode = el("h3", "", title); titleNode.id = "monopoly-dialog-title";
    d.setAttribute("aria-labelledby", titleNode.id);
    const close = button("关闭", () => d.close()); close.setAttribute("aria-label", "关闭对话框");
    heading.append(titleNode, close); d.append(heading);
    const body = el("div", "monopoly-dialog-body"); d.append(body); c.board.append(d);
    d.monopolyReturnFocus = previousFocus;
    d.addEventListener("close", () => {d.remove(); if (previousFocus && previousFocus.isConnected) previousFocus.focus();});
    d.addEventListener("click", e => {if (e.target === d) {const r = d.getBoundingClientRect(); if (e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom) d.close();}});
    d.showModal(); return {d, body};
  }
  function tileIcon(kind, id) {
    const paths = {
      chance: "M7 6a4 4 0 0 1 8 0c0 3-4 3-4 6M11 16v1",
      chest: "M3 9h16v3H3zM5 12v7h12v-7M11 9v10M11 9C3 9 4 2 8 4zM11 9c8 0 7-7 3-5z",
      jail: "M3 3h16v16H3zM7 3v16M12 3v16M16 3v16M2 7h18M2 15h18",
      go_to_jail: "M12 9V6a3 3 0 0 1 6 0v3M11 9h9v10h-9zM15 13v2M1 10h7M5 6l4 4-4 4",
      start: "M5 20V2M5 3h13l-3 4 3 4H5M2 20h8",
      parking: "M3 2h16v18H3zM8 16V6h4a3 3 0 0 1 0 6H8",
      tax: id === 38 ? "M5 2h12v18l-3-2-3 2-3-2-3 2zM8 8l3-3 3 3-3 3zM8 14h6" : "M5 2h12v18l-3-2-3 2-3-2-3 2zM8 6h6M8 10h6M8 14h4",
      railroad: "M5 3h12v12H5zM5 9h12M8 4v5M14 4v5M7 15l-3 5M15 15l3 5M6 18h10M8 12h1M13 12h1",
      utility: id === 28 ? "M11 2c-2 5-7 8-7 12a7 7 0 0 0 14 0c0-4-5-7-7-12z" : "M12 2L4 12h6l-1 8 9-12h-6z",
    };
    if (!paths[kind]) return null;
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("viewBox", "0 0 22 22"); svg.setAttribute("class", "monopoly-tile-icon");
    svg.setAttribute("aria-hidden", "true"); svg.setAttribute("focusable", "false");
    const path = document.createElementNS(svg.namespaceURI, "path"); path.setAttribute("d", paths[kind]); svg.append(path); return svg;
  }
  function bankruptcyDialog(c, action) {
    const {d, body} = dialog(c, "确认宣告破产");
    body.append(el("p", "", "破产后将退出本局，剩余资产按规则交给债权人或银行。可先出售建筑、抵押或协商交易筹款。"));
    const b = button("确认破产并离场", async () => {if (await submit(c, action, b)) d.close();}, !canAct(c));
    body.append(b);
  }
  function tileDialog(c, tile) {
    const {body} = dialog(c, tile.name);
    const owner = tile.owner ? name(c, tile.owner) : "银行";
    const summary = el("p", "monopoly-detail-summary", `${KINDS[tile.kind] || "地块"} · ${owner}${tile.mortgaged ? " · 已抵押" : ""}${tile.level ? ` · ${tile.level === 5 ? "旅馆" : tile.level + " 级建筑"}` : ""}`);
    if (tile.kind === "property" && tile.color) {
      const group = el("span", "monopoly-color-group", "颜色组"); group.style.setProperty("--land-color", tile.color); summary.append(group);
    }
    body.append(summary);
    if (tile.price) {
      const dl = el("dl", "monopoly-values");
      let rentValue = ["当前租金", tile.rent];
      if (tile.kind === "property") {
        if (tile.mortgaged) rentValue = ["当前收租", "已抵押，暂停收租"];
        else if (tile.owner == null) {
          const baseRent = Array.isArray(tile.rents) ? tile.rents[0] : undefined;
          rentValue = ["购买后基础租金", Number.isFinite(baseRent) && baseRent >= 0 ? baseRent : "—"];
        }
      }
      const values = [["购入价", tile.price], rentValue, ["每级建造", tile.build_cost], ["抵押所得", tile.mortgage_value], ["赎回花费", tile.redemption_cost]];
      values.forEach(([label, value]) => {if (value !== undefined && value !== null && !(label === "每级建造" && tile.kind !== "property")) dl.append(el("dt", "", label), el("dd", "", typeof value === "string" ? value : money(value)));});
      body.append(dl);
      if (tile.kind === "property" && Array.isArray(tile.rents) && tile.rents.length) {
        const table = el("table", "monopoly-rents");
        table.append(el("caption", "", "租金"));
        const tbody = el("tbody");
        tile.rents.forEach((rent, i) => {const row = el("tr", tile.kind === "property" && i === (tile.level || 0) ? "current" : ""); const th = el("th", "", i === 0 ? "空地" : i === 5 ? "旅馆" : `${i} 级建筑`); th.scope = "row"; if (row.classList.contains("current")) th.append(el("small", "", "当前档位")); row.append(th, el("td", "", money(rent))); tbody.append(row);});
        table.append(tbody); body.append(table);
      }
      const hints = tile.kind === "property" ? "集齐同色地产后，未抵押的空地租金翻倍；整套无抵押时可均衡建房。出售建筑按建造价的一半回收。" : tile.kind === "utility" ? "持有一处设施，租金为骰点的 4 倍；两处为 10 倍。机会事件以服务端公布的新骰点结算。" : "持有 1 / 2 / 3 / 4 座车站，租金分别为 25 / 50 / 100 / 200。";
      body.append(el("p", "monopoly-help", hints));
    } else {
      const copy = {start: "经过或停在起点领取 200。", jail: "经过此处只是探访。入狱后可尝试双骰、支付保释金或使用出狱卡。", chance: "抽取机会事件并由银行自动结算。", chest: "抽取公益事件并由银行自动结算。", tax: "停在这里时缴纳规定税费。", parking: "免费停车，不收取费用，也不积累奖金。", go_to_jail: "立即进入监狱，不领取经过起点奖励。"};
      body.append(el("p", "", copy[tile.kind] || "由银行自动结算此格效果。"));
    }
    const controls = el("div", "monopoly-actions");
    actions(c).filter(a => ASSET_ACTIONS.includes(a.action) && a.tile_id === tile.id).forEach(a => controls.append(actionButton(c, a)));
    if (controls.childElementCount) body.append(controls);
  }
  function playerTabs(c, ids, selected, label, onChange) {
    const group = el("div", "monopoly-player-tabs");
    group.setAttribute("role", "group"); group.setAttribute("aria-label", label);
    ids.forEach(id => {
      const b = button(name(c, id), () => {
        if (b.getAttribute("aria-pressed") === "true") return;
        [...group.children].forEach(item => item.setAttribute("aria-pressed", String(item === b)));
        onChange(id);
      });
      b.classList.add(`seat-${seat(c, id)}`); b.title = name(c, id);
      b.dataset.playerId = id; b.setAttribute("aria-pressed", String(id === selected)); group.append(b);
    });
    return group;
  }
  function assetList(c, playerId, list) {
    list.replaceChildren();
    const owned = tiles(c).filter(t => t.owner === playerId);
    if (!owned.length) list.append(el("p", "monopoly-help monopoly-empty", "暂无地产。"));
    owned.forEach(t => {
      const b = button("", () => tileDialog(c, t));
      b.append(el("span", "", t.name), el("small", "", t.mortgaged ? "已抵押" : t.level ? t.level === 5 ? "旅馆" : t.level + " 级建筑" : "空地"));
      b.classList.add("monopoly-asset"); b.style.setProperty("--land-color", t.color || "#8678a0"); list.append(b);
    });
  }
  function playerKind(participant) {
    return {human: "人类", bound_machine: "绑定小机", system_npc: "系统 NPC"}[participant.participant_kind] || "玩家";
  }
  function playerDialog(c, playerId) {
    const player = players(c).find(p => p.player_id === playerId);
    if (!player) return;
    const participant = (c.participants || []).find(p => p.player_id === playerId) || player;
    const {body} = dialog(c, "玩家详情");
    const identity = el("div", `monopoly-player-detail seat-${seat(c, playerId)}`);
    const avatar = el("span", "board-edge-avatar"); c.helpers.renderParticipantAvatar(avatar, participant);
    const isSelf = playerId === (c.viewer || {}).player_id;
    const current = !c.isTerminal && playerId === c.room.current_player_id;
    const copy = el("div", "monopoly-player-identity");
    copy.append(el("strong", "", name(c, playerId)), el("small", "monopoly-help", `${playerKind(participant)}${isSelf ? " · 你" : ""}${current ? " · 行动中" : ""}${player.bankrupt ? " · 已破产" : player.jailed ? " · 在押" : ""}`));
    identity.append(avatar, copy);
    const owned = tiles(c).filter(t => t.owner === playerId);
    const primary = el("dl", "monopoly-player-wealth");
    [["现金", money(player.cash)], ["总资产", money(player.asset_value)]].forEach(([label, value]) => {
      const item = el("div"); item.append(el("dt", "", label), el("dd", "", value)); primary.append(item);
    });
    const stats = el("dl", "monopoly-player-stats");
    [["地产", `${owned.length} 处`], ["已抵押", `${owned.filter(t => t.mortgaged).length} 处`],
      ["建筑", `${owned.reduce((n, t) => n + (t.level < 5 ? t.level || 0 : 0), 0)} 栋房屋 · ${owned.filter(t => t.level === 5).length} 座旅馆`]]
      .forEach(([label, value]) => {const item = el("div"); item.append(el("dt", "", label), el("dd", "", value)); stats.append(item);});
    const list = el("div", "monopoly-asset-list"); assetList(c, playerId, list);
    body.append(identity, primary, stats, list);
  }

  function assetsDialog(c) {
    const {body} = dialog(c, "地产与经营");
    const supply = c.state.bank_supply;
    if (supply) body.append(el("p", "monopoly-help", `银行余量：${supply.houses} 栋房屋 · ${supply.hotels} 座旅馆。建筑数量有限，按整套地产均衡升级。`));
    let selected = c.viewer && c.viewer.player_id || c.state.current_player_id;
    body.append(playerTabs(c, players(c).map(p => p.player_id), selected, "查看玩家", id => {selected = id; draw();}));
    const balance = el("p", "monopoly-asset-summary"); body.append(balance);
    const list = el("div", "monopoly-asset-list"); body.append(list);
    function draw() {
      list.replaceChildren();
      const player = players(c).find(p => p.player_id === selected);
      balance.replaceChildren(el("span", "", name(c, selected)), el("span", "", `现金 ${money(player && player.cash)}`));
      assetList(c, selected, list);
    }
    draw();
  }
  function tradeDialog(c) {
    const options = c.privateState && c.privateState.trade_options;
    const selfId = c.viewer && c.viewer.player_id;
    const partners = (options && options.partners || []).filter(id => id !== selfId && players(c).some(p => p.player_id === id && !p.bankrupt));
    if (!partners.length) return;
    const {d, body} = dialog(c, "提出交易");
    const help = el("p", "monopoly-help", "填写的是提议，对方同意才交换；不付现金填 0。提出即确认你给出的条件，抵押状态随地产转移。");
    help.id = "monopoly-trade-help"; body.append(help);
    const form = el("form", "monopoly-trade-form"); form.setAttribute("aria-describedby", help.id);
    let partner = partners[0];
    form.append(playerTabs(c, partners, partner, "交易对象", id => {partner = id; changePartner();}));
    const columns = el("div", "monopoly-trade-columns");
    function side(title, key) {
      const fieldset = el("fieldset"); fieldset.append(el("legend", "", title));
      const label = el("label", "monopoly-field", "现金"); const input = el("input"); input.type = "number"; input.min = "0"; input.step = "1"; input.value = "0"; input.required = true; input.name = key;
      const balance = el("small", "monopoly-help"); label.append(input, balance); const checklist = el("div", "monopoly-trade-tiles"); fieldset.append(label, checklist); columns.append(fieldset); return {input, checklist, balance};
    }
    const give = side("我给出", "give_cash"), take = side("希望对方给出", "take_cash");
    const checked = target => [...target.querySelectorAll("input:checked")].map(n => Number(n.value));
    const preview = el("p", "monopoly-trade-preview"); preview.setAttribute("role", "status");
    function describe(side) {
      const lands = checked(side.checklist).map(id => tiles(c).find(t => t.id === id).name);
      return `${Number(side.input.value) ? "现金 " + money(side.input.value) : "不付现金"}、${lands.length ? lands.join(" / ") : "无地产"}`;
    }
    function updatePreview() {
      preview.replaceChildren(el("small", "monopoly-help", "交换预览"), el("span", "", `你：${describe(give)} ↔ ${name(c, partner)}：${describe(take)}`));
    }
    form.addEventListener("input", updatePreview);
    function populate(target, ids) {
      target.replaceChildren();
      ids.forEach(id => {const tile = tiles(c).find(t => t.id === id); if (!tile) return; const label = el("label", "monopoly-check"); const input = el("input"); input.type = "checkbox"; input.value = String(id); label.append(input, el("span", "", `${tile.name}${tile.mortgaged ? "（抵押）" : ""}`)); target.append(label);});
      if (!ids.length) target.append(el("p", "monopoly-help", "没有可交易的地产"));
    }
    give.input.max = String((options.cash_by_player || {})[selfId] || 0);
    populate(give.checklist, options.give_tiles || []);
    give.balance.textContent = `可用现金 ${money(give.input.max)}`;
    function changePartner() {
      take.input.max = String((options.cash_by_player || {})[partner] || 0); take.input.value = "0";
      take.balance.textContent = `对方现金 ${money(take.input.max)}`;
      populate(take.checklist, (options.take_tiles_by_player || {})[partner] || []); updatePreview();
    }
    changePartner();
    const error = el("p", "monopoly-form-error"); error.setAttribute("role", "alert");
    const send = button("确认条件并提出交易", () => {} , !canAct(c), true); send.type = "submit";
    form.append(columns, preview, error, send); body.append(form);
    form.addEventListener("submit", async e => {
      e.preventDefault(); if (!form.reportValidity()) return;
      const proposal = {action: "propose_trade", to: partner, give_cash: Number(give.input.value), take_cash: Number(take.input.value), give_tiles: checked(give.checklist), take_tiles: checked(take.checklist)};
      if (!proposal.give_cash && !proposal.take_cash && !proposal.give_tiles.length && !proposal.take_tiles.length) {error.textContent = "请至少加入现金或一处地产。"; return;}
      if (await submit(c, proposal, send)) d.close(); else error.textContent = "交易未提交，请检查当前回合与交易条件。";
    });
  }
  function dice(values) {
    const row = el("div", "monopoly-dice"); row.setAttribute("aria-label", values && values.length ? `骰子 ${values.join("、")} 点` : "尚未掷骰");
    (values && values.length ? values : [0, 0]).forEach(value => {
      const die = el("span", `monopoly-die${value ? "" : " empty"}`); die.setAttribute("aria-hidden", "true");
      for (let i = 1; i <= 9; i++) die.append(el("i", (PIPS[value] || []).includes(i) ? "on" : "")); row.append(die);
    }); return row;
  }
  function tilePosition(i) {
    if (i <= 10) return [11, 11 - i];
    if (i <= 20) return [21 - i, 1];
    if (i <= 30) return [1, i - 19];
    return [i - 29, 11];
  }
  // Only the currently rendered room/viewer owns presentation state. Never
  // reconstruct unobserved turns from a stale die or a position difference.
  let movementView = null;
  const movementKey = (room, viewer) => JSON.stringify([room.room_id, viewer || "spectator"]);
  const jailStatus = (state, player) => player.jailed ? "（在押）" : (state.tiles || []).find(t => t.id === player.position)?.kind === "jail" ? "（只是探访，未入狱）" : "";
  const positionCopy = (state, player) => `你当前在：${(state.tiles || []).find(t => t.id === player.position)?.name || "未知地块"}${jailStatus(state, player)}`;
  function monopolyTransitionBeats(previousRoom, nextRoom) {
    if (!previousRoom || !nextRoom || previousRoom.room_id !== nextRoom.room_id) return [];
    const before = previousRoom.board_state || {}, after = nextRoom.board_state || {};
    if (!(after.action_seq > before.action_seq)) return [];
    const beats = [], prior = new Map((before.players || []).map(p => [p.player_id, p]));
    const lands = new Map((before.tiles || []).map(t => [t.id, t]));
    const roster = after.players || [], tiles = after.tiles || [], note = eventCopy(after.last_action_note);
    const fullName = id => name({participants: nextRoom.participants, state: after}, id);
    const who = id => {const value = Array.from(fullName(id)); return value.length > 12 ? value.slice(0, 11).join("") + "…" : value.join("");};
    const rawName = p => p.name || fullName(p.player_id);
    const paid = (p, tile) => {
      const prefix = `${rawName(p)}以`, suffix = `买下${tile.name}。`;
      const start = note.indexOf(prefix);
      if (start < 0) return null;
      const tail = note.slice(start + prefix.length), end = tail.indexOf(suffix);
      return end >= 0 && /^\d+$/.test(tail.slice(0, end)) ? Number(tail.slice(0, end)) : null;
    };
    const add = (kind, title, text, tileIds = [], detail = "", strong = false, duration = 1400) =>
      beats.push({kind, title, text, tileIds: tileIds.filter(Number.isInteger), detail, strong, duration});
    const cards = (after.last_card_events || []).filter((e, i, all) =>
      e.action_seq > before.action_seq && e.action_seq <= after.action_seq && ["chance", "chest"].includes(e.deck)
      && all.findIndex(other => other.event_id === e.event_id) === i);
    for (const card of cards) {
      const land = tiles.find(t => card.summary?.includes(t.name));
      add(card.deck, card.deck === "chance" ? "机会" : "公益", `${who(card.player_id)}抽到：${card.text}`,
        land ? [land.id] : [], card.summary || "", true, 2000);
      beats.at(-1).eventId = card.event_id;
    }
    const failed = new Set(roster.filter(p => p.bankrupt && !prior.get(p.player_id)?.bankrupt).map(p => p.player_id));
    const removedTrades = pendingTrades(before).filter(t => !pendingTrades(after).some(next => next.to === t.to));
    const trade = note.includes("交易已同时交割") ? removedTrades.find(t => t.to === before.turn_player_id) : null;
    if (trade) {
      const offer = (cash, ids) => [cash ? `现金 ${money(cash)}` : "", ids?.length === 1 ? `「${tiles.find(t => t.id === ids[0])?.name || "地产"}」` : ids?.length ? `${ids.length} 处地产` : ""].filter(Boolean).join("、") || "无现金或地产";
      add("trade", "交易完成", `${who(trade.from)} ⇄ ${who(trade.to)}`, [...(trade.give_tiles || []), ...(trade.take_tiles || [])],
        `${offer(trade.give_cash, trade.give_tiles)} ⇄ ${offer(trade.take_cash, trade.take_tiles)}`, false, 2000);
    } else if (note.includes("交易已同时交割")) {
      // A poll may miss the proposal itself. Acknowledge the confirmed trade
      // without inventing its terms from unrelated net cash changes.
      add("trade", "交易完成", "双方已完成交易交割", tiles.filter(t => lands.get(t.id)?.owner !== t.owner).map(t => t.id));
    }
    if (removedTrades.length && note.includes("交易条件已失效"))
      add("trade-expired", "报价已取消", "交易条件已失效，可继续当前回合");
    for (const tile of tiles) {
      const old = lands.get(tile.id);
      if (!old || failed.has(old.owner)) continue; // Liquidation is one bankruptcy, not a series of sales.
      if (!old.owner && tile.owner) {
        const owner = roster.find(p => p.player_id === tile.owner), price = owner ? paid(owner, tile) : null;
        const auction = before.auction?.tile_id === tile.id;
        if (auction) add("auction", "拍卖成交", `「${tile.name}」由${who(tile.owner)}${price !== null ? `以 ${money(price)} ` : ""}拍得`, [tile.id], "", true, 1800);
        else if (price !== null || (before.phase === "purchase" && before.current_player_id === tile.owner))
          add("buy", "购入地产", `${who(tile.owner)}买下「${tile.name}」 · ${money(price ?? tile.price)}`, [tile.id]);
        else add("ownership", "产权变更", `${who(tile.owner)}取得「${tile.name}」`, [tile.id]);
      }
      if (old.owner && old.owner === tile.owner && old.level !== tile.level) {
        const built = tile.level > old.level;
        add(built ? "build" : "sell", built ? "建房升级" : "出售建筑",
          `${who(tile.owner)}在「${tile.name}」${built ? "建至" : "卖房后剩余"} ${tile.level === 5 ? "旅馆" : `${tile.level} 级`}`, [tile.id]);
      }
      if (old.owner && old.owner === tile.owner && !!old.mortgaged !== !!tile.mortgaged)
        add(tile.mortgaged ? "mortgage" : "redeem", tile.mortgaged ? "抵押地产" : "赎回地产", `${who(tile.owner)}${tile.mortgaged ? "抵押" : "赎回"}「${tile.name}」`, [tile.id]);
    }
    // Cash deltas can combine salary, cards and rent. Read explicit settlement
    // receipts instead of labelling an arbitrary net cash loss as a payment.
    for (const p of roster) {
      const old = prior.get(p.player_id); if (!old) continue;
      const prefix = `${rawName(p)}支付`, receipts = note.split(prefix).slice(1);
      for (const tail of receipts) {
        const match = /^(\d+)（([^）]+)）/.exec(tail); if (!match) continue;
        const amount = money(Number(match[1])), reason = match[2];
        if (cards.some(card => card.text === reason || (reason === "费用" && card.summary?.includes("维修费")))) continue;
        const land = tiles.find(t => `${t.name}租金` === reason);
        if (land?.owner) add("rent", "支付租金", `${who(p.player_id)}向${who(land.owner)}支付「${land.name}」租金 ${amount}`, [land.id]);
        else add(/保释金/.test(reason) ? "bail" : "tax", "支付费用", `${who(p.player_id)}支付${reason} ${amount}`, [p.position]);
      }
      if (after.debt && after.debt.payer === p.player_id
        && (!before.debt || before.debt.payer !== p.player_id || before.debt.reason !== after.debt.reason))
        add("debt", "等待筹款", `${who(p.player_id)}需筹款 ${money(after.debt.amount)}`, [p.position], after.debt.reason || "");
      if (old.jailed && !p.jailed && old.cash - p.cash === 50 && note.includes("已出狱，可以掷骰"))
        add("bail", "支付保释金", `${who(p.player_id)}支付保释金 50`, [10]);
      if (!old.jailed && p.jailed && !cards.some(card => card.player_id === p.player_id && card.summary?.includes("进入监狱")))
        add("jail", "进入监狱", `${who(p.player_id)}被送进监狱`, [10], "", true, 1800);
      if (failed.has(p.player_id)) add("bankrupt", "破产离场", `${who(p.player_id)}破产离场`, [old.position], "", true, 1800);
    }
    // Salary is secondary, and only attributed when this snapshot spans one action.
    if (after.action_seq === before.action_seq + 1 && note.includes("经过起点，领取200"))
      add("salary", "经过起点", `${who(before.current_player_id)}领取 200`, [0], "", false, 1200);
    return beats;
  }
  async function transitionFeedback(args = {}) {
    const {previousRoom, nextRoom, document: doc = window.document} = args;
    if (!previousRoom || !nextRoom || previousRoom.game_type !== "monopoly" || nextRoom.game_type !== "monopoly"
      || previousRoom.room_id !== nextRoom.room_id || previousRoom.revision === nextRoom.revision
      || previousRoom.viewer?.player_id !== nextRoom.viewer?.player_id) return;
    const view = movementView, seq = nextRoom.board_state?.action_seq;
    const wrap = doc.querySelector(".monopoly-game"), key = movementKey(nextRoom, nextRoom.viewer?.player_id);
    if (!view || view.key !== key || !wrap || wrap.dataset.movementContext !== key
      || wrap.dataset.revision !== String(previousRoom.revision) || view.pending || !(seq > view.seen)) return;
    const beats = monopolyTransitionBeats(previousRoom, nextRoom);
    const alive = () => movementView === view && wrap.isConnected && !wrap.closest(".hidden") && doc.querySelector(".monopoly-game") === wrap;
    if (!alive()) return;
    view.pending = true; view.seen = seq;
    window.clearTimeout(view.timer);
    wrap.querySelectorAll(".is-arrival").forEach(t => t.classList.remove("is-arrival"));
    const center = wrap.querySelector(".monopoly-center"), controls = doc.querySelector(".monopoly-controls");
    const inert = controls?.inert, buttons = [...wrap.querySelectorAll("button")].map(b => [b, b.disabled]);
    let layer;
    const pause = async ms => {
      const end = Date.now() + ms;
      while (alive() && Date.now() < end) await new Promise(resolve => window.setTimeout(resolve, Math.min(100, end - Date.now())));
    };
    try {
      if (controls) controls.inert = true; buttons.forEach(([b]) => {b.disabled = true;});
      await playViewerMovement(args);
      if (!alive()) return;
      // Keep the just-moved pawn at its destination while the old board hosts
      // feedback; the normal host render still owns all authoritative updates.
      if (beats.length && view.arrival?.seq === seq) {
        const source = wrap.querySelector(".monopoly-tokens .is-viewer"), dest = wrap.querySelector(`[data-tile-id="${view.arrival.position}"]`);
        if (source && dest && source.closest(".monopoly-tile") !== dest) {
          const oldRack = source.parentElement, oldTile = source.closest(".monopoly-tile");
          let rack = dest.querySelector(".monopoly-tokens");
          if (!rack) {rack = el("span", "monopoly-tokens"); rack.setAttribute("aria-hidden", "true"); dest.append(rack);}
          rack.append(source); dest.classList.add("occupied", "is-viewer-tile"); oldTile.classList.remove("is-viewer-tile");
          for (const r of [oldRack, rack]) {
            r.classList.toggle("small-group", r.childElementCount <= 2); r.classList.toggle("crowded", r.childElementCount > 4);
          }
          if (!oldRack.childElementCount) {oldRack.remove(); oldTile.classList.remove("occupied");}
        }
      }
      for (const beat of beats) {
        if (!alive()) return;
        layer = el("div", `monopoly-event-layer${beat.strong ? " important" : ""} event-${beat.kind}`);
        layer.dataset.kind = beat.kind; if (beat.eventId) layer.dataset.eventId = beat.eventId;
        layer.setAttribute("role", "status"); layer.setAttribute("aria-atomic", "true");
        layer.append(el("span", "monopoly-event-label", beat.title), el("strong", "monopoly-event-copy", beat.text));
        const result = el("p", "monopoly-event-detail", beat.detail);
        if (beat.detail) {if (beat.eventId) result.classList.add("pending-result"); layer.append(result);}
        center.classList.add("has-event"); center.append(layer);
        const highlighted = beat.tileIds.map(id => wrap.querySelector(`[data-tile-id="${id}"]`)).filter(Boolean);
        highlighted.forEach(t => t.classList.add("is-event-target"));
        try {
          if (beat.eventId) {await pause(650); result.classList.remove("pending-result"); await pause(beat.duration - 650);}
          else await pause(beat.duration);
        } finally {highlighted.forEach(t => t.classList.remove("is-event-target")); layer.remove();}
      }
      if (alive() && view.arrival?.seq === seq) view.arrival.until = Date.now() + 2400;
    } finally {
      layer?.remove(); center?.classList.remove("has-event");
      if (controls) controls.inert = inert; buttons.forEach(([b, disabled]) => {b.disabled = disabled;});
      view.pending = false;
    }
  }
  async function playViewerMovement({document: doc = window.document, previousRoom, nextRoom} = {}) {
    if (!previousRoom || !nextRoom || previousRoom.game_type !== "monopoly" || nextRoom.game_type !== "monopoly"
      || previousRoom.room_id !== nextRoom.room_id || previousRoom.revision === nextRoom.revision) return;
    const viewer = nextRoom.viewer?.player_id;
    if (!viewer || previousRoom.viewer?.player_id !== viewer) return;
    const before = previousRoom.board_state || {}, after = nextRoom.board_state || {};
    const oldPlayer = (before.players || []).find(p => p.player_id === viewer);
    const newPlayer = (after.players || []).find(p => p.player_id === viewer);
    if (!oldPlayer || !newPlayer || oldPlayer.bankrupt || newPlayer.bankrupt
      || !Number.isInteger(oldPlayer.position) || !Number.isInteger(newPlayer.position)
      || oldPlayer.position < 0 || oldPlayer.position >= 40 || newPlayer.position < 0 || newPlayer.position >= 40
      || !(after.action_seq > before.action_seq)) return;
    const jailed = newPlayer.jailed && !oldPlayer.jailed;
    if (oldPlayer.position === newPlayer.position && !jailed) return;
    const key = movementKey(nextRoom, viewer), view = movementView;
    const wrap = doc.querySelector(".monopoly-game"), ring = wrap?.querySelector(".monopoly-ring");
    if (!view || view.key !== key || !ring || wrap.dataset.movementContext !== key
      || wrap.dataset.revision !== String(previousRoom.revision)) return;
    const source = [...ring.querySelectorAll(".monopoly-tokens .monopoly-token")].find(t => t.dataset.playerId === viewer);
    if (!source) return;
    const destination = (after.tiles || []).find(t => t.id === newPlayer.position)?.name || "未知地块";
    const copy = jailed ? "你被送到监狱" : `你移动到：${destination}${jailStatus(after, newPlayer)}`;
    const alive = () => movementView === view && wrap.isConnected && !wrap.closest(".hidden")
      && doc.querySelector(".monopoly-game") === wrap;
    const values = after.dice;
    const rolled = after.action_seq === before.action_seq + 1 && before.current_player_id === viewer
      && (before.phase === "roll" || (before.phase === "manage" && before.extra_roll))
      && Array.isArray(values) && values.length === 2 && values.every(n => Number.isInteger(n) && n >= 1 && n <= 6);
    const steps = rolled && !jailed ? values[0] + values[1] : 0;
    const route = Array.from({length: steps}, (_, i) => (oldPlayer.position + i + 1) % 40);
    const reduced = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    const wait = ms => new Promise(resolve => window.setTimeout(resolve, ms));
    const location = wrap.querySelector(".monopoly-location"), originalCopy = location?.textContent;
    const controls = doc.querySelector(".monopoly-controls");
    const wasInert = wrap.inert, controlsInert = controls?.inert;
    let ghost = null, passing = null, completed = false;
    try {
      if (!alive()) return;
      wrap.inert = true; if (controls) controls.inert = true;
      wrap.setAttribute("aria-busy", "true");
      if (!reduced) {
        ghost = source.cloneNode(true); ghost.classList.add("monopoly-moving-token");
        ghost.setAttribute("aria-hidden", "true"); ring.append(ghost);
        source.classList.add("is-moving-source");
        const place = id => {
          const tile = ring.querySelector(`[data-tile-id="${id}"]`);
          if (!tile) return;
          const rect = tile.getBoundingClientRect(), bounds = ring.getBoundingClientRect();
          ghost.style.left = `${rect.left - bounds.left - ring.clientLeft + (rect.width - 16) / 2}px`;
          ghost.style.top = `${rect.top - bounds.top - ring.clientTop + (rect.height - 16) / 2}px`;
          ghost.dataset.tileId = id;
          passing?.classList.remove("is-passing"); passing = tile; passing.classList.add("is-passing");
        };
        place(oldPlayer.position);
        // Flush the initial position before enabling the short per-cell tween.
        ghost.getBoundingClientRect(); ghost.classList.add("is-walking");
        if (location) location.textContent = steps ? `你正在移动：${values.join(" + ")} 点` : copy;
        for (const id of route) {
          if (!alive()) return;
          place(id); await wait(110);
        }
        if (!alive()) return;
        if (!route.length || route.at(-1) !== newPlayer.position) {
          // A card, jail, or missed polling revisions: show the authoritative
          // destination with a fade, never invent a long clockwise route.
          ghost.classList.remove("is-walking"); ghost.classList.add("is-jumping");
          if (location) location.textContent = copy;
          await wait(120); if (!alive()) return;
          place(newPlayer.position); ghost.classList.remove("is-jumping");
          await wait(160);
        }
      }
      if (!alive()) return;
      view.arrival = {position: newPlayer.position, seq: after.action_seq, copy, until: Date.now() + 2400};
      completed = true;
    } finally {
      ghost?.remove(); passing?.classList.remove("is-passing"); source.classList.remove("is-moving-source");
      wrap.inert = wasInert; if (controls) controls.inert = controlsInert;
      wrap.removeAttribute("aria-busy");
      if (location) location.textContent = completed ? copy : originalCopy;
    }
  }
  function renderBoard(c) {
    ensureStyles(); c.board.classList.add("monopoly");
    const wrap = el("div", "monopoly-game");
    const viewer = players(c).find(p => p.player_id === c.viewer?.player_id && !p.bankrupt);
    const key = movementKey(c.room, c.viewer?.player_id);
    window.clearTimeout(movementView?.timer);
    if (movementView?.key !== key) movementView = {key, seen: Number(c.state.action_seq || 0)};
    const view = movementView;
    view.seen = Math.max(view.seen, Number(c.state.action_seq || 0));
    const arrival = view.arrival?.until > Date.now() && view.arrival.seq === c.state.action_seq
      && view.arrival.position === viewer?.position ? view.arrival : null;
    wrap.dataset.movementContext = key; wrap.dataset.revision = c.room.revision;
    const heading = el("div", "monopoly-heading"); heading.append(el("strong", "", "大富翁"), el("span", "", `第 ${c.state.turn_number || 1} 回合 · ${phaseLabel(c)}`)); wrap.append(heading);
    // The same edge identity component/classes as aeroplane chess, with assets
    // inside each seat rather than a second set of player cards.
    const edges = [el("div", "monopoly-seats"), el("div", "monopoly-seats")];
    const ordered = [...players(c)];
    const viewerIndex = ordered.findIndex(p => p.player_id === (c.viewer || {}).player_id);
    if (viewerIndex >= 0) ordered.push(...ordered.splice(0, viewerIndex + 1));
    ordered.forEach((p, i) => {
      const participant = (c.participants || []).find(item => item.player_id === p.player_id) || p;
      const current = !c.isTerminal && p.player_id === c.room.current_player_id;
      const isSelf = p.player_id === (c.viewer || {}).player_id;
      const card = el("button", `board-edge-participant monopoly-player seat-${seat(c, p.player_id)}${current ? " current" : ""}${isSelf ? " viewer" : ""}${p.bankrupt ? " bankrupt" : ""}`);
      card.type = "button"; card.setAttribute("aria-haspopup", "dialog");
      card.setAttribute("aria-label", `座位 ${seat(c, p.player_id) + 1}，${name(c, p.player_id)}${isSelf ? "，你" : ""}，查看公开资产详情`);
      card.addEventListener("click", () => playerDialog(c, p.player_id));
      card.dataset.playerId = p.player_id; card.setAttribute("aria-current", String(current));
      const avatar = el("span", "board-edge-avatar"); c.helpers.renderParticipantAvatar(avatar, participant);
      const copy = el("span", "board-edge-copy");
      const title = el("strong", "", `${name(c, p.player_id)}${isSelf ? "（你）" : ""}`); title.title = title.textContent;
      const kind = playerKind(participant);
      copy.append(title, el("small", "", `${kind}${current ? " · 行动中" : ""}${p.jailed ? " · 在押" : ""}`));
      const identity = el("span", "monopoly-seat-identity");
      const badge = el("span", `monopoly-token monopoly-seat-badge seat-${seat(c, p.player_id)}`, seat(c, p.player_id) + 1);
      badge.setAttribute("aria-label", `座位 ${seat(c, p.player_id) + 1}`); identity.append(avatar, badge);
      card.append(identity, copy, el("span", "monopoly-cash", p.bankrupt ? "已破产" : `现金 ${money(p.cash)}`), el("small", "monopoly-assets", `${p.property_count || 0} 处地产 · 总资产 ${money(p.asset_value)}`));
      edges[i < Math.ceil(ordered.length / 2) ? 0 : 1].append(card);
    });
    wrap.append(edges[0]);
    const ring = el("div", "monopoly-ring"); ring.setAttribute("aria-label", "40 格地产棋盘，点击地块查看详情");
    const center = el("div", "monopoly-center");
    if (tradeResponseDue(c)) {
      const t = incomingTrade(c), task = el("div", "monopoly-trade-task"); task.setAttribute("role", "status");
      task.append(el("strong", "", "⇄ 交易待回应"), el("p", "", tradePrompt(c)),
        el("small", "", `${tradeOffer(c, t.give_cash, t.give_tiles, true)} ↔ ${tradeOffer(c, t.take_cash, t.take_tiles, true)}`));
      center.append(task);
    } else {
      center.append(el("span", "monopoly-center-label", "城市经营"), el("strong", "monopoly-center-title", "大富翁"), dice(c.state.dice));
      center.append(el("p", "monopoly-turn", c.isTerminal || c.state.phase === "finished" ? "本局已结束" : `${name(c, c.state.turn_player_id || c.state.current_player_id)}的${c.state.phase === "auction" ? "出价" : "回合"}`));
    }
    if (viewer) {
      const location = el("p", "monopoly-location", arrival ? arrival.copy : positionCopy(c.state, viewer));
      location.setAttribute("role", "status"); center.append(location);
    }
    center.append(el("span", "monopoly-center-hint", "点地块看详情")); ring.append(center);
    tiles(c).forEach(t => {
      const [row, col] = tilePosition(t.id); const edge = row === 11 ? "bottom" : row === 1 ? "top" : col === 1 ? "left" : "right";
      const b = el("button", `monopoly-tile edge-${edge} kind-${t.kind}${t.mortgaged ? " mortgaged" : ""}${t.id % 10 === 0 ? " corner" : ""}`);
      b.type = "button"; b.style.gridRow = String(row); b.style.gridColumn = String(col); b.style.setProperty("--land-color", t.color || "#d8cbdc"); b.dataset.tileId = t.id;
      const occupants = players(c).filter(p => !p.bankrupt && p.position === t.id);
      if (occupants.length) b.classList.add("occupied");
      if (viewer?.position === t.id) b.classList.add("is-viewer-tile");
      if (arrival?.position === t.id) {
        b.classList.add("is-arrival"); b.style.animationDelay = `${arrival.until - Date.now() - 2400}ms`;
      }
      const ownerIndex = t.owner ? seat(c, t.owner) : -1;
      b.setAttribute("aria-label", `${t.name}，${t.owner ? name(c, t.owner) + "的地产" : KINDS[t.kind] || "地块"}${t.mortgaged ? "，已抵押" : ""}${t.level ? `，建筑 ${t.level} 级` : ""}${occupants.length ? "，" + occupants.map(p => name(c, p.player_id)).join("、") + "在此" : ""}`);
      const label = el("span", "monopoly-tile-label");
      const icon = tileIcon(t.kind, t.id); if (icon) label.append(icon);
      const tileName = el("span", "monopoly-tile-name");
      if (t.kind === "property" && ["top", "bottom"].includes(edge)) {
        // Some system CJK fonts report zero vertical advances in writing-mode.
        // Stack normal glyphs so street names remain legible on those devices.
        Array.from(t.name).forEach(character => tileName.append(el("span", "", character)));
      } else tileName.textContent = t.name;
      label.append(tileName); b.append(label);
      const markers = el("span", "monopoly-tile-markers");
      if (ownerIndex >= 0) {const owner = el("span", `monopoly-owner seat-${ownerIndex}`, ownerIndex + 1); owner.title = `产权：${name(c, t.owner)}`; markers.append(owner);}
      if (t.level) {
        const building = el("span", `monopoly-buildings${t.level === 5 ? " hotel" : ""}`, t.level === 5 ? "▰" : `⌂${t.level}`);
        building.title = t.level === 5 ? "旅馆" : `建筑 ${t.level} 级`; markers.append(building);
      }
      if (markers.childElementCount) b.append(markers);
      if (occupants.length) {
        const tokens = el("span", `monopoly-tokens${occupants.length > 4 ? " crowded" : ""}${occupants.length <= 2 ? " small-group" : ""}`);
        tokens.setAttribute("aria-hidden", "true");
        occupants.forEach(p => {
          const n = seat(c, p.player_id), isViewer = p.player_id === viewer?.player_id;
          const token = el("span", `monopoly-token seat-${n}${isViewer ? " is-viewer" : ""}`, n + 1);
          token.dataset.playerId = p.player_id; token.title = isViewer ? "你的棋子" : name(c, p.player_id); tokens.append(token);
        }); b.append(tokens);
      }
      b.addEventListener("click", () => tileDialog(c, t)); ring.append(b);
    }); wrap.append(ring, edges[1]);
    const activity = el("div", "monopoly-activity"); activity.setAttribute("role", "status");
    activity.textContent = eventCopy(c.state.last_action_note) || eventCopy(c.state.last_event) || "经过起点领取 200，成为最后一位未破产的玩家。"; wrap.append(activity);
    const recent = c.state.last_card_events || [];
    if (recent.length) wrap.append(el("p", "monopoly-card-history monopoly-help", "最近抽卡 · " + recent.map(e => `${name(c, e.player_id)} · ${e.deck === "chance" ? "机会" : "公益"}：${e.text}（${e.summary}）`).join("；")));
    c.board.append(wrap);
    if (arrival) view.timer = window.setTimeout(() => {
      if (movementView !== view || !wrap.isConnected) return;
      wrap.querySelector(".is-arrival")?.classList.remove("is-arrival");
      const location = wrap.querySelector(".monopoly-location");
      if (location && viewer) location.textContent = positionCopy(c.state, viewer);
    }, Math.max(0, arrival.until - Date.now()));

  }
  function renderControls(c) {
    const wrap = el("div", "monopoly-controls");
    const panel = el("div", "monopoly-action-panel");
    const title = el("strong", "", phaseLabel(c)); panel.append(title);
    const row = el("div", "monopoly-action-strip"); row.setAttribute("role", "group"); row.setAttribute("aria-label", "回合与经营操作");
    if (c.state.phase === "purchase" && !tradeResponseDue(c)) {
      const p = players(c).find(p => p.player_id === c.state.current_player_id); const t = p && tiles(c).find(t => t.id === p.position);
      if (t) panel.append(el("p", "monopoly-context", `${t.name} · 售价 ${money(t.price)}，不购买则进入拍卖。`));
    }
    const self = players(c).find(p => p.player_id === (c.viewer || {}).player_id);
    if (self && !self.jailed && tiles(c).find(t => t.id === self.position)?.kind === "jail") panel.append(el("p", "monopoly-context", "你只是探访监狱，未入狱，可以正常行动。"));
    if (self && self.jailed && canAct(c) && c.state.phase === "roll" && !tradeResponseDue(c)) panel.append(el("p", "monopoly-context", `你在监狱，已尝试 ${self.jail_turns || 0} 轮。掷出双骰可出狱，或支付 50 保释金；第三次失败会强制缴费。`));
    if ((c.privateState || {}).jail_cards) panel.append(el("p", "monopoly-help", `你持有 ${c.privateState.jail_cards} 张出狱卡，只有真正入狱后，才能在自己的回合掷骰前使用。`));
    if (c.state.debt) panel.append(el("p", "monopoly-context", `待付 ${money(c.state.debt.amount)} 给${c.state.debt.creditor ? name(c, c.state.debt.creditor) : "银行"}。可出售建筑、抵押或交易筹款。`));
    if (c.state.auction) {
      const a = c.state.auction, t = tiles(c).find(t => t.id === a.tile_id);
      panel.append(el("p", "monopoly-context", `${t ? t.name : "地产"} · 最高出价 ${money(a.bid)}${a.highest_bidder ? "（" + name(c, a.highest_bidder) + "）" : ""}`));
      if (actions(c).some(a => a.action === "bid")) {
        const f = el("form", "monopoly-bid-form"); const label = el("label", "monopoly-field", "出价金额"); const input = el("input"); input.type = "number"; input.step = "1"; input.min = String(Number(a.bid || 0) + 1); input.value = input.min; input.required = true;
        const self = players(c).find(p => p.player_id === (c.viewer || {}).player_id); if (self) input.max = String(self.cash); input.disabled = !canAct(c); label.append(input);
        const bid = button("确认出价", () => {}, !canAct(c), true); bid.type = "submit"; f.append(label, bid);
        f.addEventListener("submit", async e => {e.preventDefault(); if (f.reportValidity()) await submit(c, {action: "bid", amount: Number(input.value)}, bid);}); row.append(f);
      }
    }
    if (pendingTrades(c.state).length) panel.append(pendingTradeList(c));
    else delete c.uiState.monopolyTrades;
    actions(c).filter(a => !ASSET_ACTIONS.includes(a.action) && !["bid", "propose_trade", "respond_trade"].includes(a.action)).forEach(a => row.append(actionButton(c, a)));
    if (!canAct(c) && !c.isTerminal && !tradeResponseDue(c)) panel.append(el("p", "monopoly-help", `等待${name(c, c.state.turn_player_id || c.state.current_player_id)}操作。`));
    row.append(button("地产与经营", () => assetsDialog(c)));
    if (((c.privateState || {}).trade_options || {}).partners?.length) row.append(button("提出交易", () => tradeDialog(c), !canAct(c)));
    panel.append(row, el("small", "monopoly-help", "局内现金仅用于本局经营")); wrap.append(panel); c.controls.append(wrap);
  }
  window.DuelGameUI.register("monopoly", {participantPresentation: "board-edge", usesEmbeddedActionFeedback: true, usesStandardMoveConfirmation: false, ownsPrivateStatePresentation: true, renderBoard, renderControls, transitionFeedback, monopolyTransitionBeats});
}());

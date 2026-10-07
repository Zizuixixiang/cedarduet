(function registerMonopolyPlusRenderer() {
  "use strict";
  // 大富翁·改：所有规则与合法动作都来自服务端投影；这里只负责呈现与提交。
  const GAME = "monopoly_plus";
  const PHASES = {setup: "选择版本", roll: "掷骰出发", purchase: "购入地产", manage: "经营时间", auction: "地产拍卖",
    debt: "偿还欠款", choice: "等待决定", finished: "本局结束"};
  const LABELS = {roll: "掷骰子", buy: "买下地产", auction: "不买 · 拍卖", end_turn: "结束回合", pay_bail: "交保释金出狱",
    use_jail_card: "使用出狱卡", bankrupt: "宣告破产", build: "建房升级", sell_building: "卖掉一级建筑", mortgage: "抵押",
    redeem: "赎回", pass_bid: "退出本次拍卖", skip_choice: "跳过", decline_nope: "接受", use_nope: "打出「不行」"};
  const KINDS = {start: "起点", chance: "机会", chest: "公益", event: "事件", tax: "税费", jail: "监狱", auction_house: "拍卖行",
    parking: "停车", go_to_jail: "入狱", railroad: "车站", utility: "设施", property: "地产"};
  const SPEED = {1: "1", 2: "2", 3: "3", bus: "巴士", mr: "富翁"};
  const BET = {big: ["大", "白骰和 ≥ 8", 100], small: ["小", "白骰和 ≤ 6", 100], seven: ["7", "白骰和 = 7", 200]};
  const ITEM_HELP = {
    swap: "用你的一块地强制换对手的一块地（成套、有房、抵押中的都不行）。",
    acquire: "刚付完租的这块地，按标价 2 倍强制买下（无建筑、不在对方成套组里）。",
    nope: "被强行交易、被收购或被罚款时打出，抵消这一次。",
  };
  const ASSET_ACTIONS = ["build", "sell_building", "mortgage", "redeem"];
  const PIPS = {1: [5], 2: [1, 9], 3: [1, 5, 9], 4: [1, 3, 7, 9], 5: [1, 3, 5, 7, 9], 6: [1, 3, 4, 6, 7, 9]};

  const el = (tag, cls, text) => {const e = document.createElement(tag); if (cls) e.className = cls; if (text !== undefined) e.textContent = text; return e;};
  const money = value => Number(value || 0).toLocaleString("zh-CN");
  const players = c => c.state.players || [];
  const tiles = c => c.state.tiles || [];
  const tileOf = (c, id) => tiles(c).find(t => t.id === id);
  const actions = c => Array.isArray(c.legalActions) ? c.legalActions : [];
  const canAct = c => c.canMove && !c.isTerminal && c.helpers.canMove();
  const viewerId = c => c.viewer && c.viewer.player_id;
  const participant = (c, id) => (c.participants || []).find(p => p.player_id === id);
  const name = (c, id) => {
    const p = participant(c, id) || players(c).find(p => p.player_id === id);
    return p && (p.display_name || p.name) || "玩家";
  };
  const seat = (c, id) => {
    const p = participant(c, id);
    return p && Number.isInteger(p.seat_index) ? p.seat_index : Math.max(0, players(c).findIndex(p => p.player_id === id));
  };
  // Pawn letter: the first character, or the last one when first letters clash
  // (e.g. 下棋助手3 / 下棋助手4 → 3 / 4). Shape and colour stay seat-specific.
  const initial = (c, id) => {
    const chars = n => Array.from(n.replace(/\s+/g, ""));
    const own = chars(name(c, id));
    const clash = players(c).some(p => p.player_id !== id && chars(name(c, p.player_id))[0] === own[0]);
    return (clash ? own.at(-1) : own[0]) || "?";
  };
  const pendingTrades = state => state.trades || (state.trade ? [state.trade] : []);
  const tileLabel = t => t ? t.name + (t.title ? `「${t.title}」` : "") : "地块";

  function ensureStyles() {
    if (document.getElementById("duel-game-monopoly-plus-styles")) return;
    const link = el("link"); link.id = "duel-game-monopoly-plus-styles"; link.rel = "stylesheet";
    link.href = "/static/games/monopoly_plus.css?v=3"; link.dataset.duelGameStyle = GAME; document.head.append(link);
  }
  function button(label, handler, {disabled = false, primary = false, compact = true} = {}) {
    const b = el("button", `pixel-btn mp-btn${primary ? " primary" : " secondary"}${compact ? " compact" : ""}`, label);
    b.type = "button"; b.disabled = disabled; b.addEventListener("click", handler); return b;
  }
  async function submit(c, action, source, side = false) {
    if ((!side && !canAct(c)) || c.uiState.mpSubmitting) return false;
    c.uiState.mpSubmitting = true;
    if (source) source.disabled = true;
    try {
      const payload = {...action, action_seq: c.state.action_seq};
      return side && c.helpers.submitSideMove ? await c.helpers.submitSideMove(payload) : await c.helpers.submitMove(payload);
    } catch (_) {
      c.helpers.announce("操作没有完成，请检查连接后重试。", {error: true}); return false;
    } finally {
      c.uiState.mpSubmitting = false;
      if (source && source.isConnected) source.disabled = !side && !canAct(c);
    }
  }
  function actionButton(c, action, label, primary) {
    let b;
    b = button(label || LABELS[action.action] || action.action, async () => {
      if (action.action === "bankrupt") return confirmDialog(c, "确认宣告破产", "破产后退出本局，剩余资产按规则交给债权人或银行（银行贷款优先）。可以先卖房、抵押或借款筹钱。", "确认破产", action);
      await submit(c, action, b);
    }, {disabled: !canAct(c), primary: primary ?? ["roll", "buy", "end_turn", "use_nope"].includes(action.action)});
    b.dataset.action = action.action; return b;
  }

  // ---------- pawns, dice, icons ----------
  function pawn(c, id, extra = "") {
    const n = seat(c, id);
    const wrap = el("span", `mp-pawn seat-${n}${extra}`);
    wrap.dataset.playerId = id; wrap.title = name(c, id);
    wrap.append(el("span", "mp-pawn-face", initial(c, id)));
    return wrap;
  }
  function die(value, cls = "") {
    const d = el("span", `mp-die${value ? "" : " empty"}${cls}`); d.setAttribute("aria-hidden", "true");
    for (let i = 1; i <= 9; i++) d.append(el("i", (PIPS[value] || []).includes(i) ? "on" : ""));
    return d;
  }
  function diceRow(c) {
    const values = c.state.dice || [];
    const row = el("div", "mp-dice");
    row.setAttribute("aria-label", values.length ? `骰子 ${values.join("、")}${c.state.speed ? "，速度骰 " + SPEED[c.state.speed] : ""}` : "尚未掷骰");
    (values.length ? values : [0, 0]).forEach(v => row.append(die(v)));
    if (c.state.speed !== null && c.state.speed !== undefined) {
      const sp = el("span", `mp-die speed${typeof c.state.speed === "number" ? "" : " word"}`);
      if (typeof c.state.speed === "number") for (let i = 1; i <= 9; i++) sp.append(el("i", (PIPS[c.state.speed] || []).includes(i) ? "on" : ""));
      else sp.append(el("b", "", SPEED[c.state.speed]));
      sp.title = "速度骰"; row.append(sp);
    }
    return row;
  }
  function tileIcon(kind, id) {
    const paths = {
      chance: "M7 6a4 4 0 0 1 8 0c0 3-4 3-4 6M11 16v1",
      chest: "M3 9h16v3H3zM5 12v7h12v-7M11 9v10M11 9C3 9 4 2 8 4zM11 9c8 0 7-7 3-5z",
      event: "M11 2l2.6 5.6 6 .7-4.5 4.1 1.3 6L11 15.5 5.6 18.4l1.3-6L2.4 8.3l6-.7z",
      jail: "M3 3h16v16H3zM7 3v16M12 3v16M16 3v16M2 7h18M2 15h18",
      go_to_jail: "M12 9V6a3 3 0 0 1 6 0v3M11 9h9v10h-9zM15 13v2M1 10h7M5 6l4 4-4 4",
      start: "M5 20V2M5 3h13l-3 4 3 4H5M2 20h8",
      auction_house: "M4 8l6-6 4 4-6 6zM9 7l9 9M14 20h7M2 20h9",
      parking: "M3 2h16v18H3zM8 16V6h4a3 3 0 0 1 0 6H8",
      tax: "M5 2h12v18l-3-2-3 2-3-2-3 2zM8 6h6M8 10h6M8 14h4",
      railroad: "M5 3h12v12H5zM5 9h12M8 4v5M14 4v5M7 15l-3 5M15 15l3 5M6 18h10",
      utility: id === 28 ? "M11 2c-2 5-7 8-7 12a7 7 0 0 0 14 0c0-4-5-7-7-12z" : "M12 2L4 12h6l-1 8 9-12h-6z",
    };
    if (!paths[kind]) return null;
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("viewBox", "0 0 22 22"); svg.setAttribute("class", "mp-tile-icon"); svg.setAttribute("aria-hidden", "true");
    const path = document.createElementNS(svg.namespaceURI, "path"); path.setAttribute("d", paths[kind]); svg.append(path); return svg;
  }
  function tilePosition(i) {
    if (i <= 10) return [11, 11 - i];
    if (i <= 20) return [21 - i, 1];
    if (i <= 30) return [1, i - 19];
    return [i - 29, 11];
  }

  // Inner track: pawns stand just inside the ring, on the side of their tile
  // that faces the centre, so a tile only ever shows its colour band and stamp.
  const SIDE = 74 / 9;
  function trackSpot(id) {
    const [row, col] = tilePosition(id);
    const mid = n => n === 1 ? 6.5 : n === 11 ? 93.5 : 13 + (n - 1.5) * SIDE;
    if (id === 0) return {x: 87, y: 87, edge: "corner-br"};
    if (id === 10) return {x: 13, y: 87, edge: "corner-bl"};
    if (id === 20) return {x: 13, y: 13, edge: "corner-tl"};
    if (id === 30) return {x: 87, y: 13, edge: "corner-tr"};
    if (row === 11) return {x: mid(col), y: 87, edge: "bottom"};
    if (row === 1) return {x: mid(col), y: 13, edge: "top"};
    if (col === 1) return {x: 13, y: mid(row), edge: "left"};
    return {x: 87, y: mid(row), edge: "right"};
  }
  function placeOnTrack(node, id) {
    const spot = trackSpot(id);
    node.style.left = `${spot.x}%`; node.style.top = `${spot.y}%`;
    node.dataset.edge = spot.edge;
    return spot;
  }

  // ---------- dialogs ----------
  function dialog(c, title) {
    const host = c.board;
    host.querySelectorAll("dialog.mp-dialog").forEach(d => {d.close(); d.remove();});
    const previousFocus = document.activeElement;
    const d = el("dialog", "mp-dialog");
    const head = el("header", "mp-dialog-head"); const h = el("h3", "", title); h.id = "mp-dialog-title";
    d.setAttribute("aria-labelledby", h.id);
    const close = button("关闭", () => d.close()); close.setAttribute("aria-label", "关闭对话框");
    head.append(h, close); d.append(head);
    const body = el("div", "mp-dialog-body"); d.append(body); host.append(d);
    d.addEventListener("close", () => {d.remove(); if (previousFocus && previousFocus.isConnected) previousFocus.focus();});
    d.addEventListener("click", e => {if (e.target === d) d.close();});
    d.showModal(); return {d, body};
  }
  function confirmDialog(c, title, copy, label, action) {
    const {d, body} = dialog(c, title);
    body.append(el("p", "", copy));
    const b = button(label, async () => {if (await submit(c, action, b)) d.close();}, {primary: true, disabled: !canAct(c)});
    body.append(b);
  }
  function tileDialog(c, t) {
    const {d, body} = dialog(c, tileLabel(t));
    const owner = t.owner ? name(c, t.owner) : "银行";
    const summary = el("p", "mp-detail-summary");
    if (t.owner) summary.append(pawn(c, t.owner, " small"));
    summary.append(el("span", "", `${KINDS[t.kind] || "地块"} · ${t.owner ? owner + "的地产" : t.price ? "无主" : "公共格"}${t.mortgaged ? " · 已抵押" : ""}${t.level ? ` · ${t.level === 5 ? "旅馆" : t.level + " 栋房"}` : ""}${t.closed ? " · 修路封闭中" : ""}`));
    if (t.kind === "property") {const g = el("span", "mp-color-chip"); g.style.setProperty("--land", t.color); summary.append(g);}
    body.append(summary);
    if (t.motto) body.append(el("blockquote", "mp-motto", `「${t.title}」${t.motto}`));
    if (t.price) {
      const dl = el("dl", "mp-values");
      const values = [["标价", money(t.price)]];
      if (t.sale_price !== undefined && t.sale_price !== t.price) values.push(["本轮售价", money(t.sale_price)]);
      values.push(["当前租金", t.owner ? money(t.rent) : "—"]);
      if (t.kind === "property") values.push(["每级建造", money(t.build_cost)]);
      values.push(["抵押所得", money(t.mortgage_value)], ["赎回花费", money(t.redemption_cost)]);
      values.forEach(([k, v]) => dl.append(el("dt", "", k), el("dd", "", v)));
      body.append(dl);
      if (t.kind === "property" && Array.isArray(t.rents)) {
        const table = el("table", "mp-rents"); const tb = el("tbody");
        t.rents.forEach((r, i) => {const tr = el("tr", i === (t.level || 0) && t.owner ? "current" : ""); tr.append(el("th", "", i === 0 ? "空地（成套翻倍）" : i === 5 ? "旅馆" : `${i} 栋房`), el("td", "", money(r))); tb.append(tr);});
        table.append(el("caption", "", "租金表"), tb); body.append(table);
      } else body.append(el("p", "mp-help", t.kind === "railroad" ? "持有 1/2/3/4 座车站：租金 25/50/100/200。" : "持有一处设施租金为骰点×4，两处×10。"));
    } else {
      const copy = {start: "经过或停在起点领 200（有贷款时先扣利息）。", jail: "经过只是探监。牢里同时只关一个人。", chance: "抽一张机会牌，里面混有道具卡。",
        chest: "抽一张公益牌，里面混有道具卡。", event: "抽一张事件卡：全桌事件、地产事件、温馨事件或聊天卡。", tax: "停在这里缴税。",
        auction_house: "停在这里的人挑一块无主地开拍，所有人都能出价。", go_to_jail: "直接入狱，不领起点奖励。"};
      body.append(el("p", "", copy[t.kind] || "由银行自动结算。"));
    }
    const row = el("div", "mp-actions");
    actions(c).filter(a => ASSET_ACTIONS.includes(a.action) && a.tile_id === t.id).forEach(a => row.append(actionButton(c, a)));
    if (row.childElementCount) body.append(row);
    if (t.owner && t.owner === viewerId(c) && c.state.rules?.naming && canAct(c) && c.state.turn_player_id === c.state.current_player_id
        && ["roll", "purchase", "manage"].includes(c.state.phase)) body.append(nameForm(c, t, d));
  }
  function nameForm(c, t, d) {
    const form = el("form", "mp-form");
    form.append(el("strong", "", "挂地名牌"), el("p", "mp-help", "别人踩到交租时会看到。换主人后牌子自动摘下。"));
    const nameLabel = el("label", "mp-field", "地名（最多 8 字）"); const nameInput = el("input"); nameInput.maxLength = 8; nameInput.required = true; nameInput.value = t.title || ""; nameLabel.append(nameInput);
    const mottoLabel = el("label", "mp-field", "一句话（最多 30 字，可空）"); const motto = el("input"); motto.maxLength = 30; motto.value = t.motto || ""; mottoLabel.append(motto);
    const error = el("p", "mp-error"); error.setAttribute("role", "alert");
    const send = button("挂牌", () => {}, {primary: true}); send.type = "submit";
    form.append(nameLabel, mottoLabel, error, send);
    form.addEventListener("submit", async e => {
      e.preventDefault();
      if (await submit(c, {action: "name_tile", tile_id: t.id, name: nameInput.value.trim(), motto: motto.value.trim()}, send)) d.close();
      else error.textContent = "没挂上：可能字数超了，或者有链接/不太友好的词。";
    });
    return form;
  }
  function playerDialog(c, id) {
    const p = players(c).find(p => p.player_id === id); if (!p) return;
    const {body} = dialog(c, name(c, id));
    const head = el("div", "mp-player-head"); head.append(pawn(c, id, " big"));
    const swatch = el("span", `mp-stamp mp-owner-swatch seat-${seat(c, id)}`, initial(c, id)); swatch.title = "名下地产的格子里印这枚章";
    head.append(el("p", "", `${p.bankrupt ? "已破产" : p.jailed ? "在押" : "在场"} · 产权章：`), swatch);
    body.append(head);
    const dl = el("dl", "mp-values");
    [["现金", money(p.cash)], ["总资产", money(p.asset_value)], ["道具卡", `${p.items || 0} 张`], ["巴士票", `${p.bus || 0} 张`],
      ["贷款", p.loan ? `${money(p.loan.balance)}（还剩 ${p.loan.laps_left} 圈到期）` : "无"], ["速度骰", p.passed_go ? "已启用" : "首次过起点后启用"]]
      .forEach(([k, v]) => dl.append(el("dt", "", k), el("dd", "", v)));
    body.append(dl);
    const list = el("div", "mp-asset-list");
    tiles(c).filter(t => t.owner === id).forEach(t => {
      const b = button("", () => tileDialog(c, t)); b.classList.add("mp-asset"); b.style.setProperty("--land", t.color || "#9b8db5");
      b.append(el("span", "", tileLabel(t)), el("small", "", t.mortgaged ? "已抵押" : t.level ? (t.level === 5 ? "旅馆" : `${t.level} 栋房`) : "空地"));
      list.append(b);
    });
    if (!list.childElementCount) list.append(el("p", "mp-help", "暂无地产。"));
    body.append(list);
  }

  // ---------- first-visit tutorial ----------
  // Tutorial state belongs to a page visit, not uiState (which resets every revision).
  // The first target of a step is the one its text describes; fallbackText covers the rest.
  const TUTORIAL_KEY = "cedarduet.monopoly_plus.tutorial.v1";
  const TUTORIAL_STEPS = [
    {title: "欢迎来到大富翁·改", targets: [".mp-center", ".mp-ring"],
      text: "这是经典大富翁的加强版：买地、收租、盖房，让对手破产；还多了速度骰、道具卡、事件卡这些新花样。教程只介绍，不替你出手。"},
    {title: "现在轮到谁", targets: [".mp-turn-banner"],
      text: "最上面这条横幅告诉你现在轮到谁、在做什么。写着“轮到你了”、还闪着金边的时候，就该你动手啦。"},
    {title: "看懂棋盘", targets: [".mp-ring"],
      text: "格子里的小圆章＝这块地是谁的；格子外面带尖角的实心棋子＝人现在站在哪。点任意一格，能看标价和租金表。"},
    {title: "掷骰区", targets: [".mp-dice", ".mp-center"],
      text: "两颗白骰决定走几步。第一次经过起点后，每次会多掷一颗速度骰：1/2/3 加到步数里，“巴士”送你一张巴士票，“大富翁先生”带你去下一块无主地。"},
    {title: "我的手牌", targets: [".mp-hand"],
      text: "道具卡只能从机会、公益牌堆里抽到：强行交易、收购，还有专门抵消它们的「不行」。手牌最多 3 张，别人只看得到张数。",
      fallbackText: "开局后，下方会出现“我的手牌”。道具卡只能从机会、公益牌堆里抽到：强行交易、收购，还有专门抵消它们的「不行」。"},
    {title: "事件和聊天卡", targets: [".mp-choice", ".mp-event-bar"],
      text: "踩到事件格会抽一张事件卡。聊天卡要你讲一段话，其他人投赞成或反对，通过了才拿奖励；超时按不通过。",
      fallbackText: "踩到事件格会抽一张事件卡，结果会写在这条消息栏里。聊天卡要你讲一段话，其他人投赞成或反对，通过了才拿奖励。"},
    {title: "银行借贷", targets: [".mp-loan"],
      text: "缺钱可以向银行借：额度是名下未抵押地产标价的一半，最多 1000。每过一次起点扣 10% 利息，第 3 次过起点要还清。",
      fallbackText: "开局后，手牌下面会显示可借额度。额度是名下未抵押地产标价的一半，最多 1000；每过一次起点扣 10% 利息，第 3 次过起点要还清。"},
    {title: "观战押注", targets: [".mp-bets"],
      text: "没轮到你的时候，可以在别人掷骰前押一把：大（≥8）、小（≤6）或 7，押金 50。押中大小拿回 100，押中 7 拿回 200。",
      fallbackText: "别人掷骰之前，下方会出现押注区：没轮到你也能押大（≥8）、小（≤6）或 7，押金 50。押中大小拿回 100，押中 7 拿回 200。"},
    {title: "地产与经营", targets: [".mp-controls .mp-actions.tools"],
      text: "点“地产与经营”看看名下的地：凑齐一组同色地就能建房，缺钱可以抵押；在自己回合点自己的地，还能起个名字、挂一块小牌子。",
      fallbackText: "开局后，下方会有“地产与经营”按钮：凑齐一组同色地就能建房，缺钱可以抵押；在自己回合点自己的地，还能起名字、挂牌子。"},
  ];
  const SITE_MODALS = ".wait-mode-modal-backdrop:not(.hidden), .result-modal-backdrop:not(.hidden)";
  let tutorialContext = null;
  let tutorialVisit = null;
  let tutorialOverlay = null;
  let tutorialFrame = 0;
  let tutorialObserver = null;

  function tutorialRemembered() {
    try { return ["completed", "dismissed"].includes(window.localStorage.getItem(TUTORIAL_KEY)); }
    catch (_) { return false; }
  }

  function closeTutorial(remember, restore = true) {
    const tour = tutorialOverlay;
    if (!tour) return;
    tutorialOverlay = null;
    if (remember) {
      try { window.localStorage.setItem(TUTORIAL_KEY, remember); }
      catch (_) { /* Storage may be unavailable; this visit still stays dismissed. */ }
    }
    tour.resizeObserver?.disconnect();
    tour.dialog.close();
    tour.dialog.remove();
    if (restore) {
      window.scrollTo({left: tour.scrollX, top: tour.scrollY, behavior: "instant"});
      if (tour.focus?.isConnected) tour.focus.focus({preventScroll: true});
    }
  }

  function tutorialTarget(step) {
    const context = tutorialContext;
    for (const selector of step.targets) {
      const node = context.board.querySelector(selector) || context.controls.querySelector(selector);
      if (node && node.getClientRects().length) return node;
    }
    return null;
  }

  function positionTutorial(scrollToTarget = false) {
    const tour = tutorialOverlay;
    if (!tour) return;
    const {dialog, panel, spotlight} = tour;
    const width = dialog.clientWidth, height = dialog.clientHeight;
    const margin = 12, gap = 14;
    panel.style.left = `${Math.max(margin, (width - panel.offsetWidth) / 2)}px`;
    const step = TUTORIAL_STEPS[tour.step];
    const target = step && tutorialTarget(step);
    const text = step && (step.fallbackText && !target?.matches(step.targets[0]) ? step.fallbackText : step.text);
    if (text && tour.copy.textContent !== text) tour.copy.textContent = text;
    const panelHeight = panel.getBoundingClientRect().height;
    if (!target) {
      spotlight.hidden = true;
      dialog.classList.remove("has-target");
      panel.style.top = `${Math.max(margin, (height - panelHeight) / 2)}px`;
      return;
    }
    if (scrollToTarget) target.scrollIntoView({block: "center", inline: "nearest", behavior: "instant"});
    let rect = target.getBoundingClientRect();
    // Make room for the explanation without changing the game's document layout.
    if (scrollToTarget && rect.bottom + gap + panelHeight > height - margin
        && rect.top - gap - panelHeight < margin) {
      window.scrollBy({top: rect.top - margin - 4, behavior: "instant"});
      rect = target.getBoundingClientRect();
    }
    const below = rect.bottom + gap;
    const above = rect.top - gap - panelHeight;
    const top = below + panelHeight <= height - margin ? below
      : above >= margin ? above : height - margin - panelHeight;
    panel.style.top = `${Math.max(margin, top)}px`;
    const left = Math.max(2, rect.left - 4), right = Math.min(width - 2, rect.right + 4);
    const targetTop = Math.max(2, rect.top - 4), bottom = Math.min(height - 2, rect.bottom + 4);
    spotlight.hidden = false;
    dialog.classList.add("has-target");
    Object.assign(spotlight.style, {left: `${left}px`, top: `${targetTop}px`,
      width: `${Math.max(0, right - left)}px`, height: `${Math.max(0, bottom - targetTop)}px`});
  }

  function showTutorialStep(index) {
    const tour = tutorialOverlay;
    tour.step = index;
    tour.dialog.dataset.step = String(index + 1);
    tour.title.textContent = TUTORIAL_STEPS[index].title;
    tour.progress.textContent = `${index + 1} / ${TUTORIAL_STEPS.length}`;
    tour.copy.textContent = TUTORIAL_STEPS[index].text;
    tour.previous.hidden = false;
    tour.previous.disabled = index === 0;
    tour.dismiss.hidden = true;
    tour.next.textContent = index === TUTORIAL_STEPS.length - 1 ? "完成" : "下一步";
    positionTutorial(true);
    tour.next.focus({preventScroll: true});
  }

  function openTutorial(startAt = -1) {
    const dialog = el("dialog", "mp-tutorial");
    dialog.setAttribute("aria-labelledby", "mp-tutorial-title");
    dialog.setAttribute("aria-describedby", "mp-tutorial-copy");
    const spotlight = el("div", "mp-tutorial-spotlight");
    spotlight.setAttribute("aria-hidden", "true");
    spotlight.hidden = true;
    const panel = el("section", "mp-tutorial-panel");
    const head = el("div", "mp-tutorial-head");
    const title = el("h2", "", "第一次来大富翁·改？");
    title.id = "mp-tutorial-title";
    const close = button("×", () => closeTutorial());
    close.classList.add("mp-tutorial-close");
    close.setAttribute("aria-label", "关闭本次教程");
    const progress = el("span", "mp-tutorial-progress", `${TUTORIAL_STEPS.length} 步认识牌桌`);
    const copy = el("p", "mp-tutorial-copy", "要看看怎么玩吗？跟着高亮认识牌桌，教程只介绍、不替你出手。这次先关掉也没关系，下次进入还会提示；“规则”旁边的“教程”随时能再打开。");
    copy.id = "mp-tutorial-copy";
    copy.setAttribute("aria-live", "polite");
    const actionsRow = el("div", "mp-actions");
    const previous = button("上一步", () => showTutorialStep(tutorialOverlay.step - 1));
    previous.hidden = true;
    const next = button("查看教程", () => {
      const index = tutorialOverlay.step;
      if (index === TUTORIAL_STEPS.length - 1) closeTutorial("completed");
      else showTutorialStep(index + 1);
    }, {primary: true});
    const dismiss = button("不再提示", () => closeTutorial("dismissed"));
    dismiss.classList.add("mp-tutorial-dismiss");
    head.append(title, close);
    actionsRow.append(previous, next, dismiss);
    panel.append(head, progress, copy, actionsRow);
    dialog.append(spotlight, panel);
    tutorialOverlay = {dialog, panel, spotlight, title, progress, copy, previous, next, dismiss, step: -1,
      scrollX: window.scrollX, scrollY: window.scrollY, focus: document.activeElement};
    dialog.addEventListener("cancel", event => { event.preventDefault(); closeTutorial(); });
    dialog.addEventListener("keydown", event => {
      if (event.key !== "Tab") return;
      const buttons = [...dialog.querySelectorAll("button")].filter(node => !node.disabled && node.getClientRects().length);
      const first = buttons[0], last = buttons[buttons.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault(); last.focus({preventScroll: true});
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault(); first.focus({preventScroll: true});
      }
    });
    document.body.appendChild(dialog);
    dialog.showModal();
    if (window.ResizeObserver) {
      tutorialOverlay.resizeObserver = new window.ResizeObserver(() => scheduleTutorial());
      [tutorialContext.board, tutorialContext.controls, panel].forEach(node => tutorialOverlay.resizeObserver.observe(node));
    }
    if (startAt >= 0) return showTutorialStep(startAt);
    positionTutorial();
    next.focus({preventScroll: true});
  }

  // A "教程" entry beside the shared 规则 button; it lives only while this game is on screen.
  function syncTutorialEntry(visible) {
    const existing = document.getElementById("mpTutorialButton");
    const rules = document.getElementById("rulesButton");
    if (!visible || !rules) { existing?.remove(); return; }
    if (existing && existing.previousElementSibling === rules) return;
    existing?.remove();
    const entry = el("button", "pixel-btn secondary compact mp-tutorial-entry", "教程");
    entry.type = "button"; entry.id = "mpTutorialButton";
    entry.setAttribute("aria-label", "重新打开新手教程");
    entry.addEventListener("click", () => {
      if (!tutorialContext) return;
      closeTutorial(null, false);
      openTutorial(0);
    });
    rules.after(entry);
  }

  function scheduleTutorial() {
    if (tutorialFrame) return;
    tutorialFrame = window.requestAnimationFrame(() => {
      tutorialFrame = 0;
      const context = tutorialContext;
      if (!context) return;
      const visible = context.board.isConnected && context.board.getClientRects().length
        && context.board.querySelector(".mp-game");
      syncTutorialEntry(Boolean(visible));
      // Site-level prompts (挂等提示、结算) come first; the tour waits until they close.
      if (!visible || document.querySelector(SITE_MODALS)) {
        closeTutorial(null, false);
        tutorialVisit = null;
        return;
      }
      const key = `${context.room.room_id || ""}:${viewerId(context) || "spectator"}`;
      if (!tutorialVisit || tutorialVisit.key !== key || tutorialVisit.board !== context.board) {
        closeTutorial(null, false);
        tutorialVisit = {key, board: context.board};
        if (!tutorialRemembered()) openTutorial();
      } else {
        positionTutorial();
      }
    });
  }

  function syncTutorial(context) {
    // Modal positioning requires a mounted browser document.
    if (!window.MutationObserver || !window.requestAnimationFrame || !document.body) return;
    tutorialContext = context;
    if (!tutorialObserver) {
      tutorialObserver = new window.MutationObserver(records => {
        if (records.some(record => !record.target.closest?.(".mp-tutorial, #mpTutorialButton"))) scheduleTutorial();
      });
      tutorialObserver.observe(document.body, {
        subtree: true, childList: true, attributes: true, attributeFilter: ["class", "hidden"],
      });
      window.addEventListener("scroll", () => { if (tutorialOverlay) scheduleTutorial(); }, true);
      window.addEventListener("resize", () => { if (tutorialOverlay) scheduleTutorial(); });
      window.visualViewport?.addEventListener("resize", () => { if (tutorialOverlay) scheduleTutorial(); });
    }
    scheduleTutorial();
  }

  // ---------- board ----------
  let motion = null; // {key, arrival: {tiles, until}}
  const motionKey = c => JSON.stringify([c.room.room_id, viewerId(c) || "spectator"]);

  function turnBanner(c) {
    const s = c.state;
    const banner = el("div", "mp-turn-banner");
    if (c.isTerminal || s.phase === "finished") {
      banner.classList.add("finished"); banner.append(el("strong", "", "本局结束")); return banner;
    }
    const current = s.current_player_id, decider = s.turn_player_id || current;
    const mine = decider === viewerId(c);
    banner.classList.add(`seat-${seat(c, decider)}`); banner.classList.toggle("mine", mine);
    banner.append(pawn(c, decider, " banner"));
    const copy = el("div", "mp-turn-copy");
    let headline = mine ? "轮到你了" : `轮到 ${name(c, decider)}`;
    if (s.phase === "choice" && s.choice) {
      const kind = s.choice.kind;
      headline = mine ? {vote: "请你投票", nope_attack: "有人对你出手了", nope_fine: "要不要打出「不行」"}[kind === "chat" && s.choice.stage === "vote" ? "vote" : kind] || "请你做个决定"
        : `等 ${name(c, decider)} ${kind === "chat" && s.choice.stage === "vote" ? "投票" : "做决定"}`;
    } else if (s.phase === "auction") headline = mine ? "轮到你出价" : `${name(c, decider)} 正在出价`;
    else if (s.phase === "setup") headline = mine ? "请你选择版本" : `等 ${name(c, decider)} 选择版本`;
    copy.append(el("strong", "", headline));
    copy.append(el("small", "", `第 ${s.turn_number || 1} 回合 · ${name(c, current)}的回合 · ${PHASES[s.phase] || ""}`));
    banner.append(copy);
    if (s.bet_window && Object.keys(s.bets || {}).length) {
      const bets = el("span", "mp-bet-tags");
      Object.entries(s.bets).forEach(([pid, choice]) => bets.append(el("span", `mp-bet-tag seat-${seat(c, pid)}`, `${initial(c, pid)}押${BET[choice][0]}`)));
      banner.append(bets);
    }
    return banner;
  }

  function seatCards(c) {
    const row = el("div", `mp-seats n${players(c).length}`);
    players(c).forEach(p => {
      const current = !c.isTerminal && p.player_id === c.state.current_player_id;
      const deciding = !c.isTerminal && p.player_id === c.state.turn_player_id;
      const card = el("button", `mp-seat seat-${seat(c, p.player_id)}${current ? " current" : ""}${deciding ? " deciding" : ""}${p.bankrupt ? " bankrupt" : ""}${p.player_id === viewerId(c) ? " viewer" : ""}`);
      card.type = "button"; card.setAttribute("aria-current", String(current));
      card.setAttribute("aria-label", `${name(c, p.player_id)}${p.player_id === viewerId(c) ? "（你）" : ""}，现金 ${p.cash}，查看详情`);
      card.addEventListener("click", () => playerDialog(c, p.player_id));
      const avatar = el("span", "board-edge-avatar"); c.helpers.renderParticipantAvatar(avatar, participant(c, p.player_id) || p);
      const id = el("span", "mp-seat-id"); id.append(avatar, pawn(c, p.player_id, " badge"));
      const copy = el("span", "mp-seat-copy");
      copy.append(el("strong", "", `${name(c, p.player_id)}${p.player_id === viewerId(c) ? "（你）" : ""}`));
      copy.append(el("span", "mp-cash", p.bankrupt ? "已破产" : `¥${money(p.cash)}`));
      const tags = el("span", "mp-seat-tags");
      tags.append(el("small", "", `${p.property_count || 0}地`));
      if (p.items) tags.append(el("small", "tag-item", `道具${p.items}`));
      if (p.bus) tags.append(el("small", "tag-bus", `巴士${p.bus}`));
      if (p.loan) tags.append(el("small", "tag-loan", `贷${money(p.loan.balance)}`));
      if (p.jailed) tags.append(el("small", "tag-jail", "在押"));
      copy.append(tags);
      if (current) card.append(el("span", "mp-current-mark", "▶"));
      card.append(id, copy); row.append(card);
    });
    return row;
  }

  function renderBoard(c) {
    ensureStyles(); c.board.classList.add(GAME);
    const wrap = el("div", "mp-game");
    wrap.dataset.revision = c.room.revision; wrap.dataset.motionKey = motionKey(c);
    if (motion?.key !== motionKey(c)) motion = {key: motionKey(c), arrival: null};
    const arrival = motion.arrival && motion.arrival.until > Date.now() ? motion.arrival : null;
    wrap.append(turnBanner(c), seatCards(c));
    const ring = el("div", "mp-ring"); ring.setAttribute("aria-label", "40 格棋盘，点地块看详情");
    const center = el("div", "mp-center");
    center.append(el("span", "mp-center-kicker", c.state.edition_name ? `${c.state.edition_name} 版` : "城市经营"), el("strong", "mp-center-title", "大富翁·改"), diceRow(c));
    const recent = (c.state.log || []).filter(e => e.seq >= (c.state.action_seq || 0) - 2).slice(-3);
    const feed = el("ol", "mp-center-feed"); feed.setAttribute("aria-label", "最近发生");
    recent.forEach(e => {const li = el("li", `kind-${e.kind}`); if (e.player) li.append(pawn(c, e.player, " tiny")); li.append(el("span", "", e.text)); if (e.motto) li.append(el("em", "", `牌子：${e.motto}`)); feed.append(li);});
    center.append(feed);
    if ((c.state.modifiers || []).length) {
      const mods = el("div", "mp-mods");
      c.state.modifiers.forEach(m => mods.append(el("span", "mp-mod", m.text)));
      center.append(mods);
    }
    ring.append(center);
    const legal = actions(c);
    const destinations = new Set(legal.filter(a => ["choose_destination", "use_bus", "pick_auction"].includes(a.action)).map(a => a.tile_id));
    const track = el("div", "mp-track"); track.setAttribute("aria-hidden", "true");
    tiles(c).forEach(t => {
      const [row, col] = tilePosition(t.id);
      const edge = row === 11 ? "bottom" : row === 1 ? "top" : col === 1 ? "left" : "right";
      const ownerSeat = t.owner ? seat(c, t.owner) : -1;
      const b = el("button", `mp-tile edge-${edge} kind-${t.kind}${t.id % 10 === 0 ? " corner" : ""}${t.mortgaged ? " mortgaged" : ""}${t.closed ? " closed" : ""}`);
      b.type = "button"; b.style.gridRow = String(row); b.style.gridColumn = String(col); b.dataset.tileId = t.id;
      b.style.setProperty("--land", t.color || "#d8cbdc");
      if (arrival && arrival.tiles.includes(t.id)) b.classList.add("is-arrival");
      if (destinations.has(t.id) && canAct(c)) b.classList.add("is-target");
      const occupants = players(c).filter(p => !p.bankrupt && p.position === t.id);
      b.setAttribute("aria-label", `${tileLabel(t)}，${t.owner ? name(c, t.owner) + "的地产" : KINDS[t.kind] || "地块"}${t.level ? `，${t.level === 5 ? "旅馆" : t.level + "栋房"}` : ""}${occupants.length ? "，" + occupants.map(p => name(c, p.player_id)).join("、") + "在这里" : ""}`);
      if (t.kind === "property") b.append(el("span", "mp-band"));
      const label = el("span", "mp-tile-label");
      const icon = tileIcon(t.kind, t.id); if (icon) label.append(icon);
      const nm = el("span", "mp-tile-name");
      if (t.kind === "property" && (edge === "top" || edge === "bottom")) Array.from(t.name).forEach(ch => nm.append(el("span", "", ch)));
      else nm.textContent = t.name;
      label.append(nm); b.append(label);
      if (t.level) b.append(el("span", `mp-houses${t.level === 5 ? " hotel" : ""}`, t.level === 5 ? "H" : "▪".repeat(t.level)));
      // Ownership: a small white stamp with a thin seat-colour ring.
      if (ownerSeat >= 0) {
        const stamp = el("span", `mp-stamp seat-${ownerSeat}${t.title ? " signed" : ""}`, initial(c, t.owner));
        stamp.title = `产权：${name(c, t.owner)}${t.title ? `（挂牌「${t.title}」）` : ""}`; b.append(stamp);
      }
      if (occupants.length) {
        const group = el("span", `mp-spot n${Math.min(occupants.length, 4)}`); group.dataset.trackTile = t.id;
        placeOnTrack(group, t.id);
        occupants.forEach((p, i) => {
          const token = pawn(c, p.player_id, ` on-track${p.player_id === viewerId(c) ? " is-viewer" : ""}${p.player_id === c.state.current_player_id ? " is-current" : ""}`);
          token.style.setProperty("--i", i); group.append(token);
        });
        track.append(group);
      }
      b.addEventListener("click", () => {
        const pick = legal.find(a => ["choose_destination", "use_bus", "pick_auction"].includes(a.action) && a.tile_id === t.id);
        if (pick && canAct(c)) return confirmDialog(c, {choose_destination: "三同：前往这里？", use_bus: "坐巴士去这里？", pick_auction: "拍卖这块地？"}[pick.action],
          `${tileLabel(t)}${t.price ? ` · 标价 ${money(t.price)}` : ""}`, "确定", pick);
        tileDialog(c, t);
      });
      ring.append(b);
    });
    ring.append(track);
    wrap.append(ring);
    const bar = el("div", "mp-event-bar"); bar.setAttribute("role", "status"); bar.setAttribute("aria-live", "polite");
    const latest = (c.state.log || []).slice(-1)[0];
    if (latest) {if (latest.player) bar.append(pawn(c, latest.player, " tiny")); bar.append(el("span", "", latest.text + (latest.motto ? ` —— 牌子：「${latest.motto}」` : "")));}
    else bar.append(el("span", "", "经过起点领 200，成为最后一位没有破产的玩家。"));
    wrap.append(bar);
    const history = el("details", "mp-history");
    history.append(el("summary", "", "本局记录"));
    const list = el("ol", "");
    (c.state.log || []).slice(-14).reverse().forEach(e => {const li = el("li", `kind-${e.kind}`); if (e.player) li.append(pawn(c, e.player, " tiny")); li.append(el("span", "", e.text)); list.append(li);});
    history.append(list); wrap.append(history);
    c.board.append(wrap);
    if (arrival) window.setTimeout(() => wrap.querySelectorAll(".is-arrival").forEach(t => t.classList.remove("is-arrival")), Math.max(0, arrival.until - Date.now()));
    syncTutorial(c);
  }

  // ---------- movement ----------
  async function transitionFeedback({previousRoom, nextRoom, document: doc = window.document} = {}) {
    if (!previousRoom || !nextRoom || previousRoom.game_type !== GAME || nextRoom.game_type !== GAME
      || previousRoom.room_id !== nextRoom.room_id || previousRoom.revision === nextRoom.revision) return;
    const before = previousRoom.board_state || {}, after = nextRoom.board_state || {};
    if (!(after.action_seq > before.action_seq)) return;
    const fresh = (after.log || []).filter(e => e.seq > before.action_seq && e.seq <= after.action_seq);
    const moves = fresh.filter(e => e.move && e.player).slice(-6);
    const landing = fresh.flatMap(e => e.tiles || []).filter(Number.isInteger);
    const wrap = doc.querySelector(".mp-game"), ring = wrap?.querySelector(".mp-ring");
    const key = JSON.stringify([nextRoom.room_id, nextRoom.viewer?.player_id || "spectator"]);
    if (!wrap || !ring || wrap.dataset.revision !== String(previousRoom.revision) || wrap.dataset.motionKey !== key) return;
    if (motion?.key !== key) motion = {key, arrival: null};
    const alive = () => wrap.isConnected && !wrap.closest(".hidden");
    const reduced = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    const wait = ms => new Promise(r => window.setTimeout(r, ms));
    const controls = doc.querySelector(".mp-controls"), bar = wrap.querySelector(".mp-event-bar");
    const inert = controls?.inert;
    try {
      if (controls) controls.inert = true;
      for (const entry of moves) {
        if (!alive() || reduced) break;
        const track = ring.querySelector(".mp-track");
        const source = track?.querySelector(`.mp-pawn[data-player-id="${CSS.escape(entry.player)}"]`);
        if (!source) continue;
        if (bar) {bar.replaceChildren(pawn({participants: nextRoom.participants, state: after}, entry.player, " tiny"), el("span", "", entry.text)); bar.classList.add("live");}
        const ghost = el("span", "mp-spot mp-ghost n1"); ghost.append(source.cloneNode(true)); track.append(ghost);
        source.classList.add("is-hidden");
        const {frm, to, steps} = entry.move;
        const route = steps > 0 ? Array.from({length: steps}, (_, i) => (frm + i + 1) % 40)
          : steps < 0 ? Array.from({length: -steps}, (_, i) => (frm - i - 1 + 40) % 40) : [to];
        placeOnTrack(ghost, frm); ghost.getBoundingClientRect(); ghost.classList.add(steps ? "walking" : "jumping");
        const pace = route.length > 12 ? 80 : 130;
        let passing = null;
        for (const id of route) {
          if (!alive()) break;
          passing?.classList.remove("is-passing");
          placeOnTrack(ghost, id);
          passing = ring.querySelector(`[data-tile-id="${id}"]`); passing?.classList.add("is-passing");
          await wait(steps ? pace : 260);
        }
        passing?.classList.remove("is-passing");
        // Leave the pawn on its new spot for the rest of this transition.
        let group = track.querySelector(`[data-track-tile="${to}"]`);
        if (!group) {group = el("span", "mp-spot n1"); group.dataset.trackTile = to; placeOnTrack(group, to); track.append(group);}
        source.style.setProperty("--i", group.children.length); group.append(source); source.classList.remove("is-hidden"); ghost.remove();
        ring.querySelector(`[data-tile-id="${to}"]`)?.classList.add("is-arrival");
        await wait(220);
      }
    } finally {
      if (controls) controls.inert = inert;
      bar?.classList.remove("live");
    }
    motion.arrival = {tiles: [...new Set(landing)], until: Date.now() + 1800};
  }

  // ---------- controls ----------
  function setupPanel(c, panel) {
    const grid = el("div", "mp-editions");
    const available = new Set(actions(c).filter(a => a.action === "choose_edition").map(a => a.edition));
    (c.state.editions || []).forEach(e => {
      const card = el("button", `mp-edition${e.available ? "" : " soon"}`);
      card.type = "button"; card.disabled = !e.available || !available.has(e.id) || !canAct(c);
      card.append(el("strong", "", e.name), el("small", "", e.available ? e.tagline : "敬请期待"));
      if (!e.available) card.append(el("span", "mp-soon", e.tagline.replace(/^敬请期待：?/, "")));
      card.addEventListener("click", () => submit(c, {action: "choose_edition", edition: e.id}, card));
      grid.append(card);
    });
    panel.append(el("p", "mp-context", canAct(c) ? "选一个版本开局。以后会有更多地图和卡池。" : `等 ${name(c, c.state.turn_player_id)} 选择版本。`), grid);
  }
  function choicePanel(c, panel) {
    const ch = c.state.choice; if (!ch) return;
    const mine = canAct(c) && c.state.turn_player_id === viewerId(c);
    const box = el("section", `mp-choice kind-${ch.kind}`);
    const row = el("div", "mp-actions");
    const legal = actions(c);
    if (ch.kind === "triple") {
      box.append(el("strong", "", "三同！任选落点"), el("p", "", mine ? "点棋盘上任意一格作为落点（高亮的格子都可以）。" : `${name(c, ch.player_id)} 正在挑落点。`));
    } else if (ch.kind === "pick_auction") {
      box.append(el("strong", "", "拍卖行"), el("p", "", mine ? "挑一块无主地开拍，所有人都能出价。可以点棋盘，也可以在下面选。" : `${name(c, ch.player_id)} 正在挑拍卖的地。`));
      if (mine) {
        const sel = el("select", "mp-select"); legal.filter(a => a.action === "pick_auction").forEach(a => {const t = tileOf(c, a.tile_id); const o = el("option", "", `${t.name} · 标价 ${money(t.price)}`); o.value = a.tile_id; sel.append(o);});
        const go = button("开拍", () => submit(c, {action: "pick_auction", tile_id: Number(sel.value)}, go), {primary: true});
        row.append(sel, go);
      }
    } else if (ch.kind === "gift") {
      box.append(el("strong", "", "🧧 红包"), el("p", "", mine ? "给你最喜欢的人发一个红包，金额 50~200。" : `${name(c, ch.player_id)} 正在挑人发红包。`));
      if (mine && legal.some(a => a.action === "give_gift")) {
        const others = [...new Set(legal.filter(a => a.action === "give_gift").map(a => a.to))];
        let to = others[0];
        const tabs = el("div", "mp-tabs");
        others.forEach(id => {const t = button("", () => {to = id; [...tabs.children].forEach(x => x.setAttribute("aria-pressed", String(x === t)));}); t.append(pawn(c, id, " tiny"), el("span", "", name(c, id))); t.setAttribute("aria-pressed", String(id === to)); tabs.append(t);});
        const amount = el("input"); amount.type = "range"; amount.min = "50"; amount.max = String(Math.min(200, ch.max || 200)); amount.step = "10"; amount.value = "100";
        const shown = el("b", "mp-amount", "¥100"); amount.addEventListener("input", () => {shown.textContent = `¥${amount.value}`;});
        const send = button("发红包", () => submit(c, {action: "give_gift", to, amount: Number(amount.value)}, send), {primary: true});
        box.append(tabs); row.append(amount, shown, send);
      }
    } else if (ch.kind === "chat") {
      box.append(el("strong", "", `💬 聊天卡 · ${ch.card}`), el("p", "mp-prompt", ch.prompt), el("small", "mp-help", `${ch.reward}。其他人投票，超时按不通过。`));
      if (ch.stage === "perform") {
        if (mine) {
          const text = el("textarea", "mp-textarea"); text.maxLength = 150; text.rows = 3; text.placeholder = "在这里说出来（最多 150 字）";
          const send = button("说完了，请大家投票", async () => {if (text.value.trim()) await submit(c, {action: "perform", text: text.value.trim()}, send);}, {primary: true});
          box.append(text); row.append(send);
        } else box.append(el("p", "mp-help", `等 ${name(c, ch.player_id)} 开口……`));
      } else {
        box.append(el("blockquote", "mp-speech", ch.text));
        const votes = el("div", "mp-votes");
        (ch.voters || []).forEach(v => {const tag = el("span", `mp-vote-tag${v in (ch.votes || {}) ? (ch.votes[v] ? " yes" : " no") : ""}`); tag.append(pawn(c, v, " tiny"), el("span", "", v in (ch.votes || {}) ? (ch.votes[v] ? "赞成" : "反对") : "待投")); votes.append(tag);});
        box.append(votes);
        if (mine) legal.filter(a => a.action === "vote").forEach(a => row.append(actionButton(c, a, a.accept ? "👍 赞成" : "👎 反对", a.accept)));
      }
    } else if (ch.kind === "nope_attack") {
      box.append(el("strong", "", mine ? "有人对你使用了道具卡！" : "道具卡对决"), el("p", "", ch.text || ""));
      if (mine) {
        box.append(el("p", "mp-help", legal.some(a => a.action === "use_nope") ? "你有「不行」卡，可以抵消这一次。" : "你没有「不行」卡，只能接受。"));
        legal.forEach(a => row.append(actionButton(c, a, a.action === "use_nope" ? "打出「不行」" : "接受", a.action === "use_nope")));
      } else box.append(el("p", "mp-help", `等 ${name(c, ch.player_id)} 回应。`));
    } else if (ch.kind === "nope_fine") {
      box.append(el("strong", "", "罚款事件"), el("p", "", `${ch.reason} · 应付 ${money(ch.amount)}`));
      if (mine) legal.forEach(a => row.append(actionButton(c, a, a.action === "use_nope" ? "打出「不行」免掉" : "照付", a.action === "use_nope")));
    }
    if (mine && legal.some(a => a.action === "skip_choice") && !row.querySelector('[data-action="skip_choice"]')) row.append(actionButton(c, legal.find(a => a.action === "skip_choice"), ch.kind === "chat" ? "放弃这张卡" : "跳过"));
    if (row.childElementCount) box.append(row);
    panel.append(box);
  }
  function betPanel(c, panel) {
    const side = (c.privateState || {}).side_actions || [];
    const s = c.state;
    if (!s.bet_window && !(s.last_bets || []).length) return;
    const box = el("section", "mp-bets");
    if (s.bet_window && viewerId(c) === s.current_player_id) {
      const placed = Object.entries(s.bets || {});
      box.append(el("small", "mp-help", placed.length ? "大家押你这一把：" + placed.map(([pid, ch]) => `${name(c, pid)}押${BET[ch][0]}`).join("、")
        : "其他玩家可以在你掷骰前押大/小/7。"));
    } else if (s.bet_window) {
      box.append(el("strong", "", `观战押注 · ${name(c, s.current_player_id)} 这次掷骰`));
      const row = el("div", "mp-bet-chips");
      Object.entries(BET).forEach(([key, [label, hint, payout]]) => {
        const act = side.find(a => a.choice === key);
        const chip = el("button", `mp-chip bet-${key}`); chip.type = "button"; chip.disabled = !act;
        chip.append(el("b", "", label), el("small", "", hint), el("small", "", `押 50 → 回 ${payout}`));
        chip.addEventListener("click", () => act && submit(c, {action: "bet", choice: key}, chip, true));
        row.append(chip);
      });
      box.append(row);
      const placed = Object.entries(s.bets || {});
      box.append(el("p", "mp-help", placed.length ? "已押：" + placed.map(([pid, ch]) => `${name(c, pid)}押${BET[ch][0]}`).join("、")
        : viewerId(c) === s.current_player_id ? "其他玩家可以在你掷骰前押注。" : side.length ? "每人每回合一次，押金 50。" : "你这回合已经押过或不能押。"));
    } else {
      box.append(el("small", "mp-help", "上一把押注：" + s.last_bets.map(r => `${name(c, r.player_id)}押${BET[r.choice][0]}${r.won ? `中 +${r.payout}` : "未中"}`).join("；")));
    }
    panel.append(box);
  }
  function handPanel(c, panel) {
    const priv = c.privateState || {};
    const items = priv.items || [];
    const me = players(c).find(p => p.player_id === viewerId(c));
    if (!me || me.bankrupt) return;
    const box = el("section", "mp-hand");
    box.append(el("strong", "", "我的手牌"));
    const cards = el("div", "mp-cards");
    items.forEach(item => {
      const card = el("div", `mp-card item-${item.kind}`); card.append(el("b", "", item.name), el("small", "", ITEM_HELP[item.kind] || ""));
      const options = (priv.item_options || {})[item.kind];
      if (item.kind === "swap" && options && canAct(c)) card.append(button("使用", () => swapDialog(c, options), {primary: true}));
      if (item.kind === "acquire" && options && canAct(c)) {
        const act = actions(c).find(a => a.action === "use_item" && a.item === "acquire");
        if (act) card.append(actionButton(c, act, `收购 ${tileOf(c, options.tile_id)?.name} · ¥${money(options.price)}`, true));
      }
      cards.append(card);
    });
    if (me.bus) {const card = el("div", "mp-card item-bus"); card.append(el("b", "", `巴士票 ×${me.bus}`), el("small", "", "回合开始时代替掷骰，去本条边上任意一格。")); if (actions(c).some(a => a.action === "use_bus")) card.append(el("small", "mp-help", "点棋盘上高亮的格子上车。")); cards.append(card);}
    if (priv.jail_cards) {
      const card = el("div", "mp-card item-jail");
      card.append(el("b", "", `出狱卡 ×${priv.jail_cards}`), el("small", "", "入狱后在自己回合使用。"));
      cards.append(card);
    }
    if (!cards.childElementCount) cards.append(el("p", "mp-help", "还没有道具卡。机会、公益牌堆里混着强行交易、收购和「不行」卡。"));
    box.append(cards);
    // Loan status and actions.
    const loanRow = el("div", "mp-loan");
    const take = actions(c).filter(a => a.action === "take_loan"), repay = actions(c).find(a => a.action === "repay_loan");
    loanRow.append(el("span", "", me.loan ? `银行贷款 ¥${money(me.loan.balance)} · 还剩 ${me.loan.laps_left} 圈到期` : `可借额度 ¥${money(priv.loan_limit || 0)}`));
    if (take.length && canAct(c)) loanRow.append(button("借款", () => loanDialog(c, "take_loan", 50, Math.max(...take.map(a => a.amount)))));
    if (me.loan && canAct(c) && ["roll", "purchase", "manage"].includes(c.state.phase) && me.cash > 0) loanRow.append(button("还款", () => loanDialog(c, "repay_loan", 1, Math.min(me.cash, me.loan.balance), repay)));
    box.append(loanRow);
    panel.append(box);
  }
  function loanDialog(c, kind, min, max) {
    const {d, body} = dialog(c, kind === "take_loan" ? "向银行借款" : "提前还款");
    body.append(el("p", "mp-help", kind === "take_loan" ? "每过一次起点计息 10%（先从工资里扣），借款后第 3 次过起点到期；到期还不上，银行会拍卖你的地产抵债。" : "可以只还一部分。"));
    const form = el("form", "mp-form"); const label = el("label", "mp-field", `金额（${money(min)}~${money(max)}）`);
    const input = el("input"); input.type = "number"; input.min = String(min); input.max = String(max); input.step = "1"; input.value = String(max); input.required = true; label.append(input);
    const send = button("确认", () => {}, {primary: true}); send.type = "submit"; form.append(label, send); body.append(form);
    form.addEventListener("submit", async e => {e.preventDefault(); if (form.reportValidity() && await submit(c, {action: kind, amount: Number(input.value)}, send)) d.close();});
  }
  function swapDialog(c, options) {
    const {d, body} = dialog(c, "强行交易卡");
    body.append(el("p", "mp-help", "选你的一块地和对手的一块地互换。对方可以打出「不行」抵消。"));
    const form = el("form", "mp-form");
    const pick = (label, ids) => {const l = el("label", "mp-field", label); const s = el("select", "mp-select"); ids.forEach(id => {const t = tileOf(c, id); const o = el("option", "", `${t.name}${t.owner !== viewerId(c) ? ` · ${name(c, t.owner)}` : ""} · ¥${money(t.price)}`); o.value = id; s.append(o);}); l.append(s); form.append(l); return s;};
    const mine = pick("用我的", options.mine), theirs = pick("换对手的", options.theirs);
    const send = button("发动", () => {}, {primary: true}); send.type = "submit"; form.append(send); body.append(form);
    form.addEventListener("submit", async e => {e.preventDefault(); if (await submit(c, {action: "use_item", item: "swap", tile_id: Number(mine.value), target_tile_id: Number(theirs.value)}, send)) d.close();});
  }
  function tradeDialog(c) {
    const options = (c.privateState || {}).trade_options || {}; const me = viewerId(c);
    const partners = (options.partners || []).filter(id => id !== me); if (!partners.length) return;
    const {d, body} = dialog(c, "提出交易");
    body.append(el("p", "mp-help", "对方在自己的回合开始时决定是否接受。不付现金就填 0。"));
    const form = el("form", "mp-form"); let partner = partners[0];
    const tabs = el("div", "mp-tabs");
    const cols = el("div", "mp-trade-cols");
    const side = title => {const f = el("fieldset"); f.append(el("legend", "", title)); const l = el("label", "mp-field", "现金"); const i = el("input"); i.type = "number"; i.min = "0"; i.value = "0"; l.append(i); const list = el("div", "mp-checks"); f.append(l, list); cols.append(f); return {input: i, list};};
    const give = side("我给出"), take = side("对方给出");
    const fill = (target, ids) => {target.replaceChildren(); ids.forEach(id => {const t = tileOf(c, id); const l = el("label", "mp-check"); const i = el("input"); i.type = "checkbox"; i.value = id; l.append(i, el("span", "", t.name + (t.mortgaged ? "（抵押）" : ""))); target.append(l);}); if (!ids.length) target.append(el("small", "mp-help", "没有可交易的地"));};
    const change = () => {take.input.max = String((options.cash_by_player || {})[partner] || 0); fill(take.list, (options.take_tiles_by_player || {})[partner] || []);};
    partners.forEach(id => {const t = button("", () => {partner = id; [...tabs.children].forEach(x => x.setAttribute("aria-pressed", String(x === t))); change();}); t.append(pawn(c, id, " tiny"), el("span", "", name(c, id))); t.setAttribute("aria-pressed", String(id === partner)); tabs.append(t);});
    give.input.max = String((options.cash_by_player || {})[me] || 0); fill(give.list, options.give_tiles || []); change();
    const checked = list => [...list.querySelectorAll("input:checked")].map(i => Number(i.value));
    const error = el("p", "mp-error"); const send = button("提出交易", () => {}, {primary: true}); send.type = "submit";
    form.append(tabs, cols, error, send); body.append(form);
    form.addEventListener("submit", async e => {
      e.preventDefault();
      const p = {action: "propose_trade", to: partner, give_cash: Number(give.input.value || 0), take_cash: Number(take.input.value || 0), give_tiles: checked(give.list), take_tiles: checked(take.list)};
      if (!p.give_cash && !p.take_cash && !p.give_tiles.length && !p.take_tiles.length) {error.textContent = "至少放一点现金或一块地。"; return;}
      if (await submit(c, p, send)) d.close(); else error.textContent = "没提交成功，请检查条件。";
    });
  }
  function tradesPanel(c, panel) {
    const list = pendingTrades(c.state); if (!list.length) return;
    const box = el("details", "mp-trades"); box.open = list.some(t => t.to === viewerId(c));
    box.append(el("summary", "", `待处理交易 ${list.length} 笔`));
    list.forEach(t => {
      const card = el("div", "mp-trade");
      const offer = (cash, ids) => [cash ? `¥${money(cash)}` : "", ...(ids || []).map(id => tileOf(c, id)?.name)].filter(Boolean).join("、") || "无";
      card.append(el("strong", "", `${name(c, t.from)} → ${name(c, t.to)}`), el("p", "", `给出：${offer(t.give_cash, t.give_tiles)}；换取：${offer(t.take_cash, t.take_tiles)}`));
      if (t.to === viewerId(c) && canAct(c)) {
        const row = el("div", "mp-actions");
        actions(c).filter(a => a.action === "respond_trade").sort((a, b) => Number(b.accept) - Number(a.accept)).forEach(a => row.append(actionButton(c, a, a.accept ? "接受交易" : "拒绝", a.accept)));
        card.append(row);
      }
      box.append(card);
    });
    panel.append(box);
  }
  function renderControls(c) {
    const wrap = el("div", "mp-controls");
    const panel = el("div", "mp-panel");
    const s = c.state;
    const mine = canAct(c);
    const head = el("div", "mp-panel-head");
    head.append(el("strong", "", mine ? `该你了 · ${PHASES[s.phase] || ""}` : PHASES[s.phase] || "等待"));
    const me = players(c).find(p => p.player_id === viewerId(c));
    if (me) head.append(el("span", "mp-panel-cash", `现金 ¥${money(me.cash)}`));
    panel.append(head);
    if (s.phase === "setup") setupPanel(c, panel);
    if (s.phase === "purchase" && me && s.current_player_id === me.player_id) {
      const t = tileOf(c, me.position);
      if (t) panel.append(el("p", "mp-context", `${tileLabel(t)} · 售价 ¥${money(t.sale_price ?? t.price)}${t.sale_price !== undefined && t.sale_price !== t.price ? `（原价 ${money(t.price)}，促销中）` : ""}。不买就进入拍卖。`));
    }
    if (me?.jailed && mine && s.phase === "roll") panel.append(el("p", "mp-context", me.jail_turns >= 1 ? `在押第二回合：这次掷骰会强制交 ${s.rules?.bail || 50} 出狱再走。` : "在押第一回合：可以交钱/用卡出狱，或者试掷双骰。"));
    if (s.debt) panel.append(el("p", "mp-context warn", `${name(c, s.debt.payer)} 需要付 ¥${money(s.debt.amount)} 给${s.debt.creditor ? name(c, s.debt.creditor) : "银行"}（${s.debt.reason}）。可以卖房、抵押或借款。`));
    if (s.auction) {
      const a = s.auction, t = tileOf(c, a.tile_id);
      const box = el("section", "mp-auction");
      box.append(el("strong", "", `拍卖：${tileLabel(t)}${a.seizure_of ? `（银行扣押 ${name(c, a.seizure_of)} 的地）` : ""}`), el("p", "", `标价 ¥${money(t?.price)} · 当前最高 ¥${money(a.bid)}${a.highest_bidder ? `（${name(c, a.highest_bidder)}）` : ""}`));
      if (mine && actions(c).some(x => x.action === "bid")) {
        const f = el("form", "mp-bid"); const input = el("input"); input.type = "number"; input.min = String(a.bid + 1); input.max = String(me?.cash || 0); input.value = String(a.bid + 10 <= (me?.cash || 0) ? a.bid + 10 : a.bid + 1);
        const go = button("出价", () => {}, {primary: true}); go.type = "submit"; f.append(input, go);
        f.addEventListener("submit", async e => {e.preventDefault(); if (f.reportValidity()) await submit(c, {action: "bid", amount: Number(input.value)}, go);});
        box.append(f);
      }
      panel.append(box);
    }
    if (s.phase === "choice") choicePanel(c, panel);
    const row = el("div", "mp-actions main"); row.setAttribute("role", "group"); row.setAttribute("aria-label", "回合操作");
    const skip = new Set([...ASSET_ACTIONS, "bid", "propose_trade", "respond_trade", "take_loan", "repay_loan", "use_item", "use_bus",
      "choose_destination", "pick_auction", "give_gift", "vote", "use_nope", "decline_nope", "skip_choice", "choose_edition"]);
    actions(c).filter(a => !skip.has(a.action)).forEach(a => row.append(actionButton(c, a)));
    if (actions(c).some(a => a.action === "use_bus") && mine) row.append(el("small", "mp-help", "或点高亮格子坐巴士"));
    if (row.childElementCount) panel.append(row);
    if (!mine && !c.isTerminal && s.phase !== "setup") panel.append(el("p", "mp-help", `等 ${name(c, s.turn_player_id || s.current_player_id)} 操作。`));
    betPanel(c, panel);
    tradesPanel(c, panel);
    if (s.phase !== "setup") handPanel(c, panel);
    const tools = el("div", "mp-actions tools");
    tools.append(button("地产与经营", () => playerDialog(c, viewerId(c) || s.current_player_id)));
    if (((c.privateState || {}).trade_options || {}).partners?.length && mine) tools.append(button("提出交易", () => tradeDialog(c)));
    if (s.phase !== "setup") panel.append(tools);
    panel.append(el("small", "mp-help", "局内现金只在本局有效，和平台筹码无关。"));
    wrap.append(panel); c.controls.append(wrap);
  }

  window.DuelGameUI.register(GAME, {
    participantPresentation: "board-edge", multiplayerTable: true, usesEmbeddedActionFeedback: true,
    usesStandardMoveConfirmation: false, ownsPrivateStatePresentation: true,
    renderBoard, renderControls, transitionFeedback,
  });
}());

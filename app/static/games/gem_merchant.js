(function registerGemMerchant() {
  "use strict";

  // 宝石商人：服务端 legal_actions 是唯一权威。这里只做展示、草稿选择和
  // “当前选择是否等于某个合法动作”的比对，不在浏览器复制规则。
  const STYLE_ID = "duel-game-gem-merchant-styles";
  const STYLE_HREF = "/static/games/gem_merchant.css?v=1";
  const GEMS = ["white", "blue", "green", "red", "black"];
  const TOKENS = [...GEMS, "pearl", "gold"];
  const LABELS = {
    white: "白", blue: "蓝", green: "绿", red: "红", black: "黑", pearl: "珍珠", gold: "金",
  };
  const ABILITY_SHORT = {extra_turn: "再来", take_gem: "+同色", steal: "偷1", privilege: "+券"};
  const ABILITY_LONG = {
    extra_turn: "再来一回合",
    take_gem: "从盘上拿 1 枚同色宝石",
    steal: "从对手拿 1 枚宝石或珍珠",
    privilege: "拿 1 张特权券",
  };
  const LEVELS = {1: "一级", 2: "二级", 3: "三级"};
  const ROYAL_MARKS = {1: "冠", 2: "影", 3: "风", 4: "卷"};

  function el(doc, tag, cls, text) {
    const node = doc.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined && text !== null) node.textContent = String(text);
    if (tag === "button") node.type = "button";
    return node;
  }

  function ensureStylesheet(doc) {
    if (!doc || !doc.head) return null;
    const existing = typeof doc.getElementById === "function" ? doc.getElementById(STYLE_ID) : null;
    if (existing) return existing;
    const link = doc.createElement("link");
    link.id = STYLE_ID;
    link.rel = "stylesheet";
    link.href = STYLE_HREF;
    link.dataset.duelGameStyle = "gem_merchant";
    doc.head.appendChild(link);
    return link;
  }

  const cellName = (cell) => `${"ABCDE"[cell[0]]}${cell[1] + 1}`;
  const sameCell = (a, b) => Boolean(a && b && a[0] === b[0] && a[1] === b[1]);
  const sortCells = (cells) => [...cells].sort((a, b) => a[0] - b[0] || a[1] - b[1]);
  const cellsKey = (cells) => sortCells(cells).map((c) => c.join(",")).join(";");

  function draft(context) {
    if (!context.uiState.gm) {
      context.uiState.gm = {
        mode: null, sel: [], gold: null, card: null, deck: null,
        joker: null, pick: null, busy: false, error: "",
      };
    }
    return context.uiState.gm;
  }

  function legal(context) {
    const list = context.legalActions || (context.privateState || {}).legal_actions;
    return Array.isArray(list) ? list : [];
  }

  function viewerId(context) {
    return (context.viewer && context.viewer.player_id) || "";
  }

  function order(context) {
    const ids = context.state.participant_order || [];
    const me = viewerId(context);
    if (!ids.includes(me)) return [ids[1], ids[0]].filter(Boolean);
    return [ids.find((id) => id !== me), me];
  }

  function nameOf(context, playerId) {
    const participant = (context.participants || []).find((p) => p.player_id === playerId);
    return participant ? (participant.display_name || participant.player_id) : (playerId || "玩家");
  }

  function isTerminal(context) {
    return Boolean(
      context.isTerminal || context.state.result
      || ((context.state.flow || {}).phase === "finished")
      || ["finished", "archived"].includes((context.room || {}).status)
    );
  }

  function myTurn(context) {
    return Boolean(context.canMove && !isTerminal(context) && legal(context).length);
  }

  function rerender(context) {
    if (context.helpers && typeof context.helpers.rerender === "function") context.helpers.rerender();
  }

  function submit(context, move) {
    const ui = draft(context);
    if (ui.busy) return;
    if (context.helpers.canMove && !context.helpers.canMove()) return;
    ui.busy = true;
    ui.error = "";
    rerender(context);
    Promise.resolve(context.helpers.submitMove(move)).then((ok) => {
      ui.busy = false;
      if (!ok) rerender(context);
    }, () => {
      ui.busy = false;
      rerender(context);
    });
  }

  function reset(context) {
    const ui = draft(context);
    Object.assign(ui, {mode: null, sel: [], gold: null, card: null, deck: null, joker: null, pick: null, error: ""});
  }

  // ---------------------------------------------------------------- 画面零件

  function gem(doc, color, extra) {
    const node = el(doc, "span", `gm-gem gem-${color}${extra ? ` ${extra}` : ""}`);
    node.appendChild(el(doc, "span", "gm-facet"));
    node.setAttribute("aria-hidden", "true");
    return node;
  }

  function crownIcon(doc) {
    const node = el(doc, "span", "gm-crown-icon");
    node.setAttribute("aria-hidden", "true");
    return node;
  }

  function scrollIcon(doc) {
    const node = el(doc, "span", "gm-scroll-icon");
    node.setAttribute("aria-hidden", "true");
    return node;
  }

  function cardText(card) {
    if (!card || card.hidden) return `${LEVELS[card && card.level] || ""}盲抽卡（对手看不到）`;
    const kind = card.joker ? (card.as ? `百搭（作${LABELS[card.as]}）` : "百搭") : card.color ? `${LABELS[card.color]}色` : "纯分";
    const bits = [`${LEVELS[card.level]}${kind}卡 #${card.id}`, `${card.points} 分`];
    if (card.crowns) bits.push(`${card.crowns} 顶皇冠`);
    if (card.bonus === 2) bits.push("双加成");
    if (card.ability) bits.push(ABILITY_LONG[card.ability]);
    const cost = TOKENS.filter((t) => card.cost && card.cost[t]).map((t) => `${LABELS[t]}${card.cost[t]}`);
    bits.push(`成本 ${cost.join(" ") || "0"}`);
    return bits.join("，");
  }

  function cardNode(doc, card, options = {}) {
    const tag = options.onClick ? "button" : "div";
    if (!card) {
      const empty = el(doc, "div", "gm-card is-empty");
      empty.setAttribute("aria-label", "空位");
      return empty;
    }
    if (card.hidden) {
      const back = el(doc, tag, `gm-card is-back lv-${card.level}`);
      if (options.size) back.classList.add(`is-${options.size}`);
      back.appendChild(el(doc, "span", "gm-back-mark", "盲抽"));
      back.appendChild(el(doc, "span", "gm-level", "◆".repeat(card.level || 1)));
      back.setAttribute("aria-label", cardText(card));
      if (options.onClick) back.addEventListener("click", options.onClick);
      return back;
    }
    const tint = card.joker && !card.as ? "is-joker" : `tint-${card.as || card.color || "none"}`;
    const node = el(doc, tag, `gm-card lv-${card.level} ${tint}`);
    if (options.size) node.classList.add(`is-${options.size}`);
    if (options.buyable) node.classList.add("can-buy");
    if (options.focused) node.classList.add("is-focused");
    if (options.reservable) node.classList.add("can-reserve");
    node.dataset.cardId = String(card.id);
    const top = el(doc, "span", "gm-card-top");
    top.appendChild(el(doc, "span", `gm-points${card.points ? "" : " is-zero"}`, card.points || ""));
    if (card.crowns) {
      const crowns = el(doc, "span", "gm-card-crowns");
      crowns.appendChild(crownIcon(doc));
      if (card.crowns > 1) crowns.appendChild(el(doc, "span", "", card.crowns));
      top.appendChild(crowns);
    }
    const bonus = el(doc, "span", "gm-card-bonus");
    if (card.joker && !card.as) {
      bonus.appendChild(el(doc, "span", "gm-joker-mark", "百搭"));
    } else if (card.as || card.color) {
      for (let i = 0; i < (card.bonus || 0); i += 1) bonus.appendChild(gem(doc, card.as || card.color, "is-mini"));
    }
    top.appendChild(bonus);
    node.appendChild(top);
    // 原创底纹：每种颜色一枚大号淡色宝石轮廓。
    if (card.as || card.color) node.appendChild(gem(doc, card.as || card.color, "gm-card-art"));
    if (card.ability) node.appendChild(el(doc, "span", "gm-ability", ABILITY_SHORT[card.ability]));
    const cost = el(doc, "span", "gm-cost");
    TOKENS.filter((t) => card.cost && card.cost[t]).forEach((t) => {
      const dot = el(doc, "span", `gm-cost-dot gem-${t}`, card.cost[t]);
      if (options.short && options.short[t]) dot.classList.add("is-short");
      cost.appendChild(dot);
    });
    node.appendChild(cost);
    node.appendChild(el(doc, "span", "gm-level", "◆".repeat(card.level)));
    node.setAttribute("aria-label", cardText(card));
    if (options.onClick) node.addEventListener("click", options.onClick);
    return node;
  }

  function royalNode(doc, royal, options = {}) {
    const node = el(doc, options.onClick ? "button" : "div", "gm-royal");
    if (options.selected) node.classList.add("is-selected");
    node.dataset.royalId = String(royal.id);
    node.appendChild(el(doc, "span", "gm-royal-emblem", ROYAL_MARKS[royal.id] || "冠"));
    const body = el(doc, "span", "gm-royal-body");
    body.appendChild(el(doc, "strong", "gm-royal-name", royal.name));
    body.appendChild(el(doc, "span", "gm-royal-ability", royal.ability ? ABILITY_LONG[royal.ability] : "只有声望"));
    body.appendChild(el(doc, "span", "gm-royal-short", royal.ability ? ABILITY_SHORT[royal.ability] : "纯分"));
    node.appendChild(body);
    node.appendChild(el(doc, "span", "gm-royal-points", royal.points));
    node.setAttribute("aria-label", `称号卡「${royal.name}」${royal.points} 分，${royal.ability ? ABILITY_LONG[royal.ability] : "无能力"}`);
    if (options.onClick) node.addEventListener("click", options.onClick);
    return node;
  }

  function meter(doc, label, value, target) {
    const node = el(doc, "span", "gm-meter");
    node.appendChild(el(doc, "strong", "", value));
    node.appendChild(el(doc, "span", "", `${label}/${target}`));
    const bar = el(doc, "span", "gm-meter-bar");
    bar.style.setProperty("--fill", `${Math.min(100, Math.round((Number(value) || 0) / target * 100))}%`);
    node.appendChild(bar);
    return node;
  }

  // ---------------------------------------------------------------- 动作索引

  function actionsOf(context, kind) {
    return legal(context).filter((a) => a.action === kind);
  }

  function takeFor(context, cells) {
    const key = cellsKey(cells);
    return actionsOf(context, "take").find((a) => cellsKey(a.cells) === key) || null;
  }

  function extendable(context, cells, cell) {
    const wanted = cells.concat([cell]);
    const key = cellsKey(wanted);
    return actionsOf(context, "take").some((a) => {
      if (a.cells.length < wanted.length) return false;
      if (cellsKey(a.cells) === key) return true;
      return wanted.every((w) => a.cells.some((c) => sameCell(c, w)));
    });
  }

  function buyOptions(context, cardId) {
    return actionsOf(context, "buy").filter((a) => a.card_id === cardId);
  }

  function reserveOptions(context, target) {
    return actionsOf(context, "reserve").filter((a) => (
      target.card_id !== undefined ? a.card_id === target.card_id : a.level === target.level
    ));
  }

  // ---------------------------------------------------------------- 点击

  function onCell(context, cell) {
    const ui = draft(context);
    if (!myTurn(context) || ui.busy) return;
    const value = context.state.board[cell[0]][cell[1]];
    const pending = context.state.pending;
    if (pending) {
      if (pending.kind === "take_gem" && actionsOf(context, "take_bonus_gem").some((a) => sameCell(a.cell, cell))) {
        ui.pick = cell;
        rerender(context);
      }
      return;
    }
    if (ui.mode === "privilege") {
      if (actionsOf(context, "use_privilege").some((a) => sameCell(a.cell, cell))) ui.pick = cell;
      else ui.error = value === "gold" ? "特权券不能换金" : "这一格不能用特权券";
      rerender(context);
      return;
    }
    if (value === "gold") {
      if (!actionsOf(context, "reserve").some((a) => sameCell(a.gold, cell))) {
        ui.error = "现在不能拿金保留（保留区满了或无卡可保留）";
        rerender(context);
        return;
      }
      const keepCard = ui.mode === "reserve" ? ui.card : null;
      const keepDeck = ui.mode === "reserve" ? ui.deck : null;
      reset(context);
      Object.assign(ui, {mode: "reserve", gold: cell, card: keepCard, deck: keepDeck});
      rerender(context);
      return;
    }
    if (!value) return;
    if (ui.mode && ui.mode !== "take") reset(context);
    ui.mode = "take";
    ui.card = null;
    if (ui.sel.some((c) => sameCell(c, cell))) {
      ui.sel = ui.sel.filter((c) => !sameCell(c, cell));
    } else if (ui.sel.length >= 3) {
      ui.error = "一次最多拿 3 枚";
    } else {
      ui.sel = ui.sel.concat([cell]);
      ui.error = "";
    }
    if (!ui.sel.length) ui.mode = null;
    rerender(context);
  }

  function onCard(context, cardId, where) {
    const ui = draft(context);
    if (ui.busy) return;
    if (ui.mode === "reserve" && where === "pyramid" && myTurn(context)) {
      ui.card = cardId;
      ui.deck = null;
      rerender(context);
      return;
    }
    if (ui.mode === "take" || ui.mode === "privilege") reset(context);
    ui.mode = "card";
    ui.card = cardId;
    ui.cardWhere = where;
    ui.joker = null;
    rerender(context);
  }

  function onDeck(context, level) {
    const ui = draft(context);
    if (!myTurn(context) || ui.busy || context.state.pending) return;
    if (!reserveOptions(context, {level}).length) {
      ui.error = `${LEVELS[level]}牌堆现在不能盲抽保留`;
      rerender(context);
      return;
    }
    if (ui.mode !== "reserve") {
      reset(context);
      ui.mode = "reserve";
      ui.gold = reserveOptions(context, {level})[0].gold;
    }
    ui.deck = level;
    ui.card = null;
    rerender(context);
  }

  // ---------------------------------------------------------------- 桌面

  function playerPanel(context, playerId, isViewer) {
    const doc = context.board.ownerDocument;
    const state = context.state;
    const player = (state.players || {})[playerId];
    const panel = el(doc, "section", `gm-player${isViewer ? " is-viewer" : " is-opponent"}`);
    if (!player) return panel;
    const acting = !isTerminal(context) && context.room && context.room.current_player_id === playerId;
    if (acting) panel.classList.add("is-acting");
    panel.dataset.playerId = playerId;
    const head = el(doc, "div", "gm-player-head");
    const title = el(doc, "div", "gm-player-name");
    title.appendChild(el(doc, "strong", "", isViewer ? `${nameOf(context, playerId)}（你）` : nameOf(context, playerId)));
    if (state.first_player_id === playerId) title.appendChild(el(doc, "span", "gm-tag", "先手"));
    if (acting) title.appendChild(el(doc, "span", "gm-tag is-acting", "行动中"));
    head.appendChild(title);
    const meters = el(doc, "div", "gm-meters");
    const targets = state.win_targets || {points: 20, crowns: 10, color_points: 10};
    meters.append(
      meter(doc, "分", player.points, targets.points),
      meter(doc, "冠", player.crowns, targets.crowns),
      meter(doc, "单色", player.best_color_points, targets.color_points),
    );
    head.appendChild(meters);
    panel.appendChild(head);

    const tokens = el(doc, "div", "gm-token-row");
    TOKENS.forEach((t) => {
      const chip = el(doc, "span", `gm-token-chip${player.tokens[t] ? "" : " is-zero"}`);
      chip.appendChild(gem(doc, t, "is-small"));
      chip.appendChild(el(doc, "span", "", player.tokens[t] || 0));
      chip.setAttribute("aria-label", `${LABELS[t]} ${player.tokens[t] || 0} 枚`);
      tokens.appendChild(chip);
    });
    const privileges = el(doc, "span", "gm-privileges");
    privileges.appendChild(scrollIcon(doc));
    privileges.appendChild(el(doc, "span", "", player.privileges));
    privileges.setAttribute("aria-label", `特权券 ${player.privileges} 张`);
    tokens.appendChild(privileges);
    const total = el(doc, "span", `gm-token-total${player.token_total > (targets.token_limit || 10) ? " is-over" : ""}`,
      `${player.token_total}/${targets.token_limit || 10}`);
    tokens.appendChild(total);
    panel.appendChild(tokens);

    const bonuses = el(doc, "div", "gm-bonus-row");
    const owned = GEMS.filter((c) => player.bonuses[c]);
    if (!owned.length) bonuses.appendChild(el(doc, "span", "gm-muted", "还没有加成卡"));
    owned.forEach((c) => {
      const box = el(doc, "span", `gm-bonus-box gem-${c}`);
      box.appendChild(el(doc, "strong", "", player.bonuses[c]));
      if (player.color_points[c]) box.appendChild(el(doc, "small", "", `${player.color_points[c]}分`));
      box.setAttribute("aria-label", `${LABELS[c]}色加成 ${player.bonuses[c]}，该色 ${player.color_points[c]} 分`);
      bonuses.appendChild(box);
    });
    if ((player.purchased || []).length) bonuses.appendChild(el(doc, "span", "gm-muted", `${player.purchased.length} 张卡`));
    (player.royals || []).forEach((r) => bonuses.appendChild(el(doc, "span", "gm-royal-tag", `${r.name} ${r.points}分`)));
    panel.appendChild(bonuses);

    const reservedCards = isViewer && Array.isArray((context.privateState || {}).reserved)
      ? context.privateState.reserved
      : (player.reserved || []);
    if (reservedCards.length) {
      const reserved = el(doc, "div", "gm-reserved");
      reserved.appendChild(el(doc, "span", "gm-reserved-label", "保留"));
      const ui = draft(context);
      reservedCards.forEach((card) => {
        const node = cardNode(doc, card, {
          size: "small",
          buyable: isViewer && !card.hidden && buyOptions(context, card.id).length > 0,
          focused: !card.hidden && ui.card === card.id,
          onClick: card.hidden ? null : () => onCard(context, card.id, isViewer ? "reserved" : "opponent"),
        });
        if (isViewer && card.blind) node.classList.add("is-blind-own");
        reserved.appendChild(node);
      });
      panel.appendChild(reserved);
    }
    return panel;
  }

  function statusLine(context) {
    const doc = context.board.ownerDocument;
    const state = context.state;
    const line = el(doc, "div", "gm-status");
    line.setAttribute("role", "status");
    if (isTerminal(context)) {
      const winner = state.winner_player_id;
      line.classList.add("is-final");
      line.textContent = winner
        ? `${nameOf(context, winner)} 获胜：${state.finish_reason || ""}`
        : `平局：${state.finish_reason || ""}`;
      return line;
    }
    const turn = (state.flow || {}).turn_number || 0;
    const actor = (context.room || {}).current_player_id;
    const who = actor === viewerId(context) ? "你" : nameOf(context, actor);
    line.appendChild(el(doc, "strong", "", `第 ${turn + 1} 回合 · ${who}`));
    const note = state.last_action_note || "";
    if (note) line.appendChild(el(doc, "span", "gm-last-note", note));
    return line;
  }

  function royalsStrip(context) {
    const doc = context.board.ownerDocument;
    const strip = el(doc, "section", "gm-royals");
    strip.setAttribute("aria-label", "称号卡：皇冠累计到第 3、第 6 顶各选一张");
    const ui = draft(context);
    const pendingRoyal = myTurn(context) && context.state.pending && context.state.pending.kind === "royal";
    const royals = context.state.royals_available || [];
    if (!royals.length) strip.appendChild(el(doc, "span", "gm-muted", "称号卡都被请走了"));
    royals.forEach((royal) => {
      const option = pendingRoyal && actionsOf(context, "choose_royal").some((a) => a.royal_id === royal.id);
      strip.appendChild(royalNode(doc, royal, {
        selected: option && ui.pick === royal.id,
        onClick: option ? () => { ui.pick = royal.id; rerender(context); } : null,
      }));
    });
    return strip;
  }

  function pyramid(context) {
    const doc = context.board.ownerDocument;
    const state = context.state;
    const ui = draft(context);
    const wrap = el(doc, "section", "gm-pyramid");
    wrap.setAttribute("aria-label", "发展卡：三级在上、一级在下，左侧为牌堆");
    ["3", "2", "1"].forEach((level) => {
      const row = el(doc, "div", `gm-pyramid-row lv-${level}`);
      const count = (state.deck_counts || {})[level] || 0;
      const blindable = myTurn(context) && reserveOptions(context, {level: Number(level)}).length > 0;
      const deck = el(doc, "button", `gm-deck lv-${level}${blindable ? " can-reserve" : ""}${ui.deck === Number(level) ? " is-focused" : ""}`);
      deck.appendChild(el(doc, "span", "gm-level", "◆".repeat(Number(level))));
      deck.appendChild(el(doc, "strong", "", count));
      deck.setAttribute("aria-label", `${LEVELS[level]}牌堆 ${count} 张${blindable ? "，可盲抽保留" : ""}`);
      deck.disabled = !blindable;
      deck.addEventListener("click", () => onDeck(context, Number(level)));
      row.appendChild(deck);
      const cards = el(doc, "div", "gm-pyramid-cards");
      ((state.pyramid || {})[level] || []).forEach((card) => {
        cards.appendChild(cardNode(doc, card, card ? {
          buyable: buyOptions(context, card.id).length > 0,
          reservable: ui.mode === "reserve" && reserveOptions(context, {card_id: card.id}).length > 0,
          focused: ui.card === card.id,
          onClick: () => onCard(context, card.id, "pyramid"),
        } : {}));
      });
      row.appendChild(cards);
      wrap.appendChild(row);
    });
    return wrap;
  }

  function gemBoard(context) {
    const doc = context.board.ownerDocument;
    const state = context.state;
    const ui = draft(context);
    const zone = el(doc, "section", "gm-board-zone");
    const grid = el(doc, "div", "gm-gem-board");
    grid.setAttribute("aria-label", "宝石盘 5×5，行 A–E，列 1–5");
    const active = myTurn(context) && !ui.busy;
    const pending = state.pending;
    const bonusCells = actionsOf(context, "take_bonus_gem").map((a) => a.cell);
    const privilegeCells = ui.mode === "privilege" ? actionsOf(context, "use_privilege").map((a) => a.cell) : [];
    (state.board || []).forEach((row, r) => {
      row.forEach((value, c) => {
        const cell = [r, c];
        const button = el(doc, "button", `gm-cell${value ? "" : " is-empty"}`);
        button.dataset.cell = cellName(cell);
        if (value) button.appendChild(gem(doc, value));
        const selected = ui.sel.some((s) => sameCell(s, cell)) || sameCell(ui.gold, cell) || sameCell(ui.pick, cell);
        if (selected) button.classList.add("is-selected");
        let option = false;
        if (active && pending && pending.kind === "take_gem") option = bonusCells.some((s) => sameCell(s, cell));
        else if (active && ui.mode === "privilege") option = privilegeCells.some((s) => sameCell(s, cell));
        else if (active && ui.mode === "take" && ui.sel.length && !selected && value && value !== "gold") {
          option = extendable(context, ui.sel, cell);
        }
        if (option && !selected) button.classList.add("is-option");
        button.disabled = !active || !value;
        button.setAttribute("aria-pressed", String(selected));
        button.setAttribute("aria-label", `${cellName(cell)} ${value ? LABELS[value] : "空格"}`);
        button.addEventListener("click", () => onCell(context, cell));
        grid.appendChild(button);
      });
    });
    zone.appendChild(grid);
    const side = el(doc, "div", "gm-board-side");
    const table = el(doc, "span", "gm-side-box");
    table.appendChild(el(doc, "small", "", "桌上券"));
    const scroll = el(doc, "strong", "");
    scroll.appendChild(scrollIcon(doc));
    scroll.appendChild(el(doc, "span", "", state.privileges_on_table));
    table.appendChild(scroll);
    const bag = el(doc, "span", "gm-side-box");
    bag.appendChild(el(doc, "small", "", "袋中"));
    bag.appendChild(el(doc, "strong", "", state.bag_count || 0));
    side.append(table, bag);
    if (state.refilled) side.appendChild(el(doc, "span", "gm-side-note", "本回合已补盘"));
    zone.appendChild(side);
    return zone;
  }

  function terminalReview(context) {
    const doc = context.board.ownerDocument;
    const review = el(doc, "section", "gm-review");
    review.appendChild(el(doc, "strong", "", "终局复盘：盲抽保留的卡"));
    let any = false;
    (context.state.participant_order || []).forEach((pid) => {
      const cards = ((context.state.players || {})[pid] || {}).reserved || [];
      const blind = cards.filter((c) => c.blind && !c.hidden);
      if (!blind.length) return;
      any = true;
      const row = el(doc, "div", "gm-review-row");
      row.dataset.playerId = pid;
      row.appendChild(el(doc, "span", "", nameOf(context, pid)));
      blind.forEach((card) => row.appendChild(cardNode(doc, card, {size: "small"})));
      review.appendChild(row);
    });
    return any ? review : null;
  }

  function renderBoard(context) {
    const doc = context.board.ownerDocument;
    ensureStylesheet(doc);
    context.board.classList.add("gem_merchant");
    context.board.setAttribute("aria-label", "宝石商人桌面");
    const [opponent, me] = order(context);
    const root = el(doc, "div", "gm-game");
    root.appendChild(statusLine(context));
    if (opponent) root.appendChild(playerPanel(context, opponent, false));
    root.appendChild(royalsStrip(context));
    root.appendChild(pyramid(context));
    root.appendChild(gemBoard(context));
    if (me) root.appendChild(playerPanel(context, me, true));
    if (isTerminal(context)) {
      const review = terminalReview(context);
      if (review) root.appendChild(review);
    }
    context.board.appendChild(root);
  }

  // ---------------------------------------------------------------- 操作区

  function cardDetail(context, controls) {
    const doc = context.board.ownerDocument;
    const ui = draft(context);
    const state = context.state;
    const [, me] = order(context);
    const all = [];
    ["1", "2", "3"].forEach((lv) => ((state.pyramid || {})[lv] || []).forEach((c) => c && all.push(c)));
    ((context.privateState || {}).reserved || []).forEach((c) => all.push(c));
    Object.values(state.players || {}).forEach((p) => (p.reserved || []).forEach((c) => !c.hidden && all.push(c)));
    const card = all.find((c) => c.id === ui.card);
    if (!card) return false;
    const panel = el(doc, "div", "gm-detail");
    const player = (state.players || {})[me];
    const short = {};
    let need = [];
    if (player) {
      TOKENS.forEach((t) => {
        const cost = (card.cost || {})[t] || 0;
        if (!cost) return;
        const due = Math.max(0, cost - ((player.bonuses || {})[t] || 0));
        if (!due) return;
        const lack = Math.max(0, due - (player.tokens[t] || 0));
        if (lack) short[t] = lack;
        need.push(`${LABELS[t]}${due}${lack ? `（缺${lack}）` : ""}`);
      });
    }
    panel.appendChild(cardNode(doc, card, {size: "large", short}));
    const info = el(doc, "div", "gm-detail-info");
    const head = el(doc, "div", "gm-detail-head");
    head.appendChild(el(doc, "strong", "", cardText(card).split("，")[0]));
    const close = actionButton(doc, "关上", false, false, () => { reset(context); rerender(context); });
    close.classList.add("gm-detail-close");
    head.appendChild(close);
    info.appendChild(head);
    info.appendChild(el(doc, "span", "", `${card.points} 分${card.crowns ? ` · ${card.crowns} 顶皇冠` : ""}${card.bonus === 2 ? " · 双加成" : ""}`));
    if (card.ability) info.appendChild(el(doc, "span", "", `能力：${ABILITY_LONG[card.ability]}`));
    if (card.joker) info.appendChild(el(doc, "span", "", "百搭：压在你已有加成的一种颜色上"));
    if (player && ui.cardWhere !== "opponent") {
      const lack = Object.values(short).reduce((a, b) => a + b, 0);
      info.appendChild(el(doc, "span", "", `扣加成后要付：${need.join(" ") || "0（加成全抵）"}`));
      if (lack) info.appendChild(el(doc, "span", lack <= (player.tokens.gold || 0) ? "" : "gm-warn",
        `差 ${lack} 枚，用金补${lack <= (player.tokens.gold || 0) ? "得上" : "不够"}`));
    }
    panel.appendChild(info);
    controls.appendChild(panel);

    const buys = buyOptions(context, card.id);
    const reserves = reserveOptions(context, {card_id: card.id});
    const buttons = el(doc, "div", "gm-actions");
    if (card.joker && buys.length > 1) {
      const chooser = el(doc, "div", "gm-choices");
      chooser.appendChild(el(doc, "span", "gm-muted", "压在哪个颜色："));
      buys.forEach((a) => {
        const b = el(doc, "button", `gm-choice${ui.joker === a.joker_color ? " is-selected" : ""}`);
        b.appendChild(gem(doc, a.joker_color, "is-small"));
        b.appendChild(el(doc, "span", "", LABELS[a.joker_color]));
        b.setAttribute("aria-pressed", String(ui.joker === a.joker_color));
        b.addEventListener("click", () => { ui.joker = a.joker_color; rerender(context); });
        chooser.appendChild(b);
      });
      controls.appendChild(chooser);
    }
    if (myTurn(context) && !context.state.pending && ui.cardWhere !== "opponent") {
      const buy = card.joker
        ? (buys.length === 1 ? buys[0] : buys.find((a) => a.joker_color === ui.joker))
        : buys[0];
      buttons.appendChild(actionButton(doc, "买下", true, !buy || ui.busy, () => buy && submit(context, buy)));
      if (ui.cardWhere === "pyramid") {
        buttons.appendChild(actionButton(doc, "拿金保留", false, !reserves.length || ui.busy, () => {
          const chosen = reserves.find((a) => sameCell(a.gold, ui.gold)) || reserves[0];
          if (chosen) submit(context, chosen);
        }));
      }
    }
    if (buttons.children.length) controls.appendChild(buttons);
    return true;
  }

  function actionButton(doc, label, primary, disabled, onClick) {
    const b = el(doc, "button", `pixel-btn compact gm-button${primary ? " gm-primary" : " secondary"}`, label);
    b.disabled = Boolean(disabled);
    b.addEventListener("click", () => { if (!b.disabled) onClick(); });
    return b;
  }

  function prompt(doc, controls, text, warn) {
    const p = el(doc, "p", `gm-prompt${warn ? " gm-warn" : ""}`, text);
    p.setAttribute("role", "status");
    controls.appendChild(p);
  }

  function pendingControls(context, controls) {
    const doc = context.board.ownerDocument;
    const ui = draft(context);
    const pending = context.state.pending;
    const buttons = el(doc, "div", "gm-actions");
    if (pending.kind === "take_gem") {
      const move = actionsOf(context, "take_bonus_gem").find((a) => sameCell(a.cell, ui.pick));
      prompt(doc, controls, ui.pick
        ? `卡牌能力：拿 ${cellName(ui.pick)} 的${LABELS[pending.color]}？`
        : `卡牌能力：点盘上亮起的一枚${LABELS[pending.color]}`);
      buttons.appendChild(actionButton(doc, "确认", true, !move || ui.busy, () => submit(context, move)));
    } else if (pending.kind === "royal") {
      const move = actionsOf(context, "choose_royal").find((a) => a.royal_id === ui.pick);
      prompt(doc, controls, "集到皇冠：点上方一张称号卡请回来");
      buttons.appendChild(actionButton(doc, "就这位", true, !move || ui.busy, () => submit(context, move)));
    } else {
      const kind = pending.kind === "steal" ? "steal" : "discard";
      const options = actionsOf(context, kind);
      prompt(doc, controls, kind === "steal"
        ? "从对手那里拿 1 枚（金不能拿）"
        : `宝石超过 10 枚，还要弃 ${pending.count || 1} 枚（一次一枚）`);
      const chooser = el(doc, "div", "gm-choices");
      options.forEach((a) => {
        const b = el(doc, "button", `gm-choice${ui.pick === a.gem ? " is-selected" : ""}`);
        b.dataset.gem = a.gem;
        b.appendChild(gem(doc, a.gem, "is-small"));
        b.appendChild(el(doc, "span", "", LABELS[a.gem]));
        b.setAttribute("aria-pressed", String(ui.pick === a.gem));
        b.addEventListener("click", () => { ui.pick = a.gem; rerender(context); });
        chooser.appendChild(b);
      });
      controls.appendChild(chooser);
      const move = options.find((a) => a.gem === ui.pick);
      buttons.appendChild(actionButton(doc, kind === "steal" ? "拿这枚" : "弃这枚", true, !move || ui.busy,
        () => submit(context, move)));
    }
    controls.appendChild(buttons);
  }

  function optionalControls(context, controls) {
    const doc = context.board.ownerDocument;
    const ui = draft(context);
    const state = context.state;
    const buttons = el(doc, "div", "gm-actions");
    const cancel = () => { reset(context); rerender(context); };
    if (ui.mode === "privilege") {
      const move = actionsOf(context, "use_privilege").find((a) => sameCell(a.cell, ui.pick));
      prompt(doc, controls, ui.pick
        ? `用 1 张特权券拿 ${cellName(ui.pick)} 的${LABELS[state.board[ui.pick[0]][ui.pick[1]]]}`
        : "点盘上一枚非金宝石，用 1 张特权券换它");
      buttons.append(actionButton(doc, "撤销", false, false, cancel),
        actionButton(doc, "确认", true, !move || ui.busy, () => submit(context, move)));
    } else if (ui.mode === "refill") {
      prompt(doc, controls, `袋中 ${state.bag_count} 枚从中心沿螺旋填回空格；补完对手拿 1 张特权券，本回合不能再用特权券。`);
      buttons.append(actionButton(doc, "算了", false, false, cancel),
        actionButton(doc, "补盘", true, ui.busy, () => submit(context, {action: "refill"})));
    } else if (ui.mode === "reserve") {
      const target = ui.card !== null ? {card_id: ui.card} : ui.deck !== null ? {level: ui.deck} : null;
      const options = target ? reserveOptions(context, target) : [];
      const move = options.find((a) => sameCell(a.gold, ui.gold)) || null;
      const what = ui.card !== null ? `#${ui.card}` : ui.deck !== null ? `${LEVELS[ui.deck]}牌堆盲抽 1 张` : "";
      prompt(doc, controls, target
        ? `拿 ${cellName(ui.gold)} 的金，保留 ${what}`
        : `拿 ${cellName(ui.gold)} 的金：再点场上一张卡或左侧牌堆`);
      buttons.append(actionButton(doc, "撤销", false, false, cancel),
        actionButton(doc, "确认保留", true, !move || ui.busy, () => submit(context, move)));
    } else if (ui.mode === "take" && ui.sel.length) {
      const move = takeFor(context, ui.sel);
      const colors = ui.sel.map((c) => state.board[c[0]][c[1]]);
      const gift = (colors.length === 3 && new Set(colors).size === 1) || colors.filter((c) => c === "pearl").length === 2;
      prompt(doc, controls, move
        ? `拿 ${colors.map((c) => LABELS[c]).join("·")}${gift ? "（这样拿对手会得 1 张特权券）" : ""}`
        : "要在同一条横、竖或斜线上紧挨着，中间不能隔空格或金", !move);
      buttons.append(actionButton(doc, "撤销", false, false, cancel),
        actionButton(doc, "拿！", true, !move || ui.busy, () => submit(context, move)));
    } else {
      const canPrivilege = actionsOf(context, "use_privilege").length > 0;
      const canRefill = actionsOf(context, "refill").length > 0;
      const canPass = actionsOf(context, "pass").length > 0;
      const mandatory = legal(context).some((a) => ["take", "reserve", "buy"].includes(a.action));
      prompt(doc, controls, !mandatory && canRefill
        ? "拿、保留、买都做不了：先补盘"
        : canPass
          ? "什么都做不了，只能跳过"
          : state.refilled
            ? "补过盘了：拿宝石、拿金保留或买卡"
            : "点连成一线的 1–3 枚宝石 · 点金保留 · 点卡查看或购买", !mandatory && canRefill);
      const me = (state.players || {})[viewerId(context)] || {};
      if (canPrivilege) {
        buttons.appendChild(actionButton(doc, `特权券 ×${me.privileges || 0}`, false, ui.busy, () => {
          reset(context); ui.mode = "privilege"; rerender(context);
        }));
      }
      if (canRefill) {
        buttons.appendChild(actionButton(doc, "补盘", !mandatory, ui.busy, () => {
          reset(context); ui.mode = "refill"; rerender(context);
        }));
      }
      if (canPass) buttons.appendChild(actionButton(doc, "跳过", true, ui.busy, () => submit(context, {action: "pass"})));
    }
    if (buttons.children.length) controls.appendChild(buttons);
  }

  function renderControls(context) {
    const doc = context.board.ownerDocument;
    const ui = draft(context);
    const controls = el(doc, "div", "gm-controls");
    if (ui.mode === "card" && cardDetail(context, controls)) {
      context.controls.appendChild(controls);
      return;
    }
    if (isTerminal(context)) {
      prompt(doc, controls, "对局结束。可以点场上的卡看看。");
    } else if (!myTurn(context)) {
      const pending = context.state.pending;
      prompt(doc, controls, pending
        ? `${nameOf(context, context.room.current_player_id)} 正在结算卡牌能力…`
        : `等 ${nameOf(context, context.room.current_player_id)} 出手；可以先点卡看看`);
    } else if (context.state.pending) {
      pendingControls(context, controls);
    } else {
      optionalControls(context, controls);
    }
    if (ui.error) prompt(doc, controls, ui.error, true);
    context.controls.appendChild(controls);
  }

  window.DuelGameUI.register("gem_merchant", {
    usesStandardMoveConfirmation: false,
    ownsPrivateStatePresentation: true,
    participantPresentation: "embedded",
    renderBoard,
    renderControls,
  });
}());

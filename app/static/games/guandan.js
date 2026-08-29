(function registerGuandanRenderer() {
  "use strict";

  const STYLE_ID = "duel-game-guandan-styles";
  const STYLE_HREF = "/static/games/guandan.css?v=0.1.0";
  const SUITS = {
    spades: "\u2660\uFE0E",
    hearts: "\u2665\uFE0E",
    clubs: "\u2663\uFE0E",
    diamonds: "\u2666\uFE0E",
  };
  const SUIT_NAMES = {
    spades: "黑桃", hearts: "红桃", clubs: "梅花", diamonds: "方块",
  };
  const TEAM_NAMES = {A: "甲队", B: "乙队"};

  function ensureStylesheet(documentRef) {
    if (!documentRef || !documentRef.head) return null;
    const existing = documentRef.getElementById(STYLE_ID);
    if (existing) return existing;
    const link = documentRef.createElement("link");
    link.id = STYLE_ID;
    link.rel = "stylesheet";
    link.href = STYLE_HREF;
    link.dataset.duelGameStyle = "guandan";
    documentRef.head.appendChild(link);
    return link;
  }

  function element(documentRef, tag, className, text = null) {
    const node = documentRef.createElement(tag);
    if (className) node.className = className;
    if (text !== null) node.textContent = String(text);
    return node;
  }

  function participant(context, playerId) {
    return (context.participants || []).find((item) => item.player_id === playerId) || null;
  }

  function playerName(context, playerId) {
    const item = participant(context, playerId);
    return (item && (item.display_name || item.player_id)) || playerId || "玩家";
  }

  function cardName(card) {
    if (!card) return "未知牌";
    if (card.rank === "black_joker") return "小王";
    if (card.rank === "red_joker") return "大王";
    return `${SUIT_NAMES[card.suit] || ""}${card.rank || ""}`;
  }

  function cardNode(documentRef, card, options = {}) {
    const interactive = Boolean(options.interactive);
    const node = element(
      documentRef,
      interactive ? "button" : "span",
      `guandan-card suit-${card.suit || "joker"}${card.wild ? " wild" : ""}`
    );
    if (interactive) node.type = "button";
    node.dataset.cardId = String(card.id || "");
    node.classList.toggle("selected", Boolean(options.selected));
    node.classList.toggle("selectable", Boolean(options.selectable));
    node.setAttribute("aria-label", `${cardName(card)}${card.wild ? "，逢人配" : ""}`);
    if (interactive) {
      node.setAttribute("aria-pressed", String(Boolean(options.selected)));
      node.disabled = Boolean(options.disabled);
    } else {
      node.setAttribute("role", "img");
    }

    const corner = element(documentRef, "span", "guandan-card-corner");
    const rankText = card.rank === "black_joker"
      ? "小" : card.rank === "red_joker" ? "大" : card.rank;
    corner.append(
      element(documentRef, "strong", "guandan-card-rank", rankText),
      element(
        documentRef,
        "span",
        "guandan-card-suit",
        card.suit === "joker" ? "王" : (SUITS[card.suit] || "")
      )
    );
    const center = element(
      documentRef,
      "span",
      "guandan-card-center",
      card.suit === "joker"
        ? (card.rank === "red_joker" ? "大王" : "小王")
        : (SUITS[card.suit] || "")
    );
    node.append(corner, center);
    if (card.wild) node.appendChild(element(documentRef, "span", "guandan-wild-tag", "配"));
    return node;
  }

  function legalActions(context) {
    return Array.isArray(context.legalActions)
      ? context.legalActions.filter((item) => item && item.action_id)
      : [];
  }

  function cardActions(context) {
    return legalActions(context).filter((item) => (
      item.kind !== "pass" && Array.isArray(item.card_ids) && item.card_ids.length
    ));
  }

  function selectedIds(context) {
    const handIds = new Set(
      ((context.privateState && context.privateState.hand) || []).map((card) => String(card.id))
    );
    const selected = Array.isArray(context.uiState.selectedCardIds)
      ? context.uiState.selectedCardIds.map(String).filter((id) => handIds.has(id))
      : [];
    context.uiState.selectedCardIds = [...new Set(selected)];
    return context.uiState.selectedCardIds;
  }

  function cardSignature(card) {
    return `${card && card.suit || ""}:${card && card.rank || ""}`;
  }

  function signatureForIds(context, ids) {
    const byId = new Map(
      ((context.privateState && context.privateState.hand) || []).map(
        (card) => [String(card.id), card]
      )
    );
    return [...ids].map((id) => cardSignature(byId.get(String(id)))).sort().join("|");
  }

  function exactActions(context) {
    const selected = signatureForIds(context, selectedIds(context));
    if (!selected) return [];
    return cardActions(context).filter(
      (action) => signatureForIds(context, action.card_ids) === selected
    );
  }

  function chosenAction(context) {
    const exact = exactActions(context);
    if (!exact.length) return null;
    const chosenId = String(context.uiState.chosenActionId || "");
    const chosen = exact.find((action) => action.action_id === chosenId) || exact[0];
    context.uiState.chosenActionId = chosen.action_id;
    return chosen;
  }

  function setChosenCards(context, action) {
    context.uiState.selectedCardIds = action ? action.card_ids.map(String) : [];
    context.uiState.chosenActionId = action ? action.action_id : null;
  }

  function relativePosition(context, seatIndex) {
    const viewerSeat = Number(context.viewer && context.viewer.seat);
    const difference = ((Number(seatIndex) - viewerSeat) % 4 + 4) % 4;
    return ["bottom", "right", "top", "left"][difference] || "bottom";
  }

  function renderSeat(documentRef, context, item) {
    const playerId = item.player_id;
    const team = context.state.teams && context.state.teams[playerId] || "?";
    const metadata = item.game_metadata || {};
    const count = Number((context.state.hand_counts || {})[playerId] || 0);
    const position = relativePosition(context, item.seat_index);
    const seat = element(
      documentRef,
      "article",
      `guandan-seat position-${position} team-${team}`
    );
    seat.dataset.playerId = playerId;
    seat.dataset.seatIndex = String(item.seat_index);
    seat.classList.toggle("current", context.room.current_player_id === playerId);
    seat.classList.toggle("viewer", context.viewer && context.viewer.player_id === playerId);
    const passIds = new Set((context.state.current_trick || {}).pass_player_ids || []);
    seat.classList.toggle("passed", passIds.has(playerId));

    const identity = element(documentRef, "div", "guandan-seat-identity");
    const avatar = element(documentRef, "span", "guandan-avatar");
    context.helpers.renderParticipantAvatar(avatar, item);
    const words = element(documentRef, "div", "guandan-seat-words");
    const title = element(documentRef, "div", "guandan-seat-title");
    title.append(
      element(documentRef, "strong", "guandan-seat-name", item.display_name || playerId),
      element(documentRef, "span", "guandan-team-chip", TEAM_NAMES[team] || team)
    );
    const relation = position === "top"
      ? "对家" : position === "bottom" ? "我" : "对手";
    const status = passIds.has(playerId)
      ? "已过" : (metadata.deal_status || (context.room.current_player_id === playerId ? "行动中" : "在局"));
    words.append(
      title,
      element(
        documentRef,
        "span",
        "guandan-seat-meta",
        `${relation} · ${metadata.level || (context.state.team_levels || {})[team] || "2"} 级 · ${status}`
      )
    );
    identity.append(avatar, words);
    const handInfo = element(documentRef, "div", "guandan-seat-hand");
    const backs = element(documentRef, "span", "guandan-mini-backs");
    for (let index = 0; index < Math.min(count, 4); index += 1) {
      backs.appendChild(element(documentRef, "i", "guandan-mini-back"));
    }
    handInfo.append(backs, element(documentRef, "b", "guandan-seat-count", `${count} 张`));
    seat.append(identity, handInfo);
    return seat;
  }

  function renderTableStatus(documentRef, context, table) {
    const state = context.state;
    const header = element(documentRef, "header", "guandan-table-status");
    const title = element(documentRef, "div", "guandan-brand");
    title.append(
      element(documentRef, "span", "guandan-brand-mark", "掼"),
      element(documentRef, "strong", "", "掼蛋")
    );
    const metrics = element(documentRef, "div", "guandan-metrics");
    metrics.append(
      element(documentRef, "span", "guandan-metric", `第 ${state.deal_number || 0} 副`),
      element(documentRef, "span", "guandan-metric level", `本副级牌 ${state.level_rank || "2"}`),
      element(
        documentRef,
        "span",
        "guandan-metric teams",
        `甲 ${state.team_levels && state.team_levels.A || "2"} · 乙 ${state.team_levels && state.team_levels.B || "2"}`
      ),
      element(documentRef, "span", "guandan-metric phase", state.phase_label || state.phase)
    );
    header.append(title, metrics);
    table.appendChild(header);
  }

  function renderTribute(documentRef, context, table) {
    const tribute = context.state.tribute || {};
    if (!tribute.status || tribute.status === "none") return;
    const band = element(documentRef, "section", "guandan-tribute-band");
    band.dataset.status = tribute.status;
    const heading = tribute.status === "countered"
      ? "抗贡成立" : tribute.status === "complete" ? "贡还完成" : "贡还阶段";
    band.appendChild(element(documentRef, "strong", "", heading));
    const details = [];
    if (tribute.mode && tribute.mode !== "none") {
      details.push(tribute.mode === "double" ? "双贡" : "单贡");
    }
    (tribute.tributes || []).forEach((item) => {
      details.push(`${playerName(context, item.payer_player_id)} 贡 ${cardName(item.card)}`);
    });
    (tribute.returns || []).forEach((item) => {
      details.push(`${playerName(context, item.receiver_player_id)} 还 ${cardName(item.card)}`);
    });
    if (tribute.countered) details.push("由上一副头游领出");
    band.appendChild(element(documentRef, "span", "", details.join(" · ") || "等待规则核心处理"));
    table.appendChild(band);
  }

  function renderCenter(documentRef, context, table) {
    const trick = context.state.current_trick || {};
    const play = trick.last_play || null;
    const center = element(documentRef, "section", "guandan-center");
    center.setAttribute("aria-label", `第 ${trick.number || 1} 墩公开出牌区`);
    const heading = element(documentRef, "div", "guandan-center-heading");
    if (play) {
      heading.append(
        element(documentRef, "strong", "", play.pattern && play.pattern.label || "出牌"),
        element(documentRef, "span", "", `${playerName(context, play.player_id)} · ${play.cards.length} 张`)
      );
    } else {
      heading.append(
        element(documentRef, "strong", "", trick.wind_follow ? "接风" : "等待领出"),
        element(documentRef, "span", "", `${playerName(context, trick.leader_player_id)} 可领任意合法牌`)
      );
    }
    const cards = element(documentRef, "div", "guandan-center-cards");
    if (play && Array.isArray(play.cards)) {
      play.cards.forEach((card, index) => {
        const node = cardNode(documentRef, card);
        node.style.setProperty("--center-index", index);
        cards.appendChild(node);
      });
    } else {
      cards.appendChild(element(documentRef, "span", "guandan-center-empty", "本墩尚无公开出牌"));
    }
    const passes = element(documentRef, "div", "guandan-pass-row");
    const passIds = Array.isArray(trick.pass_player_ids) ? trick.pass_player_ids : [];
    if (passIds.length) {
      passIds.forEach((playerId) => passes.appendChild(
        element(documentRef, "span", "guandan-pass-chip", `${playerName(context, playerId)} 已过`)
      ));
    } else {
      passes.appendChild(element(documentRef, "span", "guandan-pass-none", "无人过牌"));
    }
    center.append(heading, cards, passes);
    table.appendChild(center);
  }

  function renderHand(documentRef, context, table) {
    const hand = context.privateState && Array.isArray(context.privateState.hand)
      ? context.privateState.hand : [];
    const selected = selectedIds(context);
    const actions = cardActions(context);
    const byId = new Map(hand.map((card) => [String(card.id), card]));
    const selectable = new Set(actions.flatMap((action) => action.card_ids.map(
      (id) => cardSignature(byId.get(String(id)))
    )));
    const singleChoicePhase = ["tribute", "return_tribute"].includes(context.state.phase);
    const zone = element(documentRef, "section", "guandan-hand-zone");
    const heading = element(documentRef, "div", "guandan-hand-heading");
    heading.append(
      element(documentRef, "strong", "", "我的手牌"),
      element(
        documentRef,
        "span",
        "",
        `${hand.length} 张 · ${singleChoicePhase ? "选择 1 张" : "横向滑动，多选出牌"}`
      )
    );
    const scroller = element(documentRef, "div", "guandan-hand-scroll");
    scroller.setAttribute("role", "group");
    scroller.setAttribute("aria-label", "我的私密手牌，可横向滚动选择");
    hand.forEach((card, index) => {
      const cardId = String(card.id);
      const isSelected = selected.includes(cardId);
      // The two deck copies of an identical suit/rank are rule-equivalent.
      // The core publishes one canonical action_id; either visible copy may
      // select that action without the client inventing a card combination.
      const canSelect = selectable.has(cardSignature(card));
      const node = cardNode(documentRef, card, {
        interactive: true,
        selected: isSelected,
        selectable: canSelect,
        disabled: !context.canMove || !canSelect || Boolean(context.uiState.submitting),
      });
      const middle = (hand.length - 1) / 2;
      const angle = Math.max(-5, Math.min(5, (index - middle) * 0.42));
      node.style.setProperty("--fan-angle", `${angle}deg`);
      node.style.setProperty("--hand-index", index);
      node.addEventListener("click", () => {
        if (!context.helpers.canMove() || !canSelect || context.uiState.submitting) return;
        const current = selectedIds(context);
        context.uiState.selectedCardIds = singleChoicePhase
          ? (isSelected ? [] : [cardId])
          : (isSelected ? current.filter((id) => id !== cardId) : [...current, cardId]);
        context.uiState.chosenActionId = null;
        context.helpers.rerender();
      });
      scroller.appendChild(node);
    });
    if (!hand.length) {
      scroller.appendChild(element(documentRef, "span", "guandan-empty-hand", "本副手牌已出完"));
    }
    zone.append(heading, scroller);
    table.appendChild(zone);
  }

  function renderBoard(context) {
    const documentRef = context.board.ownerDocument || window.document;
    ensureStylesheet(documentRef);
    context.helpers.setBoardLayout({
      rows: 1, cols: 1, large: true, ariaLabel: "掼蛋四人两队牌桌",
    });
    const game = element(documentRef, "div", "guandan-game");
    const table = element(documentRef, "div", "guandan-table");
    renderTableStatus(documentRef, context, table);
    (context.participants || []).forEach((item) => {
      table.appendChild(renderSeat(documentRef, context, item));
    });
    renderTribute(documentRef, context, table);
    renderCenter(documentRef, context, table);
    renderHand(documentRef, context, table);
    game.appendChild(table);
    context.board.appendChild(game);
    return true;
  }

  function renderControls(context) {
    const documentRef = context.controls.ownerDocument || window.document;
    ensureStylesheet(documentRef);
    const actions = cardActions(context);
    const exact = exactActions(context);
    const selected = selectedIds(context);
    const chosen = chosenAction(context);
    const pass = legalActions(context).find((item) => item.kind === "pass") || null;
    const panel = element(documentRef, "div", "guandan-controls");
    panel.setAttribute("aria-busy", String(Boolean(context.uiState.submitting)));
    const status = element(documentRef, "div", "guandan-selection-status");
    const summary = element(documentRef, "strong", "", "");
    const detail = element(documentRef, "span", "", "");
    if (chosen) {
      summary.textContent = `已选：${chosen.label}`;
      detail.textContent = `${chosen.card_ids.length} 张 · 权威动作 ${chosen.action_id}`;
    } else if (selected.length) {
      summary.textContent = `已选 ${selected.length} 张`;
      detail.textContent = "当前选牌尚未对应规则核心合法动作";
    } else if (context.canMove) {
      summary.textContent = context.state.phase_label || "请选择动作";
      detail.textContent = `${legalActions(context).length} 个规则核心合法 action_id`;
    } else {
      summary.textContent = "等待其他玩家";
      detail.textContent = "轮到你时会显示本人权威合法动作";
    }
    status.append(summary, detail);

    if (exact.length > 1) {
      const interpretation = element(documentRef, "label", "guandan-interpretation", "逢人配解释");
      const select = element(documentRef, "select", "");
      exact.forEach((action) => {
        const option = element(documentRef, "option", "", `${action.label} · ${action.pattern_type || action.kind}`);
        option.value = action.action_id;
        select.appendChild(option);
      });
      select.value = chosen && chosen.action_id || exact[0].action_id;
      select.addEventListener("change", () => {
        context.uiState.chosenActionId = select.value;
      });
      interpretation.appendChild(select);
      status.appendChild(interpretation);
    }

    const buttons = element(documentRef, "div", "guandan-action-buttons");
    const hint = element(documentRef, "button", "guandan-hint-button", "提示");
    hint.type = "button";
    hint.disabled = !context.canMove || !actions.length || Boolean(context.uiState.submitting);
    hint.addEventListener("click", () => {
      if (!context.helpers.canMove() || !actions.length) return;
      setChosenCards(context, actions[0]);
      context.helpers.rerender();
    });
    const play = element(
      documentRef,
      "button",
      "guandan-play-button",
      context.state.phase === "tribute"
        ? "进贡" : context.state.phase === "return_tribute" ? "还贡" : "出牌"
    );
    play.type = "button";
    play.disabled = !context.canMove || !chosen || Boolean(context.uiState.submitting);
    play.addEventListener("click", async () => {
      const action = chosenAction(context);
      if (!context.helpers.canMove() || !action || context.uiState.submitting) return;
      context.uiState.submitting = true;
      context.helpers.rerender();
      const submitted = await context.helpers.submitMove({
        action: "act", action_id: action.action_id,
      });
      if (!submitted) {
        context.uiState.submitting = false;
        context.helpers.rerender();
      }
    });
    const passButton = element(documentRef, "button", "guandan-pass-button", "过");
    passButton.type = "button";
    passButton.disabled = !context.canMove || !pass || Boolean(context.uiState.submitting);
    passButton.setAttribute("aria-label", pass ? "本墩过牌" : "当前阶段不能过牌");
    passButton.addEventListener("click", async () => {
      if (!context.helpers.canMove() || !pass || context.uiState.submitting) return;
      context.uiState.submitting = true;
      context.helpers.rerender();
      const submitted = await context.helpers.submitMove({
        action: "act", action_id: pass.action_id,
      });
      if (!submitted) {
        context.uiState.submitting = false;
        context.helpers.rerender();
      }
    });
    buttons.append(hint, play, passButton);
    panel.append(status, buttons);
    context.controls.appendChild(panel);
    return true;
  }

  window.DuelGameUI.register("guandan", Object.freeze({
    participantPresentation: "embedded",
    usesStandardMoveConfirmation: false,
    ownsPrivateStatePresentation: true,
    renderBoard,
    renderControls,
  }));
}());

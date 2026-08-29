(function registerDoudizhuRenderer() {
  "use strict";

  const STYLE_ID = "duel-game-doudizhu-styles";
  const STYLE_HREF = "/static/games/doudizhu.css?v=0.1.0";
  const SUIT_TEXT = {
    spades: "\u2660\uFE0E",
    hearts: "\u2665\uFE0E",
    clubs: "\u2663\uFE0E",
    diamonds: "\u2666\uFE0E",
  };
  const SUIT_LABELS = {
    spades: "黑桃",
    hearts: "红桃",
    clubs: "梅花",
    diamonds: "方块",
  };

  function ensureStylesheet(documentRef) {
    if (!documentRef || !documentRef.head) return null;
    const existing = typeof documentRef.getElementById === "function"
      ? documentRef.getElementById(STYLE_ID)
      : null;
    if (existing) return existing;
    const link = documentRef.createElement("link");
    link.id = STYLE_ID;
    link.rel = "stylesheet";
    link.href = STYLE_HREF;
    link.dataset.duelGameStyle = "doudizhu";
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

  function participantName(context, playerId) {
    const value = participant(context, playerId);
    return value && (value.display_name || value.player_id) || "玩家";
  }

  function initials(name) {
    return [...String(name || "玩")].slice(-2).join("");
  }

  function roleFor(context, playerId) {
    return (context.state.roles_by_player || {})[playerId] || "unassigned";
  }

  function roleLabel(role) {
    return role === "landlord" ? "地主" : role === "farmer" ? "农民" : "待定";
  }

  function farmerPartnerId(context, playerId) {
    if (roleFor(context, playerId) !== "farmer") return null;
    return Object.keys(context.state.roles_by_player || {}).find(
      (candidate) => candidate !== playerId && roleFor(context, candidate) === "farmer"
    ) || null;
  }

  function cardName(card) {
    if (!card) return "未知牌";
    if (card.rank === "small_joker") return "小王";
    if (card.rank === "big_joker") return "大王";
    return `${SUIT_LABELS[card.suit] || ""}${card.rank || ""}`;
  }

  function createCard(documentRef, card, options = {}) {
    const interactive = Boolean(options.interactive);
    const node = element(
      documentRef,
      interactive ? "button" : "span",
      `doudizhu-card suit-${card.suit || "joker"}`
    );
    if (interactive) node.type = "button";
    node.dataset.cardId = String(card.id || "");
    node.classList.toggle("selected", Boolean(options.selected));
    node.classList.toggle("selectable", Boolean(options.selectable));
    node.classList.toggle("joker", card.suit === "joker");
    node.setAttribute("aria-label", cardName(card));
    if (interactive) {
      node.setAttribute("aria-pressed", String(Boolean(options.selected)));
      node.disabled = Boolean(options.disabled);
    } else {
      node.setAttribute("role", "img");
    }
    const corner = element(documentRef, "span", "doudizhu-card-corner");
    corner.append(
      element(
        documentRef,
        "strong",
        "doudizhu-card-rank",
        card.rank === "small_joker" ? "小" : card.rank === "big_joker" ? "大" : card.rank
      ),
      element(
        documentRef,
        "span",
        "doudizhu-card-suit",
        card.suit === "joker" ? "王" : (SUIT_TEXT[card.suit] || "")
      )
    );
    node.append(
      corner,
      element(
        documentRef,
        "span",
        "doudizhu-card-center",
        card.suit === "joker"
          ? (card.rank === "big_joker" ? "大王" : "小王")
          : (SUIT_TEXT[card.suit] || "")
      )
    );
    return node;
  }

  function playActions(context) {
    return (Array.isArray(context.legalActions) ? context.legalActions : [])
      .filter((action) => action && action.action === "play" && Array.isArray(action.card_ids));
  }

  function canonicalIds(ids) {
    return [...ids].map(String).sort().join("|");
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

  function matchingSelectedActions(context) {
    const key = canonicalIds(selectedIds(context));
    if (!key) return [];
    return playActions(context).filter((action) => canonicalIds(action.card_ids) === key);
  }

  function exactSelectedAction(context) {
    const matches = matchingSelectedActions(context);
    if (matches.length === 1) return matches[0];
    return matches.find((action) => action.action_id === context.uiState.selectedActionId) || null;
  }

  function isSubset(subset, values) {
    const source = new Set(values.map(String));
    return subset.every((item) => source.has(String(item)));
  }

  function renderSeat(documentRef, context, value, passIds) {
    const playerId = value.player_id;
    const name = value.display_name || playerId;
    const role = roleFor(context, playerId);
    const count = Number((context.state.hand_counts || {})[playerId] || 0);
    const seat = element(documentRef, "article", `doudizhu-seat role-${role}`);
    seat.dataset.playerId = playerId;
    seat.classList.toggle("current", context.room.current_player_id === playerId);
    seat.classList.toggle("passed", passIds.has(playerId));
    const avatar = element(documentRef, "span", "doudizhu-avatar", initials(name));
    avatar.setAttribute("aria-hidden", "true");
    const copy = element(documentRef, "div", "doudizhu-seat-copy");
    const heading = element(documentRef, "div", "doudizhu-seat-heading");
    heading.append(
      element(documentRef, "strong", "doudizhu-seat-name", name),
      element(documentRef, "b", "doudizhu-role-badge", roleLabel(role))
    );
    let stateText = `${count} 张`;
    if (passIds.has(playerId)) stateText += " · 已过";
    else if (context.room.current_player_id === playerId) stateText += " · 行动中";
    const partnerId = farmerPartnerId(context, playerId);
    if (partnerId) stateText += ` · 对家 ${participantName(context, partnerId)}`;
    copy.append(heading, element(documentRef, "span", "doudizhu-seat-state", stateText));
    seat.append(avatar, copy);
    return seat;
  }

  function renderBottom(documentRef, context) {
    const zone = element(documentRef, "section", "doudizhu-bottom");
    zone.appendChild(element(documentRef, "strong", "doudizhu-bottom-label", "底牌"));
    const cards = element(documentRef, "div", "doudizhu-bottom-cards");
    if (context.state.bottom_revealed && Array.isArray(context.state.bottom_cards)) {
      context.state.bottom_cards.forEach((card) => cards.appendChild(createCard(documentRef, card)));
    } else {
      for (let index = 0; index < Number(context.state.bottom_card_count || 3); index += 1) {
        const back = element(documentRef, "span", "doudizhu-card-back");
        back.setAttribute("aria-hidden", "true");
        cards.appendChild(back);
      }
      cards.setAttribute("aria-label", "三张未公开底牌");
    }
    zone.appendChild(cards);
    return zone;
  }

  function renderCenter(documentRef, context, shell) {
    const trick = context.state.current_trick || {};
    const lastPlay = trick.last_play || null;
    const center = element(documentRef, "section", "doudizhu-center");
    center.appendChild(renderBottom(documentRef, context));
    const table = element(documentRef, "div", "doudizhu-trick");
    const heading = element(documentRef, "div", "doudizhu-trick-heading");
    if (context.state.flow && context.state.flow.phase === "bidding") {
      const highest = Number((context.state.bidding || {}).highest_score || 0);
      heading.append(
        element(documentRef, "strong", "", "叫分阶段"),
        element(documentRef, "span", "", highest ? `当前最高 ${highest} 分` : "尚无人叫分")
      );
    } else if (lastPlay) {
      heading.append(
        element(documentRef, "strong", "", lastPlay.pattern && lastPlay.pattern.label || "出牌"),
        element(documentRef, "span", "", `${participantName(context, lastPlay.player_id)} · ${lastPlay.cards.length} 张`)
      );
    } else {
      heading.append(
        element(documentRef, "strong", "", "等待领出"),
        element(documentRef, "span", "", `${participantName(context, trick.leader_player_id)} 可出任意合法组合`)
      );
    }
    const cards = element(documentRef, "div", "doudizhu-trick-cards");
    if (lastPlay && Array.isArray(lastPlay.cards)) {
      lastPlay.cards.forEach((card) => cards.appendChild(createCard(documentRef, card)));
    } else {
      cards.appendChild(element(documentRef, "span", "doudizhu-empty-trick", "桌面暂无组合"));
    }
    const passes = element(documentRef, "div", "doudizhu-pass-line");
    const passIds = Array.isArray(trick.pass_player_ids) ? trick.pass_player_ids : [];
    if (passIds.length) {
      passIds.forEach((playerId) => passes.appendChild(
        element(documentRef, "span", "doudizhu-pass-chip", `${participantName(context, playerId)} 已过`)
      ));
    } else {
      passes.appendChild(element(documentRef, "span", "doudizhu-no-pass", "本墩暂无过牌"));
    }
    table.append(heading, cards, passes);
    center.appendChild(table);
    shell.appendChild(center);
  }

  function renderHand(documentRef, context, shell) {
    const hand = context.privateState && Array.isArray(context.privateState.hand)
      ? context.privateState.hand
      : [];
    const selected = selectedIds(context);
    const legal = playActions(context);
    const selectableIds = new Set(legal.flatMap((action) => action.card_ids.map(String)));
    const zone = element(documentRef, "section", "doudizhu-hand-zone");
    const label = element(documentRef, "div", "doudizhu-hand-label");
    const viewerId = context.viewer && context.viewer.player_id;
    const partnerId = farmerPartnerId(context, viewerId);
    label.append(
      element(
        documentRef,
        "strong",
        "",
        `我的手牌 · ${roleLabel(roleFor(context, viewerId))}`
          + (partnerId ? ` · 对家 ${participantName(context, partnerId)}` : "")
      ),
      element(documentRef, "span", "", `${hand.length} 张 · 横向滚动选择`)
    );
    const scroller = element(documentRef, "div", "doudizhu-hand-scroll");
    scroller.setAttribute("role", "group");
    scroller.setAttribute("aria-label", "我的私密手牌，可横向滚动并多选");
    hand.forEach((card, index) => {
      const cardId = String(card.id);
      const selectable = selectableIds.has(cardId);
      const cardNode = createCard(documentRef, card, {
        interactive: true,
        selected: selected.includes(cardId),
        selectable,
        disabled: !context.canMove || !selectable || Boolean(context.uiState.submitting),
      });
      cardNode.style.setProperty("--hand-index", index);
      cardNode.addEventListener("click", () => {
        if (!context.helpers.canMove() || !selectable || context.uiState.submitting) return;
        const current = selectedIds(context);
        context.uiState.selectedCardIds = current.includes(cardId)
          ? current.filter((id) => id !== cardId)
          : [...current, cardId];
        context.uiState.selectedActionId = null;
        context.helpers.rerender();
      });
      scroller.appendChild(cardNode);
    });
    if (!hand.length) {
      scroller.appendChild(element(documentRef, "span", "doudizhu-empty-hand", "等待开局或手牌已出完"));
    }
    zone.append(label, scroller);
    shell.appendChild(zone);
  }

  function renderBoard(context) {
    const documentRef = context.board.ownerDocument || window.document;
    ensureStylesheet(documentRef);
    context.helpers.setBoardLayout({rows: 1, cols: 1, large: true, ariaLabel: "斗地主三人中文牌桌"});
    const board = context.board;
    board.dataset.multiplier = String(context.state.multiplier || 1);
    const game = element(documentRef, "div", "doudizhu-game");
    const topbar = element(documentRef, "header", "doudizhu-topbar");
    const title = element(documentRef, "div", "doudizhu-title");
    title.append(
      element(documentRef, "span", "doudizhu-title-mark", "斗"),
      element(documentRef, "strong", "", "斗地主")
    );
    const metrics = element(documentRef, "div", "doudizhu-metrics");
    metrics.append(
      element(documentRef, "span", "doudizhu-metric multiplier", `倍率 ${context.state.multiplier || 1} 倍`),
      element(documentRef, "span", "doudizhu-metric", `炸弹 ${context.state.bomb_count || 0}`)
    );
    topbar.append(title, metrics);

    const shell = element(documentRef, "div", "doudizhu-table-shell");
    const viewerId = context.viewer && context.viewer.player_id;
    const passIds = new Set(((context.state.current_trick || {}).pass_player_ids) || []);
    const opponents = element(documentRef, "div", "doudizhu-opponents");
    (context.participants || [])
      .filter((item) => item.player_id !== viewerId)
      .forEach((item) => opponents.appendChild(renderSeat(documentRef, context, item, passIds)));
    shell.appendChild(opponents);
    renderCenter(documentRef, context, shell);
    renderHand(documentRef, context, shell);
    game.append(topbar, shell);
    board.appendChild(game);
    return true;
  }

  function submitAction(context, action) {
    if (!context.helpers.canMove() || !action || context.uiState.submitting) return;
    context.uiState.submitting = true;
    context.helpers.rerender();
    Promise.resolve(context.helpers.submitMove({...action})).then((submitted) => {
      if (!submitted) {
        context.uiState.submitting = false;
        context.helpers.rerender();
      }
    });
  }

  function renderBidControls(documentRef, context, panel) {
    const actions = (context.legalActions || []).filter((action) => action.action === "bid");
    const status = element(documentRef, "div", "doudizhu-selection-status");
    status.setAttribute("role", "status");
    status.setAttribute("aria-live", "polite");
    status.append(
      element(documentRef, "strong", "", context.canMove ? "请选择叫分" : "等待其他玩家叫分"),
      element(documentRef, "span", "", "每人仅叫一次，分数必须高于当前最高分")
    );
    const buttons = element(documentRef, "div", "doudizhu-action-buttons bid-buttons");
    actions.forEach((action) => {
      const button = element(documentRef, "button", "doudizhu-bid-button", action.label);
      button.type = "button";
      button.disabled = !context.canMove || Boolean(context.uiState.submitting);
      button.dataset.actionId = action.action_id;
      button.addEventListener("click", () => submitAction(context, action));
      buttons.appendChild(button);
    });
    panel.append(status, buttons);
  }

  function renderPlayControls(documentRef, context, panel) {
    const selected = selectedIds(context);
    const matches = matchingSelectedActions(context);
    const exact = exactSelectedAction(context);
    const legal = playActions(context);
    const possible = selected.length
      ? legal.filter((action) => isSubset(selected, action.card_ids))
      : legal;
    const passAction = (context.legalActions || []).find((action) => action.action === "pass");
    const status = element(documentRef, "div", "doudizhu-selection-status");
    status.setAttribute("role", "status");
    status.setAttribute("aria-live", "polite");
    const summary = element(documentRef, "strong", "doudizhu-selection-title");
    const detail = element(documentRef, "span", "doudizhu-selection-detail");
    if (exact) {
      summary.textContent = `已选：${exact.pattern_label}`;
      detail.textContent = `${exact.card_ids.length} 张，可出牌`;
    } else if (matches.length > 1) {
      summary.textContent = "该组合有多种牌型解释";
      detail.textContent = "请选择服务端发布的牌型";
    } else if (selected.length && possible.length) {
      summary.textContent = `已选 ${selected.length} 张`;
      detail.textContent = `还可补全为 ${possible.length} 项权威组合`;
    } else if (selected.length) {
      summary.textContent = `已选 ${selected.length} 张`;
      detail.textContent = "当前组合不在服务端合法动作中";
    } else {
      summary.textContent = context.canMove ? "请选择手牌" : "等待其他玩家";
      detail.textContent = context.canMove ? `${legal.length} 项权威组合` : "轮到你时才能出牌";
    }
    status.append(summary, detail);
    panel.appendChild(status);

    if (matches.length > 1) {
      const choices = element(documentRef, "div", "doudizhu-pattern-choices");
      matches.forEach((action) => {
        const choice = element(
          documentRef,
          "button",
          "doudizhu-pattern-choice",
          `${action.pattern_label} · 主牌 ${action.main_rank}`
        );
        choice.type = "button";
        choice.classList.toggle("selected", context.uiState.selectedActionId === action.action_id);
        choice.addEventListener("click", () => {
          context.uiState.selectedActionId = action.action_id;
          context.helpers.rerender();
        });
        choices.appendChild(choice);
      });
      panel.appendChild(choices);
    }

    const buttons = element(documentRef, "div", "doudizhu-action-buttons");
    const playButton = element(documentRef, "button", "doudizhu-play-button", "出牌");
    playButton.type = "button";
    playButton.disabled = !context.canMove || !exact || Boolean(context.uiState.submitting);
    playButton.addEventListener("click", () => submitAction(context, exactSelectedAction(context)));
    const passButton = element(documentRef, "button", "doudizhu-pass-button", "过");
    passButton.type = "button";
    passButton.disabled = !context.canMove || !passAction || Boolean(context.uiState.submitting);
    passButton.setAttribute("aria-label", passAction ? "本轮过牌" : "引牌时不能过牌");
    passButton.addEventListener("click", () => submitAction(context, passAction));
    buttons.append(playButton, passButton);
    panel.appendChild(buttons);
  }

  function renderControls(context) {
    const documentRef = context.controls.ownerDocument || window.document;
    ensureStylesheet(documentRef);
    const panel = element(documentRef, "div", "doudizhu-controls");
    panel.setAttribute("aria-busy", String(Boolean(context.uiState.submitting)));
    if (context.state.flow && context.state.flow.phase === "bidding") {
      renderBidControls(documentRef, context, panel);
    } else {
      renderPlayControls(documentRef, context, panel);
    }
    context.controls.appendChild(panel);
    return true;
  }

  const renderer = Object.freeze({
    participantPresentation: "embedded",
    usesStandardMoveConfirmation: false,
    ownsPrivateStatePresentation: true,
    renderBoard,
    renderControls,
  });
  window.DuelGameUI.register("doudizhu", renderer);
}());

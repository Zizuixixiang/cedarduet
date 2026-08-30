(function registerGandengyanRenderer() {
  "use strict";

  const STYLE_ID = "duel-game-gandengyan-styles";
  const STYLE_HREF = "/static/games/gandengyan.css?v=0.1.1";
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
    link.dataset.duelGameStyle = "gandengyan";
    documentRef.head.appendChild(link);
    return link;
  }

  function element(documentRef, tag, className, text = null) {
    const node = documentRef.createElement(tag);
    if (className) node.className = className;
    if (text !== null) node.textContent = String(text);
    return node;
  }

  function participantName(context, playerId) {
    const participant = (context.participants || []).find(
      (item) => item.player_id === playerId
    );
    return participant && (participant.display_name || participant.player_id)
      || "玩家";
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
      `gandengyan-card suit-${card.suit || "joker"}`
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

    const corner = element(documentRef, "span", "gandengyan-card-corner");
    const rank = element(
      documentRef,
      "strong",
      "gandengyan-card-rank",
      card.rank === "small_joker"
        ? "小"
        : card.rank === "big_joker" ? "大" : card.rank
    );
    const suit = element(
      documentRef,
      "span",
      "gandengyan-card-suit",
      card.suit === "joker" ? "王" : (SUIT_TEXT[card.suit] || "")
    );
    corner.append(rank, suit);
    const center = element(
      documentRef,
      "span",
      "gandengyan-card-center",
      card.suit === "joker" ? (card.rank === "big_joker" ? "大王" : "小王")
        : (SUIT_TEXT[card.suit] || "")
    );
    node.append(corner, center);
    return node;
  }

  function playActions(context) {
    return (Array.isArray(context.legalActions) ? context.legalActions : [])
      .filter((action) => (
        action && action.action === "play" && Array.isArray(action.card_ids)
      ));
  }

  function canonicalIds(ids) {
    return [...ids].map(String).sort().join("|");
  }

  function selectedIds(context) {
    const handIds = new Set(
      ((context.privateState && context.privateState.hand) || [])
        .map((card) => String(card.id))
    );
    const selected = Array.isArray(context.uiState.selectedCardIds)
      ? context.uiState.selectedCardIds.map(String).filter((id) => handIds.has(id))
      : [];
    context.uiState.selectedCardIds = [...new Set(selected)];
    return context.uiState.selectedCardIds;
  }

  function exactSelectedAction(context) {
    const key = canonicalIds(selectedIds(context));
    if (!key) return null;
    return playActions(context).find(
      (action) => canonicalIds(action.card_ids) === key
    ) || null;
  }

  function isSubset(subset, values) {
    const source = new Set(values.map(String));
    return subset.every((item) => source.has(String(item)));
  }

  function renderOpponent(documentRef, context, participant, passIds) {
    const playerId = participant.player_id;
    const count = Number((context.state.hand_counts || {})[playerId] || 0);
    const seat = element(documentRef, "article", "gandengyan-opponent");
    seat.dataset.playerId = playerId;
    seat.classList.toggle("current", context.room.current_player_id === playerId);
    seat.classList.toggle("passed", passIds.has(playerId));
    const header = element(documentRef, "div", "gandengyan-opponent-header");
    const name = element(
      documentRef,
      "strong",
      "gandengyan-opponent-name",
      participant.display_name || playerId
    );
    const state = element(
      documentRef,
      "span",
      "gandengyan-opponent-state",
      passIds.has(playerId)
        ? "已过"
        : context.room.current_player_id === playerId ? "出牌中" : "等待"
    );
    header.append(name, state);
    const backs = element(documentRef, "div", "gandengyan-card-backs");
    backs.setAttribute("aria-label", `${count} 张未公开手牌`);
    for (let index = 0; index < Math.min(count, 5); index += 1) {
      const back = element(documentRef, "span", "gandengyan-card-back");
      back.setAttribute("aria-hidden", "true");
      backs.appendChild(back);
    }
    backs.appendChild(element(documentRef, "b", "gandengyan-card-count", `${count} 张`));
    seat.append(header, backs);
    return seat;
  }

  function renderTrick(documentRef, context, shell) {
    const trick = context.state.current_trick || {};
    const lastPlay = trick.last_play || null;
    const center = element(documentRef, "section", "gandengyan-trick");
    center.setAttribute("aria-label", `第 ${trick.number || 1} 墩桌面牌`);
    const eyebrow = element(
      documentRef,
      "span",
      "gandengyan-trick-number",
      `第 ${trick.number || 1} 墩`
    );
    const heading = element(documentRef, "div", "gandengyan-trick-heading");
    if (lastPlay) {
      heading.append(
        element(documentRef, "strong", "", lastPlay.pattern && lastPlay.pattern.label || "出牌"),
        element(
          documentRef,
          "span",
          "",
          `${participantName(context, lastPlay.player_id)} · ${lastPlay.cards.length} 张`
        )
      );
    } else {
      heading.append(
        element(documentRef, "strong", "", "等待引牌"),
        element(
          documentRef,
          "span",
          "",
          `${participantName(context, trick.leader_player_id)} 领出任意合法牌`
        )
      );
    }
    const cards = element(documentRef, "div", "gandengyan-trick-cards");
    if (lastPlay && Array.isArray(lastPlay.cards)) {
      lastPlay.cards.forEach((card, index) => {
        const cardNode = createCard(documentRef, card);
        cardNode.style.setProperty("--trick-index", index);
        cards.appendChild(cardNode);
      });
    } else {
      cards.appendChild(element(documentRef, "span", "gandengyan-empty-trick", "本墩尚未出牌"));
    }
    const passLine = element(documentRef, "div", "gandengyan-pass-line");
    const passIds = Array.isArray(trick.pass_player_ids) ? trick.pass_player_ids : [];
    if (passIds.length) {
      passIds.forEach((playerId) => {
        passLine.appendChild(element(
          documentRef,
          "span",
          "gandengyan-pass-chip",
          `${participantName(context, playerId)} 已过`
        ));
      });
    } else {
      passLine.appendChild(element(documentRef, "span", "gandengyan-no-pass", "暂时无人过牌"));
    }
    center.append(eyebrow, heading, cards, passLine);
    shell.appendChild(center);
  }

  function renderHand(documentRef, context, shell) {
    const hand = context.privateState && Array.isArray(context.privateState.hand)
      ? context.privateState.hand
      : [];
    const selected = selectedIds(context);
    const legal = playActions(context);
    const selectableIds = new Set(
      legal.flatMap((action) => action.card_ids.map(String))
    );
    const handZone = element(documentRef, "section", "gandengyan-hand-zone");
    const label = element(documentRef, "div", "gandengyan-hand-label");
    label.append(
      element(documentRef, "strong", "", "我的手牌"),
      element(documentRef, "span", "", `${hand.length} 张 · 可多选`)
    );
    const scroller = element(documentRef, "div", "gandengyan-hand-scroll");
    scroller.setAttribute("role", "group");
    scroller.setAttribute("aria-label", "我的手牌，可横向滚动并多选");
    hand.forEach((card, index) => {
      const cardId = String(card.id);
      const isSelected = selected.includes(cardId);
      const selectable = selectableIds.has(cardId);
      const cardNode = createCard(documentRef, card, {
        interactive: true,
        selected: isSelected,
        selectable,
        disabled: !context.canMove || !selectable || Boolean(context.uiState.submitting),
      });
      const middle = (hand.length - 1) / 2;
      const angle = Math.max(-6, Math.min(6, (index - middle) * 0.75));
      cardNode.style.setProperty("--fan-angle", `${angle}deg`);
      cardNode.style.setProperty("--hand-index", index);
      cardNode.addEventListener("click", () => {
        if (!context.helpers.canMove() || !selectable || context.uiState.submitting) return;
        const current = selectedIds(context);
        context.uiState.selectedCardIds = current.includes(cardId)
          ? current.filter((id) => id !== cardId)
          : [...current, cardId];
        context.helpers.rerender();
      });
      scroller.appendChild(cardNode);
    });
    if (!hand.length) {
      scroller.appendChild(element(documentRef, "span", "gandengyan-empty-hand", "手牌已出完"));
    }
    handZone.append(label, scroller);
    shell.appendChild(handZone);
  }

  function renderBoard(context) {
    const documentRef = context.board.ownerDocument || window.document;
    ensureStylesheet(documentRef);
    context.helpers.setBoardLayout({
      rows: 1,
      cols: 1,
      large: true,
      ariaLabel: "干瞪眼中文扑克桌",
    });
    const board = context.board;
    board.dataset.multiplier = String(context.state.multiplier || 1);
    const game = element(documentRef, "div", "gandengyan-game");
    const topbar = element(documentRef, "header", "gandengyan-topbar");
    const title = element(documentRef, "div", "gandengyan-title");
    title.append(
      element(documentRef, "span", "gandengyan-title-mark", "干"),
      element(documentRef, "strong", "", "干瞪眼")
    );
    const metrics = element(documentRef, "div", "gandengyan-metrics");
    metrics.append(
      element(
        documentRef,
        "span",
        "gandengyan-metric multiplier",
        `倍率 ${context.state.multiplier || 1} 倍`
      ),
      element(
        documentRef,
        "span",
        "gandengyan-metric deck",
        `牌堆 ${context.state.deck_count || 0} 张`
      )
    );
    topbar.append(title, metrics);

    const shell = element(documentRef, "div", "gandengyan-table-shell");
    const trick = context.state.current_trick || {};
    const passIds = new Set(Array.isArray(trick.pass_player_ids) ? trick.pass_player_ids : []);
    const viewerId = context.viewer && context.viewer.player_id;
    const opponents = element(documentRef, "div", "gandengyan-opponents");
    (context.participants || [])
      .filter((participant) => participant.player_id !== viewerId)
      .forEach((participant) => {
        opponents.appendChild(renderOpponent(documentRef, context, participant, passIds));
      });
    shell.appendChild(opponents);
    renderTrick(documentRef, context, shell);
    renderHand(documentRef, context, shell);
    game.append(topbar, shell);
    board.appendChild(game);
    return true;
  }

  function renderControls(context) {
    const documentRef = context.controls.ownerDocument || window.document;
    ensureStylesheet(documentRef);
    const selected = selectedIds(context);
    const exact = exactSelectedAction(context);
    const legal = playActions(context);
    const possibleCompletions = selected.length
      ? legal.filter((action) => isSubset(selected, action.card_ids))
      : legal;
    const passAction = (context.legalActions || []).find(
      (action) => action && action.action === "pass"
    );
    const panel = element(documentRef, "div", "gandengyan-controls");
    panel.setAttribute("aria-busy", String(Boolean(context.uiState.submitting)));
    const status = element(documentRef, "div", "gandengyan-selection-status");
    status.setAttribute("role", "status");
    status.setAttribute("aria-live", "polite");
    const summary = element(documentRef, "strong", "gandengyan-selection-title");
    const detail = element(documentRef, "span", "gandengyan-selection-detail");
    if (exact) {
      summary.textContent = `已选：${exact.pattern_label}`;
      detail.textContent = `${exact.card_ids.length} 张，可出牌`;
    } else if (selected.length && possibleCompletions.length) {
      summary.textContent = `已选 ${selected.length} 张`;
      detail.textContent = `继续选择，可补全为 ${possibleCompletions.length} 个服务端合法组合`;
    } else if (selected.length) {
      summary.textContent = `已选 ${selected.length} 张`;
      detail.textContent = "当前组合不在服务端合法行动中";
    } else {
      summary.textContent = context.canMove ? "请选择手牌" : "等待其他玩家";
      detail.textContent = context.canMove
        ? `${legal.length} 个服务端合法组合`
        : "轮到你时才能出牌";
    }
    status.append(summary, detail);

    const buttons = element(documentRef, "div", "gandengyan-action-buttons");
    const playButton = element(documentRef, "button", "gandengyan-play-button", "出牌");
    playButton.type = "button";
    playButton.disabled = !context.canMove || !exact || Boolean(context.uiState.submitting);
    playButton.setAttribute("aria-label", exact ? `出牌，${exact.pattern_label}` : "出牌，尚未选中合法组合");
    playButton.addEventListener("click", async () => {
      const action = exactSelectedAction(context);
      if (!context.helpers.canMove() || !action || context.uiState.submitting) return;
      context.uiState.submitting = true;
      context.helpers.rerender();
      const submitted = await context.helpers.submitMove({...action});
      if (!submitted) {
        context.uiState.submitting = false;
        context.helpers.rerender();
      }
    });
    const passButton = element(documentRef, "button", "gandengyan-pass-button", "过");
    passButton.type = "button";
    passButton.disabled = !context.canMove || !passAction || Boolean(context.uiState.submitting);
    passButton.setAttribute("aria-label", passAction ? "本轮过牌" : "引牌时不能过牌");
    passButton.addEventListener("click", async () => {
      if (!context.helpers.canMove() || !passAction || context.uiState.submitting) return;
      context.uiState.submitting = true;
      context.helpers.rerender();
      const submitted = await context.helpers.submitMove({...passAction});
      if (!submitted) {
        context.uiState.submitting = false;
        context.helpers.rerender();
      }
    });
    buttons.append(playButton, passButton);
    panel.append(status, buttons);
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
  window.DuelGameUI.register("gandengyan", renderer);
}());

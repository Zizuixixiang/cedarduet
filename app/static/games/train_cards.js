(function registerTrainCardsRenderer() {
  "use strict";

  const STYLE_ID = "duel-game-train-cards-styles";
  const STYLE_HREF = "/static/games/train_cards.css?v=1.0.1";
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
    link.dataset.duelGameStyle = "train_cards";
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

  function createCard(documentRef, card) {
    const joker = card && card.suit === "joker";
    const node = element(
      documentRef,
      "span",
      `train-card suit-${card && card.suit || "unknown"}${joker ? " joker" : ""}`
    );
    node.dataset.cardId = String(card && card.id || "");
    node.setAttribute("role", "img");
    node.setAttribute("aria-label", cardName(card));
    const rankText = joker
      ? (card.rank === "big_joker" ? "大" : "小")
      : card.rank;
    const suitText = joker ? "王" : (SUIT_TEXT[card.suit] || "");
    const corner = element(documentRef, "span", "train-card-corner");
    corner.append(
      element(documentRef, "strong", "train-card-rank", rankText),
      element(documentRef, "span", "train-card-suit", suitText)
    );
    const center = element(
      documentRef,
      "span",
      "train-card-center",
      joker ? `${rankText}王` : suitText
    );
    node.append(corner, center);
    return node;
  }

  function lastActionSummary(context) {
    const action = context.state.last_action;
    if (!action || action.action !== "flip") {
      return "等待第一张牌驶上轨道";
    }
    const actor = participantName(context, action.player_id);
    const card = cardName(action.revealed_card);
    if (Number(action.collected_count) > 0) {
      return `${actor} 翻出${card}，收回 ${action.collected_count} 张`;
    }
    if (action.eliminated_player_id) {
      return `${actor} 翻出${card}，牌堆已空并淘汰`;
    }
    return `${actor} 翻出${card}，未触发收牌`;
  }

  function renderBoard(context) {
    const documentRef = context.board.ownerDocument || window.document;
    ensureStylesheet(documentRef);
    context.helpers.setBoardLayout({
      rows: 1,
      cols: 1,
      large: true,
      ariaLabel: "开火车公开牌列",
    });
    const game = element(documentRef, "div", "train-cards-game");
    const header = element(documentRef, "header", "train-cards-header");
    const title = element(documentRef, "div", "train-cards-title");
    title.append(
      element(documentRef, "span", "train-cards-title-mark", "车"),
      element(documentRef, "strong", "", "开火车")
    );
    const metrics = element(documentRef, "div", "train-cards-metrics");
    const tableCards = Array.isArray(context.state.table_cards)
      ? context.state.table_cards
      : [];
    metrics.append(
      element(documentRef, "span", "train-cards-metric", `桌面 ${tableCards.length} 张`),
      element(
        documentRef,
        "span",
        "train-cards-metric active",
        `在局 ${(context.state.active_player_ids || []).length} 人`
      )
    );
    header.append(title, metrics);

    const table = element(documentRef, "section", "train-cards-table");
    const status = element(
      documentRef,
      "div",
      "train-cards-status",
      lastActionSummary(context)
    );
    status.setAttribute("aria-live", "polite");
    const track = element(documentRef, "div", "train-cards-track");
    track.setAttribute("role", "list");
    track.setAttribute("aria-label", `桌面公开牌列，共 ${tableCards.length} 张`);
    if (tableCards.length) {
      tableCards.forEach((card, index) => {
        const wrapper = element(documentRef, "span", "train-cards-wagon");
        wrapper.setAttribute("role", "listitem");
        wrapper.style.setProperty("--wagon-index", index);
        wrapper.appendChild(createCard(documentRef, card));
        track.appendChild(wrapper);
      });
    } else {
      track.appendChild(element(
        documentRef,
        "span",
        "train-cards-empty",
        "轨道为空 · 轮到先手翻牌"
      ));
    }
    table.append(status, track);
    game.append(header, table);
    context.board.appendChild(game);
    return true;
  }

  function renderControls(context) {
    const documentRef = context.controls.ownerDocument || window.document;
    ensureStylesheet(documentRef);
    const flipAction = (Array.isArray(context.legalActions) ? context.legalActions : [])
      .find((action) => action && action.action === "flip") || null;
    const panel = element(documentRef, "div", "train-cards-controls");
    panel.setAttribute("aria-busy", String(Boolean(context.uiState.submitting)));
    const copy = element(documentRef, "div", "train-cards-control-copy");
    copy.append(
      element(
        documentRef,
        "strong",
        "",
        context.canMove && flipAction ? "轮到你发车" : "等待列车轮转"
      ),
      element(
        documentRef,
        "span",
        "",
        context.canMove && flipAction
          ? "牌面由裁判安全翻开，无需选择或猜牌"
          : "行动权会自动跳过已淘汰席位"
      )
    );
    const button = element(documentRef, "button", "train-cards-flip-button", "翻下一张");
    button.type = "button";
    button.disabled = !context.canMove || !flipAction || Boolean(context.uiState.submitting);
    button.setAttribute("aria-label", "翻开自己牌堆最上方一张牌");
    button.addEventListener("click", async () => {
      if (!context.helpers.canMove() || !flipAction || context.uiState.submitting) return;
      context.uiState.submitting = true;
      context.helpers.rerender();
      const submitted = await context.helpers.submitMove({...flipAction});
      if (!submitted) {
        context.uiState.submitting = false;
        context.helpers.rerender();
      }
    });
    panel.append(copy, button);
    context.controls.appendChild(panel);
    return true;
  }

  const renderer = Object.freeze({
    participantPresentation: "generic",
    usesStandardMoveConfirmation: false,
    ownsPrivateStatePresentation: true,
    renderBoard,
    renderControls,
  });
  window.DuelGameUI.register("train_cards", renderer);
}());

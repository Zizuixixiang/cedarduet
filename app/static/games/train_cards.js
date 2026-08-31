(function registerTrainCardsRenderer() {
  "use strict";

  const STYLE_ID = "duel-game-train-cards-styles";
  const STYLE_HREF = "/static/games/train_cards.css?v=1.0.8";
  const COLLECTION_HOLD_MS = 600;
  const COLLECTION_COLLAPSE_MS = 220;
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

  function isTerminalState(context) {
    return Boolean(
      context.isTerminal
      || (context.state.flow || {}).phase === "finished"
      || ["finished", "archived"].includes((context.room || {}).status)
    );
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
      return isTerminalState(context) ? "本局已结束" : "等待第一张牌驶上轨道";
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

  function terminalReview(documentRef, context) {
    const hands = context.state && context.state.terminal_hands;
    if (!hands || typeof hands !== "object") return null;
    const review = element(documentRef, "details", "train-cards-terminal-review");
    review.appendChild(element(
      documentRef,
      "summary",
      "train-cards-terminal-summary",
      "终局牌堆复盘 · 按未来翻牌顺序展开"
    ));
    const rows = element(documentRef, "div", "train-cards-terminal-rows");
    Object.entries(hands).forEach(([playerId, cards]) => {
      const row = element(documentRef, "section", "train-cards-terminal-row");
      row.dataset.playerId = playerId;
      row.appendChild(element(
        documentRef,
        "strong",
        "train-cards-terminal-name",
        `${participantName(context, playerId)} · ${Array.isArray(cards) ? cards.length : 0} 张`
      ));
      const pile = element(documentRef, "div", "train-cards-terminal-pile");
      (Array.isArray(cards) ? cards : []).forEach(
        (card) => pile.appendChild(createCard(documentRef, card))
      );
      if (!pile.children.length) {
        pile.appendChild(element(documentRef, "span", "train-cards-terminal-empty", "牌堆已空"));
      }
      row.appendChild(pile);
      rows.appendChild(row);
    });
    review.appendChild(rows);
    return review;
  }

  function actionFingerprint(context, action) {
    if (!action || action.action !== "flip" || !action.revealed_card) return "";
    const room = context.room || {};
    const flow = context.state && context.state.flow || {};
    const revision = room.revision === undefined || room.revision === null
      ? flow.turn_number || ""
      : room.revision;
    return [
      room.room_id || "train-cards",
      revision,
      action.player_id || "",
      action.revealed_card.id || "",
      Number(action.collected_count) || 0,
    ].join(":");
  }

  function collectedCardsFor(context, action) {
    if (Array.isArray(action && action.collected_cards) && action.collected_cards.length) {
      return action.collected_cards;
    }
    const collection = context.state && context.state.last_collection;
    return Array.isArray(collection && collection.cards) ? collection.cards : [];
  }

  function scheduleCollectionPhase(context, transition, windowRef) {
    if (!windowRef || typeof windowRef.setTimeout !== "function") return;
    if (transition.phase === "holding" && !transition.holdScheduled) {
      transition.holdScheduled = true;
      windowRef.setTimeout(() => {
        const current = context.uiState.trainCardsCollectionTransition;
        if (current !== transition || current.phase !== "holding") return;
        current.phase = "collecting";
        context.helpers.rerender();
      }, COLLECTION_HOLD_MS);
      return;
    }
    if (transition.phase === "collecting" && !transition.collapseScheduled) {
      transition.collapseScheduled = true;
      windowRef.setTimeout(() => {
        const current = context.uiState.trainCardsCollectionTransition;
        if (current !== transition || current.phase !== "collecting") return;
        context.uiState.trainCardsCollectionTransition = null;
        context.helpers.rerender();
      }, COLLECTION_COLLAPSE_MS);
    }
  }

  function tablePresentation(context, documentRef) {
    const finalCards = Array.isArray(context.state.table_cards)
      ? context.state.table_cards
      : [];
    const uiState = context.uiState || (context.uiState = {});
    const action = context.state.last_action;
    const fingerprint = actionFingerprint(context, action);
    const existing = uiState.trainCardsCollectionTransition;
    const windowRef = documentRef && documentRef.defaultView || window;

    if (existing && existing.fingerprint === fingerprint) {
      scheduleCollectionPhase(context, existing, windowRef);
      const revealIndex = existing.revealPending ? existing.cards.length - 1 : -1;
      existing.revealPending = false;
      return {
        cards: existing.cards,
        collectionStart: existing.collectionStart,
        phase: existing.phase,
        revealIndex,
      };
    }
    if (!fingerprint || uiState.trainCardsPresentedActionFingerprint === fingerprint) {
      return {cards: finalCards, collectionStart: -1, phase: "final", revealIndex: -1};
    }

    uiState.trainCardsPresentedActionFingerprint = fingerprint;
    const collectedCards = collectedCardsFor(context, action);
    const collectedCount = Math.max(
      Number(action && action.collected_count) || 0,
      Number(context.state.last_collection && context.state.last_collection.count) || 0
    );
    if (collectedCount > 0 && collectedCards.length) {
      const transition = {
        fingerprint,
        phase: "holding",
        cards: finalCards.concat(collectedCards),
        collectionStart: finalCards.length,
        revealPending: true,
        holdScheduled: false,
        collapseScheduled: false,
      };
      uiState.trainCardsCollectionTransition = transition;
      scheduleCollectionPhase(context, transition, windowRef);
      transition.revealPending = false;
      return {
        cards: transition.cards,
        collectionStart: transition.collectionStart,
        phase: transition.phase,
        revealIndex: transition.cards.length - 1,
      };
    }

    uiState.trainCardsCollectionTransition = null;
    return {
      cards: finalCards,
      collectionStart: -1,
      phase: "final",
      revealIndex: finalCards.length - 1,
    };
  }

  function restoreTrackPosition(context, track, tableCards) {
    const documentRef = track.ownerDocument || context.board.ownerDocument || window.document;
    const uiState = context.uiState || (context.uiState = {});
    const tail = tableCards.length ? tableCards[tableCards.length - 1] : null;
    const fingerprint = `${tableCards.length}:${tail && tail.id || ""}`;
    const followNewCard = uiState.trainCardsTableFingerprint !== fingerprint;
    uiState.trainCardsTableFingerprint = fingerprint;

    track.addEventListener("scroll", () => {
      const scrollLeft = Number(track.scrollLeft);
      if (Number.isFinite(scrollLeft)) {
        uiState.trainCardsTrackScrollLeft = Math.max(0, scrollLeft);
      }
    }, {passive: true});

    const restore = () => {
      const maxScrollLeft = Math.max(
        0,
        Number(track.scrollWidth || 0) - Number(track.clientWidth || 0)
      );
      const savedScrollLeft = Number(uiState.trainCardsTrackScrollLeft);
      const target = followNewCard
        ? maxScrollLeft
        : Math.min(
          maxScrollLeft,
          Number.isFinite(savedScrollLeft) ? Math.max(0, savedScrollLeft) : 0
        );
      track.scrollLeft = target;
      uiState.trainCardsTrackScrollLeft = target;
    };
    restore();
    const windowRef = documentRef && documentRef.defaultView || window;
    if (windowRef && typeof windowRef.requestAnimationFrame === "function") {
      windowRef.requestAnimationFrame(restore);
    }
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
    const presentation = tablePresentation(context, documentRef);
    const tableCards = presentation.cards;
    const terminal = isTerminalState(context);
    const game = element(documentRef, "div", "train-cards-game");
    const header = element(documentRef, "header", "train-cards-header");
    const title = element(documentRef, "div", "train-cards-title");
    title.append(
      element(documentRef, "span", "train-cards-title-mark", "车"),
      element(documentRef, "strong", "", "开火车")
    );
    const metrics = element(documentRef, "div", "train-cards-metrics");
    metrics.append(
      element(documentRef, "span", "train-cards-metric", `桌面 ${tableCards.length} 张`),
      element(
        documentRef,
        "span",
        "train-cards-metric active",
        terminal
          ? "终局复盘"
          : `在局 ${(context.state.active_player_ids || []).length} 人`
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
    track.dataset.transitionPhase = presentation.phase;
    track.setAttribute("role", "list");
    track.setAttribute("aria-label", `桌面公开牌列，共 ${tableCards.length} 张`);
    if (tableCards.length) {
      tableCards.forEach((card, index) => {
        const classNames = ["train-cards-wagon"];
        if (index >= presentation.collectionStart && presentation.collectionStart >= 0) {
          classNames.push("is-collected-segment");
          if (presentation.phase === "collecting") classNames.push("is-collecting");
        }
        if (index === presentation.revealIndex) classNames.push("is-revealed");
        const wrapper = element(documentRef, "span", classNames.join(" "));
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
        terminal ? "轨道为空 · 本局已结束" : "轨道为空 · 轮到先手翻牌"
      ));
    }
    table.append(status, track);
    const review = terminalReview(documentRef, context);
    if (review) table.appendChild(review);
    game.append(header, table);
    context.board.appendChild(game);
    restoreTrackPosition(context, track, tableCards);
    return true;
  }

  function renderControls(context) {
    const documentRef = context.controls.ownerDocument || window.document;
    ensureStylesheet(documentRef);
    const terminal = isTerminalState(context);
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
        terminal
          ? "本局已结束"
          : (context.canMove && flipAction
          ? "轮到你发车"
          : `等待 ${participantName(context, context.room && context.room.current_player_id)} 翻牌`)
      ),
      element(
        documentRef,
        "span",
        "",
        terminal
          ? "牌局已结算，不再翻牌"
          : (context.canMove && flipAction
          ? "牌面由裁判安全翻开，无需选择或猜牌"
          : "行动权会自动跳过已淘汰席位")
      )
    );
    const button = element(
      documentRef,
      "button",
      "train-cards-flip-button",
      terminal ? "已结束" : "翻下一张"
    );
    button.type = "button";
    button.disabled = terminal
      || !context.canMove
      || !flipAction
      || Boolean(context.uiState.submitting);
    button.setAttribute(
      "aria-label",
      terminal ? "本局已结束" : "翻开自己牌堆最上方一张牌"
    );
    button.addEventListener("click", async () => {
      if (
        terminal
        || !context.helpers.canMove()
        || !flipAction
        || context.uiState.submitting
      ) return;
      context.uiState.submitting = true;
      panel.setAttribute("aria-busy", "true");
      if (
        documentRef.activeElement === button
        && typeof button.blur === "function"
      ) button.blur();
      button.disabled = true;
      const submitted = await context.helpers.submitMove({...flipAction});
      if (!submitted) {
        context.uiState.submitting = false;
        context.helpers.rerender();
      }
    });
    panel.appendChild(copy);
    if (!terminal) panel.appendChild(button);
    context.controls.appendChild(panel);
    return true;
  }

  const renderer = Object.freeze({
    participantPresentation: "generic",
    usesEmbeddedActionFeedback: true,
    usesStandardMoveConfirmation: false,
    ownsPrivateStatePresentation: true,
    renderBoard,
    renderControls,
  });
  window.DuelGameUI.register("train_cards", renderer);
}());

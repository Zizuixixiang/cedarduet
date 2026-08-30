(function registerBlackjackRenderer() {
  "use strict";

  const STYLE_ID = "duel-game-blackjack-styles";
  const STYLE_HREF = "/static/games/blackjack.css?v=1.0.0";
  const SUIT_SYMBOLS = Object.freeze({
    spades: "\u2660",
    hearts: "\u2665",
    diamonds: "\u2666",
    clubs: "\u2663",
  });
  const SUIT_LABELS = Object.freeze({
    spades: "黑桃",
    hearts: "红桃",
    diamonds: "方片",
    clubs: "梅花",
  });
  const OUTCOME_LABELS = Object.freeze({win: "胜", loss: "负", push: "推和"});

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
    link.dataset.duelGameStyle = "blackjack";
    documentRef.head.appendChild(link);
    return link;
  }

  function playerName(participant) {
    return participant && (participant.display_name || participant.player_id) || "玩家";
  }

  function renderAvatar(documentRef, context, participant) {
    const avatar = documentRef.createElement("span");
    avatar.className = "blackjack-seat-avatar";
    avatar.textContent = Array.from(String(playerName(participant)).trim())[0] || "?";
    if (
      context.helpers
      && typeof context.helpers.renderParticipantAvatar === "function"
    ) {
      context.helpers.renderParticipantAvatar(avatar, participant);
    }
    return avatar;
  }

  function valueLabel(value, hidden = false) {
    if (hidden) return value && value.total ? `明牌 ${value.total} 点` : "等待翻牌";
    if (!value) return "等待计点";
    if (value.blackjack) return "Blackjack";
    if (value.bust) return `爆牌 ${value.total}`;
    return `${value.soft ? "软" : "硬"} ${value.total}`;
  }

  function createCard(documentRef, card, ownerLabel) {
    const hidden = Boolean(card && card.hidden);
    const node = documentRef.createElement("div");
    node.className = hidden ? "blackjack-card is-hidden" : "blackjack-card";
    node.setAttribute("role", "img");
    if (hidden) {
      node.setAttribute("aria-label", `${ownerLabel}暗牌`);
      const back = documentRef.createElement("span");
      back.className = "blackjack-card-back";
      back.setAttribute("aria-hidden", "true");
      node.appendChild(back);
      return node;
    }
    const rank = String(card && card.rank || "?");
    const suit = String(card && card.suit || "spades");
    const symbol = SUIT_SYMBOLS[suit] || "";
    node.classList.add(suit === "hearts" || suit === "diamonds" ? "is-red" : "is-black");
    node.setAttribute("aria-label", `${ownerLabel}${SUIT_LABELS[suit] || "牌"}${rank}`);

    const corner = documentRef.createElement("span");
    corner.className = "blackjack-card-corner";
    const rankNode = documentRef.createElement("strong");
    rankNode.textContent = rank;
    const suitNode = documentRef.createElement("span");
    suitNode.textContent = symbol;
    corner.append(rankNode, suitNode);
    const center = documentRef.createElement("span");
    center.className = "blackjack-card-suit";
    center.textContent = symbol;
    center.setAttribute("aria-hidden", "true");
    node.append(corner, center);
    return node;
  }

  function appendCards(documentRef, target, cards, ownerLabel) {
    (Array.isArray(cards) ? cards : []).forEach((card) => {
      target.appendChild(createCard(documentRef, card, ownerLabel));
    });
  }

  function badge(documentRef, text, className) {
    const node = documentRef.createElement("span");
    node.className = `blackjack-badge ${className || ""}`.trim();
    node.textContent = text;
    return node;
  }

  function renderBoard(context) {
    const documentRef = context.document || window.document;
    ensureStylesheet(documentRef);
    const {board, state, room} = context;
    const participants = Array.isArray(context.participants)
      ? [...context.participants].sort((a, b) => Number(a.seat_index) - Number(b.seat_index))
      : [];
    const viewerId = context.viewer && context.viewer.player_id;
    const currentId = room.current_player_id || state.turn_player_id;
    const phase = state.flow && state.flow.phase || "player_turns";
    const root = documentRef.createElement("section");
    root.className = "blackjack-table";
    root.setAttribute("aria-label", "21点牌桌");

    const tableHead = documentRef.createElement("header");
    tableHead.className = "blackjack-table-heading";
    const titleWrap = documentRef.createElement("div");
    const eyebrow = documentRef.createElement("span");
    eyebrow.className = "blackjack-eyebrow";
    eyebrow.textContent = "BLACKJACK · S17";
    const title = documentRef.createElement("strong");
    title.textContent = "共同挑战庄家";
    titleWrap.append(eyebrow, title);
    const shoe = badge(documentRef, `${Number(state.shoe_decks || 4)} 副牌`, "shoe");
    tableHead.append(titleWrap, shoe);

    const dealer = state.dealer || {};
    const dealerArea = documentRef.createElement("section");
    dealerArea.className = `blackjack-dealer phase-${phase}`;
    dealerArea.setAttribute("aria-label", "庄家手牌");
    const dealerCopy = documentRef.createElement("div");
    dealerCopy.className = "blackjack-hand-copy";
    const dealerName = documentRef.createElement("strong");
    dealerName.textContent = "庄家";
    const dealerMeta = documentRef.createElement("div");
    dealerMeta.className = "blackjack-hand-meta";
    dealerMeta.appendChild(badge(
      documentRef,
      valueLabel(dealer.value, Boolean(dealer.hole_hidden)),
      dealer.hole_hidden ? "hidden-value" : "value"
    ));
    if (dealer.status === "bust") dealerMeta.appendChild(badge(documentRef, "爆牌", "bust"));
    if (dealer.status === "blackjack") {
      dealerMeta.appendChild(badge(documentRef, "Blackjack", "natural"));
    }
    dealerCopy.append(dealerName, dealerMeta);
    const dealerCards = documentRef.createElement("div");
    dealerCards.className = "blackjack-cards blackjack-dealer-cards";
    appendCards(documentRef, dealerCards, dealer.hand, "庄家");
    dealerArea.append(dealerCopy, dealerCards);

    const playerRegion = documentRef.createElement("section");
    playerRegion.className = "blackjack-player-region";
    playerRegion.setAttribute("aria-label", "参与者手牌，可内部滚动");
    playerRegion.tabIndex = 0;
    const players = documentRef.createElement("div");
    players.className = "blackjack-players";
    participants.forEach((participant) => {
      const player = (state.players || {})[participant.player_id] || {};
      const isCurrent = participant.player_id === currentId && phase === "player_turns";
      const isViewer = participant.player_id === viewerId;
      const seat = documentRef.createElement("article");
      seat.className = [
        "blackjack-seat",
        isCurrent ? "is-current" : "",
        isViewer ? "is-viewer" : "",
        player.status ? `status-${player.status}` : "",
        player.outcome && player.outcome.outcome
          ? `outcome-${player.outcome.outcome}`
          : "",
      ].filter(Boolean).join(" ");
      seat.dataset.playerId = participant.player_id;
      seat.setAttribute(
        "aria-label",
        `${playerName(participant)}，${valueLabel(player.value)}，${player.status_label || "等待"}`
      );

      const seatHead = documentRef.createElement("header");
      const identity = documentRef.createElement("div");
      identity.className = "blackjack-seat-identity";
      const identityCopy = documentRef.createElement("div");
      identityCopy.className = "blackjack-seat-identity-copy";
      const name = documentRef.createElement("strong");
      name.textContent = playerName(participant);
      const marker = documentRef.createElement("span");
      marker.textContent = isViewer ? "你的手牌" : `座位 ${Number(participant.seat_index) + 1}`;
      identityCopy.append(name, marker);
      identity.append(renderAvatar(documentRef, context, participant), identityCopy);
      const meta = documentRef.createElement("div");
      meta.className = "blackjack-hand-meta";
      meta.appendChild(badge(documentRef, valueLabel(player.value), "value"));
      if (player.status === "blackjack") {
        meta.appendChild(badge(documentRef, "Blackjack", "natural"));
      } else if (player.status === "bust") {
        meta.appendChild(badge(documentRef, "爆牌", "bust"));
      } else if (player.status === "stood") {
        meta.appendChild(badge(documentRef, "已停牌", "stood"));
      }
      if (player.outcome && player.outcome.outcome) {
        meta.appendChild(badge(
          documentRef,
          OUTCOME_LABELS[player.outcome.outcome] || player.outcome.outcome,
          `outcome ${player.outcome.outcome}`
        ));
      }
      seatHead.append(identity, meta);
      const cards = documentRef.createElement("div");
      cards.className = "blackjack-cards blackjack-player-cards";
      appendCards(documentRef, cards, player.hand, `${playerName(participant)}的`);
      const result = documentRef.createElement("p");
      result.className = "blackjack-seat-result";
      result.textContent = player.outcome && player.outcome.result_text || "";
      seat.append(seatHead, cards, result);
      players.appendChild(seat);
    });
    playerRegion.appendChild(players);

    if (state.result_text) {
      const summary = documentRef.createElement("p");
      summary.className = "blackjack-result-summary";
      summary.setAttribute("role", "status");
      summary.textContent = state.result_text;
      root.append(tableHead, dealerArea, playerRegion, summary);
    } else {
      root.append(tableHead, dealerArea, playerRegion);
    }
    board.appendChild(root);
  }

  function renderControls(context) {
    const documentRef = context.document || window.document;
    ensureStylesheet(documentRef);
    const {controls, helpers, room, state} = context;
    const legalActions = Array.isArray(context.legalActions) ? context.legalActions : [];
    const legalNames = new Set(legalActions.map((action) => action && action.action));
    const viewerId = context.viewer && context.viewer.player_id;
    const viewer = (state.players || {})[viewerId] || {};
    const current = room.current_player_id === viewerId && Boolean(context.canMove);
    const bar = documentRef.createElement("section");
    bar.className = "blackjack-action-bar";
    bar.setAttribute("aria-label", "你的 21 点操作");
    const copy = documentRef.createElement("div");
    copy.className = "blackjack-action-copy";
    const heading = documentRef.createElement("strong");
    heading.textContent = current ? "轮到你" : (context.isTerminal ? "本局已结算" : "等待当前玩家");
    const hint = documentRef.createElement("span");
    hint.textContent = current
      ? `${valueLabel(viewer.value)} · 请选择要牌或停牌`
      : (viewer.outcome && viewer.outcome.result_text || valueLabel(viewer.value));
    copy.append(heading, hint);

    const actions = documentRef.createElement("div");
    actions.className = "blackjack-action-buttons";
    const makeButton = (action, label, detail, className) => {
      const button = documentRef.createElement("button");
      button.type = "button";
      button.className = `pixel-btn blackjack-action ${className}`;
      button.dataset.action = action;
      button.setAttribute("aria-label", `${label}，${detail}`);
      const main = documentRef.createElement("strong");
      main.textContent = label;
      const small = documentRef.createElement("span");
      small.textContent = detail;
      button.append(main, small);
      button.disabled = !current || !legalNames.has(action);
      button.addEventListener("click", async () => {
        if (!helpers.canMove() || !legalNames.has(action)) return;
        button.disabled = true;
        const submitted = await helpers.submitMove({action});
        if (!submitted && helpers.canMove()) button.disabled = false;
      });
      return button;
    };
    actions.append(
      makeButton("hit", "要牌", "HIT", "hit"),
      makeButton("stand", "停牌", "STAND", "stand")
    );
    bar.append(copy, actions);
    controls.appendChild(bar);
  }

  window.DuelGameUI.register("blackjack", {
    participantPresentation: "embedded",
    ownsPrivateStatePresentation: true,
    usesStandardMoveConfirmation: false,
    renderBoard,
    renderControls,
  });
}());

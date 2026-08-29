(function registerZhajinhuaRenderer() {
  "use strict";

  const STYLE_ID = "duel-game-zhajinhua-styles";
  const STYLE_HREF = "/static/games/zhajinhua.css?v=1.0.0";
  const SUIT_TEXT = {
    spades: "\u2660\uFE0E",
    hearts: "\u2665\uFE0E",
    diamonds: "\u2666\uFE0E",
    clubs: "\u2663\uFE0E",
  };
  const SUIT_LABELS = {
    spades: "黑桃",
    hearts: "红桃",
    diamonds: "方块",
    clubs: "梅花",
  };
  const STATUS_LABELS = {
    active: "在局",
    folded: "已弃牌",
    compare_lost: "比牌出局",
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
    link.dataset.duelGameStyle = "zhajinhua";
    documentRef.head.appendChild(link);
    return link;
  }

  function element(documentRef, tag, className, text = null) {
    const node = documentRef.createElement(tag);
    if (className) node.className = className;
    if (text !== null) node.textContent = String(text);
    return node;
  }

  function participantById(context, playerId) {
    return (context.participants || []).find(
      (participant) => participant.player_id === playerId
    ) || null;
  }

  function participantName(context, playerId) {
    const participant = participantById(context, playerId);
    return participant && (participant.display_name || participant.player_id)
      || String(playerId || "玩家");
  }

  function cardLabel(card) {
    if (!card || card.hidden) return "未查看的牌";
    return `${SUIT_LABELS[card.suit] || ""}${card.rank || ""}`;
  }

  function createCard(documentRef, card, ownerLabel) {
    const hidden = !card || card.hidden;
    const node = element(
      documentRef,
      "span",
      hidden
        ? "zhajinhua-card is-hidden"
        : `zhajinhua-card suit-${card.suit || "unknown"}`
    );
    node.setAttribute("role", "img");
    node.setAttribute(
      "aria-label",
      hidden ? `${ownerLabel}未查看的牌` : `${ownerLabel}${cardLabel(card)}`
    );
    if (hidden) {
      const back = element(documentRef, "span", "zhajinhua-card-back");
      back.setAttribute("aria-hidden", "true");
      node.appendChild(back);
      return node;
    }
    const corner = element(documentRef, "span", "zhajinhua-card-corner");
    corner.append(
      element(documentRef, "strong", "zhajinhua-card-rank", card.rank || ""),
      element(documentRef, "span", "zhajinhua-card-suit", SUIT_TEXT[card.suit] || "")
    );
    node.append(
      corner,
      element(documentRef, "span", "zhajinhua-card-center", SUIT_TEXT[card.suit] || "")
    );
    return node;
  }

  function playerStatus(state, playerId) {
    return state.players && state.players[playerId] || {};
  }

  function statusText(state, playerId) {
    const player = playerStatus(state, playerId);
    if (player.status && player.status !== "active") {
      return STATUS_LABELS[player.status] || player.status;
    }
    return player.seen ? "已看牌" : "未看牌";
  }

  function renderAvatar(documentRef, context, participant) {
    const avatar = element(documentRef, "span", "zhajinhua-avatar");
    if (
      context.helpers
      && typeof context.helpers.renderParticipantAvatar === "function"
    ) {
      context.helpers.renderParticipantAvatar(avatar, participant);
    } else {
      avatar.textContent = Array.from(
        String(participant.display_name || participant.player_id || "?")
      )[0] || "?";
    }
    return avatar;
  }

  function publicCardsFor(state, playerId) {
    const revealed = state.revealed_hands && state.revealed_hands[playerId];
    return revealed && Array.isArray(revealed.cards)
      ? revealed.cards
      : [{hidden: true}, {hidden: true}, {hidden: true}];
  }

  function renderSeat(documentRef, context, participant, cards, {viewer = false} = {}) {
    const playerId = participant.player_id;
    const state = context.state;
    const player = playerStatus(state, playerId);
    const seat = element(
      documentRef,
      "article",
      viewer ? "zhajinhua-seat is-viewer" : "zhajinhua-seat is-opponent"
    );
    seat.dataset.playerId = playerId;
    seat.classList.toggle("is-current", context.room.current_player_id === playerId);
    seat.classList.toggle("is-out", player.status && player.status !== "active");

    const identity = element(documentRef, "div", "zhajinhua-seat-identity");
    identity.append(
      renderAvatar(documentRef, context, participant),
      element(
        documentRef,
        "strong",
        "zhajinhua-seat-name",
        participant.display_name || playerId
      )
    );
    const badges = element(documentRef, "div", "zhajinhua-seat-badges");
    badges.append(
      element(documentRef, "span", "zhajinhua-status", statusText(state, playerId)),
      element(
        documentRef,
        "span",
        "zhajinhua-contribution",
        `已投 ${Number(player.contribution || 0)}`
      )
    );
    const hand = element(documentRef, "div", "zhajinhua-hand");
    hand.setAttribute("aria-label", `${participant.display_name || playerId}的三张牌`);
    (Array.isArray(cards) ? cards : []).forEach((card) => {
      hand.appendChild(createCard(documentRef, card, participant.display_name || playerId));
    });
    seat.append(identity, badges, hand);
    return seat;
  }

  function renderCompare(documentRef, context) {
    const comparison = context.state.last_compare;
    const panel = element(documentRef, "div", "zhajinhua-compare");
    if (!comparison) {
      panel.textContent = "比牌不会公开双方牌面";
      return panel;
    }
    const initiator = participantName(context, comparison.initiator_player_id);
    const target = participantName(context, comparison.target_player_id);
    const loser = participantName(context, comparison.loser_player_id);
    panel.textContent = comparison.tied
      ? `${initiator} 与 ${target} 同点；主动方 ${loser} 出局`
      : `${initiator} 对 ${target} · ${loser} 出局`;
    panel.classList.add("has-result");
    return panel;
  }

  function renderBoard(context) {
    const documentRef = context.board.ownerDocument || window.document;
    ensureStylesheet(documentRef);
    const state = context.state || {};
    const participants = [...(context.participants || [])].sort(
      (left, right) => Number(left.seat_index || 0) - Number(right.seat_index || 0)
    );
    const viewerId = context.viewer && context.viewer.player_id;
    const viewer = participantById(context, viewerId) || participants[0] || null;
    const opponents = participants.filter(
      (participant) => !viewer || participant.player_id !== viewer.player_id
    );
    const shell = element(documentRef, "section", "zhajinhua-game");
    shell.dataset.playerCount = String(participants.length);
    shell.dataset.round = String(state.flow && state.flow.round_number || 1);

    const topbar = element(documentRef, "header", "zhajinhua-topbar");
    const title = element(documentRef, "div", "zhajinhua-title");
    title.append(
      element(documentRef, "span", "zhajinhua-title-mark", "诈"),
      element(documentRef, "strong", "", "炸金花")
    );
    const metrics = element(documentRef, "div", "zhajinhua-metrics");
    metrics.append(
      element(
        documentRef,
        "span",
        "zhajinhua-metric pot",
        `底池 ${Number(state.pot || 0)}`
      ),
      element(
        documentRef,
        "span",
        "zhajinhua-metric",
        `闷注 ${Number(state.blind_unit || 1)}`
      ),
      element(
        documentRef,
        "span",
        "zhajinhua-metric",
        `第 ${Number(state.flow && state.flow.round_number || 1)}/${Number(state.max_rounds || 20)} 轮`
      )
    );
    topbar.append(title, metrics);

    const table = element(documentRef, "section", "zhajinhua-table");
    if (participants.length > 2) {
      const opponentRing = element(documentRef, "div", "zhajinhua-opponents");
      opponents.forEach((participant) => {
        opponentRing.appendChild(
          renderSeat(
            documentRef,
            context,
            participant,
            publicCardsFor(state, participant.player_id)
          )
        );
      });
      table.appendChild(opponentRing);
    }

    const center = element(documentRef, "div", "zhajinhua-center");
    center.append(
      element(documentRef, "span", "zhajinhua-center-label", "VIRTUAL POT"),
      element(documentRef, "strong", "zhajinhua-pot-value", Number(state.pot || 0)),
      renderCompare(documentRef, context)
    );
    table.appendChild(center);

    if (viewer) {
      const privateHand = context.privateState && Array.isArray(context.privateState.hand)
        ? context.privateState.hand
        : [{hidden: true}, {hidden: true}, {hidden: true}];
      const viewerSeat = renderSeat(
        documentRef, context, viewer, privateHand, {viewer: true}
      );
      const privateLabel = element(
        documentRef,
        "div",
        "zhajinhua-private-label",
        context.privateState && context.privateState.hand_revealed
          ? `仅你可见 · ${context.privateState.hand_type_label || "已看牌"}`
          : "尚未看牌 · 牌面保持背面"
      );
      viewerSeat.appendChild(privateLabel);
      table.appendChild(viewerSeat);
    }
    shell.append(topbar, table);
    context.board.replaceChildren(shell);
    context.board.dataset.playerCount = String(participants.length);
    return true;
  }

  function actionText(context, action) {
    if (action.action === "peek") return "看牌";
    if (action.action === "call") return `跟注 · ${action.cost}`;
    if (action.action === "raise") return `加至 ${action.unit} · 投 ${action.cost}`;
    if (action.action === "fold") return "弃牌";
    if (action.action === "compare") {
      return `比 ${participantName(context, action.target_player_id)} · ${action.cost}`;
    }
    return action.action;
  }

  function renderControls(context) {
    const documentRef = context.controls.ownerDocument || window.document;
    ensureStylesheet(documentRef);
    const legal = Array.isArray(context.legalActions) ? context.legalActions : [];
    const shell = element(documentRef, "section", "zhajinhua-controls");
    shell.setAttribute("aria-label", "炸金花服务端合法行动");
    const status = element(
      documentRef,
      "div",
      "zhajinhua-control-status",
      context.canMove
        ? "请选择服务端当前允许的行动"
        : context.isTerminal ? "本局已结束" : "等待其他玩家行动"
    );
    status.setAttribute("aria-live", "polite");
    const actions = element(documentRef, "div", "zhajinhua-actions");
    legal.forEach((action) => {
      const button = element(
        documentRef,
        "button",
        `zhajinhua-action action-${action.action}`,
        actionText(context, action)
      );
      button.type = "button";
      button.dataset.action = String(action.action);
      if (action.target_player_id) {
        button.dataset.targetPlayerId = String(action.target_player_id);
      }
      button.disabled = !context.canMove;
      button.addEventListener("click", async () => {
        if (button.disabled) return;
        button.disabled = true;
        try {
          await context.helpers.submitMove({...action});
        } finally {
          button.disabled = !context.helpers.canMove();
        }
      });
      actions.appendChild(button);
    });
    shell.append(status, actions);
    context.controls.replaceChildren(shell);
    return true;
  }

  window.DuelGameUI.register("zhajinhua", {
    participantPresentation: "embedded",
    ownsPrivateStatePresentation: true,
    usesStandardMoveConfirmation: false,
    renderBoard,
    renderControls,
  });
}());

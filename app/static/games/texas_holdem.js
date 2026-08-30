(function registerTexasHoldemRenderer() {
  "use strict";

  const STYLE_ID = "duel-game-texas-holdem-styles";
  const STYLE_HREF = "/static/games/texas_holdem.css?v=1.0.1";
  const SUIT_TEXT = {
    spades: "S",
    hearts: "H",
    diamonds: "D",
    clubs: "C",
  };
  const SUIT_LABELS = {
    spades: "黑桃",
    hearts: "红桃",
    diamonds: "方块",
    clubs: "梅花",
  };
  const STREET_LABELS = {
    waiting: "等待入座",
    preflop: "翻牌前",
    flop: "翻牌",
    turn: "转牌",
    river: "河牌",
    finished: "已结算",
  };
  const STATUS_LABELS = {
    waiting: "等待",
    active: "在局",
    all_in: "全下",
    folded: "已弃牌",
  };
  const RING_LAYOUTS = {
    1: [[50, 14]],
    2: [[28, 15], [72, 15]],
    3: [[15, 43], [50, 13], [85, 43]],
    4: [[14, 58], [29, 14], [71, 14], [86, 58]],
    5: [[14, 63], [20, 25], [50, 11], [80, 25], [86, 63]],
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
    link.dataset.duelGameStyle = "texas_holdem";
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

  function nameFor(participant) {
    return participant && (participant.display_name || participant.player_id) || "玩家";
  }

  function avatar(documentRef, context, participant) {
    const node = element(documentRef, "span", "texas-avatar");
    if (context.helpers && typeof context.helpers.renderParticipantAvatar === "function") {
      context.helpers.renderParticipantAvatar(node, participant);
    } else {
      node.textContent = Array.from(nameFor(participant))[0] || "?";
    }
    return node;
  }

  function cardLabel(card) {
    if (!card || card.hidden) return "未公开底牌";
    return `${SUIT_LABELS[card.suit] || ""}${card.rank || ""}`;
  }

  function renderCard(documentRef, card, ownerLabel, {empty = false} = {}) {
    if (empty) {
      const slot = element(documentRef, "span", "texas-card is-empty");
      slot.setAttribute("aria-hidden", "true");
      return slot;
    }
    const hidden = !card || card.hidden;
    const node = element(
      documentRef,
      "span",
      hidden
        ? "texas-card is-hidden"
        : `texas-card suit-${card.suit || "unknown"}`
    );
    node.setAttribute("role", "img");
    node.setAttribute("aria-label", hidden ? `${ownerLabel}未公开底牌` : `${ownerLabel}${cardLabel(card)}`);
    if (hidden) {
      node.appendChild(element(documentRef, "span", "texas-card-back", "DUEL"));
      return node;
    }
    node.append(
      element(documentRef, "strong", "texas-card-rank", card.rank || ""),
      element(documentRef, "span", "texas-card-suit", SUIT_TEXT[card.suit] || "")
    );
    return node;
  }

  function roleBadges(documentRef, state, playerId) {
    const row = element(documentRef, "span", "texas-role-badges");
    if (state.dealer_player_id === playerId) row.appendChild(element(documentRef, "b", "is-dealer", "D"));
    if (state.small_blind_player_id === playerId) row.appendChild(element(documentRef, "b", "is-sb", "SB"));
    if (state.big_blind_player_id === playerId) row.appendChild(element(documentRef, "b", "is-bb", "BB"));
    return row;
  }

  function publicHoleCards(state, playerId) {
    const revealed = state.showdown && state.showdown[playerId];
    if (revealed && Array.isArray(revealed.cards)) return revealed.cards;
    return [{hidden: true}, {hidden: true}];
  }

  function renderSeat(documentRef, context, participant, cards, viewer) {
    const state = context.state || {};
    const playerId = participant.player_id;
    const player = state.players && state.players[playerId] || {};
    const seat = element(
      documentRef,
      "article",
      `texas-seat${viewer ? " is-viewer" : " is-opponent"}`
    );
    seat.dataset.playerId = playerId;
    seat.classList.toggle("is-current", (context.room.current_player_id || state.turn_player_id) === playerId);
    seat.classList.toggle("is-folded", player.status === "folded");
    seat.classList.toggle("is-all-in", player.status === "all_in");

    const identity = element(documentRef, "header", "texas-seat-head");
    const copy = element(documentRef, "span", "texas-seat-copy");
    copy.append(
      element(documentRef, "strong", "texas-seat-name", nameFor(participant)),
      roleBadges(documentRef, state, playerId)
    );
    identity.append(avatar(documentRef, context, participant), copy);

    const hand = element(documentRef, "div", "texas-hole-cards");
    (Array.isArray(cards) ? cards : []).forEach((card) => {
      hand.appendChild(renderCard(documentRef, card, `${nameFor(participant)}的`));
    });
    const meta = element(documentRef, "div", "texas-seat-meta");
    meta.append(
      element(documentRef, "span", "texas-stack", `Stack ${Number(player.stack || 0)}`),
      element(documentRef, "span", "texas-bet", `Bet ${Number(player.current_bet || 0)}`),
      element(documentRef, "span", "texas-status", STATUS_LABELS[player.status] || player.status || "在局")
    );
    const handInfo = state.showdown && state.showdown[playerId];
    if (handInfo && handInfo.hand_type_label) {
      meta.appendChild(element(documentRef, "span", "texas-hand-rank", handInfo.hand_type_label));
    }
    const payout = state.game_result && state.game_result.payout_by_player
      && Number(state.game_result.payout_by_player[playerId] || 0);
    if (payout > 0) {
      meta.appendChild(element(documentRef, "span", "texas-payout", `Won ${payout}`));
    }
    seat.append(identity, hand, meta);
    return seat;
  }

  function renderCenter(documentRef, context) {
    const state = context.state || {};
    const center = element(documentRef, "section", "texas-center");
    const board = element(documentRef, "div", "texas-board-cards");
    const cards = Array.isArray(state.board) ? state.board : [];
    for (let index = 0; index < 5; index += 1) {
      board.appendChild(renderCard(
        documentRef,
        cards[index],
        "公共牌",
        {empty: !cards[index]}
      ));
    }
    const pot = element(
      documentRef,
      "strong",
      "texas-pot",
      state.game_result ? `本手底池 ${Number(state.total_pot || 0)}` : `底池 ${Number(state.pot || 0)}`
    );
    const sidePots = element(documentRef, "div", "texas-side-pots");
    const pots = Array.isArray(state.pots) ? state.pots : [];
    pots.forEach((item, index) => {
      const winners = Array.isArray(item.winner_player_ids)
        ? item.winner_player_ids.map((playerId) => nameFor(participantById(context, playerId)))
        : [];
      sidePots.appendChild(element(
        documentRef,
        "span",
        index === 0 ? "is-main" : "is-side",
        `${index === 0 ? "Main" : `Side ${index}`} ${Number(item.amount || 0)}${winners.length ? ` · ${winners.join("/")}` : ""}`
      ));
    });
    const status = element(
      documentRef,
      "span",
      "texas-street",
      STREET_LABELS[state.street] || state.street || "翻牌前"
    );
    center.append(status, board, pot, sidePots);
    if (state.game_result && state.game_result.result_text) {
      center.appendChild(element(documentRef, "p", "texas-result", state.game_result.result_text));
    }
    return center;
  }

  function renderBoard(context) {
    const documentRef = context.board.ownerDocument || context.document || window.document;
    ensureStylesheet(documentRef);
    const state = context.state || {};
    const participants = [...(context.participants || [])].sort(
      (left, right) => Number(left.seat_index || 0) - Number(right.seat_index || 0)
    );
    const viewerId = context.viewer && context.viewer.player_id;
    const viewer = participantById(context, viewerId) || participants[0] || null;
    const viewerIndex = viewer ? participants.indexOf(viewer) : -1;
    const opponents = viewerIndex < 0
      ? participants.slice(1)
      : Array.from({length: Math.max(0, participants.length - 1)}, (_unused, offset) => (
        participants[(viewerIndex + offset + 1) % participants.length]
      ));
    const scroll = element(documentRef, "div", "texas-table-scroll");
    scroll.tabIndex = 0;
    scroll.setAttribute("aria-label", "德州扑克牌桌，可横向滚动");
    const table = element(documentRef, "section", "texas-table");
    table.dataset.playerCount = String(participants.length);
    table.appendChild(renderCenter(documentRef, context));

    const layout = RING_LAYOUTS[opponents.length] || RING_LAYOUTS[5];
    opponents.forEach((participant, index) => {
      const seat = renderSeat(
        documentRef,
        context,
        participant,
        publicHoleCards(state, participant.player_id),
        false
      );
      const position = layout[index] || [50, 14];
      seat.style.left = `${position[0]}%`;
      seat.style.top = `${position[1]}%`;
      seat.dataset.ringIndex = String(index);
      table.appendChild(seat);
    });

    if (viewer) {
      const privateCards = context.privateState && Array.isArray(context.privateState.hand)
        ? context.privateState.hand
        : [{hidden: true}, {hidden: true}];
      table.appendChild(renderSeat(documentRef, context, viewer, privateCards, true));
    }
    scroll.appendChild(table);
    context.board.replaceChildren(scroll);
    context.board.dataset.playerCount = String(participants.length);
    return true;
  }

  function actionLabel(action) {
    if (action.action === "check") return "过牌";
    if (action.action === "fold") return "弃牌";
    if (action.action === "call") return `跟注 ${Number(action.amount || 0)}`;
    if (action.action === "all_in") return `全下至 ${Number(action.amount || 0)}`;
    if (action.action === "bet") return "下注";
    if (action.action === "raise") return "加注";
    return action.action || "行动";
  }

  function renderControls(context) {
    const documentRef = context.controls.ownerDocument || context.document || window.document;
    ensureStylesheet(documentRef);
    const legal = Array.isArray(context.legalActions) ? context.legalActions : [];
    const uiState = context.uiState || {};
    const riskyActions = new Set(["fold", "call", "all_in"]);
    const pendingAction = legal.find(
      (action) => riskyActions.has(action.action)
        && action.action === uiState.texasPendingAction
    ) || null;
    if (uiState.texasPendingAction && !pendingAction) {
      delete uiState.texasPendingAction;
    }
    const rerender = () => {
      if (context.helpers && typeof context.helpers.rerender === "function") {
        context.helpers.rerender();
      }
    };
    const submitOnce = async (move, button) => {
      if (uiState.texasSubmitting || !context.helpers.canMove()) return false;
      uiState.texasSubmitting = true;
      button.disabled = true;
      let submitted = false;
      try {
        submitted = await context.helpers.submitMove(move);
        return submitted;
      } finally {
        if (!submitted) {
          uiState.texasSubmitting = false;
          button.disabled = !context.helpers.canMove();
        }
      }
    };
    const shell = element(documentRef, "section", "texas-controls");
    shell.setAttribute("aria-label", "德州扑克服务端合法行动");
    shell.appendChild(element(
      documentRef,
      "p",
      "texas-control-status",
      context.canMove ? "轮到你 · 仅提交服务端允许的行动" : (context.isTerminal ? "本手牌已结算" : "等待其他席位")
    ));
    const actions = element(documentRef, "div", "texas-actions");

    legal.forEach((action) => {
      if (action.action === "bet" || action.action === "raise") {
        const group = element(documentRef, "div", `texas-range-action action-${action.action}`);
        const label = element(documentRef, "label", "", actionLabel(action));
        const input = documentRef.createElement("input");
        input.type = "range";
        input.min = String(action.min_amount);
        input.max = String(action.max_amount);
        input.step = "1";
        input.value = String(action.amount);
        input.dataset.actionAmount = action.action;
        const value = element(documentRef, "output", "texas-range-value", action.amount);
        input.addEventListener("input", () => { value.textContent = input.value; });
        const button = element(documentRef, "button", "texas-action is-primary", `确认${actionLabel(action)}`);
        button.type = "button";
        button.dataset.action = action.action;
        button.disabled = !context.canMove;
        button.addEventListener("click", async () => {
          await submitOnce({
            action: action.action,
            amount: Number(input.value),
          }, button);
        });
        label.appendChild(value);
        group.append(label, input, button);
        actions.appendChild(group);
        return;
      }
      const button = element(
        documentRef,
        "button",
        `texas-action action-${action.action}`,
        actionLabel(action)
      );
      button.type = "button";
      button.dataset.action = action.action;
      button.classList.toggle(
        "is-selected",
        Boolean(pendingAction && pendingAction.action === action.action)
      );
      if (riskyActions.has(action.action)) {
        button.setAttribute(
          "aria-pressed",
          String(Boolean(pendingAction && pendingAction.action === action.action))
        );
      }
      button.disabled = !context.canMove;
      button.addEventListener("click", async () => {
        if (uiState.texasSubmitting || !context.helpers.canMove()) return;
        if (riskyActions.has(action.action)) {
          uiState.texasPendingAction = action.action;
          rerender();
          return;
        }
        await submitOnce({...action}, button);
      });
      actions.appendChild(button);
    });
    shell.appendChild(actions);
    if (pendingAction) {
      const confirmation = element(documentRef, "div", "texas-confirmation");
      confirmation.setAttribute("role", "group");
      confirmation.setAttribute("aria-label", "确认德州扑克行动");
      confirmation.appendChild(element(
        documentRef,
        "span",
        "texas-confirmation-copy",
        `已选择：${actionLabel(pendingAction)}`
      ));
      const cancel = element(documentRef, "button", "texas-confirm-cancel", "取消");
      cancel.type = "button";
      cancel.addEventListener("click", () => {
        if (uiState.texasSubmitting) return;
        delete uiState.texasPendingAction;
        rerender();
      });
      const confirm = element(
        documentRef,
        "button",
        "texas-confirm-submit",
        `确认${actionLabel(pendingAction)}`
      );
      confirm.type = "button";
      confirm.addEventListener("click", async () => {
        await submitOnce({...pendingAction}, confirm);
      });
      confirmation.append(cancel, confirm);
      shell.appendChild(confirmation);
    }
    context.controls.replaceChildren(shell);
    return true;
  }

  window.DuelGameUI.register("texas_holdem", {
    participantPresentation: "embedded",
    ownsPrivateStatePresentation: true,
    usesStandardMoveConfirmation: false,
    renderBoard,
    renderControls,
  });
}());

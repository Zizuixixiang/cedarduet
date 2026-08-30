(function installMahjongRenderer(global) {
  "use strict";

  const STYLE_ID = "duel-game-style-mahjong";
  const STYLE_HREF = "/static/games/mahjong.css?v=0.1.5";
  const POSITION_ORDER = ["bottom", "right", "top", "left"];

  function ensureStyle() {
    if (global.document.getElementById(STYLE_ID)) return;
    const link = global.document.createElement("link");
    link.id = STYLE_ID;
    link.rel = "stylesheet";
    link.href = STYLE_HREF;
    global.document.head.appendChild(link);
  }

  function el(tag, className = "", text = "") {
    const node = global.document.createElement(tag);
    if (className) node.className = className;
    if (text !== "") node.textContent = text;
    return node;
  }

  function viewerSeat(context) {
    const direct = Number(context.viewer && context.viewer.seat);
    if (Number.isInteger(direct)) return direct;
    const viewerId = context.viewer && context.viewer.player_id;
    const participant = (context.participants || []).find(
      (item) => item.player_id === viewerId
    );
    return participant ? Number(participant.seat_index) : 0;
  }

  function positionFor(participant, context) {
    const difference = (
      Number(participant.seat_index) - viewerSeat(context) + 4
    ) % 4;
    return POSITION_ORDER[difference];
  }

  function publicTile(tile, {button = false} = {}) {
    if (!tile || tile.back) {
      const back = el("span", "mahjong-tile mahjong-tile-back");
      back.setAttribute("aria-label", "暗牌");
      return back;
    }
    const node = el(button ? "button" : "span", "mahjong-tile");
    if (button) node.type = "button";
    node.dataset.cardId = String(tile.id || "");
    node.dataset.suit = String(tile.suit || String(tile.code || "")[0] || "");
    const code = String(tile.code || "");
    if (["F", "J"].includes(code[0])) {
      node.classList.add("honor");
    }
    const face = el("span", "mahjong-tile-face", String(tile.label || code));
    node.appendChild(face);
    node.setAttribute("aria-label", String(tile.label || code));
    return node;
  }

  function meldNode(meld) {
    const group = el("div", `mahjong-meld kind-${meld.kind || "unknown"}`);
    group.setAttribute("aria-label", meld.kind === "concealed_gang" ? "暗杠" : "副露");
    (meld.tiles || []).forEach((tile) => group.appendChild(publicTile(tile)));
    return group;
  }

  function seatNode(participant, context) {
    const position = positionFor(participant, context);
    const state = context.state || {};
    const viewerId = context.viewer && context.viewer.player_id;
    const isViewer = participant.player_id === viewerId;
    const current = participant.player_id === (
      state.turn_player_id || (context.room && context.room.current_player_id)
    );
    const seat = el("section", `mahjong-seat position-${position}`);
    seat.dataset.playerId = participant.player_id;
    seat.dataset.position = position;
    seat.classList.toggle("viewer", isViewer);
    seat.classList.toggle("current", current);
    seat.setAttribute("aria-current", current ? "true" : "false");

    const identity = el("div", "mahjong-seat-identity");
    const avatar = el("span", "mahjong-seat-avatar");
    if (context.helpers && context.helpers.renderParticipantAvatar) {
      context.helpers.renderParticipantAvatar(avatar, participant);
    } else {
      avatar.textContent = String(participant.display_name || participant.player_id).slice(0, 1);
    }
    const name = el(
      "strong", "mahjong-seat-name",
      `${participant.display_name || participant.player_id}${isViewer ? "（你）" : ""}`
    );
    const badges = el("span", "mahjong-seat-badges");
    badges.appendChild(el("i", "mahjong-wind", String(state.seat_winds && state.seat_winds[participant.player_id] || "?")));
    if (participant.player_id === state.dealer_player_id) {
      badges.appendChild(el("i", "mahjong-dealer", "庄"));
    }
    if (current) badges.appendChild(el("i", "mahjong-turn", "行动"));
    identity.append(avatar, name, badges);

    const backs = el("div", "mahjong-opponent-hand");
    if (!isViewer) {
      const count = Number(state.hand_counts && state.hand_counts[participant.player_id] || 0);
      for (let index = 0; index < count; index += 1) {
        backs.appendChild(publicTile({back: true}));
      }
      backs.setAttribute("aria-label", `${count} 张暗牌`);
    }

    const melds = el("div", "mahjong-seat-melds");
    const ownMelds = context.privateState && context.privateState.own_melds;
    const visibleMelds = isViewer && Array.isArray(ownMelds)
      ? ownMelds
      : state.melds && state.melds[participant.player_id] || [];
    visibleMelds.forEach((meld) => melds.appendChild(meldNode(meld)));
    seat.append(identity, backs, melds);
    return seat;
  }

  function discardZone(participant, context) {
    const position = positionFor(participant, context);
    const zone = el("section", `mahjong-discards discard-${position}`);
    const heading = el(
      "span", "mahjong-discard-label",
      `${context.state.seat_winds && context.state.seat_winds[participant.player_id] || "?"}家弃牌`
    );
    const grid = el("div", "mahjong-discard-grid");
    const last = context.state.last_discard;
    (context.state.discards && context.state.discards[participant.player_id] || [])
      .forEach((tile) => {
        const node = publicTile(tile);
        if (last && last.tile && last.tile.id === tile.id) node.classList.add("last-discard");
        grid.appendChild(node);
      });
    zone.append(heading, grid);
    return zone;
  }

  function statusNode(context) {
    const state = context.state || {};
    const status = el("section", "mahjong-center-status");
    const primary = el("strong", "mahjong-round", `${state.round_label || "东一局"} · 圈风${state.prevalent_wind || "东"}`);
    const wall = el("span", "mahjong-wall-count", `牌墙 ${Number(state.wall_remaining || 0)} 张`);
    const current = (context.participants || []).find(
      (item) => item.player_id === state.turn_player_id
    );
    let actionText = current
      ? `当前：${current.display_name || current.player_id}（${state.seat_winds && state.seat_winds[current.player_id] || "?"}家）`
      : "本手已结束";
    const response = state.response_window;
    if (response && response.current_responder_id) {
      const responder = (context.participants || []).find(
        (item) => item.player_id === response.current_responder_id
      );
      const priority = {hu: "和牌", peng_gang: "碰/杠", chi: "吃"}[response.current_priority] || "响应";
      actionText = `等待 ${responder && (responder.display_name || responder.player_id) || "玩家"} ${priority}`;
    }
    const action = el("span", "mahjong-current-action", actionText);
    status.append(primary, wall, action);
    if (state.game_result) {
      const result = state.game_result;
      const resultText = result.draw
        ? "荒牌，本手结束"
        : `${result.seat_wind || ""}家和牌 · ${result.total_fan || 0} 番`;
      status.appendChild(el("strong", "mahjong-result", resultText));
    }
    return status;
  }

  function saveHandScroll(context, scroller) {
    const scrollLeft = Number(scroller.scrollLeft);
    if (Number.isFinite(scrollLeft)) {
      context.uiState.mahjongHandScrollLeft = Math.max(0, scrollLeft);
    }
  }

  function restoreHandScroll(context, scroller) {
    const scrollLeft = Number(context.uiState.mahjongHandScrollLeft);
    if (Number.isFinite(scrollLeft) && scrollLeft >= 0) {
      scroller.scrollLeft = scrollLeft;
    }
  }

  function ownHandNode(context) {
    const wrap = el("section", "mahjong-own-hand-wrap");
    const head = el("div", "mahjong-own-hand-head");
    const shanten = context.privateState && context.privateState.shanten;
    const basis = context.privateState && context.privateState.shanten_basis;
    head.textContent = shanten === null || shanten === undefined
      ? "我的手牌"
      : `我的手牌 · ${shanten === 0 ? "听牌" : `${shanten} 向听`}${basis === "after_best_discard" ? "（最佳打牌后）" : ""}`;
    const scroll = el("div", "mahjong-own-hand-scroll");
    scroll.addEventListener("scroll", () => saveHandScroll(context, scroll), {passive: true});
    const legal = Array.isArray(context.legalActions) ? context.legalActions : [];
    const discardById = new Map();
    legal.filter((action) => action.kind === "discard").forEach((action) => {
      const id = String(action.action_id || "").slice("discard:".length);
      discardById.set(id, action);
    });
    const selected = context.uiState.mahjongSelectedTileId;
    const drawnId = context.privateState && context.privateState.drawn_tile_id;
    (context.privateState && context.privateState.hand || []).forEach((tile) => {
      const node = publicTile(tile, {button: true});
      const selectable = discardById.has(String(tile.id));
      node.disabled = !selectable;
      node.classList.toggle("selected", selected === tile.id);
      node.classList.toggle("drawn", drawnId === tile.id);
      node.setAttribute("aria-pressed", String(selected === tile.id));
      node.addEventListener("click", () => {
        if (!selectable) return;
        saveHandScroll(context, scroll);
        context.uiState.mahjongSelectedTileId = selected === tile.id ? null : tile.id;
        context.helpers.rerender();
      });
      scroll.appendChild(node);
    });
    wrap.append(head, scroll);
    const terminalHands = context.state && context.state.terminal_hands;
    if (terminalHands && typeof terminalHands === "object") {
      const review = el("details", "mahjong-terminal-review");
      review.appendChild(el("summary", "mahjong-terminal-summary", "终局手牌复盘 · 展开查看四家牌面"));
      const rows = el("div", "mahjong-terminal-rows");
      Object.entries(terminalHands).forEach(([playerId, tiles]) => {
        const participant = (context.participants || []).find(
          (item) => item.player_id === playerId
        );
        const row = el("section", "mahjong-terminal-row");
        row.dataset.playerId = playerId;
        row.appendChild(el(
          "strong",
          "mahjong-terminal-name",
          `${participant && (participant.display_name || participant.player_id) || playerId} · ${Array.isArray(tiles) ? tiles.length : 0} 张`
        ));
        const faces = el("div", "mahjong-terminal-hand");
        (Array.isArray(tiles) ? tiles : []).forEach(
          (tile) => faces.appendChild(publicTile(tile))
        );
        row.appendChild(faces);
        rows.appendChild(row);
      });
      review.appendChild(rows);
      wrap.appendChild(review);
    }
    return {wrap, scroll};
  }

  function renderBoard(context) {
    ensureStyle();
    context.helpers.setBoardLayout({
      ariaLabel: "四人国标麻将桌",
    });
    context.board.classList.add("mahjong-board-layout");
    const table = el("div", "mahjong-table mahjong-table-four-sides");
    const participants = [...(context.participants || [])].sort(
      (left, right) => Number(left.seat_index) - Number(right.seat_index)
    );
    participants.forEach((participant) => table.appendChild(seatNode(participant, context)));
    const center = el("main", "mahjong-center");
    const discardTable = el("div", "mahjong-discard-table mahjong-four-way-discards");
    participants.forEach((participant) => discardTable.appendChild(discardZone(participant, context)));
    discardTable.appendChild(statusNode(context));
    center.appendChild(discardTable);
    table.appendChild(center);
    const ownHand = ownHandNode(context);
    table.appendChild(ownHand.wrap);
    context.board.replaceChildren(table);
    restoreHandScroll(context, ownHand.scroll);
  }

  const CONFIRMED_ACTION_KINDS = new Set([
    "hu", "chi", "peng", "ming_gang", "concealed_gang", "added_gang", "pass",
  ]);

  async function submitAction(action, context, button) {
    const uiState = context.uiState || {};
    const canMove = () => (
      context.helpers && typeof context.helpers.canMove === "function"
        ? context.helpers.canMove()
        : context.canMove
    );
    if (uiState.mahjongSubmitting || !canMove()) return false;
    uiState.mahjongSubmitting = true;
    button.disabled = true;
    let submitted = false;
    try {
      submitted = await context.helpers.submitMove({
        action: "act",
        action_id: action.action_id,
      });
      return submitted;
    } finally {
      if (!submitted) {
        uiState.mahjongSubmitting = false;
        button.disabled = !canMove();
      }
    }
  }

  function actionDisplayLabel(action) {
    const fallback = String(
      action.label || action.public_label || action.kind || "行动"
    );
    if (action.kind !== "hu") return fallback;
    const explicitFan = action.total_fan;
    if (explicitFan !== null && explicitFan !== undefined) {
      const fan = Number(explicitFan);
      if (Number.isFinite(fan)) return `胡（${fan} 番）`;
    }
    for (const source of [action.label, action.public_label]) {
      const match = String(source || "").match(/(\d+)\s*番/);
      if (match) return `胡（${match[1]} 番）`;
    }
    return "胡牌";
  }

  function actionButton(action, context, {requiresConfirmation = false} = {}) {
    const button = el(
      "button",
      `mahjong-action kind-${action.kind || "act"}`,
      actionDisplayLabel(action)
    );
    button.type = "button";
    button.disabled = !context.canMove;
    button.dataset.actionId = action.action_id;
    const selected = requiresConfirmation
      && context.uiState.mahjongPendingActionId === action.action_id;
    button.classList.toggle("selected", selected);
    if (requiresConfirmation) {
      button.setAttribute("aria-pressed", String(selected));
    }
    button.addEventListener("click", async () => {
      if (context.uiState.mahjongSubmitting) return;
      if (requiresConfirmation) {
        context.uiState.mahjongPendingActionId = action.action_id;
        context.helpers.rerender();
        return;
      }
      await submitAction(action, context, button);
    });
    return button;
  }

  function controlHint(text, state) {
    const hint = el(
      "span",
      `mahjong-waiting mahjong-control-hint state-${state}`,
      text
    );
    hint.setAttribute("role", "status");
    return hint;
  }

  function renderControls(context) {
    const legal = Array.isArray(context.legalActions) ? context.legalActions : [];
    const controls = el("div", "mahjong-controls");
    if (context.isTerminal) {
      delete context.uiState.mahjongPendingActionId;
      controls.appendChild(controlHint("本手已结束", "terminal"));
      context.controls.replaceChildren(controls);
      return;
    }
    if (!context.canMove) {
      controls.appendChild(controlHint("等待其他玩家行动", "waiting"));
      context.controls.replaceChildren(controls);
      return;
    }
    const pendingAction = legal.find(
      (action) => CONFIRMED_ACTION_KINDS.has(action.kind)
        && action.action_id === context.uiState.mahjongPendingActionId
    ) || null;
    if (context.uiState.mahjongPendingActionId && !pendingAction) {
      delete context.uiState.mahjongPendingActionId;
    }
    const selected = context.uiState.mahjongSelectedTileId;
    const discard = legal.find(
      (action) => action.kind === "discard" && action.action_id === `discard:${selected}`
    );
    if (discard) {
      const submit = actionButton({...discard, label: discard.label || "打出所选牌"}, context);
      submit.classList.add("mahjong-discard-button");
      controls.appendChild(submit);
    }
    legal.filter((action) => action.kind !== "discard").forEach(
      (action) => controls.appendChild(actionButton(action, context, {
        requiresConfirmation: CONFIRMED_ACTION_KINDS.has(action.kind),
      }))
    );
    const hasDiscard = legal.some((action) => action.kind === "discard");
    if (hasDiscard && !discard && !pendingAction) {
      controls.appendChild(controlHint("请选择一张手牌打出", "select"));
    }
    if (pendingAction) {
      const pendingLabel = actionDisplayLabel(pendingAction);
      const confirmation = el("div", "mahjong-confirmation");
      confirmation.setAttribute("role", "group");
      confirmation.setAttribute("aria-label", "确认麻将响应");
      confirmation.appendChild(el(
        "span",
        "mahjong-confirmation-copy",
        `已选择：${pendingLabel}`
      ));
      const cancel = el("button", "mahjong-confirm-cancel", "取消");
      cancel.type = "button";
      cancel.addEventListener("click", () => {
        if (context.uiState.mahjongSubmitting) return;
        delete context.uiState.mahjongPendingActionId;
        context.helpers.rerender();
      });
      const confirm = el(
        "button",
        "mahjong-confirm-submit",
        `确认${pendingLabel}`
      );
      confirm.type = "button";
      confirm.addEventListener("click", async () => {
        await submitAction(pendingAction, context, confirm);
      });
      confirmation.append(cancel, confirm);
      controls.appendChild(confirmation);
    }
    if (!controls.children.length) {
      controls.appendChild(controlHint("等待其他玩家行动", "waiting"));
    }
    context.controls.replaceChildren(controls);
  }

  global.DuelGameUI.register("mahjong", {
    participantPresentation: "board-edge",
    ownsPrivateStatePresentation: true,
    usesStandardMoveConfirmation: false,
    renderBoard,
    renderControls,
  });
}(window));

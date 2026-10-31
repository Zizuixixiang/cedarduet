(function installMahjongRenderer(global) {
  "use strict";

  const STYLE_ID = "duel-game-style-mahjong";
  const STYLE_HREF = "/static/games/mahjong.css?v=0.9.29";
  const POSITION_ORDER = ["bottom", "right", "top", "left"];
  const TILE_UNICODE = Object.freeze({
    W1: "🀇", W2: "🀈", W3: "🀉", W4: "🀊", W5: "🀋",
    W6: "🀌", W7: "🀍", W8: "🀎", W9: "🀏",
    T1: "🀐", T2: "🀑", T3: "🀒", T4: "🀓", T5: "🀔",
    T6: "🀕", T7: "🀖", T8: "🀗", T9: "🀘",
    B1: "🀙", B2: "🀚", B3: "🀛", B4: "🀜", B5: "🀝",
    B6: "🀞", B7: "🀟", B8: "🀠", B9: "🀡",
    F1: "🀀", F2: "🀁", F3: "🀂", F4: "🀃",
    J1: "🀄", J2: "🀅", J3: "🀆",
  });

  function ensureStyle() {
    const existing = global.document.getElementById(STYLE_ID);
    if (existing) return existing;
    const link = global.document.createElement("link");
    link.id = STYLE_ID;
    link.rel = "stylesheet";
    link.href = STYLE_HREF;
    global.document.head.appendChild(link);
    return link;
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

  function isTerminalState(context) {
    const state = context.state || {};
    const room = context.room || {};
    return Boolean(
      context.isTerminal
      || state.phase === "finished"
      || (state.flow || {}).phase === "finished"
      || ["finished", "archived"].includes(room.status)
    );
  }

  function actionablePlayerId(context) {
    if (isTerminalState(context)) return null;
    const state = context.state || {};
    if (!context.room || context.room.status !== "playing") return null;
    const response = state.response_window;
    if (response && response.current_responder_id) {
      return response.current_responder_id;
    }
    if (
      state.phase === "response"
      || (state.flow || {}).phase === "response"
    ) {
      return null;
    }
    return state.turn_player_id || (context.room && context.room.current_player_id) || null;
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
    const label = String(tile.label || code);
    if (["F", "J"].includes(code[0])) {
      node.classList.add("honor");
    }
    const face = el("span", "mahjong-tile-face", TILE_UNICODE[code] || label);
    node.appendChild(face);
    node.setAttribute("aria-label", label);
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
    const current = participant.player_id === actionablePlayerId(context);
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
    identity.append(avatar, name, badges);

    const turnIndicator = current
      ? el("span", "mahjong-turn-indicator", "行动中")
      : null;
    if (turnIndicator) turnIndicator.setAttribute("aria-label", "当前行动玩家，行动中");

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
    visibleMelds.forEach((meld) => {
      const hideConcealed = !isTerminalState(context) && !isViewer && meld.kind === "concealed_gang";
      melds.appendChild(meldNode(hideConcealed
        ? {...meld, tiles: (meld.tiles || []).map(() => ({back: true}))} : meld));
    });
    seat.append(identity, backs, melds);
    if (turnIndicator) seat.appendChild(turnIndicator);
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
      (item) => item.player_id === actionablePlayerId(context)
    );
    let actionText = current
      ? `当前：${current.display_name || current.player_id}（${state.seat_winds && state.seat_winds[current.player_id] || "?"}家）`
      : "本手已结束";
    const response = state.response_window;
    if (
      current
      && response
      && response.current_responder_id === current.player_id
    ) {
      const responder = (context.participants || []).find(
        (item) => item.player_id === response.current_responder_id
      );
      const priority = {hu: "和牌", peng_gang: "碰/杠", chi: "吃"}[response.current_priority] || "响应";
      actionText = `等待 ${responder && (responder.display_name || responder.player_id) || "玩家"} ${priority}`;
    }
    const action = el("span", "mahjong-current-action", actionText);
    status.append(primary, wall, action);
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
    const terminal = isTerminalState(context);
    if (terminal) delete context.uiState.mahjongSelectedTileId;
    const viewerId = context.viewer && context.viewer.player_id;
    const turnPlayerId = actionablePlayerId(context);
    wrap.classList.toggle("current", Boolean(viewerId && viewerId === turnPlayerId));
    const head = el("div", "mahjong-own-hand-head");
    const shanten = context.privateState && context.privateState.shanten;
    const basis = context.privateState && context.privateState.shanten_basis;
    head.textContent = shanten === null || shanten === undefined
      ? "我的手牌"
      : `我的手牌 · ${shanten === 0 ? "听牌" : `${shanten} 向听`}${basis === "after_best_discard" ? "（最佳打牌后）" : ""}`;
    const scroll = el("div", "mahjong-own-hand-scroll");
    scroll.addEventListener("scroll", () => saveHandScroll(context, scroll), {passive: true});
    const legal = !terminal && Array.isArray(context.legalActions)
      ? context.legalActions
      : [];
    const discardById = new Map();
    legal.filter((action) => action.kind === "discard").forEach((action) => {
      const id = String(action.action_id || "").slice("discard:".length);
      discardById.set(id, action);
    });
    const selected = terminal ? null : context.uiState.mahjongSelectedTileId;
    const drawnId = context.privateState && context.privateState.drawn_tile_id;
    (context.privateState && context.privateState.hand || []).forEach((tile) => {
      const node = publicTile(tile, {button: true});
      const selectable = !terminal && discardById.has(String(tile.id));
      node.disabled = !selectable;
      node.classList.toggle("selected", selected === tile.id);
      node.classList.toggle("drawn", drawnId === tile.id);
      node.setAttribute("aria-pressed", String(selected === tile.id));
      node.addEventListener("click", () => {
        if (terminal || !selectable) return;
        saveHandScroll(context, scroll);
        context.uiState.mahjongSelectedTileId = selected === tile.id ? null : tile.id;
        context.helpers.rerender();
      });
      scroll.appendChild(node);
    });
    wrap.append(head, scroll);
    return {wrap, scroll};
  }

  function winningResult(context) {
    if (!isTerminalState(context)) return null;
    const result = (context.state || {}).game_result;
    return result && !result.draw && result.winner_player_id
      && ["self_draw", "discard", "rob_kong"].includes(result.win_type)
      ? result : null;
  }

  function terminalSummary(context) {
    const result = winningResult(context);
    const nameFor = (id) => ((context.participants || []).find(
      (item) => item.player_id === id
    ) || {}).display_name || "玩家";
    if (!result) {
      return (context.state || {}).game_result?.draw
        ? "流局 · 本手无人和牌"
        : "本手已结束 · 未产生和牌结算";
    }
    const kind = {self_draw: "自摸", discard: "点炮和", rob_kong: "抢杠和"}[result.win_type];
    const source = result.win_type !== "self_draw" && result.source_player_id
      ? ` · ${nameFor(result.source_player_id)}${result.win_type === "rob_kong" ? "被抢杠" : "点炮"}` : "";
    const total = Number.isFinite(result.total_fan) ? ` · 总计 ${result.total_fan} 番` : "";
    return `${nameFor(result.winner_player_id)} 和牌 · ${kind}${source}${total}`;
  }

  function terminalReview(context, participants) {
    const review = el("section", "mahjong-terminal-review");
    review.id = "mahjongTerminalReview";
    review.tabIndex = -1;
    review.setAttribute("aria-labelledby", "mahjongTerminalTitle");
    const demo = context.room?.terminal_reason === "mahjong_review_demo";
    const title = el("h3", "mahjong-terminal-title", demo ? "演示房 · 终局复盘" : "终局复盘");
    title.id = "mahjongTerminalTitle";
    review.appendChild(title);
    if (demo) review.appendChild(el("p", "mahjong-terminal-note", "人工构造牌局 · 不计真实战绩、筹码或成就"));
    review.appendChild(el("p", "mahjong-terminal-summary", terminalSummary(context)));
    const result = winningResult(context);
    const fans = result && Array.isArray(result.fans) ? result.fans : [];
    if (fans.length) {
      const list = el("ul", "mahjong-fans");
      list.setAttribute("aria-label", "番型明细");
      fans.forEach((fan) => {
        const row = el("li", "mahjong-fan-row");
        row.appendChild(el("span", "", fan.name || fan.name_en || "未命名番型"));
        const multiplier = Number.isFinite(fan.points) && Number.isFinite(fan.count)
          ? `${fan.points} 番 × ${fan.count}` : "倍率未记录";
        row.appendChild(el("span", "", `${multiplier} · ${Number.isFinite(fan.fan) ? `${fan.fan} 番` : "番数未记录"}`));
        list.appendChild(row);
      });
      review.appendChild(list);
    } else if (result) {
      review.appendChild(el("p", "mahjong-terminal-note", "本局未记录番型明细"));
    }
    const state = context.state || {};
    const hands = state.terminal_hands || {};
    const rows = el("div", "mahjong-terminal-rows");
    participants.forEach((participant) => {
      const playerId = participant.player_id;
      const winner = result && result.winner_player_id === playerId;
      const row = el("section", "mahjong-terminal-row");
      row.dataset.playerId = playerId;
      row.classList.toggle("winner", Boolean(winner));
      const heading = el("div", "mahjong-terminal-player");
      heading.appendChild(el("strong", "mahjong-terminal-name", participant.display_name || "玩家"));
      heading.appendChild(el("span", "mahjong-terminal-seat", `座位 ${Number(participant.seat_index) + 1} · ${(state.seat_winds || {})[playerId] || "?"}风`));
      if (winner) heading.appendChild(el("span", "mahjong-winner-badge", "和牌"));
      row.appendChild(heading);
      const tiles = Array.isArray(hands[playerId]) ? hands[playerId] : [];
      row.appendChild(el("span", "mahjong-terminal-note", `手牌 · ${tiles.length} 张`));
      const hand = el("div", "mahjong-terminal-hand");
      hand.tabIndex = 0;
      hand.setAttribute("aria-label", `${participant.display_name || "玩家"}终局手牌，可横向滚动`);
      let highlighted = false;
      tiles.forEach((tile) => {
        const node = publicTile(tile);
        if (winner && !highlighted && result.winning_tile?.id != null
            && String(tile.id) === String(result.winning_tile.id)) {
          highlighted = true;
          node.classList.add("winning-tile");
          node.setAttribute("aria-label", `${tile.label || tile.code} · 和牌张`);
          node.appendChild(el("span", "mahjong-winning-label", "和"));
        }
        hand.appendChild(node);
      });
      if (!tiles.length) hand.appendChild(el("span", "mahjong-terminal-note", "未记录手牌"));
      row.appendChild(hand);
      const melds = Array.isArray(state.melds?.[playerId]) ? state.melds[playerId] : [];
      const groups = el("div", "mahjong-terminal-melds");
      groups.tabIndex = 0;
      groups.setAttribute("aria-label", `${participant.display_name || "玩家"}副露，可横向滚动`);
      melds.forEach((meld) => {
        const group = el("div", "mahjong-terminal-meld-group");
        group.appendChild(el("span", "mahjong-terminal-note", {
          chi: "吃", peng: "碰", ming_gang: "明杠", added_gang: "加杠", concealed_gang: "暗杠（已翻开）",
        }[meld.kind] || "副露"));
        group.appendChild(meldNode(meld));
        groups.appendChild(group);
      });
      if (melds.length) row.appendChild(groups);
      else row.appendChild(el("span", "mahjong-terminal-note", "无副露"));
      rows.appendChild(row);
    });
    review.appendChild(rows);
    return review;
  }

  function renderBoard(context) {
    const styleLink = ensureStyle();
    context.helpers.setBoardLayout({
      ariaLabel: "四人国标麻将桌",
    });
    context.board.classList.add("mahjong-board-layout");
    const table = el("div", "mahjong-table");
    const participants = [...(context.participants || [])].sort(
      (left, right) => Number(left.seat_index) - Number(right.seat_index)
    );
    participants.forEach((participant) => table.appendChild(seatNode(participant, context)));
    const center = el("main", "mahjong-center");
    center.appendChild(statusNode(context));
    const discardTable = el("div", "mahjong-discard-table");
    participants.forEach((participant) => discardTable.appendChild(discardZone(participant, context)));
    center.appendChild(discardTable);
    table.appendChild(center);
    if (isTerminalState(context)) {
      delete context.uiState.mahjongSelectedTileId;
      const review = terminalReview(context, participants);
      context.board.replaceChildren(table, review);
      // On first entry Mahjong CSS loads asynchronously; scroll the winning
      // hand after its dimensions settle without scrolling the whole page.
      const winningTile = review.querySelector(".winning-tile");
      if (winningTile) {
        const revealWinningTile = () => {
          if (winningTile.isConnected === false) return;
          const hand = winningTile.parentElement;
          if (!hand || !hand.clientWidth) return;
          const right = winningTile.offsetLeft + winningTile.offsetWidth;
          const viewportRight = hand.scrollLeft + hand.clientWidth;
          if (right > viewportRight - 4) hand.scrollLeft = Math.max(0, right - hand.clientWidth + 4);
        };
        revealWinningTile();
        if (styleLink && !styleLink.sheet) styleLink.addEventListener("load", revealWinningTile, {once: true});
        if (typeof global.requestAnimationFrame === "function") global.requestAnimationFrame(revealWinningTile);
      }
    } else {
      const ownHand = ownHandNode(context);
      table.appendChild(ownHand.wrap);
      context.board.replaceChildren(table);
      restoreHandScroll(context, ownHand.scroll);
    }
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
    if (isTerminalState(context)) {
      delete context.uiState.mahjongPendingActionId;
      delete context.uiState.mahjongSelectedTileId;
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
    terminalSummary,
    renderBoard,
    renderControls,
  });
}(window));

(function installMahjongRenderer(global) {
  "use strict";

  const STYLE_ID = "duel-game-style-mahjong";
  const STYLE_HREF = "/static/games/mahjong.css?v=0.9.47";
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

  // Same compact, native form controls as the existing dice room picker.
  function renderRulePicker(target, select) {
    ensureStyle();
    target.className = "pixel-field kwx-rule-picker";
    const details = el("details", "kwx-room-options");
    details.appendChild(el("summary", "", "可选房规"));
    const rules = JSON.parse(select.dataset.kwxRules || "{}");
    const persist = () => { select.dataset.kwxRules = JSON.stringify(rules); };
    const group = (title) => {
      const fieldset = el("fieldset", "kwx-rule-group");
      fieldset.appendChild(el("legend", "", title));
      const fields = el("div", "kwx-option-fields");
      fieldset.appendChild(fields);
      details.appendChild(fieldset);
      return fields;
    };
    const describe = (input, key, hint) => {
      input.dataset.kwxOption = key;
      const help = el("small", "invite-note", hint);
      help.id = `${target.id}-${key}-hint`;
      input.setAttribute("aria-describedby", help.id);
      return help;
    };
    const scoring = group("胡牌与杠分");
    [
      ["double_hu", "一炮双响", "同牌两家胡，分别结算。"],
      ["gang_discard_double", "杠上炮 ×2", "杠后首次出牌被胡，番数×2。"],
      ["liang_liability", "亮倒包赔", "亮倒者接炮，点炮者付两份。"],
      ["consecutive_kong_double", "连续杠翻倍", "同回合第2杠起，杠分逐次翻倍。"],
    ].forEach(([key, title, hint]) => {
      const label = el("label", "kwx-rule-toggle");
      const input = el("input");
      input.type = "checkbox";
      input.checked = rules[key] ?? false;
      input.setAttribute("aria-label", title);
      input.addEventListener("change", () => { rules[key] = input.checked; persist(); });
      const copy = el("span", "kwx-toggle-copy");
      copy.append(el("span", "kwx-option-title", title), describe(input, key, hint));
      label.append(input, copy);
      scoring.appendChild(label);
    });
    const extras = group("额外玩法与局数");
    const definitions = [
      ["horse_count", "买马", [[0, "不买马"], [1, "自摸翻一马"]], "自摸翻1张马，两家按马点付分。"],
      ["piao", "固定漂分", [[0, "不漂"], [1, "每家 1 分"], [3, "每家 3 分"], [5, "每家 5 分"]], "每次胡牌，按所选分数另付漂分。"],
      ["joker", "红中", [["none", "普通字牌"], ["red", "赖子 · 万能牌"]], "选赖子时，红中万能且不可碰杠。"],
      ["rounds", "局数", [[1, "单手"], [4, "4 局"], [8, "8 局"]], "单手、4局或8局，结束统一结算。"],
    ];
    definitions.forEach(([key, title, values, hint]) => {
      const label = el("label", "pixel-field");
      label.appendChild(el("span", "kwx-option-title", title));
      const input = el("select"); input.setAttribute("aria-label", title);
      values.forEach(([value, text]) => {const option = el("option", "", text); option.value = String(value); input.appendChild(option);});
      input.value = String(rules[key] ?? values[0][0]);
      input.addEventListener("change", () => {
        rules[key] = values.find(([value]) => String(value) === input.value)[0];
        persist();
      });
      label.append(input, describe(input, key, hint)); extras.appendChild(label);
    });
    details.appendChild(el("p", "invite-note kwx-options-note", "房规创建后固定。"));
    target.appendChild(details);
  }

  function renderRoomRules(target, room) {
    ensureStyle();
    const rules = el("details", "pixel-field kwx-room-options");
    rules.appendChild(el("summary", "", "本房房规"));
    rules.appendChild(el("p", "invite-note kwx-waiting-rules", room.rules_text || roomOptionsSummary(room.game_options)));
    target.appendChild(rules);
  }

  function roomOptionsSummary(o = {}) {
    const switches = [["double_hu", "双响"], ["gang_discard_double", "杠上炮×2"], ["liang_liability", "亮倒包赔"], ["consecutive_kong_double", "连续杠翻倍"]];
    return `${o.rounds || 1}局 · ${o.joker === "red" ? "红中万能" : "红中普通"} · ${o.horse_count ? "自摸一马" : "不买马"} · 漂${o.piao || 0} · ${switches.map(([key, name]) => `${name}${o[key] ? "开" : "关"}`).join(" / ")}`;
  }

  function isRoundEnd(context) {
    return context.state?.variant === "ka_wu_xing" && context.state?.phase === "round_end";
  }

  function handResult(context) {
    return (isRoundEnd(context) ? context.state?.round_result : context.state?.game_result) || null;
  }

  function handWinners(context) {
    const result = handResult(context);
    const winners = Array.isArray(result?.winner_results) ? result.winner_results : result?.winner_player_id ? [result] : [];
    return winners.filter(w => !w.draw && ["self_draw", "discard", "rob_kong"].includes(w.win_type));
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
    if (context.state?.variant === "ka_wu_xing") {
      const order = context.state.participant_order || [];
      const viewer = context.viewer?.player_id;
      const base = Math.max(0, order.indexOf(viewer));
      return ["bottom", "right", "left"][(order.indexOf(participant.player_id) - base + 3) % 3];
    }
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
    if (state.revealed?.[participant.player_id]) badges.appendChild(el("i", "", "亮"));
    const kongScore = state.kong_scores?.[participant.player_id];
    if (kongScore) badges.appendChild(el("i", "", `杠${kongScore > 0 ? "+" : ""}${kongScore}`));
    identity.append(avatar, name, badges);

    const turnIndicator = current
      ? el("span", "mahjong-turn-indicator", "行动中")
      : null;
    if (turnIndicator) turnIndicator.setAttribute("aria-label", "当前行动玩家，行动中");

    const backs = el("div", "mahjong-opponent-hand");
    if (!isViewer) {
      const count = Number(state.hand_counts && state.hand_counts[participant.player_id] || 0);
      const revealedCount = state.revealed?.[participant.player_id]?.hand?.length || 0;
      for (let index = revealedCount; index < count; index += 1) {
        backs.appendChild(publicTile({back: true}));
      }
      backs.setAttribute("aria-label", `${count - revealedCount} 张暗牌`);
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
    const primary = el("strong", "mahjong-round", state.variant === "ka_wu_xing" ? (state.round_label || "襄阳卡五星 · 3人") : `${state.round_label || "东一局"} · 圈风${state.prevalent_wind || "东"}`);
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
    if (isRoundEnd(context)) actionText = `本局已结算 · 等待${current?.display_name || "下一庄"}开始下一局`;
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
    legal.filter((action) => action.kind === "discard" && action.action_id.startsWith("discard:")).forEach((action) => {
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
    if (!isTerminalState(context) && !isRoundEnd(context)) return null;
    const result = handWinners(context)[0];
    return result && !result.draw && result.winner_player_id
      && ["self_draw", "discard", "rob_kong"].includes(result.win_type)
      ? result : null;
  }

  function terminalSummary(context) {
    if (["stale_archive", "resignation_liability"].includes(handResult(context)?.reason)) {
      return handResult(context).result_text;
    }
    const result = winningResult(context);
    const nameFor = (id) => ((context.participants || []).find(
      (item) => item.player_id === id
    ) || {}).display_name || "玩家";
    if (handWinners(context).length > 1) {
      return `一炮双响 · ${handWinners(context).map(w => `${nameFor(w.winner_player_id)} ×${w.multiplier}`).join("、")} · ${nameFor(handResult(context).source_player_id || handWinners(context)[0].source_player_id)}点炮`;
    }
    if (!result) {
      return handResult(context)?.draw
        ? "流局 · 本手无人和牌"
        : "本手已结束 · 未产生和牌结算";
    }
    const kind = {self_draw: "自摸", discard: "点炮和", rob_kong: "抢杠和"}[result.win_type];
    const source = result.win_type !== "self_draw" && result.source_player_id
      ? ` · ${nameFor(result.source_player_id)}${result.win_type === "rob_kong" ? "被抢杠" : "点炮"}` : "";
    const total = result.multiplier ? ` · 总计 ×${result.multiplier}` : Number.isFinite(result.total_fan) ? ` · 总计 ${result.total_fan} 番` : "";
    return `${nameFor(result.winner_player_id)} 和牌 · ${kind}${source}${total}`;
  }

  function terminalReview(context, participants) {
    const review = el("section", "mahjong-terminal-review");
    review.id = "mahjongTerminalReview";
    review.tabIndex = -1;
    review.setAttribute("aria-labelledby", "mahjongTerminalTitle");
    const demo = context.room?.terminal_reason === "mahjong_review_demo";
    const title = el("h3", "mahjong-terminal-title", demo ? "演示房 · 终局复盘" : isRoundEnd(context) ? `第 ${context.state.round_number} 局复盘` : "终局复盘");
    title.id = "mahjongTerminalTitle";
    review.appendChild(title);
    if (demo) review.appendChild(el("p", "mahjong-terminal-note", "人工构造牌局 · 不计真实战绩、筹码或成就"));
    review.appendChild(el("p", "mahjong-terminal-summary", terminalSummary(context)));
    const result = winningResult(context);
    handWinners(context).forEach((winnerResult) => {
      const fans = Array.isArray(winnerResult.fans) ? winnerResult.fans : [];
      if (handWinners(context).length > 1) review.appendChild(el("p", "mahjong-terminal-note", `${(participants.find(p => p.player_id === winnerResult.winner_player_id) || {}).display_name || "玩家"} · ×${winnerResult.multiplier}`));
      if (fans.length) {
        const list = el("ul", "mahjong-fans");
        list.setAttribute("aria-label", "番型明细");
        fans.forEach((fan) => {
          const row = el("li", "mahjong-fan-row");
          row.appendChild(el("span", "", fan.name || fan.name_en || "未命名番型"));
          const multiplier = Number.isFinite(fan.points) && Number.isFinite(fan.count)
            ? `${fan.points} 番 × ${fan.count}` : "倍率未记录";
          row.appendChild(el("span", "", fan.multiplier ? `×${fan.multiplier}` : `${multiplier} · ${Number.isFinite(fan.fan) ? `${fan.fan} 番` : "番数未记录"}`));
          list.appendChild(row);
        });
        review.appendChild(list);
      } else if (result) {
        review.appendChild(el("p", "mahjong-terminal-note", "本局未记录番型明细"));
      }
    });
    const state = context.state || {};
    const hands = state.terminal_hands || {};
    if (state.variant === "ka_wu_xing") {
      const outcome = handResult(context) || {};
      const units = outcome.round_score_units || outcome.score_units || {};
      review.appendChild(el("p", "mahjong-terminal-note", "胡牌倍数封顶后计亮倒；杠、马、漂及荒庄分独立。"));
      const categories = {hu: "胡", kong: "杠", horse: "马", piao: "漂", draw: "荒庄"};
      participants.forEach(p => {
        const parts = outcome.score_breakdown
          ? Object.entries(categories).map(([key, text]) => `${text} ${outcome.score_breakdown?.[key]?.[p.player_id] ?? 0}`)
          : [`含杠分 ${state.kong_scores?.[p.player_id] ?? 0}`];
        review.appendChild(el("p", "mahjong-terminal-note", `${p.display_name || "玩家"}：本局 ${units[p.player_id] ?? 0}（${parts.join(" / ")}） · 累计 ${state.cumulative_scores?.[p.player_id] ?? units[p.player_id] ?? 0}`));
      });
      if (isTerminalState(context) && context.room?.result?.settlement_deltas) {
        participants.forEach(p => {
          const actual = context.room.result.settlement_deltas[p.player_id];
          const requested = context.room.result.requested_settlement_deltas?.[p.player_id];
          if (actual !== undefined) review.appendChild(el("p", "mahjong-terminal-note", `${p.display_name || "玩家"}：实际筹码 ${actual}${requested !== undefined && requested !== actual ? `（应计 ${requested}，钱包封顶）` : ""}`));
        });
      }
      if (outcome.horse_tiles?.length) review.appendChild(el("p", "mahjong-terminal-note", `买马：${outcome.horse_tiles.map(t => t.label || t.code || t).join("、")}`));
      if (state.round_results?.length > 1) {
        const history = el("details", "mahjong-round-history");
        history.appendChild(el("summary", "mahjong-terminal-note", `已完成 ${state.round_results.length} 局 · 查看各局分数`));
        state.round_results.forEach((r, i) => history.appendChild(el("p", "mahjong-terminal-note", `第 ${i + 1} 局 · ${participants.map(p => `${p.display_name || "玩家"} ${r.score_units?.[p.player_id] ?? 0}`).join(" / ")}`)));
        review.appendChild(history);
      }
    }
    const rows = el("div", "mahjong-terminal-rows");
    participants.forEach((participant) => {
      const playerId = participant.player_id;
      const winnerResult = handWinners(context).find(w => w.winner_player_id === playerId);
      const winner = Boolean(winnerResult);
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
        if (winner && !highlighted && winnerResult.winning_tile?.id != null
            && String(tile.id) === String(winnerResult.winning_tile.id)) {
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
      ariaLabel: context.state?.variant === "ka_wu_xing" ? "三人襄阳卡五星麻将桌" : "四人国标麻将桌",
    });
    context.board.classList.add("mahjong-board-layout");
    const table = el("div", "mahjong-table");
    if (context.state?.variant === "ka_wu_xing") table.classList.add("three-player");
    const participants = [...(context.participants || [])].sort(
      (left, right) => Number(left.seat_index) - Number(right.seat_index)
    );
    participants.forEach((participant) => table.appendChild(seatNode(participant, context)));
    const center = el("main", "mahjong-center");
    center.appendChild(statusNode(context));
    const discardTable = el("div", "mahjong-discard-table");
    participants.forEach((participant) => discardTable.appendChild(discardZone(participant, context)));
    center.appendChild(discardTable);
    if (context.state?.variant === "ka_wu_xing" && !isTerminalState(context) && !isRoundEnd(context)) {
      participants.forEach(p => {
        const reveal = context.state.revealed?.[p.player_id];
        if (reveal) {
          const panel = el("section", "mahjong-revealed");
          panel.appendChild(el("p", "mahjong-discard-label",
            `${p.display_name || "玩家"}亮倒 · 听 ${reveal.waits.map(t => TILE_UNICODE[t] || t).join(" ")}`));
          const hand = el("div", "mahjong-revealed-hand");
          reveal.hand.forEach(tile => hand.appendChild(publicTile(tile)));
          panel.appendChild(hand); center.appendChild(panel);
        }
      });
    }
    table.appendChild(center);
    if (isTerminalState(context) || isRoundEnd(context)) {
      delete context.uiState.mahjongSelectedTileId;
      const review = terminalReview(context, participants);
      context.board.replaceChildren(table, review);
      // On first entry Mahjong CSS loads asynchronously; scroll the winning
      // hand after its dimensions settle without scrolling the whole page.
      review.querySelectorAll(".winning-tile").forEach((winningTile) => {
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
      });
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
    if (Number.isFinite(action.multiplier)) return `胡（×${action.multiplier}）`;
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
      controls.appendChild(controlHint(isRoundEnd(context) ? "本局已结算 · 等待下一庄开始" : "等待其他玩家行动", "waiting"));
      context.controls.replaceChildren(controls);
      return;
    }
    const pendingAction = legal.find(
      (action) => (CONFIRMED_ACTION_KINDS.has(action.kind) || action.action_id.startsWith("liang:"))
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
    const declaration = legal.find(a => a.action_id === `liang:${selected}`);
    if (declaration) controls.appendChild(actionButton(declaration, context, {requiresConfirmation: true}));
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

  const renderer = {
    participantPresentation: "board-edge",
    ownsPrivateStatePresentation: true,
    usesStandardMoveConfirmation: false,
    terminalSummary,
    renderBoard,
    renderControls,
  };
  global.DuelGameUI.register("mahjong", renderer);
  global.DuelGameUI.register("ka_wu_xing", {...renderer, renderRulePicker, renderRoomRules, roomOptionsSummary});
}(window));

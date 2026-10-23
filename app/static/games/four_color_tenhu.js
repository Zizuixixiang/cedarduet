(function registerFourColorCards() {
  "use strict";
  const TITLES = {four_color_tenhu:"十胡 · BGA 台湾版",four_color_jiuzhi:"九支十仔 · 五对十张",four_color_qunhu:"群胡 · 忠孝新村版"};
  const COLORS = {R: "红", Y: "黄", G: "绿", W: "白"};
  const NAMES = {R: "帥仕相俥傌炮兵", Y: "帥仕相俥傌炮兵", G: "將士象車馬包卒", W: "將士象車馬包卒"};
  const el = (doc, tag, cls, text) => {
    const n = doc.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined) n.textContent = text;
    return n;
  };
  function styles(doc) {
    if (doc.getElementById("four-color-styles")) return;
    const link = el(doc, "link");
    link.id = "four-color-styles"; link.rel = "stylesheet";
    link.href = "/static/games/four_color_tenhu.css";
    doc.head.append(link);
  }
  function renderRulePicker(target, select) {
    const doc = target.ownerDocument;
    styles(doc); target.className = "fc-rule-picker";
    target.setAttribute("role", "radiogroup"); target.setAttribute("aria-label", "四色牌规则");
    [["tenhu", "十胡", "BGA 台湾版 · 凑组满十胡"],
     ["jiuzhi", "九支十仔", "五对十张 · 公开翻牌凑对"],
     ["qunhu", "群胡", "忠孝新村版 · 有目听牌"]].forEach(([id, name, copy]) => {
      const card = el(doc, "label", "fc-rule-card");
      const radio = el(doc, "input"); radio.type = "radio";
      radio.name = `${select.id}-four-color-rule`; radio.value = id;
      radio.checked = `four_color_${id}` === (select.dataset.fourColorMode || select.value);
      radio.addEventListener("change", () => { select.dataset.fourColorMode = `four_color_${id}`; select.dispatchEvent(new Event("change"));
        document.querySelector(`#${target.id} input[value="${id}"]`)?.focus({preventScroll:true}); });
      const text = el(doc, "span", "fc-rule-copy");
      text.append(el(doc, "strong", "", name), el(doc, "span", "", copy));
      card.append(radio, text); target.append(card);
    });
  }
  function ui(context) {
    if (!context.uiState.fourColor) context.uiState.fourColor = {selected: null, busy: false};
    return context.uiState.fourColor;
  }
  const name = id => `${COLORS[id[0]]}${NAMES[id[0]][Number(id[1])]}`;
  function card(doc, id, button = false) {
    const face = el(doc, "span", `fc-card fc-${id[0]}`);
    face.lang = "zh-Hant";
    const glyph = NAMES[id[0]][Number(id[1])];
    // The repeated print and narrow frame follow Taiwanese paper cards.
    face.append(el(doc, "strong", "", glyph), el(doc, "small", "", {R:"紅",Y:"黃",G:"綠",W:"白"}[id[0]]), el(doc, "strong", "", glyph));
    const n = button ? el(doc, "button", "fc-card-target") : face;
    n.dataset.cardId = id;
    n.setAttribute("aria-label", name(id));
    if (button) {
      n.type = "button";
      face.setAttribute("aria-hidden", "true");
      n.append(face);
    } else n.setAttribute("role", "img");
    return n;
  }
  function rows(doc, ids) {
    const n = el(doc, "div", "fc-cards"); ids.forEach(id => n.append(card(doc, id))); return n;
  }
  function actions(context) { return context.privateState?.legal_actions || []; }
  function seats(context, table) {
    const doc = table.ownerDocument, state = context.state;
    const order = state.participant_order || [];
    const viewerIndex = order.indexOf(context.viewer?.player_id);
    const start = viewerIndex < 0 ? 0 : viewerIndex;
    // Same clockwise seating as Mahjong: viewer, right, opposite, left.
    // Five players occupy two distinct stations on each side of the table.
    const positions = {2: ["south", "north"], 3: ["south", "east", "west"],
      4: ["south", "east", "north", "west"], 5: ["south", "east-low", "east-high", "west-high", "west-low"]}[order.length] || [];
    table.dataset.seats = String(order.length);
    order.slice(start).concat(order.slice(0, start)).forEach((pid, index) => {
      const p = context.participants.find(p => p.player_id === pid);
      const isViewer = pid === context.viewer?.player_id;
      const current = !context.isTerminal && pid === (context.room.current_player_id || state.turn_player_id);
      const section = el(doc, "section", `fc-seat fc-position-${positions[index]} seat-${p?.seat_index ?? index}`);
      section.dataset.playerId = pid;
      section.dataset.position = positions[index];
      const side = /^(west|east)/.test(positions[index]);
      if (side) section.classList.add("fc-seat-side", positions[index].startsWith("west") ? "fc-facing-right" : "fc-facing-left");
      section.classList.toggle("fc-seat-current", current);
      section.classList.toggle("fc-seat-self", isViewer);
      section.setAttribute("aria-current", String(current));
      section.setAttribute("aria-label", `${p?.display_name || "玩家"}${isViewer ? "（你）" : ""}的公开牌区`);
      const header = el(doc, "div", "fc-seat-header"), avatar = el(doc, "span", "room-participant-avatar");
      if (p) context.helpers.renderParticipantAvatar(avatar, p);
      avatar.setAttribute("aria-hidden", "true");
      const copy = el(doc, "div", "fc-seat-copy");
      const playerName = el(doc, "strong", "fc-seat-name", `${p?.display_name || "玩家"}${isViewer ? "（你）" : ""}`);
      playerName.title = playerName.textContent;
      copy.append(playerName);
      header.append(avatar, copy);
      if (current) header.append(el(doc, "span", "fc-turn-indicator", "▸ 行动中"));
      const identity = el(doc, "div", "fc-seat-identity");
      identity.append(header); section.append(identity);
      const counts = [];
      const shownInHand = new Set([...(state.broken?.[pid] || []), ...(state.listening?.[pid] || [])]).size;
      if (Number.isInteger(state.hand_counts?.[pid])) {
        const hidden = Math.max(0, state.hand_counts[pid] - shownInHand);
        counts.push([`暗牌 ${hidden} 张`, `暗${hidden}`]);
      }
      if (Number.isInteger(state.closed_pair_counts?.[pid])) {
        const pairs = state.closed_pair_counts[pid];
        counts.push([`扣置 ${pairs} 对`, `扣${pairs}`]);
      }
      const points = state.result?.point_deltas?.[pid];
      if (Number.isInteger(points)) {
        const score = `${points > 0 ? "+" : ""}${points}`;
        counts.push([`${score} 分`, score]);
      }
      const fullCounts = counts.map(([full]) => full).join(" · ");
      const countInfo = el(doc, "div", "fc-meta fc-seat-counts");
      countInfo.title = fullCounts;
      countInfo.setAttribute("role", "group");
      countInfo.setAttribute("aria-label", fullCounts);
      counts.forEach(([, compact]) => {
        const label = el(doc, "span", "fc-seat-count", compact);
        label.setAttribute("aria-hidden", "true"); countInfo.append(label);
      });
      if (pid === state.dealer_player_id) countInfo.append(el(doc, "span", "fc-dealer", "庄"));
      copy.append(countInfo);
      // Only the public projection belongs on the table. Never consult hands or closed_pairs here.
      const publicCards = el(doc, "div", "fc-seat-public");
      publicCards.tabIndex = 0;
      publicCards.setAttribute("role", "group");
      publicCards.setAttribute("aria-label", `${p?.display_name || "玩家"}的明牌，可滚动查看`);
      const groups = el(doc, "div", "fc-groups fc-seat-groups");
      (state.melds?.[pid] || []).forEach((group, groupIndex) => {
        const pile = rows(doc, group.cards);
        pile.classList.add("fc-meld");
        pile.setAttribute("role", "group");
        pile.setAttribute("aria-label", `已亮组合 ${groupIndex + 1}，${group.cards.length} 张`);
        groups.append(pile);
      });
      if (groups.children.length) publicCards.append(groups);
      for (const [ids, label] of [[state.broken?.[pid], "断张（待弃）"], [state.listening?.[pid], "已听牌 · 展示"]]) {
        if (!ids?.length) continue;
        const reveal = rows(doc, ids); reveal.classList.add("fc-reveal");
        const shown = el(doc, "div", "fc-revealed-group");
        shown.append(el(doc, "div", "fc-meta", label), reveal);
        publicCards.append(shown);
      }
      if (side) {
        // Slots overlap along the long axis; melds themselves run along the edge.
        publicCards.querySelectorAll(".fc-card").forEach(face => {
          const slot = el(doc, "span", "fc-side-card");
          face.replaceWith(slot); slot.append(face);
        });
      }
      if (publicCards.children.length) section.append(publicCards);
      table.append(section);
    });
  }
  function renderBoard(context) {
    const doc = context.board.ownerDocument, state = context.state, local = ui(context);
    styles(doc);
    context.helpers.setBoardLayout({rows: 1, cols: 1, large: true, ariaLabel: "四色牌桌与本人手牌"});
    context.board.classList.add("four_color_tenhu");
    const root = el(doc, "div", "fc-game");
    const table = el(doc, "section", "fc-table");
    table.setAttribute("aria-label", "公共牌桌");
    seats(context, table);
    const center = el(doc, "div", "fc-table-center"), stock = el(doc, "div", "fc-stock");
    stock.dataset.depth = String(Math.min(3, state.wall_count));
    stock.setAttribute("aria-label", `牌库剩余 ${state.wall_count} 张`);
    if (state.wall_count > 0) {
      const back = el(doc, "span", "fc-card-back");
      back.setAttribute("aria-hidden", "true"); stock.append(back);
    }
    const stockCopy = el(doc, "span", "fc-stock-copy");
    stockCopy.append(el(doc, "span", "fc-meta", "牌库"), el(doc, "strong", "", `${state.wall_count} 张`));
    stock.append(stockCopy);
    const live = el(doc, "div", "fc-live-cards");
    live.append(stock); center.append(live);
    const playZone = el(doc, "section", "fc-discards");
    playZone.setAttribute("aria-label", "已弃牌");
    // Pending offers normally enter discards only after all responses finish.
    // Use physical IDs so an overlapping projection still renders each card once.
    const historyIds = [...new Set(state.discards)].filter(id => id !== state.offer?.card);
    if (state.offer?.card) {
      const offer = el(doc, "div", "fc-offer"), face = card(doc, state.offer.card);
      const label = state.offer.from_deck ? "公开翻牌" : "当前弃牌";
      face.classList.add("fc-card-current");
      face.setAttribute("aria-label", `${label}：${name(state.offer.card)}`);
      offer.append(el(doc, "span", "fc-meta", label), face);
      live.append(offer);
    }
    const history = rows(doc, historyIds);
    history.classList.add("fc-discard-history");
    if (historyIds.length) history.tabIndex = 0;
    history.setAttribute("role", "group");
    history.setAttribute("aria-label", `已弃牌 ${historyIds.length} 张，可滚动查看`);
    if (!state.offer && history.lastElementChild) {
      history.lastElementChild.classList.add("fc-card-current");
      history.lastElementChild.setAttribute("aria-label", `最近弃牌：${name(historyIds.at(-1))}`);
    }
    playZone.append(history);
    const flow = state.flow || {}, status = el(doc, "div", "fc-turn");
    const phase = {discard: "出牌", draw: "翻牌", response: "回应亮牌", opening_hu: "开局确认"}[flow.phase];
    status.append(el(doc, "strong", "", context.isTerminal ? "本手结束" : phase ? `${context.canMove ? "请" : "等待"}${phase}` : "等待开局"));
    center.append(playZone, status); table.append(center); root.append(table);
    const rack = el(doc, "section", "fc-rack");
    rack.append(el(doc, "strong", "", "我的手牌"));
    const hand = el(doc, "div", "fc-hand");
    hand.setAttribute("role", "group"); hand.setAttribute("aria-label", "我的手牌，可横向滚动查看");
    hand.setAttribute("aria-busy", String(local.busy));
    const selectable = new Set(actions(context).filter(a => a.action_id.startsWith("discard:")).map(a => a.action_id.slice(8)));
    if (!selectable.has(local.selected)) local.selected = null;
    (context.privateState?.hand || []).forEach(t => {
      const clickable = selectable.has(t.id), tile = card(doc, t.id, true);
      tile.disabled = !clickable || !context.canMove || local.busy;
      const unavailable = context.canMove && selectable.size > 0 && !clickable;
      if (unavailable) {
        tile.dataset.unavailable = "true"; tile.title = "本回合不能打出";
        tile.setAttribute("aria-label", `${name(t.id)}，本回合不能打出`);
      }
      if (clickable) {
        tile.setAttribute("aria-pressed", String(local.selected === t.id));
        tile.addEventListener("click", () => {
          local.selected = local.selected === t.id ? null : t.id; context.helpers.rerender();
          [...doc.querySelectorAll(".fc-hand button")].find(n => n.dataset.cardId === t.id)?.focus({preventScroll:true});
        });
      }
      hand.append(tile);
    });
    hand.addEventListener("scroll", () => { local.handScroll = hand.scrollLeft; }, {passive:true});
    const actionSlot = el(doc, "div", "fc-action-slot");
    actionSlot.setAttribute("role", "group");
    actionSlot.setAttribute("aria-label", "游戏专属操作");
    rack.append(hand, actionSlot); root.append(rack);
    if (context.privateState?.closed_pairs?.length) {
      const closed=el(doc,"section","fc-rack fc-closed"); closed.append(el(doc,"strong","","我的扣置对子"));
      const pairs=el(doc,"div","fc-groups");
      context.privateState.closed_pairs.forEach(pair => pairs.append(rows(doc,pair))); closed.append(pairs); root.append(closed);
    }
    if (context.privateState?.waiting_codes?.length) root.append(el(doc,"p","fc-meta",`已听：${context.privateState.waiting_codes.map(c=>name(c)).join("、")}`));
    if (state.result) {
      const result = state.result, scoring = result.scoring;
      const reason = {hu:"胡牌", under_ten:"相公", wall_exhausted:"牌库用尽，流局", hand_cancelled_by_departure:"有人离场，本手取消", exchange_limit:"连续换牌，流局"}[result.reason];
      const line = el(doc, "p", "fc-result", reason || "本手结束"); line.setAttribute("role", "status");
      root.append(line);
      if (scoring && state.board_kind === "four_color_tenhu") {
        root.append(el(doc, "p", "", `组合 ${scoring.base_hu} 胡 · 花 ${scoring.flower_hu} 胡 · 每家 ${Math.abs(scoring.per_opponent)} 分${scoring.qualified ? "付给胡家" : "由相公赔付"}`));
        const show = el(doc, "details", "fc-result-details");
        show.append(el(doc, "summary", "", "胡牌暗组与花牌"));
        scoring.concealed_groups.forEach(g => show.append(rows(doc, g.cards)));
        if (result.flower_card) show.append(rows(doc, [result.flower_card]));
        root.append(show);
      }
    }
    if (state.result?.scoring && state.board_kind !== "four_color_tenhu") {
      const result=state.result, scoring=result.scoring;
      root.append(el(doc,"p","",scoring.heads ? `${Object.entries(scoring.heads).map(([k,v])=>`${k} ${v}`).join(" · ")} · 共 ${scoring.total} 头` : `${result.win_kind === "self_draw" ? "自摸" : scoring.flower_hit ? "着花" : "未着花"} · 每家 ${scoring.per_opponent} 分`));
      const show=el(doc,"details","fc-result-details"); show.append(el(doc,"summary","","胜牌与花牌"));
      if(scoring.pairs) scoring.pairs.forEach(p=>show.append(rows(doc,p)));
      if(scoring.partition) { scoring.partition.groups.forEach(g=>show.append(rows(doc,g.cards))); show.append(rows(doc,[scoring.partition.singleton])); }
      if(result.flowers?.length) show.append(rows(doc,result.flowers)); root.append(show);
    }
    context.board.append(root);
    // Start at the newest history; preserve deliberate scrolling on UI rerenders.
    const historyKey = `${historyIds.join(",")}|${state.offer?.card || ""}`;
    history.scrollTop = local.historyKey === historyKey ? (local.historyScroll || 0) : history.scrollHeight;
    local.historyKey = historyKey;
    local.historyScroll = history.scrollTop;
    history.addEventListener("scroll", () => { local.historyScroll = history.scrollTop; }, {passive:true});
    hand.scrollLeft = local.handScroll || 0;
    if (hand.scrollWidth > hand.clientWidth) hand.tabIndex = 0;
  }
  async function submit(context, action) {
    const local = ui(context);
    if (local.busy || !context.helpers.canMove()) return;
    local.busy = true; context.helpers.rerender();
    try { await context.helpers.submitMove(action); }
    finally { local.busy = false; context.helpers.rerender(); }
  }
  function renderControls(context) {
    const doc = context.board.ownerDocument, local = ui(context), panel = el(doc, "div", "fc-controls");
    if (!context.isTerminal) {
      if (!context.canMove) panel.append(el(doc, "span", "fc-meta", "等待其他玩家行动"));
      const list = actions(context), discarding = list.some(a => a.action_id.startsWith("discard:"));
      if (discarding) {
        const b = el(doc, "button", "pixel-btn compact", local.selected ? `打出${name(local.selected)}` : "选一张牌打出");
        b.type = "button"; b.dataset.fcAction = "discard"; b.disabled = !local.selected || local.busy;
        b.addEventListener("click", () => submit(context, list.find(a => a.action_id === `discard:${local.selected}`)));
        panel.append(b);
        const listen = list.find(a=>a.action_id === `listen:${local.selected}`);
        if (listen) {
          const ready=el(doc,"button","pixel-btn compact secondary","打出并听牌"); ready.type="button"; ready.dataset.fcAction="listen"; ready.disabled=local.busy;
          ready.addEventListener("click",()=>submit(context,listen)); panel.append(ready);
        }
      } else list.forEach(action => {
        const text = context.privateState.action_labels[action.action_id];
        const b = el(doc, "button", "pixel-btn compact secondary", text);
        b.type = "button"; b.dataset.fcAction = action.action_id; b.disabled = !context.canMove || local.busy;
        // Distinguish equal-name combinations without asking the browser to judge rules.
        const held = action.action_id.includes(":") ? action.action_id.split(":")[1].split(",").filter(id => id !== "general") : [];
        if (held.length) b.append(el(doc, "span", "fc-action-cards", held.map(name).join("、")));
        b.addEventListener("click", () => submit(context, action)); panel.append(b);
      });
    }
    // Keep actions beside the hand, ahead of optional piles and terminal detail.
    const slot = context.board.querySelector(".fc-action-slot") || context.controls;
    slot.replaceChildren(panel);
  }
  Object.keys(TITLES).forEach(type => window.DuelGameUI.register(type, {
    participantPresentation: "embedded", multiplayerTable: true, ownsPrivateStatePresentation: true,
    usesStandardMoveConfirmation: false, renderBoard, renderControls, renderRulePicker,
  }));
}());

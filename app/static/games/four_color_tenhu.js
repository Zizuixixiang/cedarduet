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
  function renderBoard(context) {
    const doc = context.board.ownerDocument, state = context.state, local = ui(context);
    styles(doc);
    context.helpers.setBoardLayout({rows: 1, cols: 1, large: true, ariaLabel: "四色牌桌与本人手牌"});
    context.board.classList.add("four_color_tenhu");
    const root = el(doc, "div", "fc-game"), head = el(doc, "div", "fc-head");
    head.append(el(doc, "strong", "", TITLES[state.board_kind]), el(doc, "span", "fc-meta", `牌库 ${state.wall_count} 张`));
    root.append(head);
    const table = el(doc, "div", "fc-table");
    (state.participant_order || []).forEach(pid => {
      const p = context.participants.find(p => p.player_id === pid), section = el(doc, "section", "fc-seat");
      const points = state.result?.point_deltas?.[pid];
      section.append(el(doc, "div", "fc-meta", `${p?.display_name || "玩家"} · 手牌 ${state.hand_counts[pid]}${points !== undefined ? ` · ${points > 0 ? "+" : ""}${points} 分` : ""}`));
      const groups = el(doc, "div", "fc-groups");
      (state.melds[pid] || []).forEach(group => groups.append(rows(doc, group.cards)));
      if (state.closed_pair_counts) section.append(el(doc, "span", "fc-meta", `扣置 ${state.closed_pair_counts[pid]} 对`));
      if (state.broken?.[pid]?.length) { section.append(el(doc,"span","fc-meta","断张（待弃）")); section.append(rows(doc,state.broken[pid])); }
      if (state.listening?.[pid]) { section.append(el(doc,"strong","fc-meta","已听牌 · 展示")); section.append(rows(doc,state.listening[pid])); }
      if (groups.children.length) section.append(groups);
      table.append(section);
    });
    root.append(table);
    if (state.offer) {
      const offer = el(doc, "div", "fc-offer");
      offer.append(el(doc, "span", "", state.offer.from_deck ? "公开翻牌" : "当前弃牌"), card(doc, state.offer.card));
      root.append(offer);
    }
    if (state.discards.length) {
      const discards = el(doc, "details", "fc-discards");
      discards.append(el(doc, "summary", "", `弃牌 ${state.discards.length} 张`), rows(doc, state.discards));
      root.append(discards);
    }
    const rack = el(doc, "section", "fc-rack");
    rack.append(el(doc, "strong", "", "我的手牌"));
    const hand = el(doc, "div", "fc-hand");
    hand.classList.toggle("fc-hand-many", (context.privateState?.hand?.length || 0) > 10);
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
    rack.append(hand); root.append(rack);
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
        const show = el(doc, "details", "fc-discards");
        show.append(el(doc, "summary", "", "胡牌暗组与花牌"));
        scoring.concealed_groups.forEach(g => show.append(rows(doc, g.cards)));
        if (result.flower_card) show.append(rows(doc, [result.flower_card]));
        root.append(show);
      }
    }
    if (state.result?.scoring && state.board_kind !== "four_color_tenhu") {
      const result=state.result, scoring=result.scoring;
      root.append(el(doc,"p","",scoring.heads ? `${Object.entries(scoring.heads).map(([k,v])=>`${k} ${v}`).join(" · ")} · 共 ${scoring.total} 头` : `${result.win_kind === "self_draw" ? "自摸" : scoring.flower_hit ? "着花" : "未着花"} · 每家 ${scoring.per_opponent} 分`));
      const show=el(doc,"details","fc-discards"); show.append(el(doc,"summary","","胜牌与花牌"));
      if(scoring.pairs) scoring.pairs.forEach(p=>show.append(rows(doc,p)));
      if(scoring.partition) { scoring.partition.groups.forEach(g=>show.append(rows(doc,g.cards))); show.append(rows(doc,[scoring.partition.singleton])); }
      if(result.flowers?.length) show.append(rows(doc,result.flowers)); root.append(show);
    }
    context.board.append(root);
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
    context.controls.append(panel);
  }
  Object.keys(TITLES).forEach(type => window.DuelGameUI.register(type, {
    participantPresentation: "generic", ownsPrivateStatePresentation: true,
    usesStandardMoveConfirmation: false, renderBoard, renderControls, renderRulePicker,
  }));
}());

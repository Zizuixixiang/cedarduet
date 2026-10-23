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
    // Like Hold'em's five-player ring: two upper stations and two side stations.
    const positions = {2: ["south", "north"], 3: ["south", "east", "west"],
      4: ["south", "east", "north", "west"], 5: ["south", "east", "north-east", "north-west", "west"]}[order.length] || [];
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
      if (current) countInfo.append(el(doc, "span", "fc-turn-indicator", "行动中"));
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
  function historyPiles(context, table, neutral, ids) {
    const doc = table.ownerDocument, sources = context.state.discard_sources || {};
    const piles = new Map();
    const recent = context.state.offer ? null : ids.at(-1);
    for (const id of ids) {
      const source = sources[id];
      const seat = [...table.querySelectorAll(".fc-seat")].find(n => n.dataset.playerId === source);
      const key = seat ? `player:${source}` : "neutral";
      if (!piles.has(key)) {
        const pile = el(doc, "div", "fc-cards fc-discard-history");
        pile.dataset.historyKey = key;
        pile.dataset.axis = table.dataset.seats !== "5" && seat && /^(west|east)$/.test(seat.dataset.position) ? "y" : "x";
        pile.tabIndex = 0; pile.setAttribute("role", "group");
        if (seat) {
          pile.classList.add("fc-history", `fc-history-${seat.dataset.position}`);
          pile.dataset.playerId = source;
          pile.dataset.position = seat.dataset.position;
          const owner = context.participants.find(p => p.player_id === source)?.display_name || "玩家";
          pile.setAttribute("aria-label", `${owner}的历史弃牌，可滚动查看`);
          table.append(pile);
        } else {
          const group = el(doc, "div", "fc-neutral-pile");
          pile.setAttribute("aria-label", "中立历史牌，含公开翻牌或来源未记录的弃牌，可滚动查看");
          group.append(pile); neutral.append(group);
        }
        piles.set(key, pile);
      }
      const pile = piles.get(key), face = card(doc, id);
      if (id === recent) face.classList.add("fc-card-current");
      face.title = `${pile.getAttribute("aria-label").split("，")[0]}：${name(id)}`;
      if (!seat) {
        const origin = source === null ? "无人收取的公开翻牌" : "来源未记录的历史弃牌";
        face.title = `${origin}：${name(id)}`;
        face.setAttribute("aria-label", face.title);
      }
      if (pile.dataset.axis === "x") {
        const slot = el(doc, "span", "fc-history-card"); slot.append(face); pile.append(slot);
      } else pile.append(face);
    }
  }
  // Separate page-visit UI: never write demo cards into room/state/uiState.
  const TUTORIAL_KEY = "cedarduet.four_color.tutorial.v1";
  let tutorialVisit = null, tutorial = null, tutorialObserver = null;
  function closeTutorial(remember, restore = true) {
    if (!tutorial) return;
    const {dialog, focus, resizeObserver, position} = tutorial;
    tutorial = null;
    if (remember) {
      try { window.localStorage.setItem(TUTORIAL_KEY, remember); } catch (_) { /* Optional storage. */ }
    }
    resizeObserver?.disconnect();
    window.removeEventListener("resize", position);
    window.visualViewport?.removeEventListener("resize", position);
    dialog.close(); dialog.remove();
    if (restore && focus?.isConnected) focus.focus({preventScroll: true});
  }
  // Copy projections and allow only avatar painting; never pass live action helpers or uiState.
  function tutorialBoard(context, board) {
    const clone = value => JSON.parse(JSON.stringify(value));
    const example = context.room.status === "waiting" || !context.state?.participant_order?.length;
    const participants = example ? ["示例玩家", "右家", "对家", "左家"].map((label, i) => ({
      player_id: `tutorial-${i}`, display_name: label, seat_index: i,
    })) : clone(context.participants);
    const order = participants.map(p => p.player_id);
    const deck = Array.from("RYGW", color => Array.from({length: 7}, (_, rank) =>
      Array.from({length: 4}, (_, copy) => `${color}${rank}-${copy + 1}`))).flat(2);
    const state = example ? {
      board_kind: context.room.game_type || "four_color_tenhu", participant_order: order,
      dealer_player_id: order[0], turn_player_id: order[0], wall_count: 24,
      flow: {phase: "discard"}, melds: {}, hand_counts: {}, discards: [], discard_sources: {},
    } : clone(context.state);
    const used = new Set();
    if (example) order.forEach((pid, seat) => {
      const count = seat % 2 ? 8 : 2;
      state.melds[pid] = Array.from({length: count}, () => {
        const first = deck.find(id => !used.has(id) && id.endsWith("-1"));
        const cards = [1, 2, 3].map(copy => `${first.slice(0, 2)}-${copy}`);
        cards.forEach(id => used.add(id));
        return {cards};
      });
      state.hand_counts[pid] = 20;
    });
    if (example) [order[2], order[0], order[1], order[3]].forEach(pid => {
      const history = deck.filter(id => !used.has(id)).slice(0, pid === order[2] ? 28 : 1);
      state.discards.push(...history);
      history.forEach(id => { used.add(id); state.discard_sources[id] = pid; });
    });
    const privateState = example ? {hand: deck.filter(id => !used.has(id)).slice(0, 20).map(id => ({id}))}
      : clone(context.privateState || {});
    if (example) state.wall_count = deck.length - used.size - privateState.hand.length;
    // Removing legal actions prevents even selection handlers being installed.
    privateState.legal_actions = [];
    const practice = {
      board, state, privateState, participants, uiState: {}, canMove: false,
      viewer: example ? {player_id: order[0]} : clone(context.viewer || {}),
      room: example ? {current_player_id: order[0]} : clone(context.room),
      isTerminal: example ? false : context.isTerminal,
      helpers: {
        setBoardLayout() {},
        renderParticipantAvatar(target, participant) {
          if (!example) context.helpers.renderParticipantAvatar(target, participant);
          else target.textContent = ["🙂", "🐱", "🐼", "🦊"][participant.seat_index];
        },
      },
    };
    renderBoard(practice, true);
    board.dataset.source = example ? "example" : "snapshot";
    board.setAttribute("aria-label", example ? "教程示例牌桌" : "当前牌桌的只读副本");
    return example;
  }
  function openTutorial(context) {
    const doc = context.board.ownerDocument;
    styles(doc);
    const dialog = el(doc, "dialog", "fc-tutorial");
    dialog.setAttribute("aria-labelledby", "fc-tutorial-title");
    dialog.setAttribute("aria-describedby", "fc-tutorial-copy");
    // Gem Merchant's modal / spotlight / explanation structure, with a
    // complete practice board inside the modal so the real game stays inert.
    const spotlight = el(doc, "div", "fc-tutorial-spotlight");
    spotlight.setAttribute("aria-hidden", "true"); spotlight.hidden = true;
    const stage = el(doc, "div", "fc-tutorial-stage");
    const panel = el(doc, "section", "fc-tutorial-panel");
    const head = el(doc, "div", "fc-tutorial-head");
    const title = el(doc, "h2", "", "第一次来四色牌？"); title.id = "fc-tutorial-title";
    const button = (text, primary, action) => {
      const b = el(doc, "button", `pixel-btn compact${primary ? "" : " secondary"}`, text);
      b.type = "button"; b.addEventListener("click", action); return b;
    };
    const close = button("×", false, () => closeTutorial());
    close.classList.add("fc-tutorial-close"); close.setAttribute("aria-label", "关闭本次教程");
    head.append(title, close);
    const progress = el(doc, "span", "fc-tutorial-progress"); progress.hidden = true;
    const copy = el(doc, "p", "", "上下横滑、左右竖滑，就能查看被叠住的牌。");
    copy.id = "fc-tutorial-copy"; copy.setAttribute("aria-live", "polite");
    const sample = el(doc, "div", "board four_color_tenhu fc-tutorial-board"); sample.hidden = true;
    let steps = [], step = -1;
    const targetForStep = () => steps[step]?.target;
    const position = () => {
      const target = targetForStep();
      spotlight.hidden = !target;
      dialog.classList.toggle("has-target", !!target);
      const bounds = dialog.getBoundingClientRect();
      const panelHeight = panel.getBoundingClientRect().height;
      if (!target) {
        panel.style.top = `${Math.max(12, (dialog.clientHeight - panelHeight) / 2)}px`;
        return;
      }
      const rect = target.getBoundingClientRect();
      const top = rect.top - bounds.top + dialog.scrollTop;
      const bottom = top + rect.height;
      const below = bottom + 14, above = top - panelHeight - 14;
      // Keep the normal board geometry. On short screens scroll the tutorial,
      // never the real page; the explanation never overlaps its target.
      const panelTop = below + panelHeight <= dialog.clientHeight || above < 12 ? below : above;
      panel.style.top = `${panelTop}px`;
      stage.style.minHeight = `${Math.max(sample.offsetHeight + 24, panelTop + panelHeight + 12)}px`;
      Object.assign(spotlight.style, {
        left: `${rect.left - bounds.left - 4}px`, top: `${top - 4}px`,
        width: `${rect.width + 8}px`, height: `${rect.height + 8}px`
      });
    };
    const actions = el(doc, "div", "fc-tutorial-actions");
    const show = index => {
      if (!steps.length) {
        sample.hidden = false;
        const example = tutorialBoard(context, sample);
        for (const place of ["north", "north-west", "north-east", "south", "west", "east"]) {
          const seat = sample.querySelector(`.fc-position-${place}`);
          if (!seat) continue;
          const side = ["west", "east"].includes(place);
          const history = sample.querySelector(`.fc-history-${place}`);
          const port = seat.querySelector(".fc-seat-public");
          const vertical = port?.classList.contains("fc-discard-history") ? port.dataset.axis === "y" : side;
          const area = port?.classList.contains("fc-discard-history") ? "历史弃牌" : "明牌";
          const direction = vertical ? "上下" : "左右";
          const label = {north: "上方", "north-west": "左上方", "north-east": "右上方", south: "下方", west: "左侧", east: "右侧"}[place];
          steps.push({target: port || seat.querySelector(".fc-seat-identity"),
            title: `${label}${area}，${vertical ? "纵向" : "横向"}滑动`,
            copy: port ? `${label}牌多时可${direction}滑动，查看被叠住的牌。`
              : `${label}是这家的公开牌区，目前还没有明牌；亮牌后可${direction}滑动查看。`});
        }
      }
      targetForStep()?.removeAttribute("data-tutorial-target");
      step = index; dialog.dataset.step = String(index + 1);
      targetForStep().setAttribute("data-tutorial-target", "true");
      title.textContent = steps[index].title;
      progress.hidden = false; progress.textContent = `${index + 1} / ${steps.length}`;
      dismiss.hidden = true;
      copy.textContent = steps[index].copy;
      previous.hidden = false; previous.disabled = index === 0;
      next.textContent = index === steps.length - 1 ? "完成" : "下一步";
      dialog.scrollTop = 0;
      position();
      // A short viewport can show the explanation above a lower table edge.
      // Bring that pair into view without scrolling or moving the real board.
      const targetRect = targetForStep().getBoundingClientRect();
      const panelRect = panel.getBoundingClientRect();
      if (Math.max(targetRect.bottom, panelRect.bottom) > dialog.clientHeight) {
        dialog.scrollTop = Math.max(0, Math.min(targetRect.top, panelRect.top) - 12);
      }
      next.focus({preventScroll: true});
    };
    const previous = button("上一步", false, () => show(step - 1)); previous.hidden = true;
    const next = button("查看教程", true, () => step >= 0 && step === steps.length - 1 ? closeTutorial("completed") : show(step + 1));
    const dismiss = button("不再提示", false, () => closeTutorial("dismissed"));
    actions.append(previous, next, dismiss);
    panel.append(head, progress, copy, actions);
    stage.append(sample); dialog.append(stage, spotlight, panel);
    tutorial = {dialog, position, focus: doc.activeElement};
    dialog.addEventListener("cancel", event => { event.preventDefault(); closeTutorial(); });
    // Include scroll ports in the focus loop so keyboard users can practise too.
    dialog.addEventListener("keydown", event => {
      if (event.key !== "Tab") return;
      const nodes = [...dialog.querySelectorAll("button, [tabindex='0']")].filter(n => !n.disabled && n.getClientRects().length);
      const first = nodes[0], last = nodes.at(-1);
      if (event.shiftKey && doc.activeElement === first) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && doc.activeElement === last) { event.preventDefault(); first.focus(); }
    });
    doc.body.append(dialog); dialog.showModal();
    if (window.ResizeObserver) {
      tutorial.resizeObserver = new window.ResizeObserver(position);
      [stage, sample, panel].forEach(node => tutorial.resizeObserver.observe(node));
    }
    window.addEventListener("resize", position);
    window.visualViewport?.addEventListener("resize", position);
    position(); next.focus({preventScroll: true});
  }
  function syncTutorial(context) {
    const doc = context.board.ownerDocument;
    if (!doc.body || typeof window.HTMLDialogElement?.prototype.showModal !== "function") return;
    const waiting = context.room.status === "waiting" && context.room.room_kind === "invite";
    const key = `${context.room.room_id}:${context.viewer?.player_id || ""}`;
    if (tutorialVisit?.key !== key) { closeTutorial(null, false); tutorialVisit = {key, waiting}; }
    // Synchronous removal, before the first real board render. Never replay a
    // demo on revision changes or after waiting -> playing in this visit.
    if (tutorialVisit.waiting && !waiting) { closeTutorial(null, false); tutorialVisit.seen = true; }
    tutorialVisit.waiting = waiting;
    if (tutorialVisit.seen) return;
    tutorialVisit.seen = true;
    let remembered = false;
    try { remembered = ["completed", "dismissed"].includes(window.localStorage.getItem(TUTORIAL_KEY)); } catch (_) { /* Optional storage. */ }
    if (!remembered) openTutorial(context);
    if (!tutorialObserver) {
      tutorialObserver = new window.MutationObserver(() => {
        const view = doc.getElementById("gameView");
        const waitingPanel = doc.getElementById("inviteRoomPanel");
        const leftSurface = tutorialVisit?.waiting ? waitingPanel?.classList.contains("hidden")
          : !context.board.querySelector(".fc-game");
        if (view?.classList.contains("hidden") || leftSurface) {
          closeTutorial(null, false); tutorialVisit = null;
        }
      });
      [doc.getElementById("gameView"), doc.getElementById("inviteRoomPanel")].filter(Boolean)
        .forEach(node => tutorialObserver.observe(node, {attributes: true, attributeFilter: ["class"]}));
      tutorialObserver.observe(context.board, {childList: true});
    }
  }
  function renderBoard(context, tutorialSurface = false) {
    if (!tutorialSurface) syncTutorial(context);
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
    historyPiles(context, table, playZone, historyIds);
    const flow = state.flow || {}, status = el(doc, "div", "fc-turn");
    const phase = {discard: "出牌", draw: "翻牌", response: "回应", opening_hu: "确认"}[flow.phase];
    status.append(el(doc, "strong", "", context.isTerminal ? "本手结束" : phase ? `${context.canMove ? "请" : "待"}${phase}` : "等待开局"));
    center.append(status, playZone); table.append(center); root.append(table);
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
    // Each player's history has its own scroll position, including neutral piles.
    local.histories ||= {};
    table.querySelectorAll(".fc-discard-history").forEach(history => {
      const key = history.dataset.historyKey;
      const cards = [...history.querySelectorAll("[data-card-id]")].map(n => n.dataset.cardId).join(",");
      const saved = local.histories[key];
      const edge = history.getBoundingClientRect();
      const head = [...history.querySelectorAll(".fc-card > strong:first-child")].at(-1)?.getBoundingClientRect();
      // Reveal the newest identifying head, not its long trailing tail. Two
      // cards must not lose their first head just to scroll a blank tail in.
      history.scrollTop = saved?.cards === cards ? saved.top : Math.max(0, (head?.bottom || 0) - edge.bottom + 2);
      history.scrollLeft = saved?.cards === cards ? saved.left : Math.max(0, (head?.right || 0) - edge.right + 2);
      const remember = () => { local.histories[key] = {cards, top: history.scrollTop, left: history.scrollLeft}; };
      remember(); history.addEventListener("scroll", remember, {passive:true});
    });
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
    usesStandardMoveConfirmation: false, renderBoard, renderControls, renderRulePicker, syncTutorial,
  }));
}());

(function registerRummikub() {
  "use strict";
  const COLORS = ["red", "blue", "black", "orange"];
  const LABELS = {red: "红◆", blue: "蓝●", black: "黑■", orange: "橙▲", joker: "万能★"};
  const clone = value => JSON.parse(JSON.stringify(value));

  function element(doc, tag, cls, text) {
    const node = doc.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined) node.textContent = text;
    return node;
  }
  function setup(context) {
    const doc = context.board.ownerDocument;
    if (!doc.getElementById("rummikub-styles")) {
      const link = element(doc, "link");
      link.id = "rummikub-styles";
      link.rel = "stylesheet";
      link.href = "/static/games/rummikub.css?v=6";
      doc.head.appendChild(link);
    }
    const ui = context.uiState;
    if (!ui.rummikub) ui.rummikub = {
      melds: clone(context.state.melds || []), kinds: (context.state.meld_info || []).map(m => m.kind), selected: [], undo: [], sort: "color",
      target: null, error: "", busy: false, scroll: 0, handScroll: 0,
    };
    return ui.rummikub;
  }
  function tileMap(context) {
    return Object.fromEntries([
      ...Object.values(context.state.table_tiles || {}), ...(context.privateState?.hand || []),
    ].map(t => [t.id, t]));
  }
  // Local hints only. The server validates the whole atomic action, including
  // joker retrieval, conservation, opening and the authenticated revision.
  function shape(ids, tiles, kind = null) {
    if (ids.length < 3 || ids.length > 13) return null;
    const real = ids.map((id, i) => ({...tiles[id], i})).filter(t => t.color !== "joker");
    if (!real.length) return null;
    const candidates = [];
    if (ids.length <= 4 && new Set(real.map(t => t.number)).size === 1
        && new Set(real.map(t => t.color)).size === real.length) {
      candidates.push({kind: "group", points: real[0].number * ids.length});
    }
    const start = real[0].number - real[0].i;
    if (new Set(real.map(t => t.color)).size === 1 && start >= 1
        && start + ids.length - 1 <= 13 && real.every(t => t.number === start + t.i)) {
      candidates.push({kind: "run", points: ids.length * (2 * start + ids.length - 1) / 2});
    }
    return candidates.filter(c => !kind || c.kind === kind).sort((a, b) => b.points - a.points)[0] || null;
  }
  function checkpoint(ui) {
    ui.undo.push(clone({melds: ui.melds, kinds: ui.kinds}));
    if (ui.undo.length > 100) ui.undo.shift();
    ui.error = "";
  }
  function redraw(context, focusKey) {
    context.helpers.rerender();
    if (focusKey) {
      const doc = context.board.ownerDocument;
      const node = Array.from(doc.querySelectorAll("[data-rk-focus]")).find(n => n.dataset.rkFocus === focusKey);
      const fallback = doc.querySelector(".rk-hand button:not(:disabled)")
        || doc.querySelector(".rk-controls button:not(:disabled), .rk-sort button:not(:disabled)");
      (node && !node.disabled ? node : fallback)?.focus({preventScroll: true});
    }
  }
  function canEdit(context, ui) {
    return context.canMove && !context.isTerminal && !ui.busy;
  }
  function button(context, text, key, fn, disabled = false, primary = false) {
    const b = element(context.board.ownerDocument, "button",
      `pixel-btn compact rk-button${primary ? " rk-primary" : " secondary"}`, text);
    b.type = "button";
    b.dataset.rkFocus = key;
    b.disabled = disabled;
    b.addEventListener("click", () => { if (!b.disabled) fn(); });
    return b;
  }
  function sorted(ids, tiles, order) {
    return [...ids].sort((a, b) => {
      const x = tiles[a], y = tiles[b];
      const color = c => c === "joker" ? 4 : COLORS.indexOf(c);
      return (order === "number"
        ? (x.number || 14) - (y.number || 14) || color(x.color) - color(y.color)
        : color(x.color) - color(y.color) || x.number - y.number) || a.localeCompare(b);
    });
  }
  function quietButton(context, text, key, fn, disabled = false) {
    const b = button(context, text, key, fn, disabled);
    b.classList.add("rk-quiet");
    return b;
  }
  function moveSelection(context, target) {
    const ui = setup(context);
    if (!canEdit(context, ui) || !context.helpers.canMove() || !ui.selected.length) return;
    checkpoint(ui);
    const moved = [...ui.selected];
    ui.melds = ui.melds.map(m => m.filter(id => !moved.includes(id)));
    if (target === "new") { ui.melds.push(moved); ui.kinds.push(null); ui.target = ui.melds.length - 1; }
    else { ui.melds[target].push(...moved); ui.target = target; }
    ui.selected = [];
    redraw(context, target === "new" ? "new" : `target-${target}`);
  }
  function tile(context, id, tiles, enabled, where) {
    const ui = setup(context), t = tiles[id], doc = context.board.ownerDocument;
    const b = element(doc, "button", `rk-tile rk-${t.color}${ui.selected.includes(id) ? " is-selected" : ""}`);
    b.type = "button";
    b.dataset.tileId = id;
    b.dataset.rkFocus = `tile-${id}`;
    b.disabled = !enabled;
    b.setAttribute("aria-pressed", String(ui.selected.includes(id)));
    b.setAttribute("aria-label", `${where} · ${LABELS[t.color]}${t.number || ""}${id.endsWith("-2") ? "（第二张）" : "（第一张）"}`);
    b.append(element(doc, "strong", "rk-number", t.number || "★"),
      element(doc, "span", "rk-color", LABELS[t.color]));
    b.addEventListener("click", () => {
      if (!canEdit(context, ui) || !context.helpers.canMove()) return;
      ui.selected = ui.selected.includes(id) ? ui.selected.filter(t => t !== id) : ui.selected.concat(id);
      redraw(context, `tile-${id}`);
    });
    return b;
  }
  function draftStatus(context, ui, tiles) {
    const originalHand = new Set((context.privateState?.hand || []).map(t => t.id));
    const used = ui.melds.flat().filter(t => originalHand.has(t));
    const nonempty = ui.melds.map((m, i) => ({m, kind: ui.kinds[i]})).filter(row => row.m.length);
    const invalid = nonempty.findIndex(row => !shape(row.m, tiles, row.kind));
    const opening = !context.privateState?.opened;
    const points = nonempty.filter(row => row.m.every(t => originalHand.has(t)))
      .reduce((total, row) => total + (shape(row.m, tiles, row.kind)?.points || 0), 0);
    if (!used.length) return {ready: false, text: "点选手牌，再放入新组或目标组", points};
    if (invalid >= 0) return {ready: false, text: "草稿中有未完成组合，请继续整理", points};
    if (opening && points < 30) return {ready: false, text: `首次出牌 ${points} / 30 分，还差 ${30 - points} 分`, points};
    return {ready: true, text: `草稿已整理 · 打出 ${used.length} 张，可提交校验`, points};
  }
  function renderBoard(context) {
    const ui = setup(context), doc = context.board.ownerDocument, tiles = tileMap(context);
    const editable = canEdit(context, ui), opened = Boolean(context.privateState?.opened);
    context.helpers.setBoardLayout({rows: 1, cols: 1, large: true, ariaLabel: "拉密公共牌组与个人手牌"});
    const root = element(doc, "div", "rk-game");
    const header = element(doc, "div", "rk-header");
    header.append(element(doc, "strong", "", "公共牌组"),
      element(doc, "span", "rk-meta", `牌堆 ${context.state.pool_count || 0} 张 · ${ui.melds.filter(m => m.length).length} 组`));
    const table = element(doc, "section", "rk-table");
    table.setAttribute("aria-label", "可滚动的公共牌组草稿");
    table.tabIndex = 0;
    table.addEventListener("scroll", () => { ui.scroll = table.scrollTop; }, {passive: true});
    const originalIds = new Set((context.state.melds || []).flat());
    const placedIds = new Set(ui.melds.flat());
    const selectingFromHand = ui.selected.length > 0 && ui.selected.every(id => !placedIds.has(id));
    ui.melds.forEach((m, index) => {
      const info = shape(m, tiles, ui.kinds[index]);
      const row = element(doc, "div", `rk-meld${ui.target === index ? " is-target" : ""}${m.length && !info ? " is-incomplete" : ""}`);
      row.dataset.group = String(index);
      row.setAttribute("role", "group");
      const description = !m.length ? "空组" : info?.kind === "group" ? "同数组" : info ? "顺子" : "待整理";
      row.setAttribute("aria-label", `第 ${index + 1} 组 · ${description}`);
      const label = element(doc, "span", "rk-meld-label", `${index + 1} · ${description}`);
      label.setAttribute("aria-hidden", "true");
      const meta = element(doc, "div", "rk-meld-meta");
      const ambiguous = shape(m, tiles, "group") && shape(m, tiles, "run");
      const locked = !opened && m.some(t => originalIds.has(t));
      if (ambiguous) {
        const chooser = element(doc, "select", "rk-kind");
        chooser.setAttribute("aria-label", `第 ${index + 1} 组的组合类型`);
        chooser.dataset.rkFocus = `kind-${index}`;
        [["group", "同数组"], ["run", "同色顺子"]].forEach(([value, text]) => {
          const option = element(doc, "option", "", text); option.value = value; chooser.append(option);
        });
        chooser.value = info?.kind || "group";
        chooser.disabled = !editable || locked;
        chooser.addEventListener("change", () => {
          if (!context.helpers.canMove() || !canEdit(context, ui)) return;
          checkpoint(ui); ui.kinds[index] = chooser.value; redraw(context, `kind-${index}`);
        });
        meta.append(chooser);
      }
      if (editable && !locked && ui.selected.length) {
        // A native, transparent hit area gives the whole group keyboard and
        // pointer activation without nesting buttons or reserving a tool row.
        const target = element(doc, "button", "rk-target");
        target.type = "button";
        target.dataset.rkFocus = `target-${index}`;
        target.addEventListener("click", () => {
          if (!canEdit(context, ui) || !context.helpers.canMove() || !ui.selected.length) return;
          ui.target = index; redraw(context, `target-${index}`);
        });
        target.setAttribute("aria-label", `选第 ${index + 1} 组为目标`);
        target.setAttribute("aria-pressed", String(ui.target === index));
        row.append(target);
        if (selectingFromHand) row.classList.add("is-hand-target");
      }
      const list = element(doc, "div", "rk-tiles");
      m.forEach(id => {
        const item = tile(context, id, tiles, editable && !locked, `第 ${index + 1} 组`);
        // With only rack tiles selected, table tiles are part of the group hit
        // area. Starting from a table tile still allows multi-tile rearranging.
        if (row.classList.contains("is-hand-target")) item.tabIndex = -1;
        list.append(item);
      });
      row.append(label, list);
      if (meta.children.length) row.append(meta);
      table.append(row);
    });
    if (!ui.melds.length) table.append(element(doc, "p", "rk-empty", "桌面还没有组合 · 从自己的手牌开始"));
    root.append(header, table);
    const status = draftStatus(context, ui, tiles);
    if (context.isTerminal || context.state.result) {
      const result = context.state.result || context.room.result || {};
      const names = (result.winning_player_ids || []).map(id => context.participants.find(p => p.player_id === id)?.display_name || "玩家");
      root.append(element(doc, "p", "rk-result", names.length ? `${names.join("、")} ${names.length > 1 ? "并列获胜" : "获胜"}` : "本局已结束"));
      const scores = element(doc, "div", "rk-scores");
      Object.entries(result.tile_scores || {}).forEach(([pid, value]) => {
        const name = context.participants.find(p => p.player_id === pid)?.display_name || "玩家";
        scores.append(element(doc, "span", "", `${name}：${value > 0 ? "+" : ""}${value} 分`));
      });
      if (scores.children.length) root.append(scores, element(doc, "small", "rk-meta", "牌面局分 · 不影响平台筹码"));
    }
    const rack = element(doc, "section", "rk-rack");
    rack.setAttribute("aria-label", "自己的手牌");
    const used = new Set(ui.melds.flat());
    const hand = (context.privateState?.hand || []).map(t => t.id).filter(t => !used.has(t));
    const rackHead = element(doc, "div", "rk-header");
    rackHead.append(element(doc, "strong", "", `我的手牌 · ${hand.length} 张`),
      element(doc, "span", "rk-meta", opened ? "已开局" : `尚未开局 · ${status.points} / 30 分`));
    const sorts = element(doc, "div", "rk-sort");
    sorts.setAttribute("role", "group");
    sorts.setAttribute("aria-label", "手牌排序");
    [ ["color", "按颜色"], ["number", "按数字"] ].forEach(([order, text]) => {
      const b = button(context, text, `sort-${order}`, () => { ui.sort = order; redraw(context, `sort-${order}`); }, ui.busy);
      b.setAttribute("aria-pressed", String(ui.sort === order));
      sorts.append(b);
    });
    const tools = element(doc, "div", "rk-rack-tools");
    tools.append(sorts);
    if (!context.isTerminal && context.canMove) tools.append(quietButton(context, "整理建议", "suggest", () => {
      checkpoint(ui); ui.melds = clone(context.privateState.suggested_move.melds);
      ui.kinds = clone(context.privateState.suggested_move.kinds || ui.melds.map(m => shape(m, tiles).kind));
      ui.selected = []; ui.target = null; redraw(context, "suggest");
    }, !editable || context.privateState?.suggested_move?.action !== "meld"));
    const rackTiles = element(doc, "div", "rk-tiles rk-hand");
    rackTiles.addEventListener("scroll", () => { ui.handScroll = rackTiles.scrollTop; }, {passive: true});
    sorted(hand, tiles, ui.sort).forEach(id => rackTiles.append(tile(context, id, tiles, editable, "手牌")));
    if (!hand.length) rackTiles.append(element(doc, "span", "rk-meta", "手牌已放入草稿或已出完"));
    rack.append(rackHead, tools, rackTiles);
    root.append(rack);
    context.board.append(root);
    table.scrollTop = ui.scroll;
    rackTiles.scrollTop = ui.handScroll || 0;
  }
  async function submit(context, move) {
    const ui = setup(context);
    if (ui.busy || !context.helpers.canMove()) return;
    ui.busy = true; ui.error = "";
    redraw(context);
    try {
      const ok = await context.helpers.submitMove(move);
      if (!ok) ui.error = "未提交成功，草稿已保留。请查看对局提示；若局面已更新，请重新整理。";
    } catch (_) {
      ui.error = "连接失败，草稿已保留；请重试或刷新局面。";
    } finally {
      ui.busy = false;
      redraw(context);
    }
  }
  function renderControls(context) {
    const ui = setup(context), doc = context.controls.ownerDocument, tiles = tileMap(context);
    const editable = canEdit(context, ui), status = draftStatus(context, ui, tiles);
    const panel = element(doc, "div", "rk-controls");
    panel.setAttribute("aria-busy", String(ui.busy));
    const prompt = element(doc, "p", `rk-draft-status${status.ready ? " is-ready" : ""}`,
      context.isTerminal ? "本局已结束" : ui.busy ? "正在提交整回合…" : context.canMove ? status.text : "等待其他玩家行动");
    prompt.setAttribute("role", "status");
    panel.append(prompt);
    if (!context.isTerminal) {
      if (context.canMove && ui.selected.length) {
        const selections = element(doc, "div", "rk-selection");
        const selectionHead = element(doc, "div", "rk-header");
        const selectedCount = element(doc, "span", "rk-meta", `已选 ${ui.selected.length} 张${ui.target !== null ? ` · 目标第 ${ui.target + 1} 组` : ""}`);
        selectedCount.setAttribute("role", "status");
        selectionHead.append(selectedCount, quietButton(context, "取消", "clear", () => {
          const focus = `tile-${ui.selected[0]}`;
          ui.selected = []; redraw(context, focus);
        }, !editable));
        selections.append(selectionHead);
        const moveButtons = element(doc, "div", "rk-selection-actions");
        moveButtons.append(button(context, "新建组", "new", () => moveSelection(context, "new"), !editable));
        if (ui.target !== null) moveButtons.append(
          button(context, "移入目标组", "move", () => moveSelection(context, ui.target), !editable));
        const own = new Set((context.privateState?.hand || []).map(t => t.id));
        if (ui.selected.every(id => own.has(id)) && ui.selected.some(id => ui.melds.some(m => m.includes(id)))) {
          moveButtons.append(quietButton(context, "收回手牌", "return", () => {
            checkpoint(ui);
            ui.melds = ui.melds.map(m => m.filter(id => !ui.selected.includes(id) || !own.has(id)));
            ui.selected = []; redraw(context, "return");
          }, !editable));
        }
        selections.append(moveButtons);
        if (ui.target === null && ui.melds.some(m => context.privateState?.opened || m.every(id => own.has(id)))) {
          selections.append(element(doc, "span", "rk-help", "也可点上方牌组选为目标，再移入。"));
        }
        panel.append(selections);
        if (ui.selected.length === 1) {
          const selected = ui.selected[0], index = ui.melds.findIndex(m => m.includes(selected));
          if (index >= 0) {
            const pos = ui.melds[index].indexOf(selected), order = element(doc, "div", "rk-actions");
            [-1, 1].filter(offset => pos + offset >= 0 && pos + offset < ui.melds[index].length).forEach(offset => order.append(quietButton(context, offset < 0 ? "选牌向前" : "选牌向后", `shift-${offset}`, () => {
              checkpoint(ui);
              const m = ui.melds[index]; [m[pos], m[pos + offset]] = [m[pos + offset], m[pos]];
              redraw(context, `shift-${offset}`);
            }, !editable)));
            panel.append(order);
          }
        }
      }
      if (context.canMove && ui.undo.length) {
        const history = element(doc, "div", "rk-history");
        history.append(
          button(context, "撤销", "undo", () => { Object.assign(ui, ui.undo.pop()); ui.selected = []; ui.target = null; ui.error = ""; redraw(context, "undo"); }, !editable),
          quietButton(context, "重置本回合", "reset", () => { checkpoint(ui); ui.melds = clone(context.state.melds || []); ui.kinds = (context.state.meld_info || []).map(m => m.kind); ui.selected = []; ui.target = null; redraw(context, "reset"); }, !editable));
        panel.append(history);
      }
      const actions = element(doc, "div", "rk-commit");
      actions.append(
        button(context, "提交出牌", "submit", () => submit(context, {action: "meld", melds: ui.melds.filter(m => m.length),
          kinds: ui.melds.map((m, i) => m.length ? shape(m, tiles, ui.kinds[i]).kind : null).filter(Boolean)}), !editable || !status.ready, context.canMove && status.ready),
        button(context, context.state.pool_count ? "摸一张并结束" : "无牌可出", "draw", () => submit(context, {action: context.state.pool_count ? "draw" : "pass"}), !editable, context.canMove && !status.ready),
      );
      panel.append(actions);
      if (context.canMove && ui.undo.length) panel.append(element(doc, "p", "rk-help", "草稿尚未提交；摸牌结束会放弃本回合整理。"));
      if (!context.state.pool_count) panel.append(element(doc, "p", "rk-help", `牌堆已空，仍可出牌。${(context.state.blocked_player_ids || []).length} 人已声明无牌可出；一整圈声明后结算。`));
      if (ui.error) { const e = element(doc, "p", "rk-error", ui.error); e.setAttribute("role", "alert"); panel.append(e); }
    }
    context.controls.append(panel);
  }
  window.DuelGameUI.register("rummikub", {
    participantPresentation: "generic", ownsPrivateStatePresentation: true,
    usesStandardMoveConfirmation: false, renderBoard, renderControls,
  });
}());

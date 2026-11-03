(function registerLiarsDiceReviewRenderer(global) {
  "use strict";

  const STYLE_ID = "duel-game-liars-dice-review-styles";
  const STYLE_HREF = "/static/games/liars_dice.css?v=0.1.5";

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
    link.dataset.duelGameStyle = "liars_dice";
    documentRef.head.appendChild(link);
    return link;
  }

  function element(documentRef, tag, className, text = null) {
    const node = documentRef.createElement(tag);
    if (className) node.className = className;
    if (text !== null) node.textContent = String(text);
    return node;
  }

  function participantName(context, playerId) {
    const participant = (context.participants || []).find(
      (item) => item.player_id === playerId
    );
    return participant && (participant.display_name || participant.player_id)
      || playerId || "玩家";
  }

  function createDie(documentRef, value) {
    const face = Number(value);
    const die = element(documentRef, "i", "liars-die");
    die.setAttribute("data-face", String(face));
    die.setAttribute("role", "img");
    die.setAttribute("aria-label", `${face} 点骰子`);
    return die;
  }

  function appendTerminalReview(context) {
    const diceByPlayer = context.state && context.state.terminal_dice;
    if (!diceByPlayer || typeof diceByPlayer !== "object") return;
    const documentRef = context.board.ownerDocument || global.document;
    const review = element(documentRef, "details", "liars-terminal-review");
    review.open = true;
    review.appendChild(element(
      documentRef,
      "summary",
      "liars-terminal-summary",
      `终局骰子复盘 · ${Object.keys(diceByPlayer).length} 家`
    ));
    const rows = element(documentRef, "div", "liars-terminal-dice");
    Object.entries(diceByPlayer).forEach(([playerId, dice]) => {
      const row = element(documentRef, "div", "liars-terminal-row");
      row.dataset.playerId = playerId;
      const owner = element(
        documentRef,
        "span",
        "liars-terminal-owner",
        `${context.state.teams ? `${teamName(context.state.teams, playerId)}共用骰盅` : participantName(context, playerId)}：`
      );
      const tray = element(documentRef, "span", "liars-dice-row liars-terminal-dice-row");
      if (Array.isArray(dice) && dice.length) {
        dice.forEach((value) => tray.appendChild(createDie(documentRef, value)));
      } else {
        tray.textContent = "无骰";
      }
      row.append(owner, tray);
      rows.appendChild(row);
    });
    review.appendChild(rows);
    context.board.appendChild(review);
  }

  function renderRulePicker(target, select) {
    const doc = target.ownerDocument;
    target.className = "pixel-field liars-rule-picker";
    const label = element(doc, "label", "pixel-field");
    label.appendChild(element(doc, "span", "", "吹牛骰子玩法"));
    const mode = element(doc, "select");
    mode.setAttribute("aria-label", "吹牛骰子玩法");
    [["classic", "原版·淘汰赛"], ["party", "酒桌大话骰"]].forEach(([value, text]) => {
      const option = element(doc, "option", "", text); option.value = value; mode.appendChild(option);
    });
    mode.value = select.dataset.diceMode || "classic";
    mode.addEventListener("change", () => {
      select.dataset.diceMode = mode.value;
      select.dispatchEvent(new Event("change"));
      doc.querySelector(`#${target.id} select`)?.focus({preventScroll: true});
    });
    label.appendChild(mode); target.appendChild(label);
    if (mode.value === "party") {
      ensureStylesheet(doc);
      const advanced = element(doc, "details", "liars-advanced");
      advanced.appendChild(element(doc, "summary", "", "可选房规"));
      const group = (title) => {
        const fieldset = element(doc, "fieldset", "liars-rule-group");
        fieldset.appendChild(element(doc, "legend", "", title));
        advanced.appendChild(fieldset);
        return fieldset;
      };
      const counting = group("计数与起叫");
      const penalties = group("开骰结算");
      const surrender = group("投降");
      const literalLabel = element(doc, "label", "pixel-field");
      literalLabel.appendChild(element(doc, "span", "", "有骰计骰"));
      const literal = element(doc, "select");
      literal.setAttribute("aria-label", "有骰计骰");
      [["false", "关闭 · 围骰加数、单骰可重摇"], ["true", "开启 · 不加数、不重摇，1仍百搭"]].forEach(([value, text]) => {
        const option = element(doc, "option", "", text); option.value = value; literal.appendChild(option);
      });
      literal.value = select.dataset.literalDice || "false";
      literal.addEventListener("change", () => { select.dataset.literalDice = literal.value; });
      literalLabel.appendChild(literal); counting.appendChild(literalLabel);
      const rules = JSON.parse(select.dataset.diceRules || "{}");
      const persist = () => { select.dataset.diceRules = JSON.stringify(rules); };
      const inputs = {};
      [
        ["cup_penalty", "围骰 / 全围的叫点者败，扣分×2"],
        ["extreme_penalty", "不足一半 / 超过两倍，相应败方×2"],
        ["allow_surrender", "允许本轮投降 · 认罚0.5分"],
        ["surrender_no_reroll", "投降不揭骰、不重摇"],
        ["dark_rule", "天黑黑 · 无所叫实点则反转胜负"],
        ["companion_penalty", "随机陪扣 · 抽中本人或同队再×2"],
      ].forEach(([key, text]) => {
        const row = element(doc, "label", "liars-random-toggle");
        const input = element(doc, "input"); input.type = "checkbox";
        input.checked = Boolean(rules[key]); input.setAttribute("aria-label", text);
        inputs[key] = input;
        input.addEventListener("change", () => {
          rules[key] = input.checked;
          if (key === "allow_surrender") {
            inputs.surrender_no_reroll.disabled = !input.checked;
            if (!input.checked) { inputs.surrender_no_reroll.checked = false; rules.surrender_no_reroll = false; }
          }
          persist();
        });
        row.append(input, element(doc, "span", "", text));
        (key === "allow_surrender" || key === "surrender_no_reroll" ? surrender : penalties).appendChild(row);
      });
      inputs.surrender_no_reroll.disabled = !rules.allow_surrender;
      const openingLabel = element(doc, "label", "pixel-field liars-opening-field");
      openingLabel.appendChild(element(doc, "span", "", "5人普通起叫"));
      const opening = element(doc, "select"); opening.setAttribute("aria-label", "5人普通起叫");
      [8, 7].forEach(n => { const option = element(doc, "option", "", `${n}起${n === 8 ? " · 向上取整" : " · 地区约定"}`); option.value = n; opening.appendChild(option); });
      opening.value = rules.opening_five || 8;
      opening.addEventListener("change", () => { rules.opening_five = Number(opening.value); persist(); });
      openingLabel.appendChild(opening); counting.appendChild(openingLabel);
      counting.appendChild(element(doc, "small", "invite-note", "其他人数起叫数量向上取整。"));
      penalties.appendChild(element(doc, "small", "invite-note", "每个受罚人/队每轮最多扣4分。"));
      surrender.appendChild(element(doc, "small", "invite-note", "投降固定扣0.5分，不叠加其他处罚。"));
      const teamsLabel = element(doc, "label", "pixel-field");
      teamsLabel.appendChild(element(doc, "span", "", "组队玩法（可选）"));
      const teams = element(doc, "select");
      teams.setAttribute("aria-label", "组队玩法");
      [["none", "不组队 · 每人一盅"], ["teams", "红蓝两队 · 每队一盅（4/6人）"]].forEach(([value, text]) => {
        const option = element(doc, "option", "", text); option.value = value; teams.appendChild(option);
      });
      teams.value = select.dataset.diceTeams || "none";
      teams.addEventListener("change", () => {
        select.dataset.diceTeams = teams.value;
        select.dispatchEvent(new Event("change"));
        doc.querySelector(`#${target.id} select[aria-label="组队玩法"]`)?.focus({preventScroll: true});
      });
      teamsLabel.appendChild(teams); target.appendChild(teamsLabel);
      if (teams.value === "teams") {
        ensureStylesheet(doc);
        const randomLabel = element(doc, "label", "liars-random-toggle");
        const random = element(doc, "input");
        random.type = "checkbox";
        random.checked = select.dataset.randomTeams !== "false";
        random.setAttribute("aria-label", "随机组队");
        random.addEventListener("change", () => {
          select.dataset.randomTeams = String(random.checked);
          select.dispatchEvent(new Event("change"));
        });
        randomLabel.append(random, element(doc, "span", "", "随机组队"));
        target.appendChild(randomLabel);
        if (!random.checked) {
          const setup = element(doc, "button", "pixel-btn secondary compact liars-open-teams", "组队");
          setup.type = "button";
          setup.addEventListener("click", () => global.openDiceTeamSelection(select));
          target.appendChild(setup);
        }
        target.appendChild(element(doc, "small", "invite-note", random.checked
          ? "开局随机分成红蓝两队，每队共享5骰和队分。"
          : "开房后本人选座，约20秒后可开始；未选者随机补位。"));
      }
      target.appendChild(advanced);
      target.appendChild(element(doc, "small", "invite-note", "败方本局-1分，赢家不加分；叫斋后本轮锁斋。"));
    }
  }

  function teamName(teams, playerId) {
    const key = Object.keys(teams).find(team => teams[team].includes(playerId));
    return key === "red" ? "红队" : key === "blue" ? "蓝队" : `第${Object.keys(teams).indexOf(key) + 1}队`;
  }

  const panels = new Map();
  global.DuelDiceTeams = {
    openNext: false,
    renderSelection(target, room, helpers) {
      const doc = target.ownerDocument;
      ensureStylesheet(doc);
      const selection = room.team_selection;
      let view = panels.get(room.room_id);
      if (!view) {
        view = {open: this.openNext, collapsed: false};
        panels.set(room.room_id, view);
        this.openNext = false;
      }
      if (selection.can_start && !view.collapsed) {
        view.open = false;
        view.collapsed = true;
      }
      const section = element(doc, "section", "liars-team-selection");
      const toggle = element(doc, "button", "pixel-btn secondary compact", "组队");
      toggle.type = "button";
      toggle.setAttribute("aria-expanded", String(view.open));
      toggle.setAttribute("aria-controls", "liars-team-seats");
      const seats = element(doc, "div", "liars-team-seats");
      seats.id = "liars-team-seats";
      seats.classList.toggle("hidden", !view.open);
      toggle.addEventListener("click", () => {
        view.open = !view.open;
        toggle.setAttribute("aria-expanded", String(view.open));
        seats.classList.toggle("hidden", !view.open);
      });
      section.append(toggle, element(doc, "p", "invite-note", selection.can_start
        ? "可以开始了；未选座的成员将随机补位。"
        : "点击空位加入或换位；约20秒后房主可以开始。"));
      for (const [team, members] of Object.entries(selection.seats)) {
        const teamLabel = team === "red" ? "红队" : "蓝队";
        const row = element(doc, "section", `liars-team-row liars-team-${team}`);
        row.appendChild(element(doc, "h3", "", teamLabel));
        const cells = element(doc, "div", "liars-team-cells");
        members.forEach((id, i) => {
          const p = room.participants.find(member => member.player_id === id);
          const cell = element(doc, p ? "div" : "button", p ? "liars-team-seat" : "pixel-btn secondary liars-team-seat", p ? null : "+");
          if (p) {
            const avatar = element(doc, "span", "room-participant-avatar");
            helpers.avatar(avatar, p, room);
            cell.append(avatar, element(doc, "span", "liars-team-name", p.display_name),
              element(doc, "small", "liars-team-id", `ID ${helpers.accountId(p)}`));
            cell.classList.toggle("liars-my-seat", id === helpers.viewer);
          } else {
            cell.type = "button";
            cell.setAttribute("aria-label", `加入${teamLabel}第${i + 1}位`);
            cell.addEventListener("click", async () => {
              cell.disabled = true;
              try { await helpers.choose(team, i + 1); } finally { cell.disabled = false; }
            });
          }
          cells.appendChild(cell);
        });
        row.appendChild(cells);
        seats.appendChild(row);
      }
      section.appendChild(seats);
      target.appendChild(section);
    },
  };

  function penaltyText(context, p) {
    const names = {cup_loser:"围骰败方", under_half:"不足一半", over_double:"超过两倍", companion:"陪扣", companion_self:"抽中本人/同队"};
    const hits = (p.hits || []).map(k => names[k] || k).join("、") || "基础";
    return `${participantName(context, p.player_id)}${p.team_id ? "所在队" : ""} · ${hits} · 原始${p.raw_multiplier}倍 → ${p.multiplier}倍（封顶4） · ${p.score_delta}分`;
  }

  function appendPenaltyReview(result, context, outcome) {
    const doc = result.ownerDocument || global.document;
    const penalties = outcome.penalties || [];
    if (!penalties.length) return;
    const details = element(doc, "details", "liars-reveal-details liars-penalty-details");
    details.appendChild(element(doc, "summary", "", "扣分明细"));
    penalties.forEach(p => {
      const label = penalties.length === 1 ? "实际倍率" : `${participantName(context, p.player_id)}${p.team_id ? "所在队" : ""} ${p.score_delta}分 ·`;
      result.appendChild(element(doc, "p", "liars-result-line liars-penalty-summary",
        `${label} ×${p.multiplier}${p.raw_multiplier > p.multiplier ? "（已封顶）" : ""}`));
      details.appendChild(element(doc, "p", "liars-result-line liars-penalty", penaltyText(context, p)));
    });
    if (outcome.companion) {
      details.appendChild(element(doc, "p", "liars-result-line", `陪扣骰 ${outcome.companion.rolls.join("、")} → 第${outcome.companion.seat}位 ${participantName(context, outcome.companion.player_id)}`));
    }
    result.appendChild(details);
  }

  global.DuelDiceRules = {penaltyText, appendPenaltyReview};

  function appendPartyRecords(context) {
    if (context.state?.mode !== "party") return;
    const doc = context.board.ownerDocument || global.document;
    const panel = element(doc, "section", "liars-round-result liars-party-scores");
    panel.setAttribute("aria-label", "本局积分");
    panel.appendChild(element(doc, "strong", "liars-result-title", "本局积分"));
    const scoreGrid = element(doc, "div", "liars-score-grid");
    const scores = context.state.scores || {};
    Object.entries(scores).forEach(([id, score]) => {
      const team = Object.entries(context.state.teams || {}).find(([,members]) => members.includes(id));
      const teamLabel = team ? ` · ${teamName(context.state.teams, id)}共用` : "";
      const row = element(doc, "div", "liars-score-row");
      row.append(element(doc, "span", "liars-score-name", participantName(context, id)),
        element(doc, "strong", "liars-score-value", `${score}分`));
      if (teamLabel) row.appendChild(element(doc, "small", "liars-score-team", teamLabel.slice(3)));
      scoreGrid.appendChild(row);
    });
    panel.appendChild(scoreGrid);
    const history = element(doc, "details", "liars-reveal-details");
    history.appendChild(element(doc, "summary", "", `回合扣分记录（${(context.state.round_results || []).length}轮）`));
    (context.state.round_results || []).forEach((r) => {
      const loser = participantName(context, r.loser_player_id) + (r.loser_team_id ? "所在队" : "");
      const detail = r.phase === "surrendered" ? "投降认罚" : `${r.bid.quantity}个${r.bid.face}点${r.bid.exact ? "（斋）" : ""} · 实际${r.actual_count}`;
      history.appendChild(element(doc, "p", "liars-result-line", `第${r.round}轮 · ${detail} · ${loser} ${r.score_delta ?? -1}分，累计${r.score_after}分`));
      (r.penalties || []).forEach(p => history.appendChild(element(doc, "p", "liars-result-line", penaltyText(context, p))));
      if (r.dark_reversed) history.appendChild(element(doc, "p", "liars-result-line", "天黑黑：无所叫实点，反转输赢"));
      if (r.companion) history.appendChild(element(doc, "p", "liars-result-line", `陪扣骰${r.companion.rolls.join("、")} → 第${r.companion.seat}位 ${participantName(context, r.companion.player_id)}`));
    });
    panel.appendChild(history);
    context.board.appendChild(panel);
  }

  function renderBoard(context) {
    const documentRef = context.board.ownerDocument || global.document;
    ensureStylesheet(documentRef);
    if (typeof global.renderLiarsDice !== "function") {
      throw new Error("吹牛骰子原桌面 renderer 尚未就绪");
    }
    appendPartyRecords(context);
    global.renderLiarsDice(context.board, context.state);
    appendTerminalReview(context);
    return true;
  }

  global.DuelGameUI.register("liars_dice", {
    participantPresentation: "generic",
    usesStandardMoveConfirmation: false,
    renderBoard,
    renderRulePicker,
  });
}(window));

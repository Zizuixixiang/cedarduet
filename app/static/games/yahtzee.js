(function registerYahtzeeRenderer() {
  "use strict";

  const FALLBACK_CATEGORIES = [
    ["ones", "一点", "upper"],
    ["twos", "二点", "upper"],
    ["threes", "三点", "upper"],
    ["fours", "四点", "upper"],
    ["fives", "五点", "upper"],
    ["sixes", "六点", "upper"],
    ["three_of_a_kind", "三条", "lower"],
    ["four_of_a_kind", "四条", "lower"],
    ["full_house", "葫芦", "lower"],
    ["small_straight", "小顺", "lower"],
    ["large_straight", "大顺", "lower"],
    ["yahtzee", "快艇 / 五同", "lower"],
    ["chance", "机会", "lower"],
  ].map(([key, label, section]) => ({key, label, section}));
  const PIP_POSITIONS = {
    1: [5],
    2: [1, 9],
    3: [1, 5, 9],
    4: [1, 3, 7, 9],
    5: [1, 3, 5, 7, 9],
    6: [1, 3, 4, 6, 7, 9],
  };

  function playerName(participant) {
    return participant.display_name || participant.player_id || "玩家";
  }

  function createDie(documentRef, value, index, held, selectable, onToggle) {
    const die = documentRef.createElement(value ? "button" : "div");
    die.className = `yahtzee-die${held ? " held" : ""}${value ? "" : " empty"}`;
    die.dataset.dieIndex = String(index);
    if (!value) {
      die.setAttribute("aria-hidden", "true");
      return die;
    }
    die.type = "button";
    die.disabled = !selectable;
    die.setAttribute("aria-pressed", String(held));
    die.setAttribute(
      "aria-label",
      `第 ${index + 1} 枚骰子，${value} 点，${held ? "已保留" : "未保留"}`
    );
    const activePips = new Set(PIP_POSITIONS[value] || []);
    for (let position = 1; position <= 9; position += 1) {
      const pip = documentRef.createElement("span");
      pip.className = `yahtzee-pip${activePips.has(position) ? " on" : ""}`;
      pip.setAttribute("aria-hidden", "true");
      die.appendChild(pip);
    }
    const badge = documentRef.createElement("span");
    badge.className = "yahtzee-hold-badge";
    badge.textContent = "保留";
    badge.setAttribute("aria-hidden", "true");
    die.appendChild(badge);
    die.addEventListener("click", () => onToggle(die));
    return die;
  }

  function appendScoreSummary(documentRef, body, label, key, participants, totals) {
    const row = documentRef.createElement("tr");
    row.className = `yahtzee-summary-row yahtzee-summary-${key}`;
    const heading = documentRef.createElement("th");
    heading.scope = "row";
    heading.textContent = label;
    row.appendChild(heading);
    participants.forEach((participant) => {
      const cell = documentRef.createElement("td");
      cell.textContent = String((totals[participant.player_id] || {})[key] || 0);
      row.appendChild(cell);
    });
    const filler = documentRef.createElement("td");
    filler.className = "yahtzee-score-action";
    filler.textContent = {
      upper_bonus: "63 → +35",
      yahtzee_bonus: "每次 +100",
    }[key] || "";
    row.appendChild(filler);
    body.appendChild(row);
  }

  function renderBoard(context) {
    const {
      board,
      state,
      room,
      helpers,
    } = context;
    const documentRef = context.document || window.document;
    const submitMove = helpers && helpers.submitMove;
    const participants = Array.isArray(context.participants)
      ? context.participants
      : (Array.isArray(room.participants) ? room.participants : []);
    const categories = Array.isArray(state.categories) && state.categories.length === 13
      ? state.categories
      : FALLBACK_CATEGORIES;
    const legalActions = Array.isArray(context.legalActions)
      ? context.legalActions
      : [];
    const hasAuthoritativeActions = legalActions.length > 0;
    const dice = Array.isArray(state.dice) ? state.dice : [];
    const rollsUsed = Number(state.rolls_used || 0);
    const maxRolls = Number(state.max_rolls || 3);
    const jokerActive = Boolean(state.joker_active);
    const pendingYahtzeeBonus = Number(state.pending_yahtzee_bonus || 0);
    const humanCanMove = Boolean(context.canMove);
    const canSelectDice = humanCanMove && dice.length === 5 && rollsUsed < maxRolls;
    const heldMask = Array.from(
      {length: 5},
      (_, index) => Boolean(state.held_mask && state.held_mask[index])
    );
    const root = documentRef.createElement("section");
    root.className = "yahtzee-game";
    root.style.setProperty("--yahtzee-player-count", String(participants.length || 2));

    const rollPanel = documentRef.createElement("section");
    rollPanel.className = "yahtzee-roll-panel";
    const turnCopy = documentRef.createElement("div");
    turnCopy.className = "yahtzee-turn-copy";
    const heading = documentRef.createElement("strong");
    const round = Number((state.flow || {}).round_number || 1);
    const actor = participants.find(
      (participant) => participant.player_id === room.current_player_id
    );
    heading.textContent = `第 ${round} / 13 轮 · ${actor ? playerName(actor) : "本局"}`;
    const status = documentRef.createElement("span");
    status.textContent = dice.length
      ? `已掷 ${rollsUsed} / ${maxRolls} 次 · 点骰子选择保留`
      : (humanCanMove ? "请先掷骰" : "等待当前玩家掷骰");
    turnCopy.append(heading, status);

    const diceTray = documentRef.createElement("div");
    diceTray.className = "yahtzee-dice-tray";
    for (let index = 0; index < 5; index += 1) {
      const die = createDie(
        documentRef,
        dice[index],
        index,
        heldMask[index],
        canSelectDice,
        (target) => {
          heldMask[index] = !heldMask[index];
          target.classList.toggle("held", heldMask[index]);
          target.setAttribute("aria-pressed", String(heldMask[index]));
          target.setAttribute(
            "aria-label",
            `第 ${index + 1} 枚骰子，${dice[index]} 点，${heldMask[index] ? "已保留" : "未保留"}`
          );
        }
      );
      diceTray.appendChild(die);
    }

    const rollActions = documentRef.createElement("div");
    rollActions.className = "yahtzee-roll-actions";
    const rollButton = documentRef.createElement("button");
    rollButton.type = "button";
    rollButton.className = "pixel-btn yahtzee-roll-button";
    rollButton.textContent = rollsUsed
      ? (rollsUsed < maxRolls ? `第 ${rollsUsed + 1} 次掷骰` : "本回合已掷满 3 次")
      : "掷 5 枚骰子";
    const rollIsLegal = !hasAuthoritativeActions
      ? rollsUsed < maxRolls
      : legalActions.some((action) => action.action === "roll");
    rollButton.disabled = !humanCanMove || !rollIsLegal;
    rollButton.addEventListener("click", async () => {
      rollButton.disabled = true;
      const submitted = await submitMove({action: "roll", held_mask: heldMask});
      if (!submitted) {
        const canMoveNow = helpers && typeof helpers.canMove === "function"
          ? helpers.canMove()
          : humanCanMove;
        rollButton.disabled = !canMoveNow || !rollIsLegal;
      }
    });
    const scratchLabel = documentRef.createElement("label");
    scratchLabel.className = "yahtzee-scratch-toggle";
    const scratch = documentRef.createElement("input");
    scratch.type = "checkbox";
    scratch.disabled = !humanCanMove || dice.length !== 5 || jokerActive;
    const scratchText = documentRef.createElement("span");
    scratchText.textContent = jokerActive
      ? "Joker 回合按规则计分"
      : "划掉类别，记 0 分";
    scratchLabel.append(scratch, scratchText);
    rollActions.append(rollButton, scratchLabel);
    rollPanel.append(turnCopy, diceTray, rollActions);

    const scoreSection = documentRef.createElement("section");
    scoreSection.className = "yahtzee-score-section";
    const scoreHeading = documentRef.createElement("div");
    scoreHeading.className = "yahtzee-score-heading";
    const scoreTitle = documentRef.createElement("strong");
    scoreTitle.textContent = "计分卡";
    const scoreHint = documentRef.createElement("span");
    scoreHint.textContent = "— 未填 · 数字为已填得分 · 右栏为本轮预估";
    scoreHeading.append(scoreTitle, scoreHint);
    let jokerNotice = null;
    if (jokerActive) {
      jokerNotice = documentRef.createElement("p");
      jokerNotice.className = "yahtzee-joker-notice";
      jokerNotice.textContent = pendingYahtzeeBonus
        ? `重复快艇：本次另加 ${pendingYahtzeeBonus} 分；Joker 已限定可填格。`
        : "Joker：本次没有重复快艇奖励；已限定可填格。";
    }

    const scroller = documentRef.createElement("div");
    scroller.className = "yahtzee-scorecard-scroll";
    scroller.tabIndex = 0;
    scroller.setAttribute("aria-label", "所有玩家的 13 项计分卡，可横向滚动");
    const table = documentRef.createElement("table");
    table.className = "yahtzee-scorecard";
    const tableHead = documentRef.createElement("thead");
    const headerRow = documentRef.createElement("tr");
    const categoryHeader = documentRef.createElement("th");
    categoryHeader.scope = "col";
    categoryHeader.textContent = "类别";
    headerRow.appendChild(categoryHeader);
    participants.forEach((participant) => {
      const header = documentRef.createElement("th");
      header.scope = "col";
      header.textContent = playerName(participant);
      header.title = playerName(participant);
      header.className = participant.player_id === room.current_player_id ? "current" : "";
      headerRow.appendChild(header);
    });
    const actionHeader = documentRef.createElement("th");
    actionHeader.scope = "col";
    actionHeader.className = "yahtzee-score-action";
    actionHeader.textContent = "本轮预估";
    headerRow.appendChild(actionHeader);
    tableHead.appendChild(headerRow);
    table.appendChild(tableHead);

    const body = documentRef.createElement("tbody");
    categories.forEach((category, index) => {
      const row = documentRef.createElement("tr");
      row.className = `yahtzee-category-row section-${category.section}`;
      if (index === 6) row.classList.add("section-start");
      const label = documentRef.createElement("th");
      label.scope = "row";
      label.textContent = category.label;
      row.appendChild(label);
      participants.forEach((participant) => {
        const card = (state.scorecards || {})[participant.player_id] || {};
        const filled = Object.prototype.hasOwnProperty.call(card, category.key);
        const cell = documentRef.createElement("td");
        cell.className = filled ? "filled" : "unused";
        cell.textContent = filled ? String(card[category.key]) : "—";
        cell.setAttribute(
          "aria-label",
          `${playerName(participant)}，${category.label}，${filled ? `${card[category.key]} 分` : "未填写"}`
        );
        row.appendChild(cell);
      });
      const actionCell = documentRef.createElement("td");
      actionCell.className = "yahtzee-score-action";
      const preview = (state.score_previews || {})[category.key];
      const currentCard = (state.scorecards || {})[room.current_player_id] || {};
      const categoryUnused = !Object.prototype.hasOwnProperty.call(
        currentCard, category.key
      );
      const scoreIsLegal = !hasAuthoritativeActions
        ? categoryUnused
        : legalActions.some((action) => (
          action.action === "score" && action.category === category.key
        ));
      if (humanCanMove && dice.length === 5 && categoryUnused && scoreIsLegal) {
        const scoreButton = documentRef.createElement("button");
        scoreButton.type = "button";
        scoreButton.className = "yahtzee-score-button";
        scoreButton.textContent = preview === undefined ? "记分" : `${preview} 分`;
        scoreButton.setAttribute(
          "aria-label",
          `${category.label}，本轮预估 ${preview === undefined ? 0 : preview} 分，点击填写`
        );
        scoreButton.addEventListener("click", async () => {
          scoreButton.disabled = true;
          const submitted = await submitMove({
            action: "score",
            category: category.key,
            ...(scratch.checked ? {zero: true} : {}),
          });
          if (!submitted) scoreButton.disabled = false;
        });
        actionCell.appendChild(scoreButton);
      } else {
        actionCell.textContent = categoryUnused ? "—" : "已填";
      }
      row.appendChild(actionCell);
      body.appendChild(row);
    });
    const totals = state.totals_by_player || {};
    appendScoreSummary(documentRef, body, "上半区小计", "upper_subtotal", participants, totals);
    appendScoreSummary(documentRef, body, "上半区奖励", "upper_bonus", participants, totals);
    appendScoreSummary(documentRef, body, "重复快艇奖励", "yahtzee_bonus", participants, totals);
    appendScoreSummary(documentRef, body, "总分", "total", participants, totals);
    table.appendChild(body);
    scroller.appendChild(table);
    scoreSection.append(scoreHeading);
    if (jokerNotice) scoreSection.append(jokerNotice);
    scoreSection.append(scroller);
    root.append(rollPanel, scoreSection);
    board.appendChild(root);
  }

  const renderer = {
    glyph: "艇",
    usesStandardMoveConfirmation: false,
    boardLabel: "快艇骰子与计分卡",
    renderBoard,
  };

  window.DuelGameUI.register('yahtzee', renderer);
})();

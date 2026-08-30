(function registerGoGameUI() {
  "use strict";

  const STYLE_ID = "duel-go-styles";
  const STYLE_HREF = "/static/games/go.css?v=0.1.0";
  const SIZE = 19;
  const LETTERS = "ABCDEFGHJKLMNOPQRST";

  function ensureStylesheet() {
    if (!document || !document.head || document.getElementById(STYLE_ID)) return;
    const link = document.createElement("link");
    link.id = STYLE_ID;
    link.rel = "stylesheet";
    link.href = STYLE_HREF;
    document.head.appendChild(link);
  }

  function viewerId(context) {
    return String(
      (context.viewer && context.viewer.player_id)
      || (context.room.viewer && context.room.viewer.player_id)
      || ""
    );
  }

  function playerName(participant) {
    return participant && (
      participant.display_name || participant.player_id
    ) || "玩家";
  }

  function colorLabel(color) {
    return color === "black" ? "黑方" : color === "white" ? "白方" : "未知方";
  }

  function coordinate(row, col) {
    return `${LETTERS[col]}${SIZE - row}`;
  }

  function sameMove(left, right) {
    return Boolean(
      left && right && left.action === right.action
      && (left.row === undefined || left.row === right.row)
      && (left.col === undefined || left.col === right.col)
    );
  }

  function keyFor(action, row, col) {
    return `${action}:${row === undefined ? "" : row}:${col === undefined ? "" : col}`;
  }

  function legalActionMap(context) {
    const actions = Array.isArray(context.legalActions)
      ? context.legalActions
      : [];
    return new Map(actions.map((action) => [
      keyFor(action.action, action.row, action.col), action,
    ]));
  }

  function isPrecisionAssistDevice() {
    return Boolean(
      typeof window.matchMedia === "function"
      && window.matchMedia("(max-width: 480px), (pointer: coarse)").matches
    );
  }

  function visualPoint(row, col, rotated) {
    return rotated
      ? {row: SIZE - 1 - row, col: SIZE - 1 - col}
      : {row, col};
  }

  function createPlayerCard(context, participant, position) {
    const state = context.state;
    const card = document.createElement("article");
    const isViewer = participant && participant.player_id === viewerId(context);
    const isCurrent = participant
      && participant.player_id === context.room.current_player_id;
    const color = participant && participant.token;
    card.className = [
      "go-player-card",
      position,
      `seat-${participant ? participant.seat_index : 0}`,
      isViewer ? "viewer" : "",
      isCurrent ? "current" : "",
    ].filter(Boolean).join(" ");
    if (participant) card.dataset.playerId = participant.player_id;

    const avatar = document.createElement("span");
    avatar.className = "go-player-avatar";
    if (participant && context.helpers.renderParticipantAvatar) {
      context.helpers.renderParticipantAvatar(avatar, participant);
    } else {
      avatar.textContent = Array.from(playerName(participant))[0] || "?";
    }
    const copy = document.createElement("span");
    copy.className = "go-player-copy";
    const name = document.createElement("strong");
    name.textContent = `${playerName(participant)}${isViewer ? "（你）" : ""}`;
    const detail = document.createElement("small");
    const captures = Number(state.captures && state.captures[color]) || 0;
    detail.textContent = `${colorLabel(color)} · 提子 ${captures}`;
    const turn = document.createElement("span");
    turn.className = "go-player-turn";
    turn.textContent = isCurrent ? "▶ 当前行动" : "等待";
    copy.append(name, detail);
    card.append(avatar, copy, turn);
    return card;
  }

  function createStone(value, dead, last) {
    const stone = document.createElement("span");
    stone.className = [
      "go-stone",
      value,
      dead ? "dead" : "",
      last ? "last" : "",
    ].filter(Boolean).join(" ");
    stone.setAttribute("aria-hidden", "true");
    return stone;
  }

  function createBoardPoint(context, row, col, visual, legalMap, deadSet) {
    const state = context.state;
    const value = state.board[row][col];
    const actionName = state.phase === "scoring" ? "toggle_dead" : "play";
    const action = legalMap.get(keyFor(actionName, row, col));
    const button = document.createElement("button");
    button.type = "button";
    button.className = "go-point";
    button.dataset.row = String(row);
    button.dataset.col = String(col);
    button.dataset.coordinate = coordinate(row, col);
    button.style.setProperty("--go-row", visual.row);
    button.style.setProperty("--go-col", visual.col);
    button.disabled = !context.canMove || !action;
    button.setAttribute(
      "aria-label",
      `${coordinate(row, col)}，${value ? colorLabel(value) + "棋子" : "空点"}`
      + `${deadSet.has(`${row},${col}`) ? "，已标为死子" : ""}`
      + `${action ? `，可${actionName === "play" ? "落子" : "切换死子"}` : ""}`
    );
    const last = state.last_move && state.last_move.action === "play"
      && state.last_move.row === row && state.last_move.col === col;
    if (value) button.appendChild(
      createStone(value, deadSet.has(`${row},${col}`), last)
    );
    if (state.ko_point && state.ko_point.row === row && state.ko_point.col === col) {
      button.classList.add("ko-point");
    }
    if (sameMove(context.pendingMove, action)) button.classList.add("selected");
    if (action) {
      button.classList.add("legal");
      button.addEventListener("click", () => {
        if (action.action === "play" && isPrecisionAssistDevice()) {
          context.uiState.focusPoint = {row, col};
          context.helpers.rerender();
          return;
        }
        context.helpers.selectMove(action);
      });
    }
    return button;
  }

  function createHoshi(row, col, rotated) {
    const visual = visualPoint(row, col, rotated);
    const hoshi = document.createElement("span");
    hoshi.className = "go-hoshi";
    hoshi.style.setProperty("--go-row", visual.row);
    hoshi.style.setProperty("--go-col", visual.col);
    hoshi.setAttribute("aria-hidden", "true");
    return hoshi;
  }

  function loupeCell(context, row, col, legalMap, deadSet) {
    if (row < 0 || row >= SIZE || col < 0 || col >= SIZE) {
      const outside = document.createElement("span");
      outside.className = "go-loupe-cell outside";
      return outside;
    }
    const value = context.state.board[row][col];
    const action = legalMap.get(keyFor("play", row, col));
    const button = document.createElement("button");
    button.type = "button";
    button.className = "go-loupe-cell";
    button.dataset.row = String(row);
    button.dataset.col = String(col);
    button.disabled = !context.canMove || !action;
    button.setAttribute("aria-label", `${coordinate(row, col)}${action ? "，选择此处" : "，不可落子"}`);
    if (value) button.appendChild(
      createStone(value, deadSet.has(`${row},${col}`), false)
    );
    const label = document.createElement("small");
    label.textContent = coordinate(row, col);
    button.appendChild(label);
    if (action) button.addEventListener("click", () => context.helpers.selectMove(action));
    return button;
  }

  function appendPrecisionLoupe(context, shell, legalMap, deadSet) {
    const focus = context.uiState.focusPoint;
    if (!focus || context.state.phase !== "play") return;
    const panel = document.createElement("section");
    panel.className = "go-precision-panel";
    panel.setAttribute("aria-label", "放大选点器");
    const header = document.createElement("div");
    header.className = "go-precision-head";
    const copy = document.createElement("strong");
    copy.textContent = `放大选点 · 中心 ${coordinate(focus.row, focus.col)}`;
    const close = document.createElement("button");
    close.type = "button";
    close.textContent = "关闭";
    close.addEventListener("click", () => {
      delete context.uiState.focusPoint;
      context.helpers.rerender();
    });
    header.append(copy, close);
    const grid = document.createElement("div");
    grid.className = "go-loupe-grid";
    for (let row = focus.row - 2; row <= focus.row + 2; row += 1) {
      for (let col = focus.col - 2; col <= focus.col + 2; col += 1) {
        const cell = loupeCell(context, row, col, legalMap, deadSet);
        if (row === focus.row && col === focus.col) cell.classList.add("focus");
        grid.appendChild(cell);
      }
    }
    const hint = document.createElement("p");
    hint.textContent = "点放大格精确选择，再用下方确认按钮落子。";
    panel.append(header, grid, hint);
    shell.appendChild(panel);
  }

  function renderBoard(context) {
    ensureStylesheet();
    const {board, state} = context;
    if (!Array.isArray(state.board) || state.board.length !== SIZE) return false;
    context.helpers.setBoardLayout({
      rows: SIZE,
      cols: SIZE,
      visualRows: SIZE,
      visualCols: SIZE,
      large: true,
      ariaLabel: "十九路围棋棋盘",
    });
    board.classList.add("go");
    const viewer = (context.participants || []).find(
      (participant) => participant.player_id === viewerId(context)
    ) || null;
    const opponent = (context.participants || []).find(
      (participant) => !viewer || participant.player_id !== viewer.player_id
    ) || null;
    const rotated = Boolean(viewer && viewer.token === "white");
    const legalMap = legalActionMap(context);
    const deadSet = new Set(
      (state.dead_stones || []).map((point) => `${point.row},${point.col}`)
    );
    board.dataset.viewerColor = viewer ? viewer.token : "";
    board.dataset.rotationDegrees = rotated ? "180" : "0";
    board.dataset.phase = state.phase || "play";

    const shell = document.createElement("div");
    shell.className = "go-shell";
    shell.appendChild(createPlayerCard(context, opponent, "top"));
    const surface = document.createElement("div");
    surface.className = "go-board-surface";
    surface.setAttribute("role", "group");
    surface.setAttribute("aria-label", `十九路棋盘，${rotated ? "白方" : "黑方"}视角`);
    [3, 9, 15].forEach((row) => {
      [3, 9, 15].forEach((col) => surface.appendChild(createHoshi(row, col, rotated)));
    });
    for (let row = 0; row < SIZE; row += 1) {
      for (let col = 0; col < SIZE; col += 1) {
        surface.appendChild(createBoardPoint(
          context, row, col, visualPoint(row, col, rotated), legalMap, deadSet
        ));
      }
    }
    shell.appendChild(surface);
    shell.appendChild(createPlayerCard(context, viewer, "bottom"));
    appendPrecisionLoupe(context, shell, legalMap, deadSet);
    board.appendChild(shell);
    return true;
  }

  function controlButton(label, action, context, extraClass = "") {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `pixel-btn compact ${extraClass}`.trim();
    button.textContent = label;
    button.disabled = !context.canMove || !action;
    if (action) button.addEventListener("click", () => context.helpers.selectMove(action));
    if (sameMove(context.pendingMove, action)) button.classList.add("selected-action");
    return button;
  }

  function renderControls(context) {
    const {controls, state} = context;
    const legalMap = legalActionMap(context);
    const status = document.createElement("div");
    status.className = "go-phase-status";
    const title = document.createElement("strong");
    title.textContent = state.phase === "scoring" ? "死子双方确认" : "行棋阶段";
    const detail = document.createElement("small");
    if (state.phase === "scoring") {
      const confirmed = Array.isArray(state.confirmed_player_ids)
        ? state.confirmed_player_ids.length : 0;
      const score = state.score_preview || {};
      detail.textContent = `已确认 ${confirmed}/2 · 暂计 黑 ${score.black ?? "–"} / 白 ${score.white ?? "–"}`;
    } else {
      detail.textContent = `第 ${Number(state.move_number) + 1} 手 · ${colorLabel(state.to_play)}行动`;
    }
    status.append(title, detail);
    controls.appendChild(status);
    if (state.phase === "play") {
      controls.appendChild(controlButton(
        "Pass",
        legalMap.get(keyFor("pass")),
        context,
        "secondary",
      ));
    } else if (state.phase === "scoring") {
      controls.appendChild(controlButton(
        "确认当前死子与计分",
        legalMap.get(keyFor("confirm_score")),
        context,
      ));
      const hint = document.createElement("p");
      hint.className = "go-scoring-hint";
      hint.textContent = "点棋子可切换 Tenuki 选定的整组死子；任何修改都会要求双方重新确认。";
      controls.appendChild(hint);
    }
  }

  const renderer = {
    participantPresentation: "embedded",
    ownsPrivateStatePresentation: true,
    usesStandardMoveConfirmation: true,
    renderBoard,
    renderControls,
  };

  window.DuelGameUI.register("go", renderer);
}());

(function registerJunqiRenderer() {
  "use strict";

  const STYLE_ID = "duel-game-junqi-styles";
  const STYLE_HREF = "/static/games/junqi.css?v=0.1.2";
  const RANK_NAMES = {
    0: "炸弹", 1: "司令", 2: "军长", 3: "师长", 4: "旅长", 5: "团长",
    6: "营长", 7: "连长", 8: "排长", 9: "工兵", 10: "地雷", 11: "军旗",
  };
  const CAMP_NAMES = {b: "蓝方", r: "红方"};

  function ensureStylesheet(documentRef = document) {
    if (!documentRef || !documentRef.head) return null;
    if (typeof documentRef.getElementById === "function") {
      const existing = documentRef.getElementById(STYLE_ID);
      if (existing) return existing;
    }
    const link = documentRef.createElement("link");
    link.id = STYLE_ID;
    link.rel = "stylesheet";
    link.href = STYLE_HREF;
    link.dataset.duelGameStyle = "junqi";
    documentRef.head.appendChild(link);
    return link;
  }

  function node(documentRef, tag, className, text = null) {
    const item = documentRef.createElement(tag);
    if (className) item.className = className;
    if (text !== null) item.textContent = String(text);
    return item;
  }

  function viewerId(context) {
    return String(
      (context.viewer && context.viewer.player_id)
      || (context.room.viewer && context.room.viewer.player_id)
      || ""
    );
  }

  function participantForColor(context, color) {
    const colorByPlayer = context.state.color_by_player || {};
    return (context.participants || []).find(
      (participant) => colorByPlayer[participant.player_id] === color
    ) || null;
  }

  function participantName(participant) {
    return participant && (participant.display_name || participant.player_id) || "玩家";
  }

  function isTerminalState(context) {
    return Boolean(
      context.isTerminal
      || (context.state || {}).phase === "finished"
      || ["finished", "archived"].includes((context.room || {}).status)
    );
  }

  function statusFor(context, participant) {
    if (!participant) return "等待加入";
    const playerId = participant.player_id;
    const state = context.state;
    if (isTerminalState(context)) {
      return state.winner_player_id === playerId ? "已获胜" : "对局结束";
    }
    if (state.phase === "setup") {
      if ((state.setup_ready || {})[playerId]) return "布阵已锁定";
      return state.active_player_id === playerId ? "正在布阵" : "等待布阵";
    }
    if (state.winner_player_id === playerId) return "已获胜";
    if (state.phase === "finished") return "对局结束";
    return state.active_player_id === playerId ? "正在行动" : "等待对方";
  }

  function seatPanel(context, participant, color, position) {
    const documentRef = context.board.ownerDocument || document;
    const item = node(
      documentRef,
      "article",
      `junqi-player-strip position-${position} color-${color}`
    );
    const current = participant
      && !isTerminalState(context)
      && context.state.active_player_id === participant.player_id;
    const mine = participant && viewerId(context) === participant.player_id;
    item.classList.toggle("current", Boolean(current));
    item.classList.toggle("viewer", Boolean(mine));
    if (participant) item.dataset.playerId = participant.player_id;

    const avatar = node(documentRef, "span", "junqi-player-avatar");
    if (participant) context.helpers.renderParticipantAvatar(avatar, participant);
    const copy = node(documentRef, "span", "junqi-player-copy");
    const title = node(documentRef, "span", "junqi-player-title");
    title.append(
      node(documentRef, "strong", "junqi-player-name", participantName(participant)),
      node(documentRef, "i", "junqi-camp-chip", CAMP_NAMES[color])
    );
    copy.append(
      title,
      node(
        documentRef,
        "small",
        "junqi-player-status",
        `${statusFor(context, participant)}${mine ? " · 你" : ""}`
      )
    );
    item.append(avatar, copy);
    item.setAttribute(
      "aria-label",
      `${participantName(participant)}，${CAMP_NAMES[color]}，${statusFor(context, participant)}`
    );
    return item;
  }

  function visualOrder(viewerColor) {
    const redViewer = viewerColor === "r";
    return {
      rows: redViewer
        ? Array.from({length: 12}, (_, index) => index + 1)
        : Array.from({length: 12}, (_, index) => 12 - index),
      cols: redViewer ? ["e", "d", "c", "b", "a"] : ["a", "b", "c", "d", "e"],
      rotation: redViewer ? 180 : 0,
    };
  }

  function selectedOrigin(context) {
    if (isTerminalState(context)) {
      delete context.uiState.selectedSquare;
      return "";
    }
    return String(
      context.uiState.selectedSquare
      || (context.pendingMove && context.pendingMove.from)
      || ""
    );
  }

  function actionForTarget(actions, origin, square) {
    return actions.find((action) => (
      action.from === origin && action.to === square
      && (action.action === "swap" || action.action === "move")
    )) || null;
  }

  function chooseOrigin(context, square) {
    const wasSelected = selectedOrigin(context) === square;
    context.helpers.clearSelection({render: false});
    if (!wasSelected) context.uiState.selectedSquare = square;
    context.helpers.rerender();
  }

  function pieceNode(documentRef, color, rank, hidden) {
    const piece = node(
      documentRef,
      "span",
      `junqi-piece color-${color}${hidden ? " is-hidden" : " is-known"}`
    );
    const face = node(
      documentRef,
      "span",
      "junqi-piece-face",
      hidden ? "军" : RANK_NAMES[rank]
    );
    face.setAttribute("aria-hidden", "true");
    piece.appendChild(face);
    return piece;
  }

  function renderSquare(context, field, square, coordinates, sets, actions) {
    const documentRef = context.board.ownerDocument || document;
    const publicPiece = context.state.board && context.state.board[square];
    const ownPieces = context.privateState && context.privateState.pieces || {};
    const hasPrivateRank = Object.prototype.hasOwnProperty.call(ownPieces, square);
    const rank = hasPrivateRank
      ? Number(ownPieces[square])
      : (publicPiece && Number.isInteger(publicPiece.rank) ? Number(publicPiece.rank) : null);
    const hidden = Boolean(publicPiece && rank === null);
    const color = publicPiece && publicPiece.color;
    const origin = selectedOrigin(context);
    const originActions = actions.filter((action) => action.from === square);
    const targetAction = origin ? actionForTarget(actions, origin, square) : null;
    const lastAction = context.state.last_action || {};
    const selected = origin === square;
    const pendingTarget = Boolean(
      !isTerminalState(context)
      && context.pendingMove
      && context.pendingMove.to === square
    );
    const interactive = Boolean(context.canMove && (originActions.length || targetAction));
    const cell = node(documentRef, "button", "junqi-square");
    cell.type = "button";
    cell.dataset.square = square;
    cell.dataset.logicalRow = String(coordinates.row);
    cell.dataset.logicalCol = coordinates.col;
    cell.classList.toggle("is-bunker", sets.bunkers.has(square));
    cell.classList.toggle("is-headquarters", sets.headquarters.has(square));
    cell.classList.toggle("is-rail", sets.rails.has(square));
    cell.classList.toggle("occupied", Boolean(publicPiece));
    cell.classList.toggle("selected-origin", selected);
    cell.classList.toggle("selected-target", pendingTarget);
    cell.classList.toggle("legal-target", Boolean(targetAction));
    cell.classList.toggle("legal-capture", Boolean(targetAction && publicPiece));
    cell.classList.toggle("last-action-origin", lastAction.from === square);
    cell.classList.toggle("last-action-target", lastAction.to === square);
    cell.disabled = !interactive;
    cell.setAttribute("aria-pressed", String(selected || pendingTarget));

    const coordinate = node(documentRef, "span", "junqi-coordinate", square.toUpperCase());
    coordinate.setAttribute("aria-hidden", "true");
    cell.appendChild(coordinate);
    if (publicPiece) cell.appendChild(pieceNode(documentRef, color, rank, hidden));
    if (!publicPiece && sets.bunkers.has(square)) {
      const marker = node(documentRef, "span", "junqi-terrain-label", "营");
      marker.setAttribute("aria-hidden", "true");
      cell.appendChild(marker);
    } else if (!publicPiece && sets.headquarters.has(square)) {
      const marker = node(documentRef, "span", "junqi-terrain-label", "本");
      marker.setAttribute("aria-hidden", "true");
      cell.appendChild(marker);
    }
    if (targetAction) {
      const marker = node(
        documentRef,
        "span",
        `junqi-legal-marker ${publicPiece ? "capture" : "move"}`
      );
      marker.setAttribute("aria-hidden", "true");
      cell.appendChild(marker);
    }

    const identity = !publicPiece
      ? "空位"
      : hidden ? "对手暗子，身份未公开" : `${CAMP_NAMES[color]}${RANK_NAMES[rank]}`;
    cell.ariaLabel = (
      `${square.toUpperCase()}，${identity}`
      + `${sets.bunkers.has(square) ? "，行营" : ""}`
      + `${sets.headquarters.has(square) ? "，大本营" : ""}`
      + `${sets.rails.has(square) ? "，铁路站" : "，公路站"}`
      + `${selected ? "，已选中" : ""}`
      + `${targetAction ? (publicPiece ? "，合法碰撞目标" : "，合法移动目标") : ""}`
    );
    cell.addEventListener("click", () => {
      if (!context.canMove) return;
      if (targetAction) {
        context.helpers.selectMove(targetAction);
      } else if (originActions.length) {
        chooseOrigin(context, square);
      }
    });
    field.appendChild(cell);
  }

  function renderBoard(context) {
    ensureStylesheet(context.board.ownerDocument || document);
    const {board, state} = context;
    const playerId = viewerId(context);
    const viewerColor = (state.color_by_player || {})[playerId] || "b";
    const order = visualOrder(viewerColor);
    const opponentColor = viewerColor === "b" ? "r" : "b";
    const legal = !isTerminalState(context) && Array.isArray(context.legalActions)
      ? context.legalActions.filter((action) => (
        action && (action.action === "swap" || action.action === "move")
      ))
      : [];
    const sets = {
      bunkers: new Set(state.bunkers || []),
      headquarters: new Set(state.headquarters || []),
      rails: new Set((state.rail_lines || []).flat()),
    };
    context.helpers.setBoardLayout({
      rows: 12,
      cols: 5,
      visualRows: 12,
      visualCols: 5,
      large: true,
      ariaLabel: `标准双人暗棋陆战棋棋盘，${CAMP_NAMES[viewerColor]}视角`,
    });
    board.dataset.viewerColor = viewerColor;
    board.dataset.rotation = String(order.rotation);
    board.classList.add(`viewer-${viewerColor}`);

    const topPlayer = participantForColor(context, opponentColor);
    const bottomPlayer = participantForColor(context, viewerColor);
    board.appendChild(seatPanel(context, topPlayer, opponentColor, "top"));

    const field = node(context.board.ownerDocument || document, "div", "junqi-field");
    field.dataset.rotation = String(order.rotation);
    order.rows.forEach((row) => {
      order.cols.forEach((col) => {
        renderSquare(context, field, `${col}${row}`, {row, col}, sets, legal);
      });
    });
    board.appendChild(field);
    board.appendChild(seatPanel(context, bottomPlayer, viewerColor, "bottom"));
    return true;
  }

  function setupControl(context, action, label, className = "") {
    const documentRef = context.controls.ownerDocument || document;
    const button = node(documentRef, "button", `pixel-btn junqi-control ${className}`, label);
    button.type = "button";
    button.disabled = !context.canMove || !context.legalActions.some(
      (candidate) => candidate.action === action
    );
    button.classList.toggle(
      "selected",
      Boolean(context.pendingMove && context.pendingMove.action === action)
    );
    button.addEventListener("click", () => context.helpers.selectMove({action}));
    return button;
  }

  function renderControls(context) {
    ensureStylesheet(context.controls.ownerDocument || document);
    const documentRef = context.controls.ownerDocument || document;
    const wrap = node(documentRef, "div", "junqi-controls");
    if (isTerminalState(context)) {
      delete context.uiState.selectedSquare;
      wrap.appendChild(node(
        documentRef,
        "p",
        "junqi-battle-result terminal",
        "本局已结束 · 棋盘仅供复盘"
      ));
      context.controls.appendChild(wrap);
      return;
    }
    if (context.state.phase === "setup") {
      const hint = node(
        documentRef,
        "p",
        "junqi-setup-hint",
        context.canMove
          ? "点两枚己方棋子换位；锁定前可随机重排。"
          : "对方正在秘密布阵。你的阵形不会向对手公开。"
      );
      const actions = node(documentRef, "div", "junqi-setup-actions");
      actions.append(
        setupControl(context, "shuffle", "随机合法布阵"),
        setupControl(context, "auto_setup", "自动布阵并确认"),
        setupControl(context, "ready", "锁定当前布阵", "primary")
      );
      wrap.append(hint, actions);
    } else {
      const legend = node(documentRef, "div", "junqi-legend");
      legend.append(
        node(documentRef, "span", "road", "公路一步"),
        node(documentRef, "span", "rail", "铁路直行"),
        node(documentRef, "span", "engineer", "工兵可转弯")
      );
      wrap.appendChild(legend);
      const battle = context.state.last_battle;
      if (battle) {
        const outcome = {
          capture: "进攻方胜",
          dies: "防守方胜",
          equal: "同归于尽",
        }[battle.result] || "已裁决";
        wrap.appendChild(node(
          documentRef,
          "p",
          "junqi-battle-result",
          `最近碰撞：${battle.attacker_name} 对 ${battle.defender_name} · ${outcome}`
        ));
      }
    }
    context.controls.appendChild(wrap);
  }

  const renderer = {
    participantPresentation: "embedded",
    ownsPrivateStatePresentation: true,
    usesStandardMoveConfirmation: true,
    ensureStylesheet,
    visualOrder,
    renderBoard,
    renderControls,
  };
  if (window.DuelGameUI && typeof window.DuelGameUI.register === "function") {
    window.DuelGameUI.register("junqi", renderer);
  } else {
    window.DuelGameUIPending = window.DuelGameUIPending || [];
    window.DuelGameUIPending.push(["junqi", renderer]);
  }
}());

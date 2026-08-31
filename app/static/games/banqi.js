(function registerBanqiRenderer() {
  "use strict";

  const SYMBOLS = {
    r: {k: "帅", a: "仕", b: "相", r: "车", n: "马", c: "炮", p: "兵"},
    b: {k: "将", a: "士", b: "象", r: "车", n: "马", c: "炮", p: "卒"},
  };
  let selectedOrigin = null;
  let renderedPositionKey = null;

  const sameSquare = (left, row, col) => Boolean(
    left && left.row === row && left.col === col
  );

  function actionTargets(actions, origin) {
    if (!origin) return [];
    return actions.filter((action) => (
      action.action === "move"
      && action.from_row === origin.row
      && action.from_col === origin.col
    ));
  }

  function renderBoard(context) {
    const {
      board, state, room, canMove, pendingMove, helpers,
    } = context;
    const setPendingMove = context.setPendingMove || helpers.selectMove;
    const clearPendingMove = context.clearPendingMove || helpers.clearSelection;
    helpers?.setBoardLayout({
      rows: state.rows || state.board.length,
      cols: state.cols || state.board[0]?.length,
      large: true,
      ariaLabel: "翻翻棋棋盘",
    });
    const positionKey = `${room.room_id}:${room.revision}`;
    if (positionKey !== renderedPositionKey) {
      renderedPositionKey = positionKey;
      selectedOrigin = null;
    }

    const legalActions = Array.isArray(state.legal_actions) ? state.legal_actions : [];
    const flipActions = new Map(
      legalActions
        .filter((action) => action.action === "flip")
        .map((action) => [`${action.row},${action.col}`, action])
    );
    const moveActions = legalActions.filter((action) => action.action === "move");
    const selectedTargets = actionTargets(moveActions, selectedOrigin);
    const viewerId = room.viewer && room.viewer.player_id;
    const ownColor = viewerId && state.color_by_player
      ? state.color_by_player[viewerId]
      : null;
    const lastAction = state.last_action && typeof state.last_action === "object"
      ? state.last_action
      : null;

    state.board.forEach((rowData, rowIndex) => {
      rowData.forEach((value, colIndex) => {
        const key = `${rowIndex},${colIndex}`;
        const hidden = value === "hidden";
        const revealed = typeof value === "string" && value !== "hidden";
        const [pieceColor, pieceKind] = revealed ? value.split(":") : [null, null];
        const ownPiece = Boolean(ownColor && pieceColor === ownColor);
        const originMoves = moveActions.filter((action) => (
          action.from_row === rowIndex && action.from_col === colIndex
        ));
        const targetAction = selectedTargets.find((action) => (
          action.to_row === rowIndex && action.to_col === colIndex
        ));
        const pendingTarget = Boolean(
          pendingMove
          && (
            (pendingMove.action === "flip"
              && pendingMove.row === rowIndex && pendingMove.col === colIndex)
            || (pendingMove.action === "move"
              && pendingMove.to_row === rowIndex && pendingMove.to_col === colIndex)
          )
        );
        const selected = sameSquare(selectedOrigin, rowIndex, colIndex);
        const justRevealed = Boolean(
          lastAction && lastAction.action === "flip"
          && lastAction.row === rowIndex && lastAction.col === colIndex
        );
        const lastOrigin = Boolean(
          lastAction && lastAction.action === "move"
          && lastAction.from_row === rowIndex && lastAction.from_col === colIndex
        );
        const lastTarget = Boolean(
          lastAction && lastAction.action === "move"
          && lastAction.to_row === rowIndex && lastAction.to_col === colIndex
        );

        const cell = document.createElement("button");
        cell.type = "button";
        cell.className = "cell banqi-cell";
        cell.dataset.moveRow = String(rowIndex);
        cell.dataset.moveCol = String(colIndex);
        if (value !== null) cell.classList.add("occupied");
        if (hidden) cell.classList.add("has-hidden-piece");
        if (revealed) cell.classList.add("has-revealed-piece", `color-${pieceColor}`);
        if (selected) cell.classList.add("selected-origin");
        if (pendingTarget) cell.classList.add("selected");
        if (targetAction) cell.classList.add(value === null ? "legal-target" : "legal-capture");
        if (justRevealed) cell.classList.add("just-revealed");
        if (lastOrigin) cell.classList.add("last-action-origin");
        if (lastTarget) cell.classList.add("last-action-target");

        if (value !== null) {
          const piece = document.createElement("span");
          piece.className = hidden
            ? "banqi-piece is-hidden"
            : `banqi-piece is-revealed color-${pieceColor}`;
          if (hidden) {
            const backRing = document.createElement("span");
            backRing.className = "banqi-back-ring";
            backRing.setAttribute("aria-hidden", "true");
            const seal = document.createElement("span");
            seal.className = "banqi-back-seal";
            seal.textContent = "弈";
            seal.setAttribute("aria-hidden", "true");
            piece.append(backRing, seal);
          } else {
            const glyph = document.createElement("span");
            glyph.className = "banqi-piece-glyph";
            glyph.textContent = SYMBOLS[pieceColor][pieceKind];
            glyph.setAttribute("aria-hidden", "true");
            piece.appendChild(glyph);
          }
          cell.appendChild(piece);
        }

        if (targetAction) {
          const targetMarker = document.createElement("span");
          targetMarker.className = value === null
            ? "banqi-legal-marker move"
            : "banqi-legal-marker capture";
          targetMarker.setAttribute("aria-hidden", "true");
          cell.appendChild(targetMarker);
        }
        if (lastOrigin) {
          const trail = document.createElement("span");
          trail.className = "banqi-origin-trail";
          trail.setAttribute("aria-hidden", "true");
          cell.appendChild(trail);
        }

        const positionLabel = `第 ${rowIndex + 1} 行第 ${colIndex + 1} 列`;
        const pieceLabel = hidden
          ? "暗子，身份未公开"
          : (revealed ? `${pieceColor === "r" ? "红" : "黑"}${SYMBOLS[pieceColor][pieceKind]}` : "空位");
        cell.ariaLabel = (
          `${positionLabel}，${pieceLabel}`
          + `${selected ? "，已选中" : ""}`
          + `${targetAction ? (value === null ? "，合法移动目标" : "，合法吃子目标") : ""}`
          + `${justRevealed ? "，上一手刚翻开" : ""}`
          + `${lastTarget ? "，上一手终点" : ""}`
        );

        const interactive = Boolean(
          targetAction || flipActions.has(key) || (ownPiece && originMoves.length)
        );
        cell.disabled = !canMove || !interactive;
        cell.addEventListener("click", () => {
          if (!canMove) return;
          if (targetAction) {
            setPendingMove(targetAction);
            return;
          }
          if (hidden && flipActions.has(key)) {
            selectedOrigin = null;
            setPendingMove(flipActions.get(key));
            return;
          }
          if (ownPiece && originMoves.length) {
            selectedOrigin = selected ? null : {row: rowIndex, col: colIndex};
            clearPendingMove();
          }
        });
        board.appendChild(cell);
      });
    });
  }

  window.DuelGameUI.register("banqi", {renderBoard});
}());

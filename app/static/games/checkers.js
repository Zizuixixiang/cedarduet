(function registerCheckersRenderer() {
  "use strict";

  const selections = new Map();

  function boardElement(context) {
    return context.board || context.boardElement || document.getElementById("board");
  }

  function roomState(context) {
    return context.state || (context.room && context.room.board_state) || {};
  }

  function roomKey(context) {
    return String((context.room && context.room.room_id) || context.roomId || "checkers");
  }

  function isTerminalState(context, state = roomState(context)) {
    return Boolean(
      context.isTerminal
      || state.game_over
      || state.winner_mark
      || ["finished", "archived"].includes((context.room || {}).status)
    );
  }

  function viewerMark(context, state) {
    if (context.viewerMark === "X" || context.viewerMark === "O") {
      return context.viewerMark;
    }
    const targetRoom = context.room || {};
    const viewerId = targetRoom.viewer && targetRoom.viewer.player_id;
    const byPlayer = state.marks_by_player || {};
    if (viewerId && ["X", "O"].includes(byPlayer[viewerId])) {
      return byPlayer[viewerId];
    }
    const legacy = state.marks || {};
    return ["X", "O"].includes(legacy.human) ? legacy.human : "X";
  }

  function canMove(context, state, mark) {
    if (typeof context.canHumanMove === "function") return context.canHumanMove();
    if (typeof context.canMove === "function") return context.canMove();
    if (typeof context.canMove === "boolean") return context.canMove;
    const targetRoom = context.room || {};
    const viewerId = targetRoom.viewer && targetRoom.viewer.player_id;
    return Boolean(
      targetRoom.status === "playing"
      && viewerId
      && targetRoom.current_player_id === viewerId
      && state.turn_mark === mark
    );
  }

  function pendingMove(context) {
    return typeof context.getPendingMove === "function"
      ? context.getPendingMove()
      : (context.pendingMove || null);
  }

  function sameSquare(square, row, col) {
    return Boolean(square && square.row === row && square.col === col);
  }

  function moveIsCapture(move) {
    return Math.abs(move.to_row - move.from_row) === 2;
  }

  function selectedOrigin(context, state) {
    if (isTerminalState(context, state)) {
      selections.delete(roomKey(context));
      return null;
    }
    if (state.forced_piece) {
      return {row: state.forced_piece.row, col: state.forced_piece.col};
    }
    const pending = pendingMove(context);
    if (pending) return {row: pending.from_row, col: pending.from_col};
    const saved = selections.get(roomKey(context));
    const revision = context.room && context.room.revision;
    if (!saved || (Number.isInteger(revision) && saved.revision !== revision)) {
      selections.delete(roomKey(context));
      return null;
    }
    return {row: saved.row, col: saved.col};
  }

  function rememberOrigin(context, row, col) {
    selections.set(roomKey(context), {
      row,
      col,
      revision: context.room && context.room.revision,
    });
  }

  function clearPending(context) {
    if (typeof context.clearPendingMove === "function") {
      context.clearPendingMove();
    } else if (typeof context.setPendingMove === "function") {
      context.setPendingMove(null);
    }
  }

  function requestRender(context) {
    if (typeof context.rerender === "function") {
      context.rerender();
    } else if (typeof context.requestRender === "function") {
      context.requestRender();
    } else {
      renderBoard(context);
    }
  }

  function chooseMove(context, move) {
    if (typeof context.selectMove === "function") {
      context.selectMove({...move});
      return;
    }
    if (typeof context.onSelectMove === "function") {
      context.onSelectMove({...move});
      return;
    }
    if (typeof context.setPendingMove === "function") {
      context.setPendingMove({...move});
      requestRender(context);
    }
  }

  function pieceLabel(value, mark) {
    if (!value) return "空位";
    const [owner, kind] = value.split(":");
    const side = owner === mark ? "己方" : "对方";
    return `${side}${kind === "k" ? "王棋" : "普通棋"}`;
  }

  function renderBoard(context) {
    const state = roomState(context);
    const board = boardElement(context);
    if (!board || !Array.isArray(state.board) || state.board.length !== 8) return false;

    const mark = viewerMark(context, state);
    const terminal = isTerminalState(context, state);
    const movable = !terminal && canMove(context, state, mark);
    const legalMoves = !terminal && Array.isArray(state.legal_moves)
      ? state.legal_moves
      : [];
    const origin = selectedOrigin(context, state);
    const originMoves = origin
      ? legalMoves.filter((move) => (
        move.from_row === origin.row && move.from_col === origin.col
      ))
      : [];
    const legalOrigins = new Set(
      legalMoves.map((move) => `${move.from_row},${move.from_col}`)
    );
    const currentPending = terminal ? null : pendingMove(context);
    const lastMove = state.last_move || {};
    const rotated = mark === "O";
    const rowOrder = Array.from({length: 8}, (_, index) => rotated ? 7 - index : index);
    const colOrder = Array.from({length: 8}, (_, index) => rotated ? 7 - index : index);

    board.replaceChildren();
    board.className = "board checkers";
    board.style.setProperty("--cols", 8);
    board.style.setProperty("--rows", 8);
    board.style.setProperty("--board-ratio", "1 / 1");
    board.dataset.viewMark = mark;
    board.dataset.mustCapture = String(Boolean(!terminal && state.must_capture));
    board.dataset.forced = String(Boolean(!terminal && state.forced_piece));
    board.setAttribute(
      "aria-label",
      `西洋跳棋 8乘8棋盘，${rotated ? "O方" : "X方"}视角`
      + `${!terminal && state.forced_piece ? "，必须继续使用同一枚棋吃子" : ""}`
    );

    rowOrder.forEach((rowIndex, displayRow) => {
      colOrder.forEach((colIndex, displayCol) => {
        const value = state.board[rowIndex][colIndex];
        const [owner, kind] = value ? value.split(":") : [null, null];
        const dark = (rowIndex + colIndex) % 2 === 1;
        const key = `${rowIndex},${colIndex}`;
        const selected = sameSquare(origin, rowIndex, colIndex);
        const targetMove = originMoves.find((move) => (
          move.to_row === rowIndex && move.to_col === colIndex
        ));
        const isPendingTarget = Boolean(
          currentPending
          && currentPending.to_row === rowIndex
          && currentPending.to_col === colIndex
        );
        const isLastFrom = (
          lastMove.from_row === rowIndex && lastMove.from_col === colIndex
        );
        const isLastTo = lastMove.to_row === rowIndex && lastMove.to_col === colIndex;
        const selectableOrigin = movable && legalOrigins.has(key) && owner === mark;
        const selectableTarget = movable && Boolean(targetMove);

        const cell = document.createElement("button");
        cell.type = "button";
        cell.className = `checkers-cell ${dark ? "dark-square" : "light-square"}`;
        cell.dataset.moveRow = String(rowIndex);
        cell.dataset.moveCol = String(colIndex);
        cell.dataset.displayRow = String(displayRow);
        cell.dataset.displayCol = String(displayCol);
        if (value) cell.classList.add("occupied", `owner-${owner.toLowerCase()}`);
        if (selected) cell.classList.add("selected-origin");
        if (targetMove) {
          cell.classList.add(
            "legal-target",
            moveIsCapture(targetMove) ? "capture-target" : "step-target"
          );
        }
        if (isPendingTarget) cell.classList.add("pending-target");
        if (isLastFrom) cell.classList.add("last-move-from");
        if (isLastTo) cell.classList.add("last-move-to");
        cell.disabled = !dark || (!selectableOrigin && !selectableTarget);
        cell.setAttribute("aria-pressed", String(selected || isPendingTarget));
        cell.setAttribute(
          "aria-label",
          `己方视角第${displayRow + 1}行第${displayCol + 1}列，`
          + `${dark ? "深色格" : "浅色格"}，${pieceLabel(value, mark)}`
          + `${targetMove ? "，服务端合法落点" : ""}`
          + `${isLastFrom ? "，上一手起点" : ""}`
          + `${isLastTo ? "，上一手终点" : ""}`
        );

        if (targetMove) {
          const target = document.createElement("span");
          target.className = "checkers-legal-marker";
          target.setAttribute("aria-hidden", "true");
          cell.appendChild(target);
        }
        if (value) {
          const piece = document.createElement("span");
          piece.className = `checkers-piece side-${owner.toLowerCase()} ${kind === "k" ? "king" : "man"}`;
          piece.dataset.owner = owner;
          piece.dataset.kind = kind;
          piece.setAttribute("aria-hidden", "true");
          if (kind === "k") {
            const kingMark = document.createElement("span");
            kingMark.className = "checkers-king-mark";
            piece.appendChild(kingMark);
          }
          cell.appendChild(piece);
        }
        if (isLastTo) {
          const marker = document.createElement("span");
          marker.className = "checkers-last-move-marker";
          marker.setAttribute("aria-hidden", "true");
          cell.appendChild(marker);
        }

        cell.addEventListener("click", () => {
          if (!movable) return;
          if (selectableOrigin) {
            if (selected && !state.forced_piece) {
              selections.delete(roomKey(context));
            } else {
              rememberOrigin(context, rowIndex, colIndex);
            }
            clearPending(context);
            requestRender(context);
            return;
          }
          if (targetMove) chooseMove(context, targetMove);
        });
        board.appendChild(cell);
      });
    });

    if (typeof context.setSelectionHint === "function") {
      context.setSelectionHint(
        terminal
          ? "对局已结束，棋盘仅供复盘"
          : state.forced_piece
          ? "必须继续使用已锁定的棋子吃子"
          : (origin ? "请选择亮起的服务端合法落点" : "请选择可行动棋子")
      );
    }
    return true;
  }

  function reset(context = {}) {
    selections.delete(roomKey(context));
  }

  const renderer = {renderBoard, reset};
  if (window.DuelGameUI && typeof window.DuelGameUI.register === "function") {
    window.DuelGameUI.register('checkers', renderer);
  } else {
    window.DuelGameUIPending = window.DuelGameUIPending || [];
    window.DuelGameUIPending.push(["checkers", renderer]);
  }
})();

(function registerChessGameUI() {
  "use strict";

  const SVG_NS = "http://www.w3.org/2000/svg";
  const STYLE_ID = "duel-chess-renderer-styles";
  const PIECE_NAMES = {
    p: "兵", n: "马", b: "象", r: "车", q: "后", k: "王",
  };
  const COLOR_NAMES = {w: "白方", b: "黑方"};
  const PROMOTION_ORDER = ["q", "r", "b", "n"];
  const PIECE_PATHS = {
    p: [
      "M50 14a14 14 0 1 1 0 28 14 14 0 0 1 0-28Z",
      "M37 43h26l7 14-7 10H37l-7-10 7-14Zm-7 27h40l7 13H23l7-13Zm-9 16h58v7H21v-7Z",
    ],
    r: [
      "M22 16h14v10h9V16h10v10h9V16h14v24l-9 8 5 34H26l5-34-9-8V16Z",
      "M29 49h42M27 72h46M22 84h56v9H22Z",
    ],
    n: [
      "M27 82c3-20 7-34 19-46l-8-5 7-15c17 2 30 12 34 28l-9 11-11-5-9 9 20 23H27Z",
      "M45 16 34 30l16 7M58 28c7 1 12 5 16 10M32 83h43v10H25l7-10Z",
    ],
    b: [
      "M50 13c13 8 20 18 20 29 0 10-6 17-13 23l12 16H31l12-16c-8-6-13-13-13-23 0-11 7-21 20-29Z",
      "M56 24 43 45M30 83h40l8 10H22l8-10Z",
    ],
    q: [
      "M20 26 35 42l15-25 15 25 15-16-9 40H29l-9-40Z",
      "M29 69h42l5 13H24l5-13Zm-7 16h56v8H22v-8Z",
      "M18 20a5 5 0 1 1 10 0 5 5 0 0 1-10 0Zm27-7a5 5 0 1 1 10 0 5 5 0 0 1-10 0Zm27 7a5 5 0 1 1 10 0 5 5 0 0 1-10 0Z",
    ],
    k: [
      "M46 9h8v12h11v8H54v10c12 7 18 18 18 30H28c0-12 6-23 18-30V29H35v-8h11V9Z",
      "M29 70h42l6 13H23l6-13Zm-8 16h58v7H21v-7Z",
    ],
  };

  const STYLE_TEXT = `
    .board.chess {
      --chess-light: #ead7b1;
      --chess-dark: #9d6848;
      width: min(66vw, 560px);
      max-width: 100%;
      padding: clamp(5px, 1vw, 8px);
      position: relative;
      isolation: isolate;
      overflow: visible;
      gap: 0;
      box-sizing: border-box;
      background: linear-gradient(145deg, #5a3829, #815039 55%, #4b2e24);
      border: 3px solid #3d261f;
      box-shadow:
        0 0 0 2px #c59a69,
        0 0 0 5px #4b3026,
        8px 10px 20px rgba(52, 32, 27, .24);
      touch-action: manipulation;
    }
    .board.chess .chess-cell {
      isolation: isolate;
      overflow: hidden;
      border: 0;
      outline: 0;
      touch-action: manipulation;
    }
    .board.chess .chess-cell.light-square { background: var(--chess-light); }
    .board.chess .chess-cell.dark-square { background: var(--chess-dark); }
    .board.chess .chess-cell:hover:not(:disabled) {
      box-shadow: inset 0 0 0 3px rgba(255, 247, 220, .58);
      filter: brightness(1.07);
    }
    .board.chess .chess-cell.selected-origin {
      z-index: 3;
      box-shadow:
        inset 0 0 0 4px #684985,
        inset 0 0 18px rgba(255, 255, 255, .28);
    }
    .board.chess .chess-cell.selected-target {
      z-index: 3;
      box-shadow:
        inset 0 0 0 4px #a13750,
        inset 0 0 20px rgba(255, 243, 229, .35);
    }
    .board.chess .chess-cell.last-move-from,
    .board.chess .chess-cell.last-move-to {
      background-image: linear-gradient(rgba(235, 184, 75, .42), rgba(235, 184, 75, .42));
    }
    .board.chess .chess-cell.last-move-to::before {
      content: "";
      width: 9px;
      height: 9px;
      position: absolute;
      right: 4px;
      bottom: 4px;
      z-index: 6;
      border: 2px solid #6b2638;
      border-radius: 50%;
      background: #fff6de;
    }
    .board.chess .chess-piece {
      width: 88%;
      height: 88%;
      z-index: 2;
      overflow: visible;
      pointer-events: none;
      filter: drop-shadow(1px 2px 1px rgba(39, 25, 20, .35));
    }
    .board.chess .chess-piece path {
      vector-effect: non-scaling-stroke;
      stroke-linecap: round;
      stroke-linejoin: round;
      stroke-width: 3;
    }
    .board.chess .chess-piece.color-w path {
      fill: #fff9e9;
      stroke: #665447;
    }
    .board.chess .chess-piece.color-b path {
      fill: #29242a;
      stroke: #100e12;
    }
    .board.chess .legal-target-dot {
      width: 25%;
      aspect-ratio: 1;
      position: absolute;
      left: 50%;
      top: 50%;
      z-index: 4;
      transform: translate(-50%, -50%);
      border-radius: 50%;
      background: rgba(76, 55, 94, .62);
      box-shadow: 0 0 0 3px rgba(255, 248, 230, .42);
      pointer-events: none;
    }
    .board.chess .legal-capture .legal-target-dot {
      width: 82%;
      border: clamp(3px, .55vw, 5px) solid rgba(139, 37, 62, .82);
      background: transparent;
      box-shadow: inset 0 0 0 2px rgba(255, 245, 226, .34);
    }
    .board.chess .chess-cell.in-check {
      background-image: radial-gradient(circle, rgba(190, 39, 51, .72), rgba(127, 22, 36, .25));
      box-shadow: inset 0 0 0 4px #8f1f32;
    }
    .board.chess .chess-check-notice {
      padding: 4px 9px;
      position: absolute;
      left: 50%;
      top: 10px;
      z-index: 12;
      transform: translateX(-50%);
      color: #7f1e2d;
      background: rgba(255, 249, 231, .95);
      border: 2px solid #9f3144;
      box-shadow: 2px 2px 0 rgba(62, 37, 34, .24);
      font-family: system-ui, sans-serif;
      font-size: 11px;
      font-weight: 800;
      letter-spacing: .08em;
      line-height: 1;
      pointer-events: none;
    }
    .board.chess .chess-file-label,
    .board.chess .chess-rank-label {
      position: absolute;
      z-index: 7;
      color: rgba(63, 39, 31, .78);
      font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      font-size: clamp(7px, 1.15vw, 10px);
      font-weight: 800;
      line-height: 1;
      pointer-events: none;
    }
    .board.chess .dark-square .chess-file-label,
    .board.chess .dark-square .chess-rank-label { color: rgba(255, 244, 218, .78); }
    .board.chess .chess-file-label { right: 3px; bottom: 2px; }
    .board.chess .chess-rank-label { left: 3px; top: 2px; }
    .board.chess .chess-promotion-panel {
      width: min(88%, 360px);
      padding: 10px;
      position: absolute;
      left: 50%;
      top: 50%;
      z-index: 20;
      transform: translate(-50%, -50%);
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 7px;
      background: rgba(255, 249, 233, .98);
      border: 3px solid #4b3026;
      box-shadow: 7px 8px 0 rgba(47, 29, 25, .32);
    }
    .board.chess .chess-promotion-title {
      grid-column: 1 / -1;
      margin: 0;
      color: #4c3031;
      font-family: system-ui, sans-serif;
      font-size: 13px;
      font-weight: 800;
      text-align: center;
    }
    .board.chess .chess-promotion-choice {
      min-width: 0;
      padding: 3px;
      aspect-ratio: 1;
      display: grid;
      place-items: center;
      background: #ead7b1;
      border: 2px solid #87563e;
      border-radius: 0;
    }
    .board.chess .chess-promotion-choice:hover,
    .board.chess .chess-promotion-choice:focus-visible {
      outline: 3px solid #9f3144;
      outline-offset: -3px;
      background: #fff0d0;
    }
    .board.chess .chess-promotion-choice .chess-piece { width: 90%; height: 90%; }
    .board.chess .chess-promotion-cancel {
      grid-column: 1 / -1;
      min-height: 32px;
      color: #543a39;
      background: #fff;
      border: 2px solid #8b6c5a;
      font-family: system-ui, sans-serif;
      font-weight: 700;
    }
    .board.chess .chess-cell:focus-visible {
      z-index: 8;
      outline: 3px solid #fff7de;
      outline-offset: -5px;
      box-shadow: inset 0 0 0 5px #6b3f82;
    }
    .game-controls:has(.chess-claim-controls) {
      width: min(560px, 100%);
    }
    .chess-claim-controls {
      padding: 9px 10px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
      color: #4c3031;
      background: #fff9e9;
      border: 3px solid #87563e;
      box-shadow: 4px 5px 0 rgba(52, 32, 27, .2);
      font-family: system-ui, sans-serif;
    }
    .chess-claim-copy {
      min-width: 0;
      font-size: 12px;
      font-weight: 700;
      line-height: 1.45;
    }
    .chess-claim-button {
      min-width: 112px;
      min-height: 42px;
      flex: 0 0 auto;
    }
    @media (max-width: 599px) {
      .board.chess {
        width: min(92vw, 560px);
        padding: 4px;
        border-width: 2px;
        box-shadow: 0 0 0 2px #c59a69, 0 0 0 4px #4b3026;
      }
      .board.chess .chess-cell.selected-origin,
      .board.chess .chess-cell.selected-target,
      .board.chess .chess-cell.in-check { box-shadow: inset 0 0 0 3px #7b365c; }
      .board.chess .legal-capture .legal-target-dot { border-width: 3px; }
      .board.chess .chess-check-notice { top: 7px; padding: 3px 7px; font-size: 10px; }
      .board.chess .chess-promotion-panel { width: 92%; padding: 8px; gap: 5px; }
      .chess-claim-controls {
        width: 100%;
        padding: 8px;
        align-items: stretch;
        flex-direction: column;
        box-sizing: border-box;
      }
      .chess-claim-button { width: 100%; min-height: 44px; }
    }
    @media (prefers-reduced-motion: reduce) {
      .board.chess .chess-cell { scroll-behavior: auto; }
    }
  `;

  function installStyles() {
    if (document.getElementById(STYLE_ID)) return;
    const style = document.createElement("style");
    style.id = STYLE_ID;
    style.textContent = STYLE_TEXT;
    document.head.appendChild(style);
  }

  function movePayload(move) {
    const payload = {
      from_row: move.from_row,
      from_col: move.from_col,
      to_row: move.to_row,
      to_col: move.to_col,
    };
    if (move.promotion) payload.promotion = move.promotion;
    return payload;
  }

  function sameSquare(square, row, col) {
    return Boolean(square && square.row === row && square.col === col);
  }

  function pieceSvg(color, type) {
    const svg = document.createElementNS(SVG_NS, "svg");
    svg.classList.add("chess-piece", `color-${color}`, `piece-${type}`);
    svg.setAttribute("viewBox", "0 0 100 100");
    svg.setAttribute("aria-hidden", "true");
    svg.setAttribute("focusable", "false");
    (PIECE_PATHS[type] || PIECE_PATHS.p).forEach((definition) => {
      const path = document.createElementNS(SVG_NS, "path");
      path.setAttribute("d", definition);
      svg.appendChild(path);
    });
    return svg;
  }

  function squareName(row, col) {
    return `${String.fromCharCode("a".charCodeAt(0) + col)}${8 - row}`;
  }

  function appendPromotionPanel(context, moves, color) {
    const panel = document.createElement("section");
    panel.className = "chess-promotion-panel";
    panel.setAttribute("role", "dialog");
    panel.setAttribute("aria-modal", "true");
    panel.setAttribute("aria-label", "选择兵升变棋子");

    const title = document.createElement("p");
    title.className = "chess-promotion-title";
    title.textContent = "兵升变为";
    panel.appendChild(title);

    PROMOTION_ORDER.forEach((type) => {
      const promotionMove = moves.find((item) => item.promotion === type);
      if (!promotionMove) return;
      const choice = document.createElement("button");
      choice.type = "button";
      choice.className = "chess-promotion-choice";
      choice.setAttribute("aria-label", `升变为${PIECE_NAMES[type]}`);
      choice.appendChild(pieceSvg(color, type));
      choice.addEventListener("click", () => {
        delete context.uiState.promotionMoves;
        delete context.uiState.promotionTarget;
        context.helpers.selectMove(movePayload(promotionMove));
      });
      panel.appendChild(choice);
    });

    const cancel = document.createElement("button");
    cancel.type = "button";
    cancel.className = "chess-promotion-cancel";
    cancel.textContent = "返回棋盘";
    cancel.addEventListener("click", () => {
      delete context.uiState.promotionMoves;
      delete context.uiState.promotionTarget;
      context.helpers.rerender();
    });
    panel.appendChild(cancel);
    context.board.appendChild(panel);
  }

  function renderBoard(context) {
    const {board, state, legalMoves, uiState, helpers} = context;
    helpers.setBoardLayout({
      rows: 8,
      cols: 8,
      visualRows: 8,
      visualCols: 8,
      large: true,
      ariaLabel: "标准八乘八国际象棋棋盘",
    });

    const viewerToken = context.viewer && context.viewer.token;
    const viewerColor = viewerToken === "O" ? "b" : "w";
    const rotated = viewerColor === "b";
    const rowOrder = Array.from(
      {length: 8}, (_, index) => rotated ? 7 - index : index
    );
    const colOrder = Array.from(
      {length: 8}, (_, index) => rotated ? 7 - index : index
    );
    const selected = uiState.selectedSquare || null;
    const lastMove = state.last_move || null;
    const checkedPiece = state.in_check ? `${state.turn_color}:k` : null;

    board.classList.toggle("rotated-view", rotated);
    board.dataset.viewColor = viewerColor;
    rowOrder.forEach((rowIndex, displayRow) => {
      colOrder.forEach((colIndex, displayCol) => {
        const value = state.board[rowIndex][colIndex];
        const [color, type] = value ? value.split(":") : [null, null];
        const ownPiece = color === viewerColor;
        const targets = selected
          ? legalMoves.filter((candidate) => (
              candidate.from_row === selected.row
              && candidate.from_col === selected.col
              && candidate.to_row === rowIndex
              && candidate.to_col === colIndex
            ))
          : [];
        const isTarget = targets.length > 0;
        const isCapture = targets.some((candidate) => Boolean(candidate.captured));
        const cell = document.createElement("button");
        cell.type = "button";
        cell.className = (
          `cell chess-cell ${((rowIndex + colIndex) % 2 === 0) ? "light-square" : "dark-square"}`
          + (value ? " occupied" : "")
        );
        cell.dataset.moveRow = String(rowIndex);
        cell.dataset.moveCol = String(colIndex);
        cell.dataset.square = squareName(rowIndex, colIndex);
        cell.dataset.displayRow = String(displayRow);
        cell.dataset.displayCol = String(displayCol);

        if (sameSquare(selected, rowIndex, colIndex)) {
          cell.classList.add("selected-origin");
        }
        if (
          context.pendingMove
          && context.pendingMove.to_row === rowIndex
          && context.pendingMove.to_col === colIndex
        ) cell.classList.add("selected-target");
        if (
          lastMove
          && lastMove.from_row === rowIndex
          && lastMove.from_col === colIndex
        ) cell.classList.add("last-move-from");
        if (
          lastMove
          && lastMove.to_row === rowIndex
          && lastMove.to_col === colIndex
        ) cell.classList.add("last-move-to");
        if (checkedPiece && value === checkedPiece) cell.classList.add("in-check");
        if (isTarget) {
          cell.classList.add("legal-target");
          if (isCapture) cell.classList.add("legal-capture");
          const target = document.createElement("span");
          target.className = "legal-target-dot";
          target.setAttribute("aria-hidden", "true");
          cell.appendChild(target);
        }
        if (value) cell.appendChild(pieceSvg(color, type));

        if (displayRow === 7) {
          const file = document.createElement("span");
          file.className = "chess-file-label";
          file.textContent = String.fromCharCode("a".charCodeAt(0) + colIndex);
          file.setAttribute("aria-hidden", "true");
          cell.appendChild(file);
        }
        if (displayCol === 0) {
          const rank = document.createElement("span");
          rank.className = "chess-rank-label";
          rank.textContent = String(8 - rowIndex);
          rank.setAttribute("aria-hidden", "true");
          cell.appendChild(rank);
        }

        const descriptions = [squareName(rowIndex, colIndex)];
        descriptions.push(value ? `${COLOR_NAMES[color]}${PIECE_NAMES[type]}` : "空位");
        if (sameSquare(selected, rowIndex, colIndex)) descriptions.push("已选中");
        if (isTarget) descriptions.push(isCapture ? "可吃" : "合法落点");
        if (checkedPiece && value === checkedPiece) descriptions.push("正被将军");
        if (cell.classList.contains("last-move-from")) descriptions.push("上一手起点");
        if (cell.classList.contains("last-move-to")) descriptions.push("上一手终点");
        cell.setAttribute("aria-label", descriptions.join("，"));
        cell.setAttribute(
          "aria-pressed", String(sameSquare(selected, rowIndex, colIndex))
        );
        cell.disabled = !context.canMove || (!ownPiece && !isTarget);
        cell.addEventListener("click", () => {
          if (!helpers.canMove()) return;
          if (ownPiece) {
            if (sameSquare(uiState.selectedSquare, rowIndex, colIndex)) {
              helpers.clearSelection();
              return;
            }
            helpers.clearSelection({render: false});
            uiState.selectedSquare = {row: rowIndex, col: colIndex};
            helpers.rerender();
            return;
          }
          if (!isTarget) return;
          if (targets.length > 1 && targets.every((item) => item.promotion)) {
            uiState.promotionMoves = targets.map((item) => ({...item}));
            uiState.promotionTarget = {row: rowIndex, col: colIndex};
            helpers.rerender();
            return;
          }
          helpers.selectMove(movePayload(targets[0]));
        });
        board.appendChild(cell);
      });
    });

    if (state.in_check) {
      const notice = document.createElement("span");
      notice.className = "chess-check-notice";
      notice.setAttribute("role", "status");
      notice.setAttribute("aria-live", "polite");
      notice.textContent = `${COLOR_NAMES[state.turn_color]} · 将军`;
      board.appendChild(notice);
    }
    if (Array.isArray(uiState.promotionMoves) && uiState.promotionMoves.length) {
      appendPromotionPanel(context, uiState.promotionMoves, viewerColor);
    }
  }

  function renderControls(context) {
    const claimAction = (Array.isArray(context.legalActions)
      ? context.legalActions
      : []
    ).find((action) => action && action.action === "claim_draw");
    if (!claimAction) {
      context.controls.classList.add("hidden");
      return;
    }
    context.controls.classList.toggle("hidden", false);
    const bar = document.createElement("section");
    bar.className = "chess-claim-controls";
    bar.setAttribute("aria-label", "国际象棋申和操作");
    const copy = document.createElement("span");
    copy.className = "chess-claim-copy";
    const reasons = Array.isArray(context.state.claimable_draw_reasons)
      ? context.state.claimable_draw_reasons
      : [];
    const labels = reasons.map((reason) => (
      reason === "threefold_repetition" ? "三次重复" : "50 回合规则"
    ));
    copy.textContent = `${labels.join("、") || "当前局面"}已满足，可申和或继续走棋。`;
    const button = document.createElement("button");
    button.type = "button";
    button.className = "pixel-btn chess-claim-button";
    button.textContent = "申和";
    button.disabled = !context.canMove;
    button.addEventListener("click", async () => {
      if (!context.helpers.canMove()) return;
      button.disabled = true;
      const submitted = await context.helpers.submitMove({...claimAction});
      if (!submitted && context.helpers.canMove()) button.disabled = false;
    });
    bar.appendChild(copy);
    bar.appendChild(button);
    context.controls.appendChild(bar);
  }

  installStyles();
  window.DuelGameUI.register("chess", {
    usesStandardMoveConfirmation: true,
    renderBoard,
    renderControls,
  });
}());

(function registerLiarsDiceReviewRenderer(global) {
  "use strict";

  const STYLE_ID = "duel-game-liars-dice-review-styles";
  const STYLE_HREF = "/static/games/liars_dice.css?v=0.1.0";

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

  function appendTerminalReview(context) {
    const diceByPlayer = context.state && context.state.terminal_dice;
    if (!diceByPlayer || typeof diceByPlayer !== "object") return;
    const documentRef = context.board.ownerDocument || global.document;
    const review = element(documentRef, "details", "liars-terminal-review");
    review.appendChild(element(
      documentRef,
      "summary",
      "liars-terminal-summary",
      `终局骰子复盘 · ${Object.keys(diceByPlayer).length} 家`
    ));
    const rows = element(documentRef, "div", "liars-terminal-dice");
    Object.entries(diceByPlayer).forEach(([playerId, dice]) => {
      const row = element(documentRef, "span", "liars-terminal-row");
      row.dataset.playerId = playerId;
      row.textContent = `${participantName(context, playerId)}：${Array.isArray(dice) && dice.length ? dice.join(" · ") : "无骰"}`;
      rows.appendChild(row);
    });
    review.appendChild(rows);
    context.board.appendChild(review);
  }

  function renderBoard(context) {
    const documentRef = context.board.ownerDocument || global.document;
    ensureStylesheet(documentRef);
    if (typeof global.renderLiarsDice !== "function") {
      throw new Error("吹牛骰子原桌面 renderer 尚未就绪");
    }
    global.renderLiarsDice(context.board, context.state);
    appendTerminalReview(context);
    return true;
  }

  global.DuelGameUI.register("liars_dice", {
    participantPresentation: "generic",
    usesStandardMoveConfirmation: false,
    renderBoard,
  });
}(window));

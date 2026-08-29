"use strict";

const fs = require("node:fs");
const {Chess, validateFen} = require("./chess.js");

const STANDARD_FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1";
const UCI_PATTERN = /^[a-h][1-8][a-h][1-8][qrbn]?$/;

function coordinates(square) {
  if (typeof square !== "string" || !/^[a-h][1-8]$/.test(square)) {
    throw new Error(`规则引擎返回了无效坐标：${String(square)}`);
  }
  return {
    row: 8 - Number(square[1]),
    col: square.charCodeAt(0) - "a".charCodeAt(0),
  };
}

function uciFor(move) {
  return `${move.from}${move.to}${move.promotion || ""}`;
}

function legalMove(move) {
  const from = coordinates(move.from);
  const to = coordinates(move.to);
  return {
    from_row: from.row,
    from_col: from.col,
    to_row: to.row,
    to_col: to.col,
    ...(move.promotion ? {promotion: move.promotion} : {}),
    color: move.color,
    piece: move.piece,
    captured: move.captured || null,
    flags: move.flags,
    san: move.san,
    uci: uciFor(move),
  };
}

function checkedHistory(history) {
  if (!Array.isArray(history) || history.length > 2000) {
    throw new Error("history 必须是至多 2000 手的 UCI 数组");
  }
  history.forEach((move) => {
    if (typeof move !== "string" || !UCI_PATTERN.test(move)) {
      throw new Error(`走子历史含无效 UCI：${String(move)}`);
    }
  });
  return history;
}

function positionIdentity(game) {
  // FIDE 9.2.3 compares piece placement, side to move and possible moves.
  // chess.js' default FEN deliberately omits an en-passant square unless a
  // legal (including king-safe) en-passant capture exists. The first four FEN
  // fields therefore preserve exactly the castling/en-passant rights that can
  // change the set of possible moves while excluding the clocks.
  return game.fen().split(/\s+/).slice(0, 4).join(" ");
}

function loadGame(startingFen, history) {
  const fen = startingFen == null ? STANDARD_FEN : startingFen;
  const validation = validateFen(fen);
  if (!validation.ok) {
    throw new Error(`无效 FEN：${validation.error || "无法载入局面"}`);
  }
  const game = new Chess(fen);
  const pieces = game.board().flat().filter(Boolean);
  for (const color of ["w", "b"]) {
    const kings = pieces.filter(
      (piece) => piece.color === color && piece.type === "k"
    );
    if (kings.length !== 1) {
      throw new Error(`无效 FEN：${color === "w" ? "白" : "黑"}方必须恰有一个王`);
    }
  }
  const positionHistory = [positionIdentity(game)];
  checkedHistory(history || []).forEach((uci, index) => {
    const legal = game.moves({verbose: true}).find((move) => uciFor(move) === uci);
    if (!legal || !game.move({
      from: legal.from,
      to: legal.to,
      ...(legal.promotion ? {promotion: legal.promotion} : {}),
    })) {
      throw new Error(`无法重放第 ${index + 1} 手：${uci}`);
    }
    positionHistory.push(positionIdentity(game));
  });
  return {game, positionHistory};
}

function currentRepetitionCount(positionHistory) {
  const current = positionHistory[positionHistory.length - 1];
  return positionHistory.reduce(
    (count, identity) => count + Number(identity === current),
    0
  );
}

function automaticDrawReason(game, repetitionCount) {
  if (game.isCheckmate()) return null;
  if (game.isStalemate()) return "stalemate";
  if (game.isInsufficientMaterial()) return "insufficient_material";
  const halfmoveClock = Number(game.fen().split(/\s+/)[4]);
  if (repetitionCount >= 5) return "fivefold_repetition";
  if (halfmoveClock >= 150) return "seventy_five_move_rule";
  return null;
}

function snapshot(game, positionHistory) {
  const fen = game.fen();
  const halfmoveClock = Number(fen.split(/\s+/)[4]);
  const repetitionCount = currentRepetitionCount(positionHistory);
  const drawReason = automaticDrawReason(game, repetitionCount);
  const claimableDrawReasons = [];
  if (!game.isCheckmate() && drawReason === null) {
    if (repetitionCount >= 3) {
      claimableDrawReasons.push("threefold_repetition");
    }
    if (halfmoveClock >= 100) {
      claimableDrawReasons.push("fifty_move_rule");
    }
  }
  return {
    fen,
    turn_color: game.turn(),
    board: game.board().map((row) => row.map(
      (piece) => piece ? `${piece.color}:${piece.type}` : null
    )),
    legal_moves: game.moves({verbose: true}).map(legalMove),
    in_check: game.isCheck(),
    in_checkmate: game.isCheckmate(),
    in_stalemate: game.isStalemate(),
    insufficient_material: game.isInsufficientMaterial(),
    position_history: [...positionHistory],
    repetition_count: repetitionCount,
    in_threefold_repetition: repetitionCount >= 3,
    in_fivefold_repetition: repetitionCount >= 5,
    halfmove_clock: halfmoveClock,
    can_claim_draw: claimableDrawReasons.length > 0,
    claimable_draw_reasons: claimableDrawReasons,
    in_draw: drawReason !== null,
    game_over: game.isCheckmate() || drawReason !== null,
    draw_reason: drawReason,
  };
}

function dispatch(request) {
  if (!request || typeof request !== "object") throw new Error("请求必须是对象");
  const history = checkedHistory(request.history || []);
  if (request.action === "state") {
    const loaded = loadGame(request.starting_fen, history);
    return {state: snapshot(loaded.game, loaded.positionHistory)};
  }
  if (request.action === "apply") {
    if (typeof request.move !== "string" || !UCI_PATTERN.test(request.move)) {
      throw new Error("move 必须是四或五位 UCI 坐标");
    }
    const {game, positionHistory} = loadGame(request.starting_fen, history);
    const legal = game.moves({verbose: true}).find(
      (candidate) => uciFor(candidate) === request.move
    );
    if (!legal) throw new Error("该走法不合法");
    const applied = game.move({
      from: legal.from,
      to: legal.to,
      ...(legal.promotion ? {promotion: legal.promotion} : {}),
    });
    if (!applied) throw new Error("规则引擎未能执行合法走法");
    positionHistory.push(positionIdentity(game));
    return {move: legalMove(applied), state: snapshot(game, positionHistory)};
  }
  throw new Error(`未知规则动作：${String(request.action)}`);
}

try {
  const request = JSON.parse(fs.readFileSync(0, "utf8"));
  process.stdout.write(JSON.stringify({ok: true, result: dispatch(request)}));
} catch (error) {
  process.stdout.write(JSON.stringify({
    ok: false,
    error: error instanceof Error ? error.message : String(error),
  }));
}

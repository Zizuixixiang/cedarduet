"use strict";

const fs = require("node:fs");
const {Chess} = require("./chess.js");

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

function loadGame(startingFen, history) {
  const game = new Chess();
  const fen = startingFen == null ? STANDARD_FEN : startingFen;
  const validation = game.validate_fen(fen);
  if (!validation.valid || !game.load(fen)) {
    throw new Error(`无效 FEN：${validation.error || "无法载入局面"}`);
  }
  const pieces = game.board().flat().filter(Boolean);
  for (const color of ["w", "b"]) {
    const kings = pieces.filter(
      (piece) => piece.color === color && piece.type === "k"
    );
    if (kings.length !== 1) {
      throw new Error(`无效 FEN：${color === "w" ? "白" : "黑"}方必须恰有一个王`);
    }
  }
  checkedHistory(history || []).forEach((uci, index) => {
    const legal = game.moves({verbose: true}).find((move) => uciFor(move) === uci);
    if (!legal || !game.move({
      from: legal.from,
      to: legal.to,
      ...(legal.promotion ? {promotion: legal.promotion} : {}),
    })) {
      throw new Error(`无法重放第 ${index + 1} 手：${uci}`);
    }
  });
  return game;
}

function drawReason(game) {
  if (game.in_stalemate()) return "stalemate";
  if (game.insufficient_material()) return "insufficient_material";
  if (game.in_threefold_repetition()) return "threefold_repetition";
  const halfmoveClock = Number(game.fen().split(/\s+/)[4]);
  if (halfmoveClock >= 100) return "fifty_move_rule";
  return null;
}

function snapshot(game) {
  const fen = game.fen();
  const halfmoveClock = Number(fen.split(/\s+/)[4]);
  return {
    fen,
    turn_color: game.turn(),
    board: game.board().map((row) => row.map(
      (piece) => piece ? `${piece.color}:${piece.type}` : null
    )),
    legal_moves: game.moves({verbose: true}).map(legalMove),
    in_check: game.in_check(),
    in_checkmate: game.in_checkmate(),
    in_stalemate: game.in_stalemate(),
    insufficient_material: game.insufficient_material(),
    in_threefold_repetition: game.in_threefold_repetition(),
    halfmove_clock: halfmoveClock,
    in_draw: game.in_draw(),
    game_over: game.game_over(),
    draw_reason: drawReason(game),
  };
}

function dispatch(request) {
  if (!request || typeof request !== "object") throw new Error("请求必须是对象");
  const history = checkedHistory(request.history || []);
  if (request.action === "state") {
    return {state: snapshot(loadGame(request.starting_fen, history))};
  }
  if (request.action === "apply") {
    if (typeof request.move !== "string" || !UCI_PATTERN.test(request.move)) {
      throw new Error("move 必须是四或五位 UCI 坐标");
    }
    const game = loadGame(request.starting_fen, history);
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
    return {move: legalMove(applied), state: snapshot(game)};
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

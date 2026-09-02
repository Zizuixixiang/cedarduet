"use strict";

const fs = require("node:fs");
const {Xiangqi} = require("./xiangqi.js");

function coordinates(square) {
  if (typeof square !== "string" || !/^[a-i][0-9]$/.test(square)) {
    throw new Error(`规则引擎返回了无效坐标：${String(square)}`);
  }
  return {
    row: 9 - Number(square[1]),
    col: square.charCodeAt(0) - "a".charCodeAt(0),
  };
}

function legalMove(move) {
  const from = coordinates(move.from);
  const to = coordinates(move.to);
  return {
    from_row: from.row,
    from_col: from.col,
    to_row: to.row,
    to_col: to.col,
    piece: move.piece.toLowerCase(),
    captured: move.captured || null,
    iccs: move.iccs,
  };
}

function snapshot(game) {
  const halfmoveClock = Number(game.fen().split(/\s+/)[4]);
  const insufficientMaterial = game.insufficient_material();
  // Product rule: this casual edition does not adjudicate long-check/long-
  // chase responsibility, so the upstream approximate threefold shortcut is
  // intentionally not part of the terminal path.
  const drawReason = insufficientMaterial
    ? "insufficient_material"
    : (halfmoveClock >= 120 ? "sixty_move_no_capture" : null);
  return {
    fen: game.fen(),
    turn_color: game.turn(),
    board: game.board().map((row) => row.map(
      (piece) => piece ? `${piece.color}:${piece.type}` : null
    )),
    legal_moves: game.moves({verbose: true}).map(legalMove),
    in_check: game.in_check(),
    in_checkmate: game.in_checkmate(),
    in_stalemate: game.in_stalemate(),
    halfmove_clock: halfmoveClock,
    insufficient_material: insufficientMaterial,
    draw_reason: drawReason,
    in_draw: drawReason !== null,
  };
}

function loadGame(fen) {
  const game = new Xiangqi();
  if (fen !== undefined && fen !== null) {
    const validation = game.validate_fen(fen);
    if (!validation.valid || !game.load(fen)) {
      throw new Error(`无效 FEN：${validation.error || "无法载入局面"}`);
    }
  }
  return game;
}

function dispatch(request) {
  if (!request || typeof request !== "object") throw new Error("请求必须是对象");
  if (request.action === "state") return {state: snapshot(loadGame(request.fen))};
  if (request.action === "apply") {
    if (typeof request.move !== "string" || !/^[a-i][0-9][a-i][0-9]$/.test(request.move)) {
      throw new Error("move 必须是四位 ICCS 坐标");
    }
    const game = loadGame(request.fen);
    const move = game.move(request.move);
    if (!move) throw new Error("该走法不合法");
    return {move, state: snapshot(game)};
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

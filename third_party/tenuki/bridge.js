"use strict";

import fs from "node:fs";
import {
  HostGame,
  BOARD_SIZE,
  KOMI,
  KO_RULE,
  SCORING,
  checkedEngineHistory,
  sortedPoints,
} from "./host-game.js";

function boardFor(game) {
  const board = Array.from({length: BOARD_SIZE}, () => Array(BOARD_SIZE).fill(null));
  game.intersections().forEach((point) => {
    if (!point.isEmpty()) board[point.y][point.x] = point.value;
  });
  return board;
}

function consecutivePasses(history) {
  let count = 0;
  for (let index = history.length - 1; index >= 0; index -= 1) {
    if (history[index].action !== "pass") break;
    count += 1;
  }
  return count;
}

function snapshot(game, history) {
  const current = game.currentState();
  const over = game.isOver();
  const score = over ? game.score() : null;
  const territory = over ? game.territory() : null;
  return {
    rule_profile: {
      board_size: BOARD_SIZE,
      scoring: SCORING,
      ko_rule: KO_RULE,
      komi: KOMI,
      suicide: false,
    },
    board: boardFor(game),
    to_play: game.currentPlayer(),
    move_number: current.moveNumber,
    consecutive_passes: consecutivePasses(history),
    captures: {
      black: current.whiteStonesCaptured,
      white: current.blackStonesCaptured,
    },
    ko_point: current.koPoint
      ? {row: current.koPoint.y, col: current.koPoint.x}
      : null,
    last_move: current.playedPoint
      ? {
          action: "play",
          row: current.playedPoint.y,
          col: current.playedPoint.x,
          color: current.color,
          captured: sortedPoints(current.capturedPositions),
        }
      : current.moveNumber > 0
        ? {action: "pass", color: current.color}
        : null,
    is_over: over,
    dead_stones: sortedPoints(game.deadStones()),
    score,
    territory: territory && {
      black: sortedPoints(territory.black),
      white: sortedPoints(territory.white),
    },
    legal_actions: game.legalActions(),
    position_history_count: game._moves.length + 1,
    position_identity: game.positionIdentity(),
  };
}

function checkedDeadStones(deadStones) {
  return deadStones === undefined ? [] : deadStones;
}

function loaded(request) {
  const history = checkedEngineHistory(request.history || []);
  const game = new HostGame(history, checkedDeadStones(request.dead_stones));
  return {game, history};
}

function dispatch(request) {
  if (!request || typeof request !== "object" || Array.isArray(request)) {
    throw new Error("请求必须是对象");
  }
  if (request.action === "state") {
    const {game, history} = loaded(request);
    return {state: snapshot(game, history), history};
  }
  if (request.action === "apply") {
    const {game, history} = loaded(request);
    const move = request.move;
    if (!move || typeof move !== "object" || Array.isArray(move)) {
      throw new Error("move 必须是对象");
    }
    let applied;
    if (move.action === "pass") {
      if (!game.pass()) throw new Error("当前不能 pass");
      applied = {action: "pass", color: game.currentState().color};
    } else if (move.action === "play") {
      if (!game.playAt(move.row, move.col)) throw new Error("该落点不合法");
      const current = game.currentState();
      applied = {
        action: "play",
        row: current.playedPoint.y,
        col: current.playedPoint.x,
        color: current.color,
        captured: sortedPoints(current.capturedPositions),
      };
    } else {
      throw new Error("只支持 play 或 pass");
    }
    const nextHistory = history.concat({
      action: applied.action,
      ...(applied.action === "play" ? {row: applied.row, col: applied.col} : {}),
    });
    return {move: applied, state: snapshot(game, nextHistory), history: nextHistory};
  }
  if (request.action === "toggle_dead") {
    const {game, history} = loaded(request);
    if (!game.isOver()) throw new Error("尚未进入死子确认阶段");
    const before = new Set(game.deadStones().map((point) => `${point.y},${point.x}`));
    if (!game.toggleDeadAt(request.row, request.col)) {
      throw new Error("只能选择棋盘上的棋子");
    }
    const afterPoints = game.deadStones();
    const after = new Set(afterPoints.map((point) => `${point.y},${point.x}`));
    const changed = (source, target) => sortedPoints(
      game.intersections().filter(
        (point) => source.has(`${point.y},${point.x}`) && !target.has(`${point.y},${point.x}`)
      )
    );
    return {
      dead_added: changed(after, before),
      dead_removed: changed(before, after),
      state: snapshot(game, history),
      history,
    };
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

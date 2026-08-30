"use strict";

/* Mechanical Node.js translation of relevant vendored upstream *.test.ts cases. */
const {describe, it} = require("node:test");
const assert = require("node:assert/strict");
const {
  PieceRank,
  GameResult,
  Piece,
  Graph,
  RailroadNetwork,
  BoardValidator,
  Board,
  HEADQUARTER_SQUARE_GROUP,
  HEADQUARTER_SQUARES,
  BUNKER_SQUARES,
} = require("../runtime/core.js");

describe("upstream Piece semantics", () => {
  it("bomb and landmine both disappear", () => {
    assert.equal(
      new Piece("r", PieceRank.BOMB).compareRank(new Piece("b", PieceRank.LANDMINE)),
      GameResult.DRAW
    );
  });

  it("engineer disables landmine and ordinary piece loses", () => {
    const mine = new Piece("b", PieceRank.LANDMINE);
    assert.equal(new Piece("r", PieceRank.ENGINEER).compareRank(mine), GameResult.WIN);
    assert.equal(new Piece("r", PieceRank.COMMANDER).compareRank(mine), GameResult.LOSE);
  });

  it("rank, equal rank, bomb, and flag outcomes match upstream", () => {
    assert.equal(
      new Piece("r", PieceRank.GENERAL).compareRank(new Piece("b", PieceRank.BRIGADIER_GENERAL)),
      GameResult.WIN
    );
    assert.equal(
      new Piece("r", PieceRank.GENERAL).compareRank(new Piece("b", PieceRank.GENERAL)),
      GameResult.DRAW
    );
    assert.equal(
      new Piece("r", PieceRank.LIEUTENANT).compareRank(new Piece("b", PieceRank.FLAG)),
      GameResult.WIN
    );
  });
});

describe("upstream graph semantics", () => {
  const graph = new Graph();

  it("creates 60 nodes and makes headquarters immobile", () => {
    assert.equal(graph.nodes.length, 60);
    HEADQUARTER_SQUARES.forEach((square) => {
      assert.equal(graph.getAdjacentNeighbors(square).size, 0);
    });
  });

  it("bunkers connect diagonally", () => {
    BUNKER_SQUARES.forEach((square) => {
      assert.ok(graph.getAdjacentNeighbors(square).size >= 4);
    });
    assert.equal(graph.getAdjacentNeighbors("b3").has("a2"), true);
    assert.equal(graph.getAdjacentNeighbors("b3").has("c2"), true);
  });
});

describe("upstream railroad semantics", () => {
  const rail1 = ["a2", "a3", "a4", "a5", "a6"];
  const rail2 = ["a2", "b2", "c2", "d2", "e2"];
  const railroad = new RailroadNetwork([rail1, rail2]);
  const state = {
    e2: new Piece("r", PieceRank.ENGINEER), d2: null, c2: null, b2: null,
    a2: null, a3: null, a4: null, a5: new Piece("_", PieceRank.FLAG), a6: null,
  };

  it("engineer turns through connected railroad while blockers stop traversal", () => {
    const reachable = railroad.getReachableSquares("e2", true, state);
    assert.equal(reachable.size, 11);
    ["e2", "d2", "c2", "b2", "a2", "a3", "a4", "a5", "e1", "e3", "d3"]
      .forEach((square) => assert.equal(reachable.has(square), true));
    assert.equal(reachable.has("a6"), false);
  });

  it("ordinary pieces cannot turn at the intersection", () => {
    const reachable = railroad.getReachableSquares("e2", false, state);
    assert.equal(reachable.size, 8);
    assert.equal(reachable.has("a3"), false);
  });
});

describe("upstream setup validation", () => {
  const validator = new BoardValidator();

  it("enforces bomb, landmine, and flag positions", () => {
    assert.equal(validator.isValidBombPosition(1), true);
    assert.equal(validator.isValidBombPosition(6), false);
    assert.equal(validator.isValidLandminePosition(1), true);
    assert.equal(validator.isValidLandminePosition(2), true);
    assert.equal(validator.isValidLandminePosition(3), false);
    assert.equal(validator.isValidFlagPosition("b12", "d12"), true);
    assert.equal(validator.isValidFlagPosition("b12", "d1"), false);
  });

  it("rejects a two-way swap that would smuggle a bomb onto the front row", () => {
    const board = new Board();
    assert.equal(board.boardState.a1.getRank(), PieceRank.BOMB);
    assert.equal(board.getSwapMoves("a6", board.boardState).some((move) => move.endSquare === "a1"), false);
    assert.equal(board.getSwapMoves("a1", board.boardState).some((move) => move.endSquare === "a6"), false);
  });

  it("shuffle preserves inventory, opponent, and placement legality", () => {
    const board = new Board();
    const opponent = JSON.stringify(Object.entries(board.boardState)
      .filter(([, piece]) => piece && piece.getPieceColor() === "r")
      .map(([square, piece]) => [square, piece.getRank()]));
    const before = Object.values(board.boardState)
      .filter((piece) => piece && piece.getPieceColor() === "b")
      .map((piece) => piece.getRank()).sort((a, b) => a - b);
    board.shufflePlayerPieces("blue");
    const after = Object.values(board.boardState)
      .filter((piece) => piece && piece.getPieceColor() === "b")
      .map((piece) => piece.getRank()).sort((a, b) => a - b);
    assert.deepEqual(after, before);
    assert.equal(new BoardValidator().validateBoard(board.boardState), true);
    assert.equal(JSON.stringify(Object.entries(board.boardState)
      .filter(([, piece]) => piece && piece.getPieceColor() === "r")
      .map(([square, piece]) => [square, piece.getRank()])), opponent);
    const nonFlagHq = HEADQUARTER_SQUARE_GROUP[0].find(
      (square) => board.boardState[square].getRank() !== PieceRank.FLAG
    );
    assert.equal(
      [PieceRank.LANDMINE, PieceRank.LIEUTENANT].includes(board.boardState[nonFlagHq].getRank()),
      true
    );
  });
});

describe("upstream board move evaluation", () => {
  it("publishes moves for both sides and evaluates front-line contact", () => {
    const board = new Board();
    assert.ok(board.getMovesForPlayer("blue").length > 0);
    assert.ok(board.getMovesForPlayer("red").length > 0);
    assert.ok(board.evaluateMove("a6", "a7"));
    assert.equal(board.isCommanderAlive("blue"), true);
    assert.equal(board.isPlayerFlagCaptured("red"), false);
  });
});

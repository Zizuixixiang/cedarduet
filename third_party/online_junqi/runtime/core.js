"use strict";

/*
 * Executable CommonJS build of the vendored online-junqi rule core.
 *
 * The rule methods below are a type-erased module-format adaptation of the
 * TypeScript originals under ../src/lib.  Rule behavior is intentionally kept
 * aligned with the pinned upstream.  JSON serialization and dispatch live at
 * the bottom of this file and are CedarDuet-specific adapter code.
 */

const PieceRank = Object.freeze({
  BOMB: 0,
  COMMANDER: 1,
  GENERAL: 2,
  MAJOR_GENERAL: 3,
  BRIGADIER_GENERAL: 4,
  COLONEL: 5,
  MAJOR: 6,
  CAPTAIN: 7,
  LIEUTENANT: 8,
  ENGINEER: 9,
  LANDMINE: 10,
  FLAG: 11,
});

const GameResult = Object.freeze({WIN: 1, DRAW: 0, LOSE: -1});

class Piece {
  constructor(colorChar, rank) {
    this.colorChar = colorChar;
    this.rank = rank;
  }

  getPieceColor() {
    return this.colorChar;
  }

  getRank() {
    return this.rank;
  }

  isMovable() {
    return this.rank !== PieceRank.LANDMINE && this.rank !== PieceRank.FLAG;
  }

  compareRank(otherPiece) {
    const rank1 = this.rank;
    const rank2 = otherPiece.rank;
    if (rank1 === PieceRank.BOMB || rank2 === PieceRank.BOMB) {
      return GameResult.DRAW;
    }
    if (rank2 === PieceRank.FLAG) {
      return GameResult.WIN;
    }
    if (rank2 === PieceRank.LANDMINE) {
      return rank1 === PieceRank.ENGINEER ? GameResult.WIN : GameResult.LOSE;
    }
    if (
      rank1 >= PieceRank.COMMANDER && rank1 <= PieceRank.ENGINEER
      && rank2 >= PieceRank.COMMANDER && rank2 <= PieceRank.ENGINEER
    ) {
      if (rank1 < rank2) return GameResult.WIN;
      if (rank1 === rank2) return GameResult.DRAW;
      return GameResult.LOSE;
    }
    return GameResult.DRAW;
  }
}

const PLAYER1_LEFT_RAIL = ["a2", "a3", "a4", "a5", "a6"];
const PLAYER1_RIGHT_RAIL = ["e2", "e3", "e4", "e5", "e6"];
const PLAYER1_BACK_ROW_RAIL = ["a2", "b2", "c2", "d2", "e2"];
const PLAYER1_FRONT_ROW_RAIL = ["a6", "b6", "c6", "d6", "e6"];
const PLAYER2_LEFT_RAIL = ["e7", "e8", "e9", "e10", "e11"];
const PLAYER2_RIGHT_RAIL = ["a7", "a8", "a9", "a10", "a11"];
const PLAYER2_FRONT_ROW_RAIL = ["a7", "b7", "c7", "d7", "e7"];
const PLAYER2_BACK_ROW_RAIL = ["a11", "b11", "c11", "d11", "e11"];
const LEFT_RAIL = PLAYER1_LEFT_RAIL.concat(PLAYER2_RIGHT_RAIL);
const RIGHT_RAIL = PLAYER1_RIGHT_RAIL.concat(PLAYER2_LEFT_RAIL);
const RAIL_LINES = [
  LEFT_RAIL,
  RIGHT_RAIL,
  PLAYER1_BACK_ROW_RAIL,
  PLAYER1_FRONT_ROW_RAIL,
  PLAYER2_FRONT_ROW_RAIL,
  PLAYER2_BACK_ROW_RAIL,
];
const FRONT_ROW_SQUARES = PLAYER1_FRONT_ROW_RAIL.concat(PLAYER2_FRONT_ROW_RAIL);
const BUNKER_SQUARES = [
  "b3", "d3", "c4", "b5", "d5", "b8", "d8", "c9", "b10", "d10",
];
const HEADQUARTER_SQUARE_GROUP = [["b1", "d1"], ["b12", "d12"]];
const HEADQUARTER_SQUARES = HEADQUARTER_SQUARE_GROUP.flat();

const bunkerTransforms = [
  {x: 0, y: 1}, {x: 1, y: 1}, {x: 1, y: 0}, {x: 1, y: -1},
  {x: 0, y: -1}, {x: -1, y: -1}, {x: -1, y: 0}, {x: -1, y: 1},
];
const crossTransforms = [
  {x: 0, y: 1}, {x: 1, y: 0}, {x: 0, y: -1}, {x: -1, y: 0},
];

class Graph {
  constructor() {
    const graphNodesEdges = this.initializeGraph();
    this.nodes = graphNodesEdges.nodes;
    this.neighborMap = graphNodesEdges.neighborMap;
  }

  getAdjacentNeighbors(currentSquare) {
    return this.neighborMap[currentSquare];
  }

  initializeGraph() {
    const nodes = this.initializeNodes();
    const neighborMap = this.initializeEdges(nodes);
    return {nodes, neighborMap};
  }

  initializeNodes() {
    const allNodes = [];
    ["a", "b", "c", "d", "e"].forEach((columnChar) => {
      for (let row = 1; row <= 12; row += 1) {
        allNodes.push(columnChar + row);
      }
    });
    return allNodes;
  }

  initializeEdges(nodes) {
    const neighborMap = {};
    nodes.forEach((currentSquare) => {
      if (FRONT_ROW_SQUARES.includes(currentSquare)) return;
      this.getMoves(currentSquare, crossTransforms).forEach((newSquare) => {
        this.addEdge(neighborMap, currentSquare, newSquare);
      });
    });
    BUNKER_SQUARES.forEach((currentSquare) => {
      this.getMoves(currentSquare, bunkerTransforms).forEach((newSquare) => {
        this.addEdge(neighborMap, currentSquare, newSquare);
      });
    });
    this.addEdgesInFrontRowSamePlayer(neighborMap);
    this.addEdgesBetweenDifferentPlayers(neighborMap);
    HEADQUARTER_SQUARES.forEach((square) => neighborMap[square].clear());
    return neighborMap;
  }

  getMoves(square, transforms) {
    return transforms
      .map((move) => this.transformSquare(square, move))
      .filter((newSquare) => Boolean(newSquare));
  }

  addEdgesInFrontRowSamePlayer(neighborMap) {
    this.addEdge(neighborMap, "b6", "a6");
    this.addEdge(neighborMap, "b6", "c6");
    this.addEdge(neighborMap, "d6", "c6");
    this.addEdge(neighborMap, "d6", "e6");
    this.addEdge(neighborMap, "b7", "a7");
    this.addEdge(neighborMap, "b7", "c7");
    this.addEdge(neighborMap, "d7", "c7");
    this.addEdge(neighborMap, "d7", "e7");
  }

  addEdgesBetweenDifferentPlayers(neighborMap) {
    this.addEdge(neighborMap, "a6", "a7");
    this.addEdge(neighborMap, "c6", "c7");
    this.addEdge(neighborMap, "e6", "e7");
  }

  transformSquare(square, transform) {
    const column = square[0];
    const row = parseInt(square.substring(1), 10);
    const destColumn = Graph.alpha2num(column) + transform.x;
    const destRow = row + transform.y;
    if (destColumn < 1 || destColumn > 5 || destRow < 1 || destRow > 12) {
      return null;
    }
    return Graph.num2alpha(destColumn) + destRow;
  }

  addEdge(neighborMap, node1Key, node2Key) {
    if (!(node1Key in neighborMap)) neighborMap[node1Key] = new Set();
    if (!(node2Key in neighborMap)) neighborMap[node2Key] = new Set();
    neighborMap[node1Key].add(node2Key);
    neighborMap[node2Key].add(node1Key);
  }

  static alpha2num(value) {
    return {a: 1, b: 2, c: 3, d: 4, e: 5}[value] || 6;
  }

  static num2alpha(value) {
    return {1: "a", 2: "b", 3: "c", 4: "d", 5: "e"}[value] || "f";
  }
}

class RailroadNetwork {
  constructor(railLines) {
    this.graph = new Graph();
    this.railLines = railLines;
  }

  getReachableSquares(currentSquare, isPieceEngineer, boardState) {
    if (!this.isOnRail(currentSquare)) {
      return this.graph.getAdjacentNeighbors(currentSquare);
    }
    const singleRail = this.getAllRailroadsFromSquare(currentSquare);
    const reachableSquares = [];
    const visited = new Set();
    visited.add(currentSquare);
    reachableSquares.push(currentSquare);
    while (reachableSquares.length !== 0) {
      const iterSquare = reachableSquares.shift();
      const allNeighbors = this.graph.getAdjacentNeighbors(iterSquare);
      const neighborsOnRailroad = Array.from(allNeighbors)
        .filter((nextSquare) => this.isOnRail(nextSquare));
      neighborsOnRailroad.forEach((nextSquare) => {
        if (visited.has(nextSquare)) return;
        if (!isPieceEngineer && !singleRail.has(nextSquare)) return;
        visited.add(nextSquare);
        if (boardState[nextSquare] === null) reachableSquares.push(nextSquare);
      });
    }
    this.graph.getAdjacentNeighbors(currentSquare).forEach((square) => visited.add(square));
    return visited;
  }

  isOnRail(currentSquare) {
    return this.railLines.some((railLine) => railLine.includes(currentSquare));
  }

  getAllRailroadsFromSquare(currentSquare) {
    return new Set(this.railLines.filter((railroad) => railroad.includes(currentSquare)).flat());
  }
}

class BoardValidator {
  validateBoard(board) {
    return Object.keys(board).every((square) => {
      if (BUNKER_SQUARES.includes(square)) return board[square] === null;
      const piece = board[square];
      if (!piece) return false;
      const row = this.getBoardSquareRow(square);
      if (piece.getRank() === PieceRank.BOMB) return this.isValidBombPosition(row);
      if (piece.getRank() === PieceRank.LANDMINE) return this.isValidLandminePosition(row);
      if (piece.getRank() === PieceRank.FLAG) return HEADQUARTER_SQUARES.includes(square);
      return true;
    });
  }

  isDestinationPositionValid(pieceRank, current, destination) {
    const row = this.getBoardSquareRow(destination);
    if (pieceRank === PieceRank.BOMB) return this.isValidBombPosition(row);
    if (pieceRank === PieceRank.LANDMINE) return this.isValidLandminePosition(row);
    if (pieceRank === PieceRank.FLAG) return this.isValidFlagPosition(current, destination);
    return true;
  }

  getBoardSquareRow(square) {
    return parseInt(square.substring(1), 10);
  }

  isValidBombPosition(rowNum) {
    return rowNum !== 6 && rowNum !== 7;
  }

  isValidLandminePosition(rowNum) {
    return rowNum === 1 || rowNum === 2 || rowNum === 11 || rowNum === 12;
  }

  isValidFlagPosition(current, destination) {
    const player1 = HEADQUARTER_SQUARE_GROUP[0];
    const player2 = HEADQUARTER_SQUARE_GROUP[1];
    return (
      (player1.includes(current) && player1.includes(destination) && current !== destination)
      || (player2.includes(current) && player2.includes(destination) && current !== destination)
    );
  }
}

class BoardGenerator {
  generateBoard() {
    const P = PieceRank;
    return {
      a12: new Piece("r", P.LANDMINE), b12: new Piece("r", P.FLAG), c12: new Piece("r", P.LANDMINE), d12: new Piece("r", P.BOMB), e12: new Piece("r", P.BOMB),
      a11: new Piece("r", P.ENGINEER), b11: new Piece("r", P.LANDMINE), c11: new Piece("r", P.ENGINEER), d11: new Piece("r", P.GENERAL), e11: new Piece("r", P.ENGINEER),
      a10: new Piece("r", P.MAJOR), b10: null, c10: new Piece("r", P.MAJOR), d10: null, e10: new Piece("r", P.COLONEL),
      a9: new Piece("r", P.COMMANDER), b9: new Piece("r", P.MAJOR_GENERAL), c9: null, d9: new Piece("r", P.MAJOR_GENERAL), e9: new Piece("r", P.COLONEL),
      a8: new Piece("r", P.LIEUTENANT), b8: null, c8: new Piece("r", P.LIEUTENANT), d8: null, e8: new Piece("r", P.LIEUTENANT),
      a7: new Piece("r", P.CAPTAIN), b7: new Piece("r", P.CAPTAIN), c7: new Piece("r", P.CAPTAIN), d7: new Piece("r", P.BRIGADIER_GENERAL), e7: new Piece("r", P.BRIGADIER_GENERAL),
      a6: new Piece("b", P.CAPTAIN), b6: new Piece("b", P.GENERAL), c6: new Piece("b", P.CAPTAIN), d6: new Piece("b", P.MAJOR_GENERAL), e6: new Piece("b", P.CAPTAIN),
      a5: new Piece("b", P.BRIGADIER_GENERAL), b5: null, c5: new Piece("b", P.COLONEL), d5: null, e5: new Piece("b", P.BRIGADIER_GENERAL),
      a4: new Piece("b", P.LIEUTENANT), b4: new Piece("b", P.MAJOR_GENERAL), c4: null, d4: new Piece("b", P.COMMANDER), e4: new Piece("b", P.LIEUTENANT),
      a3: new Piece("b", P.MAJOR), b3: null, c3: new Piece("b", P.LIEUTENANT), d3: null, e3: new Piece("b", P.COLONEL),
      a2: new Piece("b", P.ENGINEER), b2: new Piece("b", P.LANDMINE), c2: new Piece("b", P.LANDMINE), d2: new Piece("b", P.ENGINEER), e2: new Piece("b", P.ENGINEER),
      a1: new Piece("b", P.BOMB), b1: new Piece("b", P.FLAG), c1: new Piece("b", P.LANDMINE), d1: new Piece("b", P.BOMB), e1: new Piece("b", P.MAJOR),
    };
  }
}

class BoardShuffler {
  constructor(boardValidator) {
    this.boardValidator = boardValidator;
  }

  shuffle(boardState, squares) {
    const remainingPieces = squares.map((square) => boardState[square]);
    const remainingSquares = new Set(squares);
    const assignment = {};
    const placeGroup = (matchesPiece, candidateSquares, maxCount = Infinity) => {
      const eligibleSquares = this.shuffleCopy(
        candidateSquares.filter((square) => remainingSquares.has(square))
      );
      const groupPieces = this.shuffleCopy(remainingPieces.filter(matchesPiece))
        .slice(0, Math.min(eligibleSquares.length, maxCount));
      groupPieces.forEach((piece, index) => {
        const square = eligibleSquares[index];
        assignment[square] = piece;
        remainingSquares.delete(square);
        remainingPieces.splice(remainingPieces.indexOf(piece), 1);
      });
    };

    const hqSquares = squares.filter((square) => HEADQUARTER_SQUARES.includes(square));
    const frontGuardRow = squares.filter(
      (square) => PLAYER1_BACK_ROW_RAIL.includes(square) || PLAYER2_BACK_ROW_RAIL.includes(square)
    );
    placeGroup((piece) => piece.getRank() === PieceRank.FLAG, hqSquares);
    placeGroup((piece) => piece.getRank() === PieceRank.LANDMINE, frontGuardRow, 1);
    placeGroup(
      (piece) => piece.getRank() === PieceRank.LANDMINE || piece.getRank() === PieceRank.LIEUTENANT,
      hqSquares
    );
    const flagSquare = hqSquares.find((square) => assignment[square].getRank() === PieceRank.FLAG);
    const hqRow = this.boardValidator.getBoardSquareRow(flagSquare);
    const centerSquare = `c${hqRow}`;
    const nearFlagColumn = flagSquare[0] === "b" ? "a" : "e";
    const farFlagColumn = nearFlagColumn === "a" ? "e" : "a";
    const nearFlagCorner = `${nearFlagColumn}${hqRow}`;
    const farFlagCorner = `${farFlagColumn}${hqRow}`;
    placeGroup(
      (piece) => piece.getRank() === PieceRank.LANDMINE || piece.getRank() === PieceRank.LIEUTENANT,
      [centerSquare]
    );
    placeGroup(
      (piece) => [PieceRank.CAPTAIN, PieceRank.LIEUTENANT, PieceRank.LANDMINE].includes(piece.getRank()),
      [farFlagCorner]
    );
    placeGroup(
      (piece) => ![
        PieceRank.FLAG, PieceRank.COMMANDER, PieceRank.GENERAL,
        PieceRank.BOMB, PieceRank.ENGINEER,
      ].includes(piece.getRank()),
      [nearFlagCorner]
    );
    const exposedFrontRow = squares.filter(
      (square) => FRONT_ROW_SQUARES.includes(square) && ["a", "c", "e"].includes(square[0])
    );
    placeGroup(
      (piece) => piece.getRank() >= PieceRank.COMMANDER && piece.getRank() <= PieceRank.LIEUTENANT,
      exposedFrontRow
    );
    placeGroup(
      (piece) => piece.getRank() === PieceRank.LANDMINE,
      squares.filter((square) => this.boardValidator.isValidLandminePosition(
        this.boardValidator.getBoardSquareRow(square)
      ))
    );
    placeGroup(
      (piece) => piece.getRank() === PieceRank.BOMB,
      squares.filter((square) => (
        this.boardValidator.isValidBombPosition(this.boardValidator.getBoardSquareRow(square))
        && this.boardValidator.getBoardSquareRow(square) !== hqRow
      ))
    );
    placeGroup(() => true, squares);
    squares.forEach((square) => { boardState[square] = assignment[square]; });
  }

  shuffleCopy(items) {
    const copy = items.slice();
    for (let index = copy.length - 1; index > 0; index -= 1) {
      const other = Math.floor(Math.random() * (index + 1));
      [copy[index], copy[other]] = [copy[other], copy[index]];
    }
    return copy;
  }
}

const MOVE_TYPE_BY_COMPARE_RESULT = Object.freeze({
  [GameResult.WIN]: "capture",
  [GameResult.DRAW]: "equal",
  [GameResult.LOSE]: "dies",
});

class Board {
  constructor() {
    this.boardState = new BoardGenerator().generateBoard();
    this.boardValidator = new BoardValidator();
    if (!this.boardValidator.validateBoard(this.boardState)) {
      throw new Error("Invalid board state");
    }
    this.nodeKeys = Object.keys(this.boardState);
    this.railroadNetwork = new RailroadNetwork(RAIL_LINES);
    this.boardShuffler = new BoardShuffler(this.boardValidator);
  }

  getMovesForPlayer(playerColor) {
    if (!playerColor || typeof playerColor !== "string") {
      throw new Error("Player color must be a non-empty string");
    }
    return this.getAllPlayerSquares(this.boardState, playerColor)
      .filter((square) => this.boardState[square] && this.boardState[square].isMovable())
      .flatMap((square) => this.getValidMoves(square, this.boardState));
  }

  getSwapForAll() {
    return Object.keys(this.boardState)
      .filter((square) => this.boardState[square])
      .flatMap((square) => this.getSwapMoves(square, this.boardState));
  }

  swapPieces(startSquare, endSquare) {
    if (!startSquare || !endSquare) throw new Error("Both start and end squares must be provided");
    if (!this.boardState[startSquare] || !this.boardState[endSquare]) {
      throw new Error("Both squares must contain pieces to swap");
    }
    [this.boardState[startSquare], this.boardState[endSquare]] = [
      this.boardState[endSquare], this.boardState[startSquare],
    ];
  }

  shufflePlayerPieces(playerColor) {
    this.boardShuffler.shuffle(
      this.boardState,
      this.getAllPlayerSquares(this.boardState, playerColor)
    );
  }

  getPieceAtSquare(currentSquare) {
    if (!currentSquare || typeof currentSquare !== "string") {
      throw new Error("Square location must be a non-empty string");
    }
    return this.boardState[currentSquare] || null;
  }

  placePieceAtSquare(currentSquare, piece) {
    if (!currentSquare || typeof currentSquare !== "string") {
      throw new Error("Square location must be a non-empty string");
    }
    if (!piece || !(piece instanceof Piece)) throw new Error("Piece must be a valid Piece instance");
    this.boardState[currentSquare] = piece;
  }

  setSquareEmpty(squareLocation) {
    if (!squareLocation || typeof squareLocation !== "string") {
      throw new Error("Square location must be a non-empty string");
    }
    this.boardState[squareLocation] = null;
  }

  isPlayerFlagCaptured(playerColor) {
    return !this.doesPieceExist(this.boardState, playerColor, PieceRank.FLAG);
  }

  isCommanderAlive(playerColor) {
    return this.doesPieceExist(this.boardState, playerColor, PieceRank.COMMANDER);
  }

  doesPieceExist(boardState, playerColor, pieceRank) {
    return this.getAllPlayerSquares(boardState, playerColor)
      .some((square) => boardState[square].getRank() === pieceRank);
  }

  evaluateMove(startSquare, destination) {
    const board = this.boardState;
    if (board[startSquare] === null || board[destination] === null) return null;
    const compareResult = board[startSquare].compareRank(board[destination]);
    const moveType = MOVE_TYPE_BY_COMPARE_RESULT[compareResult];
    return moveType ? {type: moveType, startSquare, endSquare: destination} : null;
  }

  getValidMoves(square, board) {
    const piece = board[square];
    const reachable = this.railroadNetwork.getReachableSquares(
      square,
      piece.getRank() === PieceRank.ENGINEER,
      board
    );
    return Array.from(reachable)
      .filter((destination) => square !== destination)
      .map((destination) => {
        const type = this.getMoveType(piece, board, destination);
        return type ? {type, startSquare: square, endSquare: destination} : null;
      })
      .filter(Boolean);
  }

  getSwapMoves(square, board) {
    const piece = board[square];
    return this.getAllPlayerSquares(board, piece.getPieceColor())
      .filter((destination) => square !== destination)
      .filter((destination) => this.boardValidator.isDestinationPositionValid(
        piece.getRank(), square, destination
      ))
      .filter((destination) => {
        const destinationPiece = board[destination];
        return !destinationPiece || this.boardValidator.isDestinationPositionValid(
          destinationPiece.getRank(), destination, square
        );
      })
      .map((destination) => ({type: "swap", startSquare: square, endSquare: destination}));
  }

  getAllPlayerSquares(boardState, playerColor) {
    return Object.keys(boardState)
      .filter((square) => boardState[square])
      .filter((square) => boardState[square].getPieceColor() === playerColor[0]);
  }

  getMoveType(piece, board, destination) {
    if (board[destination] === null) return "move";
    if (this.isSquareAttackable(piece, board, destination)) return "attack";
    return null;
  }

  isSquareAttackable(piece, board, destination) {
    return (
      board[destination].getPieceColor() !== piece.getPieceColor()
      && !BUNKER_SQUARES.includes(destination)
    );
  }
}

const rankValues = new Set(Object.values(PieceRank));

function serializePiece(piece) {
  return piece ? {color: piece.getPieceColor(), rank: piece.getRank()} : null;
}

function serializeBoard(board) {
  return Object.fromEntries(
    Object.entries(board.boardState).map(([square, piece]) => [square, serializePiece(piece)])
  );
}

function loadBoard(serialized) {
  if (!serialized || typeof serialized !== "object" || Array.isArray(serialized)) {
    throw new Error("board must be an object");
  }
  const board = new Board();
  if (
    Object.keys(serialized).length !== board.nodeKeys.length
    || board.nodeKeys.some((square) => !(square in serialized))
  ) {
    throw new Error("board must contain exactly the 60 authoritative squares");
  }
  const restored = {};
  board.nodeKeys.forEach((square) => {
    const value = serialized[square];
    if (value === null) {
      restored[square] = null;
      return;
    }
    if (
      !value || typeof value !== "object" || Array.isArray(value)
      || !["r", "b"].includes(value.color)
      || !Number.isInteger(value.rank) || !rankValues.has(value.rank)
    ) {
      throw new Error(`invalid piece at ${square}`);
    }
    restored[square] = new Piece(value.color, value.rank);
  });
  board.boardState = restored;
  return board;
}

function inventory(board) {
  const result = {r: {}, b: {}};
  Object.values(board.boardState).forEach((piece) => {
    if (!piece) return;
    const color = piece.getPieceColor();
    const rank = String(piece.getRank());
    result[color][rank] = (result[color][rank] || 0) + 1;
  });
  return result;
}

const expectedInventory = inventory(new Board());

function validatePersistedBoard(board) {
  const validPlacement = board.boardValidator.validateBoard(board.boardState);
  const validInventory = JSON.stringify(inventory(board)) === JSON.stringify(expectedInventory);
  return {validPlacement, validInventory};
}

function moveView(move) {
  return {type: move.type, from: move.startSquare, to: move.endSquare};
}

function applyAuthoritativeMove(board, legalMove) {
  const attacker = serializePiece(board.getPieceAtSquare(legalMove.startSquare));
  const defender = serializePiece(board.getPieceAtSquare(legalMove.endSquare));
  let evaluated = legalMove;
  if (legalMove.type === "attack") {
    evaluated = board.evaluateMove(legalMove.startSquare, legalMove.endSquare);
    if (!evaluated) throw new Error("authoritative collision evaluation failed");
  }
  const selectedPiece = board.getPieceAtSquare(evaluated.startSquare);
  switch (evaluated.type) {
    case "move":
      board.placePieceAtSquare(evaluated.endSquare, selectedPiece);
      board.setSquareEmpty(evaluated.startSquare);
      break;
    case "capture":
      board.setSquareEmpty(evaluated.endSquare);
      board.placePieceAtSquare(evaluated.endSquare, selectedPiece);
      board.setSquareEmpty(evaluated.startSquare);
      break;
    case "dies":
      board.setSquareEmpty(evaluated.startSquare);
      break;
    case "equal":
      board.setSquareEmpty(evaluated.startSquare);
      board.setSquareEmpty(evaluated.endSquare);
      break;
    default:
      throw new Error("authoritative engine returned an unknown move result");
  }
  return {
    declared_type: legalMove.type,
    result_type: evaluated.type,
    from: legalMove.startSquare,
    to: legalMove.endSquare,
    attacker,
    defender,
  };
}

function dispatch(request) {
  if (!request || typeof request !== "object" || Array.isArray(request)) {
    throw new Error("request must be an object");
  }
  if (request.action === "initial") {
    const board = new Board();
    return {
      board: serializeBoard(board),
      inventory: expectedInventory,
      squares: board.nodeKeys,
      bunkers: BUNKER_SQUARES,
      headquarters: HEADQUARTER_SQUARES,
      rail_lines: RAIL_LINES,
    };
  }
  const board = loadBoard(request.board);
  if (request.action === "validate") return validatePersistedBoard(board);
  if (request.action === "moves") {
    return {moves: board.getMovesForPlayer(String(request.color || "")).map(moveView)};
  }
  if (request.action === "swaps") {
    const color = String(request.color || "");
    return {
      swaps: board.getSwapForAll()
        .filter((move) => board.boardState[move.startSquare].getPieceColor() === color[0])
        .map(moveView),
    };
  }
  if (request.action === "shuffle") {
    const color = String(request.color || "");
    if (!["r", "b"].includes(color)) throw new Error("color must be r or b");
    board.shufflePlayerPieces(color);
    const validity = validatePersistedBoard(board);
    if (!validity.validPlacement || !validity.validInventory) {
      throw new Error("authoritative shuffler produced an invalid setup");
    }
    return {board: serializeBoard(board), ...validity};
  }
  if (request.action === "swap") {
    const color = String(request.color || "");
    const wantedFrom = String(request.from || "");
    const wantedTo = String(request.to || "");
    const legal = board.getSwapForAll().find((move) => (
      move.startSquare === wantedFrom
      && move.endSquare === wantedTo
      && board.boardState[move.startSquare].getPieceColor() === color[0]
    ));
    if (!legal) throw new Error("swap is not authoritative");
    board.swapPieces(legal.startSquare, legal.endSquare);
    const validity = validatePersistedBoard(board);
    if (!validity.validPlacement || !validity.validInventory) {
      throw new Error("authoritative swap produced an invalid setup");
    }
    return {board: serializeBoard(board), swap: moveView(legal), ...validity};
  }
  if (request.action === "apply") {
    const color = String(request.color || "");
    const wantedFrom = String(request.from || "");
    const wantedTo = String(request.to || "");
    const legal = board.getMovesForPlayer(color).find((move) => (
      move.startSquare === wantedFrom && move.endSquare === wantedTo
    ));
    if (!legal) throw new Error("move is not authoritative");
    const collision = applyAuthoritativeMove(board, legal);
    return {
      board: serializeBoard(board),
      move: collision,
      flags_captured: {
        r: board.isPlayerFlagCaptured("r"),
        b: board.isPlayerFlagCaptured("b"),
      },
      commanders_alive: {
        r: board.isCommanderAlive("r"),
        b: board.isCommanderAlive("b"),
      },
      moves_remaining: {
        r: board.getMovesForPlayer("r").length,
        b: board.getMovesForPlayer("b").length,
      },
    };
  }
  throw new Error(`unknown rule action: ${String(request.action)}`);
}

module.exports = {
  PieceRank,
  GameResult,
  Piece,
  Graph,
  RailroadNetwork,
  BoardValidator,
  BoardGenerator,
  BoardShuffler,
  Board,
  BUNKER_SQUARES,
  HEADQUARTER_SQUARE_GROUP,
  HEADQUARTER_SQUARES,
  RAIL_LINES,
  dispatch,
};

import { BoardSquarePieceMap, BUNKER_SQUARES, RAIL_LINES } from './BoardConstants';
import { BoardGenerator } from './BoardGenerator';
import { BoardShuffler } from './BoardShuffler';
import { BoardValidator } from './BoardValidator';
import {
  GameResult,
  Piece,
  PieceRank,
} from './Piece';
import { RailroadNetwork } from './RailroadNetwork';

export interface PlayerMove {
  type: string;
  startSquare: string;
  endSquare: string;
}

export interface SwapMove {
  type: string;
  startSquare: string;
  endSquare: string;
}

const MOVE_TYPE_BY_COMPARE_RESULT: Record<GameResult, string> = {
  [GameResult.WIN]: 'capture',
  [GameResult.DRAW]: 'equal',
  [GameResult.LOSE]: 'dies'
};

export class Board {
  boardState: BoardSquarePieceMap;
  nodeKeys: string[];
  railroadNetwork: RailroadNetwork;
  boardValidator: BoardValidator;
  boardShuffler: BoardShuffler;

  /*
  Create new board to store pieces
  */
  constructor() {
    const boardGenerator = new BoardGenerator();
    this.boardState = boardGenerator.generateBoard();

    this.boardValidator = new BoardValidator();
    const valid: boolean = this.boardValidator.validateBoard(this.boardState);
    if (!valid) {
      throw new Error("Invalid board state");
    }
    this.nodeKeys = Object.keys(this.boardState);
    this.railroadNetwork = new RailroadNetwork(RAIL_LINES);
    this.boardShuffler = new BoardShuffler(this.boardValidator);
  }

  /**
   * Get all the valid/safe moves a player can make.
   * Returns an array of move objects on success or an empty array on failure.
   */
  getMovesForPlayer(playerColor: string): PlayerMove[] {
    if (!playerColor || typeof playerColor !== 'string') {
      throw new Error('Player color must be a non-empty string');
    }
    
    const playerSquares: string[] = this.getAllPlayerSquares(this.boardState, playerColor);
    return playerSquares
      .filter((square) => this.boardState[square]?.isMovable())
      .map((square) => this.getValidMoves(square, this.boardState))
      .flat();
  };

  /**
   * Get all possible swaps during the setup phase
   */
  getSwapForAll(): SwapMove[] {
    return Object.keys(this.boardState)
      .filter((square) => this.boardState[square])
      .map((square) => this.getSwapMoves(square, this.boardState))
      .flat();
  };

  /**
   * Swaps pieces on startSquare and endSqaure. Assumes both squares have pieces.
   */
  swapPieces(startSquare: string, endSquare: string): void {
    if (!startSquare || !endSquare) {
      throw new Error('Both start and end squares must be provided');
    }
    
    if (!this.boardState[startSquare] || !this.boardState[endSquare]) {
      throw new Error('Both squares must contain pieces to swap');
    }
    
    const startPiece = this.boardState[startSquare];
    const endPiece = this.boardState[endSquare];

    this.boardState[startSquare] = endPiece;
    this.boardState[endSquare] = startPiece;
  }

  /**
   * Randomly rearrange one player's pieces among their own squares for the setup
   * phase. See BoardShuffler for the placement rules this enforces.
   */
  shufflePlayerPieces(playerColor: string): void {
    const squares = this.getAllPlayerSquares(this.boardState, playerColor);
    this.boardShuffler.shuffle(this.boardState, squares);
  }

  getPieceAtSquare(currentSquare: string): Piece | null {
    if (!currentSquare || typeof currentSquare !== 'string') {
      throw new Error('Square location must be a non-empty string');
    }
    
    return this.boardState[currentSquare] || null;
  }

  placePieceAtSquare(currentSquare: string, piece: Piece): void {
    if (!currentSquare || typeof currentSquare !== 'string') {
      throw new Error('Square location must be a non-empty string');
    }
    
    if (!piece || !(piece instanceof Piece)) {
      throw new Error('Piece must be a valid Piece instance');
    }
    
    this.boardState[currentSquare] = piece;
  }

  /**
   * Clear this square
   */
  setSquareEmpty(squareLocation: string): void {
    if (!squareLocation || typeof squareLocation !== 'string') {
      throw new Error('Square location must be a non-empty string');
    }
    
    this.boardState[squareLocation] = null;
  }

  /**
   * Determine if a player's flag is captured or not
   */
  isPlayerFlagCaptured(playerColor: string): boolean {
    return !this.doesPieceExist(this.boardState, playerColor, PieceRank.FLAG);
  };

  /**
   * Determine if a player still has the commander (rank 1). If not, then reveal the flag
   */
  isCommanderAlive(playerColor: string): boolean {
    return this.doesPieceExist(this.boardState, playerColor, PieceRank.COMMANDER);
  };

  doesPieceExist(boardState: { [key: string]: Piece | null }, playerColor: string, pieceRank: PieceRank): boolean {
    const playerSquares = this.getAllPlayerSquares(boardState, playerColor);
    return playerSquares.some((square) => boardState[square]!.getRank() === pieceRank);
  }

  // Evaluate an attack from startSquare to destination
  // Assumes both locations have pieces
  evaluateMove(startSquare: string, destination: string): PlayerMove | null {
    const board = this.boardState;
    // One or more squares is missing pieces
    if (board[startSquare] == null || board[destination] == null) {
      return null;
    }
    const playerPiece = board[startSquare]!;
    const enemyPiece = board[destination]!;
    const compareResult: GameResult = playerPiece.compareRank(enemyPiece);

    const moveType = MOVE_TYPE_BY_COMPARE_RESULT[compareResult];
    if (moveType) {
      return {
        type: moveType,
        startSquare: startSquare,
        endSquare: destination
      };
    }

    return null;
  }

  /**
   * Get all the moves a piece can make (with the exception of flags, landmines, etc.)
   * Returns an array of move objects on success or an empty array on failure.
   */
  getValidMoves(square: string, board: BoardSquarePieceMap): PlayerMove[] {
    const piece = board[square]!;
    const isPieceEngineer = (piece.getRank() === PieceRank.ENGINEER);
    const reachableSquares = this.railroadNetwork.getReachableSquares(square, isPieceEngineer, board);
    const self = this;
    return Array.from(reachableSquares)
      .filter((destination) => square != destination) // skip itself
      .map(function (destination) {
        const moveType = self.getMoveType(piece, board, destination);
        if (moveType != null) {
          return {
            type: moveType,
            startSquare: square,
            endSquare: destination
          };
        }
        return null;
      })
      .filter((move): move is PlayerMove => move !== null);
  }

  /*
    Piece placement must follow certain rules
    1. Flags can only be placed in one of two headquartes
    2. Landmines in back two rows
    3. Bomb cannot be in the front row

    A swap moves two pieces at once, so both directions need checking: the piece
    at `square` must be legal at `destination`, AND the piece currently at
    `destination` must be legal at `square`. Checking only the first direction
    would let a swap smuggle a restricted piece into an illegal square by trading 
    it with an unrestricted one.
  */
  getSwapMoves(square: string, board: BoardSquarePieceMap): SwapMove[] {
    const piece: Piece = board[square]!;
    return this.getAllPlayerSquares(board, piece.getPieceColor())
      .filter((destination) => square !== destination) // skip itself
      .filter((destination) => this.boardValidator.isDestinationPositionValid(piece.getRank(), square, destination))
      .filter((destination) => {
        const destPiece = board[destination];
        return !destPiece || this.boardValidator.isDestinationPositionValid(destPiece.getRank(), destination, square);
      })
      .map((destination) => ({
        type: 'swap',
        startSquare: square,
        endSquare: destination
      }));
  }

  getAllPlayerSquares(boardState: BoardSquarePieceMap, playerColor: string): string[] {
    return Object.keys(boardState)
      .filter((square) => boardState[square])
      .filter((square) => boardState[square]!.getPieceColor() === playerColor[0])
  }

  getMoveType(piece: Piece, board: BoardSquarePieceMap, destination: string): string | null {
    if (board[destination] === null) {
      // If destination square is empty, move piece without attacking
      return 'move';
    } else if (this.isSquareAttackable(piece, board, destination)) {
      return 'attack';
    }
    return null;
  }

  // Check if destination square is occupied by an enemy and the enemy is not in a bunker
  isSquareAttackable(piece: Piece, board: BoardSquarePieceMap, destination: string): boolean {
    return board[destination]!.getPieceColor() !== piece.getPieceColor() &&
      BUNKER_SQUARES.includes(destination) === false;
  }
}








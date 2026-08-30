import { BoardSquarePieceMap, FRONT_ROW_SQUARES, HEADQUARTER_SQUARES, PLAYER1_BACK_ROW_RAIL, PLAYER2_BACK_ROW_RAIL } from './BoardConstants';
import { BoardValidator } from './BoardValidator';
import { Piece, PieceRank } from './Piece';

export class BoardShuffler {
  boardValidator: BoardValidator;

  constructor(boardValidator: BoardValidator) {
    this.boardValidator = boardValidator;
  }

  /**
   * Randomly rearrange one player's pieces among their own squares for setup,
   * keeping the board valid and avoiding placements no rational player would make.
   */
  shuffle(boardState: BoardSquarePieceMap, squares: string[]): void {
    const remainingPieces = squares.map((square) => boardState[square]!);

    const remainingSquares = new Set(squares);
    const assignment: BoardSquarePieceMap = {};

    // Assign the most constrained pieces/squares first, so unrestricted pieces
    // are left to fill whatever pieces and squares remain.
    const placeGroup = (matchesPiece: (piece: Piece) => boolean, candidateSquares: string[], maxCount: number = Infinity): void => {
      const eligibleSquares = this.shuffleCopy(candidateSquares.filter((square) => remainingSquares.has(square)));
      // Cap to available squares - a matcher can match more pieces than there are squares left.
      const groupPieces = this.shuffleCopy(remainingPieces.filter(matchesPiece)).slice(0, Math.min(eligibleSquares.length, maxCount));

      groupPieces.forEach((piece, i) => {
        const square = eligibleSquares[i];
        assignment[square] = piece;
        remainingSquares.delete(square);
        remainingPieces.splice(remainingPieces.indexOf(piece), 1);
      });
    };

    const hqSquares = squares.filter((square) => HEADQUARTER_SQUARES.includes(square));
    // Row in front of HQ - reserving one landmine here guarantees not all 3 end up on the HQ row.
    const frontGuardRow = squares.filter((square) => PLAYER1_BACK_ROW_RAIL.includes(square) || PLAYER2_BACK_ROW_RAIL.includes(square));

    placeGroup((piece) => piece.getRank() === PieceRank.FLAG, hqSquares);
    placeGroup((piece) => piece.getRank() === PieceRank.LANDMINE, frontGuardRow, 1);
    // HQ squares have zero legal moves for the rest of the game, so the non-flag one
    // should only ever hold a landmine or a lieutenant - nothing worth losing that way.
    placeGroup((piece) => piece.getRank() === PieceRank.LANDMINE || piece.getRank() === PieceRank.LIEUTENANT, hqSquares);

    // HQ row's other 3 squares: near-flag corner is a fair reserve position; center and
    // far corner aren't (a piece there either gets stuck, or freeing it telegraphs which
    // HQ is real). Center is worse still (exposes both HQ squares at once), hence the
    // tighter limit. HQ squares are always 'b'/'d' columns, so center/corners are built
    // directly rather than derived by elimination over the row.
    const flagSquare = hqSquares.find((square) => assignment[square]?.getRank() === PieceRank.FLAG)!;
    const hqRow = this.boardValidator.getBoardSquareRow(flagSquare);
    const centerSquare = `c${hqRow}`;
    const nearFlagColumn = flagSquare[0] === 'b' ? 'a' : 'e';
    const farFlagColumn = nearFlagColumn === 'a' ? 'e' : 'a';
    const nearFlagCorner = `${nearFlagColumn}${hqRow}`;
    const farFlagCorner = `${farFlagColumn}${hqRow}`;

    placeGroup((piece) => piece.getRank() === PieceRank.LANDMINE || piece.getRank() === PieceRank.LIEUTENANT, [centerSquare]);
    placeGroup((piece) => [PieceRank.CAPTAIN, PieceRank.LIEUTENANT, PieceRank.LANDMINE].includes(piece.getRank()), [farFlagCorner]);
    // Engineer excluded too: deploying it later means first moving it onto a railroad,
    // which costs a turn and exposes the flag the same way extracting any other piece does
    placeGroup((piece) => ![PieceRank.FLAG, PieceRank.COMMANDER, PieceRank.GENERAL, PieceRank.BOMB, PieceRank.ENGINEER].includes(piece.getRank()), [nearFlagCorner]);

    // a/c/e front-row squares have a direct edge to the enemy's front row (only those
    // 3 column pairs, per Graph.ts) - earliest contact point, bad for Engineer (needed
    // alive for mines later). Ordinary combat piece specifically, not just "not
    // Engineer" - Landmine/Bomb are still unplaced and have their own row rules.
    const exposedFrontRow = squares.filter((square) => FRONT_ROW_SQUARES.includes(square) && ['a', 'c', 'e'].includes(square[0]));
    placeGroup((piece) => piece.getRank() >= PieceRank.COMMANDER && piece.getRank() <= PieceRank.LIEUTENANT, exposedFrontRow);

    placeGroup((piece) => piece.getRank() === PieceRank.LANDMINE, squares.filter((square) => this.boardValidator.isValidLandminePosition(this.boardValidator.getBoardSquareRow(square))));
    // Bomb is kept off the whole HQ row, not just the front row - once it's gone the row behind it is exposed
    placeGroup((piece) => piece.getRank() === PieceRank.BOMB, squares.filter((square) => this.boardValidator.isValidBombPosition(this.boardValidator.getBoardSquareRow(square)) && this.boardValidator.getBoardSquareRow(square) !== hqRow));

    // Remaining pieces have no placement restrictions
    placeGroup(() => true, squares);

    squares.forEach((square) => {
      boardState[square] = assignment[square];
    });
  }

  // Returns a new array containing the same elements in random order
  private shuffleCopy<T>(items: T[]): T[] {
    const copy = items.slice();
    for (let i = copy.length - 1; i > 0; i--) {
      const j = Math.floor(Math.random() * (i + 1));
      [copy[i], copy[j]] = [copy[j], copy[i]];
    }
    return copy;
  }
}

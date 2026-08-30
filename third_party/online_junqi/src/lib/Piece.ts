export enum PieceRank {
  BOMB = 0,
  COMMANDER = 1,
  GENERAL = 2,
  MAJOR_GENERAL = 3,
  BRIGADIER_GENERAL = 4,
  COLONEL = 5,
  MAJOR = 6,
  CAPTAIN = 7,
  LIEUTENANT = 8,
  ENGINEER = 9,
  LANDMINE = 10,
  FLAG = 11
}

export enum GameResult {
  WIN = 1,
  DRAW = 0,
  LOSE = -1
}

export class Piece {
  colorChar: string;
  rank: PieceRank;

  constructor(colorChar: string, rank: PieceRank) {
    this.colorChar = colorChar;
    this.rank = rank;
  }

  getPieceColor(): string {
    return this.colorChar;
  }

  getRank(): PieceRank {
    return this.rank;
  }

  isMovable(): boolean {
    return this.rank !== PieceRank.LANDMINE && this.rank !== PieceRank.FLAG;
  }

  // compares this piece's rank with another piece's rank
  // returns  1 if this piece wins
  //          0 if they draw
  //          -1 if this piece loses
  compareRank(otherPiece: Piece): GameResult {
    const rank1 = this.rank;
    const rank2 = otherPiece.rank;

    // the opponent is a bomb, which destroys any piece that hits it
    if (rank1 === PieceRank.BOMB || rank2 === PieceRank.BOMB) {
      return GameResult.DRAW;
    }

    // the opponent is a flag, which any of your pieces can capture
    if (rank2 === PieceRank.FLAG) {
      return GameResult.WIN;
    }

    // the opponent is a landmine, which only the engineer can disable
    if (rank2 === PieceRank.LANDMINE) {
      // engineer disables landmine
      if (rank1 === PieceRank.ENGINEER) {
        return GameResult.WIN;
      } else {
        // no other piece can disable it
        return GameResult.LOSE;
      }
    }

    // 2 regular pieces
    // the lower the number, the higher the rank (1 beats 2, 2 beats 3, etc.)
    if (rank1 >= PieceRank.COMMANDER && rank1 <= PieceRank.ENGINEER && 
        rank2 >= PieceRank.COMMANDER && rank2 <= PieceRank.ENGINEER) {
      if (rank1 < rank2) {
        return GameResult.WIN;
      } else if (rank1 === rank2) {
        return GameResult.DRAW;
      } else {
        return GameResult.LOSE;
      }
    }

    // should not reach this point
    return GameResult.DRAW;
  }
}

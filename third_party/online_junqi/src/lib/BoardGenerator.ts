import {
  Piece,
  PieceRank,
} from './Piece';

export class BoardGenerator {
    generateBoard() {
        return {
            a12: new Piece('r', PieceRank.LANDMINE), b12: new Piece('r', PieceRank.FLAG), c12: new Piece('r', PieceRank.LANDMINE), d12: new Piece('r', PieceRank.BOMB), e12: new Piece('r', PieceRank.BOMB),
            a11: new Piece('r', PieceRank.ENGINEER), b11: new Piece('r', PieceRank.LANDMINE), c11: new Piece('r', PieceRank.ENGINEER), d11: new Piece('r', PieceRank.GENERAL), e11: new Piece('r', PieceRank.ENGINEER),
            a10: new Piece('r', PieceRank.MAJOR), b10: null, c10: new Piece('r', PieceRank.MAJOR), d10: null, e10: new Piece('r', PieceRank.COLONEL),
            a9: new Piece('r', PieceRank.COMMANDER), b9: new Piece('r', PieceRank.MAJOR_GENERAL), c9: null, d9: new Piece('r', PieceRank.MAJOR_GENERAL), e9: new Piece('r', PieceRank.COLONEL),
            a8: new Piece('r', PieceRank.LIEUTENANT), b8: null, c8: new Piece('r', PieceRank.LIEUTENANT), d8: null, e8: new Piece('r', PieceRank.LIEUTENANT),
            a7: new Piece('r', PieceRank.CAPTAIN), b7: new Piece('r', PieceRank.CAPTAIN), c7: new Piece('r', PieceRank.CAPTAIN), d7: new Piece('r', PieceRank.BRIGADIER_GENERAL), e7: new Piece('r', PieceRank.BRIGADIER_GENERAL),
            a6: new Piece('b', PieceRank.CAPTAIN), b6: new Piece('b', PieceRank.GENERAL), c6: new Piece('b', PieceRank.CAPTAIN), d6: new Piece('b', PieceRank.MAJOR_GENERAL), e6: new Piece('b', PieceRank.CAPTAIN),
            a5: new Piece('b', PieceRank.BRIGADIER_GENERAL), b5: null, c5: new Piece('b', PieceRank.COLONEL), d5: null, e5: new Piece('b', PieceRank.BRIGADIER_GENERAL),
            a4: new Piece('b', PieceRank.LIEUTENANT), b4: new Piece('b', PieceRank.MAJOR_GENERAL), c4: null, d4: new Piece('b', PieceRank.COMMANDER), e4: new Piece('b', PieceRank.LIEUTENANT),
            a3: new Piece('b', PieceRank.MAJOR), b3: null, c3: new Piece('b', PieceRank.LIEUTENANT), d3: null, e3: new Piece('b', PieceRank.COLONEL),
            a2: new Piece('b', PieceRank.ENGINEER), b2: new Piece('b', PieceRank.LANDMINE), c2: new Piece('b', PieceRank.LANDMINE), d2: new Piece('b', PieceRank.ENGINEER), e2: new Piece('b', PieceRank.ENGINEER),
            a1: new Piece('b', PieceRank.BOMB), b1: new Piece('b', PieceRank.FLAG), c1: new Piece('b', PieceRank.LANDMINE), d1: new Piece('b', PieceRank.BOMB), e1: new Piece('b', PieceRank.MAJOR)
        }
    }
}

import { describe, it } from 'node:test';
import { strict as assert } from 'assert';
import { Piece, PieceRank, GameResult } from '../src/lib/Piece';

describe('Piece', function () {
  describe('#getters()', function () {
    it('should return correct color and rank', function () {
      var piece = new Piece('r', PieceRank.FLAG);
      assert.equal(piece.getPieceColor(), "r");
      assert.equal(piece.getRank(), PieceRank.FLAG);
    });
  });

  describe('#bombAttacksLandmine()', function () {
    it('Bomb and landmine both disappear', function () {
      var bombPiece = new Piece('r', PieceRank.BOMB);
      var landminePiece = new Piece('b', PieceRank.LANDMINE);

      var result = bombPiece.compareRank(landminePiece);
      assert.equal(result, GameResult.DRAW);
    });
  });

  describe('#engineerAttacksLandmine()', function () {
    it('Engineer disables landmine', function () {
      var engineerPiece = new Piece('r', PieceRank.ENGINEER);
      var landminePiece = new Piece('b', PieceRank.LANDMINE);

      var result = engineerPiece.compareRank(landminePiece);
      assert.equal(result, GameResult.WIN);
    });
  });

  describe('#regularUnitAttacksLandmine()', function () {
    it('Unit disappears', function () {
      var commanderPiece = new Piece('r', PieceRank.COMMANDER);
      var landminePiece = new Piece('b', PieceRank.LANDMINE);

      var result = commanderPiece.compareRank(landminePiece);
      assert.equal(result, GameResult.LOSE);
    });
  });

  describe('#weakerUnitAttacksStrongerUnit()', function () {
    it('Weaker unit loses', function () {
      var rank5Piece = new Piece('r', PieceRank.COLONEL);
      var rank3Piece = new Piece('b', PieceRank.MAJOR_GENERAL);

      var result = rank5Piece.compareRank(rank3Piece);
      assert.equal(result, GameResult.LOSE);
    });
  });

  describe('#strongerUnitAttacksWeakerUnit()', function () {
    it('Stronger unit wins', function () {
      var rank2Piece = new Piece('r', PieceRank.GENERAL);
      var rank4Piece = new Piece('b', PieceRank.BRIGADIER_GENERAL);

      var result = rank2Piece.compareRank(rank4Piece);
      assert.equal(result, GameResult.WIN);
    });
  });

  describe('#unitAttacksEqualRank()', function () {
    it('Both pieces disappear', function () {
      var rank2RedPiece = new Piece('r', PieceRank.GENERAL);
      var rank2BluePiece = new Piece('b', PieceRank.GENERAL);

      var result = rank2RedPiece.compareRank(rank2BluePiece);
      assert.equal(result, GameResult.DRAW);
    });
  });

  describe('#unitTakesFlag()', function () {
    it('Unit always takes flag', function () {
      var rank2RedPiece = new Piece('r', PieceRank.GENERAL);
      var flagPiece = new Piece('b', PieceRank.FLAG);

      var result = rank2RedPiece.compareRank(flagPiece);
      assert.equal(result, GameResult.WIN);
    });
  });
});

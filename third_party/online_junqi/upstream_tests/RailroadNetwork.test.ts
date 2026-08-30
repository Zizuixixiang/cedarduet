import { describe, it } from 'node:test';
import { strict as assert } from 'assert';
import { Piece, PieceRank } from '../src/lib/Piece';
import { RailroadNetwork } from '../src/lib/RailroadNetwork';

describe('RailroadNetwork', function () {
  const RAIL1 = ['a2', 'a3', 'a4', 'a5', 'a6'];
  const RAIL2 = ['a2', 'b2', 'c2', 'd2', 'e2'];
  var railroadNetwork = new RailroadNetwork([RAIL1, RAIL2]);

  describe('#getReachableSquaresEngineer()', function () {
    it('Get reachable squares', function () {
      var currentSquare = "e2"
      var boardState = {"e2": new Piece("r", PieceRank.ENGINEER), "d2": null, "c2": null, "b2": null,
       "a2": null, "a3": null, "a4": null, "a5": new Piece("_", PieceRank.FLAG), "a6": null};
      var isPieceEngineer = true;

      var reachableSquares = railroadNetwork.getReachableSquares(currentSquare, isPieceEngineer, boardState);
      assert.equal(reachableSquares.size, 11);

      // modeled in graph class
      const adjacentNeighborsNotOnRailroad =  ['e1', 'e3', 'd3'];
      const neighborsOnRailroad = ['e2', 'd2', 'c2', 'b2', 'a2', 'a3', 'a4', 'a5'];
      const expectedNeighbors = neighborsOnRailroad.concat(adjacentNeighborsNotOnRailroad);
      expectedNeighbors.forEach(function(square) {
        assert.equal(reachableSquares.has(square), true);
      })
    });
  });

  describe('#getReachableSquaresNotEngineer()', function () {
    it('Get reachable squares', function () {
      var currentSquare = "e2"
      var boardState = {"e2": new Piece("r", PieceRank.ENGINEER), "d2": null, "c2": null, "b2": null,
       "a2": null, "a3": null, "a4": null, "a5": new Piece("_", PieceRank.FLAG), "a6": null};
      var isPieceEngineer = false;

      var reachableSquares = railroadNetwork.getReachableSquares(currentSquare, isPieceEngineer, boardState);
      assert.equal(reachableSquares.size, 8);

      // modeled in graph class
      const adjacentNeighborsNotOnRailroad =  ['e1', 'e3', 'd3'];
      const neighborsOnRailroad = ['e2', 'd2', 'c2', 'b2', 'a2'];
      const expectedNeighbors = neighborsOnRailroad.concat(adjacentNeighborsNotOnRailroad);
      expectedNeighbors.forEach(function(square) {
        assert.equal(reachableSquares.has(square), true);
      })
    });
  });


  describe('#getAllRailroadsFromSquareIntersection()', function () {
    it('Return all railroads from square as a flat list', function () {
      var singleRail = railroadNetwork.getAllRailroadsFromSquare("a2");
      assert.equal(singleRail.size, 9);
    });
  });

  describe('#getAllRailroadsFromSquareNotIntersection()', function () {
    it('Return all railroads from square as a flat list', function () {
      var singleRail = railroadNetwork.getAllRailroadsFromSquare("a4");
      assert.equal(singleRail.size, 5);
    });
  });

  describe('#isOnRail()', function () {
    it('Check if the square is on any railroad', function () {
      var result = railroadNetwork.isOnRail("a4");
      assert.equal(result, true);
    });
  });

  describe('#isNotOnRail()', function () {
    it('Check if the square is not on any railroad', function () {
      var result = railroadNetwork.isOnRail("e6");
      assert.equal(result, false);
    });
  });
});

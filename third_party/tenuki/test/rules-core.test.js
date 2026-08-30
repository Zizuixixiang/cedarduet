import assert from "node:assert/strict";
import test from "node:test";

import {HostGame, KOMI, KO_RULE, SCORING} from "../host-game.js";


function playSequence(game, points) {
  points.forEach(([row, col], index) => {
    assert.equal(game.playAt(row, col), true, `move ${index + 1} at ${row},${col}`);
  });
}


test("vendored Tenuki core keeps fixed production profile", () => {
  assert.equal(KO_RULE, "positional-superko");
  assert.equal(SCORING, "area");
  assert.equal(KOMI, 7.5);
});


test("upstream ko sequence rejects immediate recapture", () => {
  const game = new HostGame();
  playSequence(game, [
    [3, 3], [3, 2], [4, 2], [4, 1],
    [5, 3], [5, 2], [4, 4], [4, 3],
  ]);
  assert.equal(game.intersectionAt(4, 2).isEmpty(), true);
  assert.equal(game.isIllegalAt(4, 2), true);
  assert.equal(game.playAt(4, 2), false);
});


test("upstream positional-superko cycle remains illegal", () => {
  const game = new HostGame();
  playSequence(game, [
    [0, 3], [0, 4], [1, 3], [1, 4], [1, 2], [2, 4],
    [1, 1], [2, 3], [2, 2], [3, 3], [3, 2], [4, 3],
    [3, 1], [4, 2], [3, 0], [4, 1], [0, 8], [4, 0],
    [1, 8], [0, 1], [2, 8], [1, 0], [3, 8], [2, 0],
    [4, 8], [0, 2], [0, 0],
  ]);
  assert.equal(game.isIllegalAt(0, 1), true);
  assert.equal(game.playAt(0, 1), false);
});


test("Tenuki rejects suicide and captures an entire chain", () => {
  const suicide = new HostGame();
  playSequence(suicide, [
    [0, 1], [5, 5], [1, 0], [5, 6],
    [1, 2], [5, 7], [2, 1],
  ]);
  assert.equal(suicide.isIllegalAt(1, 1), true);

  const capture = new HostGame();
  playSequence(capture, [
    [1, 0], [0, 0], [1, 1], [0, 1], [0, 2],
  ]);
  assert.deepEqual(
    capture.currentState().capturedPositions
      .map(({y, x}) => [y, x])
      .sort((left, right) => left[0] - right[0] || left[1] - right[1]),
    [[0, 0], [0, 1]],
  );
});


test("upstream area scorer and group dead marking apply fixed komi", () => {
  const game = new HostGame();
  for (let row = 0; row < 19; row += 1) {
    assert.equal(game.playAt(row, 9), true);
    assert.equal(game.playAt(row, 8), true);
  }
  assert.equal(game.pass(), true);
  assert.equal(game.pass(), true);
  assert.equal(game.isOver(), true);
  assert.deepEqual(game.score(), {black: 190, white: 178.5});

  const dead = new HostGame();
  playSequence(dead, [[0, 9], [0, 8], [1, 9]]);
  assert.equal(dead.pass(), true);
  assert.equal(dead.pass(), true);
  assert.equal(dead.toggleDeadAt(0, 9), true);
  assert.equal(dead.deadStones().length, 2);
  assert.equal(dead.score().white, 368.5);
});

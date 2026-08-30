import { describe, it } from 'node:test';
import { strict as assert } from 'assert';
import { BoardValidator } from '../src/lib/BoardValidator';

describe('BoardValidator', function () {
    var boardValidator = new BoardValidator();

    describe('#getBoardSquareRow()', function () {
        it('getBoardSquareRow', function () {
            var row = boardValidator.getBoardSquareRow('a12');
            assert.equal(row, 12);
        });
    });

    describe('#validPosition()', function () {
        it('valid bomb position', function () {
            assert.equal(boardValidator.isValidBombPosition(1), true);
            assert.equal(boardValidator.isValidBombPosition(6), false);
        });

        it('valid landmine position', function () {
            assert.equal(boardValidator.isValidLandminePosition(1), true);
            assert.equal(boardValidator.isValidLandminePosition(2), true);
            assert.equal(boardValidator.isValidLandminePosition(3), false);
        });

        it('valid flag swap', function() {
            assert.equal(boardValidator.isValidFlagPosition('b12', 'd12'), true);
            assert.equal(boardValidator.isValidFlagPosition('b12', 'd1'), false);
        });
    });
});

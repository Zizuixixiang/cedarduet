# online-junqi vendor notice

- Upstream: https://github.com/samuelyuan/online-junqi
- Fixed revision: `f5ba2e8cedaa7e1dc3975349d5bbe097f2d5e13a`
- License: MIT; the upstream license is preserved as `LICENSE`.
- Upstream copyright: Copyright (c) 2016 Samuel Yuan.

## Files preserved from upstream

The following rule-core sources are preserved under `src/lib/` with their
upstream contents. `BoardConstants.ts`, `BoardValidator.ts`, and
`BoardGenerator.ts` only gained a final newline when vendored; there are no
semantic source changes.

- `BoardConstants.ts`
- `Piece.ts`
- `Graph.ts`
- `RailroadNetwork.ts`
- `BoardValidator.ts`
- `BoardGenerator.ts`
- `BoardShuffler.ts`
- `Board.ts`
- `Game.ts`

Relevant upstream tests are preserved under `upstream_tests/`:

- `Piece.test.ts`
- `Graph.test.ts`
- `RailroadNetwork.test.ts`
- `BoardValidator.test.ts`
- `Board.test.ts`

## Runtime adaptation

`runtime/core.js` is a CommonJS, type-erased executable build of the files
above because CedarDuet's production runtime ships Node.js but does not install
the upstream TypeScript development toolchain. The class/method structure and
rule branches are retained. In particular:

- combat delegates to the adapted upstream `Piece.compareRank`;
- road/camp/headquarters topology delegates to the adapted upstream `Graph`;
- railroad reachability and Engineer turning delegate to the adapted upstream
  `RailroadNetwork.getReachableSquares`;
- setup legality and shuffling delegate to the adapted upstream
  `BoardValidator`, `Board.getSwapMoves`, and `BoardShuffler`;
- move generation delegates to the adapted upstream `Board.getMovesForPlayer`;
- applying `move`, `capture`, `dies`, and `equal` follows the switch in upstream
  `Game.evaluateMoveAndModifyBoard`.

Adapter-only additions are JSON serialization/restoration, persisted-board
shape checks, setup inventory checks, a small request dispatcher, and the
`bridge.js` stdin/stdout wrapper. `upstream_tests/native-loader.mjs` runs the
unchanged relevant upstream `.test.ts` files against the executable runtime
build (stripping only TypeScript non-null assertion tokens at load time).
`upstream_tests/runtime.test.js` adds a smaller mechanical parity suite for
direct CommonJS checks without TypeScript tooling.

The upstream browser UI, images, socket routes, HTTP routes, session store, and
server entrypoint are intentionally not vendored.

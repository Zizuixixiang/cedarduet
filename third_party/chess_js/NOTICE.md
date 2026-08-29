# chess.js vendored notice

- Upstream: https://github.com/jhlywa/chess.js
- Source: https://github.com/jhlywa/chess.js/blob/v0.10.3/chess.js
- Version: `v0.10.3`
- Vendored source Git blob: `b9e7fd26b3266b652cf24d2ed9694cdf6ec738e3`
- Retrieved: 2026-08-29
- License: BSD-2-Clause; see `LICENSE` in this directory.
- Local use: `chess.js` is vendored without runtime package or network access.
  CedarDuet's `bridge.js` and Python game adapter are separate integration code.
  Each rules request runs in a short-lived Node.js process and reconstructs the
  game from its starting FEN plus complete UCI move history, preserving the
  engine's threefold-repetition detection.

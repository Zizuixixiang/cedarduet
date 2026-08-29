# xiangqi.js vendored notice

- Upstream: https://github.com/lengyanyu258/xiangqi.js
- Revision: `f9019ac2303d4b80ef0b82fd0515bfb55a80a62b` (`dev`)
- Retrieved: 2026-08-29
- License: BSD-2-Clause; see `LICENSE` in this directory.
- Local use: the unmodified rule source is loaded by `bridge.js`; CedarDuet's
  bridge and game plugin remain separate integration code. The Python adapter
  runs one short-lived bridge process per rules request and keeps no resident
  Node.js worker.

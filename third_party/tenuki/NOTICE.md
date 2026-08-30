# Tenuki rules-core vendored notice

- Upstream: https://github.com/aprescott/tenuki
- Upstream version: `0.3.1`
- Source commit: `aeedb4cd39d73242e49490aea359118ea5a4df23`
- Retrieved: 2026-08-30
- License: MIT; see `LICENSE` in this directory.
- Vendored scope: `BoardState`, `Ruleset`, `Scorer`, `Region`, `EyePoint`,
  `Intersection`, Zobrist hashing, and the rule-core portions of `utils`.
  Tenuki renderers, client code, styles, examples, and other UI sources are not
  vendored.
- Upstream source changes: relative ESM imports have explicit `.js` suffixes
  for current Node.js; `src/utils.js` retains only `flatMap` and `unique`, the
  two helpers used by this rules/scoring subset. `src/zobrist.js` checks cache
  key presence instead of truthiness so a valid random hash value of zero stays
  stable during replay. Other vendored upstream files are unchanged.
- Local integration: `host-game.js` supplies the minimal Game-shaped host used
  by the unchanged rules/scoring objects. `bridge.js` is a stateless JSON/stdio
  adapter and is not upstream Tenuki code.
- Fixed CedarDuet profile: 19×19, no handicap, black first, suicide forbidden,
  positional superko, Chinese area scoring, komi 7.5.

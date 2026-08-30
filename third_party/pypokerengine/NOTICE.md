# PyPokerEngine vendoring notice

Vendored from [ishikota/PyPokerEngine](https://github.com/ishikota/PyPokerEngine)
at commit `a52a048a15da276005eca4acae96fb6eeb4dc034`.

Upstream license: MIT, copyright (c) 2016 ishikota. See `LICENSE`.

Included scope: the `pypokerengine.engine` card/deck/table/player/pay-info,
betting round manager, action checker, data/message encoder, hand evaluator,
and game/side-pot evaluator used by the Texas Hold'em integration. API,
examples, player bots, documentation, and utility helpers are not vendored.

Local changes:

- imports are relocated under `third_party.pypokerengine`;
- heads-up postflop action starts at the big blind (the button/small blind
  still acts first preflop);
- the one-hand round manager preserves terminal cards and contribution data;
- the evaluator compares the complete best five-card rank, fixing upstream
  board-only ties and missing kicker comparisons;
- prize splitting preserves odd chips in deterministic left-of-button order;
- per-pot winner selection is exposed for public side-pot settlement audits;
- duplicate all-in caps no longer produce zero-value side pots, and folded
  players are removed from terminal-pot eligibility.

The application adapter additionally constrains authoritative legal actions
to modern no-limit minimum-raise, short-all-in, and raise-reopening rules. The
vendored RoundManager remains the runtime source of blind collection, betting
transactions, street progression/all-in runout, folding, and showdown; the
vendored GameEvaluator remains the runtime source of pot layering and awards.

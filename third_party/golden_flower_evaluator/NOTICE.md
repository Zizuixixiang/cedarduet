# Golden Flower evaluator provenance

This directory contains a minimal, integration-neutral adaptation of the
three-card hand evaluator from:

- Project: **Golden Flower (大模型炸金花)**
- Upstream: <https://github.com/luyao618/golden-flower>
- Reviewed revision: `35e74e929c5ed1856ade29a2b8340b19a5e8f014`
- Upstream files reviewed: `backend/app/engine/evaluator.py`, card/game models,
  `backend/tests/test_evaluator.py`, and `docs/PRD.md`
- Upstream license: MIT, copyright (c) 2025 Yao Lu

The CedarDuet copy keeps only classification and comparison behavior. Its API
and data model were rewritten around plain dictionaries so none of the upstream
FastAPI, database, LLM, or frontend stack is vendored. The preserved behavior
is: three-of-a-kind > straight flush > flush > straight > pair > high card;
A-2-3 is the smallest straight; Q-K-A is the largest; and suits do not break an
otherwise exact tie.

Local policy (round limit, betting, privacy, and the initiator-loses exact-tie
rule) is CedarDuet code and is not part of this vendored evaluator.

## Rule cross-checks for the CedarDuet variant

Reviewed on 2026-08-30 alongside the upstream tests and PRD:

- Golden Flower PRD: <https://github.com/luyao618/golden-flower/blob/main/docs/PRD.md>
- Zha Jin Hua rules reference (52 cards, A-2-3 through Q-K-A ordering):
  <https://www.fde29.com/webviewi18n/en-us/help/g58.html>
- Chinese rules reference (categories, A-2-3 low, betting cap examples):
  <https://www.zhumingwu.cn/2019/12/11/%E6%B8%B8%E6%88%8F-%E7%82%B8%E9%87%91%E8%8A%B1.html>

Regional rules disagree on optional exceptions and comparison handling. The
player-facing `Zhajinhua.rules_text` is therefore the canonical local version;
it deliberately disables the 2-3-5 exception and suit tie-breakers.

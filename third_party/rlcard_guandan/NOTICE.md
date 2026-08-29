# rlcard-guandan integration notice

- Upstream: <https://github.com/Choysang/rlcard-guandan>
- Release/tag: `v0.1.0` (`rlcard-guandan` package version `0.1.0`)
- Vendored commit: `42f83aa8d84c0047473e069244e07db0c02af420`
- License: MIT; the upstream `LICENSE` is copied unmodified beside this file
- Vendor date: 2026-08-30

## Upstream files copied

The following files are copied from that exact commit. Paths below are relative
to the upstream repository; the package is stored under this directory's
`guandan_rlcard/` subdirectory.

- `guandan_rlcard/__init__.py`, `guandan_rlcard/constants.py`
- `guandan_rlcard/game/__init__.py`
- `guandan_rlcard/game/action_compare.py`
- `guandan_rlcard/game/card_utils.py`
- `guandan_rlcard/game/dealer.py`
- `guandan_rlcard/game/game.py`
- `guandan_rlcard/game/hand_heuristics.py`
- `guandan_rlcard/game/judger.py`
- `guandan_rlcard/game/player.py`
- `guandan_rlcard/game/round.py`
- `guandan_rlcard/agents/__init__.py`
- `guandan_rlcard/agents/base_agent.py`
- `guandan_rlcard/baselines/__init__.py`
- `guandan_rlcard/baselines/random_agent.py`
- `guandan_rlcard/envs/__init__.py`
- `guandan_rlcard/envs/guandan_env.py`
- `tests/conftest.py`
- `tests/test_action_compare.py`
- `tests/test_action_generation.py`
- `tests/test_game_flow.py`
- `tests/test_tribute.py`
- `LICENSE`

The copied tests are kept at `upstream_tests/` and their contents are unchanged;
the test command supplies this vendor directory on `PYTHONPATH` so their original
top-level `guandan_rlcard` imports still run.

## Adaptations made to copied source

Rules remain in the upstream modules. The small source changes are:

- `guandan_rlcard/__init__.py`: alias the nested vendored package to the
  upstream top-level module name, preserving its absolute imports and RLCard
  registry entry point without rewriting every file.
- `guandan_rlcard/baselines/__init__.py`: expose only `RandomAgent`, because the
  native game-flow tests need it; entries for research/training agents that are
  intentionally not vendored were removed.
- `guandan_rlcard/game/player.py`: `execute_tribute` and `execute_back` return
  the actual removed `Card` object. Existing upstream callers ignore this return
  value; the interactive host uses it solely to preserve physical card identity.
- `guandan_rlcard/game/round.py`: add an opt-in `interactive_tribute` path that
  stages the existing upstream legal tribute/back actions across host turns.
  It calls the same `legal_tribute_actions`, `legal_back_actions`,
  `execute_tribute`, `execute_back`, `tribute_card_cmp`, `_check_resist`, and
  `_check_both_down` rules. The default synchronous agent path is unchanged.
- `guandan_rlcard/game/game.py`: pass the opt-in flag to `GuandanRound`, route a
  staged tribute action, refresh the upstream Judger after card exchange, and
  retain the already-computed deal summary for host projection. Default native
  behavior is unchanged.

`engine.py` and the package-level `../__init__.py` are Cedar-authored host
adapter files, not copied upstream files. The adapter serializes the upstream
`GuandanGame` object graph, attaches stable IDs to physical `Card` objects,
maps upstream raw legal actions to short `action_id` values, and creates safe
public/private host projections. It contains no alternate combination generator,
comparison engine, tribute rules, upgrade rules, or terminal rules.

## Intentionally excluded

Upstream GUI code, rule-based/research agents, DanZero/DMC/PPO training code,
LLM integrations, pretrained checkpoints/model weights, Docker/deployment
material, evaluation scripts, and other unrelated AI research assets are not
vendored.

The exact implemented rules and known simplifications are those documented by
upstream v0.1.0 in `docs/rules.md`; Cedar's in-game `rules_text` states the same
version and simplifications.

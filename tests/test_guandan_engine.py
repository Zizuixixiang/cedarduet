import functools
import random
import unittest
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from third_party.rlcard_guandan.engine import (
    GuandanEngine,
    UPSTREAM_SHA,
    _legal_pairs,
)
from third_party.rlcard_guandan.guandan_rlcard.baselines.random_agent import RandomAgent
from third_party.rlcard_guandan.guandan_rlcard.game.action_compare import get_gt_actions
from third_party.rlcard_guandan.guandan_rlcard.game.card_utils import (
    card_from_str,
    natural_card_cmp,
)
from third_party.rlcard_guandan.guandan_rlcard.game.game import GuandanGame
from third_party.rlcard_guandan.guandan_rlcard.game.judger import GuandanJudger


ROOT = Path(__file__).resolve().parents[1]


def make_hand(card_strs):
    cards = [card_from_str(value) for value in card_strs]
    return sorted(cards, key=functools.cmp_to_key(natural_card_cmp))


def upstream_game(seed=0, interactive=True):
    game = GuandanGame(interactive_tribute=interactive)
    game.perfect_info = False
    game.np_random = np.random.RandomState(seed)
    players = [RandomAgent(index, game.np_random) for index in range(4)]
    game.init_game(players)
    return game


class GuandanVendorProvenanceTests(unittest.TestCase):
    def test_real_upstream_source_license_and_notice_are_vendored(self):
        vendor = ROOT / "third_party" / "rlcard_guandan"
        self.assertIn("Copyright (c) 2025 Choysang", (vendor / "LICENSE").read_text())
        notice = (vendor / "NOTICE.md").read_text(encoding="utf-8")
        self.assertIn("https://github.com/Choysang/rlcard-guandan", notice)
        self.assertIn("v0.1.0", notice)
        self.assertIn(UPSTREAM_SHA, notice)
        self.assertNotIn("clean-room compatibility", notice)
        expected_core = {
            "action_compare.py", "card_utils.py", "dealer.py", "game.py",
            "hand_heuristics.py", "judger.py", "player.py", "round.py",
        }
        self.assertEqual(
            {path.name for path in (vendor / "guandan_rlcard" / "game").glob("*.py")}
            - {"__init__.py"},
            expected_core,
        )
        names = {path.name for path in vendor.rglob("*") if path.is_file()}
        self.assertFalse({"q_network.ckpt", "Dockerfile", "train.py"} & names)

    def test_host_adapter_contains_no_alternate_combo_or_comparison_engine(self):
        source = (ROOT / "third_party" / "rlcard_guandan" / "engine.py").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("def generate_combinations", source)
        self.assertNotIn("def can_beat", source)
        self.assertNotIn("def recognize", source)
        self.assertIn("game.step(raw)", source)
        self.assertIn("player.available_actions", source)


class GuandanUpstreamCoreAdapterTests(unittest.TestCase):
    players = ["p0", "p1", "p2", "p3"]

    def state(self, seed=7):
        return GuandanEngine.new_match(self.players, "p0", random.Random(seed))

    def test_two_upstream_decks_are_108_unique_cards_and_conserved(self):
        deck = GuandanEngine.build_deck()
        self.assertEqual(len(deck), 108)
        self.assertEqual(len({card["id"] for card in deck}), 108)
        state = self.state()
        self.assertEqual([len(state["hands"][player]) for player in self.players], [27] * 4)
        GuandanEngine.assert_card_conservation(state)
        action = GuandanEngine.legal_actions(state, "p0")[0]
        GuandanEngine.apply_action(state, "p0", action["action_id"])
        GuandanEngine.assert_card_conservation(state)

    def test_action_ids_are_a_lossless_map_of_upstream_authoritative_actions(self):
        state = self.state(3)
        game = GuandanEngine._load_game(state)
        pairs = _legal_pairs(state, game)
        legal = GuandanEngine.legal_actions(state, "p0")
        self.assertEqual([item for item, _raw in pairs], legal)
        self.assertTrue(all(item["action_id"].startswith("g_") for item in legal))
        self.assertTrue(all(set(item["card_ids"]) <= {
            card["id"] for card in state["hands"]["p0"]
        } for item in legal))
        chosen, raw = pairs[0]
        GuandanEngine.apply_action(state, "p0", chosen["action_id"])
        advanced = GuandanEngine._load_game(state)
        self.assertEqual(advanced.round.trace[-1][1], raw)
        with self.assertRaisesRegex(ValueError, "legal_actions"):
            GuandanEngine.apply_action(state, "p1", "g_not_authoritative")

    def test_bombs_straight_flush_level_card_and_generation_are_upstream(self):
        actions = GuandanJudger.playable_actions_from_hand(
            make_hand([
                "S9", "H9", "C9", "D9", "S9", "H9", "C9", "D9",
                "H5", "H5", "S3", "S4", "S5", "S6", "S7",
                "HR", "HR", "SB", "SB",
            ]),
            3,
        )
        self.assertTrue(any(action[0] == "Bomb" and len(action[2]) == 10 for action in actions))
        self.assertTrue(any(action[0] == "StraightFlush" for action in actions))
        self.assertTrue(any(action[0] == "Bomb" and action[1] == "R" for action in actions))

        table = SimpleNamespace(
            player_id=0,
            played_action=["StraightFlush", "2", ["S2", "S3", "S4", "S5", "S6"]],
        )
        six_bomb = ["Bomb", "4", ["S4", "H4", "C4", "D4", "S4", "H4"]]
        self.assertIn(six_bomb, get_gt_actions(3, table, [six_bomb]))
        level_pair = ["Pair", "5", ["S5", "D5"]]
        ace_pair = ["Pair", "A", ["SA", "HA"]]
        self.assertIn(
            level_pair,
            get_gt_actions(3, SimpleNamespace(player_id=0, played_action=ace_pair), [level_pair]),
        )

    def test_pass_and_wind_follow_are_executed_by_upstream_game(self):
        state = self.state()
        game = GuandanEngine._load_game(state)
        wanted = ["S3", "S4", "S5", "S6"]
        chosen = []
        for code in wanted:
            chosen.append(next(card for card in game.round.dealer.deck if (
                card.suit + card.rank if card.rank else ("HR" if card.suit == "RJ" else "SB")
            ) == code))
        for seat, player in enumerate(game.players):
            player.set_current_hand([chosen[seat]])
            player.real_out = False
            player.played_action = None
        game.round.current_player = 0
        game.round.result = [-1, -1, -1, -1]
        game.round.win_count = 0
        game.round.out_flag = [False] * 4
        game.round.greater_player = None
        game.round.trace = []
        game.round._build_public(game.players)
        game.judger.reset(game.players, game.cur_rank)
        GuandanEngine._store_game(state, game)
        state["turn_player_id"] = "p0"
        state["trick"] = {
            "number": 1, "leader_player_id": "p0", "last_play": None,
            "pass_player_ids": [], "wind_follow": False,
        }

        play = next(item for item in GuandanEngine.legal_actions(state, "p0") if item["kind"] == "play")
        GuandanEngine.apply_action(state, "p0", play["action_id"])
        for player_id in ("p1", "p2", "p3"):
            passed = next(
                item for item in GuandanEngine.legal_actions(state, player_id)
                if item["kind"] == "pass"
            )
            GuandanEngine.apply_action(state, player_id, passed["action_id"])
        wind = GuandanEngine.legal_actions(state, "p0")
        self.assertEqual([item["kind"] for item in wind], ["wind_follow"])
        transition = GuandanEngine.apply_action(state, "p0", wind[0]["action_id"])
        self.assertTrue(transition["public_delta"]["wind_follow"])
        self.assertEqual(state["turn_player_id"], "p2")

    def test_interactive_tribute_uses_upstream_legal_execute_and_resist(self):
        game = upstream_game(4, interactive=True)
        hands = [
            ["S3", "HR"],
            ["S4", "SA"],
            ["S4", "S5"],
            ["S6", "SK"],
        ]
        for player, cards in zip(game.players, hands):
            player.set_current_hand(make_hand(cards))
        game.round.result = [0, 2, 1, 3]
        self.assertTrue(game.round._start_interactive_tribute(game.players))
        self.assertEqual(game.round.tribute_state["status"], "selecting_tribute")
        for payer in (3, 1):
            legal = game.round.available_tribute_actions(game.players[payer])
            self.assertTrue(legal)
            game.round.proceed_tribute(game.players[payer], legal[0])
        self.assertEqual(game.round.tribute_state["status"], "selecting_return")
        for receiver in (0, 2):
            legal = game.round.available_tribute_actions(game.players[receiver])
            self.assertTrue(legal)
            game.round.proceed_tribute(game.players[receiver], legal[0])
        self.assertEqual(game.round.tribute_state["status"], "complete")
        self.assertEqual([len(player.current_hand) for player in game.players], [2] * 4)

        counter = upstream_game(5, interactive=True)
        for player, cards in zip(counter.players, [
            ["S3", "SA"], ["S4", "HR"], ["S5", "SA"], ["S6", "HR"],
        ]):
            player.set_current_hand(make_hand(cards))
        counter.round.result = [0, 2, 1, 3]
        self.assertFalse(counter.round._start_interactive_tribute(counter.players))
        self.assertEqual(counter.round.tribute_state["status"], "countered")
        self.assertEqual(counter.round.current_player, 0)

    def test_adapter_tribute_actions_and_deltas_project_upstream_phase(self):
        state = self.state(12)
        game = GuandanEngine._load_game(state)
        hand_codes = [
            ["S3", "HR"],
            ["S4", "SA"],
            ["C3", "C4"],
            ["S6", "SK"],
        ]
        used = set()
        for player, codes in zip(game.players, hand_codes):
            cards = []
            for code in codes:
                card = next(
                    item for item in game.round.dealer.deck
                    if id(item) not in used and (
                        item.suit + item.rank if item.rank
                        else ("HR" if item.suit == "RJ" else "SB")
                    ) == code
                )
                used.add(id(card))
                cards.append(card)
            player.set_current_hand(sorted(
                cards, key=functools.cmp_to_key(natural_card_cmp)
            ))
        game.round.result = [0, 2, 1, 3]
        self.assertTrue(game.round._start_interactive_tribute(game.players))
        GuandanEngine._store_game(state, game)
        self.assertEqual(state["phase"], "tribute")
        self.assertEqual(state["turn_player_id"], "p3")

        for payer in ("p3", "p1"):
            legal = GuandanEngine.legal_actions(state, payer)
            self.assertTrue(legal)
            self.assertTrue(all(item["kind"] == "tribute" for item in legal))
            transition = GuandanEngine.apply_action(
                state, payer, legal[0]["action_id"]
            )
            self.assertEqual(transition["public_delta"]["kind"], "tribute")
            self.assertIn("card", transition["public_delta"])
            self.assertIn("tribute", transition["public_delta"])
        self.assertEqual(state["phase"], "return_tribute")

        for receiver in ("p0", "p2"):
            legal = GuandanEngine.legal_actions(state, receiver)
            self.assertTrue(legal)
            self.assertTrue(all(item["kind"] == "return_tribute" for item in legal))
            transition = GuandanEngine.apply_action(
                state, receiver, legal[0]["action_id"]
            )
            self.assertEqual(
                transition["public_delta"]["kind"], "return_tribute"
            )
        self.assertEqual(state["phase"], "playing")
        self.assertEqual(state["tribute"]["status"], "complete")

    def test_team_upgrade_and_terminal_are_upstream_game_results(self):
        game = upstream_game(8, interactive=True)
        game.round.result = [0, 2, 1, 3]
        game.round.win_count = 2
        game.round.game_over = True
        game.rank_update()
        self.assertEqual(game.team0_rank, 3)
        self.assertEqual(game.game_count, 2)
        self.assertIn(game.round.tribute_state["status"], {"selecting_tribute", "countered"})

        terminal_state = self.state(9)
        terminal = GuandanEngine._load_game(terminal_state)
        terminal.team0_rank = 12
        terminal.round.team0_rank = 12
        terminal.round.rank_list = [12, 0]
        terminal.round.result = [0, 2, 1, 3]
        terminal.round.win_count = 2
        terminal.round.game_over = True
        terminal.rank_update()
        self.assertTrue(terminal.is_over())
        self.assertEqual(terminal.winner_team, 0)
        self.assertEqual(terminal.get_payoffs(), [1.0, -1.0, 1.0, -1.0])
        GuandanEngine._store_game(terminal_state, terminal)
        self.assertEqual(terminal_state["phase"], "finished")
        self.assertEqual(terminal_state["match_result"]["winner_team"], "A")
        self.assertEqual(
            set(terminal_state["match_result"]["winning_player_ids"]),
            {"p0", "p2"},
        )


if __name__ == "__main__":
    unittest.main()

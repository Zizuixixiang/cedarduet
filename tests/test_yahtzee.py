import random
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from app import database, framework
from app.games import GAMES, game_catalog
from app.games.yahtzee import (
    CATEGORIES,
    UPPER_BONUS_SCORE,
    Yahtzee,
    score_category,
)


class RecordingRng:
    def __init__(self) -> None:
        self.calls = 0

    def randint(self, minimum: int, maximum: int) -> int:
        self.calls += 1
        return minimum + (self.calls - 1) % (maximum - minimum + 1)


def participants(count: int) -> list[dict]:
    return [
        {
            "player_id": "human-1",
            "display_name": "人类",
            "role": "human",
            "participant_kind": "human",
        },
        *(
            {
                "player_id": f"ai-{index}",
                "display_name": f"小机 {index}",
                "role": "ai",
                "participant_kind": "bound_machine",
            }
            for index in range(1, count)
        ),
    ]


class YahtzeeScoringTests(unittest.TestCase):
    def test_all_thirteen_categories_use_standard_scores(self):
        self.assertEqual(score_category("ones", [1, 1, 1, 4, 6]), 3)
        self.assertEqual(score_category("sixes", [6, 6, 6, 6, 2]), 24)
        self.assertEqual(score_category("three_of_a_kind", [4, 4, 4, 2, 5]), 19)
        self.assertEqual(score_category("three_of_a_kind", [4, 4, 3, 2, 5]), 0)
        self.assertEqual(score_category("four_of_a_kind", [5, 5, 5, 5, 2]), 22)
        self.assertEqual(score_category("four_of_a_kind", [5, 5, 5, 2, 2]), 0)
        self.assertEqual(score_category("full_house", [2, 2, 3, 3, 3]), 25)
        self.assertEqual(score_category("full_house", [3, 3, 3, 3, 3]), 0)
        self.assertEqual(score_category("small_straight", [1, 2, 3, 4, 4]), 30)
        self.assertEqual(score_category("large_straight", [2, 3, 4, 5, 6]), 40)
        self.assertEqual(score_category("yahtzee", [6, 6, 6, 6, 6]), 50)
        self.assertEqual(score_category("chance", [1, 3, 4, 5, 6]), 19)
        self.assertEqual(len(CATEGORIES), 13)

    def test_upper_bonus_is_exactly_63_to_35(self):
        card = {
            "ones": 3,
            "twos": 6,
            "threes": 9,
            "fours": 12,
            "fives": 15,
            "sixes": 18,
        }
        totals = Yahtzee._card_totals(card)
        self.assertEqual(totals["upper_subtotal"], 63)
        self.assertEqual(totals["upper_bonus"], UPPER_BONUS_SCORE)
        card["ones"] = 2
        self.assertEqual(Yahtzee._card_totals(card)["upper_bonus"], 0)

    def test_invalid_dice_and_unknown_category_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "5 枚"):
            score_category("chance", [1, 2, 3])
        with self.assertRaisesRegex(ValueError, "未知"):
            score_category("joker", [1, 2, 3, 4, 5])


class YahtzeeFrameworkTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="duel-yahtzee-")
        self.addCleanup(self.temporary.cleanup)
        self.db_patch = patch.object(
            database, "DB_PATH", Path(self.temporary.name) / "test.db"
        )
        self.db_patch.start()
        self.addCleanup(self.db_patch.stop)
        database.init_db()
        self.rng = RecordingRng()
        self.game = Yahtzee(self.rng)
        self.game_patch = patch.dict(GAMES, {"yahtzee": self.game})
        self.game_patch.start()
        self.addCleanup(self.game_patch.stop)

    @staticmethod
    def role_for(player_id: str) -> str:
        return "human" if player_id.startswith("human") else "ai"

    def create(self, count: int = 2) -> dict:
        return framework.create_room(
            "yahtzee",
            "human_first",
            "human",
            "human-1",
            opponent_id="ai-1",
            ordered_participants=participants(count),
        )

    def test_catalog_is_two_to_six_npc_entertainment_only(self):
        item = {entry["game_type"]: entry for entry in game_catalog()}["yahtzee"]
        self.assertEqual(item["allowed_player_counts"], [2, 3, 4, 5, 6])
        self.assertEqual(item["recommended_players"], 4)
        self.assertTrue(item["supports_npcs"])
        self.assertFalse(item["supports_stakes"])
        self.assertFalse(item["supports_multiplayer_stakes"])
        with self.assertRaisesRegex(framework.DuelError, "尚未定义筹码"):
            framework.create_room(
                "yahtzee", "human_first", "human", "human-1", "ai-1", stake=1
            )

    def test_rolls_are_persisted_and_only_unheld_dice_reroll(self):
        room = self.create(3)
        room = framework.play_move(
            room["room_id"], "human", "human-1", {"action": "roll"}
        )
        first = list(room["board_state"]["dice"])
        self.assertEqual(first, [1, 2, 3, 4, 5])
        self.assertEqual(self.rng.calls, 5)
        reloaded = framework.get_room(room["room_id"])
        self.assertEqual(reloaded["board_state"]["dice"], first)
        self.assertEqual(self.rng.calls, 5)

        room = framework.play_move(
            room["room_id"],
            "human",
            "human-1",
            {"action": "roll", "hold_indices": [0, 2, 4]},
        )
        self.assertEqual(self.rng.calls, 7)
        self.assertEqual(
            [room["board_state"]["dice"][index] for index in (0, 2, 4)],
            [first[index] for index in (0, 2, 4)],
        )
        self.assertEqual(room["board_state"]["held_mask"], [True, False, True, False, True])
        self.assertEqual(len(room["board_state"]["dice_rolls"]), 2)
        self.assertEqual(room["current_player_id"], "human-1")

    def test_two_through_six_players_take_one_turn_each_per_round(self):
        for count in range(2, 7):
            with self.subTest(count=count):
                room = self.create(count)
                expected_order = list(room["turn_order"])
                observed = []
                for _seat in range(count):
                    player_id = room["current_player_id"]
                    observed.append(player_id)
                    role = self.role_for(player_id)
                    room = framework.play_move(
                        room["room_id"], role, player_id, {"action": "roll"}
                    )
                    room = framework.play_move(
                        room["room_id"],
                        role,
                        player_id,
                        {"action": "score", "category": "ones", "zero": True},
                    )
                self.assertEqual(observed, expected_order)
                self.assertEqual(room["current_player_id"], expected_order[0])
                self.assertEqual(room["board_state"]["flow"]["round_number"], 2)
                for player_id in expected_order:
                    if room["status"] != "playing":
                        break
                    room = framework.resign(
                        room["room_id"], self.role_for(player_id), player_id
                    )

    def test_roll_validation_caps_three_and_rejects_bad_holds(self):
        room = self.create()
        for move in (
            {"action": "roll"},
            {"action": "roll", "held_mask": [True] * 5},
            {"action": "roll", "hold_indices": []},
        ):
            room = framework.play_move(room["room_id"], "human", "human-1", move)
        self.assertEqual(room["board_state"]["rolls_used"], 3)
        with self.assertRaisesRegex(framework.DuelError, "最多掷 3 次"):
            framework.play_move(
                room["room_id"], "human", "human-1", {"action": "roll"}
            )

        fresh = self.create()
        with self.assertRaisesRegex(framework.DuelError, "没有可保留"):
            framework.play_move(
                fresh["room_id"],
                "human",
                "human-1",
                {"action": "roll", "hold_indices": [0]},
            )
        with self.assertRaisesRegex(framework.DuelError, "不能重复"):
            framework.play_move(
                fresh["room_id"],
                "human",
                "human-1",
                {"action": "roll", "hold_indices": [1, 1]},
            )

    def test_score_is_once_per_category_and_explicit_zero_can_scratch_chance(self):
        room = self.create()
        room = framework.play_move(
            room["room_id"], "human", "human-1", {"action": "roll"}
        )
        room = framework.play_move(
            room["room_id"],
            "human",
            "human-1",
            {"action": "score", "category": "chance", "zero": True},
        )
        self.assertEqual(room["board_state"]["scorecards"]["human-1"]["chance"], 0)
        self.assertEqual(room["current_player_id"], "ai-1")
        room = framework.play_move(
            room["room_id"], "ai", "ai-1", {"action": "roll"}
        )
        room = framework.play_move(
            room["room_id"],
            "ai",
            "ai-1",
            {"action": "score", "category": "ones"},
        )
        self.assertEqual(room["board_state"]["flow"]["round_number"], 2)

        room = framework.play_move(
            room["room_id"], "human", "human-1", {"action": "roll"}
        )
        with self.assertRaisesRegex(framework.DuelError, "已经填写过"):
            framework.play_move(
                room["room_id"],
                "human",
                "human-1",
                {"action": "score", "category": "chance"},
            )

    def test_public_cards_previews_and_ai_private_decision_context(self):
        room = self.create(3)
        room = framework.play_move(
            room["room_id"], "human", "human-1", {"action": "roll"}
        )
        public = framework.project_room_for_viewer(room, "ai-2")
        state = public["board_state"]
        self.assertEqual(state["dice"], room["board_state"]["dice"])
        self.assertEqual(set(state["scorecards"]), {"human-1", "ai-1", "ai-2"})
        self.assertEqual(set(state["score_previews"]), set(CATEGORIES))
        self.assertTrue(any(action["action"] == "roll" for action in state["legal_actions"]))
        self.assertTrue(any(action["action"] == "score" for action in state["legal_actions"]))
        self.assertNotIn("dice_rolls", state)
        self.assertNotIn("action_history", state)
        self.assertEqual(public["private_state"], {})

        room = framework.play_move(
            room["room_id"],
            "human",
            "human-1",
            {"action": "score", "category": "ones"},
        )
        room = framework.play_move(
            room["room_id"], "ai", "ai-1", {"action": "roll"}
        )
        ai_view = framework.project_room_for_viewer(room, "ai-1")
        self.assertEqual(ai_view["private_state"]["dice"], room["board_state"]["dice"])
        self.assertEqual(set(ai_view["private_state"]["legal_categories"]), set(CATEGORIES))

    def test_npc_actions_are_all_legal_and_reasonable_hold_is_first(self):
        state = self.game.initialize(participants(3))
        actor = {**participants(3)[1], "token": "O"}
        state["turn_player_id"] = "ai-1"
        state["dice"] = [6, 6, 6, 2, 3]
        state["rolls_used"] = 1
        state["flow"]["phase"] = "rolling_or_scoring"
        actions = self.game.npc_legal_actions(state, actor, participants(3))
        self.assertEqual(
            actions[0],
            {"action": "roll", "held_mask": [True, True, True, False, False]},
        )
        self.assertEqual(len(actions), 32 + len(CATEGORIES))
        for action in actions:
            self.game.validate_action(deepcopy(state), action, actor)
        state["rolls_used"] = 3
        forced_scores = self.game.npc_legal_actions(state, actor, participants(3))
        self.assertEqual(len(forced_scores), len(CATEGORIES))
        self.assertTrue(all(action["action"] == "score" for action in forced_scores))

    def test_thirteen_rounds_finish_with_explicit_draw_semantics(self):
        room = self.create()
        for category in CATEGORIES:
            for _seat in range(2):
                player_id = room["current_player_id"]
                role = self.role_for(player_id)
                room = framework.play_move(
                    room["room_id"], role, player_id, {"action": "roll"}
                )
                room = framework.play_move(
                    room["room_id"],
                    role,
                    player_id,
                    {"action": "score", "category": category, "zero": True},
                )
        self.assertEqual(room["status"], "finished")
        self.assertEqual(room["winner"], "draw")
        self.assertIsNone(room["current_player_id"])
        self.assertEqual(room["result"]["tied_player_ids"], ["human-1", "ai-1"])
        self.assertEqual(room["result"]["placements"], [
            {"rank": 1, "player_id": "human-1", "total": 0},
            {"rank": 1, "player_id": "ai-1", "total": 0},
        ])
        self.assertIn("并列即和局", room["result"]["tie_policy"])

    def test_unique_high_score_wins_in_stable_placement_order(self):
        state = self.game.initialize(participants(3))
        state["scorecards"] = {
            "human-1": {category: (50 if category == "yahtzee" else 0) for category in CATEGORIES},
            "ai-1": {
                category: (40 if category == "large_straight" else 0)
                for category in CATEGORIES
            },
            "ai-2": {category: 0 for category in CATEGORIES},
        }
        result = self.game.result_for(state, participants(3))
        self.assertEqual(result["winner_player_id"], "human-1")
        self.assertFalse(result["draw"])
        self.assertEqual(
            [placement["player_id"] for placement in result["placements"]],
            ["human-1", "ai-1", "ai-2"],
        )


if __name__ == "__main__":
    unittest.main()

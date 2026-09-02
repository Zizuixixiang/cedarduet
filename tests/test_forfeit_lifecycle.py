import random
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from third_party.pypokerengine.engine.pay_info import PayInfo

from app import database, framework
from app.games.aeroplane_chess import AeroplaneChess
from app.games.blackjack import Blackjack
from app.games.dots_boxes import DotsBoxes
from app.games.gandengyan import Gandengyan
from app.games.liars_dice import LiarsDice
from app.games.texas_holdem import TexasHoldem
from app.games.train_cards import TrainCards
from app.games.uno import Uno
from app.games.yahtzee import Yahtzee
from app.games.zhajinhua import Zhajinhua


def seats(count: int) -> list[dict]:
    return [
        {
            "player_id": "human-1" if index == 0 else f"ai-{index}",
            "display_name": "人类" if index == 0 else f"小机 {index}",
            "role": "human" if index == 0 else "ai",
            "participant_kind": "human" if index == 0 else "bound_machine",
            "seat_index": index,
            "token": f"P{index + 1}",
        }
        for index in range(count)
    ]


class ForfeitHookTests(unittest.TestCase):
    def test_flexible_games_remove_or_disable_departed_actor(self):
        participants = seats(3)
        ordinary = (
            (Uno(random.Random(1)), "participant_order", False),
            (Gandengyan(random.Random(1)), "participant_order", False),
            (TrainCards(random.Random(1)), "active_player_ids", False),
            (Zhajinhua(random.Random(1)), "player_state_by_player", True),
            (LiarsDice(random.Random(1)), "dice_counts", True),
            (Yahtzee(random.Random(1)), "resigned_player_ids", True),
            (DotsBoxes(), "resigned_player_ids", True),
        )
        for game, state_key, keeps_identity in ordinary:
            with self.subTest(game=game.game_type):
                state = game.initialize(participants)
                if "turn_player_id" in state:
                    state["turn_player_id"] = "human-1"
                game.apply_resignation(state, "human-1", participants)
                if "turn_player_id" in state:
                    self.assertNotEqual(state.get("turn_player_id"), "human-1")
                container = state[state_key]
                if keeps_identity:
                    self.assertIn("human-1", container)
                else:
                    self.assertNotIn("human-1", container)
                if game.game_type == "zhajinhua":
                    self.assertEqual(container["human-1"]["status"], "folded")
                elif game.game_type == "liars_dice":
                    self.assertEqual(container["human-1"], 0)
                elif keeps_identity:
                    self.assertIn("human-1", container)

        poker = TexasHoldem(random.Random(1))
        poker_state = poker.initialize_for_first_player(participants, "human-1")
        resigned = poker_state["turn_player_id"]
        poker.apply_resignation(poker_state, resigned, participants)
        engine = poker._deserialize_engine(poker_state)
        self.assertEqual(
            poker._player(engine, resigned).pay_info.status, PayInfo.FOLDED
        )
        self.assertNotEqual(poker_state.get("turn_player_id"), resigned)

        poker_state = poker.initialize_for_first_player(participants, "human-1")
        original_turn = poker_state["turn_player_id"]
        out_of_turn = next(
            player_id for player_id in poker_state["participant_order"]
            if player_id != original_turn
        )
        poker.apply_resignation(poker_state, out_of_turn, participants)
        engine = poker._deserialize_engine(poker_state)
        self.assertEqual(
            poker._player(engine, out_of_turn).pay_info.status, PayInfo.FOLDED
        )
        self.assertEqual(poker_state["turn_player_id"], original_turn)

        aeroplane_players = seats(3)
        for participant, color in zip(
            aeroplane_players, AeroplaneChess._colors_for_count(3)
        ):
            participant["token"] = color
        aeroplane = AeroplaneChess(random.Random(1))
        aeroplane_state = aeroplane.initialize(aeroplane_players)
        aeroplane_state["turn_player_id"] = "human-1"
        aeroplane.apply_resignation(
            aeroplane_state, "human-1", aeroplane_players
        )
        self.assertNotIn("human-1", aeroplane_state["participant_order"])
        self.assertNotIn("human-1", aeroplane_state["planes"])
        self.assertNotEqual(aeroplane_state["turn_player_id"], "human-1")

        blackjack = Blackjack(random.Random(1))
        blackjack_state = blackjack.initialize(participants)
        blackjack_state["turn_player_id"] = "human-1"
        blackjack.apply_resignation(
            blackjack_state, "human-1", participants
        )
        self.assertEqual(
            blackjack_state["player_status_by_player"]["human-1"], "resigned"
        )
        self.assertNotEqual(blackjack_state.get("turn_player_id"), "human-1")


class ForfeitFrameworkTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="duel-forfeit-")
        self.addCleanup(self.temporary.cleanup)
        self.db_patch = patch.object(
            database, "DB_PATH", Path(self.temporary.name) / "test.db"
        )
        self.db_patch.start()
        self.addCleanup(self.db_patch.stop)
        database.init_db()

    @staticmethod
    def create(game_type: str, count: int, stake: int = 0) -> dict:
        participants = seats(count)
        room = framework.create_room(
            game_type,
            "human_first",
            "human",
            "human-1",
            "ai-1",
            ordered_participants=participants,
            first_player_id="human-1",
            stake=stake,
        )
        for participant in participants[1:]:
            room = framework.respond_to_invitation(
                room["room_id"], "ai", participant["player_id"], "accept"
            )
        return room

    def test_leave_continues_then_finishes_uno_with_full_stake_liability(self):
        room = self.create("uno", 3, stake=4)
        room = framework.leave_room(room["room_id"], "human", "human-1")
        self.assertEqual(room["status"], "playing")
        self.assertEqual(room["current_player_id"], "ai-1")
        self.assertNotIn("human-1", room["board_state"]["participant_order"])

        room = framework.leave_room(room["room_id"], "ai", "ai-1")
        self.assertEqual(room["status"], "finished")
        self.assertEqual(room["winner_player_id"], "ai-2")
        self.assertEqual(room["board_state"]["flow"]["phase"], "finished")
        self.assertEqual(room["result"]["settlement_deltas"], {
            "human-1": -4,
            "ai-1": -4,
            "ai-2": 8,
        })

    def test_leave_cannot_bypass_fixed_table_team_or_full_liability(self):
        for game_type, count, expected_loss in (
            ("doudizhu", 3, -6),
            ("guandan", 4, -3),
            ("mahjong", 4, -9),
        ):
            with self.subTest(game=game_type):
                room = self.create(game_type, count, stake=3)
                room = framework.leave_room(
                    room["room_id"], "human", "human-1"
                )
                self.assertEqual(room["status"], "finished")
                self.assertEqual(
                    room["result"]["settlement_deltas"]["human-1"],
                    expected_loss,
                )
                self.assertEqual(sum(room["result"]["settlement_deltas"].values()), 0)

    def test_blackjack_cannot_continue_when_only_system_npc_remains(self):
        participants = seats(2)
        participants[1].update({
            "player_id": "npc-1",
            "display_name": "系统 NPC",
            "participant_kind": "system_npc",
            "npc_persona_id": "test-blackjack",
        })

        # Even a game-level opt-out must not be able to bypass this framework
        # invariant. Keeping the synthetic flag here guards against its return.
        with patch.object(
            Blackjack,
            "continues_with_only_system_npcs_after_resignation",
            True,
            create=True,
        ):
            room = framework.create_room(
                "blackjack",
                "human_first",
                "human",
                "human-1",
                "npc-1",
                ordered_participants=participants,
                first_player_id="human-1",
            )
            self.assertEqual(room["status"], "playing")
            room = framework.leave_room(
                room["room_id"], "human", "human-1"
            )

        self.assertEqual(room["status"], "finished")
        self.assertIsNone(room["current_player_id"])
        self.assertEqual(room["board_state"]["flow"]["phase"], "finished")
        self.assertIsNone(room["board_state"]["turn_player_id"])
        self.assertEqual(
            room["board_state"]["player_status_by_player"]["human-1"],
            "resigned",
        )
        self.assertEqual(
            room["result"]["outcomes_by_player"]["human-1"]["outcome"],
            "loss",
        )

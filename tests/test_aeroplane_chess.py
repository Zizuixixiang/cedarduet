import json
import random
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from app import database, framework
from app.games import GAMES, game_catalog
from app.games.aeroplane_chess import (
    COLORS,
    FINISH_ROUTE_STEP,
    HOME_LANE_LENGTH,
    RING_LENGTH,
    SHORTCUT_FROM_STEP,
    SHORTCUT_TO_STEP,
    START_RING_INDEX,
    AeroplaneChess,
)


class QueueRng:
    def __init__(self, values):
        self.values = list(values)
        self.calls = 0

    def randint(self, minimum, maximum):
        self.calls += 1
        if not self.values:
            raise AssertionError("测试骰子序列已经用完")
        value = self.values.pop(0)
        if not minimum <= value <= maximum:
            raise AssertionError("测试骰子越界")
        return value


def participants(count):
    colors = AeroplaneChess._colors_for_count(count)
    return [
        {
            "player_id": "human-1" if index == 0 else f"ai-{index}",
            "display_name": "人类" if index == 0 else f"小机 {index}",
            "role": "human" if index == 0 else "ai",
            "participant_kind": "human" if index == 0 else "bound_machine",
            "token": color,
            "seat_index": index,
        }
        for index, color in enumerate(colors)
    ]


class AeroplaneChessRuleTests(unittest.TestCase):
    def make_game(self, rolls=(1,), count=4):
        rng = QueueRng(rolls)
        game = AeroplaneChess(rng)
        seats = participants(count)
        return game, rng, seats, game.initialize(seats)

    @staticmethod
    def actor(seats, index=0):
        return seats[index]

    @staticmethod
    def plane(state, player_id, index=0):
        return state["planes"][player_id][index]

    @staticmethod
    def set_step(game, state, player_id, plane_index, route_step):
        color = state["color_by_player"][player_id]
        game._set_plane_step(
            state["planes"][player_id][plane_index], color, route_step
        )

    def roll(self, game, state, actor):
        return game.apply_action(state, {"action": "roll"}, actor)

    def move(self, game, state, actor, plane_index=0):
        plane = self.plane(state, actor["player_id"], plane_index)
        return game.apply_action(
            state,
            {
                "action": "move",
                "plane_id": plane["plane_id"],
                "plane_index": plane_index,
            },
            actor,
        )

    def test_catalog_metadata_and_fixed_rules_are_declared(self):
        item = {
            entry["game_type"]: entry for entry in game_catalog()
        }["aeroplane_chess"]
        self.assertEqual(item["display_name"], "飞行棋")
        self.assertEqual(item["category"], "board")
        self.assertEqual(item["allowed_player_counts"], [2, 3, 4])
        self.assertEqual(item["recommended_players"], 4)
        self.assertTrue(item["supports_npcs"])
        self.assertTrue(item["supports_stakes"])
        self.assertTrue(item["supports_multiplayer_stakes"])
        rules = GAMES["aeroplane_chess"].rules_text
        for phrase in (
            "只有掷出 6", "连续第三个 6", "不组成叠机单位", "精确点数",
            "不采用偶数起飞", "刷新不会重掷",
        ):
            self.assertIn(phrase, rules)

    def test_two_three_four_player_color_and_path_mapping(self):
        expectations = {
            2: ["red", "blue"],
            3: ["red", "yellow", "blue"],
            4: list(COLORS),
        }
        for count, expected_colors in expectations.items():
            with self.subTest(count=count):
                game, _rng, seats, state = self.make_game(count=count)
                self.assertEqual(game.tokens_for(seats), expected_colors)
                self.assertEqual(
                    [state["color_by_player"][seat["player_id"]] for seat in seats],
                    expected_colors,
                )
                self.assertEqual(state["ring_length"], RING_LENGTH)
                self.assertEqual(state["home_lane_length"], HOME_LANE_LENGTH)
                for color in expected_colors:
                    mapping = state["path_mappings"][color]
                    self.assertEqual(
                        mapping["start_ring_index"], START_RING_INDEX[color]
                    )
                    self.assertEqual(len(mapping["ring_indices"]), 52)
                    self.assertEqual(len(set(mapping["ring_indices"])), 52)
                    self.assertEqual(
                        mapping["shortcut"]["from_route_step"],
                        SHORTCUT_FROM_STEP,
                    )
                    self.assertEqual(
                        mapping["shortcut"]["to_route_step"],
                        SHORTCUT_TO_STEP,
                    )
                self.assertTrue(all(
                    plane["zone"] == "airport" and plane["route_step"] == -1
                    for planes in state["planes"].values() for plane in planes
                ))

    def test_roll_is_persisted_and_choice_phase_blocks_reroll(self):
        game, rng, seats, state = self.make_game((6,), count=2)
        result = self.roll(game, state, self.actor(seats))
        self.assertTrue(result.retain_turn)
        self.assertEqual(rng.calls, 1)
        self.assertEqual(state["last_roll"]["value"], 6)
        self.assertEqual(state["dice_rolls"][0]["values"], [6])
        self.assertEqual(state["flow"]["phase"], "awaiting_plane_choice")
        persisted = deepcopy(state)
        self.assertEqual(game.public_state(state, seats), persisted)
        self.assertEqual(rng.calls, 1)
        with self.assertRaisesRegex(ValueError, "选择"):
            game.validate_action(state, {"action": "roll"}, self.actor(seats))

    def test_six_launches_only_to_safe_launch_and_retains_turn_after_move(self):
        game, _rng, seats, state = self.make_game((6,), count=2)
        actor = self.actor(seats)
        rolled = self.roll(game, state, actor)
        self.assertTrue(rolled.retain_turn)
        self.assertEqual(len(state["legal_actions"]), 4)
        moved = self.move(game, state, actor, 2)
        plane = self.plane(state, actor["player_id"], 2)
        self.assertEqual(plane["zone"], "launch")
        self.assertEqual(plane["route_step"], 0)
        self.assertTrue(moved.retain_turn)
        self.assertEqual(state["flow"]["phase"], "awaiting_roll")
        self.assertEqual(state["legal_actions"], [{"action": "roll"}])
        self.assertEqual(state["turn_six_move_plane_ids"], [plane["plane_id"]])

    def test_non_six_cannot_launch_and_server_auto_ends_turn(self):
        game, _rng, seats, state = self.make_game((4,), count=2)
        result = self.roll(game, state, self.actor(seats))
        self.assertFalse(result.retain_turn)
        self.assertEqual(state["flow"]["phase"], "awaiting_roll")
        self.assertEqual(state["turn_player_id"], None)
        self.assertEqual(state["movable_plane_ids"], [])
        self.assertTrue(state["last_action"]["auto_pass"])

    def test_six_with_no_legal_plane_auto_passes_move_but_keeps_extra_roll(self):
        game, _rng, seats, state = self.make_game((6,), count=2)
        actor = self.actor(seats)
        for index in range(4):
            self.set_step(
                game, state, actor["player_id"], index, FINISH_ROUTE_STEP - 1
            )
        result = self.roll(game, state, actor)
        self.assertTrue(result.retain_turn)
        self.assertEqual(state["flow"]["phase"], "awaiting_roll")
        self.assertEqual(state["consecutive_sixes"], 1)
        self.assertEqual(state["legal_actions"], [{"action": "roll"}])

    def test_third_consecutive_six_returns_planes_moved_by_first_two_sixes(self):
        game, _rng, seats, state = self.make_game((6, 6, 6), count=2)
        actor = self.actor(seats)
        for plane_index in (0, 1):
            self.roll(game, state, actor)
            self.move(game, state, actor, plane_index)
        penalty = self.roll(game, state, actor)
        penalty = game.progress_after_action(
            state, {"action": "roll"}, actor, seats, penalty
        )
        self.assertFalse(penalty.retain_turn)
        self.assertEqual(state["flow"]["phase"], "awaiting_roll")
        self.assertEqual(state["consecutive_sixes"], 0)
        self.assertEqual(state["turn_player_id"], None)
        self.assertEqual(
            state["last_action"]["penalty"], "third_consecutive_six"
        )
        self.assertEqual(
            state["last_action"]["returned_plane_ids"], ["red-0", "red-1"]
        )
        self.assertEqual(
            penalty.public_event["aeroplane_delta"]["penalty"],
            "third_consecutive_six",
        )
        self.assertEqual(
            penalty.public_event["aeroplane_delta"]["returned_plane_ids"],
            ["red-0", "red-1"],
        )
        self.assertTrue(all(
            self.plane(state, actor["player_id"], index)["zone"] == "airport"
            for index in (0, 1)
        ))

    def test_ordinary_move_and_own_color_jump_are_distinct(self):
        game, _rng, seats, state = self.make_game((1,), count=2)
        actor = self.actor(seats)
        self.set_step(game, state, actor["player_id"], 0, 1)
        self.roll(game, state, actor)
        self.move(game, state, actor)
        self.assertEqual(self.plane(state, actor["player_id"])["route_step"], 2)
        self.assertEqual(
            [item["kind"] for item in state["last_action"]["landings"]],
            ["dice"],
        )

        game, _rng, seats, state = self.make_game((3,), count=2)
        actor = self.actor(seats)
        self.set_step(game, state, actor["player_id"], 0, 2)
        self.roll(game, state, actor)
        self.move(game, state, actor)
        self.assertEqual(self.plane(state, actor["player_id"])["route_step"], 9)
        self.assertEqual(
            [item["kind"] for item in state["last_action"]["landings"]],
            ["dice", "jump"],
        )

    def test_jump_into_shortcut_chains_to_fixed_cross_board_destination(self):
        game, _rng, seats, state = self.make_game((3,), count=2)
        actor = self.actor(seats)
        self.set_step(game, state, actor["player_id"], 0, 14)
        self.roll(game, state, actor)
        legal = state["legal_moves"][0]
        self.assertEqual(legal["to"]["route_step"], SHORTCUT_TO_STEP)
        self.assertEqual(
            [item["kind"] for item in legal["landings"]],
            ["dice", "jump", "shortcut"],
        )
        moved = self.move(game, state, actor)
        moved = game.progress_after_action(
            state,
            {"action": "move", "plane_id": "red-0", "plane_index": 0},
            actor,
            seats,
            moved,
        )
        self.assertEqual(
            self.plane(state, actor["player_id"])["route_step"],
            SHORTCUT_TO_STEP,
        )
        delta = moved.public_event["aeroplane_delta"]
        self.assertEqual(delta["to"]["route_step"], SHORTCUT_TO_STEP)
        self.assertEqual(
            [item["kind"] for item in delta["landings"]],
            ["dice", "jump", "shortcut"],
        )

    def test_direct_shortcut_does_not_add_an_unrequested_second_jump(self):
        game, _rng, seats, state = self.make_game((3,), count=2)
        actor = self.actor(seats)
        self.set_step(game, state, actor["player_id"], 0, 18)
        self.roll(game, state, actor)
        self.move(game, state, actor)
        self.assertEqual(
            self.plane(state, actor["player_id"])["route_step"],
            SHORTCUT_TO_STEP,
        )
        self.assertEqual(
            [item["kind"] for item in state["last_action"]["landings"]],
            ["dice", "shortcut"],
        )

    def test_collision_returns_every_opponent_on_normal_square_but_not_own_plane(self):
        game, _rng, seats, state = self.make_game((3,), count=2)
        red, blue = seats
        self.set_step(game, state, red["player_id"], 0, 0)
        self.set_step(game, state, red["player_id"], 1, 3)
        # Blue route step 29 maps to global ring index 2, the red target.
        self.set_step(game, state, blue["player_id"], 0, 29)
        self.set_step(game, state, blue["player_id"], 1, 29)
        self.roll(game, state, red)
        self.move(game, state, red)
        self.assertEqual(self.plane(state, red["player_id"], 0)["ring_index"], 2)
        self.assertEqual(self.plane(state, red["player_id"], 1)["ring_index"], 2)
        self.assertEqual(
            state["last_action"]["captured_plane_ids"], ["blue-0", "blue-1"]
        )
        self.assertEqual(self.plane(state, blue["player_id"], 0)["zone"], "airport")
        self.assertEqual(self.plane(state, blue["player_id"], 1)["zone"], "airport")

    def test_jump_shortcut_cross_and_destination_all_resolve_collisions(self):
        game, _rng, seats, state = self.make_game((3,), count=2)
        red, blue = seats
        self.set_step(game, state, red["player_id"], 0, 14)
        # Red landings/crossing use global indices 16, 20, 26 and 32.
        blue_steps = [43, 47, 1, 7]
        for index, step in enumerate(blue_steps):
            self.set_step(game, state, blue["player_id"], index, step)
        self.roll(game, state, red)
        self.move(game, state, red)
        self.assertEqual(
            set(state["last_action"]["captured_plane_ids"]),
            {f"blue-{index}" for index in range(4)},
        )
        self.assertTrue(all(
            plane["zone"] == "airport"
            for plane in state["planes"][blue["player_id"]]
        ))

    def test_safe_zones_never_collide(self):
        game, _rng, seats, state = self.make_game((1,), count=2)
        red, blue = seats
        self.set_step(game, state, red["player_id"], 0, 52)
        self.set_step(game, state, blue["player_id"], 0, 53)
        self.roll(game, state, red)
        self.move(game, state, red)
        self.assertEqual(self.plane(state, red["player_id"])["zone"], "home_lane")
        self.assertEqual(self.plane(state, blue["player_id"])["zone"], "home_lane")
        self.assertEqual(state["last_action"]["captured_plane_ids"], [])

    def test_home_requires_exact_roll_and_fourth_plane_wins(self):
        game, _rng, seats, state = self.make_game((2,), count=2)
        actor = self.actor(seats)
        for index in range(4):
            self.set_step(
                game, state, actor["player_id"], index, FINISH_ROUTE_STEP - 1
            )
        passed = self.roll(game, state, actor)
        self.assertFalse(passed.retain_turn)
        self.assertEqual(state["movable_plane_ids"], [])
        self.assertTrue(all(
            plane["route_step"] == FINISH_ROUTE_STEP - 1
            for plane in state["planes"][actor["player_id"]]
        ))

        game, _rng, seats, state = self.make_game((1,), count=2)
        actor = self.actor(seats)
        for index in range(3):
            self.set_step(game, state, actor["player_id"], index, FINISH_ROUTE_STEP)
        self.set_step(
            game, state, actor["player_id"], 3, FINISH_ROUTE_STEP - 1
        )
        self.roll(game, state, actor)
        result = self.move(game, state, actor, 3)
        self.assertEqual(result.result["winner_player_id"], actor["player_id"])
        self.assertEqual(
            result.note,
            "人类的 4 号机到达终点，4 架飞机全部到家，赢得本局。",
        )
        self.assertEqual(state["flow"]["phase"], "finished")
        self.assertEqual(self.plane(state, actor["player_id"], 3)["zone"], "home")

    def test_home_arrival_note_names_plane_for_human_and_npc(self):
        for actor_index, expected_name in ((0, "人类"), (1, "小机 1")):
            with self.subTest(actor_index=actor_index):
                game, _rng, seats, state = self.make_game((1,), count=2)
                actor = self.actor(seats, actor_index)
                self.set_step(
                    game,
                    state,
                    actor["player_id"],
                    2,
                    FINISH_ROUTE_STEP - 1,
                )
                self.roll(game, state, actor)
                result = self.move(game, state, actor, 2)
                self.assertEqual(
                    result.note,
                    f"{expected_name}的 3 号机到达终点。",
                )
                self.assertEqual(
                    self.plane(state, actor["player_id"], 2)["zone"],
                    "home",
                )
                self.assertTrue(state["last_action"]["reached_home"])

    def test_move_identity_and_npc_actions_are_server_authoritative(self):
        game, _rng, seats, state = self.make_game((6,), count=2)
        actor = self.actor(seats)
        self.assertEqual(
            game.npc_legal_actions(state, actor, seats), [{"action": "roll"}]
        )
        self.roll(game, state, actor)
        actions = game.npc_legal_actions(state, actor, seats)
        self.assertEqual(actions, state["legal_actions"])
        self.assertIsNot(actions, state["legal_actions"])
        actions.pop()
        self.assertEqual(len(state["legal_actions"]), 4)
        with self.assertRaisesRegex(ValueError, "唯一对应"):
            game.validate_action(
                state,
                {"action": "move", "plane_id": "red-9", "plane_index": 3},
                actor,
            )
        self.assertEqual(
            game.npc_legal_actions(state, seats[1], seats), []
        )

        stacked_game, _rng, stacked_seats, stacked_state = self.make_game(
            (4,), count=2
        )
        stacked_actor = self.actor(stacked_seats)
        self.set_step(
            stacked_game, stacked_state, stacked_actor["player_id"], 0, 5
        )
        self.set_step(
            stacked_game, stacked_state, stacked_actor["player_id"], 1, 5
        )
        self.roll(stacked_game, stacked_state, stacked_actor)
        stacked_actions = stacked_game.npc_legal_actions(
            stacked_state, stacked_actor, stacked_seats
        )
        self.assertEqual(
            [action["plane_id"] for action in stacked_actions],
            ["red-0", "red-1"],
        )
        self.assertEqual(
            [action["plane_index"] for action in stacked_actions], [0, 1]
        )

    def test_multiplayer_stake_policy_is_complete_integer_and_zero_sum(self):
        game, _rng, seats, state = self.make_game((1,), count=4)
        deltas = game.settlement_deltas(
            state,
            {"winner_player_id": "ai-2", "draw": False},
            seats,
            7,
        )
        self.assertEqual(deltas["ai-2"], 21)
        self.assertEqual(
            {player_id: delta for player_id, delta in deltas.items() if player_id != "ai-2"},
            {"human-1": -7, "ai-1": -7, "ai-3": -7},
        )
        self.assertEqual(sum(deltas.values()), 0)

    def test_seeded_random_games_preserve_all_state_invariants(self):
        for count in (2, 3, 4):
            with self.subTest(count=count):
                seats = participants(count)
                game = AeroplaneChess(random.Random(9000 + count))
                state = game.initialize(seats)
                chooser = random.Random(1200 + count)
                current_index = 0
                for _action_number in range(16000):
                    actor = seats[current_index]
                    legal = game.npc_legal_actions(state, actor, seats)
                    self.assertTrue(legal)
                    action = chooser.choice(legal)
                    result = game.apply_action(state, action, actor)

                    all_ids = []
                    for player_id, planes in state["planes"].items():
                        color = state["color_by_player"][player_id]
                        self.assertEqual(len(planes), 4)
                        for plane in planes:
                            all_ids.append(plane["plane_id"])
                            self.assertTrue(-1 <= plane["route_step"] <= FINISH_ROUTE_STEP)
                            expected = game._location(color, plane["route_step"])
                            for key, value in expected.items():
                                self.assertEqual(plane[key], value)
                    self.assertEqual(len(all_ids), len(set(all_ids)))
                    for sequence, record in enumerate(state["dice_rolls"], start=1):
                        self.assertEqual(record["sequence"], sequence)
                        self.assertTrue(all(1 <= value <= 6 for value in record["values"]))

                    if result.result is not None:
                        self.assertEqual(state["flow"]["phase"], "finished")
                        break
                    if not result.retain_turn:
                        current_index = (current_index + 1) % count
                else:
                    self.fail(f"{count} 人随机对局未能在动作上限内结束")


class AeroplaneChessFrameworkTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="duel-aeroplane-")
        self.addCleanup(self.temporary.cleanup)
        self.db_patch = patch.object(
            database, "DB_PATH", Path(self.temporary.name) / "test.db"
        )
        self.db_patch.start()
        self.addCleanup(self.db_patch.stop)
        database.init_db()

    @staticmethod
    def framework_participants(count):
        return [
            {
                "player_id": "human-1" if index == 0 else f"ai-{index}",
                "display_name": "人类" if index == 0 else f"小机 {index}",
                "role": "human" if index == 0 else "ai",
                "participant_kind": "human" if index == 0 else "bound_machine",
            }
            for index in range(count)
        ]

    def test_revision_and_reload_preserve_the_single_server_roll(self):
        rng = QueueRng((6,))
        game = AeroplaneChess(rng)
        with patch.dict(GAMES, {"aeroplane_chess": game}):
            room = framework.create_room(
                "aeroplane_chess",
                "human_first",
                "human",
                "human-1",
                ordered_participants=self.framework_participants(2),
            )
            rolled = framework.play_move(
                room["room_id"],
                "human",
                "human-1",
                {"action": "roll"},
                expected_revision=0,
            )
            self.assertEqual(rolled["revision"], 1)
            self.assertEqual(rolled["board_state"]["last_roll"]["value"], 6)
            self.assertEqual(rng.calls, 1)
            reloaded = framework.get_room(room["room_id"])
            self.assertEqual(reloaded["board_state"]["dice_rolls"], rolled["board_state"]["dice_rolls"])
            self.assertEqual(rng.calls, 1)
            with self.assertRaisesRegex(framework.DuelError, "revision 已变化"):
                framework.play_move(
                    room["room_id"],
                    "human",
                    "human-1",
                    {"action": "move", "plane_index": 0},
                    expected_revision=0,
                )

    def test_three_player_terminal_move_attaches_and_settles_explicit_stakes(self):
        rng = QueueRng((1,))
        game = AeroplaneChess(rng)
        with patch.dict(GAMES, {"aeroplane_chess": game}):
            room = framework.create_room(
                "aeroplane_chess",
                "human_first",
                "human",
                "human-1",
                ordered_participants=self.framework_participants(3),
                stake=5,
            )
            for player_id in ("ai-1", "ai-2"):
                room = framework.respond_to_invitation(
                    room["room_id"], "ai", player_id, "accept"
                )
            state = room["board_state"]
            for index in range(3):
                game._set_plane_step(
                    state["planes"]["human-1"][index], "red", FINISH_ROUTE_STEP
                )
            game._set_plane_step(
                state["planes"]["human-1"][3],
                "red",
                FINISH_ROUTE_STEP - 1,
            )
            state["flow"]["phase"] = "awaiting_roll"
            state["legal_actions"] = [{"action": "roll"}]
            state["legal_moves"] = []
            state["turn_player_id"] = None
            with database.write_transaction() as conn:
                conn.execute(
                    "UPDATE rooms SET board_state = ? WHERE room_id = ?",
                    (
                        json.dumps(state, ensure_ascii=False, separators=(",", ":")),
                        room["room_id"],
                    ),
                )
            room = framework.play_move(
                room["room_id"], "human", "human-1", {"action": "roll"}
            )
            room = framework.play_move(
                room["room_id"],
                "human",
                "human-1",
                {"action": "move", "plane_id": "red-3", "plane_index": 3},
            )
            self.assertEqual(room["status"], "finished")
            self.assertEqual(room["winner_player_id"], "human-1")
            self.assertEqual(room["result"]["settlement_deltas"], {
                "human-1": 10,
                "ai-1": -5,
                "ai-2": -5,
            })
            self.assertTrue(room["result"]["settlement_zero_sum"])


if __name__ == "__main__":
    unittest.main()

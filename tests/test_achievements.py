import asyncio
import base64
import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import httpx

from app import achievements, chips, database, framework
from app import main as main_module


class AchievementServiceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="duel-achievements-")
        self.db_patch = patch.object(
            database, "DB_PATH", Path(self.temporary.name) / "test.db"
        )
        self.db_patch.start()
        database.init_db()
        self.fact_index = 0

    def tearDown(self):
        self.db_patch.stop()
        self.temporary.cleanup()

    def finish_human_win(self, human="human-1", ai="ai-1", *, rematch_of=None):
        room = framework.create_room(
            "tictactoe", "human_first", "human", human, ai,
            rematch_of_room_id=rematch_of,
        )
        for role, player_id, move in (
            ("human", human, {"row": 0, "col": 0}),
            ("ai", ai, {"row": 1, "col": 0}),
            ("human", human, {"row": 0, "col": 1}),
            ("ai", ai, {"row": 1, "col": 1}),
            ("human", human, {"row": 0, "col": 2}),
        ):
            room = framework.play_move(room["room_id"], role, player_id, move)
        return room

    def insert_match_fact(
        self,
        room_id,
        participants,
        *,
        winner_player_id=None,
        game_type="tictactoe",
        stake=0,
        rematch_of_room_id=None,
        rematch_root_room_id=None,
    ):
        self.fact_index += 1
        created_at = (
            datetime(2026, 1, 1, tzinfo=timezone.utc)
            + timedelta(minutes=self.fact_index)
        ).isoformat(timespec="seconds")
        terminal_at = (
            datetime(2026, 1, 1, tzinfo=timezone.utc)
            + timedelta(minutes=self.fact_index, seconds=30)
        ).isoformat(timespec="seconds")
        with database.write_transaction() as conn:
            conn.execute(
                """
                INSERT INTO achievement_matches (
                    room_id, game_type, stake, initiator_player_id,
                    rematch_of_room_id, rematch_root_room_id, created_at,
                    terminal_at, terminal_reason, normal_outcome,
                    participant_count, winner_player_id, is_draw,
                    result_json, recorded_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'game_result', 1,
                          ?, ?, ?, '{}', ?)
                """,
                (
                    room_id, game_type, stake, participants[0][0],
                    rematch_of_room_id, rematch_root_room_id, created_at,
                    terminal_at, len(participants), winner_player_id,
                    int(winner_player_id is None), terminal_at,
                ),
            )
            for seat_index, (
                player_id, subject_type, participant_kind, outcome
            ) in enumerate(participants):
                conn.execute(
                    """
                    INSERT INTO achievement_match_participants (
                        room_id, player_id, subject_type, subject_id,
                        participant_kind, seat_index, outcome, chip_delta
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 0)
                    """,
                    (
                        room_id, player_id, subject_type,
                        player_id if subject_type else None,
                        participant_kind, seat_index, outcome,
                    ),
                )

    def test_catalog_is_stable_complete_and_rewarded(self):
        self.assertEqual(len(achievements.ACHIEVEMENT_CATALOG), 54)
        ids = [item.id for item in achievements.ACHIEVEMENT_CATALOG]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertTrue(all(item.reward in {5, 10, 20, 30} for item in achievements.ACHIEVEMENT_CATALOG))
        self.assertEqual(achievements.DEFINITIONS["pair_five_wins_each"].name, "相爱相杀")
        self.assertTrue(achievements.DEFINITIONS["last_move_comeback_win"].hidden)
        self.assertEqual(
            achievements.PRODUCTION_NPC_NAMES,
            {
                "xu_zhi_heng": "许知衡",
                "yue_ming_chuan": "岳鸣川",
                "wen_xing_zhi": "温行止",
                "tang_yi": "唐熠",
                "shang_ling_yi": "商令仪",
                "qiao_mai": "乔麦",
            },
        )

    def test_migration_and_strict_backfill_are_idempotent(self):
        database.init_db()
        database.init_db()
        room = framework.create_room(
            "tictactoe", "human_first", "human", "legacy-human", "legacy-ai"
        )
        timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with database.write_transaction() as conn:
            conn.execute(
                """
                UPDATE rooms SET status = 'finished', winner = 'ai',
                    winner_player_id = 'legacy-ai', result_json = ?, revision = 1,
                    terminal_at = ?, updated_at = ?, stake = 200,
                    initiator_player_id = 'legacy-ai', preserved = 1
                WHERE room_id = ?
                """,
                (json.dumps({"winner_player_id": "legacy-ai", "draw": False}), timestamp, timestamp, room["room_id"]),
            )
            conn.execute(
                """
                INSERT INTO room_messages (
                    room_id, sender, sender_player_id, text, revision_at_send,
                    created_at, event_type, move_label, move_payload
                ) VALUES (?, 'ai', 'legacy-ai', '', 1, ?, 'move', 'A1', '{}')
                """,
                (room["room_id"], timestamp),
            )
            # Old rooms cannot prove an opening balance merely from today's wallet.
            conn.execute(
                "DELETE FROM achievement_room_openings WHERE room_id = ?",
                (room["room_id"],),
            )
            conn.execute(
                "DELETE FROM achievement_events WHERE room_id = ?",
                (room["room_id"],),
            )
            self.assertEqual(achievements.backfill_authoritative_matches(conn), 1)
            self.assertEqual(achievements.backfill_authoritative_matches(conn), 0)
        database.init_db()
        with database.connect() as conn:
            self.assertEqual(
                conn.execute(
                    "SELECT COUNT(*) FROM achievement_matches WHERE room_id = ?",
                    (room["room_id"],),
                ).fetchone()[0],
                1,
            )
            self.assertIsNone(
                conn.execute(
                    "SELECT 1 FROM achievement_unlocks WHERE subject_id = 'legacy-human' AND achievement_id = 'all_in_loss'"
                ).fetchone()
            )
            self.assertIsNone(
                conn.execute(
                    "SELECT 1 FROM achievement_unlocks WHERE subject_id = 'legacy-human' AND achievement_id = 'lose_100_in_game'"
                ).fetchone()
            )
            self.assertIsNotNone(
                conn.execute(
                    "SELECT 1 FROM achievement_unlocks WHERE subject_id = 'legacy-ai' AND achievement_id = 'ai_creates_room'"
                ).fetchone()
            )
            self.assertIsNotNone(
                conn.execute(
                    "SELECT 1 FROM achievement_unlocks WHERE subject_id = 'legacy-human' AND achievement_id = 'human_preserve_loss'"
                ).fetchone()
            )

    def test_duplicate_terminal_and_concurrent_event_evaluation_never_repeat_rewards(self):
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with database.write_transaction() as conn:
            conn.execute(
                """
                INSERT INTO achievement_matches (
                    room_id, game_type, stake, initiator_player_id,
                    rematch_of_room_id, rematch_root_room_id, created_at,
                    terminal_at, terminal_reason, normal_outcome,
                    participant_count, winner_player_id, is_draw,
                    result_json, recorded_at
                ) VALUES ('RAWROOM1', 'tictactoe', 0, 'race-human', NULL, NULL,
                          ?, ?, 'game_result', 1, 2, 'race-human', 0, '{}', ?)
                """,
                (now, now, now),
            )
            conn.execute(
                """
                INSERT INTO achievement_match_participants (
                    room_id, player_id, subject_type, subject_id,
                    participant_kind, seat_index, outcome, chip_delta
                ) VALUES ('RAWROOM1', 'race-human', 'human', 'race-human',
                          'human', 0, 'win', 0)
                """
            )
        def evaluate(_):
            with database.write_transaction() as conn:
                return achievements.evaluate_subject(
                    conn, "human", "race-human",
                    source_type="duel_room", source_id="RAWROOM1",
                )

        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(evaluate, range(2)))
        with database.connect() as conn:
            duplicates = conn.execute(
                """
                SELECT achievement_id, COUNT(*) AS count
                FROM achievement_unlocks
                WHERE subject_type = 'human' AND subject_id = 'race-human'
                GROUP BY achievement_id HAVING count > 1
                """
            ).fetchall()
            self.assertEqual(duplicates, [])
            rewards = conn.execute(
                """
                SELECT reference_id, COUNT(*) AS count FROM chip_ledger AS ledger
                JOIN chip_wallets AS wallet ON wallet.id = ledger.wallet_id
                WHERE wallet.subject_type = 'human' AND wallet.subject_id = 'race-human'
                  AND ledger.transaction_type = 'achievement_reward'
                GROUP BY reference_id
                """
            ).fetchall()
            self.assertTrue(rewards)
            self.assertTrue(all(row["count"] == 1 for row in rewards))

    def test_hidden_is_absent_until_unlock_and_all_in_uses_opening_balance(self):
        before = achievements.get_achievements("human", "all-in-human")
        serialized = json.dumps(before, ensure_ascii=False)
        self.assertNotIn("all_in_loss", serialized)
        self.assertNotIn("倾家荡产", serialized)
        self.assertEqual(before["summary"]["hidden_unlocked"], 0)

        room = framework.create_room(
            "tictactoe", "human_first", "human", "all-in-human", "all-in-ai",
            stake=200,
        )
        framework.respond_to_invitation(room["room_id"], "ai", "all-in-ai", "accept")
        terminal = framework.resign(room["room_id"], "human", "all-in-human")
        human_unlock_ids = {
            item["id"] for item in terminal.get("achievement_unlocks", [])
            if item["subject_id"] == "all-in-human"
        }
        self.assertIn("all_in_loss", human_unlock_ids)
        after = achievements.get_achievements("human", "all-in-human")
        hidden = next(section for section in after["sections"] if section["id"] == "hidden")
        self.assertIn("all_in_loss", {item["id"] for item in hidden["items"]})
        self.assertEqual(after["summary"]["total"], before["summary"]["total"])

    def test_pair_progress_isolated_across_multiple_bound_machines(self):
        for _ in range(10):
            self.finish_human_win("pair-human", "pair-ai-1")
        self.finish_human_win("pair-human", "pair-ai-2")
        first = achievements.get_achievements(
            "ai", "pair-ai-1", bound_human_id="pair-human"
        )
        second = achievements.get_achievements(
            "ai", "pair-ai-2", bound_human_id="pair-human"
        )
        first_relation = next(section for section in first["sections"] if section["id"] == "relationship")
        second_relation = next(section for section in second["sections"] if section["id"] == "relationship")
        first_ten = next(item for item in first_relation["items"] if item["id"] == "pair_ten_games")
        second_ten = next(item for item in second_relation["items"] if item["id"] == "pair_ten_games")
        self.assertTrue(first_ten["unlocked"])
        self.assertEqual(first_ten["progress"], {"current": 10, "target": 10})
        self.assertFalse(second_ten["unlocked"])
        self.assertEqual(second_ten["progress"], {"current": 1, "target": 10})

    def test_pair_check_in_unlocks_both_sides_on_shanghai_date(self):
        human = chips.claim_daily_check_in(
            "human", "check-human", bound_ai_ids=["check-ai"]
        )
        self.assertNotIn("pair_same_day_check_in", {item["id"] for item in human["unlocks"]})
        machine = chips.claim_daily_check_in(
            "ai", "check-ai", bound_human_ids=["check-human"]
        )
        pair_unlocks = [
            item for item in machine["unlocks"]
            if item["id"] == "pair_same_day_check_in"
        ]
        self.assertEqual(
            {(item["subject_type"], item["subject_id"]) for item in pair_unlocks},
            {("human", "check-human"), ("ai", "check-ai")},
        )

    def test_relationship_reward_recovers_bankruptcy_in_same_transaction(self):
        chips.change_balance("human", "recover-human", -700, "test_setup")
        chips.declare_bankruptcy("human", "recover-human")
        chips.change_balance("human", "recover-human", 140, "test_setup")
        self.assertEqual(chips.get_wallet("human", "recover-human")["balance"], 195)
        self.assertTrue(chips.get_wallet("human", "recover-human")["bankruptcy_active"])

        self.insert_match_fact(
            "RECOVER1",
            [
                ("recover-human", "human", "human", "win"),
                ("recover-ai", "ai", "bound_machine", "loss"),
            ],
            winner_player_id="recover-human",
        )
        with database.write_transaction() as conn:
            conn.execute(
                """
                INSERT INTO achievement_relationships (human_id, ai_id, created_at)
                VALUES ('recover-human', 'recover-ai', ?)
                """,
                (datetime.now(timezone.utc).isoformat(timespec="seconds"),),
            )
            unlocks = achievements.evaluate_relationship(
                conn, "recover-human", "recover-ai",
                source_type="duel_room", source_id="RECOVER1",
            )
        self.assertEqual(
            {item["id"] for item in unlocks if item["subject_id"] == "recover-human"},
            {"pair_first_game", "bankruptcy_recovery"},
        )
        recovered = chips.get_wallet("human", "recover-human")
        self.assertEqual(recovered["balance"], 210)
        self.assertFalse(recovered["bankruptcy_active"])

    def test_bound_machine_loss_requires_the_machine_to_be_the_winner(self):
        self.insert_match_fact(
            "THIRDP01",
            [
                ("winner-human", "human", "human", "loss"),
                ("table-ai", "ai", "bound_machine", "loss"),
                ("npc:third", None, "system_npc", "win"),
            ],
            winner_player_id="npc:third",
            game_type="dots_boxes",
        )
        with database.write_transaction() as conn:
            achievements.evaluate_subject(
                conn, "human", "winner-human",
                source_type="duel_room", source_id="THIRDP01",
            )
        before = achievements.get_achievements("human", "winner-human")
        human_items = next(
            section["items"] for section in before["sections"]
            if section["id"] == "human"
        )
        machine_loss = next(
            item for item in human_items
            if item["id"] == "human_loses_to_bound_ai"
        )
        self.assertFalse(machine_loss["unlocked"])
        self.assertEqual(machine_loss["progress"], {"current": 0, "target": 1})

        self.insert_match_fact(
            "BOUNDW01",
            [
                ("winner-human", "human", "human", "loss"),
                ("table-ai", "ai", "bound_machine", "win"),
            ],
            winner_player_id="table-ai",
        )
        with database.write_transaction() as conn:
            unlocks = achievements.evaluate_subject(
                conn, "human", "winner-human",
                source_type="duel_room", source_id="BOUNDW01",
            )
        self.assertIn("human_loses_to_bound_ai", {item["id"] for item in unlocks})

    def test_interrupted_streak_progress_does_not_show_historical_best(self):
        for index, outcome in enumerate(("win", "win", "loss"), start=1):
            winner = "streak-ai" if outcome == "win" else "streak-human"
            self.insert_match_fact(
                f"ASTRK{index:03d}",
                [
                    ("streak-human", "human", "human", "loss" if outcome == "win" else "win"),
                    ("streak-ai", "ai", "bound_machine", outcome),
                ],
                winner_player_id=winner,
            )
        self.insert_match_fact(
            "ASTRKB00",
            [
                ("streak-human", "human", "human", "win"),
                ("streak-ai", "ai", "bound_machine", "loss"),
            ],
            winner_player_id="streak-human",
            stake=1,
        )
        with database.write_transaction() as conn:
            achievements.evaluate_subject(
                conn, "ai", "streak-ai",
                source_type="duel_room", source_id="ASTRKB00",
            )
            win_streak = conn.execute(
                """
                SELECT current_value FROM achievement_progress
                WHERE subject_type = 'ai' AND subject_id = 'streak-ai'
                  AND achievement_id = 'ai_three_win_streak'
                """
            ).fetchone()[0]
            zero_streak = conn.execute(
                """
                SELECT current_value FROM achievement_progress
                WHERE subject_type = 'ai' AND subject_id = 'streak-ai'
                  AND achievement_id = 'five_zero_stake_same_opponent'
                """
            ).fetchone()[0]
        self.assertEqual(win_streak, 0)
        self.assertEqual(zero_streak, 0)

        for index in range(3):
            self.insert_match_fact(
                f"HLSS{index:04d}",
                [
                    ("loss-human", "human", "human", "loss"),
                    ("loss-ai", "ai", "bound_machine", "win"),
                ],
                winner_player_id="loss-ai",
            )
        self.insert_match_fact(
            "HLSSDRAW",
            [
                ("loss-human", "human", "human", "draw"),
                ("loss-ai", "ai", "bound_machine", "draw"),
            ],
        )
        with database.write_transaction() as conn:
            achievements.evaluate_subject(
                conn, "human", "loss-human",
                source_type="duel_room", source_id="HLSSDRAW",
            )
            comeback = conn.execute(
                """
                SELECT current_value FROM achievement_progress
                WHERE subject_type = 'human' AND subject_id = 'loss-human'
                  AND achievement_id = 'win_after_three_losses'
                """
            ).fetchone()[0]
        self.assertEqual(comeback, 0)

    def test_ai_revenge_counts_only_consecutive_losses_won_by_same_human(self):
        for index in range(3):
            self.insert_match_fact(
                f"REVNPC{index}",
                [
                    ("revenge-human", "human", "human", "loss"),
                    ("revenge-ai", "ai", "bound_machine", "loss"),
                    ("npc:revenge", None, "system_npc", "win"),
                ],
                winner_player_id="npc:revenge",
                game_type="dots_boxes",
            )
        self.insert_match_fact(
            "REVWIN00",
            [
                ("revenge-human", "human", "human", "loss"),
                ("revenge-ai", "ai", "bound_machine", "win"),
            ],
            winner_player_id="revenge-ai",
        )
        with database.write_transaction() as conn:
            achievements.evaluate_subject(
                conn, "ai", "revenge-ai",
                source_type="duel_room", source_id="REVWIN00",
            )
            mistaken = conn.execute(
                """
                SELECT 1 FROM achievement_unlocks
                WHERE subject_type = 'ai' AND subject_id = 'revenge-ai'
                  AND achievement_id = 'ai_revenge_bound_human'
                """
            ).fetchone()
        self.assertIsNone(mistaken)

        for index in range(3):
            self.insert_match_fact(
                f"REVLSS{index}",
                [
                    ("revenge-human", "human", "human", "win"),
                    ("revenge-ai", "ai", "bound_machine", "loss"),
                ],
                winner_player_id="revenge-human",
            )
        self.insert_match_fact(
            "REVDONE0",
            [
                ("revenge-human", "human", "human", "loss"),
                ("revenge-ai", "ai", "bound_machine", "win"),
            ],
            winner_player_id="revenge-ai",
        )
        with database.write_transaction() as conn:
            unlocks = achievements.evaluate_subject(
                conn, "ai", "revenge-ai",
                source_type="duel_room", source_id="REVDONE0",
            )
        self.assertIn("ai_revenge_bound_human", {item["id"] for item in unlocks})

    def test_rematch_chain_uses_longest_direct_path_not_root_branch_count(self):
        participants = [
            ("chain-human", "human", "human", "win"),
            ("chain-ai", "ai", "bound_machine", "loss"),
        ]
        self.insert_match_fact(
            "CHAIN000", participants, winner_player_id="chain-human"
        )
        for index in range(1, 10):
            self.insert_match_fact(
                f"BRANCH{index}", participants,
                winner_player_id="chain-human",
                rematch_of_room_id="CHAIN000",
                rematch_root_room_id="CHAIN000",
            )
        with database.write_transaction() as conn:
            achievements.evaluate_subject(
                conn, "human", "chain-human",
                source_type="duel_room", source_id="BRANCH9",
            )
            progress = conn.execute(
                """
                SELECT current_value FROM achievement_progress
                WHERE subject_type = 'human' AND subject_id = 'chain-human'
                  AND achievement_id = 'ten_game_rematch_chain'
                """
            ).fetchone()[0]
            unlock = conn.execute(
                """
                SELECT 1 FROM achievement_unlocks
                WHERE subject_type = 'human' AND subject_id = 'chain-human'
                  AND achievement_id = 'ten_game_rematch_chain'
                """
            ).fetchone()
        self.assertEqual(progress, 2)
        self.assertIsNone(unlock)

        previous = "BRANCH1"
        for index in range(1, 9):
            room_id = f"LINEAR{index}"
            self.insert_match_fact(
                room_id, participants, winner_player_id="chain-human",
                rematch_of_room_id=previous,
                rematch_root_room_id="CHAIN000",
            )
            previous = room_id
        with database.write_transaction() as conn:
            unlocks = achievements.evaluate_subject(
                conn, "human", "chain-human",
                source_type="duel_room", source_id=previous,
            )
        self.assertIn("ten_game_rematch_chain", {item["id"] for item in unlocks})

    def test_npc_unlock_uses_persona_id_and_npc_gets_no_wallet(self):
        room = {
            "room_id": "NPCFACT1", "game_type": "dots_boxes", "stake": 0,
            "initiator_player_id": "npc-human", "rematch_of_room_id": None,
            "rematch_root_room_id": None,
            "created_at": "2026-08-28T00:00:00+00:00",
            "terminal_at": "2026-08-28T00:10:00+00:00",
            "updated_at": "2026-08-28T00:10:00+00:00",
            "winner": "human", "winner_player_id": "npc-human", "result": {},
            "participants": [
                {"player_id": "npc-human", "participant_kind": "human", "role": "human", "seat_index": 0, "token": "X"},
                {"player_id": "npc:not-the-name", "participant_kind": "system_npc", "role": "ai", "seat_index": 1, "token": "O", "npc_persona_id": "xu_zhi_heng", "display_name": "完全不同的显示名"},
            ],
        }
        with database.write_transaction() as conn:
            achievements.record_room_created(conn, room)
            achievements.record_terminal_room(conn, room, "game_result", normal=True)
        payload = achievements.get_achievements("human", "npc-human")
        npc_section = next(section for section in payload["sections"] if section["id"] == "npc")
        self.assertIn("defeat_npc_xu_zhi_heng", {item["id"] for item in npc_section["items"]})
        with database.connect() as conn:
            self.assertIsNone(
                conn.execute("SELECT 1 FROM chip_wallets WHERE subject_id = 'npc:not-the-name'").fetchone()
            )
            self.assertIsNone(
                conn.execute("SELECT 1 FROM achievement_unlocks WHERE subject_id = 'npc:not-the-name'").fetchone()
            )

    def test_jungle_rat_capture_is_proven_by_authoritative_piece_payload(self):
        room = {
            "room_id": "RATFACT1", "game_type": "jungle", "revision": 8,
            "board_state": {
                "last_move": {
                    "captured": "O:E", "mark": "X", "to_row": 4, "to_col": 3,
                },
                "board": [[None] * 7 for _ in range(9)],
            },
        }
        room["board_state"]["board"][4][3] = "X:R"
        actor = {
            "player_id": "rat-human", "participant_kind": "human",
            "role": "human", "token": "X",
        }
        with database.write_transaction() as conn:
            unlocks = achievements.record_special_move(
                conn, room, actor,
                {"from_row": 4, "from_col": 2, "to_row": 4, "to_col": 3},
            )
            repeated = achievements.record_special_move(
                conn, room, actor,
                {"from_row": 4, "from_col": 2, "to_row": 4, "to_col": 3},
            )
        self.assertIn("jungle_rat_captures_elephant", {item["id"] for item in unlocks})
        self.assertEqual(repeated, [])
        payload = achievements.get_achievements("human", "rat-human")
        hidden = next(section for section in payload["sections"] if section["id"] == "hidden")
        self.assertIn("jungle_rat_captures_elephant", {item["id"] for item in hidden["items"]})

    def test_forced_archive_and_active_leave_do_not_count_as_normal(self):
        stale = framework.create_room(
            "tictactoe", "human_first", "human", "stale-human", "stale-ai"
        )
        cutoff = (datetime.now(timezone.utc) - timedelta(days=31)).isoformat(timespec="seconds")
        with database.write_transaction() as conn:
            conn.execute("UPDATE rooms SET last_move_at = ? WHERE room_id = ?", (cutoff, stale["room_id"]))
        self.assertEqual(framework.get_room(stale["room_id"])["status"], "archived")
        left = framework.create_room(
            "tictactoe", "human_first", "human", "left-human", "left-ai"
        )
        framework.leave_room(left["room_id"], "human", "left-human")
        with database.connect() as conn:
            rows = conn.execute(
                "SELECT room_id, normal_outcome FROM achievement_matches WHERE room_id IN (?, ?)",
                (stale["room_id"], left["room_id"]),
            ).fetchall()
        self.assertEqual({row["room_id"]: row["normal_outcome"] for row in rows}, {stale["room_id"]: 0, left["room_id"]: 0})
        self.assertEqual(achievements.get_achievements("human", "stale-human")["summary"]["unlocked"], 0)
        self.assertEqual(achievements.get_achievements("human", "left-human")["summary"]["unlocked"], 0)

    def test_room_deletion_cannot_delete_achievement_history(self):
        room = self.finish_human_win("delete-human", "delete-ai")
        before = achievements.get_achievements("human", "delete-human")
        framework.delete_terminal_room(room["room_id"], "delete-human")
        after = achievements.get_achievements("human", "delete-human")
        self.assertEqual(after, before)
        with database.connect() as conn:
            self.assertIsNotNone(
                conn.execute("SELECT 1 FROM achievement_matches WHERE room_id = ?", (room["room_id"],)).fetchone()
            )

    def test_room_ids_are_never_reused_from_permanent_achievement_facts(self):
        self.insert_match_fact(
            "AAAAAAAA",
            [
                ("id-human", "human", "human", "win"),
                ("id-ai", "ai", "bound_machine", "loss"),
            ],
            winner_player_id="id-human",
        )
        with database.write_transaction() as conn:
            conn.execute(
                """
                INSERT INTO achievement_room_openings (
                    room_id, player_id, subject_type, subject_id,
                    participant_kind, opening_balance, created_at
                ) VALUES ('BBBBBBBB', 'old-human', 'human', 'old-human',
                          'human', 200, ?)
                """,
                (datetime.now(timezone.utc).isoformat(timespec="seconds"),),
            )
        with patch.object(
            framework.secrets,
            "choice",
            side_effect=list("AAAAAAAA" "BBBBBBBB" "CCCCCCCC"),
        ):
            room = framework.create_room(
                "tictactoe", "human_first", "human", "new-human", "new-ai"
            )
        self.assertEqual(room["room_id"], "CCCCCCCC")

    def test_authoritative_rematch_and_preserved_loss(self):
        first = framework.create_room(
            "tictactoe", "human_first", "human", "rematch-human", "rematch-ai"
        )
        first = framework.resign(first["room_id"], "human", "rematch-human")
        preserved = framework.set_room_preserved(first["room_id"], "rematch-human", True)
        self.assertIn(
            "human_preserve_loss",
            {item["id"] for item in preserved.get("achievement_unlocks", [])},
        )
        rematch = framework.create_room(
            "tictactoe", "ai_first", "human", "rematch-human", "rematch-ai",
            rematch_of_room_id=first["room_id"],
        )
        self.assertEqual(rematch["rematch_of_room_id"], first["room_id"])
        self.assertIn(
            "human_rematch_after_loss",
            {item["id"] for item in rematch.get("achievement_unlocks", [])},
        )
        with self.assertRaisesRegex(framework.DuelError, "同一批参与者"):
            framework.create_room(
                "tictactoe", "ai_first", "human", "rematch-human", "other-ai",
                rematch_of_room_id=first["room_id"],
            )


class AchievementApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="duel-achievement-api-")
        self.db_patch = patch.object(
            database, "DB_PATH", Path(self.temporary.name) / "test.db"
        )
        self.db_patch.start()
        database.init_db()
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=main_module.app),
            base_url="http://duel.test",
        )

    async def asyncTearDown(self):
        await self.client.aclose()
        self.db_patch.stop()
        self.temporary.cleanup()

    @staticmethod
    def headers():
        encoded = base64.urlsafe_b64encode(
            json.dumps([{"id": "api-ai", "name": "小机"}]).encode()
        ).decode().rstrip("=")
        return {
            "X-Duel-Human-Player": "api-human",
            "X-Duel-Human-Name": "%E4%BA%BA%E7%B1%BB",
            "X-Duel-Bound-Ais": encoded,
        }

    async def test_human_machine_api_and_mcp_categories_are_identity_safe(self):
        human = await self.client.get("/api/chips", headers=self.headers())
        self.assertEqual(human.status_code, 200, human.text)
        self.assertEqual(
            [section["id"] for section in human.json()["achievements"]["sections"]],
            ["common", "human"],
        )
        machine = await self.client.get(
            "/api/chips/machines/api-ai", headers=self.headers()
        )
        self.assertEqual(machine.status_code, 200, machine.text)
        self.assertEqual(
            [section["id"] for section in machine.json()["achievements"]["sections"]],
            ["common", "ai", "relationship"],
        )
        mcp = await self.client.post(
            "/mcp/play",
            json={
                "action": "chips", "op": "achievements",
                "player_id": "api-ai", "opponent_id": "api-human",
            },
        )
        payload = mcp.json()["achievements"]
        self.assertLess(len(json.dumps(payload, ensure_ascii=False)), 6000)
        self.assertEqual([section["id"] for section in payload["sections"]], ["common", "ai", "relationship"])
        self.assertNotIn("last_move_comeback_win", json.dumps(payload))

    async def test_human_check_in_never_projects_machine_only_repair_unlocks(self):
        chips.claim_daily_check_in(
            "ai", "api-ai", bound_human_ids=["api-human"]
        )
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with database.write_transaction() as conn:
            conn.execute(
                """
                INSERT INTO achievement_unlocks (
                    subject_type, subject_id, achievement_id, context_key,
                    reward, unlocked_at, source_type, source_id
                ) VALUES ('human', 'api-human', 'pair_same_day_check_in',
                          'api-human|api-ai', 10, ?, 'repair_fixture', NULL)
                """,
                (now,),
            )
        response = await self.client.post(
            "/api/chips/check-in", headers=self.headers(), json={},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertNotIn("unlocks", response.json())
        with database.connect() as conn:
            machine_unlock = conn.execute(
                """
                SELECT 1 FROM achievement_unlocks
                WHERE subject_type = 'ai' AND subject_id = 'api-ai'
                  AND achievement_id = 'pair_same_day_check_in'
                  AND context_key = 'api-human|api-ai'
                """
            ).fetchone()
        self.assertIsNotNone(machine_unlock)

    async def test_ai_can_initiate_symmetric_authoritative_rematch(self):
        first = framework.create_room(
            "tictactoe", "human_first", "human", "api-human", "api-ai"
        )
        framework.resign(first["room_id"], "human", "api-human")
        response = await self.client.post(
            "/mcp/play",
            json={
                "action": "rematch", "player_id": "api-ai",
                "opponent_id": "api-human", "room_id": first["room_id"],
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertIn("ai_authoritative_rematch", {item["id"] for item in payload["unlocks"]})
        self.assertEqual(payload["room"]["rematch_of_room_id"], first["room_id"])

    async def test_terminal_request_returns_only_viewers_compact_unlocks(self):
        room = framework.create_room(
            "tictactoe", "human_first", "human", "api-human", "api-ai"
        )
        moves = [
            ("human", "api-human", 0, 0), ("ai", "api-ai", 1, 0),
            ("human", "api-human", 0, 1), ("ai", "api-ai", 1, 1),
        ]
        for role, player_id, row, col in moves:
            framework.play_move(
                room["room_id"], role, player_id, {"row": row, "col": col}
            )
        response = await self.client.post(
            f"/api/rooms/{room['room_id']}/move",
            json={"player_id": "api-human", "move": {"row": 0, "col": 2}},
        )
        self.assertEqual(response.status_code, 200, response.text)
        unlocks = response.json()["unlocks"]
        self.assertIn("first_normal_game", {item["id"] for item in unlocks})
        self.assertTrue(all("subject_id" not in item for item in unlocks))
        self.assertNotIn("ai_beats_bound_human", {item["id"] for item in unlocks})


if __name__ == "__main__":
    unittest.main()

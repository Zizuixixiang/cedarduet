import base64
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import httpx

from app import chips, database, framework
from app import main as main_module


class StakeFrameworkTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="duel-stakes-")
        self.db_patch = patch.object(
            database, "DB_PATH", Path(self.temporary.name) / "test.db"
        )
        self.db_patch.start()
        database.init_db()

    def tearDown(self):
        self.db_patch.stop()
        self.temporary.cleanup()

    def paired_room(self, stake=5, mode="human_first"):
        room = framework.create_room(
            "tictactoe", mode, "human", "human-1", "ai-1", stake=stake
        )
        if stake:
            room = framework.respond_to_invitation(
                room["room_id"], "ai", "ai-1", "accept"
            )
        return room

    def ledger_types(self, subject_type, subject_id):
        return [
            item["transaction_type"]
            for item in chips.list_ledger(subject_type, subject_id)
        ]

    def test_zero_stake_starts_immediately_and_writes_no_game_ledger(self):
        room = self.paired_room(stake=0)
        self.assertEqual(room["status"], "playing")
        moves = [
            ("human", "human-1", 0, 0),
            ("ai", "ai-1", 1, 0),
            ("human", "human-1", 0, 1),
            ("ai", "ai-1", 1, 1),
            ("human", "human-1", 0, 2),
        ]
        for role, player_id, row, col in moves:
            room = framework.play_move(
                room["room_id"], role, player_id, {"row": row, "col": col}
            )
        self.assertEqual(room["winner"], "human")
        conn = database.connect()
        try:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM chip_wallets").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM chip_ledger").fetchone()[0], 0)
        finally:
            conn.close()

    def test_nonzero_room_is_pending_until_accept_and_reject_cancels(self):
        pending = framework.create_room(
            "gomoku", "human_first", "human", "human-a", "ai-a", stake=8
        )
        self.assertEqual(pending["status"], "pending")
        self.assertEqual(pending["pending_for"], ["ai-a"])
        accepted = framework.respond_to_invitation(
            pending["room_id"], "ai", "ai-a", "accept"
        )
        self.assertEqual(accepted["status"], "playing")

        rejected = framework.create_room(
            "othello", "ai_first", "human", "human-b", "ai-b", stake=9
        )
        result = framework.respond_to_invitation(
            rejected["room_id"], "ai", "ai-b", "reject"
        )
        self.assertEqual(result["status"], "cancelled")
        with self.assertRaisesRegex(framework.DuelError, "不存在"):
            framework.get_room(rejected["room_id"])

    def test_pending_invitation_expires_lazily_after_24_hours_without_chips(self):
        pending = framework.create_room(
            "connect4", "human_first", "human", "human-old", "ai-old", stake=12
        )
        expired = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(
            timespec="seconds"
        )
        with database.write_transaction() as conn:
            conn.execute(
                "UPDATE rooms SET confirmation_expires_at = ? WHERE room_id = ?",
                (expired, pending["room_id"]),
            )
        self.assertEqual(framework.list_ai_rooms("ai-old"), [])
        with self.assertRaisesRegex(framework.DuelError, "不存在"):
            framework.get_room(pending["room_id"])
        conn = database.connect()
        try:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM chip_ledger").fetchone()[0], 0)
        finally:
            conn.close()

    def test_ai_can_only_accept_its_own_invitation(self):
        pending = framework.create_room(
            "dots_boxes", "human_first", "human", "human-owner", "ai-owner", stake=4
        )
        with self.assertRaisesRegex(framework.DuelError, "席位不匹配"):
            framework.respond_to_invitation(
                pending["room_id"], "ai", "ai-attacker", "accept"
            )
        self.assertEqual(framework.get_room(pending["room_id"])["status"], "pending")

    def test_win_loss_and_repeat_settlement_are_atomic_and_idempotent(self):
        room = self.paired_room(stake=5)
        for role, player_id, move in (
            ("human", "human-1", {"row": 0, "col": 0}),
            ("ai", "ai-1", {"row": 1, "col": 0}),
            ("human", "human-1", {"row": 0, "col": 1}),
            ("ai", "ai-1", {"row": 1, "col": 1}),
            ("human", "human-1", {"row": 0, "col": 2}),
        ):
            room = framework.play_move(room["room_id"], role, player_id, move)
        self.assertEqual(chips.get_wallet("human", "human-1")["balance"], 205)
        self.assertEqual(chips.get_wallet("ai", "ai-1")["balance"], 195)

        with database.write_transaction() as conn:
            row = conn.execute(
                "SELECT * FROM rooms WHERE room_id = ?", (room["room_id"],)
            ).fetchone()
            decoded = database.decode_room(row, conn)
            self.assertFalse(framework._settle_terminal_room(conn, decoded))
        framework.get_room(room["room_id"])
        self.assertEqual(chips.get_wallet("human", "human-1")["balance"], 205)
        self.assertEqual(chips.get_wallet("ai", "ai-1")["balance"], 195)
        self.assertEqual(self.ledger_types("human", "human-1").count("duel_win"), 1)
        self.assertEqual(self.ledger_types("ai", "ai-1").count("duel_loss"), 1)

    def test_draw_has_no_chip_change_or_settlement_ledger(self):
        room = self.paired_room(stake=7)
        sequence = [
            ("human", "human-1", 0, 0), ("ai", "ai-1", 0, 1),
            ("human", "human-1", 0, 2), ("ai", "ai-1", 1, 1),
            ("human", "human-1", 1, 0), ("ai", "ai-1", 1, 2),
            ("human", "human-1", 2, 1), ("ai", "ai-1", 2, 0),
            ("human", "human-1", 2, 2),
        ]
        for role, player_id, row, col in sequence:
            room = framework.play_move(
                room["room_id"], role, player_id, {"row": row, "col": col}
            )
        self.assertEqual(room["winner"], "draw")
        self.assertNotIn("duel_win", self.ledger_types("human", "human-1"))
        self.assertNotIn("duel_loss", self.ledger_types("ai", "ai-1"))
        self.assertEqual(chips.get_wallet("human", "human-1")["balance"], 200)
        self.assertEqual(chips.get_wallet("ai", "ai-1")["balance"], 200)

    def test_resign_settles_loss_and_negative_balance_is_allowed(self):
        chips.change_balance("human", "human-1", -205, "test_setup")
        room = self.paired_room(stake=10)
        room = framework.resign(room["room_id"], "human", "human-1")
        self.assertEqual(room["winner"], "ai")
        self.assertEqual(chips.get_wallet("human", "human-1")["balance"], -15)
        self.assertEqual(chips.get_wallet("ai", "ai-1")["balance"], 210)

    def test_nonzero_rematch_contract_requires_fresh_confirmation(self):
        first = self.paired_room(stake=6)
        framework.resign(first["room_id"], "human", "human-1")
        rematch = framework.create_room(
            first["game_type"], "ai_first", "human", "human-1", "ai-1",
            stake=first["stake"],
        )
        self.assertEqual(rematch["stake"], 6)
        self.assertEqual(rematch["status"], "pending")
        self.assertEqual(rematch["pending_for"], ["ai-1"])


class StakeHttpTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="duel-stakes-http-")
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
        machines = [{"id": "ai-web", "name": "小紫"}]
        encoded = base64.urlsafe_b64encode(
            json.dumps(machines, ensure_ascii=False).encode("utf-8")
        ).decode("ascii").rstrip("=")
        return {
            "X-Duel-Human-Player": "human-web",
            "X-Duel-Human-Name": "%E5%8D%97%E5%B1%B1",
            "X-Duel-Bound-Ais": encoded,
        }

    async def test_human_create_pending_survives_refresh_then_ai_accepts(self):
        created = await self.client.post(
            "/api/rooms", headers=self.headers(),
            json={
                "player_id": "human-web", "ai_player": "ai-web",
                "game_type": "tictactoe", "mode": "human_first", "stake": 3,
            },
        )
        self.assertEqual(created.status_code, 200, created.text)
        room_id = created.json()["room"]["room_id"]
        self.assertEqual(created.json()["room"]["status"], "pending")

        refreshed = await self.client.get("/api/whoami", headers=self.headers())
        self.assertIn(room_id, {item["room_id"] for item in refreshed.json()["rooms"]})
        listed = await self.client.post(
            "/mcp/play", json={"action": "rooms", "player_id": "ai-web"}
        )
        summary = next(item for item in listed.json()["rooms"] if item["room_id"] == room_id)
        self.assertEqual(summary["status"], "pending")
        self.assertEqual(summary["confirmation_decision"], "pending")

        accepted = await self.client.post(
            "/mcp/play",
            json={"action": "accept", "player_id": "ai-web", "room_id": room_id},
        )
        self.assertEqual(accepted.status_code, 200, accepted.text)
        self.assertEqual(accepted.json()["room"]["status"], "playing")

    async def test_ai_initiated_invite_appears_for_human_accept_or_reject(self):
        created = await self.client.post(
            "/mcp/play",
            json={
                "action": "new", "player_id": "ai-web", "opponent_id": "human-web",
                "game_type": "jungle", "mode": "ai_first", "stake": 11,
            },
        )
        self.assertEqual(created.json()["status"], "pending")
        self.assertNotIn("room", created.json())
        room_id = created.json()["room_id"]
        whoami = await self.client.get("/api/whoami", headers=self.headers())
        pending = whoami.json()["pending_invitations"]
        self.assertEqual([item["room_id"] for item in pending], [room_id])
        self.assertEqual(pending[0]["stake_label"], "🪙11/人")

        attacker = await self.client.post(
            "/mcp/play",
            json={"action": "accept", "player_id": "other-ai", "room_id": room_id},
        )
        self.assertEqual(attacker.status_code, 403)
        accepted = await self.client.post(
            f"/api/rooms/{room_id}/invitation", headers=self.headers(),
            json={"decision": "accept"},
        )
        self.assertEqual(accepted.status_code, 200, accepted.text)
        self.assertEqual(accepted.json()["room"]["status"], "playing")

        second = await self.client.post(
            "/mcp/play",
            json={
                "action": "new", "player_id": "ai-web", "opponent_id": "human-web",
                "game_type": "othello", "mode": "human_first", "stake": 2,
            },
        )
        rejected = await self.client.post(
            f"/api/rooms/{second.json()['room_id']}/invitation",
            headers=self.headers(), json={"decision": "reject"},
        )
        self.assertEqual(rejected.json()["status"], "cancelled")

    async def test_stake_validation_rejects_fraction_and_negative(self):
        for invalid in (-1, 1.5, True, "3"):
            response = await self.client.post(
                "/api/rooms", headers=self.headers(),
                json={
                    "player_id": "human-web", "ai_player": "ai-web",
                    "game_type": "tictactoe", "stake": invalid,
                },
            )
            self.assertEqual(response.status_code, 422, (invalid, response.text))


if __name__ == "__main__":
    unittest.main()

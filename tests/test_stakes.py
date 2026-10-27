import base64
import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import httpx

from app import chips, database, framework, invites
from app import main as main_module
from app.npc_personas import NpcPersona


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
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM chip_wallets").fetchone()[0], 2)
            self.assertEqual(
                conn.execute(
                    "SELECT COUNT(*) FROM chip_ledger WHERE transaction_type IN ('duel_win', 'duel_loss')"
                ).fetchone()[0],
                0,
            )
            self.assertGreater(
                conn.execute(
                    "SELECT COUNT(*) FROM chip_ledger WHERE transaction_type = 'achievement_reward'"
                ).fetchone()[0],
                0,
            )
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
        self.assertEqual(chips.get_wallet("human", "human-1")["balance"], 230)
        self.assertEqual(chips.get_wallet("ai", "ai-1")["balance"], 210)

        with database.write_transaction() as conn:
            row = conn.execute(
                "SELECT * FROM rooms WHERE room_id = ?", (room["room_id"],)
            ).fetchone()
            decoded = database.decode_room(row, conn)
            self.assertFalse(framework._settle_terminal_room(conn, decoded))
        framework.get_room(room["room_id"])
        self.assertEqual(chips.get_wallet("human", "human-1")["balance"], 230)
        self.assertEqual(chips.get_wallet("ai", "ai-1")["balance"], 210)
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
        self.assertEqual(chips.get_wallet("human", "human-1")["balance"], 220)
        self.assertEqual(chips.get_wallet("ai", "ai-1")["balance"], 220)

    def test_negative_balance_cannot_enter_staked_room(self):
        chips.change_balance("human", "human-1", -205, "test_setup")
        with self.assertRaisesRegex(framework.DuelError, "可用筹码不足"):
            self.paired_room(stake=10)
        self.assertEqual(self.paired_room(stake=0)["status"], "playing")

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


class StakeBudgetTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="duel-stake-budget-")
        self.addCleanup(temporary.cleanup)
        db_patch = patch.object(database, "DB_PATH", Path(temporary.name) / "test.db")
        db_patch.start()
        self.addCleanup(db_patch.stop)
        database.init_db()

    def balance(self, role, player_id, amount):
        with database.write_transaction() as conn:
            wallet = chips._ensure_wallet(conn, role, player_id)
            conn.execute("UPDATE chip_wallets SET balance = ? WHERE id = ?", (amount, wallet["id"]))

    def create(self, stake=100, human="human", ai="ai"):
        return framework.create_room("uno", "human_first", "human", human, ai, stake=stake)

    def test_create_checks_initiator_wallet_and_zero_stake_bypasses_budget(self):
        for role in ("human", "ai"):
            for index, (balance, stake) in enumerate([
                (100, 101), (0, 1), (-100, 1), (200, 10**12), (200, 10**100),
            ]):
                with self.subTest(role=role, balance=balance, stake=stake):
                    player_id, opponent = f"{role}-{index}", f"other-{role}-{index}"
                    self.balance(role, player_id, balance)
                    with self.assertRaisesRegex(framework.DuelError, "可用筹码不足"):
                        framework.create_room("uno", "human_first", role, player_id, opponent, stake=stake)
                    free = framework.create_room("uno", "human_first", role, player_id, opponent, stake=0)
                    self.assertEqual(free["status"], "playing")

    def test_invitees_with_zero_negative_or_insufficient_balance_can_accept(self):
        for role in ("human", "ai"):
            invited_role = "ai" if role == "human" else "human"
            for index, balance in enumerate((0, -100, 99)):
                with self.subTest(initiator_role=role, invitee_balance=balance):
                    owner, guest = f"owner-{role}-{index}", f"guest-{role}-{index}"
                    self.balance(role, owner, 100)
                    self.balance(invited_role, guest, balance)
                    room = framework.create_room("uno", "human_first", role, owner, guest, stake=100)
                    self.assertEqual(room["initiator_player_id"], owner)
                    self.assertEqual(framework.respond_to_invitation(
                        room["room_id"], invited_role, guest, "accept")["status"], "playing")
                    self.assertEqual(chips.get_wallet(invited_role, guest)["balance"], balance)

    def test_accept_rechecks_initiator_wallet_and_rolls_back(self):
        for role in ("human", "ai"):
            with self.subTest(role=role):
                invited_role = "ai" if role == "human" else "human"
                owner, guest = f"owner-{role}", f"guest-{role}"
                self.balance(invited_role, guest, -1)
                room = framework.create_room("uno", "human_first", role, owner, guest, stake=100)
                self.balance(role, owner, 99)
                with self.assertRaisesRegex(framework.DuelError, "可用筹码不足"):
                    framework.respond_to_invitation(room["room_id"], invited_role, guest, "accept")
                pending = framework.get_room(room["room_id"])
                self.assertEqual(pending["status"], "pending")
                self.assertEqual(pending["pending_for"], [guest])
                self.balance(role, owner, 100)
                self.assertEqual(framework.respond_to_invitation(
                    room["room_id"], invited_role, guest, "accept")["status"], "playing")

    def test_ordinary_join_rechecks_only_initiator(self):
        room = self.create(100, ai="invited-ai")
        self.balance("human", "human", -1)
        self.balance("ai", "ai", -1)
        self.balance("ai", "invited-ai", 0)
        with self.assertRaisesRegex(framework.DuelError, "可用筹码不足"):
            framework.join_room(room["room_id"], "ai", "ai")
        self.assertEqual(len(framework.get_room(room["room_id"])["participants"]), 2)
        self.balance("human", "human", 100)
        self.assertEqual(framework.join_room(room["room_id"], "ai", "ai")["status"], "pending")
        self.assertEqual(framework.respond_to_invitation(
            room["room_id"], "ai", "invited-ai", "accept")["status"], "playing")

    def test_private_invite_rechecks_owner_but_allows_poor_household_and_guests(self):
        for index, balance in enumerate((0, -100, 99)):
            owner, household, guest = f"owner-{index}", f"household-{index}", f"guest-{index}"
            self.balance("ai", household, balance)
            self.balance("ai", guest, balance)
            self.balance("human", owner, 0)
            with self.assertRaisesRegex(framework.DuelError, "可用筹码不足"):
                invites.create_invite("uno", "human", owner, target_player_count=3, stake=100)
            self.balance("human", owner, 100)
            room = invites.create_invite("uno", "human", owner, target_player_count=3,
                                         stake=100, ai_players=[household],
                                         trusted_bound_ais=[{"id": household, "name": "小机"}])
            self.balance("human", owner, 99)
            with self.assertRaisesRegex(framework.DuelError, "可用筹码不足"):
                invites.join_invite(room["invite_code"], "ai", guest)
            self.balance("human", owner, 100)
            invites.join_invite(room["invite_code"], "ai", guest)
            self.balance("human", owner, 99)
            with self.assertRaisesRegex(framework.DuelError, "可用筹码不足"):
                invites.start_invite(room["room_id"], "human", owner)
            self.assertEqual(framework.get_room(room["room_id"])["status"], "waiting")
            self.balance("human", owner, 100)
            self.assertEqual(invites.start_invite(room["room_id"], "human", owner)["status"], "playing")
        self.balance("ai", "free-owner", -1)
        free = invites.create_invite("uno", "ai", "free-owner", target_player_count=2)
        self.balance("human", "free-guest", -1)
        invites.join_invite(free["invite_code"], "human", "free-guest")
        self.assertEqual(invites.start_invite(free["room_id"], "ai", "free-owner")["status"], "playing")

    def test_commitments_gate_new_initiation_but_not_receiving_an_invitation(self):
        self.balance("human", "human", 100)
        room = self.create(60)
        with self.assertRaisesRegex(framework.DuelError, "可用筹码不足"):
            invites.create_invite("uno", "human", "human", target_player_count=2, stake=41)
        other = invites.create_invite("uno", "human", "other", target_player_count=2, stake=41)
        invites.join_invite(other["invite_code"], "human", "human")
        framework.respond_to_invitation(room["room_id"], "ai", "ai", "reject")
        # A received/accepted invitation still counts when this player initiates.
        with self.assertRaisesRegex(framework.DuelError, "可用筹码不足"):
            self.create(60)
        self.assertEqual(self.create(59)["status"], "pending")

    def test_npc_fill_rechecks_stake_face_value_and_rolls_back(self):
        self.balance("human", "owner", 20)
        room = invites.create_invite("uno", "human", "owner", target_player_count=4, stake=20)
        self.balance("human", "guest", -100)
        invites.join_invite(room["invite_code"], "human", "guest")
        self.balance("human", "owner", 19)
        personas = [NpcPersona(f"test-{i}", f"NPC {i}", "测试人设") for i in range(2)]
        with patch.object(invites, "require_npc"), patch.object(invites, "select_personas", return_value=personas):
            with self.assertRaisesRegex(framework.DuelError, "可用筹码不足"):
                invites.start_invite(room["room_id"], "human", "owner", fill_with_npcs=True)
            self.assertEqual(len(framework.get_room(room["room_id"])["participants"]), 2)
            self.balance("human", "owner", 20)
            self.assertEqual(invites.start_invite(
                room["room_id"], "human", "owner", fill_with_npcs=True)["status"], "playing")

    def test_concurrent_creations_cannot_reuse_balance(self):
        self.balance("human", "human", 100)
        barrier = Barrier(2)

        def create(index):
            barrier.wait(timeout=5)
            try:
                return self.create(60, ai=f"ai-{index}")["status"]
            except framework.DuelError as exc:
                self.assertIn("可用筹码不足", str(exc))
                return "rejected"

        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertCountEqual(pool.map(create, range(2)), ["pending", "rejected"])

    def test_inactive_seat_keeps_stake_face_value_until_terminal(self):
        members = [{"player_id": "human", "role": "human"}, {"player_id": "ai", "role": "ai"}]
        members += [{"player_id": f"npc:test-{i}", "role": "ai", "participant_kind": "system_npc",
                     "npc_persona_id": f"test-{i}"} for i in range(2)]
        self.balance("human", "human", 3)
        self.balance("ai", "ai", 6)
        room = framework.create_room("uno", "human_first", "human", "human", "ai",
                                     stake=3, ordered_participants=members)
        framework.respond_to_invitation(room["room_id"], "ai", "ai", "accept")
        room = framework.resign(room["room_id"], "human", "human")
        self.assertEqual(room["status"], "playing")
        with self.assertRaisesRegex(framework.DuelError, "可用筹码不足"):
            self.create(1, ai="new-ai")
        room = framework.resign(room["room_id"], "ai", "ai")
        self.assertEqual(room["status"], "finished")
        self.assertEqual(room["result"]["settlement_deltas"]["human"], -6)
        self.balance("human", "human", 6)
        self.assertEqual(self.create(6, ai="new-ai")["status"], "pending")

    def test_expired_pending_commitment_does_not_block_invite_creation(self):
        self.balance("human", "human", 100)
        room = self.create(100)
        with database.write_transaction() as conn:
            conn.execute("UPDATE rooms SET confirmation_expires_at = ? WHERE room_id = ?",
                         ((datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(), room["room_id"]))
        self.assertEqual(invites.create_invite(
            "uno", "human", "human", target_player_count=2, stake=100)["status"], "waiting")

    def test_concurrent_accepts_do_not_gate_invitee_balance(self):
        self.balance("ai", "ai", 100)
        rooms = [self.create(60, human=f"human-{i}") for i in range(2)]
        barrier = Barrier(2)

        def accept(room):
            barrier.wait(timeout=5)
            try:
                result = framework.respond_to_invitation(room["room_id"], "ai", "ai", "accept")
                return result["status"]
            except framework.DuelError as exc:
                self.assertIn("可用筹码不足", str(exc))
                return "rejected"

        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertCountEqual(pool.map(accept, rooms), ["playing", "playing"])
        with self.assertRaisesRegex(framework.DuelError, "可用筹码不足"):
            framework.create_room("uno", "human_first", "ai", "ai", "new-human", stake=1)

    def test_game_admission_uses_stake_face_value_not_settlement_multiplier(self):
        for game, count in [
            ("uno", 2), ("texas_holdem", 2),
            ("blackjack", 2), ("doudizhu", 3),
            ("zhajinhua", 2), ("mahjong", 4),
            ("gandengyan", 2), ("guandan", 4),
            ("chinese_checkers", 6),
        ]:
            with self.subTest(game=game):
                members = [{"role": "human" if i == 0 else "ai", "player_id": f"{game}-{i}"}
                           for i in range(count)]
                for member in members:
                    self.balance(member["role"], member["player_id"], -1)
                self.balance("human", members[0]["player_id"], 1000)

                def create(stake):
                    return framework.create_room(game, "human_first", "human", members[0]["player_id"],
                                                 stake=stake, ordered_participants=members)

                with self.assertRaisesRegex(framework.DuelError, "可用筹码不足"):
                    create(1001)
                room = create(10)
                for member in members[1:]:
                    room = framework.respond_to_invitation(room["room_id"], "ai", member["player_id"], "accept")
                self.assertEqual(room["status"], "playing")
                # The first room occupies only 10, even for multiplied payouts.
                remaining = framework.create_room("uno", "human_first", "human", members[0]["player_id"],
                                                  f"extra-{game}", stake=990)
                self.assertEqual(remaining["status"], "pending")
                with self.assertRaisesRegex(framework.DuelError, "可用筹码不足"):
                    framework.create_room("uno", "human_first", "human", members[0]["player_id"],
                                          f"overflow-{game}", stake=1)


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
        self.assertEqual(summary["allowed_actions"], ["accept", "reject"])
        self.assertIn("用 accept(room_id) 接受", listed.json()["notices"][0]["summary"])

        listed_again = await self.client.post(
            "/mcp/play", json={"action": "rooms", "player_id": "ai-web"}
        )
        self.assertEqual(listed_again.status_code, 200, listed_again.text)
        self.assertNotIn("notices", listed_again.json())
        self.assertEqual(listed_again.json()["rooms"][0]["allowed_actions"], ["accept", "reject"])

        accepted = await self.client.post(
            "/mcp/play",
            json={"action": "accept", "player_id": "ai-web", "room_id": room_id},
        )
        self.assertEqual(accepted.status_code, 200, accepted.text)
        self.assertEqual(accepted.json()["room"]["status"], "playing")

        playing = await self.client.post(
            "/mcp/play", json={"action": "rooms", "player_id": "ai-web"}
        )
        self.assertEqual(playing.status_code, 200, playing.text)
        self.assertEqual(playing.json()["rooms"][0]["status"], "playing")
        self.assertNotIn("allowed_actions", playing.json()["rooms"][0])

    async def test_pending_actions_follow_each_ai_confirmation(self):
        room = framework.create_room(
            "dots_boxes", "human_first", "human", "human-owner", stake=3,
            ordered_participants=[
                {"player_id": "human-owner", "role": "human"},
                {"player_id": "ai-first", "role": "ai"},
                {"player_id": "ai-second", "role": "ai"},
            ],
        )
        room_id = room["room_id"]
        pending = main_module._pending_ai_response(room, "ai-first", "")
        self.assertEqual(pending["confirmation_decision"], "pending")
        self.assertEqual(pending["allowed_actions"], ["accept", "reject"])

        async def mcp(action, player_id):
            response = await self.client.post(
                "/mcp/play",
                json={"action": action, "player_id": player_id, "room_id": room_id},
            )
            self.assertEqual(response.status_code, 200, response.text)
            return response.json()

        before = await mcp("state", "ai-first")
        self.assertEqual(before["status"], "pending")
        self.assertEqual(before["room_id"], room_id)
        self.assertEqual(before["allowed_actions"], ["accept", "reject"])

        accepted = await mcp("accept", "ai-first")
        self.assertEqual(accepted["status"], "pending")
        self.assertEqual(accepted["confirmation_decision"], "accepted")
        self.assertNotIn("allowed_actions", accepted)
        after = await mcp("state", "ai-first")
        self.assertEqual(after["status"], "pending")
        self.assertNotIn("allowed_actions", after)
        other = await mcp("state", "ai-second")
        self.assertEqual(other["allowed_actions"], ["accept", "reject"])

        started = await mcp("accept", "ai-second")
        self.assertEqual(started["status"], "playing")
        self.assertNotIn("allowed_actions", started)
        self.assertNotIn("allowed_actions", await mcp("state", "ai-first"))

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
        listed = await self.client.post(
            "/mcp/play", json={"action": "rooms", "player_id": "ai-web"}
        )
        self.assertEqual(listed.status_code, 200, listed.text)
        summary = listed.json()["rooms"][0]
        self.assertEqual(summary["status"], "pending")
        self.assertEqual(summary["confirmation_decision"], "accepted")
        self.assertNotIn("allowed_actions", summary)
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

    async def test_web_and_mcp_reject_trillion_stakes_without_creating_rooms(self):
        for path, body in [
            ("/api/rooms", {"player_id": "human-web", "ai_player": "ai-web", "game_type": "uno"}),
            ("/mcp/play", {"action": "new", "player_id": "ai-web", "opponent_id": "human-web", "game_type": "uno"}),
            ("/api/invites", {"game_type": "uno", "target_player_count": 2}),
            ("/mcp/play", {"action": "invite", "player_id": "ai-web", "game_type": "uno", "target_player_count": 2}),
        ]:
            with self.subTest(path=path, body=body):
                response = await self.client.post(path, headers=self.headers(), json={**body, "stake": 10**12})
                self.assertEqual(response.status_code, 409, response.text)
                self.assertIn("可用筹码不足", response.json()["message"])
        with database.write_transaction() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM rooms").fetchone()[0], 0)

    def set_wallet_balance(self, role, balance):
        with database.write_transaction() as conn:
            wallet = chips._ensure_wallet(conn, role, f"{role}-web")
            conn.execute("UPDATE chip_wallets SET balance = ? WHERE id = ?", (balance, wallet["id"]))

    async def accept_room(self, room_id, initiator_role):
        if initiator_role == "human":
            return await self.client.post("/mcp/play", json={
                "action": "accept", "player_id": "ai-web", "room_id": room_id,
            })
        return await self.client.post(f"/api/rooms/{room_id}/invitation", headers=self.headers(),
                                      json={"decision": "accept"})

    async def test_http_normal_rooms_gate_only_initiator_at_create_and_accept(self):
        for role in ("human", "ai"):
            guest_role = "ai" if role == "human" else "human"
            path = "/api/rooms" if role == "human" else "/mcp/play"
            body = ({"player_id": "human-web", "ai_player": "ai-web"} if role == "human" else
                    {"action": "new", "player_id": "ai-web", "opponent_id": "human-web"})
            for balance in (0, -100, 99):
                with self.subTest(initiator_role=role, balance=balance):
                    self.set_wallet_balance(role, balance)
                    self.set_wallet_balance(guest_role, 2000)
                    response = await self.client.post(path, headers=self.headers(),
                                                      json={**body, "game_type": "uno", "stake": 100})
                    self.assertEqual(response.status_code, 409, response.text)
                    self.set_wallet_balance(role, 2000)
                    self.set_wallet_balance(guest_role, balance)
                    response = await self.client.post(path, headers=self.headers(),
                                                      json={**body, "game_type": "uno", "stake": 100})
                    self.assertEqual(response.status_code, 200, response.text)
                    payload = response.json()
                    room_id = payload["room"]["room_id"] if role == "human" else payload["room_id"]
                    self.set_wallet_balance(role, 99)
                    rejected = await self.accept_room(room_id, role)
                    self.assertEqual(rejected.status_code, 409, rejected.text)
                    self.set_wallet_balance(role, 2000)
                    accepted = await self.accept_room(room_id, role)
                    self.assertEqual(accepted.status_code, 200, accepted.text)
                    self.assertEqual(framework.get_room(room_id)["status"], "playing")

    async def test_http_invite_rooms_allow_poor_guests_to_join_and_start(self):
        for role in ("human", "ai"):
            guest_role = "ai" if role == "human" else "human"
            for balance in (0, -100, 99):
                with self.subTest(initiator_role=role, invitee_balance=balance):
                    self.set_wallet_balance(role, 2000)
                    self.set_wallet_balance(guest_role, balance)
                    path = "/api/invites" if role == "human" else "/mcp/play"
                    body = {"game_type": "uno", "stake": 100, "target_player_count": 2}
                    if role == "ai":
                        body.update(action="invite", player_id="ai-web")
                    response = await self.client.post(path, headers=self.headers(), json=body)
                    self.assertEqual(response.status_code, 200, response.text)
                    room = response.json()["room"]
                    join_path = "/mcp/play" if role == "human" else "/api/invites/join"
                    join_body = {"invite_code": room["invite_code"]}
                    if role == "human":
                        join_body.update(action="join", player_id="ai-web")
                    joined = await self.client.post(join_path, headers=self.headers(), json=join_body)
                    self.assertEqual(joined.status_code, 200, joined.text)
                    start_path = f"/api/rooms/{room['room_id']}/start" if role == "human" else "/mcp/play"
                    start_body = {} if role == "human" else {
                        "action": "start", "player_id": "ai-web", "room_id": room["room_id"],
                    }
                    self.set_wallet_balance(role, 99)
                    rejected = await self.client.post(start_path, headers=self.headers(), json=start_body)
                    self.assertEqual(rejected.status_code, 409, rejected.text)
                    self.set_wallet_balance(role, 2000)
                    started = await self.client.post(start_path, headers=self.headers(), json=start_body)
                    self.assertEqual(started.status_code, 200, started.text)
                    self.assertEqual(framework.get_room(room["room_id"])["status"], "playing")

    async def test_web_and_mcp_rematch_gate_new_initiator_but_allow_poor_invitee(self):
        first = framework.create_room("tictactoe", "human_first", "human", "human-web", "ai-web", stake=100)
        framework.respond_to_invitation(first["room_id"], "ai", "ai-web", "accept")
        framework.resign(first["room_id"], "human", "human-web")
        for role in ("ai", "human"):
            guest_role = "human" if role == "ai" else "ai"
            path, body = (
                ("/mcp/play", {"action": "rematch", "player_id": "ai-web", "room_id": first["room_id"]})
                if role == "ai" else
                ("/api/rooms", {"player_id": "human-web", "ai_player": "ai-web", "game_type": "tictactoe",
                                 "stake": 100, "rematch_of_room_id": first["room_id"]})
            )
            self.set_wallet_balance(role, 99)
            self.set_wallet_balance(guest_role, 2000)
            rejected = await self.client.post(path, headers=self.headers(), json=body)
            self.assertEqual(rejected.status_code, 409, rejected.text)
            self.set_wallet_balance(role, 2000)
            self.set_wallet_balance(guest_role, -100)
            created = await self.client.post(path, headers=self.headers(), json=body)
            self.assertEqual(created.status_code, 200, created.text)
            payload = created.json()
            room_id = payload["room"]["room_id"] if role == "human" else payload["room_id"]
            self.assertEqual(framework.get_room(room_id)["initiator_player_id"], f"{role}-web")
            accepted = await self.accept_room(room_id, role)
            self.assertEqual(accepted.status_code, 200, accepted.text)
            self.assertEqual(framework.get_room(room_id)["status"], "playing")
            first = framework.resign(room_id, role, f"{role}-web")

    async def test_gandengyan_1000_balance_allows_10_rejects_1001_on_web_and_mcp(self):
        for role in ("human", "ai"):
            for private_invite in (False, True):
                with self.subTest(initiator_role=role, private_invite=private_invite):
                    self.set_wallet_balance(role, 1000)
                    self.set_wallet_balance("ai" if role == "human" else "human", -100)
                    path = ("/api/invites" if private_invite else "/api/rooms") if role == "human" else "/mcp/play"
                    body = {"game_type": "gandengyan", "player_id": f"{role}-web"}
                    if private_invite:
                        body["target_player_count"] = 2
                    elif role == "human":
                        body["ai_player"] = "ai-web"
                    else:
                        body["opponent_id"] = "human-web"
                    if role == "ai":
                        body["action"] = "invite" if private_invite else "new"
                    rejected = await self.client.post(path, headers=self.headers(), json={**body, "stake": 1001})
                    self.assertEqual(rejected.status_code, 409, rejected.text)
                    created = await self.client.post(path, headers=self.headers(), json={**body, "stake": 10})
                    self.assertEqual(created.status_code, 200, created.text)
                    payload = created.json()
                    room_id = payload["room"]["room_id"] if "room" in payload else payload["room_id"]
                    if private_invite:
                        join_body = {"invite_code": payload["room"]["invite_code"]}
                        if role == "human":
                            join_body.update(action="join", player_id="ai-web")
                        joined = await self.client.post("/mcp/play" if role == "human" else "/api/invites/join",
                                                        headers=self.headers(), json=join_body)
                        self.assertEqual(joined.status_code, 200, joined.text)
                        started = await self.client.post(
                            f"/api/rooms/{room_id}/start" if role == "human" else "/mcp/play",
                            headers=self.headers(), json={} if role == "human" else {
                                "action": "start", "player_id": "ai-web", "room_id": room_id,
                            })
                    else:
                        started = await self.accept_room(room_id, role)
                    self.assertEqual(started.status_code, 200, started.text)
                    self.assertEqual(framework.get_room(room_id)["status"], "playing")


if __name__ == "__main__":
    unittest.main()

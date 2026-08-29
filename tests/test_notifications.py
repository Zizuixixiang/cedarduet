import base64
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import httpx

from app import achievements, database, exchanges, framework, loans, notifications
from app import main as main_module


class NotificationStoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="duel-notifications-")
        self.db_patch = patch.object(
            database, "DB_PATH", Path(self.temporary.name) / "test.db"
        )
        self.db_patch.start()
        database.init_db()

    def tearDown(self):
        self.db_patch.stop()
        self.temporary.cleanup()

    def rows(self, subject_type=None, subject_id=None, category=None, reference_id=None):
        clauses = []
        params = []
        for column, value in (
            ("subject_type", subject_type),
            ("subject_id", subject_id),
            ("category", category),
            ("reference_id", reference_id),
        ):
            if value is not None:
                clauses.append(f"{column} = ?")
                params.append(value)
        conn = database.connect()
        try:
            return [
                dict(row)
                for row in conn.execute(
                    "SELECT * FROM notifications"
                    + (f" WHERE {' AND '.join(clauses)}" if clauses else "")
                    + " ORDER BY id",
                    params,
                )
            ]
        finally:
            conn.close()

    def test_additive_migration_idempotency_and_subject_isolation(self):
        database.init_db()
        database.init_db()
        conn = database.connect()
        try:
            columns = {
                row["name"] for row in conn.execute("PRAGMA table_info(notifications)")
            }
            state_columns = {
                row["name"]
                for row in conn.execute(
                    "PRAGMA table_info(notification_subject_states)"
                )
            }
        finally:
            conn.close()
        self.assertEqual(
            columns,
            {
                "id", "subject_type", "subject_id", "category", "event_type",
                "reference_id", "event_key", "summary", "created_at", "read_at",
            },
        )
        self.assertEqual(
            state_columns, {"subject_type", "subject_id", "revision"}
        )

        with database.write_transaction() as conn:
            for _ in range(2):
                notifications.create_notification(
                    conn, "human", "same-id", "loan", "created", "loan-1",
                    "借款提案待回应", event_key="loan:proposal:loan-1:revision:1",
                )
            notifications.create_notification(
                conn, "ai", "same-id", "loan", "created", "loan-1",
                "借款提案待回应", event_key="loan:proposal:loan-1:revision:1",
            )
            notifications.create_notification(
                conn, "human", "other-human", "loan", "created", "loan-1",
                "借款提案待回应", event_key="loan:proposal:loan-1:revision:1",
            )

        first = notifications.unread_summary("human", "same-id")
        self.assertEqual(first["total"], 1)
        self.assertEqual(first["categories"], {
            "game": 0, "loan": 1, "exchange": 0, "achievement": 0,
        })
        self.assertEqual(notifications.unread_summary("human", "same-id"), first)
        self.assertEqual(
            notifications.unread_state("human", "same-id")["unread_revision"], 1
        )
        self.assertEqual(len(self.rows()), 3)

        notifications.ack_notifications("human", "same-id", "loan")
        self.assertEqual(notifications.unread_summary("human", "same-id")["total"], 0)
        self.assertEqual(
            notifications.unread_state("human", "same-id")["unread_revision"], 2
        )
        with database.write_transaction() as conn:
            inserted = notifications.create_notification(
                conn, "human", "same-id", "loan", "created", "loan-1",
                "借款提案待回应", event_key="loan:proposal:loan-1:revision:1",
            )
        self.assertFalse(inserted)
        self.assertEqual(notifications.unread_summary("human", "same-id")["total"], 0)
        self.assertEqual(
            notifications.unread_state("human", "same-id")["unread_revision"], 2
        )
        self.assertEqual(notifications.unread_summary("ai", "same-id")["total"], 1)
        self.assertEqual(
            notifications.unread_summary("human", "other-human")["total"], 1
        )

    def test_game_create_accept_reject_and_deleted_room_notification(self):
        zero = framework.create_room(
            "tictactoe", "human_first", "human", "human-zero", "ai-zero"
        )
        self.assertEqual(
            notifications.unread_summary("ai", "ai-zero")["categories"]["game"], 1
        )
        self.assertEqual(
            notifications.unread_summary("human", "human-zero")["categories"]["game"], 0
        )
        self.assertIn("对方新建", self.rows("ai", "ai-zero", "game")[0]["summary"])

        accepted = framework.create_room(
            "gomoku", "human_first", "human", "human-accept", "ai-accept", stake=6
        )
        invite = self.rows("ai", "ai-accept", "game", accepted["room_id"])[0]
        self.assertIn("需确认 6 筹码", invite["summary"])
        framework.respond_to_invitation(
            accepted["room_id"], "ai", "ai-accept", "accept"
        )
        invite = self.rows("ai", "ai-accept", "game", accepted["room_id"])[0]
        self.assertIsNotNone(invite["read_at"])
        result = self.rows("human", "human-accept", "game", accepted["room_id"])
        self.assertEqual([row["event_type"] for row in result], ["invitation_accepted"])

        rejected = framework.create_room(
            "othello", "ai_first", "ai", "ai-reject", "human-reject", stake=9
        )
        framework.respond_to_invitation(
            rejected["room_id"], "human", "human-reject", "reject"
        )
        with self.assertRaises(framework.DuelError):
            framework.get_room(rejected["room_id"])
        receiver_rows = self.rows(
            "human", "human-reject", "game", rejected["room_id"]
        )
        self.assertEqual(len(receiver_rows), 1)
        self.assertIsNotNone(receiver_rows[0]["read_at"])
        sender_rows = self.rows("ai", "ai-reject", "game", rejected["room_id"])
        self.assertEqual(sender_rows[0]["event_type"], "invitation_rejected")
        self.assertIsNone(sender_rows[0]["read_at"])

        # A retry cannot duplicate either a surviving notification or a deleted room.
        self.assertEqual(len(self.rows(reference_id=rejected["room_id"])), 2)
        self.assertEqual(len(self.rows(reference_id=zero["room_id"])), 1)

    def test_invitation_expiry_replaces_receiver_prompt_and_notifies_sender(self):
        pending = framework.create_room(
            "connect4", "human_first", "human", "human-expire", "ai-expire", stake=12
        )
        expired = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(
            timespec="seconds"
        )
        with database.write_transaction() as conn:
            conn.execute(
                "UPDATE rooms SET confirmation_expires_at = ? WHERE room_id = ?",
                (expired, pending["room_id"]),
            )
        self.assertEqual(framework.list_ai_rooms("ai-expire"), [])
        receiver = self.rows("ai", "ai-expire", "game", pending["room_id"])
        self.assertEqual(len(receiver), 1)
        self.assertIsNotNone(receiver[0]["read_at"])
        sender = self.rows("human", "human-expire", "game", pending["room_id"])
        self.assertEqual([row["event_type"] for row in sender], ["invitation_expired"])
        self.assertIsNone(sender[0]["read_at"])

    def test_leave_resign_finish_notify_other_real_participants_not_npc(self):
        resigned = framework.create_room(
            "tictactoe", "human_first", "human", "human-resign", "ai-resign"
        )
        notifications.ack_notifications("ai", "ai-resign", "game")
        framework.resign(resigned["room_id"], "human", "human-resign")
        rows = self.rows("ai", "ai-resign", "game", resigned["room_id"])
        self.assertEqual(rows[-1]["event_type"], "resigned")

        left = framework.create_room(
            "dots_boxes",
            "human_first",
            "human",
            "human-left",
            "ai-left",
            ordered_participants=[
                {"player_id": "human-left", "role": "human"},
                {"player_id": "ai-left", "role": "ai"},
                {
                    "player_id": "npc:tang_yi",
                    "display_name": "NPC",
                    "role": "ai",
                    "participant_kind": "system_npc",
                    "npc_persona_id": "tang_yi",
                },
            ],
        )
        notifications.ack_notifications("ai", "ai-left", "game")
        framework.leave_room(left["room_id"], "human", "human-left")
        self.assertEqual(
            self.rows("ai", "ai-left", "game", left["room_id"])[-1]["event_type"],
            "left",
        )
        self.assertEqual(self.rows(subject_id="npc:tang_yi"), [])

        finished = framework.create_room(
            "tictactoe", "human_first", "human", "human-finish", "ai-finish"
        )
        notifications.ack_notifications("ai", "ai-finish", "game")
        for role, player_id, move in (
            ("human", "human-finish", {"row": 0, "col": 0}),
            ("ai", "ai-finish", {"row": 1, "col": 0}),
            ("human", "human-finish", {"row": 0, "col": 1}),
            ("ai", "ai-finish", {"row": 1, "col": 1}),
            ("human", "human-finish", {"row": 0, "col": 2}),
        ):
            terminal = framework.play_move(finished["room_id"], role, player_id, move)
        self.assertEqual(terminal["status"], "finished")
        self.assertEqual(
            self.rows("ai", "ai-finish", "game", finished["room_id"])[-1]["event_type"],
            "finished",
        )

    def test_achievement_unlock_notification_is_idempotent(self):
        with database.write_transaction() as conn:
            first = achievements._unlock(
                conn,
                "human",
                "human-achievement",
                "loan_first_overdue",
                source_type="test",
                source_id="source-1",
            )
            second = achievements._unlock(
                conn,
                "human",
                "human-achievement",
                "loan_first_overdue",
                source_type="test",
                source_id="source-1",
            )
        self.assertIsNotNone(first)
        self.assertIsNone(second)
        rows = self.rows("human", "human-achievement", "achievement")
        self.assertEqual(len(rows), 1)
        self.assertIn("成就", rows[0]["summary"])

    def test_unlocks_in_current_human_response_are_read_but_opponents_remain(self):
        room = framework.create_room(
            "tictactoe", "human_first", "human", "human-unlock", "ai-unlock"
        )
        for role, player_id, move in (
            ("human", "human-unlock", {"row": 0, "col": 0}),
            ("ai", "ai-unlock", {"row": 1, "col": 0}),
            ("human", "human-unlock", {"row": 0, "col": 1}),
            ("ai", "ai-unlock", {"row": 1, "col": 1}),
            ("human", "human-unlock", {"row": 0, "col": 2}),
        ):
            room = framework.play_move(room["room_id"], role, player_id, move)
        self.assertGreater(
            notifications.unread_summary("human", "human-unlock")["categories"]["achievement"],
            0,
        )
        payload = main_module.human_response(room, "已完成", "human-unlock")
        self.assertTrue(payload["unlocks"])
        self.assertEqual(
            notifications.unread_summary("human", "human-unlock")["categories"]["achievement"],
            0,
        )
        self.assertGreater(
            notifications.unread_summary("ai", "ai-unlock")["categories"]["achievement"],
            0,
        )


class LoanNotificationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="duel-loan-notifications-")
        self.db_patch = patch.object(
            database, "DB_PATH", Path(self.temporary.name) / "test.db"
        )
        self.db_patch.start()
        database.init_db()
        self.clock = datetime(2026, 8, 1, 4, 0, tzinfo=timezone.utc)
        self.time_patch = patch.object(loans, "_utc_now", side_effect=lambda: self.clock)
        self.time_patch.start()

    def tearDown(self):
        self.time_patch.stop()
        self.db_patch.stop()
        self.temporary.cleanup()

    def due(self, days=7):
        return (
            self.clock.astimezone(loans.SHANGHAI).date() + timedelta(days=days)
        ).isoformat()

    def create(self, key="loan:create:0001", *, days=7):
        return loans.create_loan(
            "human", "loan-human", "loan-ai", principal=20,
            daily_rate_micro_percent=0, due_date=self.due(days),
            idempotency_key=key, pair_is_bound=True,
        )

    def test_proposal_counter_withdraw_and_results(self):
        loan = self.create()
        replay = self.create()
        self.assertEqual(replay["loan_id"], loan["loan_id"])
        self.assertEqual(
            notifications.unread_summary("ai", "loan-ai")["categories"]["loan"], 1
        )
        countered = loans.counter_loan(
            loan["loan_id"], "ai", "loan-ai", revision=1, principal=21,
            daily_rate_micro_percent=0, due_date=self.due(),
            interest_cap_enabled=True, idempotency_key="loan:counter:0001",
            bound_counterparty_id="loan-human",
        )
        self.assertEqual(notifications.unread_summary("ai", "loan-ai")["categories"]["loan"], 0)
        self.assertEqual(notifications.unread_summary("human", "loan-human")["categories"]["loan"], 1)
        loans.close_proposal(
            loan["loan_id"], "human", "loan-human", action="withdraw",
            revision=countered["revision"], idempotency_key="loan:withdraw:0001",
        )
        self.assertEqual(notifications.unread_summary("human", "loan-human")["categories"]["loan"], 0)
        self.assertEqual(notifications.unread_summary("ai", "loan-ai")["categories"]["loan"], 0)

        accepted = self.create("loan:create:accept")
        loans.accept_loan(
            accepted["loan_id"], "ai", "loan-ai", revision=1,
            idempotency_key="loan:accept:0001", bound_counterparty_id="loan-human",
        )
        human_notices = notifications.consume_notifications(
            "human", "loan-human", "loan", reference_id=accepted["loan_id"]
        )
        self.assertEqual([item["event"] for item in human_notices], ["accepted"])

        rejected = self.create("loan:create:reject")
        loans.close_proposal(
            rejected["loan_id"], "ai", "loan-ai", action="reject", revision=1,
            idempotency_key="loan:reject:0001",
        )
        human_notices = notifications.consume_notifications(
            "human", "loan-human", "loan", reference_id=rejected["loan_id"]
        )
        self.assertEqual([item["event"] for item in human_notices], ["rejected"])

    def test_expiry_overdue_and_repayment_summaries(self):
        expiring = self.create("loan:create:expire")
        self.clock += timedelta(days=3)
        loans.get_loan(expiring["loan_id"], "human", "loan-human")
        self.assertEqual(notifications.unread_summary("ai", "loan-ai")["categories"]["loan"], 0)
        expired = notifications.consume_notifications(
            "human", "loan-human", "loan", reference_id=expiring["loan_id"]
        )
        self.assertEqual([item["event"] for item in expired], ["expired"])

        active = self.create("loan:create:active", days=1)
        active = loans.accept_loan(
            active["loan_id"], "ai", "loan-ai", revision=1,
            idempotency_key="loan:accept:active", bound_counterparty_id="loan-human",
        )
        notifications.ack_notifications("human", "loan-human", "loan")
        notifications.ack_notifications("ai", "loan-ai", "loan")
        loans.repay_loan(
            active["loan_id"], "human", "loan-human", amount=5,
            idempotency_key="loan:repay:part",
        )
        loans.repay_loan(
            active["loan_id"], "human", "loan-human", amount=15,
            idempotency_key="loan:repay:full",
        )
        repayments = notifications.consume_notifications(
            "ai", "loan-ai", "loan", reference_id=active["loan_id"]
        )
        self.assertEqual([item["event"] for item in repayments], ["repayment", "repaid"])
        self.assertIn("部分还款", repayments[0]["summary"])
        self.assertIn("已还清", repayments[1]["summary"])

        overdue = self.create("loan:create:overdue", days=1)
        loans.accept_loan(
            overdue["loan_id"], "ai", "loan-ai", revision=1,
            idempotency_key="loan:accept:overdue", bound_counterparty_id="loan-human",
        )
        notifications.ack_notifications("human", "loan-human", "loan")
        notifications.ack_notifications("ai", "loan-ai", "loan")
        self.clock += timedelta(days=2)
        loans.get_loan(overdue["loan_id"], "human", "loan-human")
        self.assertEqual(notifications.unread_summary("human", "loan-human")["categories"]["loan"], 1)
        self.assertEqual(notifications.unread_summary("ai", "loan-ai")["categories"]["loan"], 1)
        loans.get_loan(overdue["loan_id"], "ai", "loan-ai")
        self.assertEqual(notifications.unread_summary("human", "loan-human")["categories"]["loan"], 1)
        self.assertEqual(notifications.unread_summary("ai", "loan-ai")["categories"]["loan"], 1)


class ExchangeNotificationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="duel-exchange-notifications-")
        self.db_patch = patch.object(
            database, "DB_PATH", Path(self.temporary.name) / "test.db"
        )
        self.db_patch.start()
        database.init_db()
        self.clock = datetime(2026, 8, 1, 4, 0, tzinfo=timezone.utc)
        self.time_patch = patch.object(
            exchanges, "_utc_now", side_effect=lambda: self.clock
        )
        self.time_patch.start()

    def tearDown(self):
        self.time_patch.stop()
        self.db_patch.stop()
        self.temporary.cleanup()

    def create(self, actor_type="human", key="exchange:create:0001"):
        actor_id, counterparty_id = (
            ("exchange-human", "exchange-ai")
            if actor_type == "human" else ("exchange-ai", "exchange-human")
        )
        return exchanges.create_exchange_request(
            actor_type, actor_id, counterparty_id, item_key="hug",
            request_note="测试约定", chip_amount=5, custom_title=None,
            idempotency_key=key, pair_is_bound=True,
        )

    def test_create_withdraw_confirm_reject_and_expiry(self):
        created = self.create()
        self.assertEqual(self.create()["request_id"], created["request_id"])
        self.assertEqual(notifications.unread_summary("ai", "exchange-ai")["categories"]["exchange"], 1)
        exchanges.close_exchange_request(
            created["request_id"], "human", "exchange-human", action="withdraw",
            idempotency_key="exchange:withdraw:1", bound_counterparty_id="exchange-ai",
        )
        self.assertEqual(notifications.unread_summary("ai", "exchange-ai")["categories"]["exchange"], 0)

        confirmed = self.create("ai", "exchange:create:confirm")
        exchanges.confirm_exchange_request(
            confirmed["request_id"], "human", "exchange-human",
            idempotency_key="exchange:confirm:1", bound_counterparty_id="exchange-ai",
        )
        result = notifications.consume_notifications(
            "ai", "exchange-ai", "exchange", reference_id=confirmed["request_id"]
        )
        self.assertEqual([item["event"] for item in result], ["confirmed"])

        rejected = self.create("ai", "exchange:create:reject")
        exchanges.close_exchange_request(
            rejected["request_id"], "human", "exchange-human", action="reject",
            idempotency_key="exchange:reject:1", bound_counterparty_id="exchange-ai",
        )
        result = notifications.consume_notifications(
            "ai", "exchange-ai", "exchange", reference_id=rejected["request_id"]
        )
        self.assertEqual([item["event"] for item in result], ["rejected"])

        expiring = self.create("human", "exchange:create:expire")
        self.clock += timedelta(hours=72)
        exchanges.list_exchange_requests(
            "ai", "exchange-ai", bound_counterparty_ids={"exchange-human"}
        )
        self.assertEqual(notifications.unread_summary("ai", "exchange-ai")["categories"]["exchange"], 0)
        result = notifications.consume_notifications(
            "human", "exchange-human", "exchange", reference_id=expiring["request_id"]
        )
        self.assertEqual([item["event"] for item in result], ["expired"])


class NotificationApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="duel-notification-api-")
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
    def headers(human_id="api-human", ai_id="api-ai"):
        encoded = base64.urlsafe_b64encode(
            json.dumps([{"id": ai_id, "name": "小机"}]).encode("utf-8")
        ).decode("ascii").rstrip("=")
        return {
            "X-Duel-Human-Player": human_id,
            "X-Duel-Human-Name": "%E4%BA%BA%E7%B1%BB",
            "X-Duel-Bound-Ais": encoded,
        }

    def seed(self, subject_type, subject_id, category, reference_id, suffix):
        with database.write_transaction() as conn:
            notifications.create_notification(
                conn, subject_type, subject_id, category, "test", reference_id,
                f"{category} test", event_key=f"test:{category}:{suffix}",
            )

    async def test_human_gets_do_not_clear_and_ack_uses_only_trusted_identity(self):
        self.seed("human", "api-human", "loan", "loan-api", "human")
        self.seed("human", "other-human", "loan", "loan-api", "other")
        whoami = await self.client.get("/api/whoami", headers=self.headers())
        self.assertEqual(whoami.status_code, 200, whoami.text)
        self.assertEqual(whoami.json()["unread"]["categories"]["loan"], 1)
        chips = await self.client.get("/api/chips", headers=self.headers())
        self.assertEqual(chips.json()["unread"]["categories"]["loan"], 1)
        self.assertEqual(notifications.unread_summary("human", "api-human")["total"], 1)

        untrusted = await self.client.post(
            "/api/notifications/read", json={"category": "loan"}
        )
        self.assertEqual(untrusted.status_code, 403)
        self_reported = await self.client.post(
            "/api/notifications/read", headers=self.headers(),
            json={"category": "loan", "player_id": "other-human"},
        )
        self.assertEqual(self_reported.status_code, 422)
        other_ack = await self.client.post(
            "/api/notifications/read", headers=self.headers("other-human", "other-ai"),
            json={"category": "loan"},
        )
        self.assertEqual(other_ack.status_code, 200)
        self.assertEqual(notifications.unread_summary("human", "api-human")["total"], 1)

        acked = await self.client.post(
            "/api/notifications/read", headers=self.headers(),
            json={"category": "loan", "reference_id": "loan-api"},
        )
        self.assertEqual(acked.status_code, 200, acked.text)
        self.assertEqual(acked.json()["read"], 1)
        self.assertEqual(acked.json()["unread"]["total"], 0)

    async def test_human_ack_is_versioned_type_isolated_and_does_not_revive(self):
        for category in ("game", "loan", "exchange", "achievement"):
            self.seed(
                "human", "api-human", category, f"{category}-api", category
            )
        self.seed("ai", "api-human", "exchange", "exchange-ai", "ai")

        before = await self.client.get(
            "/api/notifications/unread", headers=self.headers()
        )
        self.assertEqual(before.status_code, 200, before.text)
        self.assertEqual(before.json()["unread_revision"], 4)
        self.assertEqual(
            before.json()["unread"]["categories"],
            {"game": 1, "loan": 1, "exchange": 1, "achievement": 1},
        )

        acked = await self.client.post(
            "/api/notifications/read",
            headers=self.headers(),
            json={"category": "exchange"},
        )
        self.assertEqual(acked.status_code, 200, acked.text)
        self.assertEqual(acked.json()["read"], 1)
        self.assertEqual(acked.json()["unread_revision"], 5)
        self.assertEqual(
            acked.json()["unread"]["categories"],
            {"game": 1, "loan": 1, "exchange": 0, "achievement": 1},
        )
        self.assertEqual(
            notifications.unread_summary("ai", "api-human")["categories"]["exchange"],
            1,
        )

        repeated = await self.client.post(
            "/api/notifications/read",
            headers=self.headers(),
            json={"category": "exchange"},
        )
        refreshed = await self.client.get(
            "/api/notifications/unread", headers=self.headers()
        )
        chips = await self.client.get("/api/chips", headers=self.headers())
        for response in (repeated, refreshed, chips):
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["unread_revision"], 5)
            self.assertEqual(
                response.json()["unread"]["categories"]["exchange"], 0
            )

    async def test_human_exchange_visit_clears_unread_but_not_pending_work(self):
        created = exchanges.create_exchange_request(
            "ai",
            "api-ai",
            "api-human",
            item_key="hug",
            request_note="测试互动商店未读",
            chip_amount=5,
            custom_title=None,
            idempotency_key="api-exchange-unread-create",
            pair_is_bound=True,
        )
        before = await self.client.get("/api/chips", headers=self.headers())
        self.assertEqual(before.status_code, 200, before.text)
        self.assertEqual(before.json()["unread"]["categories"]["exchange"], 1)
        self.assertEqual(before.json()["exchange"]["pending_count"], 1)
        self.assertEqual(
            before.json()["exchange"]["pending_for_me"][0]["request_id"],
            created["request_id"],
        )

        acked = await self.client.post(
            "/api/notifications/read",
            headers=self.headers(),
            json={"category": "exchange"},
        )
        refreshed = await self.client.get("/api/chips", headers=self.headers())
        self.assertEqual(acked.status_code, 200, acked.text)
        self.assertEqual(acked.json()["read"], 1)
        self.assertEqual(refreshed.json()["unread"]["categories"]["exchange"], 0)
        self.assertEqual(refreshed.json()["exchange"]["pending_count"], 1)
        self.assertEqual(
            refreshed.json()["unread_revision"], acked.json()["unread_revision"]
        )

        second = exchanges.create_exchange_request(
            "ai",
            "api-ai",
            "api-human",
            item_key="hug",
            request_note="测试业务动作同步未读",
            chip_amount=5,
            custom_title=None,
            idempotency_key="api-exchange-action-create",
            pair_is_bound=True,
        )
        confirmed = await self.client.post(
            f"/api/chips/exchanges/{second['request_id']}/confirm",
            headers=self.headers(),
            json={"idempotency_key": "api-exchange-action-confirm"},
        )
        self.assertEqual(confirmed.status_code, 200, confirmed.text)
        self.assertEqual(confirmed.json()["unread"]["categories"]["exchange"], 0)
        self.assertEqual(confirmed.json()["exchange"]["pending_count"], 1)
        self.assertGreater(
            confirmed.json()["unread_revision"], refreshed.json()["unread_revision"]
        )

    async def test_mcp_summary_hints_and_category_lists_consume_atomically(self):
        self.seed("ai", "mcp-ai", "loan", "loan-mcp", "loan")
        self.seed("ai", "mcp-ai", "exchange", "exchange-mcp", "exchange")
        self.seed("ai", "mcp-ai", "achievement", "achievement-mcp", "achievement")
        status = await self.client.post(
            "/mcp/play",
            json={
                "action": "chips", "player_id": "mcp-ai",
                "opponent_id": "mcp-human", "op": "status",
            },
        )
        self.assertEqual(status.status_code, 200, status.text)
        self.assertEqual(status.json()["unread"], {
            "total": 3,
            "categories": {"game": 0, "loan": 1, "exchange": 1, "achievement": 1},
        })
        self.assertIn("借款（未读1）→chips/loans", status.json()["unread_hint"])
        self.assertIn("兑换（未读1）→chips/exchange", status.json()["unread_hint"])
        self.assertIn("成就（未读1）→chips/achievements", status.json()["unread_hint"])

        listed = await self.client.post(
            "/mcp/play",
            json={
                "action": "chips", "player_id": "mcp-ai",
                "opponent_id": "mcp-human", "op": "loans", "loan_action": "list",
            },
        )
        self.assertEqual([item["reference_id"] for item in listed.json()["notices"]], ["loan-mcp"])
        self.assertEqual(listed.json()["unread"]["categories"]["loan"], 0)
        self.assertEqual(listed.json()["unread"]["categories"]["exchange"], 1)
        self.assertEqual(listed.json()["unread"]["categories"]["achievement"], 1)

        exchange_list = await self.client.post(
            "/mcp/play",
            json={
                "action": "chips", "player_id": "mcp-ai",
                "opponent_id": "mcp-human", "op": "exchange",
                "exchange_action": "list",
            },
        )
        self.assertEqual(
            [item["reference_id"] for item in exchange_list.json()["notices"]],
            ["exchange-mcp"],
        )
        self.assertEqual(exchange_list.json()["unread"]["categories"]["exchange"], 0)
        self.assertEqual(exchange_list.json()["unread"]["categories"]["achievement"], 1)

        achievements_list = await self.client.post(
            "/mcp/play",
            json={
                "action": "chips", "player_id": "mcp-ai",
                "opponent_id": "mcp-human", "op": "achievements",
            },
        )
        self.assertEqual(
            [item["reference_id"] for item in achievements_list.json()["notices"]],
            ["achievement-mcp"],
        )
        self.assertNotIn("unread", achievements_list.json())
        self.assertNotIn("unread_hint", achievements_list.json())

    async def test_rooms_and_terminal_state_consume_game_without_repeat(self):
        room = framework.create_room(
            "tictactoe", "human_first", "human", "mcp-game-human", "mcp-game-ai"
        )
        listed = await self.client.post(
            "/mcp/play", json={"action": "rooms", "player_id": "mcp-game-ai"}
        )
        self.assertEqual(listed.status_code, 200, listed.text)
        self.assertEqual(listed.json()["notices"][0]["reference_id"], room["room_id"])
        self.assertNotIn("unread", listed.json())
        listed_again = await self.client.post(
            "/mcp/play", json={"action": "rooms", "player_id": "mcp-game-ai"}
        )
        self.assertNotIn("notices", listed_again.json())

        for role, player_id, move in (
            ("human", "mcp-game-human", {"row": 0, "col": 0}),
            ("ai", "mcp-game-ai", {"row": 1, "col": 0}),
            ("human", "mcp-game-human", {"row": 0, "col": 1}),
            ("ai", "mcp-game-ai", {"row": 1, "col": 1}),
            ("human", "mcp-game-human", {"row": 0, "col": 2}),
        ):
            framework.play_move(room["room_id"], role, player_id, move)
        self.assertEqual(
            notifications.unread_summary("ai", "mcp-game-ai")["categories"]["game"], 1
        )
        state = await self.client.post(
            "/mcp/play",
            json={"action": "state", "player_id": "mcp-game-ai", "room_id": room["room_id"]},
        )
        self.assertEqual(state.json()["status"], "finished")
        self.assertEqual(
            notifications.unread_summary("ai", "mcp-game-ai")["categories"]["game"], 0
        )
        self.assertEqual(state.json()["unread"]["categories"]["game"], 0)
        self.assertGreater(state.json()["unread"]["categories"]["achievement"], 0)


class NotificationFrontendTests(unittest.TestCase):
    def test_badges_and_explicit_visible_ack_hooks_exist(self):
        root = Path(__file__).resolve().parents[1]
        index = (root / "app/static/index.html").read_text(encoding="utf-8")
        app_js = (root / "app/static/app.js").read_text(encoding="utf-8")
        chips_html = (root / "app/static/chips.html").read_text(encoding="utf-8")
        chips_js = (root / "app/static/chips.js").read_text(encoding="utf-8")
        for element_id in ("gameUnreadBadge", "chipCenterUnreadBadge"):
            self.assertIn(f'id="{element_id}"', index)
        for element_id in (
            "loanUnreadBadge", "exchangeUnreadBadge", "achievementUnreadBadge"
        ):
            self.assertIn(f'id="{element_id}"', chips_html)
        self.assertIn('request("/api/notifications/read"', app_js)
        self.assertIn('requestJson("/api/notifications/read"', chips_js)
        self.assertIn('if (!quiet) await ackHumanNotifications("game")', app_js)
        self.assertIn('await ackHumanNotifications("game", roomId)', app_js)
        self.assertIn("if (!quiet || visibleStateChanged)", app_js)
        self.assertIn("if (!identity) return", app_js)
        self.assertIn("if (document.hidden)", app_js)
        self.assertIn("if (!summary) return false", chips_js)
        self.assertIn("deferredUnreadAcks.add(category)", chips_js)
        self.assertIn("if (category) void ackUnreadCategory(category)", chips_js)
        self.assertIn('window.addEventListener("storage"', app_js)
        self.assertIn('window.addEventListener("storage"', chips_js)
        self.assertIn('document.addEventListener("visibilitychange"', app_js)
        self.assertIn('document.addEventListener("visibilitychange"', chips_js)
        self.assertIn("revision < latestUnreadRevision", app_js)
        self.assertIn("revision < latestUnreadRevision", chips_js)
        load_summary = chips_js[
            chips_js.index("async function loadSummary()"):
            chips_js.index("async function runHumanAction(")
        ]
        self.assertIn("ackVisibleUnreadCategory", load_summary)
        self.assertNotIn("还价", chips_html + chips_js)

import base64
import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import httpx

from app import chips, database, exchanges
from app import main as main_module
from app.framework import DuelError


class ExchangeServiceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="duel-exchange-")
        self.db_patch = patch.object(
            database, "DB_PATH", Path(self.temporary.name) / "test.db"
        )
        self.db_patch.start()
        database.init_db()
        self.clock = datetime(2026, 8, 29, 2, 0, tzinfo=timezone.utc)
        self.time_patch = patch.object(
            exchanges, "_utc_now", side_effect=lambda: self.clock
        )
        self.time_patch.start()

    def tearDown(self):
        self.time_patch.stop()
        self.db_patch.stop()
        self.temporary.cleanup()

    def create(
        self,
        actor_type="human",
        actor_id="human-1",
        counterparty_id="ai-1",
        *,
        item_key=None,
        note="我会在常用聊天里发一张窗外照片",
        amount=10,
        custom_title=None,
        key=None,
        bound=True,
    ):
        return exchanges.create_exchange_request(
            actor_type,
            actor_id,
            counterparty_id,
            item_key=item_key or ("good_life" if actor_type == "human" else "cyber_gift"),
            request_note=note,
            chip_amount=amount,
            custom_title=custom_title,
            idempotency_key=key or f"create:{actor_type}:{actor_id}:{amount}:{item_key or 'default'}",
            pair_is_bound=bound,
        )

    def confirm(self, request, *, key=None, bound_id=None):
        payer = request["payer"]
        return exchanges.confirm_exchange_request(
            request["request_id"],
            payer["type"],
            payer["id"],
            idempotency_key=key or f"confirm:{request['request_id']}",
            bound_counterparty_id=bound_id or request["initiator"]["id"],
        )

    def test_catalog_visibility_keeps_machine_items_secret(self):
        human = exchanges.list_catalog("human")
        machine = exchanges.list_catalog("ai")
        human_keys = {item["key"] for item in human}
        machine_keys = {item["key"] for item in machine}

        self.assertEqual(len(human), 9)
        self.assertEqual(len(machine), 8)
        self.assertIn("good_life", human_keys)
        self.assertNotIn("cyber_gift", human_keys)
        self.assertIn("cyber_gift", machine_keys)
        self.assertNotIn("good_life", machine_keys)
        self.assertTrue({"hug", "kiss", "nickname", "custom"} <= human_keys & machine_keys)

        secret = self.create("ai", "ai-1", "human-1")
        human_view = exchanges.list_exchange_requests(
            "human", "human-1", bound_counterparty_ids={"ai-1"}
        )["pending_for_me"][0]
        self.assertEqual(human_view["item"], secret["item"])
        self.assertEqual(human_view["item"]["key"], "cyber_gift")

    def test_both_directions_assign_payer_and_allowed_actions(self):
        human_request = self.create()
        machine_request = self.create("ai", "ai-1", "human-1")

        self.assertEqual(human_request["payer"], {"type": "ai", "id": "ai-1"})
        self.assertEqual(human_request["allowed_actions"], ["withdraw"])
        ai_list = exchanges.list_exchange_requests(
            "ai", "ai-1", bound_counterparty_ids={"human-1"}
        )
        self.assertEqual(ai_list["pending_for_me"][0]["allowed_actions"], ["confirm", "reject"])
        self.assertEqual(machine_request["payer"], {"type": "human", "id": "human-1"})
        human_list = exchanges.list_exchange_requests(
            "human", "human-1", bound_counterparty_ids={"ai-1"}
        )
        self.assertEqual(human_list["pending_for_me"][0]["request_id"], machine_request["request_id"])

    def test_note_custom_amount_and_catalog_role_validation(self):
        invalid_cases = [
            {"note": " ", "amount": 1},
            {"note": "好" * 121, "amount": 1},
            {"amount": 0},
            {"amount": 101},
            {"amount": True},
            {"item_key": "cyber_gift"},
            {"item_key": "good_life", "custom_title": "不该出现"},
            {"item_key": "custom", "custom_title": ""},
            {"item_key": "custom", "custom_title": "长" * 31},
        ]
        for index, values in enumerate(invalid_cases):
            with self.subTest(values=values), self.assertRaises(DuelError):
                self.create(key=f"invalid:create:{index}", **values)

        valid = self.create(
            item_key="custom", custom_title="一起看晚霞", amount=100,
            key="valid:custom:create",
        )
        self.assertEqual(valid["display_title"], "一起看晚霞")

    def test_pending_limit_is_per_pair_and_expired_requests_free_capacity(self):
        for index in range(3):
            self.create(amount=index + 1, key=f"pending:create:{index}")
        with self.assertRaises(DuelError):
            self.create(amount=4, key="pending:create:four")
        other = self.create(
            actor_id="human-1", counterparty_id="ai-2", amount=4,
            key="pending:create:other",
        )
        self.assertEqual(other["status"], "pending")

        self.clock += timedelta(hours=72, seconds=1)
        replacement = self.create(amount=5, key="pending:create:replacement")
        self.assertEqual(replacement["status"], "pending")

    def test_72_hours_expiry_and_close_actions_do_not_touch_wallets(self):
        request = self.create()
        before = chips.get_wallet("human", "human-1")["balance"]
        self.clock += timedelta(hours=72)
        expired = exchanges.get_exchange_request(
            request["request_id"], "human", "human-1", bound_counterparty_id="ai-1"
        )
        self.assertEqual(expired["status"], "expired")
        self.assertEqual(chips.get_wallet("human", "human-1")["balance"], before)

        withdrawn = self.create(key="close:create:withdraw")
        withdrawn = exchanges.close_exchange_request(
            withdrawn["request_id"], "human", "human-1", action="withdraw",
            idempotency_key="close:withdraw:1", bound_counterparty_id="ai-1",
        )
        rejected = self.create(key="close:create:reject")
        rejected = exchanges.close_exchange_request(
            rejected["request_id"], "ai", "ai-1", action="reject",
            idempotency_key="close:reject:1", bound_counterparty_id="human-1",
        )
        self.assertEqual((withdrawn["status"], rejected["status"]), ("withdrawn", "rejected"))
        self.assertEqual(chips.get_wallet("human", "human-1")["balance"], before)

    def test_confirm_writes_atomic_bilateral_ledger_and_is_idempotent(self):
        request = self.create(amount=35)
        first = self.confirm(request, key="confirm:stable:one")
        replay = self.confirm(request, key="confirm:stable:one")
        second_key = self.confirm(request, key="confirm:stable:two")

        self.assertEqual(first["status"], "completed")
        self.assertEqual(replay["status"], "completed")
        self.assertEqual(second_key["status"], "completed")
        self.assertEqual(chips.get_wallet("human", "human-1")["balance"], 235)
        self.assertEqual(chips.get_wallet("ai", "ai-1")["balance"], 165)
        human_rows = [
            row for row in chips.list_ledger("human", "human-1")
            if row["transaction_type"] == "exchange_in"
        ]
        ai_rows = [
            row for row in chips.list_ledger("ai", "ai-1")
            if row["transaction_type"] == "exchange_out"
        ]
        self.assertEqual(len(human_rows), 1)
        self.assertEqual(len(ai_rows), 1)
        self.assertEqual(human_rows[0]["reference_id"], request["request_id"])
        self.assertEqual(human_rows[0]["metadata"]["item"], request["item"])
        self.assertEqual(human_rows[0]["metadata"]["request_note"], request["request_note"])
        conn = database.connect()
        try:
            settlement_keys = {
                row[0] for row in conn.execute(
                    """
                    SELECT ledger.idempotency_key
                    FROM chip_ledger AS ledger
                    JOIN chip_wallets AS wallet ON wallet.id = ledger.wallet_id
                    WHERE ledger.reference_type = 'exchange_request'
                      AND ledger.reference_id = ?
                    """,
                    (request["request_id"],),
                )
            }
            achievement_rewards = conn.execute(
                "SELECT COUNT(*) FROM chip_ledger WHERE transaction_type = 'achievement_reward'"
            ).fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(settlement_keys, {f"exchange_settlement:{request['request_id']}"})
        self.assertEqual(achievement_rewards, 0)

    def test_confirmation_transaction_rolls_back_if_second_ledger_write_fails(self):
        request = self.create(amount=20)
        original = exchanges._apply_balance_change
        calls = 0

        def fail_second(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError("simulated second ledger failure")
            return original(*args, **kwargs)

        with patch.object(exchanges, "_apply_balance_change", side_effect=fail_second):
            with self.assertRaises(RuntimeError):
                self.confirm(request, key="confirm:rollback:1")
        self.assertEqual(chips.get_wallet("human", "human-1")["balance"], 200)
        self.assertEqual(chips.get_wallet("ai", "ai-1")["balance"], 200)
        current = exchanges.get_exchange_request(
            request["request_id"], "human", "human-1", bound_counterparty_id="ai-1"
        )
        self.assertEqual(current["status"], "pending")

    def test_concurrent_confirmation_moves_chips_once(self):
        request = self.create(amount=25)

        def approve(index):
            return self.confirm(request, key=f"confirm:race:{index}")

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(approve, range(2)))
        self.assertEqual([item["status"] for item in results], ["completed", "completed"])
        self.assertEqual(chips.get_wallet("human", "human-1")["balance"], 225)
        self.assertEqual(chips.get_wallet("ai", "ai-1")["balance"], 175)

    def test_daily_payer_limit_uses_shanghai_day_across_counterparties(self):
        first = self.create("ai", "ai-1", "human-1", amount=60, key="daily:create:1")
        second = self.create("ai", "ai-2", "human-1", amount=40, key="daily:create:2")
        self.confirm(first, key="daily:confirm:1")
        self.confirm(second, key="daily:confirm:2")
        over = self.create("ai", "ai-3", "human-1", amount=1, key="daily:create:3")
        with self.assertRaises(DuelError):
            self.confirm(over, key="daily:confirm:3")

        self.clock = datetime(2026, 8, 29, 16, 0, tzinfo=timezone.utc)
        next_day = self.confirm(over, key="daily:confirm:next")
        self.assertEqual(next_day["status"], "completed")

    def test_positive_and_sufficient_payer_balance_are_rechecked(self):
        zero = self.create(amount=1, key="balance:create:zero")
        chips.change_balance("ai", "ai-1", -200, "test_adjustment")
        with self.assertRaises(DuelError):
            self.confirm(zero, key="balance:confirm:zero")

        enough = self.create(
            actor_id="human-2", counterparty_id="ai-2", amount=100,
            key="balance:create:short",
        )
        chips.change_balance("ai", "ai-2", -101, "test_adjustment")
        with self.assertRaises(DuelError):
            self.confirm(enough, key="balance:confirm:short")

    def test_unbinding_expires_pending_but_preserves_completed_history(self):
        completed = self.create(amount=10, key="unbind:create:done")
        self.confirm(completed, key="unbind:confirm:done")
        pending = self.create(amount=11, key="unbind:create:pending")
        unbound_list = exchanges.list_exchange_requests(
            "human", "human-1", bound_counterparty_ids=set()
        )
        self.assertEqual(unbound_list, {
            "pending_for_me": [], "waiting_for_other": [], "history": []
        })
        conn = database.connect()
        try:
            states = dict(conn.execute(
                "SELECT request_id, status FROM exchange_requests"
            ).fetchall())
        finally:
            conn.close()
        self.assertEqual(states[completed["request_id"]], "completed")
        self.assertEqual(states[pending["request_id"]], "expired")

    def test_role_and_resource_permissions_cannot_cross_pairs(self):
        human_request = self.create()
        with self.assertRaises(DuelError):
            exchanges.confirm_exchange_request(
                human_request["request_id"], "human", "human-1",
                idempotency_key="permission:wrong:payer", bound_counterparty_id="ai-1",
            )
        with self.assertRaises(DuelError):
            exchanges.close_exchange_request(
                human_request["request_id"], "ai", "ai-1", action="withdraw",
                idempotency_key="permission:wrong:withdraw", bound_counterparty_id="human-1",
            )
        with self.assertRaises(DuelError):
            exchanges.get_exchange_request(
                human_request["request_id"], "ai", "ai-else",
                bound_counterparty_id="human-else",
            )
        with self.assertRaises(DuelError):
            exchanges.confirm_exchange_request(
                human_request["request_id"], "ai", "ai-1",
                idempotency_key="permission:wrong:binding",
                bound_counterparty_id="human-else",
            )
        self.assertEqual(chips.get_wallet("human", "human-1")["balance"], 200)

    def test_schema_initialization_is_additive_and_idempotent(self):
        chips.change_balance("human", "legacy-human", 7, "legacy_adjustment")
        database.init_db()
        database.init_db()
        self.assertEqual(chips.get_wallet("human", "legacy-human")["balance"], 207)
        conn = database.connect()
        try:
            tables = {row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )}
        finally:
            conn.close()
        self.assertTrue({"exchange_requests", "exchange_operations"} <= tables)


class ExchangeApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="duel-exchange-api-")
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
    def headers(machines=None):
        machines = machines or [
            {"id": "api-ai-1", "name": "甲机"},
            {"id": "api-ai-2", "name": "乙机"},
        ]
        encoded = base64.urlsafe_b64encode(
            json.dumps(machines, ensure_ascii=False).encode()
        ).decode().rstrip("=")
        return {
            "X-Duel-Human-Player": "api-human",
            "X-Duel-Human-Name": "%E4%BA%BA%E7%B1%BB",
            "X-Duel-Bound-Ais": encoded,
        }

    async def mcp(self, **values):
        body = {
            "action": "chips", "op": "exchange",
            "player_id": "api-ai-1", "opponent_id": "api-human",
            **values,
        }
        return await self.client.post("/mcp/play", json=body)

    async def test_web_catalog_never_leaks_machine_exclusives(self):
        response = await self.client.get(
            "/api/chips/exchanges/catalog", headers=self.headers()
        )
        self.assertEqual(response.status_code, 200, response.text)
        keys = {item["key"] for item in response.json()["catalog"]}
        self.assertNotIn("cyber_gift", keys)
        summary = await self.client.get("/api/chips", headers=self.headers())
        summary_text = json.dumps(summary.json(), ensure_ascii=False)
        self.assertNotIn("cyber_gift", summary_text)
        self.assertNotIn("赛博小礼物", summary_text)

    async def test_human_create_machine_confirm_and_mcp_catalog(self):
        created = await self.client.post(
            "/api/chips/exchanges", headers=self.headers(), json={
                "machine_id": "api-ai-1", "item_key": "world_glimpse",
                "request_note": "我会在聊天里发一张窗外照片", "chip_amount": 12,
                "custom_title": None, "idempotency_key": "web:exchange:create:1",
            },
        )
        self.assertEqual(created.status_code, 200, created.text)
        request_id = created.json()["request"]["request_id"]
        catalog = await self.mcp(exchange_action="catalog")
        keys = {item["key"] for item in catalog.json()["catalog"]}
        self.assertIn("cyber_gift", keys)
        self.assertNotIn("world_glimpse", keys)
        listed = await self.mcp(exchange_action="list")
        self.assertEqual(listed.json()["pending_approval"][0]["request_id"], request_id)
        confirmed = await self.mcp(
            exchange_action="confirm", request_id=request_id,
            idempotency_key="mcp:exchange:confirm:1",
        )
        self.assertEqual(confirmed.status_code, 200, confirmed.text)
        self.assertEqual(confirmed.json()["wallet"]["balance"], 188)
        self.assertEqual(confirmed.json()["bound_human_balance"], 212)

    async def test_machine_create_human_approves_while_machine_wallet_is_selected(self):
        created = await self.mcp(
            exchange_action="create", item_key="bedtime_story",
            request_note="今晚在聊天里讲一个短故事", chip_amount=20,
            idempotency_key="mcp:exchange:create:2",
        )
        self.assertEqual(created.status_code, 200, created.text)
        request_id = created.json()["request"]["request_id"]
        selected = await self.client.get(
            "/api/chips/machines/api-ai-1", headers=self.headers()
        )
        self.assertTrue(selected.json()["read_only"])
        pending = selected.json()["exchange"]["pending_for_me"]
        self.assertEqual(pending[0]["item"]["title"], "今夜有故事")
        approved = await self.client.post(
            f"/api/chips/exchanges/{request_id}/confirm",
            headers=self.headers(), json={"idempotency_key": "web:exchange:confirm:2"},
        )
        self.assertEqual(approved.status_code, 200, approved.text)
        self.assertEqual(approved.json()["wallet"]["balance"], 180)
        self.assertEqual(chips.get_wallet("ai", "api-ai-1")["balance"], 220)

    async def test_multi_machine_aggregate_and_selected_pair_filter(self):
        for machine_id, key in (("api-ai-1", "multi:create:1"), ("api-ai-2", "multi:create:2")):
            response = await self.client.post(
                "/api/chips/exchanges", headers=self.headers(), json={
                    "machine_id": machine_id, "item_key": "hug",
                    "request_note": f"给 {machine_id} 一个抱抱", "chip_amount": 1,
                    "idempotency_key": key,
                },
            )
            self.assertEqual(response.status_code, 200, response.text)
        summary = await self.client.get("/api/chips", headers=self.headers())
        waiting = summary.json()["exchange"]["waiting_for_other"]
        self.assertEqual({item["machine_name"] for item in waiting}, {"甲机", "乙机"})
        selected = await self.client.get(
            "/api/chips/machines/api-ai-2", headers=self.headers()
        )
        selected_waiting = selected.json()["exchange"]["waiting_for_other"]
        self.assertEqual([item["ai_id"] for item in selected_waiting], ["api-ai-2"])

    async def test_mcp_role_and_pair_isolation(self):
        created = await self.mcp(
            exchange_action="create", item_key="cyber_gift",
            request_note="送一份赛博礼物", chip_amount=5,
            idempotency_key="mcp:isolation:create",
        )
        request_id = created.json()["request"]["request_id"]
        wrong_machine = await self.client.post("/mcp/play", json={
            "action": "chips", "op": "exchange", "exchange_action": "withdraw",
            "player_id": "api-ai-2", "opponent_id": "api-human",
            "request_id": request_id, "idempotency_key": "mcp:isolation:machine",
        })
        wrong_human = await self.client.post("/mcp/play", json={
            "action": "chips", "op": "exchange", "exchange_action": "withdraw",
            "player_id": "api-ai-1", "opponent_id": "other-human",
            "request_id": request_id, "idempotency_key": "mcp:isolation:human",
        })
        self.assertEqual(wrong_machine.status_code, 403)
        self.assertEqual(wrong_human.status_code, 403)

    async def test_exchange_reminders_do_not_leak_to_whoami_game_or_normal_chips_mcp(self):
        created = await self.mcp(
            exchange_action="create", item_key="biased_fortune",
            request_note="给你一份偏心运势", chip_amount=3,
            idempotency_key="mcp:no-leak:create",
        )
        self.assertEqual(created.status_code, 200, created.text)
        whoami = await self.client.get("/api/whoami", headers=self.headers())
        normal = await self.client.post("/mcp/play", json={
            "action": "chips", "op": "status",
            "player_id": "api-ai-1", "opponent_id": "api-human",
        })
        rooms = await self.client.post("/mcp/play", json={
            "action": "rooms", "player_id": "api-ai-1",
        })
        game = await self.client.post("/mcp/play", json={
            "action": "new", "player_id": "api-ai-1",
            "opponent_id": "api-human", "game_type": "tictactoe",
            "mode": "human_first",
        })
        home = await self.client.get("/")
        self.assertNotIn("pending_count", home.text)
        for payload in (whoami.json(), normal.json(), rooms.json(), game.json()):
            serialized = json.dumps(payload, ensure_ascii=False)
            self.assertNotIn("pending_count", serialized)
            self.assertNotIn("biased_fortune", serialized)
            self.assertNotIn("兑换", serialized)


if __name__ == "__main__":
    unittest.main()

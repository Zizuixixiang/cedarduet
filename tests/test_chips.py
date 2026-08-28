import base64
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from app import chips, database
from app import main as main_module
from app.framework import DuelError


class ChipServiceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="duel-chips-")
        self.db_patch = patch.object(
            database, "DB_PATH", Path(self.temporary.name) / "test.db"
        )
        self.db_patch.start()
        database.init_db()

    def tearDown(self):
        self.db_patch.stop()
        self.temporary.cleanup()

    @staticmethod
    def wallet_count(subject_type, subject_id):
        conn = database.connect()
        try:
            return conn.execute(
                """
                SELECT COUNT(*) FROM chip_wallets
                WHERE subject_type = ? AND subject_id = ?
                """,
                (subject_type, subject_id),
            ).fetchone()[0]
        finally:
            conn.close()

    def test_new_wallet_receives_initial_balance_only_once(self):
        first = chips.get_wallet("human", "human-1")
        second = chips.get_wallet("human", "human-1")

        self.assertEqual(first["balance"], 200)
        self.assertEqual(second["balance"], 200)
        self.assertEqual(self.wallet_count("human", "human-1"), 1)
        ledger = chips.list_ledger("human", "human-1")
        self.assertEqual(len(ledger), 1)
        self.assertEqual(ledger[0]["transaction_type"], "wallet_opened")
        self.assertEqual(ledger[0]["amount"], 200)

    def test_human_and_ai_with_same_stable_id_have_separate_wallets(self):
        chips.change_balance("human", "shared-1", -40, "test_adjustment")

        self.assertEqual(chips.get_wallet("human", "shared-1")["balance"], 160)
        self.assertEqual(chips.get_wallet("ai", "shared-1")["balance"], 200)

    def test_same_day_check_in_is_idempotent(self):
        first = chips.claim_daily_check_in("human", "human-1")
        second = chips.claim_daily_check_in("human", "human-1")

        self.assertTrue(first["claimed"])
        self.assertFalse(second["claimed"])
        self.assertEqual(second["wallet"]["balance"], 220)
        check_ins = [
            row
            for row in chips.list_ledger("human", "human-1")
            if row["transaction_type"] == "daily_check_in"
        ]
        self.assertEqual(len(check_ins), 1)
        self.assertEqual(check_ins[0]["amount"], 20)

    def test_balance_above_threshold_cannot_declare_bankruptcy(self):
        chips.change_balance("human", "human-1", -699, "test_loss")
        self.assertEqual(chips.get_wallet("human", "human-1")["balance"], -499)

        with self.assertRaises(DuelError):
            chips.declare_bankruptcy("human", "human-1")

    def test_threshold_bankruptcy_is_atomic_and_writes_one_ledger_entry(self):
        chips.change_balance("human", "human-1", -700, "test_loss")
        wallet = chips.declare_bankruptcy("human", "human-1")

        self.assertEqual(wallet["balance"], 50)
        self.assertEqual(wallet["bankruptcy_count"], 1)
        self.assertTrue(wallet["bankruptcy_active"])
        self.assertEqual(wallet["bankruptcy_badge"]["id"], "pixel_dirt_poor")
        bankruptcy = [
            row
            for row in chips.list_ledger("human", "human-1")
            if row["transaction_type"] == "bankruptcy_reset"
        ]
        self.assertEqual(len(bankruptcy), 1)
        self.assertEqual(bankruptcy[0]["amount"], 550)
        self.assertEqual(bankruptcy[0]["balance_after"], 50)
        self.assertEqual(bankruptcy[0]["metadata"]["balance_before"], -500)

    def test_any_balance_change_reaching_200_clears_bankruptcy_badge(self):
        chips.change_balance("human", "human-1", -700, "test_loss")
        chips.declare_bankruptcy("human", "human-1")
        still_active = chips.change_balance(
            "human", "human-1", 149, "test_recovery"
        )
        recovered = chips.change_balance(
            "human", "human-1", 1, "test_recovery"
        )

        self.assertEqual(still_active["balance"], 199)
        self.assertTrue(still_active["bankruptcy_active"])
        self.assertEqual(recovered["balance"], 200)
        self.assertFalse(recovered["bankruptcy_active"])
        self.assertIsNone(recovered["bankruptcy_badge"])
        self.assertEqual(recovered["bankruptcy_count"], 1)


class ChipApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="duel-chips-api-")
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
    def wallet_count(subject_type, subject_id):
        conn = database.connect()
        try:
            return conn.execute(
                """
                SELECT COUNT(*) FROM chip_wallets
                WHERE subject_type = ? AND subject_id = ?
                """,
                (subject_type, subject_id),
            ).fetchone()[0]
        finally:
            conn.close()

    @staticmethod
    def trusted_headers(machines=None):
        encoded = base64.urlsafe_b64encode(
            json.dumps(machines or [], ensure_ascii=False).encode("utf-8")
        ).decode("ascii").rstrip("=")
        return {
            "X-Duel-Human-Player": "human-7",
            "X-Duel-Human-Name": "%E5%8D%97%E5%B1%B1%E5%90%9B",
            "X-Duel-Bound-Ais": encoded,
        }

    async def test_chip_center_requires_authenticated_human(self):
        response = await self.client.get("/api/chips")

        self.assertEqual(response.status_code, 403)
        self.assertIn("首页登录", response.json()["message"])

    async def test_unbound_ai_wallet_cannot_be_read(self):
        response = await self.client.get(
            "/api/chips/machines/ai-9", headers=self.trusted_headers()
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.wallet_count("ai", "ai-9"), 0)

    async def test_bound_ai_wallet_is_read_only_and_separate(self):
        headers = self.trusted_headers([{"id": "ai-9", "name": "克莱奥"}])
        chips.claim_daily_check_in("ai", "ai-9")
        chips.change_balance("ai", "ai-9", -720, "test_loss")
        chips.declare_bankruptcy("ai", "ai-9")
        response = await self.client.get(
            "/api/chips/machines/ai-9", headers=headers
        )

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertTrue(payload["read_only"])
        self.assertEqual(payload["machine"], {"id": "ai-9", "name": "克莱奥"})
        self.assertEqual(payload["wallet"]["balance"], 50)
        self.assertTrue(payload["wallet"]["checked_in_today"])
        self.assertTrue(payload["wallet"]["bankruptcy_active"])
        self.assertEqual(payload["wallet"]["bankruptcy_count"], 1)
        self.assertEqual(
            [entry["transaction_type"] for entry in payload["ledger"]],
            ["bankruptcy_reset", "test_loss", "daily_check_in", "wallet_opened"],
        )

        for action in ("check-in", "bankruptcy"):
            forbidden_target = await self.client.post(
                f"/api/chips/machines/ai-9/{action}",
                headers=headers,
                json={},
            )
            self.assertEqual(forbidden_target.status_code, 404)

    async def test_human_action_rejects_client_supplied_ai_identity(self):
        headers = self.trusted_headers([{"id": "ai-9", "name": "克莱奥"}])
        for action in ("check-in", "bankruptcy"):
            response = await self.client.post(
                f"/api/chips/{action}",
                headers=headers,
                json={"player_id": "ai-9"},
            )
            self.assertEqual(response.status_code, 422)

        self.assertEqual(self.wallet_count("ai", "ai-9"), 0)

    async def test_daily_check_in_api_is_idempotent_for_authenticated_human(self):
        headers = self.trusted_headers()
        first = await self.client.post(
            "/api/chips/check-in", headers=headers, json={}
        )
        second = await self.client.post(
            "/api/chips/check-in", headers=headers, json={}
        )

        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(second.status_code, 200, second.text)
        self.assertTrue(first.json()["claimed"])
        self.assertFalse(second.json()["claimed"])
        self.assertEqual(second.json()["wallet"]["balance"], 220)

    async def test_page_and_its_assets_are_independent(self):
        page = await self.client.get("/chips")
        script = await self.client.get("/static/chips.js")
        styles = await self.client.get("/static/chips.css")

        self.assertEqual(page.status_code, 200)
        self.assertIn("筹码中心", page.text)
        self.assertIn("不支持人民币充值", page.text)
        self.assertIn("/static/chips.js", page.text)
        self.assertIn("/static/chips.css", page.text)
        self.assertNotIn("/static/app.js", page.text)
        self.assertNotIn("/static/styles.css", page.text)
        self.assertEqual(script.status_code, 200)
        self.assertEqual(styles.status_code, 200)
        self.assertEqual(script.headers["cache-control"], "no-store")
        self.assertEqual(styles.headers["cache-control"], "no-store")


if __name__ == "__main__":
    unittest.main()

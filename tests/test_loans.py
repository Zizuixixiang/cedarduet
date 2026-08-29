import base64
import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import httpx

from app import achievements, chips, database, loans
from app import main as main_module
from app.framework import DuelError


class LoanServiceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="duel-loans-")
        self.db_patch = patch.object(
            database, "DB_PATH", Path(self.temporary.name) / "test.db"
        )
        self.db_patch.start()
        database.init_db()
        self.clock = datetime(2026, 8, 1, 4, 0, tzinfo=timezone.utc)
        self.time_patch = patch.object(loans, "_utc_now", side_effect=lambda: self.clock)
        self.time_patch.start()
        self.sequence = 0

    def tearDown(self):
        self.time_patch.stop()
        self.db_patch.stop()
        self.temporary.cleanup()

    def key(self, prefix="operation"):
        self.sequence += 1
        return f"{prefix}:{self.sequence:04d}"

    def due(self, days=7):
        return (
            self.clock.astimezone(loans.SHANGHAI).date() + timedelta(days=days)
        ).isoformat()

    def create(
        self, borrower_type="human", borrower_id="human-1", lender_id="ai-1",
        *, principal=20, rate=0, days=7, cap=True,
    ):
        return loans.create_loan(
            borrower_type, borrower_id, lender_id,
            principal=principal,
            daily_rate_micro_percent=rate,
            due_date=self.due(days),
            interest_cap_enabled=cap,
            idempotency_key=self.key("create"),
            pair_is_bound=True,
        )

    def accept(self, loan, *, key=None):
        lender = loan["lender"]
        return loans.accept_loan(
            loan["loan_id"], lender["type"], lender["id"],
            revision=loan["revision"],
            idempotency_key=key or self.key("accept"),
            bound_counterparty_id=loan["borrower"]["id"],
        )

    def set_last_accrual(self, loan_id, moment):
        with database.write_transaction() as conn:
            conn.execute(
                "UPDATE loans SET last_accrual_at = ? WHERE loan_id = ?",
                (moment.isoformat(timespec="seconds"), loan_id),
            )

    def unlock_ids(self, subject_type, subject_id):
        conn = database.connect()
        try:
            return {
                row["achievement_id"]
                for row in conn.execute(
                    "SELECT achievement_id FROM achievement_unlocks "
                    "WHERE subject_type = ? AND subject_id = ?",
                    (subject_type, subject_id),
                )
            }
        finally:
            conn.close()

    def test_additive_schema_is_idempotent_and_does_not_infer_legacy_debt(self):
        database.init_db()
        chips.change_balance(
            "human", "legacy-human", -10, "loan_repayment_out",
            reference_type="loan", reference_id="old-ledger-row",
        )
        database.init_db()
        self.assertEqual(loans.list_loans("human", "legacy-human"), [])
        self.assertFalse(
            self.unlock_ids("human", "legacy-human")
            & {item.id for item in achievements.ACHIEVEMENT_CATALOG if item.category == "loan"}
        )

    def test_only_borrower_creates_and_both_directions_are_isolated(self):
        human_borrow = self.create()
        ai_borrow = self.create("ai", "ai-2", "human-1")
        self.assertEqual(human_borrow["borrower"], {"type": "human", "id": "human-1"})
        self.assertEqual(ai_borrow["borrower"], {"type": "ai", "id": "ai-2"})
        self.assertEqual(human_borrow["direction"], "borrowing")
        self.assertEqual(ai_borrow["direction"], "borrowing")
        self.assertEqual(
            [item["loan_id"] for item in loans.list_loans("ai", "ai-1")],
            [human_borrow["loan_id"]],
        )
        with self.assertRaises(DuelError):
            loans.get_loan(human_borrow["loan_id"], "ai", "ai-2")
        with self.assertRaises(DuelError):
            loans.create_loan(
                "human", "human-1", "ai-3", principal=1,
                daily_rate_micro_percent=0, due_date=self.due(),
                idempotency_key=self.key(), pair_is_bound=False,
            )

    def test_revision_turn_counter_and_old_acceptance(self):
        loan = self.create(principal=30)
        with self.assertRaises(DuelError):
            loans.counter_loan(
                loan["loan_id"], "human", "human-1", revision=1,
                principal=31, daily_rate_micro_percent=0, due_date=self.due(),
                interest_cap_enabled=True, idempotency_key=self.key(),
                bound_counterparty_id="ai-1",
            )
        countered = loans.counter_loan(
            loan["loan_id"], "ai", "ai-1", revision=1,
            principal=31, daily_rate_micro_percent=125_000,
            due_date=self.due(), interest_cap_enabled=False,
            idempotency_key=self.key("counter"), bound_counterparty_id="human-1",
        )
        self.assertEqual(countered["revision"], 2)
        self.assertTrue(countered["awaiting"]["id"] == "human-1")
        with self.assertRaises(DuelError):
            loans.accept_loan(
                loan["loan_id"], "human", "human-1", revision=1,
                idempotency_key=self.key(), bound_counterparty_id="ai-1",
            )
        active = loans.accept_loan(
            loan["loan_id"], "human", "human-1", revision=2,
            idempotency_key=self.key("accept"), bound_counterparty_id="ai-1",
        )
        self.assertEqual(active["principal"], 31)
        self.assertFalse(active["interest_cap_enabled"])
        self.assertIn("loan_pair_counter_activated", self.unlock_ids("human", "human-1"))
        self.assertIn("loan_pair_counter_activated", self.unlock_ids("ai", "ai-1"))

    def test_reject_withdraw_and_expiry_release_open_slot(self):
        first = self.create()
        rejected = loans.close_proposal(
            first["loan_id"], "ai", "ai-1", action="reject", revision=1,
            idempotency_key=self.key("reject"),
        )
        self.assertEqual(rejected["status"], "rejected")
        second = self.create()
        with self.assertRaises(DuelError):
            loans.close_proposal(
                second["loan_id"], "ai", "ai-1", action="withdraw", revision=1,
                idempotency_key=self.key(),
            )
        withdrawn = loans.close_proposal(
            second["loan_id"], "human", "human-1", action="withdraw", revision=1,
            idempotency_key=self.key("withdraw"),
        )
        self.assertEqual(withdrawn["status"], "withdrawn")
        expiring = self.create()
        self.clock += timedelta(days=3)
        self.assertEqual(
            loans.get_loan(expiring["loan_id"], "human", "human-1")["status"],
            "expired",
        )
        self.create()

    def test_three_open_limit_and_overdue_blocks_only_new_borrowing(self):
        for index in range(3):
            self.create(lender_id=f"ai-{index}", principal=1)
        with self.assertRaisesRegex(DuelError, "最多同时保有 3 张"):
            self.create(lender_id="ai-4", principal=1)

        other = self.create("human", "human-2", "ai-overdue", principal=5, days=1)
        active = self.accept(other)
        self.clock += timedelta(days=2)
        refreshed = loans.get_loan(active["loan_id"], "human", "human-2")
        self.assertEqual(refreshed["status"], "overdue")
        with self.assertRaisesRegex(DuelError, "逾期"):
            self.create("human", "human-2", "ai-new", principal=1)
        chips.claim_daily_check_in("human", "human-2")
        loans.repay_loan(
            active["loan_id"], "human", "human-2", amount=5,
            idempotency_key=self.key("repay"),
        )

    def test_shanghai_date_boundaries_and_thirty_day_validation(self):
        self.clock = datetime(2026, 8, 1, 15, 59, 59, tzinfo=timezone.utc)
        self.assertEqual(self.due(1), "2026-08-02")
        self.create(days=30)
        with self.assertRaises(DuelError):
            self.create(days=0)
        with self.assertRaises(DuelError):
            self.create(days=31)
        loan = self.create("human", "boundary-human", "boundary-ai", days=1)
        active = self.accept(loan)
        self.clock = datetime(2026, 8, 2, 15, 59, 59, tzinfo=timezone.utc)
        self.assertEqual(
            loans.get_loan(active["loan_id"], "human", "boundary-human")["status"],
            "active",
        )
        self.clock += timedelta(seconds=1)
        overdue = loans.get_loan(active["loan_id"], "human", "boundary-human")
        self.assertEqual(overdue["status"], "overdue")
        self.assertEqual(overdue["overdue_days"], 1)

        stale_due = self.create(
            "human", "stale-human", "stale-ai", principal=1, days=1
        )
        self.clock += timedelta(days=1)
        with self.assertRaisesRegex(DuelError, "至少"):
            self.accept(stale_due)

    def test_accept_rechecks_balance_atomically_and_is_idempotent(self):
        empty = self.create("human", "human-empty", "ai-empty", principal=10)
        chips.change_balance("ai", "ai-empty", -200, "test_adjustment")
        with self.assertRaises(DuelError):
            self.accept(empty)
        self.assertEqual(
            loans.get_loan(empty["loan_id"], "human", "human-empty")["status"],
            "negotiating",
        )
        self.assertFalse(any(
            row["transaction_type"].startswith("loan_principal")
            for row in chips.list_ledger("ai", "ai-empty")
        ))

        loan = self.create("human", "human-race", "ai-race", principal=100)
        key = self.key("same-accept")
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self.accept(loan, key=key), range(2)))
        self.assertTrue(all(item["status"] == "active" for item in results))
        principal_out = [
            row for row in chips.list_ledger("ai", "ai-race")
            if row["transaction_type"] == "loan_principal_out"
        ]
        self.assertEqual(len(principal_out), 1)

    def test_competing_accepts_never_make_lender_negative(self):
        first = self.create("human", "borrower-a", "shared-ai", principal=150)
        second = self.create("human", "borrower-b", "shared-ai", principal=150)
        self.accept(first)
        with self.assertRaises(DuelError):
            self.accept(second)
        self.assertGreaterEqual(chips.get_wallet("ai", "shared-ai")["balance"], 0)
        self.assertEqual(loans.get_loan(second["loan_id"], "human", "borrower-b")["status"], "negotiating")

    def test_interest_carries_remainder_and_floors_only_when_whole(self):
        loan = self.accept(self.create(principal=1, rate=100_000_000))
        self.clock += timedelta(hours=12)
        half = loans.get_loan(loan["loan_id"], "human", "human-1")
        self.assertEqual(half["accrued_interest"], 0)
        self.clock += timedelta(hours=12)
        whole = loans.get_loan(loan["loan_id"], "human", "human-1")
        self.assertEqual(whole["accrued_interest"], 1)
        self.assertEqual(whole["interest_rounding"], "carry_remainder_then_floor")

    def test_partial_repayment_is_interest_first_and_retry_is_idempotent(self):
        loan = self.accept(self.create(principal=100, rate=1_000_000))
        self.clock += timedelta(days=1)
        key = self.key("repay-same")
        first = loans.repay_loan(
            loan["loan_id"], "human", "human-1", amount=51,
            idempotency_key=key,
        )
        replay = loans.repay_loan(
            loan["loan_id"], "human", "human-1", amount=51,
            idempotency_key=key,
        )
        self.assertEqual(first["repayment"]["interest"], 1)
        self.assertEqual(first["repayment"]["principal"], 50)
        self.assertEqual(first["remaining_principal"], 50)
        self.assertTrue(replay["repayment"]["idempotent_replay"])
        rows = [
            row for row in chips.list_ledger("human", "human-1")
            if row["transaction_type"] == "loan_repayment_out"
        ]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["metadata"]["interest"], 1)
        self.assertEqual(rows[0]["metadata"]["principal"], 50)
        self.assertIn("loan_first_partial_repayment", self.unlock_ids("human", "human-1"))

    def test_repayment_permissions_wallet_floor_and_overpayment(self):
        loan = self.accept(self.create("human", "poor-human", "ai-lender", principal=20))
        with self.assertRaises(DuelError):
            loans.repay_loan(
                loan["loan_id"], "ai", "ai-lender", amount=1,
                idempotency_key=self.key(),
            )
        with self.assertRaises(DuelError):
            loans.repay_loan(
                loan["loan_id"], "human", "poor-human", amount=21,
                idempotency_key=self.key(),
            )
        chips.change_balance("human", "poor-human", -225, "test_adjustment")
        with self.assertRaises(DuelError):
            loans.repay_loan(
                loan["loan_id"], "human", "poor-human", amount=1,
                idempotency_key=self.key(),
            )

    def test_lifetime_interest_cap_survives_partial_payment_and_uncapped_continues(self):
        capped = self.accept(self.create(
            "human", "cap-human", "cap-ai", principal=100,
            rate=100_000_000, cap=True,
        ))
        self.clock += timedelta(hours=12)
        capped = loans.get_loan(capped["loan_id"], "human", "cap-human")
        self.assertEqual(capped["accrued_interest"], 50)
        loans.repay_loan(
            capped["loan_id"], "human", "cap-human", amount=50,
            idempotency_key=self.key("cap-interest"),
        )
        self.clock += timedelta(days=2)
        capped = loans.get_loan(capped["loan_id"], "human", "cap-human")
        self.assertEqual(capped["lifetime_interest"], 100)
        self.assertTrue(capped["interest_cap_reached"])
        self.assertIn("loan_interest_cap_reached", self.unlock_ids("human", "cap-human"))

        uncapped = self.accept(self.create(
            "human", "uncap-human", "uncap-ai", principal=100,
            rate=200_000_000, cap=False,
        ))
        self.clock += timedelta(days=1)
        uncapped = loans.get_loan(uncapped["loan_id"], "human", "uncap-human")
        self.assertEqual(uncapped["accrued_interest"], 200)
        self.assertFalse(uncapped["interest_cap_enabled"])

    def test_bankruptcy_does_not_change_debt_and_unbound_debt_can_be_repaid(self):
        loan = self.accept(self.create(
            "human", "bankrupt-human", "bankrupt-ai", principal=20
        ))
        chips.change_balance("human", "bankrupt-human", -725, "test_loss")
        chips.declare_bankruptcy("human", "bankrupt-human")
        after = loans.get_loan(loan["loan_id"], "human", "bankrupt-human")
        self.assertEqual(after["remaining_principal"], 20)
        unbound = loans.list_loans(
            "human", "bankrupt-human", bound_counterparty_ids=set()
        )[0]
        self.assertFalse(unbound["pair_currently_bound"])
        self.assertIn("repay", unbound["allowed_actions"])
        repaid = loans.repay_loan(
            loan["loan_id"], "human", "bankrupt-human", amount=20,
            idempotency_key=self.key("after-unbind"),
        )
        self.assertEqual(repaid["status"], "repaid")

    def test_loan_achievements_use_authoritative_facts_hidden_rules_and_zero_rewards(self):
        # A negative borrower at acceptance unlocks lender relief; countering also
        # supplies the reliable relationship fact.
        chips.change_balance("human", "hero-human", -250, "test_adjustment")
        countered = self.create("human", "hero-human", "hero-ai", principal=20)
        countered = loans.counter_loan(
            countered["loan_id"], "ai", "hero-ai", revision=1,
            principal=21, daily_rate_micro_percent=0, due_date=self.due(),
            interest_cap_enabled=True, idempotency_key=self.key("counter"),
            bound_counterparty_id="hero-human",
        )
        active = loans.accept_loan(
            countered["loan_id"], "human", "hero-human", revision=2,
            idempotency_key=self.key("accept"), bound_counterparty_id="hero-ai",
        )
        self.assertIn("loan_lend_to_negative_borrower", self.unlock_ids("ai", "hero-ai"))
        self.assertIn("loan_first_borrower_active", self.unlock_ids("human", "hero-human"))
        self.assertIn("loan_first_lender_active", self.unlock_ids("ai", "hero-ai"))

        # Fund repayment without changing the authoritative loan event history.
        chips.change_balance("human", "hero-human", 100, "test_grant")
        loans.repay_loan(
            active["loan_id"], "human", "hero-human", amount=21,
            idempotency_key=self.key("repay"),
        )
        self.assertIn("loan_first_ontime_repayment", self.unlock_ids("human", "hero-human"))

        # Same pair borrowing in the other direction awards both subjects.
        reverse = self.create("ai", "hero-ai", "hero-human", principal=10)
        self.accept(reverse)
        self.assertIn("loan_pair_bidirectional", self.unlock_ids("human", "hero-human"))
        self.assertIn("loan_pair_bidirectional", self.unlock_ids("ai", "hero-ai"))

        # Three simultaneously active debts, then all cleared, cover four public/
        # hidden progress achievements including three on-time repayments.
        active_loans = []
        for index in range(3):
            proposal = self.create(
                "human", "three-human", f"three-ai-{index}", principal=5
            )
            active_loans.append(self.accept(proposal))
        self.assertIn("loan_three_active", self.unlock_ids("human", "three-human"))
        projected = achievements.get_achievements("human", "three-human")
        hidden_ids = {
            item["id"] for section in projected["sections"] if section["id"] == "hidden"
            for item in section["items"]
        }
        self.assertIn("loan_three_active", hidden_ids)
        self.assertNotIn("loan_first_overdue", hidden_ids)
        for item in active_loans:
            loans.repay_loan(
                item["loan_id"], "human", "three-human", amount=5,
                idempotency_key=self.key("repay"),
            )
        unlocked = self.unlock_ids("human", "three-human")
        self.assertIn("loan_three_ontime_repayments", unlocked)
        self.assertIn("loan_debt_free_after_three", unlocked)

        overdue = self.accept(self.create(
            "human", "late-human", "late-ai", principal=1, days=1
        ))
        before = chips.get_wallet("human", "late-human")["balance"]
        self.clock += timedelta(days=2)
        loans.get_loan(overdue["loan_id"], "human", "late-human")
        after = chips.get_wallet("human", "late-human")["balance"]
        self.assertEqual(before, after)
        self.assertIn("loan_first_overdue", self.unlock_ids("human", "late-human"))
        hidden = next(
            section for section in achievements.get_achievements("human", "late-human")["sections"]
            if section["id"] == "hidden"
        )
        overdue_item = next(item for item in hidden["items"] if item["id"] == "loan_first_overdue")
        self.assertEqual(overdue_item["reward"], 0)
        self.assertFalse(any(
            row["transaction_type"] == "achievement_reward"
            and row["reference_id"] == "loan_first_overdue"
            for row in chips.list_ledger("human", "late-human")
        ))


class LoanApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="duel-loan-api-")
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
        machines = machines or [{"id": "api-ai", "name": "小机"}]
        encoded = base64.urlsafe_b64encode(
            json.dumps(machines, ensure_ascii=False).encode()
        ).decode().rstrip("=")
        return {
            "X-Duel-Human-Player": "api-human",
            "X-Duel-Human-Name": "%E4%BA%BA%E7%B1%BB",
            "X-Duel-Bound-Ais": encoded,
        }

    @staticmethod
    def due(days=7):
        return (
            datetime.now(timezone.utc).astimezone(loans.SHANGHAI).date()
            + timedelta(days=days)
        ).isoformat()

    async def test_human_web_create_and_ai_mcp_decisions(self):
        created = await self.client.post(
            "/api/chips/loans", headers=self.headers(), json={
                "machine_id": "api-ai", "principal": 30,
                "daily_rate_micro_percent": 125_000,
                "due_date": self.due(), "interest_cap_enabled": True,
                "idempotency_key": "web:create:0001",
            },
        )
        self.assertEqual(created.status_code, 200, created.text)
        loan = created.json()["loan"]
        wrong_turn = await self.client.post(
            f"/api/chips/loans/{loan['loan_id']}/accept",
            headers=self.headers(), json={
                "revision": 1, "idempotency_key": "web:accept:bad1"
            },
        )
        self.assertEqual(wrong_turn.status_code, 409)
        accepted = await self.client.post("/mcp/play", json={
            "action": "chips", "op": "loans", "loan_action": "accept",
            "player_id": "api-ai", "opponent_id": "api-human",
            "loan_id": loan["loan_id"], "loan_revision": 1,
            "idempotency_key": "mcp:accept:0001",
        })
        self.assertEqual(accepted.status_code, 200, accepted.text)
        self.assertEqual(accepted.json()["loan"]["status"], "active")

    async def test_ai_mcp_create_human_counter_accept_and_ai_repay(self):
        created = await self.client.post("/mcp/play", json={
            "action": "chips", "op": "loans", "loan_action": "create",
            "player_id": "api-ai", "opponent_id": "api-human",
            "principal": 20, "daily_rate_micro_percent": 0,
            "due_date": self.due(), "interest_cap_enabled": True,
            "idempotency_key": "mcp:create:0002",
        })
        self.assertEqual(created.status_code, 200, created.text)
        loan = created.json()["loan"]
        countered = await self.client.post(
            f"/api/chips/loans/{loan['loan_id']}/counter",
            headers=self.headers(), json={
                "revision": 1, "principal": 21,
                "daily_rate_micro_percent": 1,
                "due_date": self.due(), "interest_cap_enabled": False,
                "idempotency_key": "web:counter:0002",
            },
        )
        self.assertEqual(countered.status_code, 200, countered.text)
        accepted = await self.client.post("/mcp/play", json={
            "action": "chips", "op": "loans", "loan_action": "accept",
            "player_id": "api-ai", "opponent_id": "api-human",
            "loan_id": loan["loan_id"], "loan_revision": 2,
            "idempotency_key": "mcp:accept:0002",
        })
        self.assertEqual(accepted.status_code, 200, accepted.text)
        repaid = await self.client.post("/mcp/play", json={
            "action": "chips", "op": "loans", "loan_action": "repay",
            "player_id": "api-ai", "opponent_id": "api-human",
            "loan_id": loan["loan_id"], "amount": 21,
            "idempotency_key": "mcp:repay:0002",
        })
        self.assertEqual(repaid.status_code, 200, repaid.text)
        self.assertEqual(repaid.json()["loan"]["status"], "repaid")

    async def test_multi_machine_filter_and_explicit_exposure_boundary(self):
        headers = self.headers([
            {"id": "api-ai", "name": "甲"}, {"id": "api-ai-2", "name": "乙"}
        ])
        created = await self.client.post(
            "/api/chips/loans", headers=headers, json={
                "machine_id": "api-ai", "principal": 1,
                "daily_rate_micro_percent": 0, "due_date": self.due(),
                "interest_cap_enabled": True,
                "idempotency_key": "web:create:filter",
            },
        )
        self.assertEqual(created.status_code, 200, created.text)
        other = await self.client.get("/api/chips/machines/api-ai-2", headers=headers)
        self.assertEqual(other.status_code, 200, other.text)
        self.assertEqual(other.json()["loans"], [])
        whoami = await self.client.get("/api/whoami", headers=headers)
        self.assertNotIn("loans", whoami.json())
        status = await self.client.post("/mcp/play", json={
            "action": "chips", "op": "status",
            "player_id": "api-ai", "opponent_id": "api-human",
        })
        self.assertNotIn("loans", status.json())
        explicit = await self.client.post("/mcp/play", json={
            "action": "chips", "op": "loans", "loan_action": "list",
            "player_id": "api-ai", "opponent_id": "api-human",
        })
        self.assertEqual(len(explicit.json()["loans"]), 1)


if __name__ == "__main__":
    unittest.main()

"""Independent HTTP surface for the global chip center."""

from pathlib import Path
from typing import Callable
from urllib.parse import unquote

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel, ConfigDict

from .chips import (
    BANKRUPTCY_RESET_BALANCE,
    BANKRUPTCY_THRESHOLD,
    DAILY_CHECK_IN_AMOUNT,
    INITIAL_BALANCE,
    claim_daily_check_in,
    declare_bankruptcy,
    get_wallet,
    list_ledger,
)
from .achievements import filter_unlocks, get_achievements
from .framework import DuelError
from .exchanges import (
    CATALOG_BY_KEY,
    close_exchange_request,
    confirm_exchange_request,
    create_exchange_request,
    list_catalog,
    list_exchange_requests,
)
from .loans import (
    accept_loan,
    close_proposal,
    counter_loan,
    create_loan,
    get_loan,
    list_loans,
    repay_loan,
)
from .models import (
    ExchangeCreateBody,
    ExchangeDecisionBody,
    LoanCounterBody,
    LoanCreateBody,
    LoanDecisionBody,
    LoanRepaymentBody,
)
from .notifications import ack_explicit_achievement_unlocks, unread_state

ROOT = Path(__file__).resolve().parent
CHIPS_HTML = (ROOT / "static" / "chips.html").read_text(encoding="utf-8")
CHIPS_CSS = (ROOT / "static" / "chips.css").read_text(encoding="utf-8")
CHIPS_JS = (ROOT / "static" / "chips.js").read_text(encoding="utf-8")
EXCHANGE_ITEMS_ROOT = ROOT / "static" / "assets" / "exchange-shop" / "items"
EXCHANGE_ITEM_FILENAMES = frozenset(f"{key}.png" for key in CATALOG_BY_KEY)


class ChipActionBody(BaseModel):
    """Actions intentionally accept no target identity from the browser."""

    model_config = ConfigDict(extra="forbid")


def create_chips_router(
    trusted_human_player: Callable[[Request], str],
    trusted_bound_ais: Callable[[Request], list[dict[str, str]]],
) -> APIRouter:
    router = APIRouter()

    def named_exchange_payload(
        human_id: str,
        machines: list[dict[str, str]],
        *,
        machine_id: str | None = None,
    ) -> dict:
        names = {machine["id"]: machine["name"] for machine in machines}
        exchange = list_exchange_requests(
            "human",
            human_id,
            counterparty_id=machine_id,
            bound_counterparty_ids=set(names),
        )
        for bucket in exchange.values():
            for item in bucket:
                item["machine_name"] = names.get(item["ai_id"], item["ai_id"])
        exchange["catalog"] = list_catalog("human")
        exchange["pending_count"] = len(exchange["pending_for_me"])
        return exchange

    @router.get("/chips", response_class=HTMLResponse, include_in_schema=False)
    async def chips_page():
        return HTMLResponse(CHIPS_HTML, headers={"Cache-Control": "no-store"})

    @router.get("/static/chips.css", include_in_schema=False)
    async def chips_styles():
        return Response(
            CHIPS_CSS,
            media_type="text/css",
            headers={"Cache-Control": "no-store"},
        )

    @router.get("/static/chips.js", include_in_schema=False)
    async def chips_javascript():
        return Response(
            CHIPS_JS,
            media_type="text/javascript",
            headers={"Cache-Control": "no-store"},
        )

    @router.get(
        "/static/assets/exchange-shop/items/{filename}",
        include_in_schema=False,
    )
    async def exchange_item_image(filename: str):
        if filename not in EXCHANGE_ITEM_FILENAMES:
            raise DuelError("兑换商品图片不存在", 404)
        try:
            content = (EXCHANGE_ITEMS_ROOT / filename).read_bytes()
        except OSError as exc:
            raise DuelError("兑换商品图片不存在", 404) from exc
        return Response(
            content,
            media_type="image/png",
            headers={"Cache-Control": "public, max-age=31536000, immutable"},
        )

    @router.get("/api/chips")
    async def chips_summary(request: Request):
        human_id = trusted_human_player(request)
        human_name = (
            unquote(request.headers.get("X-Duel-Human-Name", "")).strip() or "你"
        )
        machines = trusted_bound_ais(request)
        machine_ids = {machine["id"] for machine in machines}
        loans = list_loans("human", human_id, bound_counterparty_ids=machine_ids)
        achievements = get_achievements("human", human_id)
        exchange = named_exchange_payload(human_id, machines)
        notification_state = unread_state("human", human_id)
        return {
            "ok": True,
            "human_name": human_name,
            "wallet": get_wallet("human", human_id),
            **notification_state,
            "machines": machines,
            "ledger": list_ledger("human", human_id),
            "loans": loans,
            "exchange": exchange,
            "achievements": achievements,
            "rules": {
                "initial_balance": INITIAL_BALANCE,
                "daily_check_in_amount": DAILY_CHECK_IN_AMOUNT,
                "bankruptcy_threshold": BANKRUPTCY_THRESHOLD,
                "bankruptcy_reset_balance": BANKRUPTCY_RESET_BALANCE,
                "real_money_exchange": False,
            },
        }

    @router.get("/api/chips/machines/{machine_id}")
    async def bound_machine_wallet(request: Request, machine_id: str):
        human_id = trusted_human_player(request)
        machines = trusted_bound_ais(request)
        selected = next(
            (machine for machine in machines if machine["id"] == machine_id), None
        )
        if selected is None:
            raise DuelError("这只小机不在当前账号的绑定清单中", 403)
        loans = list_loans(
            "human", human_id, counterparty_id=machine_id,
            bound_counterparty_ids={machine_id},
        )
        achievements = get_achievements(
            "ai", machine_id, bound_human_id=human_id
        )
        return {
            "ok": True,
            "machine": {"id": machine_id, "name": selected["name"]},
            "wallet": get_wallet("ai", machine_id),
            "ledger": list_ledger("ai", machine_id),
            "loans": loans,
            "exchange": named_exchange_payload(
                human_id, machines, machine_id=machine_id
            ),
            "achievements": achievements,
            "read_only": True,
        }

    @router.post("/api/chips/check-in")
    async def human_check_in(
        request: Request, _body: ChipActionBody | None = None
    ):
        human_id = trusted_human_player(request)
        machines = trusted_bound_ais(request)
        result = claim_daily_check_in(
            "human",
            human_id,
            bound_ai_ids=[machine["id"] for machine in machines],
        )
        unlocks = filter_unlocks(
            result.pop("unlocks", []), "human", human_id
        )
        ack_explicit_achievement_unlocks("human", human_id, unlocks)
        return {
            "ok": True,
            **result,
            "message": (
                f"签到成功，筹码 +{DAILY_CHECK_IN_AMOUNT}"
                + (f"；另有 {len(unlocks)} 项成就奖励已自动到账" if unlocks else "")
                if result["claimed"]
                else "今天已经签过到了"
                + (f"；另有 {len(unlocks)} 项成就奖励已自动到账" if unlocks else "")
            ),
            "ledger": list_ledger("human", human_id),
            "achievements": get_achievements("human", human_id),
            **unread_state("human", human_id),
            **({"unlocks": unlocks} if unlocks else {}),
        }

    @router.post("/api/chips/bankruptcy")
    async def human_bankruptcy(
        request: Request, _body: ChipActionBody | None = None
    ):
        human_id = trusted_human_player(request)
        wallet = declare_bankruptcy("human", human_id)
        unlocks = filter_unlocks(
            wallet.pop("unlocks", []), "human", human_id
        )
        ack_explicit_achievement_unlocks("human", human_id, unlocks)
        return {
            "ok": True,
            "wallet": wallet,
            "message": (
                f"已宣布破产，余额重置为 {BANKRUPTCY_RESET_BALANCE}"
                + ("；成就奖励已自动到账" if unlocks else "")
            ),
            "ledger": list_ledger("human", human_id),
            "achievements": get_achievements("human", human_id),
            **unread_state("human", human_id),
            **({"unlocks": unlocks} if unlocks else {}),
        }

    def human_loan_payload(request: Request, human_id: str, loan: dict) -> dict:
        machine_ids = {machine["id"] for machine in trusted_bound_ais(request)}
        loans = list_loans("human", human_id, bound_counterparty_ids=machine_ids)
        return {
            "ok": True, "loan": loan, "loans": loans,
            "wallet": get_wallet("human", human_id),
            "ledger": list_ledger("human", human_id),
            "achievements": get_achievements("human", human_id),
            **unread_state("human", human_id),
        }

    def human_exchange_payload(
        request: Request, human_id: str, exchange_request: dict
    ) -> dict:
        machines = trusted_bound_ais(request)
        return {
            "ok": True,
            "request": exchange_request,
            "exchange": named_exchange_payload(human_id, machines),
            "wallet": get_wallet("human", human_id),
            "ledger": list_ledger("human", human_id),
            **unread_state("human", human_id),
        }

    def current_exchange_for_human(
        request: Request, human_id: str, request_id: str
    ) -> tuple[dict, str]:
        machines = trusted_bound_ais(request)
        exchange = named_exchange_payload(human_id, machines)
        match = next(
            (
                item
                for bucket in ("pending_for_me", "waiting_for_other", "history")
                for item in exchange[bucket]
                if item["request_id"] == request_id
            ),
            None,
        )
        if match is None:
            raise DuelError("这张兑换申请不属于当前绑定关系", 403)
        return match, match["ai_id"]

    @router.get("/api/chips/exchanges/catalog")
    async def human_exchange_catalog(request: Request):
        trusted_human_player(request)
        return {"ok": True, "catalog": list_catalog("human")}

    @router.get("/api/chips/exchanges")
    async def human_list_exchanges(request: Request, machine_id: str | None = None):
        human_id = trusted_human_player(request)
        machines = trusted_bound_ais(request)
        if machine_id is not None and machine_id not in {
            machine["id"] for machine in machines
        }:
            raise DuelError("这只小机不在当前账号的绑定清单中", 403)
        return {
            "ok": True,
            "exchange": named_exchange_payload(
                human_id, machines, machine_id=machine_id
            ),
        }

    @router.post("/api/chips/exchanges")
    async def human_create_exchange(request: Request, body: ExchangeCreateBody):
        human_id = trusted_human_player(request)
        machines = trusted_bound_ais(request)
        machine_ids = {machine["id"] for machine in machines}
        created = create_exchange_request(
            "human",
            human_id,
            body.machine_id,
            item_key=body.item_key,
            request_note=body.request_note,
            chip_amount=body.chip_amount,
            custom_title=body.custom_title,
            idempotency_key=body.idempotency_key,
            pair_is_bound=body.machine_id in machine_ids,
        )
        return {
            **human_exchange_payload(request, human_id, created),
            "message": "申请已发送；请先在常用聊天中完成约定，再等小机确认并支付筹码。",
        }

    @router.post("/api/chips/exchanges/{request_id}/confirm")
    async def human_confirm_exchange(
        request_id: str, request: Request, body: ExchangeDecisionBody
    ):
        human_id = trusted_human_player(request)
        _existing, machine_id = current_exchange_for_human(
            request, human_id, request_id
        )
        confirmed = confirm_exchange_request(
            request_id,
            "human",
            human_id,
            idempotency_key=body.idempotency_key,
            bound_counterparty_id=machine_id,
        )
        return {
            **human_exchange_payload(request, human_id, confirmed),
            "message": f"已确认发放 {confirmed['chip_amount']} 枚筹码。",
        }

    @router.post("/api/chips/exchanges/{request_id}/reject")
    async def human_reject_exchange(
        request_id: str, request: Request, body: ExchangeDecisionBody
    ):
        human_id = trusted_human_player(request)
        _existing, machine_id = current_exchange_for_human(
            request, human_id, request_id
        )
        rejected = close_exchange_request(
            request_id,
            "human",
            human_id,
            action="reject",
            idempotency_key=body.idempotency_key,
            bound_counterparty_id=machine_id,
        )
        return {
            **human_exchange_payload(request, human_id, rejected),
            "message": "已拒绝这张兑换申请，不会移动筹码。",
        }

    @router.post("/api/chips/exchanges/{request_id}/withdraw")
    async def human_withdraw_exchange(
        request_id: str, request: Request, body: ExchangeDecisionBody
    ):
        human_id = trusted_human_player(request)
        _existing, machine_id = current_exchange_for_human(
            request, human_id, request_id
        )
        withdrawn = close_exchange_request(
            request_id,
            "human",
            human_id,
            action="withdraw",
            idempotency_key=body.idempotency_key,
            bound_counterparty_id=machine_id,
        )
        return {
            **human_exchange_payload(request, human_id, withdrawn),
            "message": "已撤回兑换申请，不会移动筹码。",
        }

    @router.post("/api/chips/loans")
    async def human_create_loan(request: Request, body: LoanCreateBody):
        human_id = trusted_human_player(request)
        machine_ids = {machine["id"] for machine in trusted_bound_ais(request)}
        loan = create_loan(
            "human", human_id, body.machine_id,
            principal=body.principal,
            daily_rate_micro_percent=body.daily_rate_micro_percent,
            due_date=body.due_date,
            interest_cap_enabled=body.interest_cap_enabled,
            idempotency_key=body.idempotency_key,
            pair_is_bound=body.machine_id in machine_ids,
        )
        return {
            **human_loan_payload(request, human_id, loan),
            "message": "借款提案已发给小机，等待小机回应。",
        }

    def bound_counterparty_for(
        request: Request, human_id: str, loan_id: str
    ) -> str | None:
        loan = get_loan(loan_id, "human", human_id)
        bound_ids = {machine["id"] for machine in trusted_bound_ais(request)}
        return loan["counterparty_id"] if loan["counterparty_id"] in bound_ids else None

    @router.post("/api/chips/loans/{loan_id}/accept")
    async def human_accept_loan(
        loan_id: str, request: Request, body: LoanDecisionBody
    ):
        human_id = trusted_human_player(request)
        loan = accept_loan(
            loan_id, "human", human_id, revision=body.revision,
            idempotency_key=body.idempotency_key,
            bound_counterparty_id=bound_counterparty_for(request, human_id, loan_id),
        )
        return {
            **human_loan_payload(request, human_id, loan),
            "message": "已接受当前条款，本金已原子转账。",
        }

    @router.post("/api/chips/loans/{loan_id}/reject")
    async def human_reject_loan(
        loan_id: str, request: Request, body: LoanDecisionBody
    ):
        human_id = trusted_human_player(request)
        loan = close_proposal(
            loan_id, "human", human_id, action="reject",
            revision=body.revision, idempotency_key=body.idempotency_key,
        )
        return {
            **human_loan_payload(request, human_id, loan),
            "message": "已拒绝当前借款提案。",
        }

    @router.post("/api/chips/loans/{loan_id}/withdraw")
    async def human_withdraw_loan(
        loan_id: str, request: Request, body: LoanDecisionBody
    ):
        human_id = trusted_human_player(request)
        loan = close_proposal(
            loan_id, "human", human_id, action="withdraw",
            revision=body.revision, idempotency_key=body.idempotency_key,
        )
        return {
            **human_loan_payload(request, human_id, loan),
            "message": "已撤销未生效的借款提案。",
        }

    @router.post("/api/chips/loans/{loan_id}/counter")
    async def human_counter_loan(
        loan_id: str, request: Request, body: LoanCounterBody
    ):
        human_id = trusted_human_player(request)
        loan = counter_loan(
            loan_id, "human", human_id, revision=body.revision,
            principal=body.principal,
            daily_rate_micro_percent=body.daily_rate_micro_percent,
            due_date=body.due_date,
            interest_cap_enabled=body.interest_cap_enabled,
            idempotency_key=body.idempotency_key,
            bound_counterparty_id=bound_counterparty_for(request, human_id, loan_id),
        )
        return {
            **human_loan_payload(request, human_id, loan),
            "message": "新条件已生成新 revision，等待小机回应。",
        }

    @router.post("/api/chips/loans/{loan_id}/repay")
    async def human_repay_loan(
        loan_id: str, request: Request, body: LoanRepaymentBody
    ):
        human_id = trusted_human_player(request)
        loan = repay_loan(
            loan_id, "human", human_id, amount=body.amount,
            idempotency_key=body.idempotency_key,
        )
        split = loan.get("repayment", {})
        return {
            **human_loan_payload(request, human_id, loan),
            "message": (
                f"还款 {split.get('amount', body.amount)} 枚："
                f"利息 {split.get('interest', 0)}，本金 {split.get('principal', 0)}。"
            ),
        }

    return router

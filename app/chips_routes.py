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

ROOT = Path(__file__).resolve().parent
CHIPS_HTML = (ROOT / "static" / "chips.html").read_text(encoding="utf-8")
CHIPS_CSS = (ROOT / "static" / "chips.css").read_text(encoding="utf-8")
CHIPS_JS = (ROOT / "static" / "chips.js").read_text(encoding="utf-8")


class ChipActionBody(BaseModel):
    """Actions intentionally accept no target identity from the browser."""

    model_config = ConfigDict(extra="forbid")


def create_chips_router(
    trusted_human_player: Callable[[Request], str],
    trusted_bound_ais: Callable[[Request], list[dict[str, str]]],
) -> APIRouter:
    router = APIRouter()

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

    @router.get("/api/chips")
    async def chips_summary(request: Request):
        human_id = trusted_human_player(request)
        human_name = (
            unquote(request.headers.get("X-Duel-Human-Name", "")).strip() or "你"
        )
        achievements = get_achievements("human", human_id)
        return {
            "ok": True,
            "human_name": human_name,
            "wallet": get_wallet("human", human_id),
            "machines": trusted_bound_ais(request),
            "ledger": list_ledger("human", human_id),
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
        achievements = get_achievements(
            "ai", machine_id, bound_human_id=human_id
        )
        return {
            "ok": True,
            "machine": {"id": machine_id, "name": selected["name"]},
            "wallet": get_wallet("ai", machine_id),
            "ledger": list_ledger("ai", machine_id),
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
        return {
            "ok": True,
            "wallet": wallet,
            "message": (
                f"已宣布破产，余额重置为 {BANKRUPTCY_RESET_BALANCE}"
                + ("；成就奖励已自动到账" if unlocks else "")
            ),
            "ledger": list_ledger("human", human_id),
            "achievements": get_achievements("human", human_id),
            **({"unlocks": unlocks} if unlocks else {}),
        }

    return router

import asyncio
import base64
import json
import mimetypes
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from secrets import choice as secure_choice
from urllib.parse import unquote

from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, Response

from .database import init_db
from .framework import (
    DuelError,
    claim_mcp_bootstrap,
    acknowledge_liars_dice_round,
    create_room,
    delete_terminal_room,
    get_room,
    has_new_room_events,
    join_room,
    leave_room,
    list_ai_rooms,
    list_human_pending_invitations,
    list_human_rooms,
    list_timeline,
    play_move,
    post_message,
    project_mcp_snapshot_for_viewer,
    project_room_for_viewer,
    read_new_room_events,
    resign,
    respond_to_invitation,
    set_room_preserved,
    update_participant_display_names,
)
from .chips_routes import create_chips_router
from .chips import (
    claim_daily_check_in,
    declare_bankruptcy,
    get_wallet,
    list_ledger,
)
from .achievements import compact_achievements, filter_unlocks, get_achievements
from .loans import (
    accept_loan,
    close_proposal,
    counter_loan,
    create_loan,
    list_loans,
    repay_loan,
)
from .exchanges import (
    EXCHANGE_RULE_SUMMARY,
    close_exchange_request,
    compact_exchange_lists,
    confirm_exchange_request,
    create_exchange_request,
    list_catalog,
    list_exchange_requests,
)
from .games import game_catalog, get_game
from .models import (
    CreateRoomBody,
    InvitationDecisionBody,
    JoinRoomBody,
    LeaveRoomBody,
    McpPlayBody,
    MessageBody,
    MoveBody,
    NotificationAckBody,
    ResignBody,
    RoomDeleteBody,
    RoomRetentionBody,
)
from .notifications import (
    ack_explicit_achievement_unlocks,
    ack_notification_ids_with_state,
    ack_notifications,
    ack_notifications_with_state,
    attach_mcp_unread,
    consume_notifications,
    notification_inbox_state,
    unread_state,
)
from .npc_personas import PersonaConfigError, resolve_avatar_file, select_personas
from .npc_providers import npc_provider_capabilities
from .npc_scheduler import NpcTurnScheduler, is_system_npc_turn

ROOT = Path(__file__).resolve().parent
INDEX_HTML = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
STYLES_CSS = (ROOT / "static" / "styles.css").read_text(encoding="utf-8")
APP_JS = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
GAME_UI_REGISTRY_JS = (
    ROOT / "static" / "game_ui_registry.js"
).read_text(encoding="utf-8")
GAME_UI_RENDERER_DIR = ROOT / "static" / "games"


def _parse_max_concurrent_waits(raw: str) -> int:
    try:
        count = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("DUEL_MAX_CONCURRENT_WAITS 必须是 1–500 的整数") from exc
    if not 1 <= count <= 500:
        raise ValueError("DUEL_MAX_CONCURRENT_WAITS 必须是 1–500 的整数")
    return count


MAX_CONCURRENT_WAITS = _parse_max_concurrent_waits(
    os.getenv("DUEL_MAX_CONCURRENT_WAITS", "20")
)


def _parse_mcp_wait_seconds(raw: str) -> float:
    try:
        seconds = float(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("DUEL_MCP_WAIT_SECONDS 必须是 1–45 的秒数") from exc
    if not 1 <= seconds <= 45:
        raise ValueError("DUEL_MCP_WAIT_SECONDS 必须是 1–45 的秒数")
    return seconds


MCP_WAIT_SECONDS = _parse_mcp_wait_seconds(
    os.getenv("DUEL_MCP_WAIT_SECONDS", "30")
)


class RevisionEvents:
    """Single-process revision notification hub; SQLite remains the source of truth."""

    def __init__(self, max_waiters: int = MAX_CONCURRENT_WAITS) -> None:
        self._events: dict[str, asyncio.Event] = {}
        self._max_waiters = max_waiters
        self._waiting_count = 0
        self._counter_lock = asyncio.Lock()

    def current(self, room_id: str) -> asyncio.Event:
        event = self._events.get(room_id)
        if event is None:
            event = asyncio.Event()
            self._events[room_id] = event
        return event

    def notify(self, room_id: str) -> None:
        event = self._events.pop(room_id, None)
        if event is not None:
            event.set()

    async def try_acquire_wait_slot(self) -> bool:
        async with self._counter_lock:
            if self._waiting_count >= self._max_waiters:
                return False
            self._waiting_count += 1
            return True

    async def release_wait_slot(self) -> None:
        async with self._counter_lock:
            self._waiting_count = max(0, self._waiting_count - 1)

    @property
    def waiting_count(self) -> int:
        return self._waiting_count


revision_events = RevisionEvents()
npc_turn_scheduler = NpcTurnScheduler(room_changed=revision_events.notify)


async def _schedule_current_system_npc(room: dict) -> bool:
    if not is_system_npc_turn(room):
        return False
    return await npc_turn_scheduler.schedule(room["room_id"])


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    await npc_turn_scheduler.start()
    try:
        yield
    finally:
        await npc_turn_scheduler.shutdown()


app = FastAPI(
    title="Duel — Human vs AI",
    version="1.0.0",
    description="纯单机、非社交的人类与绑定 AI 回合制对弈服务。",
    lifespan=lifespan,
)


@app.exception_handler(DuelError)
async def duel_error_handler(_request: Request, exc: DuelError):
    return JSONResponse(
        status_code=exc.status_code,
        content={"ok": False, "status": "error", "message": exc.message},
    )


@app.exception_handler(RequestValidationError)
async def validation_error_handler(_request: Request, exc: RequestValidationError):
    details = [
        {
            "field": ".".join(str(part) for part in error["loc"] if part != "body"),
            "message": error["msg"],
        }
        for error in exc.errors()
    ]
    return JSONResponse(
        status_code=422,
        content={
            "ok": False,
            "status": "error",
            "message": "请求参数不符合接口格式，请检查字段、类型与必填项。",
            "details": details,
        },
    )


def human_response(
    room: dict, message: str, viewer_player_id: str, status: str = "ok"
) -> dict:
    viewer = next(
        (
            item for item in room.get("participants", [])
            if item["player_id"] == viewer_player_id
        ),
        None,
    )
    if viewer is None:
        raise DuelError("viewer 不是该房间参与者", 403)
    if viewer.get("join_status") == "left":
        raise DuelError("当前参与者已经离开房间", 403)
    projected_room = project_room_for_viewer(room, viewer_player_id)
    payload = {
        "ok": True,
        "status": status,
        "message": message,
        "room": projected_room,
    }
    unlocks = filter_unlocks(
        room.get("achievement_unlocks", []), "human", viewer_player_id
    )
    if unlocks:
        payload["unlocks"] = unlocks
        ack_explicit_achievement_unlocks("human", viewer_player_id, unlocks)
    # Web receives its projected shared timeline while its independent cursor is
    # advanced so long-poll visibility checks remain correct.
    read_new_room_events(room["room_id"], viewer_player_id)
    payload["timeline"] = list_timeline(
        room["room_id"], viewer_player_id=viewer_player_id
    )
    return payload


def _chip_balances(room: dict) -> dict[str, int] | None:
    participants = room.get("participants", [])
    humans = [
        item for item in participants
        if item.get("participant_kind") in {None, "human"}
        and item["role"] == "human"
    ]
    ais = [
        item for item in participants
        if item.get("participant_kind") in {None, "bound_machine"}
        and item["role"] == "ai"
    ]
    if len(participants) != 2 or len(humans) != 1 or len(ais) != 1:
        return None
    ai_player_id = ais[0]["player_id"]
    human_player_id = humans[0]["player_id"]
    return {
        "ai": get_wallet("ai", ai_player_id)["balance"],
        "human": get_wallet("human", human_player_id)["balance"],
    }


def _compact_events(events: list[dict]) -> list[dict]:
    compact: list[dict] = []
    for event in events:
        item = {"name": event["sender"]["name"]}
        event_type = event["event_type"]
        move = event.get("move")
        if event_type == "result" and isinstance(move, dict):
            item.update(move)
        elif isinstance(move, dict):
            item["move"] = move
        elif event_type in {"resign", "leave"}:
            item["move"] = {"action": event_type}
        if event.get("text") and not (
            event_type == "result" and isinstance(move, dict)
        ):
            item["message"] = event["text"]
        compact.append(item)
    return compact


def _pending_ai_response(room: dict, player_id: str, message: str) -> dict:
    decision = next(
        (
            item["decision"]
            for item in room.get("confirmations", [])
            if item["player_id"] == player_id
        ),
        None,
    )
    payload = {
        "ok": True,
        "status": room["status"],
        "message": message,
        "room_id": room["room_id"],
        "game": room["game_type"],
        "stake": room.get("stake", 0),
        "confirmation_decision": decision,
    }
    if room["status"] == "pending":
        balances = _chip_balances(room)
        if balances is not None:
            payload["chip_balances"] = balances
    unlocks = filter_unlocks(room.get("achievement_unlocks", []), "ai", player_id)
    if unlocks:
        payload["unlocks"] = unlocks
    return payload


def _bootstrap_ai_response(
    room: dict, player_id: str, message: str, *, claimed: bool = False
) -> dict:
    if not claimed and not claim_mcp_bootstrap(room["room_id"], player_id):
        return _move_delta_response(room, player_id)
    projected_room = project_room_for_viewer(room, player_id)
    payload = {
        "ok": True,
        "status": room["status"],
        "bootstrap": True,
        "message": message,
        "room": projected_room,
    }
    balances = _chip_balances(room)
    if balances is not None:
        payload["chip_balances"] = balances
    events = _compact_events(read_new_room_events(room["room_id"], player_id))
    if events:
        payload["events"] = events
    unlocks = filter_unlocks(room.get("achievement_unlocks", []), "ai", player_id)
    if unlocks:
        payload["unlocks"] = unlocks
    return payload


def _terminal_fields(room: dict) -> dict:
    winner = room.get("winner")
    stake = room.get("stake", 0)
    balances = _chip_balances(room)
    if balances is None:
        return {
            "winner": winner,
            "winner_player_id": room.get("winner_player_id"),
            "game_result": room.get("result"),
        }
    ai_delta = stake if winner == "ai" else -stake if winner == "human" else 0
    return {
        "winner": winner,
        "result": (
            "win" if winner == "ai" else "loss" if winner == "human" else "draw"
        ),
        "settlement": {
            "stake": stake,
            "delta": {"ai": ai_delta, "human": -ai_delta},
            "balances": balances,
        },
    }


def _move_delta_response(
    room: dict,
    player_id: str,
    *,
    consume_events: bool = False,
) -> dict:
    if room.get("status") == "cancelled":
        return {
            "ok": True,
            "status": "cancelled",
            "room_id": room["room_id"],
            "revision": room.get("revision", 0),
        }
    participant = next(
        (
            item for item in room.get("participants", [])
            if item["player_id"] == player_id
        ),
        None,
    )
    if participant is None or participant.get("join_status") == "left":
        payload = {
            "ok": True,
            "status": "left",
            "room_id": room["room_id"],
            "revision": room["revision"],
        }
        if participant is not None:
            events = _compact_events(
                read_new_room_events(room["room_id"], player_id)
            )
            if events:
                payload["events"] = events
        if room["status"] in {"finished", "archived"}:
            payload["room_status"] = room["status"]
            payload.update(_terminal_fields(room))
        return payload
    projected_room = project_room_for_viewer(room, player_id)
    payload = {
        "ok": True,
        "status": room["status"],
        "room_id": room["room_id"],
        "revision": room["revision"],
    }
    current = projected_room.get("current_actor")
    if isinstance(current, dict):
        payload["current_actor"] = {
            "player_id": current["player_id"],
            "name": current["display_name"],
        }
        payload["your_turn"] = current["player_id"] == player_id
        private_state = projected_room.get("private_state")
        if payload["your_turn"] and private_state:
            payload["private_state"] = private_state
    activity_state = participant.get("activity_state", "active")
    if not participant.get("active", True) or activity_state != "active":
        payload["participant_status"] = (
            activity_state if activity_state != "active" else "inactive"
        )
    if consume_events or _participant_response_due(room, player_id):
        events = _compact_events(
            read_new_room_events(room["room_id"], player_id)
        )
        if events:
            payload["events"] = events
    if room["status"] in {"finished", "archived"}:
        payload.update(_terminal_fields(projected_room))
    unlocks = filter_unlocks(room.get("achievement_unlocks", []), "ai", player_id)
    if unlocks:
        payload["unlocks"] = unlocks
    return payload


def _compact_wallet(wallet: dict) -> dict:
    return {
        "balance": wallet["balance"],
        "checked_in_today": wallet["checked_in_today"],
        "can_declare_bankruptcy": wallet["can_declare_bankruptcy"],
        "bankruptcy_active": wallet["bankruptcy_active"],
        "bankruptcy_count": wallet["bankruptcy_count"],
    }


def _mcp_chips(body: McpPlayBody) -> dict:
    op = body.op or "status"
    if op == "loans":
        return _mcp_loans(body)
    if op == "exchange":
        return _mcp_exchange(body)
    human_player_id = require(
        body.opponent_id,
        "chips 动作需要由可信上游注入绑定人类身份",
    )
    human_balance = get_wallet("human", human_player_id)["balance"]
    if op == "achievements":
        payload = {
            "ok": True,
            "status": "ok",
            "op": op,
            "achievements": compact_achievements(
                get_achievements(
                    "ai", body.player_id, bound_human_id=human_player_id
                )
            ),
        }
        notices = consume_notifications("ai", body.player_id, "achievement")
        if notices:
            payload["notices"] = notices
        return payload
    if op == "check_in":
        result = claim_daily_check_in(
            "ai", body.player_id, bound_human_ids=[human_player_id]
        )
        unlocks = filter_unlocks(result.get("unlocks", []), "ai", body.player_id)
        return {
            "ok": True,
            "status": "ok",
            "op": op,
            "claimed": result["claimed"],
            "wallet": _compact_wallet(result["wallet"]),
            "bound_human_balance": get_wallet("human", human_player_id)["balance"],
            **({"unlocks": unlocks} if unlocks else {}),
        }
    if op == "bankruptcy":
        wallet = declare_bankruptcy("ai", body.player_id)
        unlocks = filter_unlocks(wallet.pop("unlocks", []), "ai", body.player_id)
        return {
            "ok": True,
            "status": "ok",
            "op": op,
            "wallet": _compact_wallet(wallet),
            "bound_human_balance": human_balance,
            **({"unlocks": unlocks} if unlocks else {}),
        }
    wallet = get_wallet("ai", body.player_id)
    payload = {
        "ok": True,
        "status": "ok",
        "op": op,
        "wallet": _compact_wallet(wallet),
        "bound_human_balance": human_balance,
    }
    if op == "ledger":
        limit = body.limit if body.limit is not None else 5
        if limit > 10:
            raise DuelError("chips ledger limit 最大为 10")
        payload["ledger"] = [
            {
                key: item[key]
                for key in (
                    "transaction_type", "amount", "balance_after",
                    "effective_date", "reference_type", "reference_id", "created_at",
                )
            }
            for item in list_ledger("ai", body.player_id, limit=limit)
        ]
    return payload


def _mcp_exchange(body: McpPlayBody) -> dict:
    """AI-owned exchange operations; the trusted opponent is always human."""
    action = body.exchange_action or "list"
    human_id = require(
        body.opponent_id,
        "chips/exchange 需要由可信上游注入绑定人类身份",
    )
    if human_id == body.player_id:
        raise DuelError("小机与绑定人类不能使用同一个身份 ID", 403)
    if action == "catalog":
        return {
            "ok": True,
            "status": "ok",
            "op": "exchange",
            "exchange_action": "catalog",
            "exchange_rule": EXCHANGE_RULE_SUMMARY,
            "catalog": list_catalog("ai"),
        }
    if action == "list":
        limit = body.limit if body.limit is not None else 50
        if limit > 100:
            raise DuelError("chips/exchange limit 最大为 100")
        listed = list_exchange_requests(
            "ai",
            body.player_id,
            counterparty_id=human_id,
            bound_counterparty_ids={human_id},
            limit=limit,
        )
        payload = {
            "ok": True,
            "status": "ok",
            "op": "exchange",
            "exchange_action": "list",
            "exchange_rule": EXCHANGE_RULE_SUMMARY,
            **compact_exchange_lists(listed),
        }
        notices = consume_notifications("ai", body.player_id, "exchange")
        if notices:
            payload["notices"] = notices
        return payload
    key = require(body.idempotency_key, "兑换写操作需要 idempotency_key")
    if action == "create":
        exchange_request = create_exchange_request(
            "ai",
            body.player_id,
            human_id,
            item_key=require(body.item_key, "create 需要 item_key"),
            request_note=require(body.request_note, "create 需要 request_note"),
            chip_amount=require(body.chip_amount, "create 需要 chip_amount"),
            custom_title=body.custom_title,
            idempotency_key=key,
            pair_is_bound=True,
        )
    else:
        request_id = require(body.request_id, f"{action} 需要 request_id")
        if action == "confirm":
            exchange_request = confirm_exchange_request(
                request_id,
                "ai",
                body.player_id,
                idempotency_key=key,
                bound_counterparty_id=human_id,
            )
        elif action in {"reject", "withdraw"}:
            exchange_request = close_exchange_request(
                request_id,
                "ai",
                body.player_id,
                action=action,
                idempotency_key=key,
                bound_counterparty_id=human_id,
            )
        else:
            raise DuelError("未知 chips/exchange 操作")
    return {
        "ok": True,
        "status": "ok",
        "op": "exchange",
        "exchange_action": action,
        "exchange_rule": EXCHANGE_RULE_SUMMARY,
        "request": exchange_request,
        "wallet": _compact_wallet(get_wallet("ai", body.player_id)),
        "bound_human_balance": get_wallet("human", human_id)["balance"],
    }


def _mcp_loans(body: McpPlayBody) -> dict:
    """Explicit AI-owned loan surface; normal chip status stays debt-free."""
    action = body.loan_action or "list"
    human_id = body.opponent_id
    bound_ids = {human_id} if human_id else set()
    if action == "list":
        limit = body.limit if body.limit is not None else 20
        if limit > 50:
            raise DuelError("chips/loans limit 最大为 50")
        payload = {
            "ok": True, "status": "ok", "op": "loans",
            "loan_action": "list",
            "loans": list_loans(
                "ai", body.player_id,
                bound_counterparty_ids=bound_ids, limit=limit,
            ),
        }
        notices = consume_notifications("ai", body.player_id, "loan")
        if notices:
            payload["notices"] = notices
        return payload
    key = require(body.idempotency_key, "借款写操作需要 idempotency_key")
    if action == "create":
        human = require(human_id, "小机发起借款需要可信上游注入绑定人类身份")
        loan = create_loan(
            "ai", body.player_id, human,
            principal=require(body.principal, "create 需要 principal"),
            daily_rate_micro_percent=require(
                body.daily_rate_micro_percent, "create 需要 daily_rate_micro_percent"
            ),
            due_date=require(body.due_date, "create 需要 due_date"),
            interest_cap_enabled=(
                True if body.interest_cap_enabled is None else body.interest_cap_enabled
            ),
            idempotency_key=key, pair_is_bound=True,
        )
    else:
        loan_id = require(body.loan_id, f"{action} 需要 loan_id")
        if action == "accept":
            loan = accept_loan(
                loan_id, "ai", body.player_id,
                revision=require(body.loan_revision, "accept 需要 loan_revision"),
                idempotency_key=key, bound_counterparty_id=human_id,
            )
        elif action == "reject":
            loan = close_proposal(
                loan_id, "ai", body.player_id, action="reject",
                revision=require(body.loan_revision, "reject 需要 loan_revision"),
                idempotency_key=key,
            )
        elif action == "withdraw":
            loan = close_proposal(
                loan_id, "ai", body.player_id, action="withdraw",
                revision=require(body.loan_revision, "withdraw 需要 loan_revision"),
                idempotency_key=key,
            )
        elif action == "counter":
            loan = counter_loan(
                loan_id, "ai", body.player_id,
                revision=require(body.loan_revision, "counter 需要 loan_revision"),
                principal=require(body.principal, "counter 需要 principal"),
                daily_rate_micro_percent=require(
                    body.daily_rate_micro_percent, "counter 需要 daily_rate_micro_percent"
                ),
                due_date=require(body.due_date, "counter 需要 due_date"),
                interest_cap_enabled=require(
                    body.interest_cap_enabled, "counter 需要 interest_cap_enabled"
                ),
                idempotency_key=key, bound_counterparty_id=human_id,
            )
        elif action == "repay":
            loan = repay_loan(
                loan_id, "ai", body.player_id,
                amount=require(body.amount, "repay 需要 amount"),
                idempotency_key=key,
            )
        else:
            raise DuelError("未知 chips/loans 操作")
    payload = {
        "ok": True, "status": "ok", "op": "loans",
        "loan_action": action, "loan": loan,
        "wallet": _compact_wallet(get_wallet("ai", body.player_id)),
    }
    if human_id:
        payload["bound_human_balance"] = get_wallet("human", human_id)["balance"]
    return payload


def with_action_note(message: str, room: dict) -> str:
    note = room.get("action_note")
    return f"{message} {note}".strip() if note else message


def require(value, message: str):
    if value is None:
        raise DuelError(message)
    return value


def trusted_human_player(request: Request) -> str:
    player_id = request.headers.get("X-Duel-Human-Player", "").strip()
    if not player_id:
        raise DuelError("请从 toy.cedarstar.org 首页登录进入", 403)
    return player_id


def _trusted_account_avatar(value: object) -> dict | None:
    if not isinstance(value, dict) or value.get("type") != "emoji":
        return None
    avatar_value = value.get("value")
    if not isinstance(avatar_value, str) or not avatar_value:
        return None
    return {
        "type": "emoji",
        "value": avatar_value,
        "is_default": value.get("is_default") is True,
    }


def _decode_proxy_json_header(request: Request, name: str) -> object | None:
    encoded = request.headers.get(name, "").strip()
    if not encoded:
        return None
    try:
        padded = encoded + "=" * (-len(encoded) % 4)
        return json.loads(
            base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")
        )
    except (ValueError, UnicodeError, json.JSONDecodeError):
        raise DuelError("账号头像上下文无效，请从主站重新进入", 403)


def _trusted_human_avatar(request: Request) -> dict | None:
    return _trusted_account_avatar(
        _decode_proxy_json_header(request, "X-Duel-Human-Avatar")
    )


def _trusted_bound_ais(request: Request) -> list[dict[str, object]]:
    """Decode the compact identity context supplied only by the main-site proxy."""
    encoded = request.headers.get("X-Duel-Bound-Ais", "").strip()
    if not encoded:
        # Keep the already-running pre-migration proxy usable until its reviewed
        # server.py change is manually restarted; the new proxy strips these heads.
        legacy_id = request.headers.get("X-Duel-Ai-Player", "").strip()
        if not legacy_id:
            return []
        legacy_name = (
            unquote(request.headers.get("X-Duel-Ai-Name", "")).strip()
            or "你的小机"
        )
        return [{"id": legacy_id, "name": legacy_name}]
    try:
        value = _decode_proxy_json_header(request, "X-Duel-Bound-Ais")
    except DuelError:
        raise DuelError("绑定小机身份上下文无效，请从主站重新进入", 403)
    if not isinstance(value, list):
        raise DuelError("绑定小机身份上下文无效，请从主站重新进入", 403)
    machines: list[dict[str, object]] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, dict):
            continue
        machine_id = str(item.get("id", "")).strip()
        machine_name = str(item.get("name", "")).strip()
        if not machine_id or machine_id in seen:
            continue
        if len(machine_id) > 80 or len(machine_name) > 100:
            continue
        machine = {"id": machine_id, "name": machine_name or "你的小机"}
        avatar = _trusted_account_avatar(item.get("avatar"))
        if avatar is not None:
            machine["avatar"] = avatar
        machines.append(machine)
        seen.add(machine_id)
    return machines


app.include_router(create_chips_router(trusted_human_player, _trusted_bound_ais))


def _participant_response_due(room: dict, player_id: str) -> bool:
    participant = next(
        (
            item for item in room.get("participants", [])
            if item["player_id"] == player_id
        ),
        None,
    )
    return bool(
        room.get("status") in {"finished", "archived", "cancelled"}
        or participant is None
        or participant.get("join_status") == "left"
        or not participant.get("active", True)
        or participant.get("activity_state", "active") != "active"
        or room.get("current_player_id") == player_id
    )


def _heartbeat_or_delta(
    room_id: str,
    player_id: str,
    baseline_revision: int,
) -> dict:
    try:
        latest = get_room(room_id, "ai", player_id)
    except DuelError as exc:
        if exc.status_code != 404:
            raise
        latest = {
            "room_id": room_id,
            "status": "cancelled",
            "revision": baseline_revision,
        }
    if _participant_response_due(latest, player_id):
        return _move_delta_response(latest, player_id)
    return {
        "ok": True,
        "status": "still_waiting",
        "room_id": latest["room_id"],
        "revision": latest["revision"],
    }


async def wait_for_revision(
    room_id: str,
    player_id: str,
    baseline_revision: int,
    *,
    wake_on_visible_events: bool = False,
) -> dict | None:
    """Wait without holding a SQLite connection, transaction, or application lock."""
    deadline = time.monotonic() + MCP_WAIT_SECONDS
    while True:
        event = revision_events.current(room_id)
        try:
            room = get_room(room_id)
        except DuelError as exc:
            if exc.status_code == 404:
                return {
                    "room_id": room_id,
                    "status": "cancelled",
                    "revision": baseline_revision,
                }
            raise
        if (
            _participant_response_due(room, player_id)
            or (
                wake_on_visible_events
                and has_new_room_events(room_id, player_id)
            )
        ):
            return room
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None
        try:
            await asyncio.wait_for(event.wait(), timeout=remaining)
        except asyncio.TimeoutError:
            return None


@app.get("/health")
async def health():
    return {"ok": True, "service": "duel", "version": "1.0.0"}


@app.get("/", response_class=HTMLResponse)
async def index():
    return HTMLResponse(INDEX_HTML, headers={"Cache-Control": "no-store"})


@app.get("/static/styles.css", include_in_schema=False)
async def styles():
    return Response(
        STYLES_CSS,
        media_type="text/css",
        headers={"Cache-Control": "no-store"},
    )


@app.get("/static/app.js", include_in_schema=False)
async def javascript():
    return Response(
        APP_JS,
        media_type="text/javascript",
        headers={"Cache-Control": "no-store"},
    )


@app.get("/static/game_ui_registry.js", include_in_schema=False)
async def game_ui_registry_javascript():
    return Response(
        GAME_UI_REGISTRY_JS,
        media_type="text/javascript",
        headers={"Cache-Control": "no-store"},
    )


def _game_ui_asset_path(game_type: str, suffix: str) -> Path:
    valid = (
        1 <= len(game_type) <= 64
        and game_type[0].isalnum()
        and all(character.isascii() and (character.isalnum() or character in "_-")
                for character in game_type)
        and game_type == game_type.lower()
    )
    if not valid:
        raise DuelError("游戏界面资源不存在。", 404)
    renderer_root = GAME_UI_RENDERER_DIR.resolve()
    asset_path = (renderer_root / f"{game_type}.{suffix}").resolve()
    if asset_path.parent != renderer_root or not asset_path.is_file():
        raise DuelError("游戏界面资源不存在。", 404)
    return asset_path


@app.get("/static/games/{game_type}.js", include_in_schema=False)
async def game_ui_renderer_javascript(game_type: str):
    renderer_path = _game_ui_asset_path(game_type, "js")
    return Response(
        renderer_path.read_text(encoding="utf-8"),
        media_type="text/javascript",
        headers={"Cache-Control": "no-store"},
    )


@app.get("/static/games/{game_type}.css", include_in_schema=False)
async def game_ui_renderer_stylesheet(game_type: str):
    stylesheet_path = _game_ui_asset_path(game_type, "css")
    return Response(
        stylesheet_path.read_text(encoding="utf-8"),
        media_type="text/css",
        headers={"Cache-Control": "no-store"},
    )


@app.get("/api/npc-avatars/{filename}", include_in_schema=False)
async def npc_avatar(filename: str):
    try:
        path = resolve_avatar_file(filename)
    except PersonaConfigError as exc:
        raise DuelError(str(exc), 404) from exc
    try:
        # Avatar files are validated local assets. Reading here avoids delegating
        # this tiny response to a second thread-pool hop, which also keeps the
        # in-process ASGI test transport deterministic.
        content = path.read_bytes()
    except OSError as exc:
        raise DuelError("NPC 头像暂时无法读取", 404) from exc
    media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return Response(
        content,
        media_type=media_type,
        headers={
            "Cache-Control": "private, max-age=300",
            "X-Content-Type-Options": "nosniff",
        },
    )


@app.get("/api/whoami")
async def human_whoami(request: Request):
    human_player_id = request.headers.get("X-Duel-Human-Player")
    if not human_player_id:
        return {
            "ok": True,
            "bound": False,
            "message": "请从 toy.cedarstar.org 首页登录进入",
            "rooms": [],
        }
    human_name = (
        unquote(request.headers.get("X-Duel-Human-Name", "")).strip()
        or "你"
    )
    machines = _trusted_bound_ais(request)
    ai_names = {machine["id"]: machine["name"] for machine in machines}
    update_participant_display_names(
        human_player_id,
        {
            human_player_id: human_name,
            **ai_names,
        },
    )
    pending_invitations = list_human_pending_invitations(human_player_id)
    rooms = list_human_rooms(human_player_id, ai_names)
    notification_state = unread_state("human", human_player_id)
    return {
        "ok": True,
        "bound": True,
        "human_name": human_name,
        "human_avatar": _trusted_human_avatar(request),
        "machines": machines,
        "identity_label": f"{human_name} · {len(machines)} 只已绑定小机",
        "games": game_catalog(),
        "npc_provider": npc_provider_capabilities(),
        "wallet": get_wallet("human", human_player_id),
        **notification_state,
        "pending_invitations": pending_invitations,
        "rooms": rooms,
    }


@app.get("/api/notifications/unread")
async def human_unread_notifications(request: Request):
    human_player_id = trusted_human_player(request)
    return {
        "ok": True,
        **notification_inbox_state("human", human_player_id, limit=50),
    }


@app.post("/api/notifications/read")
async def human_read_notifications(
    request: Request, body: NotificationAckBody
):
    human_player_id = trusted_human_player(request)
    if body.notification_ids is not None:
        count, notification_state = ack_notification_ids_with_state(
            "human", human_player_id, body.notification_ids
        )
        return {
            "ok": True,
            "notification_ids": body.notification_ids,
            "read": count,
            **notification_state,
        }
    assert body.category is not None
    count, notification_state = ack_notifications_with_state(
        "human",
        human_player_id,
        body.category,
        reference_id=body.reference_id,
    )
    return {
        "ok": True,
        "category": body.category,
        "read": count,
        **notification_state,
    }


@app.post("/api/rooms")
async def human_create(request: Request, body: CreateRoomBody):
    trusted_human = request.headers.get("X-Duel-Human-Player")
    if not trusted_human:
        raise DuelError("请从 toy.cedarstar.org 首页登录进入", 403)
    if body.player_id != trusted_human:
        raise DuelError("人类身份与主站注入身份不一致", 403)
    selected_ais = list(body.ai_players or [])
    legacy_selected_ai = body.ai_player or body.opponent_id
    if not selected_ais and legacy_selected_ai is not None:
        selected_ais = [legacy_selected_ai]
    if not selected_ais:
        raise DuelError("开新对局需要先选择一只已绑定小机")
    machines = _trusted_bound_ais(request)
    allowed_ais = {machine["id"] for machine in machines}
    if any(selected_ai not in allowed_ais for selected_ai in selected_ais):
        raise DuelError("所选小机不在当前账号的绑定清单中", 403)
    try:
        game = get_game(body.game_type)
    except ValueError as exc:
        raise DuelError(str(exc)) from exc
    allowed_counts = game.resolved_allowed_player_counts()
    target_count = body.target_player_count or (1 + len(selected_ais))
    if allowed_counts == (2,):
        if target_count != 2 or body.fill_with_npcs:
            raise DuelError(f"{game.display_name}只支持双人绑定人机对局")
    if target_count not in allowed_counts:
        if len(allowed_counts) == 1:
            raise DuelError(f"{game.display_name}需要 {allowed_counts[0]} 名参与者")
        raise DuelError(f"{game.display_name}只允许 {', '.join(map(str, allowed_counts))} 人桌")
    if len(selected_ais) > target_count - 1:
        raise DuelError("所选小机数量超过目标桌型座位")
    npc_count = target_count - 1 - len(selected_ais)
    if npc_count and not body.fill_with_npcs:
        raise DuelError("仍有空座；请选择更多绑定小机或启用 NPC 补齐")
    if npc_count > 4:
        raise DuelError("每局最多补入 4 名 NPC")
    if npc_count and not game.supports_npcs:
        raise DuelError(f"{game.display_name}未启用 NPC 补位")
    if npc_count and not game.uses_local_npc_strategy:
        capability = npc_provider_capabilities()
        if not capability["available"]:
            raise DuelError(
                f"NPC 补位当前不可用：{capability['reason']}", 503
            )
    personas = []
    if npc_count:
        try:
            personas = select_personas(npc_count)
        except PersonaConfigError as exc:
            raise DuelError(str(exc), 503) from exc
    npc_participants = [
        {
            "player_id": f"npc:{persona.id}",
            "display_name": persona.display_name,
            "role": "ai",
            "participant_kind": "system_npc",
            "npc_persona_id": persona.id,
        }
        for persona in personas
    ]
    if body.mode == "human_first":
        first_player_id = body.player_id
    elif body.mode == "ai_first":
        first_player_id = secure_choice(selected_ais)
    else:
        first_player_id = secure_choice([
            body.player_id,
            *selected_ais,
            *(participant["player_id"] for participant in npc_participants),
        ])
    resolved_mode = (
        "human_first" if first_player_id == body.player_id else "ai_first"
    )
    room = create_room(
        body.game_type,
        resolved_mode,
        "human",
        body.player_id,
        opponent_id=selected_ais[0],
        ordered_participants=[
            {"player_id": body.player_id, "role": "human"},
            *(
                {
                    "player_id": selected_ai,
                    "role": "ai",
                    "participant_kind": "bound_machine",
                }
                for selected_ai in selected_ais
            ),
            *npc_participants,
        ],
        participant_names={
            body.player_id: (
                unquote(request.headers.get("X-Duel-Human-Name", "")).strip()
                or body.player_id
            ),
            **{machine["id"]: machine["name"] for machine in machines},
        },
        stake=body.stake,
        enforce_trusted_pair=True,
        first_player_id=first_player_id,
        rematch_of_room_id=body.rematch_of_room_id,
    )
    await _schedule_current_system_npc(room)
    opener = room.get("current_actor") or {}
    opening_note = f"先手：{opener.get('display_name') or first_player_id}。"
    message = (
        f"房间 {room['room_id']} 已发出 {room['stake_label']} 邀请，等待对方确认。{opening_note}"
        if room["status"] == "pending"
        else f"房间 {room['room_id']} 已为绑定 AI 创建，可以开始对局。{opening_note}"
        if room["status"] == "playing"
        else f"房间 {room['room_id']} 已创建，等待 AI 加入。{opening_note}"
    )
    return human_response(room, message, trusted_human)


@app.post("/api/rooms/{room_id}/invitation")
async def human_invitation_decision(
    room_id: str, request: Request, body: InvitationDecisionBody
):
    human_player_id = trusted_human_player(request)
    result = respond_to_invitation(
        room_id, "human", human_player_id, body.decision
    )
    revision_events.notify(room_id)
    if result["status"] == "cancelled":
        return {
            "ok": True,
            "status": "cancelled",
            "message": "已拒绝邀请，该局已取消。",
            "room_id": room_id,
        }
    await _schedule_current_system_npc(result)
    return human_response(
        result,
        "已接受邀请，对局现在开始。"
        if result["status"] == "playing"
        else "已接受邀请，仍在等待其他参与者确认。",
        human_player_id,
    )


@app.post("/api/rooms/{room_id}/join")
async def human_join(room_id: str, body: JoinRoomBody):
    room = join_room(
        room_id,
        "human",
        body.player_id,
        opponent_id=body.opponent_id,
        message=body.message,
    )
    revision_events.notify(room["room_id"])
    await _schedule_current_system_npc(room)
    message = "已加入房间。" if room["status"] == "playing" else "已占据人类席位，等待 AI。"
    return human_response(room, message, body.player_id)


@app.get("/api/rooms/{room_id}")
async def human_state(
    room_id: str,
    request: Request,
    player_id: str | None = Query(default=None, min_length=1, max_length=80),
    opponent_id: str | None = Query(default=None, min_length=1, max_length=80),
    wait: bool = Query(default=False),
):
    canonical_viewer = trusted_human_player(request)
    if player_id is not None and player_id != canonical_viewer:
        raise DuelError("viewer 必须是主站认证的人类身份", 403)
    player_id = canonical_viewer
    room = get_room(
        room_id, "human", player_id, opponent_id=opponent_id
    )
    await _schedule_current_system_npc(room)
    if (
        wait
        and room.get("current_player_id") != player_id
        and not has_new_room_events(room_id, player_id)
    ):
        if not await revision_events.try_acquire_wait_slot():
            payload = human_response(
                room, "当前等待容量已满，已立即返回最新局面。", player_id
            )
            payload["wait_downgraded"] = True
            return payload
        try:
            changed = await wait_for_revision(
                room_id,
                player_id,
                room["revision"],
                wake_on_visible_events=True,
            )
        finally:
            await revision_events.release_wait_slot()
        if changed is None:
            return human_response(
                get_room(room_id, "human", player_id, opponent_id=opponent_id),
                "等待结束，当前仍未轮到你，也没有新的可见事件。",
                player_id,
                status="still_waiting",
            )
        room = changed
    return human_response(room, "已读取最新局面。", player_id)


@app.post("/api/rooms/{room_id}/move")
async def human_move(room_id: str, request: Request, body: MoveBody):
    move = body.move
    if move is None:
        move = {
            key: value
            for key, value in {
                "row": body.row,
                "col": body.col,
                "orientation": body.orientation,
                "from_row": body.from_row,
                "from_col": body.from_col,
                "to_row": body.to_row,
                "to_col": body.to_col,
            }.items()
            if value is not None
        }
    require(move, "move 动作需要 move 对象或对应坐标字段")
    if move == {"action": "acknowledge_round"}:
        human_player_id = trusted_human_player(request)
        if body.player_id != human_player_id:
            raise DuelError("player_id 与主站认证的人类身份不匹配", 403)
        room = acknowledge_liars_dice_round(
            room_id,
            human_player_id,
            body.revision,
        )
        success_message = "已确认本轮结算，下一轮现在开始。"
        viewer_player_id = human_player_id
    else:
        room = play_move(
            room_id,
            "human",
            body.player_id,
            move,
            opponent_id=body.opponent_id,
            message=body.message,
            expected_revision=body.revision,
        )
        success_message = "人类落子成功，已通知等待中的 AI。"
        viewer_player_id = body.player_id
    revision_events.notify(room["room_id"])
    await _schedule_current_system_npc(room)
    return human_response(
        room, with_action_note(success_message, room),
        viewer_player_id,
    )


@app.post("/api/rooms/{room_id}/messages")
async def human_message(room_id: str, body: MessageBody):
    room = post_message(
        room_id,
        "human",
        body.player_id,
        body.message,
        opponent_id=body.opponent_id,
    )
    revision_events.notify(room["room_id"])
    await _schedule_current_system_npc(room)
    return human_response(
        room, "留言已发送，并已通知等待中的参与者。", body.player_id
    )


@app.post("/api/rooms/{room_id}/resign")
async def human_resign(room_id: str, body: ResignBody):
    room = resign(
        room_id,
        "human",
        body.player_id,
        opponent_id=body.opponent_id,
        message=body.message,
    )
    revision_events.notify(room["room_id"])
    await _schedule_current_system_npc(room)
    return human_response(room, "人类已认输。", body.player_id)


@app.post("/api/rooms/{room_id}/leave")
async def human_leave(
    room_id: str, request: Request, body: LeaveRoomBody
):
    human_player_id = trusted_human_player(request)
    room = leave_room(
        room_id, "human", human_player_id, message=body.message
    )
    revision_events.notify(room_id)
    if room["status"] == "cancelled":
        return {
            "ok": True,
            "status": "cancelled",
            "message": "已离开，未开始的房间已取消。",
            "room_id": room_id,
        }
    await _schedule_current_system_npc(room)
    return {
        "ok": True,
        "status": "left",
        "message": "已离开房间。",
        "room_id": room_id,
        "room_status": room["status"],
        "revision": room["revision"],
    }


@app.post("/api/rooms/{room_id}/retention")
async def human_set_room_retention(
    room_id: str, request: Request, body: RoomRetentionBody
):
    human_player_id = trusted_human_player(request)
    room = set_room_preserved(room_id, human_player_id, body.preserved)
    message = (
        "已保留此对局，不再自动删除。"
        if room["preserved"]
        else "已取消保留，此对局已恢复自动删除。"
    )
    return human_response(room, message, human_player_id)


@app.post("/api/rooms/{room_id}/delete")
async def human_delete_room(
    room_id: str, request: Request, _body: RoomDeleteBody
):
    human_player_id = trusted_human_player(request)
    deleted_room_id = delete_terminal_room(room_id, human_player_id)
    revision_events.notify(deleted_room_id)
    return {
        "ok": True,
        "status": "ok",
        "message": "对局及其棋谱、聊天记录已删除。",
        "room_id": deleted_room_id,
    }


async def _mcp_play_impl(body: McpPlayBody):
    """MCP-friendly JSON action endpoint for the bound AI."""
    if body.player_id.startswith("npc:"):
        raise DuelError("system NPC 不是可认证账号，不能通过 MCP 冒充", 403)
    if body.full_state and body.action != "state":
        raise DuelError("full_state 只适用于 state 动作")
    if body.action == "rooms":
        room_limit = body.limit if body.limit is not None else 50
        rooms = list_ai_rooms(
            body.player_id,
            include_terminal=body.include_terminal,
            limit=room_limit,
            offset=body.offset,
        )
        payload = {
            "ok": True,
            "status": "ok",
            "message": f"找到 {len(rooms)} 个该 AI 已参与或已被邀请的房间。",
            "rooms": rooms,
            "pagination": {
                "include_terminal": body.include_terminal,
                "limit": room_limit,
                "offset": body.offset,
                "returned": len(rooms),
            },
        }
        notices = consume_notifications("ai", body.player_id, "game")
        if notices:
            payload["notices"] = notices
        return payload

    if body.action == "chips":
        return _mcp_chips(body)

    if body.action == "new":
        ordered_participants = None
        if body.participant_ids is not None:
            opponent_id = require(
                body.opponent_id,
                "participant_ids 结构需要由可信上游注入绑定人类身份",
            )
            if body.player_id not in body.participant_ids:
                raise DuelError("canonical AI 必须包含在 participant_ids 中")
            if opponent_id not in body.participant_ids:
                raise DuelError("绑定人类必须包含在 participant_ids 中")
            ordered_participants = [
                {
                    "player_id": participant_id,
                    "role": "human" if participant_id == opponent_id else "ai",
                }
                for participant_id in body.participant_ids
            ]
        try:
            game = get_game(require(body.game_type, "new 动作需要 game_type"))
        except ValueError as exc:
            raise DuelError(str(exc)) from exc
        base_count = len(ordered_participants) if ordered_participants else 2
        allowed_counts = game.resolved_allowed_player_counts()
        target_count = body.target_player_count or base_count
        if allowed_counts == (2,) and (
            target_count != 2 or body.fill_with_npcs
        ):
            raise DuelError(f"{game.display_name}只支持双人绑定人机对局")
        if target_count not in allowed_counts:
            if len(allowed_counts) == 1:
                raise DuelError(f"{game.display_name}需要 {allowed_counts[0]} 名参与者")
            raise DuelError(f"{game.display_name}只允许 {', '.join(map(str, allowed_counts))} 人桌")
        npc_count = target_count - base_count
        if npc_count < 0:
            raise DuelError("participant_ids 超过目标桌型人数")
        if npc_count and not body.fill_with_npcs:
            raise DuelError("仍有空座；需要启用 NPC 补齐")
        if npc_count > 4:
            raise DuelError("每局最多补入 4 名 NPC")
        if npc_count and not game.supports_npcs:
            raise DuelError(f"{game.display_name}未启用 NPC 补位")
        if npc_count and not game.uses_local_npc_strategy:
            capability = npc_provider_capabilities()
            if not capability["available"]:
                raise DuelError(
                    f"NPC 补位当前不可用：{capability['reason']}", 503
                )
        if npc_count:
            try:
                personas = select_personas(npc_count)
            except PersonaConfigError as exc:
                raise DuelError(str(exc), 503) from exc
            if ordered_participants is None:
                ordered_participants = [
                    {"player_id": body.opponent_id, "role": "human"},
                    {
                        "player_id": body.player_id,
                        "role": "ai",
                        "participant_kind": "bound_machine",
                    },
                ]
            ordered_participants.extend(
                {
                    "player_id": f"npc:{persona.id}",
                    "display_name": persona.display_name,
                    "role": "ai",
                    "participant_kind": "system_npc",
                    "npc_persona_id": persona.id,
                }
                for persona in personas
            )
        room = create_room(
            game.game_type,
            body.mode or "human_first",
            "ai",
            body.player_id,
            opponent_id=body.opponent_id,
            stake=body.stake,
            ordered_participants=ordered_participants,
            enforce_trusted_pair=True,
        )
        await _schedule_current_system_npc(room)
        if room["status"] == "playing":
            message = (
                f"已为绑定参与者创建房间 {room['room_id']}，当前轮到 {room['turn']}；"
                f"落子格式：{room['move_format']}"
            )
        elif room["status"] == "pending":
            message = (
                f"已发出 {room['stake_label']} 邀请，房间 {room['room_id']} "
                "将在对方确认后开始。"
            )
        else:
            message = (
                f"已创建房间 {room['room_id']}。请让房间内其他参与者加入；"
                f"落子时按此格式调用：{room['move_format']}"
            )
        if room["status"] == "playing":
            return _bootstrap_ai_response(room, body.player_id, message)
        return _pending_ai_response(room, body.player_id, message)

    room_id = require(body.room_id, f"{body.action} 动作需要 room_id")

    if body.action == "rematch":
        previous = get_room(room_id, "ai", body.player_id)
        if previous["status"] not in {"finished", "archived"}:
            raise DuelError("只有已正常结束的对局可以发起权威重赛", 409)
        if any(
            item.get("participant_kind") == "system_npc"
            for item in previous["participants"]
        ):
            raise DuelError("含随机 NPC 的房间暂不支持原阵容权威重赛", 409)
        humans = [
            item for item in previous["participants"]
            if item.get("participant_kind") == "human"
        ]
        if len(humans) != 1:
            raise DuelError("权威重赛需要且只允许一名绑定人类", 409)
        ordered = [
            {
                "player_id": item["player_id"],
                "display_name": item.get("display_name") or item["player_id"],
                "role": item["role"],
                "participant_kind": item.get("participant_kind"),
            }
            for item in previous["participants"]
        ]
        rematch = create_room(
            previous["game_type"],
            "ai_first" if previous["mode"] == "human_first" else "human_first",
            "ai",
            body.player_id,
            opponent_id=humans[0]["player_id"],
            ordered_participants=ordered,
            stake=previous.get("stake", 0),
            enforce_trusted_pair=True,
            rematch_of_room_id=room_id,
        )
        message = f"AI 已发起权威重赛，房间 {rematch['room_id']}。"
        return (
            _bootstrap_ai_response(rematch, body.player_id, message)
            if rematch["status"] == "playing"
            else _pending_ai_response(rematch, body.player_id, message)
        )

    if body.action in {"accept", "reject"}:
        result = respond_to_invitation(
            room_id, "ai", body.player_id, body.action
        )
        revision_events.notify(room_id)
        if result["status"] == "cancelled":
            return {
                "ok": True,
                "status": "cancelled",
                "message": "AI 已拒绝邀请，该局已取消。",
                "room_id": room_id,
            }
        await _schedule_current_system_npc(result)
        if result["status"] == "playing":
            message = f"AI 已接受 {result['stake_label']} 邀请，对局现在开始。"
            return _bootstrap_ai_response(result, body.player_id, message)
        pending_count = len(result.get("pending_for", []))
        waiting = (
            f"仍等待其他 {pending_count} 名参与者确认"
            if pending_count
            else "仍等待其他参与者确认"
        )
        message = f"AI 已接受 {result['stake_label']} 邀请，{waiting}。"
        return _pending_ai_response(result, body.player_id, message)

    if body.action == "join":
        room = join_room(
            room_id, "ai", body.player_id,
            opponent_id=body.opponent_id,
            message=body.message,
        )
        revision_events.notify(room["room_id"])
        await _schedule_current_system_npc(room)
        message = (
            f"AI 已加入，当前轮到 {room['turn']}。落子格式：{room['move_format']}"
            if room["status"] == "playing"
            else "AI 席位已就绪，等待房间内其他参与者加入。"
        )
        if room["status"] == "playing":
            return _bootstrap_ai_response(room, body.player_id, message)
        return _pending_ai_response(room, body.player_id, message)

    if body.action == "state":
        if body.message:
            post_message(room_id, "ai", body.player_id, body.message)
            revision_events.notify(room_id)
        room = get_room(room_id, "ai", body.player_id)
        await _schedule_current_system_npc(room)
        if body.full_state:
            return {
                "ok": True,
                "status": room["status"],
                "full_state": True,
                "snapshot": project_mcp_snapshot_for_viewer(
                    room, body.player_id
                ),
            }
        participant = next(
            (
                item for item in room.get("participants", [])
                if item["player_id"] == body.player_id
            ),
            None,
        )
        if (
            room["status"] == "playing"
            and participant is not None
            and participant.get("join_status") == "joined"
            and participant.get("active", True)
            and participant.get("activity_state", "active") == "active"
            and claim_mcp_bootstrap(room_id, body.player_id)
        ):
            return _bootstrap_ai_response(
                room,
                body.player_id,
                "对局现已开始；这是本房间唯一一次完整上下文。",
                claimed=True,
            )
        if body.wait and not _participant_response_due(room, body.player_id):
            if not await revision_events.try_acquire_wait_slot():
                payload = _move_delta_response(room, body.player_id)
                payload["wait_downgraded"] = True
                return payload
            baseline = room["revision"]
            try:
                changed = await wait_for_revision(room_id, body.player_id, baseline)
            finally:
                await revision_events.release_wait_slot()
            if changed is None:
                return _heartbeat_or_delta(
                    room_id, body.player_id, baseline
                )
            return _move_delta_response(changed, body.player_id)
        return _move_delta_response(room, body.player_id)

    if body.action == "leave":
        room = leave_room(
            room_id, "ai", body.player_id,
            opponent_id=body.opponent_id, message=body.message,
        )
        revision_events.notify(room_id)
        if room["status"] == "cancelled":
            return {
                "ok": True,
                "status": "cancelled",
                "message": "AI 已离开，未开始的房间已取消。",
                "room_id": room_id,
            }
        await _schedule_current_system_npc(room)
        payload = {
            "ok": True,
            "status": "left",
            "message": "AI 已离开房间。",
            "room_id": room_id,
            "revision": room["revision"],
            "room_status": room["status"],
            "current_actor_id": room.get("current_player_id"),
            "current_actor_seat": room.get("current_actor_seat"),
            "your_action": "leave",
        }
        if room["status"] in {"finished", "archived"}:
            payload.update(_terminal_fields(room))
        return payload

    if body.action == "resign":
        room = resign(
            room_id, "ai", body.player_id, message=body.message
        )
        revision_events.notify(room["room_id"])
        await _schedule_current_system_npc(room)
        return _move_delta_response(room, body.player_id)

    move = require(body.move, "move 动作需要 move 对象")
    room = play_move(
        room_id, "ai", body.player_id, move, message=body.message,
        expected_revision=body.revision,
    )
    revision_events.notify(room["room_id"])
    await _schedule_current_system_npc(room)
    immediate_events = bool(
        get_game(room["game_type"]).mcp_immediate_public_events
        and has_new_room_events(room["room_id"], body.player_id)
    )
    if (
        not body.wait
        or _participant_response_due(room, body.player_id)
        or immediate_events
    ):
        return _move_delta_response(
            room, body.player_id, consume_events=immediate_events
        )

    if not await revision_events.try_acquire_wait_slot():
        downgraded = _move_delta_response(
            room, body.player_id, consume_events=immediate_events
        )
        downgraded["wait_downgraded"] = True
        return downgraded

    baseline = room["revision"]
    try:
        changed = await wait_for_revision(room["room_id"], body.player_id, baseline)
    finally:
        await revision_events.release_wait_slot()
    if changed is None:
        return _heartbeat_or_delta(
            room["room_id"], body.player_id, baseline
        )
    return _move_delta_response(changed, body.player_id)


def _ack_mcp_response_notifications(payload: dict, body: McpPlayBody) -> None:
    """Avoid a second red dot for events explicitly delivered in this response."""
    unlocks = payload.get("unlocks") or []
    ack_explicit_achievement_unlocks("ai", body.player_id, unlocks)
    room_status = payload.get("room_status")
    if isinstance(payload.get("room"), dict):
        room_status = payload["room"].get("status", room_status)
    if payload.get("status") in {"finished", "archived"}:
        room_status = payload["status"]
    room_id = payload.get("room_id")
    if room_id is None and isinstance(payload.get("room"), dict):
        room_id = payload["room"].get("room_id")
    if room_status in {"finished", "archived"} and room_id:
        ack_notifications("ai", body.player_id, "game", reference_id=room_id)


@app.post("/mcp/play")
async def mcp_play(body: McpPlayBody):
    payload = await _mcp_play_impl(body)
    _ack_mcp_response_notifications(payload, body)
    return attach_mcp_unread(payload, body.player_id)

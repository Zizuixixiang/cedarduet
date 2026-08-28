import asyncio
import base64
import json
import time
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import unquote

from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response

from .database import init_db
from .framework import (
    DuelError,
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
from .games import game_catalog, get_game
from .models import (
    CreateRoomBody,
    InvitationDecisionBody,
    JoinRoomBody,
    LeaveRoomBody,
    McpPlayBody,
    MessageBody,
    MoveBody,
    ResignBody,
    RoomDeleteBody,
    RoomRetentionBody,
)
from .npc_personas import PersonaConfigError, resolve_avatar_file, select_personas
from .npc_providers import npc_provider_capabilities

ROOT = Path(__file__).resolve().parent
INDEX_HTML = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
STYLES_CSS = (ROOT / "static" / "styles.css").read_text(encoding="utf-8")
APP_JS = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
MAX_WAIT_SECONDS = 50.0
MAX_CONCURRENT_WAITS = 20


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


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    yield


app = FastAPI(
    title="Duel — Human vs AI",
    version="0.9.0",
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


def response(
    room: dict,
    message: str,
    status: str = "ok",
    *,
    timeline: bool = False,
    new_messages: list[dict] | None = None,
) -> dict:
    payload = {"ok": True, "status": status, "message": message, "room": room}
    if timeline:
        payload["timeline"] = list_timeline(room["room_id"])
    if new_messages is not None:
        payload["new_messages"] = new_messages
    return payload


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
    # Web receives its projected shared timeline while its independent cursor is
    # advanced so long-poll visibility checks remain correct.
    read_new_room_events(room["room_id"], viewer_player_id)
    payload["timeline"] = list_timeline(
        room["room_id"], viewer_player_id=viewer_player_id
    )
    return payload


def ai_response(
    room: dict, message: str, player_id: str, status: str = "ok"
) -> dict:
    participant = next(
        (
            item for item in room.get("participants", [])
            if item["player_id"] == player_id
        ),
        None,
    )
    if participant is not None and participant.get("join_status") == "left":
        return {
            "ok": True,
            "status": "left",
            "message": "当前参与者已离开房间。",
            "room_id": room["room_id"],
        }
    projected_room = project_room_for_viewer(room, player_id)
    return response(
        projected_room, message, status,
        new_messages=read_new_room_events(room["room_id"], player_id),
    )


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
        item = {
            "sequence": event["sequence"],
            "event_type": event["event_type"],
            "actor": event["sender_role"],
            "actor_id": event["sender"]["player_id"],
            "revision": event["revision_at_send"],
        }
        if event["sender"].get("seat") is not None:
            item["actor_seat"] = event["sender"]["seat"]
        if event["sender"].get("participant_kind") is not None:
            item["actor_kind"] = event["sender"]["participant_kind"]
        if event.get("move") is not None:
            item["move"] = event["move"]
        if event.get("text"):
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
    return payload


def _bootstrap_ai_response(room: dict, player_id: str, message: str) -> dict:
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
        payload["new_messages"] = events
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
    your_move: dict | None = None,
    your_action: str | None = None,
    message: str | None = None,
) -> dict:
    projected_room = project_room_for_viewer(room, player_id)
    payload = {
        "ok": True,
        "status": room["status"],
        "room_id": room["room_id"],
        "revision": room["revision"],
        "turn": projected_room["turn"],
        "current_actor_id": projected_room.get("current_player_id"),
        "current_actor_seat": projected_room.get("current_actor_seat"),
    }
    if message:
        payload["message"] = message
    if your_move is not None:
        payload["your_move"] = your_move
    if your_action is not None:
        payload["your_action"] = your_action
    if projected_room.get("action_note"):
        payload["action_note"] = projected_room["action_note"]
    events = _compact_events(read_new_room_events(room["room_id"], player_id))
    if events:
        payload["new_messages"] = events
    if room["status"] in {"finished", "archived"}:
        payload.update(_terminal_fields(projected_room))
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
    human_player_id = require(
        body.opponent_id,
        "chips 动作需要由可信上游注入绑定人类身份",
    )
    op = body.op or "status"
    human_balance = get_wallet("human", human_player_id)["balance"]
    if op == "check_in":
        result = claim_daily_check_in("ai", body.player_id)
        return {
            "ok": True,
            "status": "ok",
            "op": op,
            "claimed": result["claimed"],
            "wallet": _compact_wallet(result["wallet"]),
            "bound_human_balance": human_balance,
        }
    if op == "bankruptcy":
        wallet = declare_bankruptcy("ai", body.player_id)
        return {
            "ok": True,
            "status": "ok",
            "op": op,
            "wallet": _compact_wallet(wallet),
            "bound_human_balance": human_balance,
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


def _trusted_bound_ais(request: Request) -> list[dict[str, str]]:
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
        padded = encoded + "=" * (-len(encoded) % 4)
        value = json.loads(
            base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")
        )
    except (ValueError, UnicodeError, json.JSONDecodeError):
        raise DuelError("绑定小机身份上下文无效，请从主站重新进入", 403)
    if not isinstance(value, list):
        raise DuelError("绑定小机身份上下文无效，请从主站重新进入", 403)
    machines: list[dict[str, str]] = []
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
        machines.append({"id": machine_id, "name": machine_name or "你的小机"})
        seen.add(machine_id)
    return machines


app.include_router(create_chips_router(trusted_human_player, _trusted_bound_ais))


async def wait_for_revision(
    room_id: str, player_id: str, _baseline_revision: int
) -> dict | None:
    """Wait without holding a SQLite connection, transaction, or application lock."""
    deadline = time.monotonic() + MAX_WAIT_SECONDS
    while True:
        event = revision_events.current(room_id)
        room = get_room(room_id)
        participant = next(
            (
                item for item in room.get("participants", [])
                if item["player_id"] == player_id
            ),
            None,
        )
        if (
            room["status"] in {"finished", "archived"}
            or participant is None
            or participant.get("join_status") == "left"
            or room.get("current_player_id") == player_id
            or has_new_room_events(room_id, player_id)
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
    return {"ok": True, "service": "duel", "version": "0.9.0"}


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


@app.get("/api/npc-avatars/{filename}", include_in_schema=False)
async def npc_avatar(filename: str):
    try:
        path = resolve_avatar_file(filename)
    except PersonaConfigError as exc:
        raise DuelError(str(exc), 404) from exc
    return FileResponse(
        path,
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
    return {
        "ok": True,
        "bound": True,
        "human_name": human_name,
        "machines": machines,
        "identity_label": f"{human_name} · {len(machines)} 只已绑定小机",
        "games": game_catalog(),
        "npc_provider": npc_provider_capabilities(),
        "wallet": get_wallet("human", human_player_id),
        "pending_invitations": list_human_pending_invitations(human_player_id),
        "rooms": list_human_rooms(human_player_id, ai_names),
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
    if npc_count:
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
    room = create_room(
        body.game_type,
        body.mode,
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
    )
    message = (
        f"房间 {room['room_id']} 已发出 {room['stake_label']} 邀请，等待对方确认。"
        if room["status"] == "pending"
        else f"房间 {room['room_id']} 已为绑定 AI 创建，可以开始对局。"
        if room["status"] == "playing"
        else f"房间 {room['room_id']} 已创建，等待 AI 加入。"
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
                room_id, player_id, room["revision"]
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
async def human_move(room_id: str, body: MoveBody):
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
    room = play_move(
        room_id,
        "human",
        body.player_id,
        move,
        opponent_id=body.opponent_id,
        message=body.message,
    )
    revision_events.notify(room["room_id"])
    return human_response(
        room, with_action_note("人类落子成功，已通知等待中的 AI。", room),
        body.player_id,
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
        else "已取消保留，此对局将在终局 7 天后自动删除。"
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


@app.post("/mcp/play")
async def mcp_play(body: McpPlayBody):
    """MCP-friendly JSON action endpoint for the bound AI."""
    if body.player_id.startswith("npc:"):
        raise DuelError("system NPC 不是可认证账号，不能通过 MCP 冒充", 403)
    if body.action == "rooms":
        room_limit = body.limit if body.limit is not None else 50
        rooms = list_ai_rooms(
            body.player_id,
            include_terminal=body.include_terminal,
            limit=room_limit,
            offset=body.offset,
        )
        return {
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
        if npc_count:
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
        message = f"AI 已接受 {result['stake_label']} 邀请，对局现在开始。"
        if result["status"] == "playing":
            return _bootstrap_ai_response(result, body.player_id, message)
        return _pending_ai_response(result, body.player_id, message)

    if body.action == "join":
        room = join_room(
            room_id, "ai", body.player_id,
            opponent_id=body.opponent_id,
            message=body.message,
        )
        revision_events.notify(room["room_id"])
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
        if body.wait and room.get("current_player_id") != body.player_id:
            if has_new_room_events(room_id, body.player_id):
                return ai_response(
                    room, "发现了对当前参与者可见的新事件。", body.player_id
                )
            if not await revision_events.try_acquire_wait_slot():
                payload = ai_response(
                    room,
                    "当前已有 20 个挂起等待，请求已按 wait=false 返回。",
                    body.player_id,
                )
                payload["wait_downgraded"] = True
                return payload
            baseline = room["revision"]
            try:
                changed = await wait_for_revision(room_id, body.player_id, baseline)
            finally:
                await revision_events.release_wait_slot()
            if changed is None:
                latest = get_room(room_id, "ai", body.player_id)
                return {
                    "ok": True,
                    "status": "still_waiting",
                    "room_id": latest["room_id"],
                    "revision": latest["revision"],
                    "turn": latest["turn"],
                    "current_actor_id": latest.get("current_player_id"),
                    "current_actor_seat": latest.get("current_actor_seat"),
                }
            return ai_response(
                changed,
                "轮到当前参与者，或出现了对其可见的新事件。",
                body.player_id,
            )
        return ai_response(
            room,
            f"当前 revision={room['revision']}，轮到 {room['turn']}。",
            body.player_id,
        )

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
        return _move_delta_response(
            room,
            body.player_id,
            your_action="resign",
            message="AI 已认输，对局结束。",
        )

    move = require(body.move, "move 动作需要 move 对象")
    room = play_move(
        room_id, "ai", body.player_id, move, message=body.message
    )
    revision_events.notify(room["room_id"])
    if (
        not body.wait
        or room["status"] == "finished"
        or room.get("current_player_id") == body.player_id
    ):
        immediate_message = (
            "AI 落子成功；已返回本手增量。"
            if room["status"] != "finished"
            else "AI 落子成功，对局已经结束。"
        )
        if (
            room.get("current_player_id") == body.player_id
            and room["status"] == "playing"
        ):
            immediate_message = "AI 落子成功且行动权保留，请继续落子。"
        return _move_delta_response(
            room,
            body.player_id,
            your_move=move,
            message=immediate_message,
        )

    if not await revision_events.try_acquire_wait_slot():
        downgraded = _move_delta_response(
            room,
            body.player_id,
            your_move=move,
            message="AI 落子成功；当前已有 20 个挂起等待，请求已按 wait=false 立即返回。",
        )
        downgraded["wait_downgraded"] = True
        return downgraded

    baseline = room["revision"]
    try:
        changed = await wait_for_revision(room["room_id"], body.player_id, baseline)
    finally:
        await revision_events.release_wait_slot()
    if changed is None:
        latest = get_room(room["room_id"], "ai", body.player_id)
        return {
            "ok": True,
            "status": "still_waiting",
            "room_id": latest["room_id"],
            "revision": latest["revision"],
            "turn": latest["turn"],
            "current_actor_id": latest.get("current_player_id"),
            "current_actor_seat": latest.get("current_actor_seat"),
        }
    return _move_delta_response(
        changed,
        body.player_id,
        your_move=move,
        message=(
            f"房间内其他参与者已行动，revision {baseline}->{changed['revision']}。"
        ),
    )

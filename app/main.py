import asyncio
import base64
import json
import time
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import unquote

from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, Response

from .database import init_db
from .framework import (
    DuelError,
    create_room,
    get_room,
    join_room,
    list_human_rooms,
    list_timeline,
    play_move,
    post_message,
    read_new_human_messages,
    resign,
)
from .games import game_catalog, get_game
from .models import (
    CreateRoomBody,
    JoinRoomBody,
    McpPlayBody,
    MessageBody,
    MoveBody,
    ResignBody,
)

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
    version="0.5.1",
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


def human_response(room: dict, message: str, status: str = "ok") -> dict:
    return response(room, message, status, timeline=True)


def ai_response(
    room: dict, message: str, player_id: str, status: str = "ok"
) -> dict:
    return response(
        room,
        message,
        status,
        new_messages=read_new_human_messages(room["room_id"], player_id),
    )


def with_action_note(message: str, room: dict) -> str:
    note = room.get("action_note")
    return f"{message} {note}".strip() if note else message


def require(value, message: str):
    if value is None:
        raise DuelError(message)
    return value


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


async def wait_for_revision(
    room_id: str, player_id: str, baseline_revision: int
) -> dict | None:
    """Wait without holding a SQLite connection, transaction, or application lock."""
    deadline = time.monotonic() + MAX_WAIT_SECONDS
    while True:
        event = revision_events.current(room_id)
        room = get_room(room_id, "ai", player_id)
        if room["revision"] > baseline_revision:
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
    return {"ok": True, "service": "duel", "version": "0.5.1"}


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
    return {
        "ok": True,
        "bound": True,
        "human_name": human_name,
        "machines": machines,
        "identity_label": f"{human_name} · {len(machines)} 只已绑定小机",
        "games": game_catalog(),
        "rooms": list_human_rooms(human_player_id, ai_names),
    }


@app.post("/api/rooms")
async def human_create(request: Request, body: CreateRoomBody):
    trusted_human = request.headers.get("X-Duel-Human-Player")
    if not trusted_human:
        raise DuelError("请从 toy.cedarstar.org 首页登录进入", 403)
    if body.player_id != trusted_human:
        raise DuelError("人类身份与主站注入身份不一致", 403)
    selected_ai = body.ai_player or body.opponent_id
    if selected_ai is None:
        raise DuelError("开新对局需要先选择一只已绑定小机")
    allowed_ais = {machine["id"] for machine in _trusted_bound_ais(request)}
    if selected_ai not in allowed_ais:
        raise DuelError("所选小机不在当前账号的绑定清单中", 403)
    try:
        game = get_game(body.game_type)
    except ValueError as exc:
        raise DuelError(str(exc)) from exc
    participant_count = 2
    if not game.min_players <= participant_count <= game.max_players:
        requirement = (
            str(game.min_players)
            if game.min_players == game.max_players
            else f"{game.min_players}–{game.max_players}"
        )
        raise DuelError(
            f"{game.display_name} 需要 {requirement} 名参与者"
        )
    room = create_room(
        body.game_type,
        body.mode,
        "human",
        body.player_id,
        opponent_id=selected_ai,
    )
    message = (
        f"房间 {room['room_id']} 已为绑定 AI 创建，可以开始对局。"
        if room["status"] == "playing"
        else f"房间 {room['room_id']} 已创建，等待 AI 加入。"
    )
    return human_response(room, message)


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
    return human_response(room, message)


@app.get("/api/rooms/{room_id}")
async def human_state(
    room_id: str,
    player_id: str = Query(min_length=1, max_length=80),
    opponent_id: str | None = Query(default=None, min_length=1, max_length=80),
):
    room = get_room(
        room_id, "human", player_id, opponent_id=opponent_id
    )
    return human_response(room, "已读取最新局面。")


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
        room, with_action_note("人类落子成功，已通知等待中的 AI。", room)
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
    # 独立留言只暂存，不能把它伪装成一次人类落子来唤醒 AI。
    return human_response(room, "留言已暂存，将随 AI 下一次返回送达。")


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
    return human_response(room, "人类已认输。")


@app.post("/mcp/play")
async def mcp_play(body: McpPlayBody):
    """MCP-friendly JSON action endpoint for the bound AI."""
    if body.action == "new":
        room = create_room(
            require(body.game_type, "new 动作需要 game_type"),
            body.mode or "human_first",
            "ai",
            body.player_id,
            opponent_id=body.opponent_id,
        )
        if room["status"] == "playing":
            message = (
                f"已为绑定人类创建房间 {room['room_id']}，当前轮到 {room['turn']}；"
                f"落子格式：{room['move_format']}"
            )
        else:
            message = (
                f"已创建房间 {room['room_id']}。请把房间号交给人类加入；"
                f"落子时按此格式调用：{room['move_format']}"
            )
        return ai_response(
            room,
            message,
            body.player_id,
        )

    room_id = require(body.room_id, f"{body.action} 动作需要 room_id")

    if body.action == "join":
        room = join_room(
            room_id, "ai", body.player_id, message=body.message
        )
        revision_events.notify(room["room_id"])
        message = (
            f"AI 已加入，当前轮到 {room['turn']}。落子格式：{room['move_format']}"
            if room["status"] == "playing"
            else "AI 席位已就绪，等待人类加入。"
        )
        return ai_response(room, message, body.player_id)

    if body.action == "state":
        if body.message:
            post_message(room_id, "ai", body.player_id, body.message)
        room = get_room(room_id, "ai", body.player_id)
        return ai_response(
            room,
            f"当前 revision={room['revision']}，轮到 {room['turn']}。",
            body.player_id,
        )

    if body.action == "resign":
        room = resign(
            room_id, "ai", body.player_id, message=body.message
        )
        revision_events.notify(room["room_id"])
        return ai_response(room, "AI 已认输，对局结束。", body.player_id)

    move = require(body.move, "move 动作需要 move 对象")
    room = play_move(
        room_id, "ai", body.player_id, move, message=body.message
    )
    revision_events.notify(room["room_id"])
    if (
        not body.wait
        or room["status"] == "finished"
        or room["turn"] == "ai"
    ):
        immediate_message = (
            "AI 落子成功；已立即返回当前局面。"
            if room["status"] != "finished"
            else "AI 落子成功，对局已经结束。"
        )
        if room["turn"] == "ai" and room["status"] == "playing":
            immediate_message = "AI 落子成功且行动权保留，请继续落子。"
        return ai_response(
            room,
            with_action_note(immediate_message, room),
            body.player_id,
        )

    if not await revision_events.try_acquire_wait_slot():
        downgraded = ai_response(
            room,
            "AI 落子成功；当前已有 20 个挂起等待，请求已按 wait=false 立即返回。",
            body.player_id,
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
        return ai_response(
            latest,
            "等待 50 秒仍未收到对方落子；请使用 state 查看，或在下一次 move 后继续 wait=true。",
            body.player_id,
            status="still_waiting",
        )
    return ai_response(
        changed,
        with_action_note(
            f"对方已行动，局面从 revision={baseline} 更新到 revision={changed['revision']}。",
            changed,
        ),
        body.player_id,
    )

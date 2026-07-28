import asyncio
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, Response

from .database import init_db
from .framework import (
    DuelError,
    create_room,
    get_room,
    join_room,
    play_move,
    resign,
)
from .models import (
    CreateRoomBody,
    JoinRoomBody,
    McpPlayBody,
    MoveBody,
    ResignBody,
)

ROOT = Path(__file__).resolve().parent
INDEX_HTML = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
STYLES_CSS = (ROOT / "static" / "styles.css").read_text(encoding="utf-8")
APP_JS = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
MAX_WAIT_SECONDS = 50.0


class RevisionEvents:
    """Single-process revision notification hub; SQLite remains the source of truth."""

    def __init__(self) -> None:
        self._events: dict[str, asyncio.Event] = {}

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


revision_events = RevisionEvents()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    yield


app = FastAPI(
    title="Duel — Human vs AI",
    version="0.1.0",
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


def response(room: dict, message: str, status: str = "ok") -> dict:
    return {"ok": True, "status": status, "message": message, "room": room}


def require(value, message: str):
    if value is None:
        raise DuelError(message)
    return value


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
    return {"ok": True, "service": "duel", "version": "0.1.0"}


@app.get("/", response_class=HTMLResponse)
async def index():
    return INDEX_HTML


@app.get("/static/styles.css", include_in_schema=False)
async def styles():
    return Response(STYLES_CSS, media_type="text/css")


@app.get("/static/app.js", include_in_schema=False)
async def javascript():
    return Response(APP_JS, media_type="text/javascript")


@app.post("/api/rooms")
async def human_create(body: CreateRoomBody):
    room = create_room(body.game_type, body.mode, "human", body.player_id)
    return response(room, f"房间 {room['room_id']} 已创建，等待 AI 加入。")


@app.post("/api/rooms/{room_id}/join")
async def human_join(room_id: str, body: JoinRoomBody):
    room = join_room(room_id, "human", body.player_id)
    revision_events.notify(room["room_id"])
    message = "已加入房间。" if room["status"] == "playing" else "已占据人类席位，等待 AI。"
    return response(room, message)


@app.get("/api/rooms/{room_id}")
async def human_state(
    room_id: str,
    player_id: str = Query(min_length=1, max_length=80),
):
    room = get_room(room_id, "human", player_id)
    return response(room, "已读取最新局面。")


@app.post("/api/rooms/{room_id}/move")
async def human_move(room_id: str, body: MoveBody):
    room = play_move(
        room_id, "human", body.player_id, {"row": body.row, "col": body.col}
    )
    revision_events.notify(room["room_id"])
    return response(room, "人类落子成功，已通知等待中的 AI。")


@app.post("/api/rooms/{room_id}/resign")
async def human_resign(room_id: str, body: ResignBody):
    room = resign(room_id, "human", body.player_id)
    revision_events.notify(room["room_id"])
    return response(room, "人类已认输。")


@app.post("/mcp/play")
async def mcp_play(body: McpPlayBody):
    """MCP-friendly JSON action endpoint for the bound AI."""
    if body.action == "new":
        room = create_room(
            require(body.game_type, "new 动作需要 game_type"),
            body.mode or "human_first",
            "ai",
            body.player_id,
        )
        return response(
            room,
            f"已创建房间 {room['room_id']}。请把房间号交给人类加入；"
            f"落子时按此格式调用：{room['move_format']}",
        )

    room_id = require(body.room_id, f"{body.action} 动作需要 room_id")

    if body.action == "join":
        room = join_room(room_id, "ai", body.player_id)
        revision_events.notify(room["room_id"])
        message = (
            f"AI 已加入，当前轮到 {room['turn']}。落子格式：{room['move_format']}"
            if room["status"] == "playing"
            else "AI 席位已就绪，等待人类加入。"
        )
        return response(room, message)

    if body.action == "state":
        room = get_room(room_id, "ai", body.player_id)
        return response(room, f"当前 revision={room['revision']}，轮到 {room['turn']}。")

    if body.action == "resign":
        room = resign(room_id, "ai", body.player_id)
        revision_events.notify(room["room_id"])
        return response(room, "AI 已认输，对局结束。")

    move = require(body.move, "move 动作需要 move 对象")
    room = play_move(room_id, "ai", body.player_id, move)
    revision_events.notify(room["room_id"])
    if not body.wait or room["status"] == "finished":
        return response(
            room,
            "AI 落子成功；已立即返回当前局面。"
            if room["status"] != "finished"
            else "AI 落子成功，对局已经结束。",
        )

    baseline = room["revision"]
    changed = await wait_for_revision(room["room_id"], body.player_id, baseline)
    if changed is None:
        latest = get_room(room["room_id"], "ai", body.player_id)
        return response(
            latest,
            "等待 50 秒仍未收到对方落子；请使用 state 查看，或在下一次 move 后继续 wait=true。",
            status="still_waiting",
        )
    return response(
        changed,
        f"对方已行动，局面从 revision={baseline} 更新到 revision={changed['revision']}。",
    )

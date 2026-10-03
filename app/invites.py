"""Private invitation lifecycle. All admission/start mutations are transactional."""
import json
import re
import secrets
from contextlib import closing
from .database import connect, decode_room, write_transaction
from .framework import (
    DuelError, _now, _player_id, _room_id, _new_room_id, _decorate,
    _check_global_capacity, _initialize_opening_state, _record_event,
    _assert_player, account_key, _stake, _touch_room_presence,
)
from .games import get_game
from .npc_personas import select_personas, PersonaConfigError
from .npc_providers import npc_provider_capabilities


def _game(game_type):
    try:
        return get_game(game_type)
    except ValueError as exc:
        raise DuelError(str(exc)) from exc


def require_npc(game, *, temporary=False):
    if not game.supports_npcs and not temporary:
        raise DuelError(f"{game.display_name}尚无系统 NPC 合法动作支持，请关闭超时接管并由真实玩家坐满")
    if not game.uses_local_npc_strategy:
        capability = npc_provider_capabilities()
        if not capability["available"]:
            raise DuelError(f"系统 NPC 当前不可用：{capability['reason']}", 503)


def _insert_member(conn, room_id, player_id, role, name, seat, persona=None):
    kind = "system_npc" if persona else "human" if role == "human" else "bound_machine"
    conn.execute("""INSERT INTO room_participants
        (room_id, player_id, display_name, role, participant_kind, npc_persona_id,
         seat_index, token, joined_at, join_status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'joined')""",
        (room_id, player_id, name or "玩家", role, kind, persona, seat, f"P{seat+1}", _now()))
    # Seating a bound machine/NPC is not a client heartbeat. The authenticated
    # creator/joiner is touched explicitly by the lifecycle entry point.
    conn.execute("INSERT INTO room_event_cursors (room_id, player_id, updated_at) VALUES (?, ?, ?)",
                 (room_id, player_id, "1970-01-01T00:00:00+00:00"))


def create_invite(game_type, role, player_id, *, target_player_count, stake=0,
                  timeout_takeover=None, timeout_takeover_seconds=0,
                  display_name=None, ai_players=None, trusted_bound_ais=()):
    player_id = _player_id(player_id)
    game = _game(game_type)
    if role not in {"human", "ai"} or player_id.startswith("npc:"):
        raise DuelError("邀请房需要真实认证账号", 403)
    if isinstance(target_player_count, bool) or target_player_count not in game.resolved_allowed_player_counts():
        raise DuelError("目标人数不符合该游戏规则")
    selected = [_player_id(value) for value in (ai_players or [])]
    if selected and role != "human":
        raise DuelError("小机创建邀请房只能加入自己", 403)
    household_count = 1 + len(selected)
    if household_count >= target_player_count:
        raise DuelError("邀请房至少需要 1 名外部玩家；所选自家小机超过座位上限")
    machines = {machine["id"]: machine for machine in trusted_bound_ais}
    if any(ai not in machines or ai.startswith("npc:") for ai in selected):
        raise DuelError("所选小机不在当前账号的绑定清单中", 403)
    if len({account_key(pid) for pid in [player_id, *selected]}) != household_count:
        raise DuelError("同一账号不能重复入座", 409)
    stake = _stake(stake)
    if stake > 0:
        if not game.supports_stakes:
            raise DuelError(f"{game.display_name}尚未定义筹码结算规则")
        if target_player_count != 2 and not game.supports_multiplayer_stakes:
            raise DuelError("该多人桌型尚未定义筹码结算规则，只能创建娱乐局")
    if isinstance(timeout_takeover_seconds, bool) or timeout_takeover_seconds not in {0, 90, 180}:
        raise DuelError("超时接管只能选择关闭、90 秒或 180 秒")
    # Backward compatibility: the old boolean switch meant the fixed 90s mode.
    if timeout_takeover and timeout_takeover_seconds == 0:
        timeout_takeover_seconds = 90
    if timeout_takeover_seconds:
        require_npc(game, temporary=True)
    with write_transaction() as conn:
        _check_global_capacity(conn)
        # Cross-account rooms have no bound-pair quota. Apply a per-owner cap.
        count = conn.execute("SELECT COUNT(*) FROM rooms WHERE initiator_player_id = ? AND status IN ('waiting','playing','pending')", (player_id,)).fetchone()[0]
        if count >= 10:
            raise DuelError("当前账号已有 10 个活跃房间", 409)
        room_id = _new_room_id(conn)
        now = _now()
        conn.execute("""INSERT INTO rooms
            (room_id, game_type, mode, board_state, turn, status, stake, initiator_player_id,
             created_at, updated_at, last_move_at)
            VALUES (?, ?, 'human_first', '{}', ?, 'waiting', ?, ?, ?, ?, ?)""",
            (room_id, game_type, role, stake, player_id, now, now, now))
        code = secrets.token_hex(6).upper()
        conn.execute("""INSERT INTO room_invites
            (room_id, invite_code, target_player_count, timeout_takeover,
             timeout_takeover_seconds, household_count)
            VALUES (?, ?, ?, ?, ?, ?)""",
            (room_id, code, target_player_count, int(timeout_takeover_seconds > 0),
             timeout_takeover_seconds, household_count))
        _insert_member(conn, room_id, player_id, role, display_name, 0)
        for seat, ai in enumerate(selected, start=1):
            _insert_member(conn, room_id, ai, "ai", machines[ai]["name"], seat)
        _touch_room_presence(conn, room_id, player_id)
        return _decorate(decode_room(conn.execute("SELECT * FROM rooms WHERE room_id = ?", (room_id,)).fetchone(), conn))


def _lookup(conn, code):
    code = (code or "").strip().upper()
    if not re.fullmatch(r"[A-F0-9]{12}", code):
        raise DuelError("邀请码无效或已失效", 404)
    row = conn.execute("SELECT r.* FROM rooms r JOIN room_invites i USING(room_id) WHERE i.invite_code = ? AND r.status = 'waiting'", (code,)).fetchone()
    if row is None:
        raise DuelError("邀请码无效或已失效", 404)
    return decode_room(row, conn)


def preview_invite(code):
    with closing(connect()) as conn:
        room = _lookup(conn, code)
        return {key: value for key, value in _decorate(room).items() if key in {
            "room_id", "game_type", "game_name", "target_player_count", "participant_count",
            "stake", "timeout_takeover", "timeout_takeover_seconds"}}


def join_invite(code, role, player_id, *, display_name=None):
    player_id = _player_id(player_id)
    if role not in {"human", "ai"} or player_id.startswith("npc:"):
        raise DuelError("邀请房需要真实认证账号", 403)
    with write_transaction() as conn:
        room = _lookup(conn, code)
        if any(account_key(p["player_id"]) == account_key(player_id) for p in room["participants"]):
            raise DuelError("同一账号不能重复入座", 409)
        if len(room["participants"]) >= room["target_player_count"]:
            raise DuelError("房间已满", 409)
        _insert_member(conn, room["room_id"], player_id, role, display_name, len(room["participants"]))
        _touch_room_presence(conn, room["room_id"], player_id)
        conn.execute("UPDATE rooms SET revision = revision + 1, updated_at = ? WHERE room_id = ?", (_now(), room["room_id"]))
        _record_event(conn, room["room_id"], role, player_id, room["revision"] + 1,
                      event_type="message", text="加入了邀请房。")
        return _decorate(decode_room(conn.execute("SELECT * FROM rooms WHERE room_id = ?", (room["room_id"],)).fetchone(), conn))


def start_invite(room_id, role, player_id, *, fill_with_npcs=False):
    room_id = _room_id(room_id)
    with write_transaction() as conn:
        row = conn.execute("SELECT * FROM rooms WHERE room_id = ?", (_room_id(room_id),)).fetchone()
        if row is None:
            raise DuelError("房间不存在", 404)
        room = decode_room(row, conn)
        _assert_player(room, role, player_id)
        if room.get("room_kind") != "invite" or room["status"] != "waiting":
            raise DuelError("该邀请房已开局或关闭", 409)
        if player_id != room["initiator_player_id"]:
            raise DuelError("只有房主可以开始", 403)
        members = room["participants"]
        # Before start, only join_invite can add seats; leaving closes the room.
        # The persisted initial count excludes household AIs from invitees.
        real_count = sum(p["participant_kind"] != "system_npc" and p["join_status"] == "joined" for p in members)
        if real_count <= room["household_count"]:
            raise DuelError("至少需要 1 名外部受邀玩家加入后才能开始", 409)
        missing = room["target_player_count"] - len(members)
        game = _game(room["game_type"])
        if missing:
            if not fill_with_npcs:
                raise DuelError("尚未满员，可继续等人或选择 NPC 补满并开始", 409)
            require_npc(game)
            try:
                personas = select_personas(missing)
            except PersonaConfigError as exc:
                raise DuelError(str(exc), 503) from exc
            for persona in personas:
                _insert_member(conn, room_id, f"npc:{persona.id}", "ai", persona.display_name, len(members), persona.id)
                members.append({"player_id": f"npc:{persona.id}"})
        room = decode_room(row, conn)
        members = room["participants"]
        secrets.SystemRandom().shuffle(members)
        # Move all old seats out of range before assigning the shuffled indices.
        conn.execute("UPDATE room_participants SET seat_index = seat_index + 100 WHERE room_id = ?", (room_id,))
        for index, member in enumerate(members):
            member.update(seat_index=index, seat=index, order=index)
        tokens = game.tokens_for(members)
        if len(tokens) != len(members) or len(set(tokens)) != len(tokens):
            raise DuelError("游戏必须为每个最终座位分配唯一 token")
        for member, token in zip(members, tokens):
            member["token"] = token
            conn.execute("UPDATE room_participants SET seat_index = ?, token = ? WHERE room_id = ? AND player_id = ?",
                         (member["seat_index"], token, room_id, member["player_id"]))
        # Shuffle identities before game initialization. Seat-zero contracts
        # (teams, colors, dealer rules) remain the plugin's responsibility.
        try:
            state, first = _initialize_opening_state(game, members, members[0]["player_id"])
        except (ValueError, KeyError, TypeError) as exc:
            raise DuelError(f"游戏插件初始化失败：{exc}") from exc
        state["marks_by_player"] = {p["player_id"]: p["token"] for p in members}
        now = _now()
        turn = next(p["role"] for p in members if p["player_id"] == first)
        conn.execute("""UPDATE rooms SET status='playing', board_state=?, current_player_id=?,
            turn=?, revision=revision+1, updated_at=?, last_move_at=? WHERE room_id=?""",
            (json.dumps(state, ensure_ascii=False), first, turn, now, now, room_id))
        conn.execute("UPDATE room_invites SET invite_code=NULL, turn_revision=?, turn_started_at=? WHERE room_id=?",
                     (room["revision"]+1, now, room_id))
        _touch_room_presence(conn, room_id, player_id)
        _record_event(conn, room_id, "system", "system", room["revision"]+1,
                      event_type="message", text="邀请房已开局，座位已随机分配。")
        return _decorate(decode_room(conn.execute("SELECT * FROM rooms WHERE room_id=?", (room_id,)).fetchone(), conn))


def reclaim(room_id, role, player_id):
    room_id = _room_id(room_id)
    from .framework import get_room
    with write_transaction() as conn:
        row = conn.execute("SELECT * FROM rooms WHERE room_id=?", (_room_id(room_id),)).fetchone()
        if row is None:
            raise DuelError("房间不存在", 404)
        room = decode_room(row, conn)
        _assert_player(room, role, player_id)
        participant = next(p for p in room["participants"] if p["player_id"] == player_id)
        if participant["participant_kind"] != "system_npc":
            _touch_room_presence(conn, room_id, player_id)
            # Protect this revision from in-flight/overdue work.
            conn.execute("""UPDATE room_invites SET reclaim_revision=?, takeover_revision=NULL
                            WHERE room_id=?""", (room["revision"], room_id))
    return get_room(room_id, role, player_id)


def has_targeted_chat(room_id, player_id):
    with closing(connect()) as conn:
        return conn.execute("""SELECT 1 FROM room_mentions m
            JOIN room_messages e ON e.id=m.event_id
            JOIN room_invites i ON i.room_id=e.room_id
            JOIN room_event_cursors c ON c.room_id=e.room_id AND c.player_id=m.player_id
            WHERE e.room_id=? AND m.player_id=? AND m.event_id>c.last_event_id LIMIT 1""",
            (room_id, player_id)).fetchone() is not None

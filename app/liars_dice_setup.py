"""Room-local pregame seats. No dice exist until the host starts the table."""
import json
import secrets
from copy import deepcopy
from datetime import datetime, timedelta, timezone


def manual_teams(options):
    return (options.get("mode") == "party"
            and options.get("team_mode") in ("teams", "pairs")
            and options.get("random_teams", True) is False)


def has_selection(room):
    return (room.get("game_type") == "liars_dice"
            and "team_selection" in room.get("board_state", {}))


def is_selecting(room):
    return room.get("status") == "waiting" and has_selection(room)


def initial_selection(options, count, first_player_id=None):
    return {**options, "team_selection": {
        "target_player_count": count,
        "start_after": (datetime.now(timezone.utc) + timedelta(seconds=20)).isoformat(),
        "seats": {"red": [None] * (count // 2), "blue": [None] * (count // 2)},
        "first_player_id": first_player_id,
    }}


def ready(selection):
    return datetime.now(timezone.utc) >= datetime.fromisoformat(selection["start_after"])


def public_selection(state):
    selection = deepcopy(state["team_selection"])
    selection.pop("first_player_id", None)
    selection.pop("deferred_npcs", None)
    selection["can_start"] = ready(selection)
    return selection


def final_teams(state, participants):
    seats = deepcopy(state["team_selection"]["seats"])
    chosen = {pid for row in seats.values() for pid in row if pid is not None}
    remaining = [p["player_id"] for p in participants if p["player_id"] not in chosen]
    secrets.SystemRandom().shuffle(remaining)
    for row in seats.values():
        for i, pid in enumerate(row):
            if pid is None:
                row[i] = remaining.pop()
    if remaining:
        raise ValueError("组队席位数量不匹配")
    return seats


def remember_profiles(room, profiles):
    """Only server-authenticated display metadata; never accepts seat assignments."""
    from . import framework as f
    from .database import write_transaction, decode_room
    if not is_selecting(room):
        return room
    with write_transaction() as conn:
        row = conn.execute("SELECT * FROM rooms WHERE room_id=?", (room['room_id'],)).fetchone()
        if row is None:
            raise f.DuelError("房间已关闭", 409)
        current = decode_room(row, conn)
        if not is_selecting(current):
            return f._decorate(current)
        avatars = current['board_state'].setdefault('participant_avatars', {})
        changed = False
        for p in current['participants']:
            profile = profiles.get(p['player_id'], {})
            avatar = profile.get('avatar')
            if (isinstance(avatar, dict) and avatar.get('type') == 'emoji'
                    and isinstance(avatar.get('value'), str) and 0 < len(avatar['value']) <= 64
                    and avatars.get(p['player_id']) != avatar):
                avatars[p['player_id']] = {'type':'emoji', 'value':avatar['value']}
                changed = True
            name = profile.get('name')
            if isinstance(name, str) and name.strip() and p['display_name'] != name.strip()[:100]:
                conn.execute('UPDATE room_participants SET display_name=? WHERE room_id=? AND player_id=?',
                             (name.strip()[:100],room['room_id'],p['player_id']))
                changed = True
        if changed:
            conn.execute('UPDATE rooms SET board_state=?, revision=revision+1 WHERE room_id=?',
                         (json.dumps(current['board_state'],ensure_ascii=False),room['room_id']))
        return f._decorate(decode_room(conn.execute('SELECT * FROM rooms WHERE room_id=?', (room['room_id'],)).fetchone(), conn))


def choose_seat(room_id, role, player_id, move, revision):
    from . import framework as f
    from .database import write_transaction, decode_room
    room_id, player_id = f._room_id(room_id), f._player_id(player_id)
    if set(move) != {"action", "team", "seat"} or move.get("action") != "choose_team_seat":
        raise f.DuelError("选座仅接受 action、team(red/blue)、seat(1起)")
    team, seat = move["team"], move["seat"]
    if team not in ("red", "blue") or isinstance(seat, bool) or not isinstance(seat, int):
        raise f.DuelError("team 须为 red/blue，seat 须为整数")
    with write_transaction() as conn:
        row = conn.execute("SELECT * FROM rooms WHERE room_id=?", (room_id,)).fetchone()
        if row is None:
            raise f.DuelError("房间不存在", 404)
        room = decode_room(row, conn)
        f._assert_player(room, role, player_id)
        actor = f._participant_by_id(room, player_id)
        if (not actor or actor.get("join_status") != "joined"
                or actor.get("participant_kind") == "system_npc"):
            raise f.DuelError("仅已加入的真实参与者可选择自己的座位", 403)
        if not is_selecting(room):
            raise f.DuelError("当前房间不在手动组队阶段", 409)
        if revision is None:
            raise f.DuelError("选座必须携带 revision", 409)
        f._assert_expected_revision(room, revision)
        state = room["board_state"]
        seats = state["team_selection"]["seats"]
        if not 1 <= seat <= len(seats[team]):
            raise f.DuelError("座位超出本队范围")
        occupant = seats[team][seat - 1]
        if occupant not in (None, player_id):
            raise f.DuelError("该座位已有人，请选其他空位", 409)
        for row in seats.values():
            for i, pid in enumerate(row):
                if pid == player_id:
                    row[i] = None
        seats[team][seat - 1] = player_id
        conn.execute("UPDATE rooms SET board_state=?, revision=revision+1, updated_at=? WHERE room_id=?",
                     (json.dumps(state, ensure_ascii=False), f._now(), room_id))
        f._touch_room_presence(conn, room_id, player_id)
        return f._decorate(decode_room(conn.execute("SELECT * FROM rooms WHERE room_id=?", (room_id,)).fetchone(), conn))


def start_regular(room_id, role, player_id, fill_with_npcs=False):
    from . import framework as f
    from .database import write_transaction, decode_room
    from .invites import _insert_member, require_npc
    from .npc_personas import select_personas, PersonaConfigError
    room_id = f._room_id(room_id)
    with write_transaction() as conn:
        row = conn.execute("SELECT * FROM rooms WHERE room_id=?", (room_id,)).fetchone()
        if row is None:
            raise f.DuelError("房间不存在", 404)
        room = decode_room(row, conn)
        f._assert_player(room, role, player_id)
        if not is_selecting(room) or room.get("room_kind") == "invite":
            raise f.DuelError("该房间没有待开始的手动组队", 409)
        if player_id != room["initiator_player_id"]:
            raise f.DuelError("只有房主可以开始", 403)
        selection = room["board_state"]["team_selection"]
        if not ready(selection):
            raise f.DuelError("组队开始约20秒后可开局", 409)
        game = f.get_game("liars_dice")
        missing = selection["target_player_count"] - len(room["participants"])
        if missing:
            if not fill_with_npcs:
                raise f.DuelError("还有空位，可选择 NPC 补满并开始", 409)
            if missing > f.MAX_NPCS_PER_ROOM:
                raise f.DuelError("每局最多补入4名NPC")
            require_npc(game)
            deferred = selection.get('deferred_npcs', [])
            if deferred:
                if len(deferred) != missing:
                    raise f.DuelError("重赛NPC数量与空位不一致", 409)
                for i, npc in enumerate(deferred, start=len(room['participants'])):
                    _insert_member(conn, room_id, npc['player_id'], 'ai', npc['display_name'], i, npc['npc_persona_id'])
            else:
                try:
                    personas = select_personas(missing)
                except PersonaConfigError as exc:
                    raise f.DuelError(str(exc), 503) from exc
                for i, persona in enumerate(personas, start=len(room["participants"])):
                    _insert_member(conn, room_id, f"npc:{persona.id}", "ai", persona.display_name, i, persona.id)
        members = decode_room(row, conn)["participants"]
        teams = final_teams(room["board_state"], members)
        first = selection["first_player_id"] or game.first_player_id(members, room["mode"])
        state, first = f._initialize_opening_state(game, members, first,
            game.saved_options(room["board_state"]), team_assignments=teams)
        state['participant_avatars'] = room['board_state'].get('participant_avatars', {})
        now = f._now()
        turn = next(p["role"] for p in members if p["player_id"] == first)
        conn.execute("""UPDATE rooms SET status='playing', board_state=?, current_player_id=?,
            turn=?, revision=revision+1, updated_at=?, last_move_at=? WHERE room_id=?""",
            (json.dumps(state, ensure_ascii=False), first, turn, now, now, room_id))
        f._touch_room_presence(conn, room_id, player_id)
        f._record_event(conn, room_id, "system", "system", room["revision"]+1,
                        event_type="message", text="组队完成，游戏开始。")
        return f._decorate(decode_room(conn.execute("SELECT * FROM rooms WHERE room_id=?", (room_id,)).fetchone(), conn))

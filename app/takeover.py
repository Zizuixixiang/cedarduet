"""Configurable per-action temporary assistance; participant identity never changes."""
import asyncio
import logging
from datetime import datetime, timezone
from .database import write_transaction
from .framework import DuelError, get_room, play_move, has_live_room_participant
from .games import get_game
from .npc_controller import _decision_request
from .npc_providers import get_npc_provider

logger = logging.getLogger(__name__)
# Actual in-flight work, not configuration or the last completed move. A restart
# drops these tasks too, so this transient UI status must not be persisted.
_active_turns = {}


def temporary_takeover_active(room, player_id):
    return bool(
        room.get("room_kind") == "invite" and is_due(room)
        and room.get("current_player_id") == player_id
        and (room["room_id"], room["revision"], player_id) in tuple(_active_turns.values())
    )


def is_due(room):
    if (room.get("status") != "playing" or not room.get("timeout_takeover")
            or room.get("reclaim_revision") == room["revision"]
            or room.get("turn_revision") != room["revision"]):
        return False
    actor = next((p for p in room["participants"] if p["player_id"] == room.get("current_player_id")), None)
    if not actor or actor["participant_kind"] == "system_npc":
        return False
    started = room.get("turn_started_at")
    timeout_seconds = int(room.get("timeout_takeover_seconds") or (90 if room.get("timeout_takeover") else 0))
    return bool(started and timeout_seconds > 0
                and (datetime.now(timezone.utc) - datetime.fromisoformat(started)).total_seconds() >= timeout_seconds
                and has_live_room_participant(room))


def timeout_legal_actions(room, actor):
    game = get_game(room["game_type"])
    state = room["board_state"]
    if game.supports_npcs:
        if room["game_type"] == "liars_dice" and state.get("pending_next_round"):
            return [{"action": "acknowledge_round"}]
        return game.npc_legal_actions(state, actor, room["participants"])
    # Legacy public-board games already expose legal moves or validators. Reuse
    # those authorities for temporary assistance without adding permanent NPCs.
    if room["game_type"] == "xiangqi":
        candidates = state.get("legal_moves", [])
    elif room["game_type"] == "jungle":
        candidates = state.get("legal_moves_by_mark", {}).get(actor["token"], [])
    elif room["game_type"] == "connect4":
        candidates = [{"col": c} for c in range(7)]
    else:
        size = len(state.get("board", []))
        candidates = [{"row": r, "col": c} for r in range(size) for c in range(size)]
    actions = []
    for candidate in candidates:
        try:
            game.validate_action(state, candidate, actor)
        except (ValueError, KeyError, TypeError):
            continue
        actions.append(candidate)
    return actions


async def run_timeout_turn(room_id, provider=None):
    room = get_room(room_id)
    if not is_due(room):
        return None
    actor = next(p for p in room["participants"] if p["player_id"] == room["current_player_id"])
    game = get_game(room["game_type"])
    actions = timeout_legal_actions(room, actor)
    if not actions:
        return None
    operation = object()
    _active_turns[operation] = (room["room_id"], room["revision"], actor["player_id"])
    try:
        if game.uses_local_npc_strategy:
            action = game.choose_local_npc_action(room["board_state"], actor, room["participants"])
            if action not in actions:
                raise DuelError("本地 NPC 未返回合法动作")
        elif len(actions) == 1:
            action = actions[0]
        else:
            request, action_map = _decision_request(room, actor["player_id"], actions, temporary=True)
            try:
                decision = await (provider or get_npc_provider()).decide(request)
                action = action_map[decision.action_id]
            except asyncio.CancelledError:
                raise
            except Exception:
                # Same authoritative fallback as the system-NPC controller.
                action = action_map[sorted(action_map)[0]]
        try:
            return play_move(room_id, actor["role"], actor["player_id"], action,
                             expected_revision=room["revision"], takeover=True,
                             message="超时，由 NPC 临时代操作。")
        except DuelError as exc:
            if exc.status_code == 409:
                return None  # A player move/reclaim won the revision race.
            raise
    finally:
        _active_turns.pop(operation, None)



class TakeoverScheduler:
    def __init__(self, on_change, schedule_npc):
        self.on_change = on_change
        self.schedule_npc = schedule_npc
        self.task = None
        self.workers = {}

    async def start(self):
        self.task = asyncio.create_task(self._run(), name="invite-timeouts")

    async def shutdown(self):
        tasks = [t for t in [self.task, *self.workers.values()] if t]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.workers.clear()
        self.task = None

    async def _one(self, room_id):
        try:
            room = await run_timeout_turn(room_id)
            if room:
                self.on_change(room_id)
                await self.schedule_npc(room)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Invitation timeout failed for %s", room_id)
        finally:
            self.workers.pop(room_id, None)

    async def _run(self):
        while True:
            # Also synchronize phase changes caused by resign/leave/NPC actions.
            with write_transaction() as conn:
                conn.execute("""UPDATE room_invites SET
                    turn_started_at=(SELECT updated_at FROM rooms WHERE rooms.room_id=room_invites.room_id),
                    turn_revision=(SELECT revision FROM rooms WHERE rooms.room_id=room_invites.room_id),
                    reclaim_revision=NULL
                    WHERE (timeout_takeover_seconds > 0 OR timeout_takeover=1) AND turn_revision !=
                        (SELECT revision FROM rooms WHERE rooms.room_id=room_invites.room_id)""")
                ids = [r[0] for r in conn.execute("""SELECT r.room_id FROM rooms r
                    JOIN room_invites i USING(room_id)
                    WHERE r.status='playing'
                      AND (i.timeout_takeover_seconds > 0 OR i.timeout_takeover=1)""")]
            for room_id in ids:
                if room_id in self.workers:
                    continue
                try:
                    room = get_room(room_id)
                except DuelError as exc:
                    if exc.status_code == 404:
                        continue  # A concurrent leave may remove the room.
                    raise
                if is_due(room):
                    self.workers[room_id] = asyncio.create_task(self._one(room_id))
            await asyncio.sleep(1)

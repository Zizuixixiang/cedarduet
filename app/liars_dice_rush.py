"""Persisted party reaction deadlines and bounded, private-information NPC reactions."""
import asyncio
import json
import logging
import random
import time
from datetime import datetime, timezone

from .games import get_game

log = logging.getLogger(__name__)


def expire_windows(conn, room_id=None):
    # A read/reconnect also advances expired windows when no worker is running.
    sql = """SELECT room_id, board_state, revision FROM rooms WHERE game_type='liars_dice'
        AND status='playing' AND json_extract(board_state, '$.rush_until') <= ?
        AND json_extract(board_state, '$.rush_until') > 0"""
    args = [time.time()]
    if room_id is not None:
        sql += ' AND room_id=?'
        args.append(room_id)
    for row in conn.execute(sql, args).fetchall():
        state = json.loads(row['board_state'])
        state.pop('rush_until', None)
        conn.execute('UPDATE rooms SET board_state=?, revision=revision+1 WHERE room_id=?',
                     (json.dumps(state, ensure_ascii=False), row['room_id']))
        # Normal-turn assistance starts after the reaction interval. Preserve a
        # player's explicit reclaim across this clock-only revision transition.
        conn.execute('''UPDATE room_invites SET turn_revision=?, turn_started_at=?,
            reclaim_revision=CASE WHEN reclaim_revision=? THEN ? ELSE reclaim_revision END
            WHERE room_id=?''', (row['revision']+1, datetime.now(timezone.utc).isoformat(),
                                row['revision'], row['revision']+1, row['room_id']))


def npc_wants_rush(game, state, actor):
    """One local decision using ONLY the actor's cup and public bid/counts.

    A deadline cannot depend on an unbounded external model request. NPCs use
    the same visible opportunity as humans, with staggered reaction times.
    """
    bid = state['current_bid']
    own = game.private_state(state, actor, [])['dice']
    wild = not bid.get('exact') and bid['face'] != 1
    known = sum(d == bid['face'] or (wild and d == 1) for d in own)
    cups = len(state['teams']) if game.shared_team_cups(state) else len(state['dice_counts'])
    expectation = known + (cups - 1) * 5 * (2 if wild else 1) / 6
    return bid['quantity'] > expectation + 1


class RushScheduler:
    def __init__(self, notify, resume):
        self.notify, self.resume = notify, resume
        self.tasks = {}
        self.started = False

    async def start(self):
        from .database import connect
        from .framework import get_room, DuelError
        self.started = True
        conn = connect()
        try:
            ids = [r[0] for r in conn.execute("SELECT room_id FROM rooms WHERE game_type='liars_dice' AND status='playing'")]
        finally:
            conn.close()
        for rid in ids:
            try:
                room = get_room(rid)
            except DuelError as exc:
                if exc.status_code == 404:
                    continue
                raise
            await self.schedule(room)
            await self.resume(room)

    async def shutdown(self):
        self.started = False
        tasks = list(self.tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.tasks.clear()

    async def schedule(self, room):
        if not self.started or room.get('game_type') != 'liars_dice' or room.get('status') != 'playing':
            return
        deadline = get_game('liars_dice').rush_deadline(room['board_state'])
        if not deadline:
            return
        key = (room['room_id'], deadline)
        if key not in self.tasks:
            self.tasks[key] = asyncio.create_task(self._run(room, key))

    async def _react(self, room, actor, deadline):
        from .framework import get_room, play_move, DuelError
        # No fixed seat gets priority. The server transaction decides the winner.
        react_at = deadline - 10 + random.SystemRandom().uniform(1.5, 8.0)
        await asyncio.sleep(max(0, react_at - time.time()))
        latest = get_room(room['room_id'])
        game = get_game('liars_dice')
        state = latest['board_state']
        if (game.rush_deadline(state) != deadline or not game.can_rush(state, actor['player_id'])
                or not npc_wants_rush(game, state, actor)):
            return
        try:
            updated = play_move(room['room_id'], 'ai', actor['player_id'], {'action':'rush_open'},
                                expected_revision=latest['revision'])
        except DuelError:  # Another transaction, departure or deadline won.
            return
        self.notify(room['room_id'])
        await self.resume(updated)

    async def _run(self, room, key):
        from .framework import get_room, DuelError
        from .npc_limits import npc_budget_exhausted
        reactions = []
        try:
            game = get_game('liars_dice')
            if not npc_budget_exhausted(room):
                reactions = [asyncio.create_task(self._react(room, actor, key[1]))
                    for actor in room['participants']
                    if actor['participant_kind'] == 'system_npc' and actor['join_status'] == 'joined'
                    and actor.get('active', True) and actor.get('activity_state', 'active') == 'active'
                    and game.can_rush(room['board_state'], actor['player_id'])]
            await asyncio.sleep(max(0, key[1] - time.time()))
            latest = get_room(room['room_id'])  # Expiry commits exactly once.
            self.notify(room['room_id'])
            await self.resume(latest)
        except DuelError:
            pass  # Room removed while waiting.
        except Exception:
            log.exception('dice reaction window failed: %s', key[0])
        finally:
            for task in reactions:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*reactions, return_exceptions=True)
            self.tasks.pop(key, None)

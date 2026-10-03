"""Run against baseline and working copy via PYTHONPATH; temporary SQLite only.

Writes raw HTTP replies and the next snapshot-selected action. Count/compare
with compare_legacy_full_state.py using cl100k_base. No live services or models.
"""
import argparse
import asyncio
import json
import random
import tempfile
from pathlib import Path
from unittest.mock import patch

import httpx
from app import database, framework, main
from app.games import GAMES
from tests.full_state_support import LEGACY, choose


async def sample(output):
    records = json.loads(Path(output).read_text()) if Path(output).exists() else []
    with tempfile.TemporaryDirectory(prefix='legacy-full-state-') as tmp:
        with patch.object(database, 'DB_PATH', Path(tmp) / 'test.db'):
            database.init_db()
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url='http://test') as client:
                for seed in (11, 37):
                    for index, name in enumerate(LEGACY):
                        if any(r['seed'] == seed and r['game'] == name for r in records):
                            continue
                        print(seed, name, flush=True)
                        game = GAMES[name]
                        if hasattr(game, '_rng'):
                            game._rng = random.Random(seed)
                        count = 4 if name == 'blackjack' else max(2, game.min_players)
                        seats = [dict(player_id=f'p{i}', role='ai', participant_kind='bound_machine', display_name=f'玩家{i}') for i in range(count)]
                        # Xiangqi explicitly requires one bound human and AI.
                        if name == 'xiangqi':
                            seats[1].update(role='human', participant_kind='human')
                        room = framework.create_room(name, 'ai_first', 'ai', 'p0',
                            ordered_participants=seats, require_confirmations=False)
                        async def call(pid, **body):
                            response = await client.post('/mcp/play', json=dict(room_id=room['room_id'], player_id=pid, **body))
                            assert response.status_code == 200, response.text
                            return response.json()
                        trace = []
                        # Bootstrap every AI viewer, then consume ordinary replies.
                        for seat in seats:
                            if seat['role'] == 'ai':
                                trace.append(await call(seat['player_id'], action='state'))
                        steps = 1 if name == 'blackjack' else 2 if name == 'tictactoe' else 3 if name == 'texas_holdem' else 10
                        for step in range(steps):
                            assert room['status'] == 'playing', (name, step)
                            pid = room['current_player_id']
                            snapshot = framework.project_mcp_snapshot_for_viewer(room, pid)
                            move = choose(snapshot, step)
                            actor = next(p for p in room['participants'] if p['player_id'] == pid)
                            if actor['role'] == 'ai':
                                trace.append(await call(pid, action='state'))
                                trace.append(await call(pid, action='move', move=move, revision=room['revision']))
                            else:
                                framework.play_move(room['room_id'], 'human', pid, move, expected_revision=room['revision'])
                            room = framework.get_room(room['room_id'])
                        pid = room['current_player_id']
                        assert pid is not None, name
                        # Drain unread text before measuring layout alone. Full-state
                        # unread-text semantics are tested separately.
                        for seat in seats:
                            framework.read_new_room_events(room['room_id'], seat['player_id'], mcp=True)
                        full = await call(pid, action='state', full_state=True)
                        next_move = choose(full['snapshot'], steps)
                        actor = next(p for p in room['participants'] if p['player_id'] == pid)
                        framework.play_move(room['room_id'], actor['role'], pid, next_move, expected_revision=room['revision'])
                        records.append(dict(seed=seed, game=name, steps=steps, full_state=full,
                                            next_move=next_move, ordinary=trace))
                        Path(output).write_text(json.dumps(records, ensure_ascii=False, indent=2) + '\n')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', required=True)
    asyncio.run(sample(parser.parse_args().out))

"""Real /mcp/play traces on disposable SQLite; no production database or services.

Run with the project's Python. --before-dir holds unmodified game modules only.
Token comparison is a separate script so tiktoken need not enter runtime deps.
"""
import argparse
import asyncio
import importlib.util
import json
import os
import random
import tempfile
from pathlib import Path
from unittest.mock import patch

import httpx
from app import database, framework, main
from app.games import GAMES
from third_party.rlcard_guandan.engine import GuandanEngine
from tests.guandan_mahjong_compact_support import seats, guandan_scenarios, mahjong_scenarios


def baseline_games(directory):
    result = {}
    for name in ('guandan', 'mahjong'):
        spec = importlib.util.spec_from_file_location('app.games._before_' + name, Path(directory) / (name + '.py'))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        result[name] = getattr(module, name.title())()
    return result


async def sample(args):
    records = []
    full_states = []
    with tempfile.TemporaryDirectory(prefix='duel-two-game-sampling-') as tmp, \
         patch.object(database, 'DB_PATH', Path(tmp) / 'sample.db'), \
         patch.object(main, 'revision_events', main.RevisionEvents()), \
         patch.dict(GAMES, baseline_games(args.before_dir) if args.before_dir else {}):
        database.init_db()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url='http://sample') as client:
            async def call(room, pid, **body):
                reply = await client.post('/mcp/play', json=dict(room_id=room['room_id'], player_id=pid, **body))
                assert reply.status_code == 200, reply.text
                return reply.json()
            async def create(name):
                room = framework.create_room(name, 'ai_first', 'ai', 'p0', ordered_participants=seats(), require_confirmations=False)
                for pid in ('p0', 'p1', 'p2', 'p3'):
                    assert (await call(room, pid, action='state'))['bootstrap']
                return room
            async def one_round(room, seed, stage, step, kind=None):
                game = GAMES[room['game_type']]
                pid = room['current_player_id']
                state = room['board_state']
                actions = (GuandanEngine.legal_actions(state, pid) if game.game_type == 'guandan'
                           else game.legal_actions_for(state, pid))
                candidates = [a for a in actions if a['kind'] == kind] if kind else actions
                assert candidates
                chosen = candidates[0] if kind else random.Random(seed * 1000 + step).choice(candidates)
                move = dict(action='act', action_id=chosen['action_id'])
                turn = await call(room, pid, action='state')
                assert 'bootstrap' not in turn and turn['your_turn'] and 'private_state' in turn
                reply = await call(room, pid, action='move', revision=room['revision'], move=move)
                records.append(dict(game=game.game_type, seed=seed, stage=stage, step=step,
                    phase=state['phase'], move=move, room_id=room['room_id'],
                    legal_action_count=len(actions), turn_state=turn, move_reply=reply))
                return framework.get_room(room['room_id'])
            for name in ('guandan', 'mahjong'):
                game = GAMES[name]
                for seed in (7, 11, 37):
                    game._rng = random.Random(seed)
                    room = await create(name)
                    for step in range(args.steps):
                        if room['status'] != 'playing':
                            break
                        room = await one_round(room, seed, 'seeded_play', step)
                        if step % 12 == 0:
                            print(name, seed, step, flush=True)
                    full_states.append(dict(game=name, seed=seed, stage='seeded_play', room_id=room['room_id'],
                        response=await call(room, room['current_player_id'] or 'p0', action='state', full_state=True)))
                game._rng = random.Random(17)
                scenarios = (guandan_scenarios if name == 'guandan' else mahjong_scenarios)(game)
                for index, (stage, state, kind) in enumerate(scenarios):
                    room = await create(name)
                    status = 'finished' if state['phase'] == 'finished' else 'playing'
                    with database.write_transaction() as conn:
                        conn.execute('UPDATE rooms SET board_state=?, current_player_id=?, status=?, revision=revision+1 WHERE room_id=?',
                            (json.dumps(state), state.get('turn_player_id'), status, room['room_id']))
                    room = framework.get_room(room['room_id'])
                    # Terminal recovery is recorded separately, never counted as an ordinary turn.
                    full_states.append(dict(game=name, seed=17, stage=stage, room_id=room['room_id'],
                        response=await call(room, room['current_player_id'] or 'p0', action='state', full_state=True)))
                    if status == 'playing':
                        await one_round(room, 17, stage, index, kind)
                    print(name, stage, flush=True)
    Path(args.out).write_text(json.dumps(dict(records=records, full_states=full_states), ensure_ascii=False, separators=(',', ':')) + '\n')
    print(f'{len(records)} rounds, {len(full_states)} full states -> {args.out}', flush=True)


if __name__ == '__main__':
    if os.environ.get('PYTHONHASHSEED') != '0':
        raise SystemExit('Run with PYTHONHASHSEED=0: upstream set iteration affects legal option ordering.')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--before-dir')
    parser.add_argument('--out', required=True)
    parser.add_argument('--steps', type=int, default=48)
    asyncio.run(sample(parser.parse_args()))

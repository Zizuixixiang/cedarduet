"""Seeded complete games through real ASGI /mcp/play on disposable SQLite.

PYTHONHASHSEED=0 PYTHONPATH=. python scripts/sample_gem_merchant_tokens.py --out /tmp/gem-before.json
Use --before-dir containing an unmodified gem_merchant.py to replay the baseline.
No tiktoken dependency in the application; comparison is a separate script.
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
from app.games.gem_merchant import GemMerchant


async def sample(args):
    game_class = GemMerchant
    if args.before_dir:
        spec = importlib.util.spec_from_file_location(
            'app.games._gem_before', Path(args.before_dir) / 'gem_merchant.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        game_class = module.GemMerchant
    records, bootstraps, games = [], [], []
    with tempfile.TemporaryDirectory(prefix='gem-token-') as tmp, \
         patch.object(database, 'DB_PATH', Path(tmp) / 'sample.db'), \
         patch.object(main, 'revision_events', main.RevisionEvents()):
        database.init_db()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url='http://sample') as client:
            for policy in ('random', 'buy_preferred'):
                for seed in (3, 11, 37, 71):
                    game = game_class(random.Random(seed))
                    rng = random.Random(seed + 1000)
                    with patch.dict(GAMES, gem_merchant=game), \
                         patch.object(framework, '_new_room_id', return_value=f'GEM{len(games):05d}'):
                        room = framework.create_room('gem_merchant', 'ai_first', 'ai', 'ai-1', 'human-1',
                                                     require_confirmations=False)
                        async def call(**body):
                            response = await client.post('/mcp/play', json=dict(
                                room_id=room['room_id'], player_id='ai-1', **body))
                            assert response.status_code == 200, response.text
                            return response.json()
                        boot = await call(action='state')
                        assert boot['bootstrap']
                        bootstraps.append(dict(seed=seed, policy=policy, response=boot))
                        trajectory = []
                        for step in range(4000):
                            if room['status'] != 'playing':
                                break
                            pid = room['current_player_id']
                            state = room['board_state']
                            actions = game._legal_actions_for(state, pid)
                            buys = [a for a in actions if a['action'] == 'buy']
                            move = rng.choice(buys if policy == 'buy_preferred' and buys and rng.random() < .8 else actions)
                            trajectory.append(dict(player=pid, move=move))
                            if pid == 'ai-1':
                                turn = await call(action='state')
                                assert 'bootstrap' not in turn and turn['your_turn']
                                reply = await call(action='move', revision=room['revision'], move=move)
                                records.append(dict(seed=seed, policy=policy, step=step,
                                    phase=(state['turn_state']['pending'] or [{'kind': 'optional'}])[0]['kind'],
                                    move=move, turn_state=turn, move_reply=reply))
                                room = framework.get_room(room['room_id'])
                            else:
                                room = framework.play_move(room['room_id'], 'human', pid, move,
                                                           expected_revision=room['revision'])
                        assert room['status'] == 'finished', (seed, policy, step)
                        games.append(dict(seed=seed, policy=policy, trajectory=trajectory,
                                          final_state=room['board_state']))
                        print(policy, seed, len(trajectory), 'actions', flush=True)
    Path(args.out).write_text(json.dumps(dict(records=records, bootstraps=bootstraps, games=games),
                                        ensure_ascii=False, separators=(',', ':')) + '\n')


if __name__ == '__main__':
    if os.environ.get('PYTHONHASHSEED') != '0':
        raise SystemExit('Run with PYTHONHASHSEED=0')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--before-dir')
    parser.add_argument('--out', required=True)
    asyncio.run(sample(parser.parse_args()))

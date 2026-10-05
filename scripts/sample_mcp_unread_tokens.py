"""Small paired legacy /mcp/play sample on disposable SQLite.

Run before and after the change with PYTHONHASHSEED=0 PYTHONPATH=.:
  python scripts/sample_mcp_unread_tokens.py --out /tmp/unread-before.json
  python scripts/sample_mcp_unread_tokens.py --out /tmp/unread-after.json --compare /tmp/unread-before.json
Token counting uses the existing gem sampler's cl100k_base compact JSON method.
"""
import argparse
import asyncio
import json
import os
from pathlib import Path
import random
import tempfile
from unittest.mock import patch

import httpx

from app import database, framework, main, notifications
from app.games import GAMES
from app.games.gem_merchant import GemMerchant
from app.wait_control import WaitControl
from scripts.compare_gem_merchant_tokens import sizes


async def sample():
    records = []
    for seed in (3, 11):
        with tempfile.TemporaryDirectory(prefix='unread-tokens-') as tmp, \
             patch.object(database, 'DB_PATH', Path(tmp) / 'sample.db'), \
             patch.object(main, 'revision_events', main.RevisionEvents()), \
             patch.object(main, 'wait_control', WaitControl()), \
             patch.dict(GAMES, gem_merchant=GemMerchant(random.Random(seed))), \
             patch.object(framework, '_new_room_id', return_value=f'GEM{seed:05d}'):
            database.init_db()
            room = framework.create_room('gem_merchant', 'ai_first', 'ai', 'ai-1', 'human-1',
                                         require_confirmations=False)
            rng = random.Random(seed + 1000)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app),
                                         base_url='http://sample') as client:
                async def call(**body):
                    response = await client.post('/mcp/play', json=dict(
                        room_id=room['room_id'], player_id='ai-1', **body))
                    assert response.status_code == 200, response.text
                    return response.json()

                assert (await call(action='state'))['bootstrap']
                rounds = 0
                trajectory = []
                while rounds < 20:
                    assert room['status'] == 'playing'
                    pid = room['current_player_id']
                    move = rng.choice(GAMES['gem_merchant']._legal_actions_for(room['board_state'], pid))
                    trajectory.append(dict(player=pid, move=move))
                    if pid == 'ai-1':
                        # Keep old work unread, then add a different category.
                        if rounds in (0, 10):
                            category = 'achievement' if rounds == 0 else 'exchange'
                            with database.write_transaction() as conn:
                                notifications.create_notification(
                                    conn, 'ai', pid, category, 'test', f'sample-{rounds}',
                                    'Sample unread work', event_key=f'sample:{rounds}')
                        state = await call(action='state')
                        reply = await call(action='move', revision=room['revision'], move=move)
                        records.append(dict(seed=seed, round=rounds, trajectory=trajectory,
                                            turn_state=state, move_reply=reply))
                        trajectory = []
                        rounds += 1
                        room = framework.get_room(room['room_id'])
                    else:
                        room = framework.play_move(room['room_id'], 'human', pid, move,
                                                   expected_revision=room['revision'])
    return records


def compare(before, after):
    assert len(before) == len(after) == 40
    for left, right in zip(before, after):
        for key in ('seed', 'round', 'trajectory'):
            assert left[key] == right[key], key
        for key in ('turn_state', 'move_reply'):
            strip = lambda r: {k: v for k, v in r.items() if k not in ('unread', 'unread_hint')}
            assert strip(left[key]) == strip(right[key]), (left['seed'], left['round'], key)
    report = {'method': 'cl100k_base; compact ensure_ascii=False JSON; state+move per round',
              'rounds': len(after), 'paired_responses_checked': 2 * len(after)}
    for side, records in (('before', before), ('after', after)):
        counts = [sizes(r) for r in records]
        report[side] = {key: round(sum(r[key] for r in counts) / len(counts), 2)
                        for key in ('total', 'envelope', 'unread_notices')}
        report[side]['responses_with_unread'] = sum(
            'unread' in r[key] for r in records for key in ('turn_state', 'move_reply'))
    report['envelope_saved_per_round'] = round(report['before']['envelope'] - report['after']['envelope'], 2)
    return report


if __name__ == '__main__':
    if os.environ.get('PYTHONHASHSEED') != '0':
        raise SystemExit('Run with PYTHONHASHSEED=0')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', required=True)
    parser.add_argument('--compare')
    args = parser.parse_args()
    result = asyncio.run(sample())
    Path(args.out).write_text(json.dumps(result, ensure_ascii=False, separators=(',', ':')) + '\n')
    if args.compare:
        print(json.dumps(compare(json.loads(Path(args.compare).read_text()), result), indent=2))
    else:
        print(f'Sampled {len(result)} rounds / {len(result) * 2} responses')

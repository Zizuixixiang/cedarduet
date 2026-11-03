"""Actual ASGI response sizes on a disposable DB; never reads live rooms.

PYTHONPATH=. .venv/bin/python scripts/sample_liars_dice_mcp.py /tmp/dice.json
Pass --classic-only when running against the pre-party HEAD tree.
"""
import asyncio
import json
from pathlib import Path
import random
import sys
import tempfile
from unittest.mock import patch

import httpx
import tiktoken

from app import database, framework, main
from app.games import GAMES
from app.games.liars_dice import LiarsDice
from tests.test_multiplayer_games import participants


async def sample(classic_only=False):
    traces = {}
    with tempfile.TemporaryDirectory(prefix='dice-wire-') as tmp, \
         patch.object(database, 'DB_PATH', Path(tmp) / 'sample.db'), \
         patch.dict(GAMES, liars_dice=LiarsDice(random.Random(51))):
        database.init_db()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url='http://test') as client:
            async def call(room, pid='ai-1', **body):
                response = await client.post('/mcp/play', json=dict(room_id=room['room_id'], player_id=pid, **body))
                assert response.status_code == 200, response.text
                return response.json()
            for mode in (['classic'] if classic_only else ['classic', 'party']):
                opts = {} if classic_only else {'game_options': {'mode': mode}}
                room = framework.create_room('liars_dice','ai_first','ai','ai-1',opponent_id='human-1',require_confirmations=False, **opts)
                records = traces[mode] = {}
                records['bootstrap'] = await call(room, action='state')
                records['unchanged'] = await call(room, action='state')
                records['bid'] = await call(room, action='move', revision=room['revision'], move={'action':'bid','quantity':3,'face':2})
                room = framework.get_room(room['room_id'])
                room = framework.play_move(room['room_id'],'human','human-1',{'action':'challenge'},expected_revision=room['revision'])
                records['challenge'] = await call(room, action='state')
                if mode == 'classic':
                    room = framework.acknowledge_liars_dice_round(room['room_id'],'human-1',expected_revision=room['revision'])
                else:
                    pid = room['current_player_id']
                    room = framework.play_move(room['room_id'],'ai' if pid.startswith('ai') else 'human',pid,{'action':'acknowledge_round'},expected_revision=room['revision'])
                records['next_round'] = await call(room, action='state')
            if not classic_only:
                room = framework.create_room('liars_dice','human_first','human','human-1',opponent_id='ai-1',ordered_participants=participants(4),
                    game_options={'mode':'party','team_mode':'teams','random_teams':False},target_player_count=4)
                records = traces['manual'] = {}
                records['bootstrap'] = await call(room, action='state')
                records['seat'] = await call(room, action='move',revision=room['revision'],move={'action':'choose_team_seat','team':'red','seat':1})
                records['unchanged'] = await call(room, action='state')
    return traces


if __name__ == '__main__':
    data = asyncio.run(sample('--classic-only' in sys.argv))
    enc = tiktoken.get_encoding('cl100k_base')
    sizes = {mode: {scenario: {'chars':len(s), 'tokens':len(enc.encode(s))}
             for scenario, value in records.items()
             for s in [json.dumps(value,ensure_ascii=False,separators=(',',':'))]}
             for mode, records in data.items()}
    Path(sys.argv[1]).write_text(json.dumps({'responses':data,'sizes':sizes},ensure_ascii=False,indent=2))
    print(json.dumps(sizes,ensure_ascii=False,indent=2))

"""Paired real HTTP turn replies on temporary SQLite; no live DB or services.

PYTHONPATH=. .venv/bin/python scripts/sample_doudizhu_junqi_compact.py --out DIR
python scripts/sample_doudizhu_junqi_compact.py --summarize DIR
The second command needs tiktoken; it does not import the application.
"""
import argparse
import asyncio
from copy import deepcopy
import json
import math
from pathlib import Path
import random
import tempfile
from unittest.mock import patch


async def sample(output):
    import httpx
    from app import database, framework, main
    from app.games import GAMES
    from tests.doudizhu_junqi_compact_support import (
        doudizhu_cases, junqi_cases, junqi_position, seats,
    )

    output.mkdir(parents=True, exist_ok=True)
    records = []
    with tempfile.TemporaryDirectory(prefix='ddz-junqi-sample-') as tmp:
        with patch.object(database, 'DB_PATH', Path(tmp) / 'test.db'):
            database.init_db()
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url='http://test') as client:
                for name in ('doudizhu', 'junqi'):
                    game = GAMES[name]
                    players = seats(game.min_players)
                    if name == 'doudizhu':
                        cases = list(doudizhu_cases(64))
                    else:
                        cases = list(junqi_cases())
                        cases.append(('setup', game.initialize_for_first_player(players, 'p0')))
                        rng = random.Random(20261003)
                        state = junqi_position()
                        for step in range(40):
                            pid = state['active_player_id']
                            if state.get('winner_player_id'):
                                break
                            cases.append((f'trace_{step}', deepcopy(state)))
                            actor = next(p for p in players if p['player_id'] == pid)
                            legal = game.legal_actions_for(state, pid)
                            state = game.apply_action(state, rng.choice(legal), actor).state
                    for label, state in cases:
                        room = framework.create_room(name, 'ai_first', 'ai', 'p0',
                            ordered_participants=players, require_confirmations=False)
                        pid = state.get('turn_player_id') or state.get('active_player_id')
                        with database.write_transaction() as conn:
                            conn.execute('UPDATE rooms SET board_state=?, current_player_id=? WHERE room_id=?',
                                         (json.dumps(state), pid, room['room_id']))
                        async def call(**body):
                            response = await client.post('/mcp/play', json=dict(
                                action='state', room_id=room['room_id'], player_id=pid, **body))
                            assert response.status_code == 200, response.text
                            return response.json()
                        await call()  # claim bootstrap
                        full = await call(full_state=True)  # drain old events
                        # The isolated baseline had no turn hook for these games.
                        # Disable only the new hook: identical state, old projection.
                        with patch.object(game, 'mcp_turn_private_state', lambda private, public: deepcopy(private)):
                            before = await call()
                        after = await call()
                        assert {k: v for k, v in before.items() if k != 'private_state'} == {
                            k: v for k, v in after.items() if k != 'private_state'}
                        actor = next(p for p in players if p['player_id'] == pid)
                        legal = game.legal_actions_for(state, pid)
                        expected_private = game.mcp_private_state(game.private_state(state, actor, players), actor, players)
                        assert before['private_state'] == expected_private
                        assert full['snapshot']['private_state'] == expected_private
                        records.append(dict(game=name, case=label, phase=state.get('phase') or state['flow']['phase'],
                                            legal_count=len(legal), before=before, after=after))
                        print(name, label, len(legal), flush=True)
    (output / 'samples.json').write_text(json.dumps(records, ensure_ascii=False, indent=2) + '\n')


def summarize(output):
    import tiktoken
    enc = tiktoken.get_encoding('cl100k_base')
    def tokens(value):
        # Matches compact JSON wire serialization (not pretty-printed artifacts).
        return len(enc.encode(json.dumps(value, ensure_ascii=False, separators=(',', ':'))))
    records = json.loads((output / 'samples.json').read_text())
    for r in records:
        r['tokens'] = {side: tokens(r[side]) for side in ('before', 'after')}
        r['private_tokens'] = {side: tokens(r[side]['private_state']) for side in ('before', 'after')}
    rows = []
    groups = {
        '斗地主 全样本': [r for r in records if r['game'] == 'doudizhu'],
        '斗地主 随机自由领出': [r for r in records if r['game'] == 'doudizhu' and r['case'].endswith('_lead')],
        '斗地主 随机跟牌': [r for r in records if r['game'] == 'doudizhu' and r['case'].endswith('_follow')],
        '斗地主 压力手牌自由领出': [r for r in records if r['game'] == 'doudizhu' and not r['case'].startswith(('deal_', 'bidding'))],
        '军棋 play': [r for r in records if r['game'] == 'junqi' and r['phase'] == 'play'],
        '军棋 setup': [r for r in records if r['game'] == 'junqi' and r['phase'] == 'setup'],
    }
    def stats(values):
        values = sorted(values)
        return [values[math.ceil(len(values) * q) - 1] for q in (.50, .95, 1)]
    for label, group in groups.items():
        for measure in ('tokens', 'private_tokens'):
            before = stats([r[measure]['before'] for r in group])
            after = stats([r[measure]['after'] for r in group])
            rows.append(dict(group=label, measure=measure, n=len(group), before=before, after=after))
    lines = ['# cl100k_base before / after', '',
             '口径：实际 /mcp/play 普通 state HTTP JSON，ensure_ascii=False、separators=(",", ":")；',
             '同一临时 SQLite 局面，bootstrap 和旧事件已消费，before 仅禁用新增 turn hook，after 启用。',
             '统计采用 nearest-rank p50/p95/max。样本是确定性验收语料，不是生产分布或数学全局最大值。', '',
             '| 样本 | 口径 | n | before p50 / p95 / max | after p50 / p95 / max | p50 降幅 |',
             '|---|---|---:|---|---|---:|']
    for r in rows:
        reduction = 100 * (1 - r['after'][0] / r['before'][0])
        lines.append(f"| {r['group']} | {r['measure']} | {r['n']} | {' / '.join(map(str, r['before']))} | {' / '.join(map(str, r['after']))} | {reduction:.1f}% |")
    (output / 'tokens.md').write_text('\n'.join(lines) + '\n')
    (output / 'token-statistics.json').write_text(json.dumps(rows, ensure_ascii=False, indent=2) + '\n')
    ddz = [r for r in records if r['game'] == 'doudizhu']
    worst = max(ddz, key=lambda r: r['tokens']['before'])
    (output / 'worst-doudizhu.json').write_text(json.dumps(worst, ensure_ascii=False, indent=2) + '\n')
    stress = sorted(ddz, key=lambda r: r['tokens']['before'], reverse=True)[:12]
    lines = ['# 斗地主最坏样本（当前语料中）', '',
             '| 样本 | 合法动作数 | before 整包 | after 整包 | 降幅 |', '|---|---:|---:|---:|---:|']
    for r in stress:
        b, a = r['tokens']['before'], r['tokens']['after']
        lines.append(f"| {r['case']} | {r['legal_count']} | {b} | {a} | {100 * (1-a/b):.1f}% |")
    lines += ['', '最坏样本完整 before/after 见 worst-doudizhu.json；所有原始样本见 samples.json。',
              '随机语料：seed 0–63 的真实 20 张地主手牌，各一份自由领出及一份由对手真实出牌形成的跟牌；',
              '压力语料：五组四张、四组三张带对、六组三张、三组四张带散牌、歧义四带两对、长连对、双王炸弹带牌；',
              '另含两份叫分。军棋为五个专项局面、固定随机种子 20261003 的 40 步权威合法行棋轨迹及一份 setup。']
    (output / 'worst-doudizhu.md').write_text('\n'.join(lines) + '\n')
    print((output / 'tokens.md').read_text())


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--out', type=Path)
    group.add_argument('--summarize', type=Path)
    args = parser.parse_args()
    if args.summarize:
        summarize(args.summarize)
    else:
        asyncio.run(sample(args.out))

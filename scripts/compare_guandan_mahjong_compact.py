"""Paired ASGI evidence; cl100k_base, compact JSON, nearest-rank percentiles."""
import argparse
import hashlib
import json
import math
import re
from pathlib import Path

import tiktoken


def compare(before_path, after_path, out_dir):
    encoder = tiktoken.get_encoding('cl100k_base')
    before, after = [json.loads(Path(p).read_text()) for p in (before_path, after_path)]
    out = Path(out_dir)
    def wire(value, room_id):
        text = json.dumps(value, ensure_ascii=False, separators=(',', ':'))
        text = text.replace(room_id, 'ROOM0000')
        return re.sub(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?(?:\+00:00|Z)', 'TIME', text)
    def tokens(value, room_id):
        return len(encoder.encode(wire(value, room_id))) if value is not None else 0
    def key(r):
        return r['game'], r['seed'], r['stage'], r.get('step')
    old, new = [{key(r): r for r in data['records']} for data in (before, after)]
    assert old.keys() == new.keys()
    rows = []
    for k, b in old.items():
        a = new[k]
        assert b['move'] == a['move'], k
        assert b['legal_action_count'] == a['legal_action_count'], k
        for part in ('turn_state', 'move_reply'):
            left, right = [dict(r[part]) for r in (b, a)]
            bp, ap = left.pop('private_state', None), right.pop('private_state', None)
            assert wire(left, b['room_id']) == wire(right, a['room_id']), (k, part)
            if bp is None:
                assert ap is None
            elif b['game'] == 'guandan':
                expected = json.loads(json.dumps(bp))
                for field in ('fields', 'pattern_labels', 'submit', 'coverage'):
                    expected['legal_actions'].pop(field)
                assert ap == expected, (k, part)
            else:
                expected = json.loads(json.dumps(bp))
                expected['format'] = 'mahjong_tiles_v1'
                expected['hand'] = [[t['id'], t['label']] for t in bp['hand']]
                for meld in expected['own_melds']:
                    meld['tiles'] = [[t['id'], t['label']] for t in meld['tiles']]
                    meld.pop('tile_count')
                assert ap == expected, (k, part)
        row = {field: a[field] for field in ('game', 'seed', 'stage', 'step', 'phase', 'legal_action_count')}
        for label, record in [('before', b), ('after', a)]:
            sizes = {}
            for part in ('turn_state', 'move_reply'):
                sizes[part] = tokens(record[part], record['room_id'])
                sizes[part + '_private'] = tokens(record[part].get('private_state'), record['room_id'])
            sizes['normal_round'] = sizes['turn_state'] + sizes['move_reply']
            sizes['normal_round_private'] = sizes['turn_state_private'] + sizes['move_reply_private']
            row[label] = sizes
        rows.append(row)
    def stats(values):
        values = sorted(values)
        return dict(n=len(values), **{name: values[max(0, math.ceil(len(values) * p) - 1)]
                    for name, p in [('p50', .5), ('p95', .95), ('max', 1)]})
    summary = {}
    lines = ['# Guandan / Mahjong ordinary MCP token 对照', '',
             '口径：临时 SQLite + 真实 ASGI `/mcp/play`；cl100k_base；紧凑 JSON、保留中文；',
             '只将随机 room_id 规范为 ROOM0000、ISO 时间规范为 TIME。分位数为 nearest rank。',
             'normal_round = 一次当前行动者 turn_state + 其 move_reply 的 token 之和；不含请求、bootstrap、full_state。',
             'private 是相应响应 private_state 子对象单独编码的 token 数；不存在时为 0。',
             '子对象 token 不能严格相加等于整包（边界分词不同）。', '',
             '| 游戏 | 范围 | 指标 | n | before p50/p95/max | after p50/p95/max | p50 变化 |',
             '|---|---|---|---:|---:|---:|---:|']
    for game in ('guandan', 'mahjong'):
        summary[game] = {}
        for scope in ('all', 'seeded_play', 'special'):
            subset = [r for r in rows if r['game'] == game and
                      (scope == 'all' or (r['stage'] == 'seeded_play') == (scope == 'seeded_play'))]
            summary[game][scope] = {}
            for metric in ('turn_state', 'move_reply', 'normal_round', 'turn_state_private', 'move_reply_private', 'normal_round_private'):
                b, a = [stats([r[side][metric] for r in subset]) for side in ('before', 'after')]
                summary[game][scope][metric] = dict(before=b, after=a)
                fmt = lambda d: '/'.join(str(d[p]) for p in ('p50', 'p95', 'max'))
                delta = f'{(a["p50"] / b["p50"] - 1) * 100:+.1f}%' if b['p50'] else '—'
                lines.append(f'| {game} | {scope} | {metric} | {b["n"]} | {fmt(b)} | {fmt(a)} | {delta} |')
    full_before, full_after = [{key(r): r for r in data['full_states']} for data in (before, after)]
    assert full_before.keys() == full_after.keys()
    lines.extend(['', '## full_state（记录，不以压缩为目标）', '', '| 游戏 | n | before p50/p95/max | after p50/p95/max |', '|---|---:|---:|---:|'])
    for k, b in full_before.items():
        a = full_after[k]
        left, right = [json.loads(json.dumps(r['response'])) for r in (b, a)]
        left['snapshot'].pop('move_format')
        right['snapshot'].pop('move_format')
        assert wire(left, b['room_id']) == wire(right, a['room_id']), ('full_state', k)
    for game in ('guandan', 'mahjong'):
        b, a = [stats([tokens(r['response'], r['room_id']) for r in data.values() if r['game'] == game])
                for data in (full_before, full_after)]
        summary[game]['full_state'] = dict(before=b, after=a)
        lines.append(f'| {game} | {b["n"]} | {fmt(b)} | {fmt(a)} |')
    lines.extend(['', '## 麻将 private 字段占比（before）', '', '| 字段组 | p50/p95/max token | 平均占 private 比例 |', '|---|---:|---:|'])
    breakdown = {}
    for fields in [('hand',), ('own_melds',), ('shanten', 'shanten_basis'), ('legal_actions',)]:
        values, ratios = [], []
        for r in old.values():
            if r['game'] != 'mahjong':
                continue
            private = r['turn_state']['private_state']
            size = tokens({f: private[f] for f in fields}, r['room_id'])
            values.append(size)
            ratios.append(size / tokens(private, r['room_id']))
        name = '+'.join(fields)
        breakdown[name] = dict(**stats(values), mean_fraction=sum(ratios) / len(ratios))
        lines.append(f'| {name} | {fmt(stats(values))} | {sum(ratios) / len(ratios):.1%} |')
    lines.extend(['', f'逐值验收：{len(rows)} 对普通 round 的动作、事件、非 private 字段完全相同；',
                  '掼蛋全部动态 private 字段相同；麻将只发生声明的牌对象投影和 tile_count 省略。',
                  f'{len(full_before)} 对 full_state 除补全的 MCP move_format 外逐值相同，详细 private 原样恢复。', '',
                  'seeded_play 是 7/11/37 三种子实际推进；special 是引擎构造的可复现边界状态。',
                  '分组分位数分别列出，避免把人为特殊阶段频率解释为生产分布。', ''])
    for p in (before_path, after_path):
        lines.append(f'`{Path(p).name}` SHA256 `{hashlib.sha256(Path(p).read_bytes()).hexdigest()}`')
    (out / 'tokens.json').write_text(json.dumps(dict(summary=summary, mahjong_private_breakdown=breakdown, rounds=rows), ensure_ascii=False, indent=2) + '\n')
    (out / 'TOKENS.md').write_text('\n'.join(lines) + '\n')
    # Entire real responses, including the full recovery schema, for human review.
    examples = {}
    for game, stage in [('guandan', 'single_return_tribute_1'), ('mahjong', 'own_concealed_gang')]:
        r = next(r for r in new.values() if r['game'] == game and r['stage'] == stage)
        full = next(r for r in full_after.values() if r['game'] == game and r['stage'] == stage)
        examples[game] = dict(ordinary_response=r['turn_state'], full_state=full['response'])
    (out / 'readable-examples.json').write_text(json.dumps(examples, ensure_ascii=False, indent=2) + '\n')
    print(f'{len(rows)} paired rounds; {len(full_before)} paired full_state snapshots verified')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('before')
    parser.add_argument('after')
    parser.add_argument('--out-dir', required=True)
    args = parser.parse_args()
    compare(args.before, args.after, args.out_dir)

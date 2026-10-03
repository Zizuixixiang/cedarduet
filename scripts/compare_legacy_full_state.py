"""Compare raw HTTP artifacts. Requires tiktoken (analysis environment only)."""
import argparse
import hashlib
import json
import re
from pathlib import Path

import tiktoken


def normalized(record, value):
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    room_id = record['full_state']['snapshot']['room_id']
    encoded = encoded.replace(room_id, 'ROOM0000')
    # Clock values are the only other nondeterministic response values.
    encoded = re.sub(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:\+00:00|Z)', 'TIME', encoded)
    return encoded


def compare(before_path, after_path, output):
    before, after = (json.loads(Path(p).read_text()) for p in (before_path, after_path))
    before = {(r['game'], r['seed']): r for r in before}
    after = {(r['game'], r['seed']): r for r in after}
    assert set(before) == set(after), (set(before) - set(after), set(after) - set(before))
    encoder = tiktoken.get_encoding('cl100k_base')
    lines = ['| 游戏 | 种子 | 中局动作数 | before | after | 变化 | 普通响应逐值相等 |',
             '|---|---:|---:|---:|---:|---:|---|']
    response_count = 0
    for key, old in before.items():
        new = after[key]
        assert old['steps'] == new['steps']
        old_trace, new_trace = (normalized(r, r['ordinary']) for r in (old, new))
        if old_trace != new_trace:
            Path('/tmp/legacy-trace-before.json').write_text(old_trace)
            Path('/tmp/legacy-trace-after.json').write_text(new_trace)
            raise AssertionError(f'ordinary response changed: {key}')
        assert old['next_move'] == new['next_move'], key
        assert old['full_state']['snapshot']['private_state'] == new['full_state']['snapshot']['private_state'], key
        sizes = []
        for r in (old, new):
            # Same room identifier in each measurement avoids random ID token noise.
            encoded = json.dumps(r['full_state'], ensure_ascii=False, separators=(',', ':'))
            encoded = encoded.replace(r['full_state']['snapshot']['room_id'], 'ROOM0000')
            sizes.append(len(encoder.encode(encoded)))
        b, a = sizes
        count = len(old['ordinary'])
        response_count += count
        lines.append(f'| {key[0]} | {key[1]} | {old["steps"]} | {b} | {a} | {a-b:+d} ({(a/b-1)*100:+.1f}%) | {count} 条相等 |')
    lines.extend(['', f'共 {len(before)} 组，{response_count} 条 bootstrap / normal state / move 响应逐值相等。',
                  '仅规范化随机 room_id 和 ISO 时间值；其余字段、事件、私有信息与合法行动均参与比较。',
                  'token 口径：完整 full_state JSON（紧凑序列化，ensure_ascii=False），cl100k_base；无未读文字的中局。',
                  '每组 after 均从 full_state 选出 next_move 并通过真实框架执行，没有再请求 state/guide。', ''])
    for path in (before_path, after_path):
        lines.append(f'原始证据 `{Path(path).name}` SHA256：`{hashlib.sha256(Path(path).read_bytes()).hexdigest()}`')
    Path(output).write_text('\n'.join(lines) + '\n')
    print(f'{len(before)} samples, {response_count} identical ordinary responses; {output}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('before')
    parser.add_argument('after')
    parser.add_argument('--out', required=True)
    args = parser.parse_args()
    compare(args.before, args.after, args.out)

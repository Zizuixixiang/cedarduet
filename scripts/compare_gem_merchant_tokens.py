"""Compare paired gem ASGI traces: cl100k_base, compact JSON, nearest rank."""
import argparse
from collections import Counter
from copy import deepcopy
import hashlib
import gzip
import json
import math
from pathlib import Path

import tiktoken
from tests.gem_merchant_compact_support import apply_delta

ENCODER = tiktoken.get_encoding('cl100k_base')


def tokens(value):
    return len(ENCODER.encode(json.dumps(value, ensure_ascii=False, separators=(',', ':'))))


def stats(values):
    values = sorted(values)
    return dict(n=len(values), mean=round(sum(values) / len(values), 1), **{
        name: values[max(0, math.ceil(len(values) * p) - 1)]
        for name, p in [('p10', .1), ('p50', .5), ('p90', .9), ('max', 1)]})


def sizes(record):
    pair = [record[k] for k in ('turn_state', 'move_reply')]
    total = sum(map(tokens, pair))
    def marginal(remove):
        stripped = deepcopy(pair)
        for response in stripped:
            remove(response)
        return total - sum(map(tokens, stripped))
    result = dict(total=total, state=tokens(pair[0]), move=tokens(pair[1]))
    result['envelope'] = sum(tokens({k: v for k, v in r.items() if k not in ('events', 'private_state')}) for r in pair)
    result['unread_notices'] = marginal(lambda r: [r.pop(k, None) for k in ('unread', 'unread_hint')])
    result['private'] = marginal(lambda r: r.pop('private_state', None))
    for key in ('reserved', 'blind_reserved', 'legal_summary', 'legal_actions'):
        result[key] = marginal(lambda r: r.get('private_state', {}).pop(key, None))
    result['events'] = marginal(lambda r: r.pop('events', None))
    result['opponent_events'] = tokens(pair[0].get('events', [])) - tokens([])
    for key in ('board', 'board_set', 'players', 'pyramid_set'):
        result['delta_' + key] = marginal(lambda r: [
            e.get('gem_merchant_delta', {}).pop(key, None) for e in r.get('events', [])])
    def remove_descriptions(r):
        summary = r.get('private_state', {}).get('legal_summary', {})
        for key in ('take', 'use_privilege'):
            if isinstance(summary.get(key), str):
                summary[key] = True
    result['repeated_instructions'] = marginal(remove_descriptions)
    return result


def compare(before_path, after_path, out_path):
    before, after = [json.loads(gzip.decompress(Path(p).read_bytes()) if str(p).endswith('.gz')
                               else Path(p).read_bytes()) for p in (before_path, after_path)]
    assert before['games'] == after['games'], 'Rules, complete action trajectories and terminal states must match'
    assert len(before['records']) == len(after['records'])
    boards = []
    for data in (before, after):
        boards.append({b['response']['room']['room_id']: deepcopy(b['response']['room']['board_state'])
                       for b in data['bootstraps']})
    rows = []
    lengths = {(g['policy'], g['seed']): len(g['trajectory']) for g in before['games']}
    terminal = 0
    for b, a in zip(before['records'], after['records']):
        for key in ('seed', 'policy', 'step', 'phase', 'move'):
            assert b[key] == a[key], key
        for part in ('turn_state', 'move_reply'):
            left, right = deepcopy(b[part]), deepcopy(a[part])
            bp, ap = left.pop('private_state', None), right.pop('private_state', None)
            if bp is None:
                assert ap is None
            else:
                expected = deepcopy(bp)
                expected['blind_reserved'] = [c for c in expected.pop('reserved') if c.endswith(' blind')]
                summary = expected.get('legal_summary', {})
                if 'take' in summary:
                    summary['take'] = int(summary['take'].split()[0])
                if 'use_privilege' in summary:
                    summary['use_privilege'] = True
                assert expected == ap
            # Check every delivered event, not only the terminal state. All
            # non-delta event fields and the shared envelope stay identical.
            assert len(left.get('events', [])) == len(right.get('events', []))
            for le, re in zip(left.get('events', []), right.get('events', [])):
                for index, event in enumerate((le, re)):
                    apply_delta(boards[index][left['room_id']], event.pop('gem_merchant_delta', {}))
                for field, value in boards[0][left['room_id']].items():
                    if field != 'delta_format':
                        assert value == boards[1][left['room_id']][field], (part, field, a['step'])
            # Achievement timestamps vary across runs; neither these terminal
            # responses nor their timestamps enter ordinary-round statistics.
            for response in (left, right):
                for unlock in response.get('unlocks', []):
                    unlock.pop('unlocked_at', None)
            assert left == right, (part, a['step'])
        if a['move_reply']['status'] != 'playing':
            terminal += 1
            continue
        stage = min(2, a['step'] * 3 // lengths[a['policy'], a['seed']])
        rows.append(dict(seed=a['seed'], policy=a['policy'], step=a['step'], phase=a['phase'],
                         stage=('early', 'middle', 'late')[stage], before=sizes(b), after=sizes(a)))
    groups = dict(all=rows)
    for field in ('policy', 'stage', 'phase'):
        for value in sorted({r[field] for r in rows}):
            groups[value] = [r for r in rows if r[field] == value]
    summary = {name: {side: stats([r[side]['total'] for r in selected])
                      for side in ('before', 'after')} for name, selected in groups.items()}
    breakdown = {side: {key: round(sum(r[side][key] for r in rows) / len(rows), 1)
                        for key in rows[0][side]} for side in ('before', 'after')}
    report = dict(method='cl100k_base; compact ensure_ascii=False JSON; actual state+move responses; nearest rank; no normalization',
                  games=len(before['games']), terminal_rounds_excluded=terminal,
                  trajectory_sha256=hashlib.sha256(json.dumps(before['games'], sort_keys=True).encode()).hexdigest(),
                  raw_sha256={side: hashlib.sha256(Path(path).read_bytes()).hexdigest()
                              for side, path in [('before', before_path), ('after', after_path)]},
                  summary=summary, mean_marginal_tokens=breakdown,
                  bootstrap={side: stats([tokens(b['response']) for b in data['bootstraps']])
                             for side, data in [('before', before), ('after', after)]},
                  actions=dict(Counter(a['move']['action'] for g in after['games'] for a in g['trajectory'])),
                  paired_response_checks=len(before['records']) * 2)
    Path(out_path).write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({k: v for k, v in report.items() if k != 'rounds'}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('before')
    parser.add_argument('after')
    parser.add_argument('--out', required=True)
    parser.add_argument('--check-budgets', action='store_true', help='Guard the fixed eight-game ordinary p50/p90')
    args = parser.parse_args()
    compare(args.before, args.after, args.out)
    if args.check_budgets:
        result = json.loads(Path(args.out).read_text())['summary']['all']['after']
        assert result['n'] == 515, result
        assert result['p50'] <= 430 and result['p90'] <= 580, result

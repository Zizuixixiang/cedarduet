"""Small reference client for the documented gem MCP wire format, no engine imports."""
from copy import deepcopy
from itertools import combinations


def apply_delta(board, delta):
    for key, value in delta.items():
        if key == 'board_set':
            for r, c, gem in value:
                row = board['board'][r]
                board['board'][r] = row[:c] + gem + row[c + 1:]
        elif key == 'pyramid_set':
            for level, index, card in value:
                board['pyramid'][str(level)][index] = card
        elif key == 'players':
            for pid, changes in value.items():
                for field, data in changes.items():
                    if field == 'purchased_add':
                        board['players'][pid]['purchased'].extend(data)
                    else:
                        board['players'][pid][field] = deepcopy(data)
        else:
            board[key] = deepcopy(value)


def response_actions(board, private):
    """Enumerate submissions using received summary, board and documented rules."""
    if 'legal_actions' in private:
        return deepcopy(private['legal_actions'])
    summary = private['legal_summary']
    actions = []
    cells = [[r, c] for r, row in enumerate(board['board']) for c, gem in enumerate(row) if gem not in '.O']
    if summary.get('use_privilege'):
        actions += [dict(action='use_privilege', cell=cell) for cell in cells]
    for action in ('refill', 'pass'):
        if summary.get(action):
            actions.append(dict(action=action))
    if summary.get('take'):
        takes = []
        for n in (1, 2, 3):
            for line in combinations(cells, n):
                differences = {(b[0] - a[0], b[1] - a[1]) for a, b in zip(line, line[1:])}
                if not differences or (len(differences) == 1 and differences <= {(0, 1), (1, -1), (1, 0), (1, 1)}):
                    takes.append(dict(action='take', cells=list(line)))
        count = summary['take']
        assert len(takes) == (count if isinstance(count, int) else int(count.split()[0]))
        actions += takes
    if 'reserve' in summary:
        reserve = summary['reserve']
        for gold in reserve['gold']:
            for key, values in [('card_id', reserve['card_ids']), ('level', reserve['levels'])]:
                actions += [dict(action='reserve', gold=gold, **{key: value}) for value in values]
    for buy in summary.get('buy', []):
        if isinstance(buy, int):
            actions.append(dict(action='buy', card_id=buy))
        else:
            actions += [dict(action='buy', card_id=buy['card_id'], joker_color=color) for color in buy['joker_color']]
    return actions

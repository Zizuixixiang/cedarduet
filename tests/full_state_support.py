"""Deterministic, snapshot-only action chooser for resync acceptance tests."""
from copy import deepcopy

LEGACY = '''aeroplane_chess banqi blackjack tictactoe texas_holdem train_cards
gomoku go gandengyan guandan othello connect4 checkers chess chinese_checkers
dots_boxes doudizhu liars_dice mahjong yahtzee uno jungle junqi xiangqi zhajinhua'''.split()


def choose(snapshot, step=0):
    game = snapshot['game']
    board = snapshot['board_state']
    private = snapshot['private_state']
    actor = snapshot['current_actor']
    if game in {'tictactoe', 'gomoku'}:
        return next({'row': r, 'col': c} for r, row in enumerate(board['board'])
                    for c, cell in enumerate(row) if cell is None)
    if game == 'connect4':
        return next({'col': c} for c, cell in enumerate(board['board'][0]) if cell is None)
    if game == 'dots_boxes':
        return next({'orientation': direction, 'row': r, 'col': c}
                    for direction, key in [('h', 'horizontal_edges'), ('v', 'vertical_edges')]
                    for r, row in enumerate(board[key]) for c, edge in enumerate(row) if edge is None)
    if game in {'othello', 'jungle'}:
        return deepcopy(board['legal_moves_by_mark'][actor['token']][0])
    if game == 'liars_dice':
        if private.get('legal_actions'):
            return private['legal_actions'][0]
        bid = board.get('current_bid')
        quantity, face = (bid['quantity'], bid['face']) if bid else (1, 0)
        if face == 6:
            quantity, face = quantity + 1, 0
        if quantity > board['max_bid_quantity']:
            return {'action': 'challenge'}
        return {'action': 'bid', 'quantity': quantity, 'face': face + 1}
    if game == 'go':
        spec = private.get('legal_action_spec')
        if spec:
            r, columns = next((r, v) for r, v in enumerate(spec['columns_by_row']) if v)
            return {'action': spec['action'], 'row': r, 'col': int(columns.split(',')[0].split('-')[0])}
    actions = private.get('legal_actions') or board.get('legal_actions') or board.get('legal_moves')
    if game == 'guandan':
        # First published option keeps the baseline trace executable. Some other
        # parametric examples already fail the baseline canonicalizer; see the
        # acceptance report. Full-state code does not alter this legal contract.
        option = dict(zip(actions['fields'], actions['options'][0]))
        indexes = option['example_hand_indexes']
        digits = '0123456789abcdefghijklmnopqrstuvwxyz'
        def base36(n):
            return digits[n] if n < 36 else digits[n // 36] + digits[n % 36]
        suffix = '.' + ','.join(base36(i) for i in sorted(indexes)) if indexes else ''
        return {'action': 'act', 'action_id': actions['action_id_prefix'] + option['suffix'] + suffix}
    assert actions, (game, board, private)
    preferred = {
        'junqi': ['ready', 'move'], 'blackjack': ['stand'],
        'texas_holdem': ['check', 'call'], 'zhajinhua': ['peek', 'call'],
        'doudizhu': ['bid', 'play', 'pass'],
        'yahtzee': ['score', 'roll'], 'uno': ['play', 'pass', 'draw'],
    }.get(game, [])
    for kind in preferred:
        matching = [a for a in actions if a.get('action') == kind]
        if matching:
            if game == 'doudizhu' and kind == 'bid':
                return deepcopy(matching[-1])
            return deepcopy(matching[step % len(matching)])
    return deepcopy(actions[step % len(actions)])

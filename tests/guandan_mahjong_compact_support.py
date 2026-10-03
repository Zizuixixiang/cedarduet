"""Deterministic boundary states and response-only decoders for the two games.

Fixtures use upstream transitions. Only temporary test rooms receive these states.
No decoder reads fixture hands, engine actions or production format constants.
"""
import functools
import random
import re
from copy import deepcopy

from app.games.mahjong import build_wall
from third_party.rlcard_guandan.engine import GuandanEngine
from third_party.rlcard_guandan.guandan_rlcard.game.card_utils import natural_card_cmp, card_to_str


def seats():
    return [dict(player_id=f'p{i}', role='ai', participant_kind='bound_machine',
                 display_name=f'玩家{i}', seat_index=i, active=True) for i in range(4)]


def guandan_moves(private, full_private, indexes_by_suffix=None):
    """Decode each published option using only a recovered full-state schema."""
    spec = full_private['legal_actions']
    dynamic = private['legal_actions']
    assert dynamic['format'] == spec['format']
    base = int(re.search(r'base(\d+)', spec['submit']['action_id'])[1])
    alphabet = '0123456789abcdefghijklmnopqrstuvwxyz'
    def encode(number):
        result = ''
        while number:
            number, digit = divmod(number, base)
            result = alphabet[digit] + result
        return result or '0'
    moves = []
    for row in dynamic['options']:
        assert len(row) == len(spec['fields'])
        option = dict(zip(spec['fields'], row))
        indexes = (indexes_by_suffix[option['suffix']] if indexes_by_suffix is not None
                   else option['example_hand_indexes'])
        assert indexes == sorted(set(indexes))
        assert all(0 <= n < len(private['hand']) for n in indexes)
        action_id = dynamic['action_id_prefix'] + option['suffix']
        if indexes:
            action_id += '.' + ','.join(encode(n) for n in indexes)
        moves.append(dict(action=spec['submit']['action'], action_id=action_id))
    return moves


def mahjong_moves(private, full_snapshot):
    """Read tile rows against full_state's live detailed tiles, copy ALL actions."""
    assert private['format'] in full_snapshot['move_format']
    detailed = full_snapshot['private_state']
    assert private['hand'] == [[t['id'], t['label']] for t in detailed['hand']]
    for short, full in zip(private['own_melds'], detailed['own_melds']):
        assert short['kind'] == full['kind']
        assert short['source_player_id'] == full['source_player_id']
        assert short['tiles'] == [[t['id'], t['label']] for t in full['tiles']]
    assert private['legal_actions'] == detailed['legal_actions']
    return deepcopy(private['legal_actions'])


def guandan_scenarios(game):
    result = []
    ps = seats()
    # Full real decks for single/double tribute and resistance; force red-joker
    # ownership by swaps, then ask the actual upstream tribute state machine.
    for mode, order, countered in (
        ('double', [0, 2, 1, 3], False), ('single', [0, 1, 2, 3], False),
        ('resist_double', [0, 2, 1, 3], True), ('resist_single', [0, 1, 2, 3], True),
    ):
        state = game.initialize(ps)
        core = GuandanEngine._load_game(state)
        target = 3 if countered else 0
        for owner in core.players:
            for card in list(owner.current_hand):
                if card_to_str(card) == 'HR' and owner is not core.players[target]:
                    other = next(c for c in core.players[target].current_hand if card_to_str(c) != 'HR')
                    owner.current_hand.remove(card)
                    core.players[target].current_hand.remove(other)
                    owner.current_hand.append(other)
                    core.players[target].current_hand.append(card)
        for player in core.players:
            player.set_current_hand(sorted(player.current_hand, key=functools.cmp_to_key(natural_card_cmp)))
        core.round.result = order
        pending = core.round._start_interactive_tribute(core.players)
        assert pending != countered
        core.judger.reset(core.players, core.cur_rank)
        GuandanEngine._store_game(state, core)
        if countered:
            result.append((mode, deepcopy(state), 'play'))
        else:
            i = 0
            while state['phase'] in {'tribute', 'return_tribute'}:
                kind = state['phase']
                result.append((f'{mode}_{kind}_{i}', deepcopy(state), kind))
                action = GuandanEngine.legal_actions(state, state['turn_player_id'])[0]
                GuandanEngine.apply_action(state, state['turn_player_id'], action['action_id'])
                i += 1
            result.append((f'{mode}_complete', deepcopy(state), 'play'))
    # Sparse late-deal fixture, as in the pre-existing upstream wind-follow test.
    state = game.initialize(ps)
    core = GuandanEngine._load_game(state)
    for player in core.players:
        player.set_current_hand([player.current_hand[0]])
        player.real_out = False
        player.played_action = None
    core.round.current_player = 0
    core.round.result = [-1] * 4
    core.round.win_count = 0
    core.round.out_flag = [False] * 4
    core.round.greater_player = None
    core.round.trace = []
    core.round._build_public(core.players)
    core.judger.reset(core.players, core.cur_rank)
    GuandanEngine._store_game(state, core)
    state['turn_player_id'] = 'p0'
    state['trick'] = dict(number=1, leader_player_id='p0', last_play=None,
                          pass_player_ids=[], wind_follow=False)
    for pid, kind in [('p0', 'play'), ('p1', 'pass'), ('p2', 'pass'), ('p3', 'pass')]:
        result.append((f'wind_before_{pid}', deepcopy(state), kind))
        action = next(a for a in GuandanEngine.legal_actions(state, pid) if a['kind'] == kind)
        GuandanEngine.apply_action(state, pid, action['action_id'])
    result.append(('wind_follow', deepcopy(state), 'wind_follow'))
    action = GuandanEngine.legal_actions(state, 'p0')[0]
    GuandanEngine.apply_action(state, 'p0', action['action_id'])
    result.append(('wind_partner', deepcopy(state), 'play'))
    for terminal in (False, True):
        state = game.initialize(ps)
        core = GuandanEngine._load_game(state)
        if terminal:
            core.team0_rank = core.round.team0_rank = 12
            core.round.rank_list = [12, 0]
        core.round.result = [0, 2, 1, 3]
        core.round.win_count = 2
        core.round.game_over = True
        core.rank_update()
        GuandanEngine._store_game(state, core)
        result.append(('match_finished' if terminal else 'deal_upgrade', state, None))
    return result


def mahjong_scenarios(game):
    ps = seats()
    rng = random.Random(41)
    def rig(groups, melds=None):
        pool = build_wall()
        rng.shuffle(pool)
        state = game._empty_state([p['player_id'] for p in ps])
        state.update(phase='discard', turn_player_id='p0')
        state['flow']['phase'] = 'discard'
        def take(codes):
            tiles = []
            for code in codes:
                tile = next(t for t in pool if t['code'] == code)
                pool.remove(tile)
                tiles.append(tile)
            return tiles
        for pid, codes in groups.items():
            state['hands'][pid] = take(codes)
        for pid, kind, codes, source in melds or []:
            state['melds'][pid].append(dict(kind=kind, tiles=take(codes),
                engine_tile=codes[1], offer=1 if source else 0, source_player_id=source))
        for pid in state['hands']:
            target = (14 if pid == 'p0' else 13) - 3 * len(state['melds'][pid])
            while len(state['hands'][pid]) < target:
                state['hands'][pid].append(pool.pop())
        state['wall'] = pool
        state['drawn_tile_id'] = state['hands']['p0'][0]['id']
        state['hand_counts'] = {pid: len(hand) for pid, hand in state['hands'].items()}
        return state
    def apply(state, kind):
        pid = state['turn_player_id']
        action = next(a for a in game.legal_actions_for(state, pid) if a['kind'] == kind)
        return game.apply_action(state, dict(action='act', action_id=action['action_id']),
                                 next(p for p in ps if p['player_id'] == pid)).state
    def discard_code(state, code):
        action = next(a for a in game.legal_actions_for(state, 'p0')
                      if a['kind'] == 'discard' and a['tile_id'].startswith(code + '-'))
        return game.apply_action(state, dict(action='act', action_id=action['action_id']), ps[0]).state
    result = []
    state = rig({'p0': ['W3'], 'p1': ['W1', 'W2'], 'p2': ['W3'] * 3})
    state = discard_code(state, 'W3')
    while state['turn_player_id'] != 'p2':
        state = apply(state, 'pass')
    result.append(('peng_gang_window', deepcopy(state), 'peng'))
    result.append(('own_peng', apply(state, 'peng'), 'discard'))
    result.append(('own_ming_gang', apply(state, 'ming_gang'), 'discard'))
    state = apply(state, 'pass')
    while not any(a['kind'] == 'chi' for a in game.legal_actions_for(state, state['turn_player_id'])):
        state = apply(state, 'pass')
    result.append(('chi_window', deepcopy(state), 'chi'))
    result.append(('own_chi', apply(state, 'chi'), 'discard'))
    state = rig({'p0': ['J2'] * 4})
    result.append(('concealed_gang_choice', deepcopy(state), 'concealed_gang'))
    result.append(('own_concealed_gang', apply(state, 'concealed_gang'), 'discard'))
    waiting = ['W2', 'W3', 'W4', 'T2', 'T3', 'T4', 'B2', 'B3', 'B4', 'W6', 'W7', 'J1', 'J1']
    state = rig({'p0': ['W8'], 'p1': waiting}, [('p0', 'peng', ['W8'] * 3, 'p3')])
    result.append(('added_gang_choice', deepcopy(state), 'added_gang'))
    state = apply(state, 'added_gang')
    result.append(('rob_kong_window', deepcopy(state), 'hu'))
    result.append(('own_added_gang', apply(state, 'pass'), 'discard'))
    result.append(('rob_kong_finished', apply(state, 'hu'), None))
    state = rig({'p0': ['W8'], 'p1': waiting})
    state = discard_code(state, 'W8')
    result.append(('hu_window', deepcopy(state), 'hu'))
    result.append(('discard_hu_finished', apply(state, 'hu'), None))
    state = rig({'p0': [*waiting, 'W8']})
    state['drawn_tile_id'] = next(t['id'] for t in state['hands']['p0'] if t['code'] == 'W8')
    result.append(('self_draw_choice', deepcopy(state), 'hu'))
    result.append(('self_draw_finished', apply(state, 'hu'), None))
    return result

"""Deterministic positions shared by the two-game compact audit and sampler."""
from copy import deepcopy
import random

from app.games.doudizhu import Doudizhu, build_deck
from app.games.junqi import Junqi


def seats(count):
    return [dict(player_id=f'p{i}', role='ai', participant_kind='bound_machine',
                 display_name=f'玩家{i}', seat_index=i, token=f'P{i + 1}')
            for i in range(count)]


def doudizhu_position(ranks):
    game = Doudizhu(random.Random(37))
    state = game.initialize_for_first_player(seats(3), 'p0')
    deck = build_deck()
    hand = []
    for rank in ranks:
        card = next(c for c in deck if c['rank'] == rank)
        deck.remove(card)
        hand.append(card)
    state['cards'] = dict(deck=[], discard=[], hands={
        'p0': hand, 'p1': deck[:17], 'p2': deck[17:34],
    })
    # The stress fixtures use a legal landlord-sized hand and a disjoint deck.
    state['bottom_cards'] = deepcopy(hand[-3:])
    state['bottom_revealed'] = True
    state['landlord_player_id'] = 'p0'
    state['roles_by_player'] = dict(p0='landlord', p1='farmer', p2='farmer')
    state['base_score'] = state['multiplier'] = 1
    state['trick'] = game._new_trick(1, 'p0')
    state['flow']['phase'] = 'playing'
    return state


STRESS_HANDS = {
    'five_quads': '33334444555566667777',
    'four_triples_wings': '333444555666778899TT',
    'six_triples': '33344455566677788899',
    'three_quads_singles': '3333444455556789TJQK',
    'ambiguous_four_pairs': '33332222444455556666',
    'long_straights': '33445566778899TTJJQQ',
}


def doudizhu_cases(random_count=24):
    game = Doudizhu(random.Random(20261003))
    bidding = game.initialize_for_first_player(seats(3), 'p0')
    yield 'bidding', bidding
    higher = deepcopy(bidding)
    higher['bidding'].update(highest_score=2, highest_bidder_id='p1')
    yield 'bidding_above_2', higher
    stress = [(name, ['10' if r == 'T' else r for r in ranks])
              for name, ranks in STRESS_HANDS.items()]
    stress.append(('joker_bomb_wings', list('333344445556667789') +
                   ['small_joker', 'big_joker']))
    for name, ranks in stress:
        state = doudizhu_position(ranks)
        yield name, state
    for seed in range(random_count):
        game = Doudizhu(random.Random(seed))
        state = game.initialize_for_first_player(seats(3), 'p0')
        state = game.apply_action(state, {'action': 'bid', 'action_id': 'bid:3'}, seats(3)[0]).state
        yield f'deal_{seed}_lead', state
        # Opponent's actual legal lead creates a real follow/pass position.
        leader = deepcopy(state)
        leader['turn_player_id'] = 'p2'
        actions = game.legal_actions_for(leader, 'p2')
        lead = actions[seed % len(actions)]
        following = game.apply_action(leader, lead, seats(3)[2]).state
        yield f'deal_{seed}_follow', following


def junqi_position(pieces=None):
    state = Junqi().initialize_for_first_player(seats(2), 'p0')
    state.update(phase='play', setup_ready={'p0': True, 'p1': True})
    if pieces is not None:
        state['board'] = {square: None for square in state['board']}
        for square, (color, rank) in pieces.items():
            state['board'][square] = dict(color=color, rank=rank)
    return state


def junqi_cases():
    yield 'initial_play', junqi_position()
    for rank, name in [(9, 'engineer_turn'), (8, 'rail_straight')]:
        yield name, junqi_position({'e2': ('b', rank), 'a5': ('r', 11), 'e5': ('r', 8)})
    yield 'road_camp_hq', junqi_position({
        'c3': ('b', 8), 'b3': ('r', 7), 'd3': ('b', 7),
        'b1': ('b', 8), 'a1': ('b', 10), 'd1': ('b', 11), 'd12': ('r', 11),
    })
    yield 'road_adjacency', junqi_position({'c4': ('b', 8), 'd12': ('r', 11)})

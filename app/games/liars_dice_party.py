"""Authoritative party counting and local-score settlement, shared by all clients.

Never reads wallets. Scores are persisted as integer half-points; the outward
score fields are compatibility projections, never used for repeated arithmetic.
"""
from copy import deepcopy

BOOL_OPTIONS = ('literal_dice', 'cup_penalty', 'extreme_penalty', 'allow_surrender',
                'surrender_no_reroll', 'dark_rule', 'companion_penalty', 'rush_open')
OPTION_KEYS = ('mode', *BOOL_OPTIONS, 'team_mode', 'random_teams', 'opening_five')


def modern(state):
    return state.get('party_rules_version', 1) >= 2


def cup_kind(dice):
    if len(dice) != 5:
        return None
    if len(set(dice)) == 1:
        return 'full_cup'
    natural = set(dice) - {1}
    return 'wild_cup' if 1 in dice and len(natural) == 1 else None


def count_cup(dice, face, exact, bonuses):
    wild = not exact and face != 1
    count = sum(value == face or (wild and value == 1) for value in dice)
    bonus = 0
    if bonuses and len(dice) == 5:
        if all(value == face for value in dice):
            bonus = 2
        elif wild and face in dice and count == 5:
            bonus = 1
    return count + bonus, bonus


def count_bid(state, bid):
    bonuses = modern(state) and not state.get('literal_dice')
    counts, additions = {}, {}
    for owner, dice in state['dice_by_player'].items():
        counts[owner], additions[owner] = count_cup(dice, bid['face'], bid.get('exact', False), bonuses)
    return sum(counts.values()), {k:v for k,v in additions.items() if v}


def can_reroll(state, owner):
    return (modern(state) and not state.get('literal_dice')
            and state.get('flow', {}).get('phase') == 'bidding'
            and not state.get('current_bid')
            and not state.get('rerolled_cups', {}).get(owner)
            and len(set(state.get('dice_by_player', {}).get(owner, []))) == 5)


def settle(game, state, loser, winner, *, hits=(), surrender=False):
    """Charge each scoring unit once, after combining all hits and companion.

    Companion seat is a public one-based roster index selected by rejection
    sampling a virtual d6. Same-team selection is the same scoring unit (self).
    Surrender is always 0.5, with no challenge-only or companion multipliers.
    """
    def unit(pid):
        return game.team_for(state, pid) or pid

    old_scores = game.party_scores(state)
    units = state.setdefault('score_half_units', {
        unit(pid): int(score * 2) for pid, score in old_scores.items()})
    base = 1 if surrender else 2
    multiplier = 2 ** len(hits)
    charges = {unit(loser): {'player_id':loser, 'hits':list(hits), 'raw_multiplier':multiplier}}
    companion = None
    if not surrender and state.get('companion_penalty'):
        order = game._active_ids(state)
        # Rejection is unbiased for 2..6 safe participant seats. A broken RNG
        # fails the whole transaction instead of hanging or partially scoring.
        rolls = []
        for _ in range(64):
            roll = game._roll(1)[0]
            rolls.append(roll)
            if roll <= len(order):
                break
        else:
            raise ValueError('陪扣随机源未产生有效座位，请重试本轮行动')
        picked = order[roll - 1]
        same = unit(picked) == unit(loser)
        companion = {'rolls':rolls, 'seat':roll, 'player_id':picked, 'same_unit':same}
        if same:
            charges[unit(loser)]['hits'].append('companion_self')
            charges[unit(loser)]['raw_multiplier'] *= 2
        else:
            charges[unit(picked)] = {'player_id':picked, 'hits':['companion'], 'raw_multiplier':multiplier}
    penalties = []
    for key, charge in charges.items():
        raw = charge['raw_multiplier']
        applied = min(raw, 4)
        deduction = min(base * applied, 8)
        units[key] -= deduction
        penalties.append({**charge, 'team_id':game.team_for(state, charge['player_id']),
                          'base_half_units':base, 'multiplier':applied, 'cap':4,
                          'capped':raw > applied, 'deduction_half_units':deduction,
                          'score_delta':-deduction / 2, 'score_after':units[key] / 2})
    state['scores'] = {pid:units[unit(pid)] / 2 for pid in state['participant_order']}
    if state.get('teams'):
        state['team_scores'] = {team:units[team] / 2 for team in state['teams']}
    state['records'][loser]['losses'] += 1
    state['records'][winner]['wins'] += 1
    primary = penalties[0]
    return {'penalties':penalties, 'companion':companion,
            'score_delta':primary['score_delta'], 'score_after':primary['score_after'],
            'loser_team_id':game.team_for(state, loser)}


def history_result(result):
    # Reveals belong to the current result. History does not duplicate all cups.
    return {k:deepcopy(v) for k,v in result.items()
            if k not in ('revealed_dice_by_player', 'summary', 'result_summary')}


def final_result(game, state, reason='session_ended'):
    """Snapshot the existing ledger, never score an unfinished round or pay chips."""
    teams = state.get('teams') or {}
    members = teams or {pid: [pid] for pid in state['participant_order']}
    scores = game.party_scores(state)
    units = state.get('score_half_units', {})
    values = {key: units.get(key, int(scores[people[0]] * 2))
              for key, people in members.items()}
    completed = max(len(state.get('round_results', [])), sum(
        record.get('losses', 0) for record in state.get('records', {}).values()))
    rankings = []
    previous = None
    rank = None
    for index, key in enumerate(sorted(values, key=values.get, reverse=True), 1):
        if completed and values[key] != previous:
            rank = index
        rankings.append({'rank': rank, 'score': values[key] / 2,
                         'player_ids': list(members[key]),
                         **({'team_id': key} if teams else {'player_id': key})})
        previous = values[key]
    leaders = [row for row in rankings if row['rank'] == 1]
    verdict = ('未结算回合，无胜负' if not completed else
               ('并列第一' if len(leaders) > 1 else '排名已确定'))
    result = {
        # Compatibility sentinel for the shared terminal/payout framework only.
        # Rankings below define the outcome; explicit deltas settle paid tables.
        'draw': True, 'mode': 'party', 'reason': reason,
        'outcome': 'ranked' if completed else 'no_contest',
        'completed_rounds': completed, 'rankings': rankings,
        'winner_player_ids': [pid for row in leaders for pid in row['player_ids']],
        'scores': {pid: values[key] / 2 for key, people in members.items() for pid in people},
        'records': deepcopy(state.get('records', {})),
        'result_text': f'酒桌局结束 · 完成 {completed} 轮 · {verdict}。',
    }
    if teams:
        result.update(team_scores={key: value / 2 for key, value in values.items()},
                      winner_team_ids=[row['team_id'] for row in leaders])
    return result


def platform_deltas(result, participants, stake):
    """One table, one bounded zero-sum payout; NPCs never own a wallet."""
    deltas = {p['player_id']: 0 for p in participants}
    if not result.get('completed_rounds'):
        return deltas
    real = sorted((p for p in participants if p.get('participant_kind') != 'system_npc'),
                  key=lambda p: p.get('seat_index', 0))
    leaders = set(result.get('winner_player_ids', []))
    winners = [p['player_id'] for p in real if p['player_id'] in leaders]
    losers = [p['player_id'] for p in real if p['player_id'] not in leaders]
    if not winners or not losers:
        return deltas
    share, remainder = divmod(stake * len(losers), len(winners))
    deltas.update({pid: -stake for pid in losers})
    deltas.update({pid: share + int(i < remainder) for i, pid in enumerate(winners)})
    return deltas

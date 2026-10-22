"""Four-color-only wire helpers. Inputs to delta() are public projections.

Use Duel's transactional per-viewer v2 cursor; no additional root tool fields.
"""
from copy import deepcopy
from .cards import RED_NAMES, BLACK_NAMES

FORMAT = (
    'from_revision must equal your retained r; otherwise state(full_state=true) resyncs rules and state. '
    'events=[actor,action,patch]. Patch keys replace public board fields; '
    'discards+ appends IDs; melds+ maps player IDs to new exposed groups. '
    'private.+/- adds/removes own physical IDs; private.legal_actions replaces '
    'the complete current action list ([] means wait). Other private fields replace. '
    'closed_pairs are your sealed pairs; waiting_codes are your fixed Qunhu waits. Never infer other hands. '
    'Submit {action:move,room_id,revision:r,move:{action:act,action_id:ID}} using '
    'an exact ID from legal_actions. R/Y/G/W=红/黄/绿/白; ranks 0..6: '
    f'R/Y={"".join(RED_NAMES)}, G/W={"".join(BLACK_NAMES)}; '
    '-1..4=physical copy.'
)


def private(game, state, pid):
    return {'hand': sorted(state['hands'][pid]),
            'legal_actions': [o['id'] for o in game._options(state, pid)],
            **(game.private_extra(state, pid) if hasattr(game, 'private_extra') else {})}


def delta(value, old):
    patch = {k: deepcopy(v) for k, v in value.items() if old.get(k) != v}
    before, after = old.get('discards', []), value.get('discards', [])
    if 'discards' in patch and after[:len(before)] == before:
        patch.pop('discards')
        patch['discards+'] = after[len(before):]
    if 'melds' in patch:
        earlier = old.get('melds', {})
        if all(groups[:len(earlier.get(p, []))] == earlier.get(p, [])
               for p, groups in value['melds'].items()):
            patch.pop('melds')
            patch['melds+'] = {p: groups[len(earlier.get(p, [])):]
                               for p, groups in value['melds'].items()
                               if groups != earlier.get(p, [])}
    return patch


def snapshot(game, room, pid, full=True):
    return {'room_id': room['room_id'], 'game': game.game_type,
            'rules_text': game.rules_text,
            **({'move_format': FORMAT} if full else {}),
            'viewer_player_id': pid,
            'participants': [{k: p.get(k) for k in ('player_id', 'display_name', 'seat_index')}
                             for p in room['participants']],
            'board_state': game.public_state(room['board_state'], room['participants']),
            'private_state': private(game, room['board_state'], pid)}

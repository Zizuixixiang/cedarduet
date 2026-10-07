"""Explicit legacy-game resync only; never used by bootstrap or turn replies.

Resync must work without the initial bootstrap. Keep static maps and rule
parameters alongside the live position. Private hands and legal actions are
opaque to this layer and are never shortened.
"""
from copy import deepcopy
import json

from . import database, framework
from .games import get_game
from .games.tools import public_card_state


# 原协议（非 MCP v2）游戏的显式 resync 白名单；gem_merchant 为后加入的原协议游戏。
LEGACY_GAMES = frozenset('''aeroplane_chess banqi blackjack tictactoe texas_holdem
train_cards gomoku go gandengyan guandan othello connect4 checkers chess
chinese_checkers dots_boxes doudizhu liars_dice mahjong yahtzee uno jungle junqi
xiangqi zhajinhua gem_merchant monopoly_plus'''.split())

# No static/roster deletion has a verified repeatable mid-game query contract.
# Retain the empty audit list so tests reject new omissions without review.
STATIC_FIELDS = {}

# Older shared snapshot hooks also omitted bootstrap-only map data. Restore
# those maps here from the canonical safe projection; never change the shared
# hooks, bootstrap, turn replies, or their compact legal-action representations.
RESTORE_MAP_FIELDS = {
    'aeroplane_chess': ('path_mappings', 'ring_length', 'home_lane_length', 'finish_route_step'),
    'chinese_checkers': ('nodes', 'camps'),
}


def project_full_state(room: dict, player_id: str) -> dict:
    """Pure output projection except for a read-only public battle-log lookup."""
    snapshot = framework.project_mcp_snapshot_for_viewer(room, player_id)
    game_type = room['game_type']
    if game_type not in LEGACY_GAMES:
        return snapshot
    game = get_game(game_type)
    snapshot['rules_text'] = game.rules_text
    snapshot['move_format'] = getattr(game, 'mcp_move_format', game.move_format)
    if not room['board_state']:
        return snapshot
    board = snapshot['board_state']
    if game_type in RESTORE_MAP_FIELDS:
        public = framework.project_room_for_viewer(room, player_id)['board_state']
        for key in RESTORE_MAP_FIELDS[game_type]:
            if key in public:
                board[key] = deepcopy(public[key])
    for participant, source in zip(snapshot['participants'], room['participants']):
        if not source.get('active', True):
            participant['active'] = False

    # These zones contain only previously played public cards. Never copy the
    # cards container (which also holds opponents' hands and future deck order).
    if game_type in {'doudizhu', 'gandengyan', 'uno'}:
        board['discard'] = public_card_state(room['board_state'])['discard']
    elif game_type == 'guandan':
        game = get_game(game_type)
        board['played_cards'] = [
            game._public_card(card, str(room['board_state'].get('level_rank', '2')))
            for card in room['board_state'].get('played_cards', [])
        ]
    elif game_type == 'junqi':
        # The engine keeps only 40 public actions. The persisted PUBLIC referee
        # events retain older battles, including ranks already revealed by death.
        conn = database.connect()
        try:
            rows = conn.execute('''SELECT revision_at_send, move_payload
                FROM room_messages WHERE room_id=? AND event_type='result'
                  AND visible_to_json IS NULL AND revision_at_send<=?
                  AND json_type(move_payload, '$.junqi_delta.battle')='object'
                ORDER BY id''', (room['room_id'], room['revision'])).fetchall()
        finally:
            conn.close()
        board['battles'] = [
            {'revision': row['revision_at_send'],
             **json.loads(row['move_payload'])['junqi_delta']}
            for row in rows
        ]
    if room['status'] in {'finished', 'archived'}:
        for key in ('winner', 'winner_player_id', 'result', 'terminal_reason'):
            snapshot[key] = deepcopy(room.get(key))
    return snapshot


def read_full_state_text(room: dict, player_id: str) -> list[dict]:
    """Consume only the prefix covered by this snapshot, returning every word.

    Ordinary state's reader is unchanged. The full-state branch acknowledges
    old board mutations while delivering chat, move annotations and notices
    here exactly once. A concurrent newer revision and everything after it stay
    unread. The one-time bootstrap claim and independent Web cursor are untouched.
    """
    with database.write_transaction() as conn:
        cursor = conn.execute('''SELECT last_event_id FROM room_event_cursors
            WHERE room_id=? AND player_id=?''', (room['room_id'], player_id)).fetchone()
        last_id = cursor['last_event_id'] if cursor else 0
        rows = conn.execute('''SELECT * FROM room_messages
            WHERE room_id=? AND id>? ORDER BY id''',
            (room['room_id'], last_id)).fetchall()
        events = []
        for row in rows:
            if row['revision_at_send'] > room['revision']:
                break
            last_id = row['id']
            if row['sender_player_id'] == player_id:
                continue
            visible = row['visible_to_json']
            if visible is not None and player_id not in json.loads(visible):
                continue
            event = framework._project_event_for_viewer(
                room, framework._timeline_entry(row, room), player_id)
            if event and event.get('text'):
                events.append({'name': event['sender']['name'],
                               'message': event['text']})
        conn.execute('''INSERT INTO room_event_cursors
                (room_id, player_id, last_event_id, updated_at) VALUES (?, ?, ?, ?)
            ON CONFLICT(room_id, player_id) DO UPDATE SET
                last_event_id=excluded.last_event_id''',
            (room['room_id'], player_id, last_id, '1970-01-01T00:00:00+00:00'))
        return events


def full_state_response(room: dict, player_id: str) -> dict:
    payload = {'ok': True, 'status': room['status'], 'full_state': True,
               'snapshot': project_full_state(room, player_id)}
    events = read_full_state_text(room, player_id)
    if events:
        payload['events'] = events
    return payload

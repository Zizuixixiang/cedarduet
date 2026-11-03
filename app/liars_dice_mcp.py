"""Party-dice incremental wire format. Web and original/classic wire stay intact.

One transaction owns the event cursor and viewer-only private baseline. Public
events preserve intervening bids/results even if a client skips several turns.
"""
from copy import deepcopy
import json

from .database import write_transaction, decode_room
from .games import get_game
from .games.mcp_incremental import changed


GUIDE = ('保留上下文；r为revision。events依次应用：[玩家,move]或{round_result:权威结算}；'
         'delta只替换变化字段，null清空；seats=[队,1起座号,成员或null]；private仅己方变化。'
         '无变化只返回r/wait；full_state:true显式恢复完整安全快照。'
         '下一轮清空叫法；选座仅本人choose_team_seat，20秒后房主start，未选者随机补位。'
         '房规及可选动作按需guide，不依赖常驻legal_actions。')


def safe_state(room, pid):
    from . import framework as f, liars_dice_setup as setup
    game = get_game('liars_dice')
    state = room['board_state']
    meta = {'status':room['status'], 'next':room.get('current_player_id')}
    roster = {p['player_id']: {k:p.get(k) for k in ('display_name','role','participant_kind','seat_index','avatar')}
              for p in room['participants']}
    meta['players'] = roster
    if 'dice_counts' not in state:
        selection = setup.public_selection(state) if setup.has_selection(room) else None
        meta['selection'] = selection
        meta['room_ready'] = room.get('room_ready', False)
        return meta, {}, None
    public = game.public_state(state, room['participants'])
    meta.update(phase=state['flow']['phase'], round=state['flow']['round_number'],
                teams=public.get('teams'), max_bid_quantity=public['max_bid_quantity'], selection=None)
    viewer = f._participant_by_id(room, pid)
    private = game.private_state(state, viewer, room['participants'])
    return meta, private, public


def response(room_id, pid, *, full=False, source_room=None, **unused):
    from . import framework as f
    from .full_state import project_full_state
    with write_transaction() as conn:
        f._maintain_rooms(conn, room_id)
        row = conn.execute('SELECT * FROM rooms WHERE room_id=?', (room_id,)).fetchone()
        if row is None:
            raise f.DuelError('房间不存在',404)
        room = f._decorate(decode_room(row,conn))
        f._assert_participant(room,pid)
        viewer = f._participant_by_id(room,pid)
        if viewer.get('join_status') == 'left':
            return {'r':room['revision'], 'status':'left'}
        row = conn.execute('SELECT context FROM mcp_minimal_contexts WHERE room_id=? AND player_id=?',(room_id,pid)).fetchone()
        ctx = json.loads(row[0]) if row else {}
        boot = ctx.get('dice_protocol') != 1
        now, private, public = safe_state(room,pid)
        out = {'r':room['revision']}
        cursor = ctx.get('event_id',0)
        rows = conn.execute('''SELECT * FROM room_messages WHERE room_id=? AND id>?
            AND (visible_to_json IS NULL OR EXISTS(SELECT 1 FROM json_each(visible_to_json) WHERE value=?)) ORDER BY id''',
            (room_id,cursor,pid)).fetchall()
        events = []
        for row in rows:
            e = f._project_event_for_viewer(room, f._timeline_entry(row,room), pid)
            if e is None:
                continue
            move = e.get('move') or {}
            actor = e['sender']['player_id']
            if not (boot or full):
                if e['event_type'] == 'result' and 'round_result' in move:
                    events.append({'round_result':move['round_result']})
                elif e['event_type'] == 'move' and move.get('action') not in ('challenge','surrender'):
                    events.append([actor,move])
            if e.get('text') and e['event_type'] != 'result' and actor != pid:
                events.append({'actor':actor,'message':e['text']})
        if full:
            out.update(full_state=True, snapshot=project_full_state(room,pid))
        elif boot:
            game = get_game('liars_dice')
            board = deepcopy(public or {})
            for key in ('round_results','records','last_action_note','bid_ranges'):
                board.pop(key,None)
            out.update(protocol=2, bootstrap=True, protocol_guide=GUIDE,
                       room={'room_id':room_id, 'game_type':'liars_dice', 'game_options':room['game_options'],
                             **now, 'board_state':board, 'private_state':private})
            if room.get('invite_code'):
                out['room'].update(invite_code=room['invite_code'], invite_link=room['invite_link'])
            out['room']['rules_text'] = game.npc_compact_rules(room['board_state'], viewer, room['participants'])
        else:
            delta = changed(now,ctx.get('public',{}))
            old_selection = ctx.get('public',{}).get('selection')
            new_selection = now.get('selection')
            if old_selection and new_selection:
                delta.pop('selection',None)
                seats = [[team,i+1,p] for team, people in new_selection['seats'].items()
                         for i,p in enumerate(people) if old_selection['seats'][team][i] != p]
                if seats:
                    delta['seats'] = seats
                if old_selection.get('can_start') != new_selection.get('can_start'):
                    delta['can_start'] = new_selection['can_start']
            if 'players' in delta:
                delta['players'] = changed(now['players'],ctx.get('public',{}).get('players',{}))
            if delta:
                out['delta'] = delta
            patch = changed(private,ctx.get('private',{}))
            if patch:
                out['private'] = patch
            if now['status'] in ('finished','archived') and ctx.get('public',{}).get('status') != now['status']:
                out['result'] = deepcopy(room.get('result'))
        if events:
            out['events'] = events
        if room['status'] == 'playing' and not f.participant_can_act(room,pid):
            out['wait'] = room.get('current_player_id')
        newest = conn.execute('SELECT COALESCE(MAX(id),0) FROM room_messages WHERE room_id=?',(room_id,)).fetchone()[0]
        ctx = dict(v=2,dice_protocol=1,revision=room['revision'],public=now,private=private,event_id=newest)
        conn.execute('INSERT OR REPLACE INTO mcp_minimal_contexts VALUES(?,?,?)',(room_id,pid,json.dumps(ctx)))
        conn.execute('''INSERT INTO room_event_cursors(room_id,player_id,last_event_id,updated_at,mcp_bootstrapped)
            VALUES(?,?,?,'1970-01-01T00:00:00+00:00',1) ON CONFLICT(room_id,player_id)
            DO UPDATE SET last_event_id=excluded.last_event_id,mcp_bootstrapped=1''',(room_id,pid,newest))
    from .achievements import filter_unlocks
    unlocks = filter_unlocks((source_room or {}).get('achievement_unlocks',[]),'ai',pid)
    if unlocks:
        out['unlocks'] = unlocks
    return out


def has_updates(room, pid):
    from .database import connect
    from . import framework as f, liars_dice_setup as setup
    conn = connect()
    try:
        row = conn.execute('SELECT context FROM mcp_minimal_contexts WHERE room_id=? AND player_id=?',
                           (room['room_id'],pid)).fetchone()
    finally:
        conn.close()
    ctx = json.loads(row[0]) if row else {}
    if ctx.get('revision') != room['revision']:
        return True
    if setup.has_selection(room) and ctx.get('public',{}).get('selection') != setup.public_selection(room['board_state']):
        return True
    return f.has_new_room_events(room['room_id'],pid)

"""Stateful, opt-in MCP v2 wire protocol. Never used by Web or NPC policy.

The response revision, public cursor and private baseline are read/advanced in
one SQLite transaction. Room messages remain canonical and are never rewritten.
"""
from copy import deepcopy
import json

from .database import write_transaction
from .framework import DuelError
from .games import get_game
from .games.mcp_incremental import changed, table_patch

GAMES = frozenset(('monopoly', 'rummikub', 'bomb_plane', 'carcassonne'))
VERSION = 2


def enabled(room):
    return (room.get('game_type') in GAMES
            and room.get('status') in ('playing', 'finished', 'archived')
            and bool(room.get('board_state')))


def baseline(game, state):
    if game.game_type == 'monopoly':
        return game._public_delta(state)
    if game.game_type == 'rummikub':
        return {'rummikub_delta': dict(melds=state['melds'], meld_kinds=state['meld_kinds'],
                opened=state['opened'], hand_counts={p:len(h) for p,h in state['hands'].items()},
                pool_count=len(state['pool']), result=state['result'])}
    if game.game_type == 'carcassonne':
        return {'carcassonne_delta': {k:state[k] for k in ('scores','supply')}}
    return {}


def private_baseline(name, state, pid):
    if name == 'rummikub':
        return {'hand': state['hands'][pid]}
    if name == 'bomb_plane':
        return {'planes': state['planes'][pid]}
    if name == 'monopoly':
        p = next(p for p in state['players'] if p['player_id'] == pid)
        return {'jail_cards':len(p['jail_cards'])}
    return {}


COMMON = ('MCP v2: retain context. Ordinary reply r is the authoritative revision; '
          'send it as revision in the next move (old request key remains valid). '
          'Events are ordered, exactly once per consumed cursor; apply ALL, including your own. '
          'Missing delta keys mean unchanged; null clears. No events means no new actions. '
          'wait names the current actor only when you cannot act. '
          'full_state=true replaces dynamic state, retaining bootstrap rules/static data; '
          'covered actions are skipped atomically, but unread text is delivered once on the next ordinary response. '
          'Unknown/v1 contexts automatically get this v2 bootstrap. '
          'private is only your delta. Notifications appear once when new; rooms/chips retain unread access.')
FORMATS = {
    'monopoly': ('events=[actor,action,delta?]. Increment action_seq once per action (including resign/leave). '
        'delta: p=[id,cash,position,bankrupt,jailed,jail_turns] rows; t=[id,owner,level,mortgaged] rows; '
        'next=turn_player_id; other fields replace. Static tiles/rents are in bootstrap. '
        'private.jail_cards replaces your count. Normal choices derive from phase and ledger; '
        'legal_actions in the snapshot are examples at that revision, not permanent choices.'),
    'rummikub': ('events=[actor,draw|pass] or [actor,meld,{table_patch,joker_roles?}]. '
        'draw adds 1 to actor hand_count and subtracts 1 from pool_count; never reveals their tile. '
        'meld removes newly tabled IDs from actor hand_count, sets opened=true. '
        'table_patch={size,set:[[index,IDs,kind]]}; resize then set. '
        'draw/meld clears blocked players; pass adds actor. Advance to next active seat. '
        'private.+/- adds/removes hand IDs. For your successful submitted meld first remove '
        'the submitted table IDs from your hand; private.- only reports any additional difference. '
        'No automatic suggestion. Submit draw, pass only if pool empty, or meld with FULL final melds '
        'and optional kinds (group/run); opening >=30 from own rack, old table tiles exactly once.'),
    'bomb_plane': ('events=[actor,cell,miss|hit|head]. Alternate attackers; 3 heads wins. '
        'setup flag is open/locked; absent setup means attack phase unless terminal status. private.planes replaces your layout only after changes. '
        'Setup moves: place(head,direction N/E/S/W), set_layout(planes), undo,clear,shuffle,ready,auto_setup. '
        'Attack move={action:attack,cell:A1..J10}; no repeated cells.'),
    'carcassonne': ('events=[actor,tile,x,y,rotation,meepleRegionOrNull,nextTileOrNull,effects?]. '
        'Add tile/meeple; scoring.returned removes meeples at [x,y,player]; scores/supply merge. '
        'deck_count decrements by 1 plus discarded count when next tile exists; terminal effects may override it. '
        'Advance to next active seat. move={action:place,x,y,rotation:0..3,meeple:null|region}. '
        'No default placements. Query via state(move={query:placements,x,y,rotation?,meeple?}); '
        'returns candidate validity and up to 8 nearby legal placements with available regions. '
        'state(move={query:placements,all:true}) returns all. Queries do not consume events. '
        'Use revision=r from your last consumed response; query has its own revision for stale detection.'),
}


def snapshot(room, pid, full):
    from . import framework as f
    room = f._decorate(room)
    value = (f.project_mcp_snapshot_for_viewer(room, pid) if full
             else f.project_mcp_room_for_viewer(room, pid))
    if full:
        from .mcp_recovery import recovery_snapshot
        return recovery_snapshot(value, pid, FORMATS[room['game_type']])
    value['rules_text'] = room['rules_text']
    value['board_state'].pop('delta_format', None)
    if not full:
        value['move_format'] = FORMATS[room['game_type']]
    if room['game_type']=='monopoly':
        value['action_formats'] = {
            'no_args':['roll','buy','auction','end_turn','pass_bid','pay_bail','use_jail_card','bankrupt'],
            'tile_id':['build','sell_building','mortgage','redeem'],
            'bid':{'amount':'integer > auction.bid, <= own cash'},
            'respond_trade':{'accept':'boolean'},
            'propose_trade':{'to':'partner ID','give_cash':0,'take_cash':0,'give_tiles':[],'take_tiles':[]},
            'all':'Every move includes action and action_seq from bootstrap + count of action events.'}

    private = value['private_state']
    private.pop('suggested_move', None)
    if room['game_type'] == 'carcassonne':
        private.pop('placements', None)
        private.pop('placement_regions', None)
        private['legal_action_spec'] = {'action':'place','x':'integer','y':'integer',
                                      'rotation':[0,1,2,3],'meeple':'null or available region ID'}
    if room['game_type'] == 'rummikub':
        private['legal_action_spec'] = {'action':'meld','melds':'FULL final table of tile IDs',
                                      'kinds':'optional group|run per meld'}
    return value


def encode_result(name, raw, prior, actor, action):
    """Encode canonical public results; no hidden state is accepted here."""
    if name == 'bomb_plane':
        s = raw['bomb_plane_shot']
        return [s['player_id'], s['cell'], s['result']]
    if name == 'monopoly':
        value = raw['monopoly']; old = prior.get('monopoly', {})
        delta = changed(value, old)
        for key in ('note','action_seq'):
            delta.pop(key, None)
        for key, alias, fields in (
            ('players','p',('player_id','cash','position','bankrupt','jailed','jail_turns')),
            ('tiles','t',('id','owner','level','mortgaged')),
        ):
            before = {p[fields[0]]:p for p in old.get(key, [])}
            rows = [[p[k] for k in fields] for p in value[key] if p != before.get(p[fields[0]])]
            delta.pop(key, None)
            if rows: delta[alias] = rows
        if 'turn_player_id' in delta: delta['next'] = delta.pop('turn_player_id')
        if 'last_card_events' in delta:
            delta['last_card_events'] = [{k:v for k,v in c.items() if k not in ('event_id','action_seq')}
                                         for c in delta['last_card_events']]
        return [actor, action, delta] if delta else [actor, action]
    if name == 'rummikub':
        value = raw['rummikub_delta']; old = prior.get('rummikub_delta', {})
        last = value['last_action']; actor = last['player_id']; action = last['action']
        if action != 'meld': return [actor, action]
        from .games.rummikub import meld_info
        patch = table_patch(value['melds'], value['meld_kinds'], old.get('melds', []), old.get('meld_kinds', []))
        delta = {'table_patch':patch}
        roles = lambda v: {j:r for m,k in zip(v.get('melds',[]),v.get('meld_kinds',[]))
                          for j,r in meld_info(m,k)['joker_roles'].items()}
        if roles(value) != roles(old): delta['joker_roles'] = roles(value)
        return [actor, action, delta]
    value = raw['carcassonne_delta']; old = prior.get('carcassonne_delta', {})
    p = value['placed']
    effects = {k:deepcopy(value[k]) for k in ('scoring','discarded') if value.get(k)}
    for k in ('scores','supply'):
        update = changed(value[k], old.get(k, {}))
        if update: effects[k] = update
    if value['current_tile'] is None: effects['deck_count'] = value['deck_count']
    event = [p['player_id'],p['tile'],p['x'],p['y'],p['rotation'],
             p.get('meeple',{}).get('region'),value['current_tile']['tile'] if value['current_tile'] else None]
    return event + [effects] if effects else event


def response(room_id, pid, *, consume=False, full=False, submitted=None, source_room=None):
    from . import framework as f
    from .main import _participant_response_due, _terminal_fields
    with write_transaction() as conn:
        f._maintain_rooms(conn, room_id)
        row = conn.execute('SELECT * FROM rooms WHERE room_id=?',(room_id,)).fetchone()
        if row is None: raise DuelError('房间不存在',404)
        room = f.decode_room(row,conn); f._assert_participant(room,pid)
        game = get_game(room['game_type']); name = game.game_type; state = room['board_state']
        viewer = next(p for p in room['participants'] if p['player_id']==pid)
        if viewer.get('join_status') == 'left':
            return {'r':room['revision'], 'status':'left'}
        saved = conn.execute('SELECT context FROM mcp_minimal_contexts WHERE room_id=? AND player_id=?',(room_id,pid)).fetchone()
        ctx = json.loads(saved[0]) if saved else {}
        boot = ctx.get('v') != VERSION
        covered = ctx.get('snapshot_event_id', 0)
        consume = consume or full or boot or bool(covered) or _participant_response_due(room,pid)
        out = {'r':room['revision']}
        events = []
        if consume:
            cursor = conn.execute('SELECT last_event_id FROM room_event_cursors WHERE room_id=? AND player_id=?',(room_id,pid)).fetchone()
            rows = conn.execute('''SELECT * FROM room_messages WHERE room_id=? AND id>?
                AND (visible_to_json IS NULL OR EXISTS(SELECT 1 FROM json_each(visible_to_json) WHERE value=?)) ORDER BY id''',
                (room_id,cursor[0] if cursor else 0,pid)).fetchall()
            prior = ctx.get('public',{})
            pending = None
            repair = False
            first_unread_text = None
            for row in rows:
                e = f._project_event_for_viewer(room,f._timeline_entry(row,room),pid)
                if e is None: continue
                kind = e['event_type']; move = e.get('move') or {}; actor = e['sender']['player_id']
                if e.get('text') and kind != 'result' and actor != pid:
                    if full:
                        if first_unread_text is None: first_unread_text = row['id']
                    elif row['id'] <= covered:
                        events.append({'actor':actor,'message':e['text']})
                # A snapshot covers actions, never the text accompanying them.
                # Keep the canonical cursor before unread text and skip only
                # the covered actions when the next ordinary response reads it.
                if row['id'] <= covered:
                    continue
                if kind == 'result' and e.get('is_public') and game.mcp_event_key in move:
                    if not (boot or full):
                        matching = pending and pending['revision_at_send']==e['revision_at_send'] and pending.get('is_public') and e.get('is_public')
                        source = pending if matching else None
                        action = source['move']['action'] if source else 'update'
                        owner = source['sender']['player_id'] if source else actor
                        encoded = encode_result(name,move,prior,owner,action)
                        if matching and any(item is pending['_wire'] for item in events):
                            pending['_wire'][:] = encoded
                        else:
                            events.append(encoded)
                    prior = move
                    pending = None
                elif kind in ('move','resign','leave') and not (boot or full):
                    action = move.get('action',kind)
                    wire = [actor,action]
                    # Setup layout is private. Its own layout replacement below
                    # is authoritative; the public phase/ready flags suffice.
                    if name != 'bomb_plane' or action not in ('place','set_layout','undo','clear','shuffle','ready','auto_setup'):
                        events.append(wire)
                    e['_wire'] = wire; pending = e
                    repair |= kind in ('resign','leave')
                if e.get('text') and kind != 'result' and actor != pid and not full:
                    events.append({'actor':actor,'message':e['text']})
            now_private = private_baseline(name,state,pid)
            if boot:
                out.update(protocol=VERSION, protocol_guide=COMMON+' '+FORMATS[name])
                out['bootstrap'] = True
                out['room'] = snapshot(room,pid,False)
                out['resync'] = 'MCP v2 bootstrap replaces earlier context, including rules and static data.'
            elif full:
                out['full_state'] = True
                out['snapshot'] = snapshot(room,pid,True)
            else:
                private = changed(now_private,ctx.get('private',{}))
                if name == 'rummikub':
                    before = ctx.get('private',{}).get('hand',[]); after = now_private['hand']
                    added = [t for t in after if t not in before]; removed = [t for t in before if t not in after]
                    private = {}
                    if submitted and submitted.get('action')=='meld':
                        used = {t for m in submitted['melds'] for t in m}
                        removed = [t for t in removed if t not in used]
                    if added: private['+'] = added
                    if removed: private['-'] = removed
                if private: out['private'] = private
                if repair:
                    projected = f.project_mcp_room_for_viewer(room,pid)
                    out['public_state'] = game.mcp_lifecycle_state(projected['board_state']) if hasattr(game,'mcp_lifecycle_state') else projected['board_state']
            if events: out['events'] = events
            newest = conn.execute('SELECT COALESCE(MAX(id),0) FROM room_messages WHERE room_id=?',(room_id,)).fetchone()[0]
            last_delivered = first_unread_text - 1 if first_unread_text is not None else newest
            conn.execute('''INSERT INTO room_event_cursors(room_id,player_id,last_event_id,updated_at,mcp_bootstrapped)
                VALUES(?,?,?,'1970-01-01T00:00:00+00:00',1) ON CONFLICT(room_id,player_id)
                DO UPDATE SET last_event_id=excluded.last_event_id,mcp_bootstrapped=1''',(room_id,pid,last_delivered))
            ctx = dict(v=VERSION,public=baseline(game,state),private=now_private)
            if first_unread_text is not None:
                ctx['snapshot_event_id'] = newest
            conn.execute('INSERT OR REPLACE INTO mcp_minimal_contexts VALUES(?,?,?)',(room_id,pid,json.dumps(ctx)))
        if room['status']=='playing':
            if name=='bomb_plane' and state['phase']=='setup':
                out['setup'] = 'locked' if state['ready'][pid] else 'open'
            if not f.participant_can_act(room,pid):
                out['wait'] = room['current_player_id']
                if name=='bomb_plane' and state['phase']=='setup':
                    out['wait'] = next((p for p in state['participant_order'] if not state['ready'][p]),None)
        else:
            out['status'] = room['status']
        if viewer.get('join_status')=='left': out['status']='left'
        if not viewer.get('active',True) or viewer.get('activity_state','active')!='active':
            out['participant_status']=viewer.get('activity_state','inactive')
        # Wallet and terminal helpers open their own connections. Do this after
        # committing, so settlement/notification reads cannot nest a write lock.
    if room['status'] in ('finished','archived'):
        out.update(_terminal_fields(room,pid))
    from .achievements import filter_unlocks
    unlocks = filter_unlocks((source_room or {}).get('achievement_unlocks',[]), 'ai', pid)
    if unlocks: out['unlocks']=unlocks
    return out


def placements(room, pid, query):
    """Read-only candidate query; never consumes the event/private cursor."""
    from .framework import participant_can_act
    from .games.carcassonne import legal_placements, FeatureGraph
    if room['game_type']!='carcassonne' or not participant_can_act(room,pid):
        raise DuelError('placements 仅供卡卡颂当前行动者查询')
    if set(query)-{'query','all','x','y','rotation','meeple'} or query.get('query')!='placements':
        raise DuelError('未知 placements 查询参数')
    if 'all' in query and type(query['all']) is not bool: raise DuelError('all 必须是布尔值')
    all_ = query.get('all',False)
    if not all_ and any(type(query.get(k)) is not int for k in ('x','y')): raise DuelError('候选需要整数 x/y')
    if 'rotation' in query and (type(query['rotation']) is not int or query['rotation'] not in range(4)):
        raise DuelError('rotation 必须为 0..3')
    if 'meeple' in query and query['meeple'] is not None and not isinstance(query['meeple'],str): raise DuelError('meeple 必须为区域 ID 或 null')
    state=room['board_state']; tile=state['current_tile']; graph=FeatureGraph(state['board'])
    legal=legal_placements(state['board'],tile)
    regions=lambda x,y,r: graph.available(tile,x,y,r) if state['supply'][pid] else []
    out={'revision':room['revision']}
    if not all_:
        x,y=query['x'],query['y']
        rotations=[r for px,py,r in legal if (px,py)==(x,y)]
        r=query.get('rotation')
        out['valid']=any((r is None or r==cr) and (query.get('meeple') is None or query['meeple'] in regions(x,y,cr)) for cr in rotations)
        legal=sorted(legal,key=lambda p:(abs(p[0]-x)+abs(p[1]-y),p))[:8]
    out['placements']=[[x,y,r,regions(x,y,r)] for x,y,r in legal]
    return out


def attach_new_notifications(payload, pid):
    from .notifications import unread_summary, unread_hint
    with write_transaction() as conn:
        newest=conn.execute("SELECT COALESCE(MAX(id),0) FROM notifications WHERE subject_type='ai' AND subject_id=? AND read_at IS NULL",(pid,)).fetchone()[0]
        old=conn.execute('SELECT last_id FROM mcp_notification_delivery WHERE player_id=?',(pid,)).fetchone()
        if newest and (not old or newest>old[0]):
            summary=unread_summary('ai',pid,conn=conn)
            payload['unread']=summary; payload['unread_hint']=unread_hint(summary)
            conn.execute('INSERT OR REPLACE INTO mcp_notification_delivery VALUES(?,?)',(pid,newest))
    return payload


def needs_bootstrap(room_id, pid):
    from .database import connect
    conn = connect()
    try:
        row=conn.execute('SELECT context FROM mcp_minimal_contexts WHERE room_id=? AND player_id=?',(room_id,pid)).fetchone()
        return not row or json.loads(row[0]).get('v') != VERSION
    finally:
        conn.close()

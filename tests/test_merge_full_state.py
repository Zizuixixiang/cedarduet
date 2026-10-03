"""Joint privacy and multiplayer resync audit on disposable rooms."""
import unittest
from unittest.mock import patch
from app import database, framework, invites
from app.games import GAMES
from tests import test_legacy_full_state as legacy
from tests.full_state_support import choose


def nodes(value, path=()):
    yield path, value
    if isinstance(value, dict):
        for key, child in value.items():
            yield from nodes(child, path+(key,))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from nodes(child, path+(index,))


class MergeFullStateTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = legacy.LegacyFullStateTests.asyncSetUp
    asyncTearDown = legacy.LegacyFullStateTests.asyncTearDown
    room = legacy.LegacyFullStateTests.room
    call = legacy.LegacyFullStateTests.call

    async def test_recursive_privacy_all_29_after_discarding_bootstrap(self):
        for name in sorted(GAMES):
            with self.subTest(game=name):
                room = self.room(name, 4 if name=='blackjack' else None)
                for p in room['participants']:
                    if p['role']=='ai':
                        await self.call(room,p['player_id'],action='state')
                if name=='bomb_plane':
                    for pid in ('p0','p1'):
                        room=framework.play_move(room['room_id'],'ai',pid,{'action':'auto_setup'},expected_revision=room['revision'])
                for p in room['participants']:
                    if p['role']!='ai': continue
                    pid=p['player_id']; full=await self.call(room,pid,action='state',full_state=True)
                    snapshot=full['snapshot']; raw=room['board_state']
                    for path,value in nodes(full):
                        if not path:continue
                        key=path[-1]
                        self.assertNotIn(key, ('_decks','rng_state','random_state','revealed_planes'),(name,path))
                        if key in ('hands','pool','deck','shoe','dice_by_player') and isinstance(value,(list,dict)):
                            self.assertFalse(value,(name,path,value))
                        if name=='bomb_plane' and key=='planes' and isinstance(value,list):
                            self.assertEqual(path,('snapshot','private_state','planes'))
                            self.assertEqual(value,raw['planes'][pid])
                    # Search every nested object/list, including arbitrary keys,
                    # for whole opponent hands and unique private card IDs.
                    zones = raw.get('cards', {})
                    if isinstance(zones, dict):
                        own_ids = {c.get('id') for c in zones.get('hands', {}).get(pid, []) if isinstance(c, dict)}
                        visible_ids = {v.get('id') for _, v in nodes(framework.project_room_for_viewer(room, pid)['board_state']) if isinstance(v, dict) and 'id' in v}
                        leaves = [v for _, v in nodes(full) if isinstance(v, str)]
                        for other, hand in zones.get('hands', {}).items():
                            if other == pid: continue
                            for path, value in nodes(full):
                                if hand: self.assertNotEqual(value, hand, (name, path))
                            for card in hand:
                                if isinstance(card, dict) and card.get('id') not in own_ids | visible_ids:
                                    self.assertNotIn(card['id'], leaves, (name, other))
                    if name=='rummikub':
                        leaves=[v for _,v in nodes(full) if isinstance(v,str)]
                        for other,hand in raw['hands'].items():
                            if other!=pid:
                                for tile in hand:self.assertNotIn(tile,leaves)
                        for tile in raw['pool']:self.assertNotIn(tile,leaves)
                    for secret in (raw.get('deck'),raw.get('pool'),raw.get('_decks')):
                        if isinstance(secret,(list,dict)) and len(secret)>1:
                            for path,value in nodes(full):self.assertNotEqual(value,secret,(name,path))

    async def test_legacy_multiplayer_normal_and_invite_text_and_new_actions(self):
        for invite in (False,True):
            with self.subTest(invite=invite):
                if invite:
                    room=invites.create_invite('uno','ai','p0',target_player_count=4)
                    for pid in ('p1','p2','p3'):invites.join_invite(room['invite_code'],'ai',pid)
                    with patch.object(invites.secrets,'SystemRandom') as rng:
                        rng.return_value.shuffle.side_effect=lambda seats:None
                        room=invites.start_invite(room['room_id'],'ai','p0')
                else:room=self.room('uno',4)
                for p in room['participants']:await self.call(room,p['player_id'],action='state')
                for pid in ('p1','p2','p3'):framework.post_message(room['room_id'],'ai',pid,'before-'+pid)
                for _ in range(4):
                    pid=room['current_player_id'];move=choose(framework.project_mcp_snapshot_for_viewer(room,pid))
                    room=framework.play_move(room['room_id'],'ai',pid,move,expected_revision=room['revision'])
                full=await self.call(room,'p0',action='state',full_state=True)
                self.assertEqual([e['message'] for e in full['events'] if e['message'].startswith('before-')],
                                 ['before-p1','before-p2','before-p3'])
                # UNO may also emit public challenge-result text. Preserve it;
                # a cold snapshot must suppress actions, not referee messages.
                self.assertTrue(all(set(e)=={'name','message'} for e in full['events']))
                self.assertNotIn('events',await self.call(room,'p0',action='state',full_state=True))
                # New actions after the snapshot: compare ordinary delivery with
                # canonical public event projection in the original order.
                with database.connect() as conn:
                    boundary=conn.execute('SELECT MAX(id) FROM room_messages WHERE room_id=?',(room['room_id'],)).fetchone()[0]
                for _ in range(8):
                    pid=room['current_player_id'];move=choose(framework.project_mcp_snapshot_for_viewer(room,pid))
                    room=framework.play_move(room['room_id'],'ai',pid,move,expected_revision=room['revision'])
                with database.connect() as conn:
                    rows=conn.execute("SELECT * FROM room_messages WHERE room_id=? AND id>? AND sender_player_id!='p0' AND event_type='move' ORDER BY id",(room['room_id'],boundary)).fetchall()
                expected=[]
                for row in rows:
                    e=framework._project_event_for_viewer(room,framework._timeline_entry(row,room),'p0')
                    if e and e.get('move'):expected.append((e['sender']['name'],e['move']))
                # chat uses the ordinary consuming response even while waiting.
                reply=await self.call(room,'p0',action='chat',message='consume-new')
                actual=[(e['name'],e['move']) for e in reply.get('events',[]) if 'move' in e]
                self.assertGreaterEqual(len(expected),3)
                self.assertEqual(actual,expected)
                self.assertNotIn('events',await self.call(room,'p0',action='chat',message='consume-again'))

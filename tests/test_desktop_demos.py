"""Isolated safety/ownership/state checks for the operator-only demo seed."""
import base64
from collections import Counter
from contextlib import closing
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import httpx
from app import database, framework, main
from app.desktop_demo import DEMO_OWNER, DEMO_ROOMS, is_desktop_demo
from app.npc_controller import run_current_npc_turn
from app.npc_runtime import list_active_npc_turn_room_ids
from app.npc_scheduler import is_system_npc_turn
from scripts.seed_desktop_demos import seed, fixtures


class DesktopDemoTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='duel-demo-test-')
        self.db = Path(self.tmp.name)/'test.db'
        self.dbpatch = patch.object(database,'DB_PATH',self.db);self.dbpatch.start()
        database.init_db()
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app),base_url='http://demo.test')

    async def asyncTearDown(self):
        await self.client.aclose();self.dbpatch.stop();self.tmp.cleanup()

    def dump(self):
        with closing(database.connect()) as conn:return '\n'.join(conn.iterdump())

    def headers(self, owner=DEMO_OWNER):
        return {'X-Duel-Human-Player':'guest:demo-reviewer','X-Duel-Bound-Ais':base64.urlsafe_b64encode(json.dumps([{'id':owner,'name':'合成测试绑定'}]).encode()).decode()}

    async def test_create_rerun_discovery_and_reads_do_not_write_or_notify(self):
        normal=framework.create_room('tictactoe','ai_first','ai','guest:ordinary')
        with closing(database.connect()) as conn:
            old=tuple(conn.execute('SELECT * FROM rooms WHERE room_id=?',(normal['room_id'],)).fetchone())
            counts={r[0]:conn.execute(f'SELECT COUNT(*) FROM "{r[0]}"').fetchone()[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertEqual(len(seed(self.db,apply=True)),7)
        before=self.dump();seed(self.db,apply=True);self.assertEqual(before,self.dump())
        listed=await self.client.get('/api/spectating/rooms',headers=self.headers())
        self.assertEqual(listed.status_code,200)
        self.assertEqual({r['room_id'] for r in listed.json()['rooms']},set(DEMO_ROOMS.values()))
        for r in listed.json()['rooms']:
            self.assertTrue(r['game_name'].startswith('【演示】'))
            for _ in range(2):
                response=await self.client.get('/api/rooms/'+r['room_id'],headers=self.headers())
                self.assertEqual(response.status_code,200)
                payload=response.json()['room']
                self.assertEqual(payload['viewer']['player_id'],DEMO_OWNER)
                self.assertTrue(payload['spectating']);self.assertEqual(payload['stake'],0)
                self.assertFalse(payload.get('timeout_takeover'))
            forbidden=await self.client.get('/api/rooms/'+r['room_id'],headers=self.headers('guest:unrelated'))
            self.assertEqual(forbidden.status_code,403)
        self.assertEqual(before,self.dump())
        with closing(database.connect()) as conn:
            self.assertEqual(old,tuple(conn.execute('SELECT * FROM rooms WHERE room_id=?',(normal['room_id'],)).fetchone()))
            # Exactly these room-local tables may grow; no wallet, achievement, notification or presence changes.
            for table,count in counts.items():
                if table not in {'rooms','room_participants','room_messages','sqlite_sequence'}:
                    self.assertEqual(conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0],count,table)

    async def test_freeze_blocks_moves_messages_npc_and_stale_archive(self):
        seed(self.db,apply=True)
        with database.write_transaction() as conn:
            conn.execute("UPDATE rooms SET last_move_at='2001-01-01T00:00:00+00:00'")
        before=self.dump()
        self.assertEqual(list_active_npc_turn_room_ids(),[])
        for rid in DEMO_ROOMS.values():
            r=framework.get_room(rid)
            self.assertTrue(is_desktop_demo(r));self.assertEqual(r['status'],'playing')
            self.assertFalse(is_system_npc_turn(r))
            for call in [lambda:framework.play_move(rid,'ai',DEMO_OWNER,{},expected_revision=100),
                         lambda:framework.post_message(rid,'ai',DEMO_OWNER,'test'),
                         lambda:framework.resign(rid,'ai',DEMO_OWNER),
                         lambda:framework.leave_room(rid,'ai',DEMO_OWNER)]:
                with self.assertRaisesRegex(framework.DuelError,'演示房'):call()
            with self.assertRaisesRegex(framework.DuelError,'演示房'):await run_current_npc_turn(rid)
        self.assertEqual(before,self.dump())
        normal=framework.create_room('tictactoe','ai_first','ai','guest:normal','guest:opponent')
        self.assertFalse(is_desktop_demo(normal))
        moved=framework.play_move(normal['room_id'],'ai','guest:normal',{'row':0,'col':0})
        self.assertEqual(moved['revision'],normal['revision']+1)

    async def test_collision_rolls_back_entire_batch_and_dry_run_is_read_only(self):
        before=self.dump();seed(self.db);self.assertEqual(before,self.dump())
        seed(self.db,apply=True)
        with database.write_transaction() as conn:
            conn.execute("UPDATE rooms SET stake=1 WHERE room_id=?",(DEMO_ROOMS['mahjong'],))
            conn.execute('DELETE FROM rooms WHERE room_id=?',(DEMO_ROOMS['four_color_qunhu'],))
        before=self.dump()
        with self.assertRaisesRegex(ValueError,'collision'):seed(self.db,apply=True)
        self.assertEqual(before,self.dump())

    async def test_fixture_density_and_card_integrity(self):
        data={e['room']['game_type']:e['room'] for e in fixtures()}
        mj=data['mahjong']['board_state']
        tiles=mj['wall']+sum(mj['hands'].values(),[])+sum(mj['discards'].values(),[])+[t for melds in mj['melds'].values() for m in melds for t in m['tiles']]
        self.assertEqual(len(tiles),136);self.assertEqual(len({t['id'] for t in tiles}),136)
        self.assertTrue(all(n==4 for n in Counter(t['code'] for t in tiles).values()))
        self.assertEqual(len(mj['hands'][DEMO_OWNER]),14)
        self.assertTrue(all(len(v)>=10 for v in mj['discards'].values()))
        self.assertGreaterEqual(sum(len(v) for v in mj['melds'].values()),4)
        ac=data['aeroplane_chess']['board_state']
        self.assertTrue(all(sum(p['zone']!='airport' for p in planes)>=3 for planes in ac['planes'].values()))
        mo=data['monopoly']['board_state']
        self.assertGreaterEqual(sum(bool(t['owner']) for t in mo['tiles']),18)
        self.assertEqual(len({p['position'] for p in mo['players']}),6)
        tx=data['texas_holdem']; projected=framework.project_room_for_viewer(framework._decorate(tx),DEMO_OWNER)
        self.assertEqual(tx['board_state']['street'],'flop');self.assertEqual(len(tx['board_state']['visible_board']),3)
        self.assertEqual(len(projected['private_state']['hand']),2)
        self.assertEqual(tx['board_state']['initial_chip_total'],6000)
        for k in ('guandan','uno','four_color_qunhu'):
            r=data[k];p=framework.project_room_for_viewer(framework._decorate(r),DEMO_OWNER)
            self.assertTrue(p['private_state'].get('hand'),k)
        self.assertGreater(len(data['guandan']['board_state']['played_cards']),0)
        self.assertGreater(len(data['uno']['board_state']['action_history']),0)
        self.assertGreater(len(data['four_color_qunhu']['board_state']['discards']),0)

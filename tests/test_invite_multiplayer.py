import asyncio
import base64
import json
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
import httpx
from app import database, framework, invites, takeover
from app import main as main_module
from app.games import GAMES, get_game
from app.npc_personas import NpcPersona


class InviteTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="duel-invites-")
        self.db = patch.object(database, "DB_PATH", Path(self.tmp.name)/"test.db")
        self.db.start(); database.init_db()
        self.events = patch.object(main_module, "revision_events", main_module.RevisionEvents())
        self.events.start()
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=main_module.app), base_url="http://test")

    async def asyncTearDown(self):
        await self.client.aclose()
        self.events.stop(); self.db.stop(); self.tmp.cleanup()

    def create(self, game="tictactoe", count=2, role="human", timeout=False):
        return invites.create_invite(game, role, "owner", target_player_count=count,
                                     timeout_takeover=timeout, display_name="同名")

    def ready(self, game="tictactoe", count=2, role="human", timeout=False):
        room = self.create(game, count, role, timeout)
        for i in range(count-1):
            invites.join_invite(room["invite_code"], "human", f"friend{i}", display_name="同名")
        return invites.start_invite(room["room_id"].lower(), role, "owner")

    def expire(self, room):
        then = (datetime.now(timezone.utc)-timedelta(seconds=91)).isoformat()
        with database.write_transaction() as conn:
            conn.execute("UPDATE room_invites SET turn_started_at=? WHERE room_id=?", (then, room["room_id"]))
        return framework.get_room(room["room_id"])

    def household_headers(self, ids=("101", "102", "103", "104", "105")):
        machines = [{"id": pid, "name": f"小机{pid}"} for pid in ids]
        return {"X-Duel-Human-Player": "owner", "X-Duel-Bound-Ais":
                base64.urlsafe_b64encode(json.dumps(machines).encode()).decode()}

    async def household(self, target, selected, headers=None):
        return await self.client.post("/api/invites", headers=headers or self.household_headers(),
                                      json={"game_type": "uno", "target_player_count": target,
                                            "ai_players": selected, "player_id": "forged"})

    async def test_household_reserves_external_seat_for_every_table_size(self):
        for target in range(2, 7):
            with self.subTest(target=target):
                rejected = await self.household(target, [str(101 + i) for i in range(target - 1)])
                self.assertEqual(rejected.status_code, 400, rejected.text)
                self.assertIn("外部玩家", rejected.json()["message"])
                allowed = await self.household(target, [str(101 + i) for i in range(target - 2)])
                self.assertEqual(allowed.status_code, 200, allowed.text)
                room = allowed.json()["room"]
                self.assertEqual(room["household_count"], target - 1)
                self.assertEqual(len(room["participants"]), target - 1)
                self.assertFalse(room["room_ready"])
                self.assertEqual(room["initiator_player_id"], "owner")
                for ai in room["participants"][1:]:
                    self.assertEqual(ai["participant_kind"], "bound_machine")
                    self.assertEqual(ai["join_status"], "joined")
                    self.assertEqual(ai["display_name"], "小机" + ai["player_id"])
                for fill in (False, True):
                    start = await self.client.post(f'/api/rooms/{room["room_id"]}/start',
                        headers=self.household_headers(), json={"fill_with_npcs": fill})
                    self.assertEqual(start.status_code, 409, start.text)
                    self.assertIn("外部受邀玩家", start.json()["message"])
                joined = invites.join_invite(room["invite_code"], "ai", "external")
                self.assertTrue(joined["room_ready"])
                started = invites.start_invite(room["room_id"], "human", "owner")
                self.assertEqual(started["status"], "playing")
                self.assertEqual(len(started["participants"]), target)

    async def test_household_rejects_forged_binding_and_duplicate_account_slots(self):
        for selected, ids, expected in [
            (["stranger"], ("101",), 403),
            (["101"], (), 403),
            (["101", "101"], ("101",), 409),
            (["101", "101:2"], ("101", "101:2"), 409),
            (["owner:2"], ("owner:2",), 409),
        ]:
            with self.subTest(selected=selected, ids=ids):
                result = await self.household(4, selected, self.household_headers(ids))
                self.assertEqual(result.status_code, expected, result.text)
        with database.connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM rooms").fetchone()[0], 0)
        # A body cannot supply the trusted list or lower the persisted count.
        for extra in ({"trusted_bound_ais": [{"id": "stranger", "name": "假"}]}, {"household_count": 1}):
            result = await self.client.post("/api/invites", headers=self.household_headers(),
                json={"game_type": "uno", "target_player_count": 4, "ai_players": ["stranger"], **extra})
            self.assertEqual(result.status_code, 422, result.text)
        room = (await self.household(4, ["101", "102"])).json()["room"]
        for pid in ("101", "101:2", "102:5", "owner:2"):
            with self.assertRaises(framework.DuelError):
                invites.join_invite(room["invite_code"], "ai", pid)

    async def test_household_requires_external_before_npc_fill_after_process_restart(self):
        room = (await self.household(4, ["101"])).json()["room"]
        # A fresh process reads the same persisted boundary and still refuses fill.
        probe = subprocess.run([sys.executable, "-c", """
import sys
from pathlib import Path
from app import database, framework, invites
database.DB_PATH = Path(sys.argv[1])
room = framework.get_room(sys.argv[2])
assert room['household_count'] == 2
try:
    invites.start_invite(room['room_id'], 'human', 'owner', fill_with_npcs=True)
except framework.DuelError as error:
    assert '外部受邀玩家' in str(error)
else:
    raise AssertionError('household bypassed external requirement')
""", str(database.DB_PATH), room["room_id"]], capture_output=True, text=True, timeout=15)
        self.assertEqual(probe.returncode, 0, probe.stderr)
        joined = invites.join_invite(room["invite_code"], "human", "external")
        self.assertFalse(joined["room_ready"])
        with self.assertRaises(framework.DuelError):
            invites.start_invite(room["room_id"], "human", "owner")
        with patch.object(invites, "require_npc"), patch.object(invites, "select_personas",
                return_value=[NpcPersona("one", "一", "测试")]):
            started = invites.start_invite(room["room_id"], "human", "owner", fill_with_npcs=True)
        self.assertEqual(started["status"], "playing")
        self.assertEqual(sum(p["participant_kind"] == "system_npc" for p in started["participants"]), 1)

    def test_household_migration_is_idempotent_on_consistent_legacy_copy(self):
        room = self.create()
        invites.join_invite(room["invite_code"], "human", "external")
        import sqlite3
        snapshot = Path(self.tmp.name) / "legacy-copy.db"
        with database.connect() as source, sqlite3.connect(snapshot) as dest:
            source.backup(dest)
            dest.execute("ALTER TABLE room_invites DROP COLUMN household_count")
        with patch.object(database, "DB_PATH", snapshot):
            database.init_db()
            database.init_db()
            restored = framework.get_room(room["room_id"])
            self.assertEqual(restored["household_count"], 1)
            self.assertEqual(restored["invite_code"], room["invite_code"])
            self.assertTrue(restored["room_ready"])
            with database.connect() as conn:
                self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")
                self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])
            self.assertEqual(invites.start_invite(room["room_id"], "human", "owner")["status"], "playing")

    async def test_human_create_link_preview_join_and_room_lists(self):
        response = await self.client.post('/api/invites', headers={"X-Duel-Human-Player":"human1", "X-Duel-Human-Name":"Alice"}, json={"game_type":"tictactoe","target_player_count":2})
        self.assertEqual(response.status_code,200,response.text)
        room=response.json()["room"]
        self.assertEqual(len(room["participants"]),1)
        code=room["invite_code"]
        self.assertEqual(room["invite_link"], f"/duel/?invite={code}")
        preview=await self.client.get('/api/invites/'+code, headers={"X-Duel-Human-Player":"human2"})
        self.assertEqual(preview.status_code,200,preview.text)
        self.assertNotIn("private_state", preview.json()["invitation"])
        response=await self.client.post('/api/invites/join', headers={"X-Duel-Human-Player":"human2"}, json={"invite_code":code,"player_id":"forged"})
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()["room"]["viewer"]["player_id"],"human2")
        for player in ("human1","human2"):
            listed=framework.list_human_rooms(player)
            self.assertEqual(listed[0]["room_kind"],"invite")
            self.assertTrue(listed[0]["room_ready"])
        bad=await self.client.post(f'/api/rooms/{room["room_id"]}/start',headers={"X-Duel-Human-Player":"human2"},json={})
        self.assertEqual(bad.status_code,403)
        start=await self.client.post(f'/api/rooms/{room["room_id"]}/start',headers={"X-Duel-Human-Player":"human1"},json={})
        self.assertEqual(start.status_code,200,start.text)
        with self.assertRaises(framework.DuelError): invites.preview_invite(code)

    async def test_ai_mcp_creator_only_self_join_ready_and_old_new(self):
        async def mcp(**body):
            r=await self.client.post('/mcp/play',json=body)
            self.assertEqual(r.status_code,200,r.text)
            return r.json()
        rejected = await self.client.post('/mcp/play', json={
            "action": "invite", "player_id": "ai1", "game_type": "tictactoe",
            "target_player_count": 2, "ai_players": ["boundHuman", "ai1:2"]})
        self.assertEqual(rejected.status_code, 422, rejected.text)
        self.assertEqual(framework.list_ai_rooms("ai1"), [])
        created=await mcp(action="invite",player_id="ai1",opponent_id="boundHuman",
                          participant_ids=["boundHuman", "ai1:2"],
                          game_type="tictactoe",target_player_count=2)
        self.assertEqual([p["player_id"] for p in created["room"]["participants"]],["ai1"])
        self.assertEqual(created["room"]["household_count"], 1)
        self.assertEqual(created["room"]["target_player_count"], 2)
        self.assertEqual(created["room"]["status"], "waiting")
        self.assertFalse(created["room"]["room_ready"])
        with self.assertRaises(framework.DuelError):
            invites.create_invite("uno", "ai", "ai1", target_player_count=4,
                ai_players=["otherAI"], trusted_bound_ais=[{"id": "otherAI", "name": "不能自动带入"}])
        await mcp(action="join",player_id="ai2",invite_code=created["invite_code"])
        state=await mcp(action="state",player_id="ai1",room_id=created["room_id"],wait=True)
        self.assertTrue(state["room_ready"])
        self.assertEqual(framework.list_ai_rooms("ai2")[0]["room_kind"],"invite")
        start=await mcp(action="start",player_id="ai1",room_id=created["room_id"])
        self.assertEqual(start["status"],"playing")
        old=await mcp(action="new",player_id="oldAI",opponent_id="oldHuman",game_type="tictactoe")
        self.assertEqual(len(old["room"]["participants"]),2)

    async def test_ai_two_player_invite_requires_human_to_join_even_if_bound(self):
        for human in ("boundHuman", "otherHuman"):
            with self.subTest(human=human):
                response = await self.client.post('/mcp/play', json={
                    "action": "invite", "player_id": "ai1", "opponent_id": "boundHuman",
                    "game_type": "tictactoe", "target_player_count": 2})
                self.assertEqual(response.status_code, 200, response.text)
                room = response.json()["room"]
                self.assertEqual([p["player_id"] for p in room["participants"]], ["ai1"])
                self.assertEqual(room["household_count"], 1)
                self.assertFalse(room["room_ready"])
                self.assertEqual(framework.list_human_rooms(human), [])
                with self.assertRaises(framework.DuelError):
                    invites.start_invite(room["room_id"], "ai", "ai1")
                joined = await self.client.post('/api/invites/join',
                    headers={"X-Duel-Human-Player": human}, json={"invite_code": room["invite_code"]})
                self.assertEqual(joined.status_code, 200, joined.text)
                self.assertTrue(joined.json()["room"]["room_ready"])
                started = await self.client.post('/mcp/play', json={
                    "action": "start", "player_id": "ai1", "room_id": room["room_id"]})
                self.assertEqual(started.status_code, 200, started.text)
                self.assertEqual(started.json()["status"], "playing")

    def test_admission_errors_and_close(self):
        room=self.create()
        code=room["invite_code"]
        for fill in (False,True):
            with self.assertRaises(framework.DuelError): invites.start_invite(room["room_id"],"human","owner",fill_with_npcs=fill)
        with self.assertRaises(framework.DuelError): invites.join_invite(code,"human","owner")
        with self.assertRaises(framework.DuelError): invites.join_invite(code,"human","owner:2")
        with self.assertRaises(framework.DuelError): invites.join_invite("WRONG","human","friend")
        with self.assertRaises(framework.DuelError): framework.join_room(room["room_id"],"human","friend")
        invites.join_invite(code,"human","friend")
        with self.assertRaises(framework.DuelError): invites.join_invite(code,"human","third")
        framework.leave_room(room["room_id"],"human","owner")
        with self.assertRaises(framework.DuelError): invites.preview_invite(code)

    def test_small_human_ids_are_distinct_from_ai_slots(self):
        room = invites.create_invite("tictactoe", "human", "human:2", target_player_count=2)
        joined = invites.join_invite(room["invite_code"], "human", "human:3")
        self.assertEqual(len({p["handle"] for p in joined["participants"]}), 2)
        self.assertEqual(framework.account_key("123:2"), "123")
        self.assertNotEqual(framework.account_key("human:2"), framework.account_key("human:3"))

    def test_atomic_last_seat(self):
        room=self.create()
        def attempt(player):
            try: invites.join_invite(room["invite_code"],"human",player); return True
            except framework.DuelError: return False
        with ThreadPoolExecutor(2) as pool:
            self.assertEqual(sum(pool.map(attempt,["one","two"])),1)

    def test_npc_fill_and_random_seats(self):
        room=self.create("train_cards",4)
        invites.join_invite(room["invite_code"],"ai","guestAI")
        with patch.object(invites, "select_personas", return_value=[NpcPersona("one", "一", "测试"), NpcPersona("two", "二", "测试")]), patch.object(invites.secrets, "SystemRandom") as random:
            random.return_value.shuffle.side_effect=lambda members: members.reverse()
            started=invites.start_invite(room["room_id"],"human","owner",fill_with_npcs=True)
        self.assertEqual(len(started["participants"]),4)
        self.assertEqual(started["participants"][-1]["player_id"],"owner")
        self.assertEqual(sum(p["participant_kind"]=="system_npc" for p in started["participants"]),2)
        for reverse in (False, True):
            room=self.create()
            invites.join_invite(room["invite_code"],"human","friend")
            with patch.object(invites.secrets,"SystemRandom") as random:
                random.return_value.shuffle.side_effect=lambda members: members.reverse() if reverse else None
                started=invites.start_invite(room["room_id"],"human","owner")
            self.assertEqual(started["current_player_id"],"friend" if reverse else "owner")

    def test_private_state_and_special_seat_contracts(self):
        for game,count in [("uno",4),("guandan",4),("junqi",2),("go",2),("chess",2),("xiangqi",2)]:
            with self.subTest(game=game):
                room=self.ready(game,count)
                self.assertEqual([p["seat_index"] for p in room["participants"]],list(range(count)))
                for member in room["participants"]:
                    projected=framework.project_room_for_viewer(room,member["player_id"])
                    expected=get_game(game).private_state(room["board_state"],member,room["participants"])
                    self.assertEqual(projected["private_state"],expected)
                    self.assertEqual(projected["viewer"]["token"],member["token"])
                    if game=="uno":
                        self.assertNotIn("hands",projected["board_state"])
                        self.assertNotEqual(projected["private_state"], framework.project_room_for_viewer(room,next(p["player_id"] for p in room["participants"] if p!=member))["private_state"])
                with self.assertRaises(framework.DuelError): framework.project_room_for_viewer(room,"stranger")

    def test_all_games_support_ai_only_invites_and_authoritative_opening_moves(self):
        for name, game in GAMES.items():
            with self.subTest(game=name):
                count = game.resolved_allowed_player_counts()[0]
                owner = f"owner-{name}"
                room = invites.create_invite(name, "ai", owner, target_player_count=count)
                for seat in range(1, count):
                    invites.join_invite(room["invite_code"], "ai", f"{owner}-{seat}")
                room = invites.start_invite(room["room_id"], "ai", owner)
                self.assertTrue(all(p["role"] == "ai" for p in room["participants"]))
                for participant in room["participants"]:
                    projected = framework.project_room_for_viewer(room, participant["player_id"])
                    self.assertEqual(projected["viewer"]["player_id"], participant["player_id"])
                actor = next(p for p in room["participants"] if p["player_id"] == room["current_player_id"])
                actions = takeover.timeout_legal_actions(room, actor)
                self.assertTrue(actions)
                moved = framework.play_move(room["room_id"], "ai", actor["player_id"], actions[0],
                                            expected_revision=room["revision"])
                self.assertEqual(moved["revision"], room["revision"] + 1)

    def test_temporary_npc_context_excludes_other_hands_and_private_chat(self):
        from dataclasses import asdict
        from app.npc_controller import _decision_request
        room = self.ready("uno", 4)
        actor = next(p for p in room["participants"] if p["player_id"] == room["current_player_id"])
        other = next(p for p in room["participants"] if p != actor)
        framework.post_message(room["room_id"], other["role"], other["player_id"],
                               "PRIVATE_CHAT_SENTINEL", visible_to_player_ids={other["player_id"]})
        actions = takeover.timeout_legal_actions(room, actor)
        request, _ = _decision_request(room, actor["player_id"], actions, temporary=True)
        payload = json.dumps(asdict(request))
        self.assertNotIn("PRIVATE_CHAT_SENTINEL", payload)
        own = framework.project_room_for_viewer(room, actor["player_id"])["private_state"]["hand"]
        self.assertEqual({c["id"] for c in request.private_state["hand"]}, {c["id"] for c in own})
        for participant in room["participants"]:
            if participant == actor:
                continue
            private = framework.project_room_for_viewer(room, participant["player_id"])["private_state"]
            for card in private["hand"]:
                self.assertNotIn(json.dumps(card["id"]), payload)

    async def test_timeout_disabled_enabled_reclaim_and_revision_race(self):
        room=self.ready("train_cards",2,timeout=False)
        self.expire(room)
        self.assertIsNone(await takeover.run_timeout_turn(room["room_id"]))
        room=self.ready("train_cards",2,timeout=True)
        self.expire(room)
        before=framework.get_room(room["room_id"])
        result=await takeover.run_timeout_turn(room["room_id"])
        self.assertEqual(result["revision"],before["revision"]+1)
        self.assertEqual([(p["player_id"],p["participant_kind"]) for p in result["participants"]],[(p["player_id"],p["participant_kind"]) for p in before["participants"]])
        self.assertEqual(result["takeover_revision"],before["revision"])
        self.expire(result)
        current=result["current_player_id"]
        invites.reclaim(room["room_id"].lower(),"human",current)
        self.assertIsNone(await takeover.run_timeout_turn(room["room_id"]))
        actor=next(p for p in result["participants"] if p["player_id"]==current)
        move=get_game("train_cards").npc_legal_actions(result["board_state"],actor,result["participants"])[0]
        moved=framework.play_move(room["room_id"],"human",current,move,expected_revision=result["revision"])
        with self.assertRaises(framework.DuelError): framework.play_move(room["room_id"],"human",current,move,expected_revision=result["revision"],takeover=True)
        self.assertEqual(moved["revision"],result["revision"]+1)

    def age_presence(self, room, seconds=600):
        then = (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat()
        with database.write_transaction() as conn:
            conn.execute("UPDATE room_event_cursors SET updated_at=? WHERE room_id=?",
                         (then, room["room_id"]))
        return then

    def presence(self, room):
        with database.connect() as conn:
            return dict(conn.execute("SELECT player_id, updated_at FROM room_event_cursors WHERE room_id=?",
                                     (room["room_id"],)).fetchall())

    async def test_one_live_participant_allows_consecutive_takeovers_then_moves(self):
        room = self.create("train_cards", count=3, timeout=True)
        for pid in ("B", "C"):
            invites.join_invite(room["invite_code"], "human", pid)
        with patch.object(invites.secrets, "SystemRandom") as random:
            random.return_value.shuffle.side_effect = lambda members: None
            room = invites.start_invite(room["room_id"], "human", "owner")
        # A was assisted, returns by opening the room, and keeps syncing while
        # B and C never return. Neither assistance creates presence for its seat.
        room = await takeover.run_timeout_turn(self.expire(room)["room_id"])
        self.age_presence(room)
        for expected in ("B", "C"):
            self.assertEqual(room["current_player_id"], expected)
            response = await self.client.get(f"/api/rooms/{room['room_id']}",
                                            headers={"X-Duel-Human-Player": "owner"})
            self.assertEqual(response.status_code, 200, response.text)
            before = self.presence(room)
            room = await takeover.run_timeout_turn(self.expire(room)["room_id"])
            self.assertIsNotNone(room)
            self.assertEqual(self.presence(room), before)
        self.assertEqual(room["current_player_id"], "owner")
        moved = framework.play_move(room["room_id"], "human", "owner", {"action": "flip"},
                                    expected_revision=room["revision"])
        self.assertEqual(moved["revision"], room["revision"] + 1)

    async def test_stale_presence_blocks_scheduler_and_transaction_then_state_resumes(self):
        room = self.expire(self.ready("train_cards", timeout=True))
        self.age_presence(room)
        self.assertFalse(takeover.is_due(room))
        self.assertIsNone(await takeover.run_timeout_turn(room["room_id"]))
        with self.assertRaises(framework.DuelError):
            framework.play_move(room["room_id"], "human", room["current_player_id"],
                                {"action": "flip"}, expected_revision=room["revision"], takeover=True)
        self.assertEqual(framework.get_room(room["room_id"])["revision"], room["revision"])
        response = await self.client.get(f"/api/rooms/{room['room_id']}",
                                        headers={"X-Duel-Human-Player": "owner"})
        self.assertEqual(response.status_code, 200)
        changed = asyncio.Event()
        async def schedule_npc(room):
            pass
        scheduler = takeover.TakeoverScheduler(lambda _: changed.set(), schedule_npc)
        await scheduler.start()
        try:
            await asyncio.wait_for(changed.wait(), 2)
        finally:
            await scheduler.shutdown()
        self.assertEqual(framework.get_room(room["room_id"])["revision"], room["revision"] + 1)

    async def test_no_clients_after_one_takeover_expires_before_next_deadline(self):
        for seconds in (90, 180):
            room = self.ready("train_cards", timeout=True)
            now = datetime.now(timezone.utc).replace(microsecond=0)
            with database.write_transaction() as conn:
                conn.execute("UPDATE room_invites SET timeout_takeover_seconds=?, turn_started_at=? WHERE room_id=?",
                             (seconds, (now-timedelta(seconds=seconds)).isoformat(), room["room_id"]))
            # Latest possible real heartbeat, immediately before the first timeout.
            with patch.object(framework, "_now", return_value=now.isoformat()):
                framework.touch_room_presence(room["room_id"], "owner")
                assisted = await takeover.run_timeout_turn(room["room_id"])
            self.assertIsNotNone(assisted)
            future = now + timedelta(seconds=seconds)
            with patch.object(framework, "datetime", wraps=datetime) as fclock, \
                    patch.object(takeover, "datetime", wraps=datetime) as tclock:
                fclock.now.return_value = tclock.now.return_value = future
                self.assertFalse(takeover.is_due(assisted))
                self.assertIsNone(await takeover.run_timeout_turn(room["room_id"]))
            self.assertEqual(framework.get_room(room["room_id"])["revision"], assisted["revision"])

    async def test_system_npc_actions_never_refresh_presence(self):
        from app import npc_controller
        room = self.create("train_cards", count=3, timeout=True)
        invites.join_invite(room["invite_code"], "human", "friend")
        with patch.object(invites, "select_personas", return_value=[NpcPersona("test", "测试", "测试")]), \
                patch.object(invites.secrets, "SystemRandom") as random:
            random.return_value.shuffle.side_effect = lambda members: members.sort(
                key=lambda p: {"owner": 0, "npc:test": 1, "friend": 2}[p["player_id"]])
            room = invites.start_invite(room["room_id"], "human", "owner", fill_with_npcs=True)
        assisted = await takeover.run_timeout_turn(self.expire(room)["room_id"])
        self.age_presence(room)
        before = self.presence(room)
        invites.reclaim(room["room_id"], "ai", "npc:test")
        framework.post_message(room["room_id"], "ai", "npc:test", "NPC 聊天")
        framework.touch_room_presence(room["room_id"], "npc:test")
        with patch.object(npc_controller, "_finish_npc_action", return_value=None):
            result = await npc_controller.run_current_npc_turn(room["room_id"])
        self.assertEqual(result.room["current_player_id"], "friend")
        self.assertEqual(self.presence(room), before)
        # Even a fresh NPC cursor cannot qualify as someone online.
        with database.write_transaction() as conn:
            conn.execute("UPDATE room_event_cursors SET updated_at=? WHERE room_id=? AND player_id='npc:test'",
                         (framework._now(), room["room_id"]))
        self.assertIsNone(await takeover.run_timeout_turn(self.expire(result.room)["room_id"]))

    async def test_web_and_mcp_reads_refresh_but_internal_reads_do_not(self):
        room = self.create("train_cards", count=3, timeout=True)
        for pid in ("ai1", "ai2"):
            invites.join_invite(room["invite_code"], "ai", pid)
        with patch.object(invites.secrets, "SystemRandom") as random:
            random.return_value.shuffle.side_effect = lambda members: None
            room = invites.start_invite(room["room_id"], "human", "owner")
        self.age_presence(room)
        before = self.presence(room)
        framework.get_room(room["room_id"], "human", "owner")
        framework.project_room_for_viewer(room, "owner")
        framework.read_new_room_events(room["room_id"], "ai1")
        framework.claim_mcp_bootstrap(room["room_id"], "ai1")
        self.assertEqual(self.presence(room), before)
        response = await self.client.get(f"/api/rooms/{room['room_id']}",
                                        headers={"X-Duel-Human-Player": "owner"})
        self.assertEqual(response.status_code, 200)
        self.assertGreater(self.presence(room)["owner"], before["owner"])
        for args in ({"full_state": True}, {}, {"wait": True}):
            self.age_presence(room)
            before = self.presence(room)
            with patch.object(main_module, "MCP_WAIT_SECONDS", 0.02):
                response = await self.client.post('/mcp/play', json={"action": "state",
                    "player_id": "ai1", "room_id": room["room_id"], **args})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertGreater(self.presence(room)["ai1"], before["ai1"])
            self.assertEqual(self.presence(room)["ai2"], before["ai2"])
            if args.get("wait"):
                self.assertEqual(response.json()["status"], "still_waiting")

    async def test_real_actions_refresh_only_the_actor_and_inactive_never_counts(self):
        for role in ("human", "ai"):
            room = self.create("train_cards", role=role, timeout=True)
            invites.join_invite(room["invite_code"], "human", "friend")
            self.age_presence(room)
            with patch.object(invites.secrets, "SystemRandom") as random:
                random.return_value.shuffle.side_effect = lambda members: None
                room = invites.start_invite(room["room_id"], role, "owner")
            self.assertTrue(framework.has_live_room_participant(room))
            for action in (lambda: framework.post_message(room["room_id"], role, "owner", "hello"),
                           lambda: invites.reclaim(room["room_id"], role, "owner"),
                           lambda: framework.play_move(room["room_id"], role, "owner", {"action": "flip"},
                                                       expected_revision=room["revision"])):
                self.age_presence(room)
                before = self.presence(room)
                action()
                self.assertGreater(self.presence(room)["owner"], before["owner"])
                self.assertEqual(self.presence(room)["friend"], before["friend"])
            with database.write_transaction() as conn:
                conn.execute("UPDATE room_participants SET active=0 WHERE room_id=? AND player_id='owner'", (room["room_id"],))
            self.assertFalse(framework.has_live_room_participant(room))

    async def test_reclaim_refreshes_presence_but_protects_current_revision(self):
        for current in (True, False):
            with self.subTest(current=current):
                room = self.ready("train_cards", timeout=True)
                self.expire(room)
                assisted = await takeover.run_timeout_turn(room["room_id"])
                self.expire(assisted)
                actor = assisted["current_player_id"]
                reclaimer = actor if current else next(p["player_id"] for p in assisted["participants"]
                                                       if p["player_id"] != actor)
                reclaimed = invites.reclaim(room["room_id"], "human", reclaimer)
                self.assertIsNone(reclaimed["takeover_revision"])
                self.assertEqual(reclaimed["reclaim_revision"], assisted["revision"])
                self.assertIsNone(await takeover.run_timeout_turn(room["room_id"]))
                with self.assertRaises(framework.DuelError):
                    framework.play_move(room["room_id"], "human", actor, {"action": "flip"},
                                        expected_revision=reclaimed["revision"], takeover=True)
                moved = framework.play_move(room["room_id"], "human", actor, {"action": "flip"},
                                            expected_revision=reclaimed["revision"])
                self.assertIsNone(moved["reclaim_revision"])
                self.assertIsNotNone(await takeover.run_timeout_turn(self.expire(moved)["room_id"]))

    async def test_stale_and_fresh_presence_survive_new_process(self):
        room = self.expire(self.ready("train_cards", timeout=True))
        for fresh in (False, True):
            self.age_presence(room)
            if fresh:
                framework.touch_room_presence(room["room_id"], "owner")
            probe = subprocess.run([sys.executable, "-c", """
import sys
from pathlib import Path
from app import database, framework, takeover
database.DB_PATH = Path(sys.argv[1])
database.init_db()
room = framework.get_room(sys.argv[2])
assert takeover.is_due(room) == (sys.argv[3] == 'True')
""", str(database.DB_PATH), room["room_id"], str(fresh)], capture_output=True, text=True, timeout=15)
            self.assertEqual(probe.returncode, 0, probe.stderr)

    async def test_chat_only_target_wakes_and_offline_persistence(self):
        room=self.create("train_cards",3)
        for ai in ("ai1","ai2"): invites.join_invite(room["invite_code"],"ai",ai,display_name="同名")
        with patch.object(invites.secrets,"SystemRandom") as random:
            random.return_value.shuffle.side_effect=lambda members: None
            room=invites.start_invite(room["room_id"],"human","owner")
        for ai in ("ai1","ai2"):
            framework.claim_mcp_bootstrap(room["room_id"],ai)
            framework.read_new_room_events(room["room_id"],ai)
        async def wait(ai):
            return await self.client.post('/mcp/play',json={"action":"state","room_id":room["room_id"],"player_id":ai,"wait":True})
        waits=[asyncio.create_task(wait(ai)) for ai in ("ai1","ai2")]
        try:
            await asyncio.sleep(.03)
            framework.post_message(room["room_id"],"human","owner","普通聊天 @同名")
            main_module.revision_events.notify(room["room_id"])
            await asyncio.sleep(.03)
            self.assertFalse(any(t.done() for t in waits))
            member=next(p for p in room["participants"] if p["player_id"]=="ai1")
            self.assertEqual(member["participant_kind"], "bound_machine")
            sent = await self.client.post(f'/api/rooms/{room["room_id"]}/messages',
                                          json={"player_id":"owner", "message":f'@{member["handle"]} 来看看'})
            self.assertEqual(sent.status_code, 200, sent.text)
            reply=await asyncio.wait_for(waits[0],1)
            self.assertIn("来看看",json.dumps(reply.json(),ensure_ascii=False))
            self.assertFalse(waits[1].done())
            self.assertFalse(invites.has_targeted_chat(room["room_id"],"ai1"))
            chat=await self.client.post('/mcp/play',json={"action":"chat","room_id":room["room_id"],"player_id":"ai1","message":"没轮到我也能聊"})
            self.assertEqual(chat.status_code,200,chat.text)
            self.assertEqual(framework.get_room(room["room_id"])["revision"],room["revision"])
            framework.post_message(room["room_id"],"human","owner",f'@{member["handle"]} 离线提醒')
            self.assertTrue(invites.has_targeted_chat(room["room_id"],"ai1"))
            offline = await self.client.post('/mcp/play', json={"action":"state", "room_id":room["room_id"], "player_id":"ai1"})
            self.assertEqual(offline.status_code, 200, offline.text)
            self.assertIn("离线提醒", json.dumps(offline.json(), ensure_ascii=False))
            self.assertFalse(invites.has_targeted_chat(room["room_id"],"ai1"))
        finally:
            for task in waits: task.cancel()
            await asyncio.gather(*waits,return_exceptions=True)

    async def test_ordinary_chat_has_no_invite_mentions_and_stays_deliverable(self):
        room = framework.create_room("tictactoe", "human_first", "human", "owner", "ai1", require_confirmations=False)
        framework.claim_mcp_bootstrap(room["room_id"], "ai1")
        framework.read_new_room_events(room["room_id"], "ai1")
        member = next(p for p in room["participants"] if p["player_id"] == "ai1")
        text = f"普通留言 @{member['handle']} 保留原文"
        response = await self.client.post(f"/api/rooms/{room['room_id']}/messages",
                                          json={"player_id": "owner", "message": text})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(framework.get_room(room["room_id"])["revision"], room["revision"])
        self.assertFalse(invites.has_targeted_chat(room["room_id"], "ai1"))
        self.assertFalse(main_module._participant_response_due(room, "ai1"))
        with database.connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM room_mentions").fetchone()[0], 0)
        # Historic ordinary-room mention rows must not revive invite semantics.
        with database.write_transaction() as conn:
            event_id = conn.execute("SELECT MAX(id) FROM room_messages WHERE room_id=?", (room["room_id"],)).fetchone()[0]
            conn.execute("INSERT INTO room_mentions VALUES (?, ?)", (event_id, "ai1"))
        self.assertFalse(invites.has_targeted_chat(room["room_id"], "ai1"))
        self.assertFalse(main_module._participant_response_due(room, "ai1"))
        framework.play_move(room["room_id"], "human", "owner", {"row": 0, "col": 0}, expected_revision=room["revision"])
        reply = await self.client.post("/mcp/play", json={"action": "state", "room_id": room["room_id"], "player_id": "ai1"})
        self.assertEqual(reply.status_code, 200, reply.text)
        self.assertIn(text, json.dumps(reply.json(), ensure_ascii=False))
        # Same recipient in another room cannot inherit this room's reminder.
        invite = invites.create_invite("tictactoe", "human", "owner", target_player_count=2)
        invite = invites.join_invite(invite["invite_code"], "ai", "ai1")
        framework.post_message(invite["room_id"], "human", "owner", f"@{member['handle']} 邀请房离线留言")
        self.assertTrue(invites.has_targeted_chat(invite["room_id"], "ai1"))
        self.assertFalse(invites.has_targeted_chat(room["room_id"], "ai1"))

    def test_additive_migration_and_invite_stakes_follow_game_capabilities(self):
        room=self.ready()
        database.init_db(); database.init_db()
        self.assertEqual(framework.get_room(room["room_id"])["participants"],room["participants"])
        staked = invites.create_invite("uno","human","stake-owner",target_player_count=3,stake=10)
        self.assertEqual(staked["stake"], 10)
        self.assertEqual(invites.preview_invite(staked["invite_code"])["stake"], 10)
        with self.assertRaisesRegex(framework.DuelError,"筹码结算规则"):
            invites.create_invite("yahtzee","human","no-stake",target_player_count=2,stake=10)


    async def test_configurable_timeout_seconds_and_legacy_boolean(self):
        for seconds, before, after in ((90, 89, 91), (180, 179, 181)):
            owner = f"owner-{seconds}"
            with patch.object(invites, "require_npc"):
                room = invites.create_invite("tictactoe", "human", owner,
                                             target_player_count=2,
                                             timeout_takeover_seconds=seconds)
            invites.join_invite(room["invite_code"], "human", f"friend-{seconds}")
            room = invites.start_invite(room["room_id"], "human", owner)
            self.assertEqual(room["timeout_takeover_seconds"], seconds)
            self.assertTrue(room["timeout_takeover"])
            with database.write_transaction() as conn:
                conn.execute("UPDATE room_invites SET turn_started_at=? WHERE room_id=?",
                             ((datetime.now(timezone.utc)-timedelta(seconds=before)).isoformat(), room["room_id"]))
            self.assertFalse(takeover.is_due(framework.get_room(room["room_id"])))
            with database.write_transaction() as conn:
                conn.execute("UPDATE room_invites SET turn_started_at=? WHERE room_id=?",
                             ((datetime.now(timezone.utc)-timedelta(seconds=after)).isoformat(), room["room_id"]))
            self.assertTrue(takeover.is_due(framework.get_room(room["room_id"])))

        with patch.object(invites, "require_npc"):
            legacy = invites.create_invite("tictactoe", "human", "legacy-owner",
                                           target_player_count=2, timeout_takeover=True)
        self.assertEqual(legacy["timeout_takeover_seconds"], 90)
        self.assertTrue(legacy["timeout_takeover"])
        with self.assertRaisesRegex(framework.DuelError, "关闭、90 秒或 180 秒"):
            invites.create_invite("tictactoe", "human", "bad-timeout",
                                  target_player_count=2, timeout_takeover_seconds=120)

    async def test_legacy_timeout_legal_moves_and_provider_reclaim_race(self):
        from app.npc_providers import ProviderDecision
        from app import npc_personas
        for game in ("tictactoe", "gomoku", "othello", "connect4", "jungle", "xiangqi"):
            room = self.ready(game)
            actor = next(p for p in room["participants"] if p["player_id"] == room["current_player_id"])
            actions = takeover.timeout_legal_actions(room, actor)
            self.assertTrue(actions, game)
            get_game(game).validate_action(room["board_state"], actions[0], actor)
        room = self.ready("tictactoe")
        with database.write_transaction() as conn:
            conn.execute("UPDATE room_invites SET timeout_takeover=1 WHERE room_id=?", (room["room_id"],))
        # Boundary: no automation at 89 seconds.
        with database.write_transaction() as conn:
            conn.execute("UPDATE room_invites SET turn_started_at=? WHERE room_id=?",
                         ((datetime.now(timezone.utc)-timedelta(seconds=89)).isoformat(),room["room_id"]))
        self.assertIsNone(await takeover.run_timeout_turn(room["room_id"]))
        self.expire(room)
        begun, release = asyncio.Event(), asyncio.Event()
        class SlowProvider:
            async def decide(self, request):
                begun.set()
                await release.wait()
                return ProviderDecision(request.legal_actions[0]["action_id"], None)
        with patch.object(npc_personas,"select_personas",return_value=[NpcPersona("test", "测试NPC", "测试")]):
            pending=asyncio.create_task(takeover.run_timeout_turn(room["room_id"],SlowProvider()))
            await asyncio.wait_for(begun.wait(),2)
            invites.reclaim(room["room_id"],"human",room["current_player_id"])
            release.set()
            self.assertIsNone(await pending)
        self.assertEqual(framework.get_room(room["room_id"])["revision"],room["revision"])

    async def test_takeover_projection_tracks_actual_viewer_work_and_clears(self):
        from app.npc_providers import ProviderDecision
        from app import npc_personas
        for outcome in ("completed", "cancelled", "reclaimed", "player_moved", "presence_expired", "provider_failed"):
            with self.subTest(outcome=outcome):
                room = self.ready("tictactoe")
                with database.write_transaction() as conn:
                    conn.execute("UPDATE room_invites SET timeout_takeover=1 WHERE room_id=?", (room["room_id"],))
                room = self.expire(room)
                actor = room["current_player_id"]
                other = next(p["player_id"] for p in room["participants"] if p["player_id"] != actor)
                def active(pid):
                    current = framework.get_room(room["room_id"])
                    return framework.project_room_for_viewer(current, pid)["viewer"]["temporary_takeover_active"]
                self.assertFalse(active(actor), "timer expiry alone is not active work")
                begun, release = asyncio.Event(), asyncio.Event()
                class SlowProvider:
                    async def decide(self, request):
                        begun.set()
                        await release.wait()
                        if outcome == "provider_failed":
                            raise RuntimeError("fake provider failure")
                        return ProviderDecision(request.legal_actions[0]["action_id"], None)
                with patch.object(npc_personas, "select_personas", return_value=[NpcPersona("test", "测试", "测试")]):
                    task = asyncio.create_task(takeover.run_timeout_turn(room["room_id"], SlowProvider()))
                    try:
                        await asyncio.wait_for(begun.wait(), 2)
                        self.assertTrue(active(actor))
                        self.assertFalse(active(other), "other seats cannot reclaim this work")
                        # HTTP projection must expose the same state with unchanged revision.
                        response = await self.client.get(f'/api/rooms/{room["room_id"]}', headers={"X-Duel-Human-Player": actor})
                        self.assertEqual(response.status_code, 200, response.text)
                        self.assertTrue(response.json()["room"]["viewer"]["temporary_takeover_active"])
                        if outcome == "cancelled":
                            task.cancel()
                        elif outcome == "reclaimed":
                            invites.reclaim(room["room_id"], "human", actor)
                            self.assertFalse(active(actor), "reclaim hides work before provider returns")
                        elif outcome == "presence_expired":
                            self.age_presence(room)
                            self.assertFalse(active(actor))
                        elif outcome == "player_moved":
                            framework.play_move(room["room_id"], "human", actor, {"row": 0, "col": 0}, expected_revision=room["revision"])
                            self.assertFalse(active(actor), "stale revision cannot advertise takeover")
                        release.set()
                        result, = await asyncio.gather(task, return_exceptions=True)
                        if outcome == "cancelled":
                            self.assertIsInstance(result, asyncio.CancelledError)
                        elif outcome in {"reclaimed", "player_moved", "presence_expired"}:
                            self.assertIsNone(result)
                        else:
                            self.assertEqual(result["revision"], room["revision"] + 1)
                        self.assertFalse(active(actor))
                        self.assertFalse(active(other))
                        self.assertFalse(takeover._active_turns)
                    finally:
                        task.cancel()
                        await asyncio.gather(task, return_exceptions=True)

    async def test_two_inflight_takeovers_commit_one_revision(self):
        from app.npc_providers import ProviderDecision
        from app import npc_personas, chips
        room=self.ready("tictactoe")
        with database.write_transaction() as conn:
            conn.execute("UPDATE room_invites SET timeout_takeover=1 WHERE room_id=?", (room["room_id"],))
        self.expire(room)
        actor=room["current_player_id"]
        balance=chips.get_wallet("human",actor)["balance"]
        both=asyncio.Event()
        class Provider:
            count=0
            async def decide(self, request):
                self.count+=1
                if self.count==2: both.set()
                await both.wait()
                return ProviderDecision(request.legal_actions[0]["action_id"],None)
        provider=Provider()
        with patch.object(npc_personas,"select_personas",return_value=[NpcPersona("test","测试","测试")]):
            results=await asyncio.wait_for(asyncio.gather(
                takeover.run_timeout_turn(room["room_id"],provider),
                takeover.run_timeout_turn(room["room_id"],provider)),2)
        self.assertEqual(sum(r is not None for r in results),1)
        self.assertEqual(framework.get_room(room["room_id"])["revision"],room["revision"]+1)
        self.assertEqual(chips.get_wallet("human",actor)["balance"],balance)

    def test_no_revision_invite_move_is_rejected_and_handles_survive_rename(self):
        room=self.ready()
        with self.assertRaisesRegex(framework.DuelError,"revision"):
            framework.play_move(room["room_id"],"human",room["current_player_id"],{"row":0,"col":0})
        handles=[p["handle"] for p in room["participants"]]
        self.assertEqual(len(set(handles)),2)
        framework.update_participant_display_names("owner",{"owner":"改名"})
        self.assertEqual([p["handle"] for p in framework.get_room(room["room_id"])["participants"]],handles)


    def test_numeric_mentions_keep_canonical_targets_despite_names_and_forged_suffixes(self):
        room = invites.create_invite("uno", "human", "human:1", target_player_count=4,
                                     display_name="同名")
        for player_id in ("123", "1234", "456:2"):
            room = invites.join_invite(room["invite_code"], "ai", player_id,
                                       display_name="同名")
        self.assertEqual([p["handle"] for p in room["participants"]],
                         ["1", "123", "1234", "456"])
        for message in ("@同名", "@123~forged", "@123-fake", "@123abc", "@@123"):
            framework.post_message(room["room_id"], "human", "human:1", message)
        with database.connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM room_mentions").fetchone()[0], 0)
        framework.post_message(room["room_id"], "human", "human:1", "@1234 来看看")
        framework.post_message(room["room_id"], "human", "human:1", "@123 来看看")
        framework.post_message(room["room_id"], "human", "human:1", "@456 来看看")
        with database.connect() as conn:
            targets = [row[0] for row in conn.execute(
                "SELECT player_id FROM room_mentions ORDER BY event_id")]
        self.assertEqual(targets, ["1234", "123", "456:2"])

    async def test_mcp_chat_mentions_only_visible_other_bound_machines(self):
        room = invites.create_invite("uno", "ai", "123", target_player_count=3)
        invites.join_invite(room["invite_code"], "ai", "456:2")
        room = invites.join_invite(room["invite_code"], "human", "human:7")
        response = await self.client.post('/mcp/play', json={
            "action": "chat", "player_id": "123", "room_id": room["room_id"],
            "message": "@456 看这里 @123 @7",
        })
        self.assertEqual(response.status_code, 200, response.text)
        self.assertNotIn("chat_refs", response.json())
        self.assertEqual(framework.get_room(room["room_id"])["revision"], room["revision"])
        framework.post_message(room["room_id"], "ai", "123", "@456 私聊",
                               visible_to_player_ids={"123"})
        with database.connect() as conn:
            targets = [row[0] for row in conn.execute(
                "SELECT player_id FROM room_mentions ORDER BY event_id")]
        self.assertEqual(targets, ["456:2"])


    async def test_ai_only_liars_dice_round_ack_and_scheduler_restart(self):
        room=invites.create_invite("liars_dice","ai","owner",target_player_count=2)
        invites.join_invite(room["invite_code"],"ai","bot")
        room=invites.start_invite(room["room_id"],"ai","owner")
        room=framework.play_move(room["room_id"],"ai",room["current_player_id"],
                                 {"action":"bid","quantity":1,"face":2}, expected_revision=room["revision"])
        room=framework.play_move(room["room_id"],"ai",room["current_player_id"],
                                 {"action":"challenge"}, expected_revision=room["revision"])
        self.assertTrue(room["current_player_id"])
        actor=room["current_player_id"]
        state=framework.project_room_for_viewer(room,actor)
        self.assertEqual(state["private_state"]["legal_actions"],[{"action":"acknowledge_round"}])
        reply=await self.client.post('/mcp/play',json={"action":"move","player_id":actor,"room_id":room["room_id"],"revision":room["revision"],"move":{"action":"acknowledge_round"}})
        self.assertEqual(reply.status_code,200,reply.text)
        room=self.ready("train_cards",2,timeout=True)
        self.expire(room)
        changed=asyncio.Event()
        async def schedule_npc(room): return False
        scheduler=takeover.TakeoverScheduler(lambda room_id: changed.set(),schedule_npc)
        await scheduler.start()
        try:
            await asyncio.wait_for(changed.wait(),2)
            self.assertEqual(framework.get_room(room["room_id"])["revision"],room["revision"]+1)
        finally:
            await scheduler.shutdown()
        self.assertFalse(scheduler.workers)


    def test_migrate_consistent_legacy_copy_without_changing_room_or_wallet(self):
        import sqlite3
        from contextlib import closing
        old=framework.create_room("tictactoe","human_first","human","legacyHuman",opponent_id="legacyAI")
        with database.write_transaction() as conn:
            conn.execute("DROP TABLE room_mentions")
            conn.execute("DROP TABLE room_invites")
        copied=Path(self.tmp.name)/"snapshot.db"
        with closing(sqlite3.connect(database.DB_PATH)) as source, closing(sqlite3.connect(copied)) as target:
            source.backup(target)
        with patch.object(database,"DB_PATH",copied):
            database.init_db(); database.init_db()
            after=framework.get_room(old["room_id"])
            self.assertEqual(after["board_state"],old["board_state"])
            self.assertEqual(after["participants"],old["participants"])
            self.assertEqual(after["revision"],old["revision"])
            with closing(database.connect()) as conn:
                self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0],"ok")
                self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(),[])

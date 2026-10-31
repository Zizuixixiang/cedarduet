"""Ordinary household-AI rooms through the same MCP, game and spectator paths."""
import base64
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
from app import database, framework, main
from app.games import GAMES
from app.npc_personas import NpcPersona
from app.npc_scheduler import is_system_npc_turn


class AiOnlyRoomsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="duel-ai-only-")
        self.addCleanup(self.tmp.cleanup)
        self.db_patch = patch.object(database, "DB_PATH", Path(self.tmp.name) / "test.db")
        self.db_patch.start()
        self.addCleanup(self.db_patch.stop)
        database.init_db()
        # Real bound machines are never model-driven here; no external NPC calls.
        schedule = patch.object(main, "_schedule_current_system_npc", new_callable=AsyncMock)
        self.schedule = schedule.start()
        self.addCleanup(schedule.stop)
        personas = patch.object(main, "select_personas", side_effect=lambda count: [
            NpcPersona(f"test-{i}", f"测试NPC{i}", "isolated") for i in range(count)])
        personas.start()
        self.addCleanup(personas.stop)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test")
        self.addAsyncCleanup(self.client.aclose)

    async def post(self, action, expected=200, **params):
        response = await self.client.post("/mcp/play", json={"action": action, "player_id": "701", **params})
        self.assertEqual(response.status_code, expected, response.text)
        return response.json()

    async def new(self, game="tictactoe", ids=("701", "702"), expected=200, **params):
        data = await self.post("new", expected=expected, game_type=game, include_human=False,
            participant_ids=list(ids), household_ais={framework.account_key(pid): f"小机{pid}" for pid in ids},
            **params)
        return framework.get_room(data.get("room", data)["room_id"], "ai", "701") if expected == 200 else data

    def dump(self):
        with closing(database.connect()) as conn:
            return "\n".join(conn.iterdump())

    def headers(self, ids=("701", "702")):
        return {"X-Duel-Human-Player": "710", "X-Duel-Bound-Ais": base64.urlsafe_b64encode(
            json.dumps([{"id": pid, "name": f"小机{pid}"} for pid in ids]).encode()).decode()}

    async def finish_tictactoe(self, room):
        for row, col in ((0, 0), (1, 0), (0, 1), (1, 1), (0, 2)):
            await self.post("move", room_id=room["room_id"], player_id=room["current_player_id"],
                            revision=room["revision"], move={"row": row, "col": col})
            room = framework.get_room(room["room_id"], "ai", "701")
        self.assertEqual(room["status"], "finished")
        return room

    async def test_actions_terminal_rematch_and_notifications_only_real_participants(self):
        room = await self.new(stake=1)
        self.assertEqual(room["status"], "pending")
        self.assertEqual([p["join_status"] for p in room["participants"]], ["joined", "invited"])
        await self.post("move", expected=409, room_id=room["room_id"], revision=0, move={"row": 0, "col": 0})
        await self.post("accept", player_id="702", room_id=room["room_id"])
        room = framework.get_room(room["room_id"], "ai", "701")
        self.assertEqual(room["current_player_id"], "701")
        self.assertFalse(is_system_npc_turn(room))
        room = await self.finish_tictactoe(room)
        self.assertEqual(room["winner_player_id"], "701")
        self.assertEqual(room["result"]["settlement_deltas"], {"701": 1, "702": -1})
        await self.post("state", room_id=room["room_id"], player_id="702", full_state=True)
        rematch = await self.post("rematch", room_id=room["room_id"], household_ais={"701": "甲", "702": "乙"})
        new = framework.get_room(rematch.get("room", rematch)["room_id"], "ai", "701")
        self.assertEqual(new["rematch_of_room_id"], room["room_id"])
        self.assertEqual(new["current_player_id"], "702")
        self.assertEqual(new["status"], "pending")
        self.assertEqual(new["stake"], 1)
        with closing(database.connect()) as conn:
            subjects = {tuple(r) for r in conn.execute("SELECT subject_type,subject_id FROM notifications")}
            self.assertTrue(subjects)
            self.assertLessEqual(subjects, {("ai", "701"), ("ai", "702")})
        self.assertTrue(all(p["role"] == "ai" for p in new["participants"]))

    async def test_backend_rejects_missing_grants_caller_omission_duplicate_or_opponent(self):
        cases = [
            ({"participant_ids": ["701", "702"]}, 403),
            ({"participant_ids": ["702", "703"], "household_ais": {"702": "乙", "703": "丙"}}, 403),
            ({"participant_ids": ["701", "703"], "household_ais": {"701": "甲", "702": "乙"}}, 403),
            ({"participant_ids": ["701", "701:2"], "household_ais": {"701": "甲"}}, 400),
            ({"participant_ids": ["701", "npc:fake"], "household_ais": {"701": "甲", "npc:fake": "伪"}}, 403),
            ({"participant_ids": ["701", "702"], "household_ais": {"701": "甲", "702": "乙"}, "opponent_id": "710"}, 403),
        ]
        for params, code in cases:
            with self.subTest(params=params):
                await self.post("new", expected=code, game_type="tictactoe", include_human=False, **params)
        await self.post("new", expected=422, game_type="uno", include_human="false")
        with closing(database.connect()) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM rooms").fetchone()[0], 0)

    async def test_rematch_rechecks_household_and_participation(self):
        room = await self.finish_tictactoe(await self.new())
        before = self.dump()
        for player, names in (("701", {"701": "甲"}), ("703", {"701": "甲", "702": "乙", "703": "丙"})):
            await self.post("rematch", expected=403, room_id=room["room_id"], player_id=player, household_ais=names)
        self.assertEqual(self.dump(), before)

    async def test_nonparticipant_cannot_read_write_accept_or_leave(self):
        room = await self.new(stake=1)
        for action, params in (("state", {}), ("accept", {}), ("leave", {}), ("resign", {}),
                               ("chat", {"message": "冒名"}), ("move", {"revision": room["revision"], "move": {"row": 0, "col": 0}})):
            before = self.dump()
            await self.post(action, expected=403, player_id="703", room_id=room["room_id"], **params)
            self.assertEqual(self.dump(), before)

    async def test_npc_fill_respects_game_count_capability_and_provider(self):
        await self.new("uno", ids=("701",), expected=400)
        await self.new("uno", ids=("701",), target_player_count=2, expected=400)
        await self.new("tictactoe", ids=("701",), target_player_count=2, fill_with_npcs=True, expected=400)
        await self.new("uno", ids=("701",), target_player_count=6, fill_with_npcs=True, expected=400)
        await self.new("mahjong", target_player_count=3, expected=400)
        await self.new("uno", target_player_count=2, stake=1000000000, expected=409)
        with patch.object(main, "npc_provider_capabilities", return_value={"available": False, "reason": "isolated unavailable"}):
            await self.new("uno", ids=("701",), target_player_count=2, fill_with_npcs=True, expected=503)
        with patch.object(main, "npc_provider_capabilities", return_value={"available": True}):
            room = await self.new("uno", ids=("701", "702"), target_player_count=4, fill_with_npcs=True)
        self.assertEqual([p["participant_kind"] for p in room["participants"]],
                         ["bound_machine", "bound_machine", "system_npc", "system_npc"])
        self.assertEqual(room["current_player_id"], "701")
        self.assertFalse(is_system_npc_turn(room))

    async def test_caller_plus_local_npc_stakes_start_and_same_npc_can_rematch(self):
        with patch.object(main, "npc_provider_capabilities", side_effect=AssertionError("local game needs no provider")):
            room = await self.new("bomb_plane", ids=("701",), target_player_count=2, fill_with_npcs=True, stake=1)
            self.assertEqual(room["status"], "playing")
            self.assertIsNone(room["confirmation_expires_at"])
            self.assertTrue(room["confirmation_required"])
            # A normal resignation is a valid terminal outcome for authoritative rematches.
            room = framework.resign(room["room_id"], "ai", "701")
            rematch = await self.post("rematch", room_id=room["room_id"], household_ais={"701": "甲"})
        new = framework.get_room(rematch["room"]["room_id"], "ai", "701")
        self.assertEqual(new["status"], "playing")
        self.assertEqual(new["participants"][0]["npc_persona_id"], "test-0")
        self.assertEqual(new["current_player_id"], "npc:test-0")
        self.assertTrue(is_system_npc_turn(new))
        self.assertEqual(self.schedule.call_args.args[0]["room_id"], new["room_id"])

    async def test_all_game_initialization_bootstrap_private_views_and_read_only_spectating(self):
        for game, plugin in GAMES.items():
            with self.subTest(game=game):
                database.DB_PATH = Path(self.tmp.name) / f"{game}.db"
                database.init_db()
                count = min(plugin.resolved_allowed_player_counts())
                ids = tuple(str(701 + i) for i in range(count))
                room = await self.new(game, ids=ids)
                self.assertEqual(room["status"], "playing")
                self.assertEqual(len(room["participants"]), count)
                for pid in ids[:2]:
                    await self.post("state", player_id=pid, room_id=room["room_id"], full_state=True)
                before = self.dump()
                for pid in ids[:2]:
                    response = await self.client.get("/api/rooms/" + room["room_id"],
                        headers=self.headers(), params={"perspective": pid, "wait": "true", "after_revision": room["revision"]})
                    self.assertEqual(response.status_code, 200, response.text)
                    expected = framework.project_room_for_viewer(room, pid)
                    self.assertEqual(response.json()["room"]["private_state"], expected["private_state"])
                    self.assertEqual(response.json()["room"]["board_state"], expected["board_state"])
                self.assertEqual(self.dump(), before)

    async def test_spectator_pending_terminal_revoked_binding_and_no_human_actions(self):
        room = await self.new(stake=1)
        for terminal in (False, True):
            if terminal:
                await self.post("accept", player_id="702", room_id=room["room_id"])
                room = await self.finish_tictactoe(framework.get_room(room["room_id"], "ai", "701"))
            before = self.dump()
            listing = await self.client.get("/api/spectating/rooms", headers=self.headers())
            self.assertEqual([r["room_id"] for r in listing.json()["rooms"]], [room["room_id"]])
            response = await self.client.get("/api/rooms/" + room["room_id"], headers=self.headers())
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["room"]["status"], "finished" if terminal else "pending")
            denied = await self.client.get("/api/rooms/" + room["room_id"], headers=self.headers(()), params={"perspective": "701"})
            self.assertEqual(denied.status_code, 403)
            self.assertEqual(self.dump(), before)

    async def test_default_pair_still_requires_human_and_confirmation(self):
        data = await self.post("new", game_type="tictactoe", opponent_id="710", stake=1)
        room = framework.get_room(data.get("room", data)["room_id"], "ai", "701")
        self.assertEqual(room["status"], "pending")
        self.assertEqual({(p["player_id"], p["role"]) for p in room["participants"]}, {("710", "human"), ("701", "ai")})

    async def test_per_caller_capacity_and_cross_process_restore(self):
        first = await self.new()
        script = """
import json,sys
from app import database,framework
database.init_db()
r=framework.get_room(sys.argv[1], 'ai', '701')
assert [p['player_id'] for p in r['participants']]==['701','702']
r=framework.play_move(r['room_id'],'ai','701',{'row':0,'col':0},expected_revision=r['revision'])
print(json.dumps({'revision':r['revision'],'next':r['current_player_id']}))
"""
        restored = subprocess.run([sys.executable, "-c", script, first["room_id"]],
            env={**os.environ, "DUEL_DB_PATH": str(database.DB_PATH), "PYTHONDONTWRITEBYTECODE": "1"},
            text=True, capture_output=True, check=True)
        self.assertEqual(json.loads(restored.stdout), {"revision": first["revision"] + 1, "next": "702"})
        for _ in range(9):
            await self.new()
        await self.new(expected=409)

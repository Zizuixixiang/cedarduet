"""Waiting rooms remain discoverable and reopenable through the real web APIs."""
import base64
from datetime import datetime
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

import httpx

from app import database, framework, main
from app.npc_personas import NpcPersona
from tests.test_liars_dice_teams import OPTIONS, expire


class DiceLobbyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="duel-dice-lobby-")
        self.addCleanup(self.directory.cleanup)
        patcher = patch.object(database, "DB_PATH", Path(self.directory.name) / "test.db")
        patcher.start()
        self.addCleanup(patcher.stop)
        database.init_db()
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://dice.test")
        self.addAsyncCleanup(self.client.aclose)
        self.headers = {"X-Duel-Human-Player": "human-1", "X-Duel-Bound-Ais": base64.b64encode(json.dumps([
            {"id": f"ai-{i}", "name": f"小机{i}"} for i in range(1, 4)
        ]).encode()).decode()}

    async def request(self, method, path, body=None, *, headers=None):
        reply = await self.client.request(method, path, headers=headers or self.headers, json=body)
        self.assertEqual(reply.status_code, 200, reply.text)
        return reply.json()

    async def lifecycle(self, invite, fill):
        for _ in range(9):
            framework.create_room('tictactoe', 'human_first', 'human', 'human-1', opponent_id='ai-1')
        other_order = [r['room_id'] for r in framework.list_human_rooms('human-1')]
        count = 6 if fill else 4
        created = await self.request("POST", "/api/invites" if invite else "/api/rooms", {
            "player_id": "human-1", "ai_players": ["ai-1"] if invite else ["ai-1", "ai-2"] if fill else ["ai-1", "ai-2", "ai-3"],
            "game_type": "liars_dice", "target_player_count": count, "game_options": OPTIONS,
        })
        room = created["room"]
        rid = room["room_id"]
        self.assertEqual(room["status"], "waiting")
        self.assertFalse(room["team_selection"]["can_start"])
        duration = datetime.fromisoformat(room["team_selection"]["start_after"]) - datetime.fromisoformat(room["created_at"])
        self.assertGreaterEqual(duration.total_seconds(), 19)
        self.assertLess(duration.total_seconds(), 22)
        if invite:
            # Even after the selection window, household members alone cannot start.
            saved_state = framework.get_room(rid)['board_state']
            expire(framework.get_room(rid))
            denied = await self.client.post(f"/api/rooms/{rid}/start", headers=self.headers, json={"fill_with_npcs": True})
            self.assertEqual(denied.status_code, 409)
            self.assertIn('外部受邀', denied.text)
            with database.write_transaction() as conn:
                conn.execute('UPDATE rooms SET board_state=? WHERE room_id=?', (json.dumps(saved_state), rid))
            for actor in (["human-2"] if fill else ["human-2", "human-3"]):
                await self.request("POST", "/api/invites/join", {"invite_code": room["invite_code"]},
                                   headers={"X-Duel-Human-Player": actor})
        for _ in range(2):
            identity = await self.request("GET", "/api/whoami")
            self.assertEqual(identity['rooms'][0]['room_id'], rid)
            self.assertEqual([r['room_id'] for r in identity['rooms'][1:]], other_order)
            summary = next(r for r in identity["rooms"] if r["room_id"] == rid)
            self.assertEqual((summary['participant_count'], summary['target_player_count']), (3, 6) if fill else (4, 4))
            self.assertEqual(summary["status"], "waiting")
            self.assertTrue(summary["team_selection_pending"])
            self.assertNotIn(rid, [r["room_id"] for r in identity["pending_invitations"]])
            self.assertNotIn(rid, [r['room_id'] for r in framework.list_human_rooms('human-1', {'ai-1':'小机'}, spectating=True)])
            spectator_order = [r['room_id'] for r in framework.list_human_rooms(
                'human-outsider', {'ai-1':'小机'}, spectating=True)]
            self.assertEqual(spectator_order[-1], rid)
            self.assertEqual(set(spectator_order[:-1]), set(other_order))
            room = (await self.request("GET", f"/api/rooms/{rid}"))["room"]
            self.assertEqual(room["room_id"], rid)
            self.assertEqual(room["status"], "waiting")
            self.assertFalse(any(p["participant_kind"] == "system_npc" for p in room["participants"]))
        for team, seat in (("red", 1), ("blue", 2)):
            room = (await self.request("POST", f"/api/rooms/{rid}/move", {
                "player_id": "human-1", "revision": room["revision"],
                "move": {"action": "choose_team_seat", "team": team, "seat": seat},
            }))["room"]
        forged = await self.client.post(f"/api/rooms/{rid}/move", headers=self.headers, json={
            "player_id": "ai-1", "revision": room["revision"],
            "move": {"action": "choose_team_seat", "team": "red", "seat": 1},
        })
        self.assertEqual(forged.status_code, 403)
        await self.request("POST", "/mcp/play", {
            "action": "move", "player_id": "ai-1", "room_id": rid, "revision": room["revision"],
            "move": {"action": "choose_team_seat", "team": "red", "seat": 1},
        })
        early = await self.client.post(f"/api/rooms/{rid}/start", headers=self.headers, json={"fill_with_npcs": fill})
        self.assertEqual(early.status_code, 409)
        room = expire(framework.get_room(rid))
        with patch("app.invites.npc_provider_capabilities", return_value={"available": True}), \
             patch("app.invites.select_personas", side_effect=lambda n: [NpcPersona(f"test-{i}", f"NPC {i}", "test") for i in range(n)]), \
             patch("app.npc_personas.select_personas", side_effect=lambda n: [NpcPersona(f"test-{i}", f"NPC {i}", "test") for i in range(n)]), \
             patch("app.main._schedule_current_system_npc", new=AsyncMock()):
            room = (await self.request("POST", f"/api/rooms/{rid}/start", {"fill_with_npcs": fill}))["room"]
        self.assertEqual(room["status"], "playing")
        self.assertEqual(len(room["participants"]), count)
        self.assertEqual(room["board_state"]["teams"]["blue"][1], "human-1")
        self.assertEqual(room["board_state"]["teams"]["red"][0], "ai-1")
        self.assertEqual(sum(p["participant_kind"] == "system_npc" for p in room["participants"]), 3 if fill else 0)
        summary = next(r for r in (await self.request("GET", "/api/whoami"))["rooms"] if r["room_id"] == rid)
        self.assertNotIn("team_selection_pending", summary)

    async def test_regular_waiting_list_reenter_full_start(self):
        await self.lifecycle(invite=False, fill=False)

    async def test_regular_waiting_list_reenter_npc_start(self):
        await self.lifecycle(invite=False, fill=True)

    async def test_invite_waiting_list_reenter_npc_start(self):
        await self.lifecycle(invite=True, fill=True)

    async def test_invite_waiting_list_reenter_full_start(self):
        await self.lifecycle(invite=True, fill=False)

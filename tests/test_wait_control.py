import asyncio
import importlib.util
import json
import tempfile
import time
import unittest
from pathlib import Path
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import httpx
from app import database, framework, invites, main, takeover
from app.wait_control import WaitControl, WaitCancelled, LEASE_SECONDS


class WaitControlTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="duel-wait-control-")
        self.patches = [
            patch.object(database, "DB_PATH", Path(self.temp.name) / "test.db"),
            patch.object(main, "revision_events", main.RevisionEvents()),
            patch.object(main, "wait_control", WaitControl()),
        ]
        for item in self.patches:
            item.start()
        database.init_db()
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://duel.test")
        await self.new_room()

    async def asyncTearDown(self):
        await self.client.aclose()
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    async def new_room(self):
        response = await self.call("new", room_id=None, opponent_id="human-1", game_type="tictactoe", mode="ai_first")
        self.room = response["room"]["room_id"]
        await self.call("move", move={"row": 0, "col": 0})

    async def call(self, action, **params):
        body = {"action": action, "player_id": "ai-1", "room_id": getattr(self, "room", None), **params}
        response = await self.client.post("/mcp/play", json=body)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    async def waiting(self):
        async def poll():
            while main.revision_events.waiting_count != 1:
                await asyncio.sleep(0.001)
        await asyncio.wait_for(poll(), 2)

    def human_move(self):
        framework.play_move(self.room, "human", "human-1", {"row": 1, "col": 1})
        main.revision_events.notify(self.room)

    def snapshot(self):
        with database.connect() as conn:
            return list(conn.iterdump())

    def assert_cancelled(self, payload):
        self.assertEqual(payload["status"], "wait_cancelled")
        self.assertFalse(payload.get("your_turn"))
        self.assertNotIn("events", payload)
        self.assertNotIn("next_call", payload)

    async def test_new_wait_supersedes_old_and_only_latest_receives_move(self):
        first = asyncio.create_task(self.call("state", wait=True))
        await self.waiting()
        second = asyncio.create_task(self.call("state", wait=True))
        self.assert_cancelled(await asyncio.wait_for(first, 1))
        await self.waiting()
        self.human_move()
        self.assertTrue((await asyncio.wait_for(second, 1))["your_turn"])
        self.assertEqual(main.revision_events.waiting_count, 0)

    async def test_cancel_is_immediate_read_only_and_new_wait_recovers(self):
        first = asyncio.create_task(self.call("state", wait=True))
        await self.waiting()
        before = self.snapshot()
        self.assert_cancelled(await self.call("cancel_wait"))
        self.assert_cancelled(await asyncio.wait_for(first, 1))
        self.assertEqual(before, self.snapshot())
        second = asyncio.create_task(self.call("state", wait=True))
        await self.waiting()
        self.human_move()
        self.assertTrue((await asyncio.wait_for(second, 1))["your_turn"])

    async def test_cancel_wins_revision_wake_race_without_consuming_events(self):
        first = asyncio.create_task(self.call("state", wait=True))
        await self.waiting()
        self.human_move()  # revision and cancel are ready before the waiter runs
        before = self.snapshot()
        await self.call("cancel_wait")
        self.assert_cancelled(await asyncio.wait_for(first, 1))
        self.assertEqual(before, self.snapshot())
        self.assertTrue((await self.call("state"))["your_turn"])

    async def test_heartbeat_resumes_same_generation_and_late_retry_stays_cancelled(self):
        generation = time.time_ns()
        with patch.object(main, "MCP_WAIT_SECONDS", 0.01):
            for resume in (False, True, True):
                result = await self.call("state", wait=True, wait_generation=generation, wait_resume=resume)
                self.assertEqual(result["status"], "still_waiting")
                self.assertFalse(main.wait_control.entries[("ai-1", self.room)][1].cancelled.is_set())
        await self.call("cancel_wait", wait_generation=generation + 1)
        self.assert_cancelled(await self.call("state", wait=True, wait_generation=generation, wait_resume=True))
        self.assert_cancelled(await self.call("state", wait=True, wait_generation=generation))
        newer = asyncio.create_task(self.call("state", wait=True, wait_generation=generation + 2))
        await self.waiting()
        await self.call("cancel_wait", wait_generation=generation)  # old disconnect
        self.assertFalse(newer.done())
        self.human_move()
        self.assertTrue((await asyncio.wait_for(newer, 1))["your_turn"])

    async def test_nonwait_resign_leave_invalidate_old_wait(self):
        for action in ("state", "resign", "leave"):
            with self.subTest(action=action):
                if action != "state":
                    await self.new_room()
                waiter = asyncio.create_task(self.call("state", wait=True))
                await self.waiting()
                await self.call(action)
                self.assert_cancelled(await asyncio.wait_for(waiter, 1))

    async def test_cancel_is_scoped_to_player_and_room_and_normalizes_room(self):
        waiter = asyncio.create_task(self.call("state", wait=True))
        await self.waiting()
        await self.call("cancel_wait", player_id="another-ai")
        await self.call("cancel_wait", room_id="ZZZZZZZZ")
        self.assertFalse(waiter.done())
        await self.call("cancel_wait", room_id=f" {self.room.lower()} ")
        self.assert_cancelled(await asyncio.wait_for(waiter, 1))

    async def test_cancel_invite_keeps_presence_seats_and_90_180_takeover_configuration(self):
        for seconds in (90, 180):
            with self.subTest(seconds=seconds):
                with patch.object(invites, "npc_provider_capabilities", return_value={"available": True}):
                    room = invites.create_invite("tictactoe", "human", "human-1", target_player_count=2, timeout_takeover_seconds=seconds)
                invites.join_invite(room["invite_code"], "ai", "ai-1")
                room = invites.start_invite(room["room_id"], "human", "human-1")
                self.room = room["room_id"]
                await self.call("state")  # claim the one-time bootstrap
                if room["current_player_id"] == "ai-1":
                    await self.call("move", move={"row": 0, "col": 0}, revision=room["revision"])
                waiter = asyncio.create_task(self.call("state", wait=True))
                await self.waiting()
                before = self.snapshot()
                await self.call("cancel_wait")
                self.assert_cancelled(await asyncio.wait_for(waiter, 1))
                self.assertEqual(before, self.snapshot())
                # Cancellation leaves the real participant's presence intact:
                # the existing 90/180-second takeover may still run when due.
                with database.write_transaction() as conn:
                    conn.execute("UPDATE room_invites SET turn_started_at=? WHERE room_id=?",
                                 ((datetime.now(timezone.utc) - timedelta(seconds=seconds + 1)).isoformat(), self.room))
                self.assertTrue(takeover.is_due(framework.get_room(self.room)))
                from app.npc_personas import NpcPersona
                from app.npc_providers import ProviderDecision
                class Provider:
                    async def decide(self, request):
                        return ProviderDecision(request.legal_actions[0]["action_id"], None)
                with patch("app.npc_personas.select_personas", return_value=[NpcPersona("test", "测试", "测试")]):
                    assisted = await takeover.run_timeout_turn(self.room, Provider())
                self.assertIsNotNone(assisted)
                self.assertEqual(assisted["revision"], room["revision"] + (2 if room["current_player_id"] == "ai-1" else 1))

    async def test_move_after_cancel_can_wait_and_new_nonwait_move_beats_old_wake(self):
        waiter = asyncio.create_task(self.call("state", wait=True))
        await self.waiting()
        await self.call("cancel_wait")
        self.assert_cancelled(await asyncio.wait_for(waiter, 1))
        self.human_move()
        moving = asyncio.create_task(self.call("move", move={"row": 0, "col": 1}, wait=True))
        await self.waiting()
        framework.play_move(self.room, "human", "human-1", {"row": 2, "col": 0})
        main.revision_events.notify(self.room)
        # The explicit move takes precedence before the old waiter is scheduled.
        result = await self.call("move", move={"row": 2, "col": 1})
        self.assertEqual(result["status"], "playing")
        self.assert_cancelled(await asyncio.wait_for(moving, 1))

    async def test_disconnected_backend_task_releases_slot_and_lease(self):
        generation = time.time_ns()
        waiter = asyncio.create_task(self.call("state", wait=True, wait_generation=generation))
        await self.waiting()
        waiter.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await waiter
        self.assertEqual(main.revision_events.waiting_count, 0)
        self.assert_cancelled(await self.call("state", wait=True, wait_generation=generation, wait_resume=True))

    async def test_duplicate_generation_never_replays_move_or_creates_second_wait(self):
        self.human_move()
        generation = time.time_ns()
        move = {"row": 0, "col": 1}
        first = asyncio.create_task(self.call("move", move=move, wait=True, wait_generation=generation))
        await self.waiting()
        before = self.snapshot()
        self.assert_cancelled(await self.call("move", move=move, wait=True, wait_generation=generation))
        self.assert_cancelled(await self.call("state", wait=True, wait_generation=generation, wait_resume=True))
        self.assertEqual(before, self.snapshot())
        self.assertFalse(first.done())
        await self.call("cancel_wait", wait_generation=generation + 1)
        self.assert_cancelled(await asyncio.wait_for(first, 1))

    async def test_restart_rejects_delayed_initial_request_and_resume(self):
        generation = time.time_ns()
        with patch.object(main, "MCP_WAIT_SECONDS", 0.001):
            await self.call("state", wait=True, wait_generation=generation)
        main.wait_control = WaitControl()  # fresh backend process state
        before = self.snapshot()
        for resume in (False, True):
            self.assert_cancelled(await self.call("state", wait=True, wait_generation=generation, wait_resume=resume))
        self.assertEqual(before, self.snapshot())
        fresh = asyncio.create_task(self.call("state", wait=True, wait_generation=time.time_ns()))
        await self.waiting()
        self.human_move()
        self.assertTrue((await asyncio.wait_for(fresh, 1))["your_turn"])

    def test_expired_tombstone_cannot_resurrect_delayed_generation(self):
        control = WaitControl()
        generation = time.time_ns()
        lease = control.apply("ai", "room", generation=generation, waiting=True)
        control.finish("ai", "room", lease)
        with patch("app.wait_control.time.time_ns", return_value=generation + (LEASE_SECONDS + 1) * 10**9):
            with self.assertRaises(WaitCancelled):
                control.apply("ai", "room", generation=generation, waiting=True)

    async def test_terminal_wake_cannot_offer_another_turn_or_resume_old_chain(self):
        generation = time.time_ns()
        waiter = asyncio.create_task(self.call("state", wait=True, wait_generation=generation))
        await self.waiting()
        framework.resign(self.room, "human", "human-1")
        main.revision_events.notify(self.room)
        payload = await asyncio.wait_for(waiter, 1)
        self.assertEqual(payload["status"], "finished")
        self.assertFalse(payload.get("your_turn"))
        self.assert_cancelled(await self.call("state", wait=True, wait_generation=generation, wait_resume=True))

    async def test_real_gateway_heartbeats_supersede_cancel_and_resume(self):
        # Exercise the actual gateway loop against the actual game ASGI app;
        # authentication/finalize fencing have separate platform tests.
        path = Path(__file__).resolve().parents[3] / "duel_async_gateway.py"
        spec = importlib.util.spec_from_file_location("wait_test_gateway", path)
        gateway = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(gateway)
        requests = []

        class RecordingTransport(httpx.ASGITransport):
            async def handle_async_request(self, request):
                requests.append(json.loads(request.content))
                return await super().handle_async_request(request)

        async def platform(request):
            data = json.loads(request.content)
            if request.url.path.endswith("prepare"):
                arguments = data["params"]["arguments"]
                payload = {"action": arguments["action"], "player_id": "ai-1", **arguments["params"], "wait_generation": time.time_ns()}
                return httpx.Response(200, json={"kind": "ready", "ticket": str(payload["wait_generation"]), "backend_payload": payload})
            if request.url.path.endswith("abandon"):
                return httpx.Response(200, json={"ok": True})
            return httpx.Response(200, json={"ok": True, "body": data["completion"]["data"]})

        async def spin_until(predicate):
            async def spin():
                while not predicate():
                    await asyncio.sleep(0.001)
            await asyncio.wait_for(spin(), 2)

        async with httpx.AsyncClient(transport=httpx.MockTransport(platform)) as pc, httpx.AsyncClient(transport=RecordingTransport(app=main.app)) as bc:
            app = gateway.create_app(cedartoy_client=pc, duel_client=bc, shared_secret="test")
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://gateway.test") as client:
                async def call(action, **params):
                    result = await client.post("/mcp", json={"id": 1, "method": "tools/call", "params": {"name": "play", "arguments": {"game": "duel", "action": action, "params": {"room_id": self.room, **params}}}})
                    return result.json()

                with patch.object(main, "MCP_WAIT_SECONDS", 0.01):
                    first = asyncio.create_task(call("state", wait=True))
                    await spin_until(lambda: len(requests) >= 3)
                    generation = requests[0]["wait_generation"]
                    self.assertTrue(all(item["wait_generation"] == generation for item in requests))
                    self.assertTrue(all(item.get("wait_resume") for item in requests[1:]))
                    second = asyncio.create_task(call("state", wait=True))
                    self.assert_cancelled(await asyncio.wait_for(first, 2))
                    await self.waiting()
                    self.assert_cancelled(await call("cancel_wait"))
                    self.assert_cancelled(await asyncio.wait_for(second, 2))
                    third = asyncio.create_task(call("state", wait=True))
                    await self.waiting()
                    self.human_move()
                    self.assertTrue((await asyncio.wait_for(third, 2))["your_turn"])
        self.assertEqual(main.revision_events.waiting_count, 0)

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from app import database
from app import main as main_module


class MessageApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="duel-messages-")
        self.db_patch = patch.object(
            database, "DB_PATH", Path(self.temporary.name) / "test.db"
        )
        self.db_patch.start()
        database.init_db()
        self.original_events = main_module.revision_events
        main_module.revision_events = main_module.RevisionEvents()
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=main_module.app),
            base_url="http://duel.test",
        )

    async def asyncTearDown(self):
        await self.client.aclose()
        main_module.revision_events = self.original_events
        self.db_patch.stop()
        self.temporary.cleanup()

    async def new_room(self) -> str:
        response = await self.client.post(
            "/mcp/play",
            json={
                "action": "new",
                "player_id": "Clio",
                "opponent_id": "human-one",
                "game_type": "tictactoe",
                "mode": "ai_first",
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["new_messages"], [])
        return response.json()["room"]["room_id"]

    async def test_message_on_human_move_wakes_ai_and_is_read_once(self):
        room_id = await self.new_room()
        waiter = asyncio.create_task(
            self.client.post(
                "/mcp/play",
                json={
                    "action": "move",
                    "player_id": "Clio",
                    "room_id": room_id,
                    "move": {"row": 0, "col": 0},
                    "message": "我先占角。",
                    "wait": True,
                },
            )
        )
        await asyncio.sleep(0.03)
        self.assertFalse(waiter.done())
        human = await self.client.post(
            f"/api/rooms/{room_id}/move",
            json={
                "player_id": "human-one",
                "move": {"row": 1, "col": 1},
                "message": "那我守中间。",
            },
        )
        self.assertEqual(human.status_code, 200, human.text)
        self.assertTrue(any(
            event["display_text"] == "Clio 落 A1：我先占角。"
            for event in human.json()["timeline"]
        ))
        resumed = await asyncio.wait_for(waiter, timeout=1)
        payload = resumed.json()
        self.assertEqual(
            [message["text"] for message in payload["new_messages"]],
            ["那我守中间。"],
        )
        state = await self.client.post(
            "/mcp/play",
            json={"action": "state", "player_id": "Clio", "room_id": room_id},
        )
        self.assertEqual(state.json()["new_messages"], [])

    async def test_standalone_message_is_stored_without_wakeup(self):
        room_id = await self.new_room()
        initial = await self.client.get(
            f"/api/rooms/{room_id}", params={"player_id": "human-one"}
        )
        baseline = initial.json()["room"]["revision"]
        event = main_module.revision_events.current(room_id)
        sent = await self.client.post(
            f"/api/rooms/{room_id}/messages",
            json={"player_id": "human-one", "message": "轮到你时看看这里。"},
        )
        self.assertEqual(sent.status_code, 200, sent.text)
        self.assertEqual(sent.json()["room"]["revision"], baseline)
        self.assertFalse(event.is_set())
        state = await self.client.post(
            "/mcp/play",
            json={"action": "state", "player_id": "Clio", "room_id": room_id},
        )
        self.assertEqual(
            [message["text"] for message in state.json()["new_messages"]],
            ["轮到你时看看这里。"],
        )
        repeated = await self.client.post(
            "/mcp/play",
            json={"action": "state", "player_id": "Clio", "room_id": room_id},
        )
        self.assertEqual(repeated.json()["new_messages"], [])

    async def test_still_waiting_delivers_stored_message(self):
        room_id = await self.new_room()
        with patch.object(main_module, "MAX_WAIT_SECONDS", 0.08):
            waiter = asyncio.create_task(
                self.client.post(
                    "/mcp/play",
                    json={
                        "action": "move",
                        "player_id": "Clio",
                        "room_id": room_id,
                        "move": {"row": 0, "col": 0},
                        "wait": True,
                    },
                )
            )
            await asyncio.sleep(0.02)
            sent = await self.client.post(
                f"/api/rooms/{room_id}/messages",
                json={"player_id": "human-one", "message": "我还在想。"},
            )
            self.assertEqual(sent.status_code, 200, sent.text)
            self.assertFalse(waiter.done())
            result = await asyncio.wait_for(waiter, timeout=1)
        payload = result.json()
        self.assertEqual(payload["status"], "still_waiting")
        self.assertEqual(
            [message["text"] for message in payload["new_messages"]],
            ["我还在想。"],
        )
        repeated = await self.client.post(
            "/mcp/play",
            json={"action": "state", "player_id": "Clio", "room_id": room_id},
        )
        self.assertEqual(repeated.json()["new_messages"], [])

    async def test_message_length_is_limited_to_500(self):
        room_id = await self.new_room()
        response = await self.client.post(
            f"/api/rooms/{room_id}/messages",
            json={"player_id": "human-one", "message": "字" * 501},
        )
        self.assertEqual(response.status_code, 422)


if __name__ == "__main__":
    unittest.main()

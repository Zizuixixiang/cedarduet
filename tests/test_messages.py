import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from app import database
from app import main as main_module
from app import framework


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
        event = payload["new_messages"][0]
        self.assertEqual(event["event_type"], "move")
        self.assertEqual(event["sender"]["player_id"], "human-one")
        self.assertEqual(event["sender"]["name"], "human-one")
        self.assertEqual(event["sender"]["role"], "human")
        self.assertEqual(event["sequence"], event["id"])
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

    async def test_move_without_speech_is_a_shared_timeline_event(self):
        room_id = await self.new_room()
        ai_move = await self.client.post(
            "/mcp/play",
            json={
                "action": "move",
                "player_id": "Clio",
                "room_id": room_id,
                "move": {"row": 0, "col": 0},
            },
        )
        self.assertEqual(ai_move.status_code, 200, ai_move.text)
        human_move = await self.client.post(
            f"/api/rooms/{room_id}/move",
            json={
                "player_id": "human-one",
                "move": {"row": 1, "col": 1},
            },
        )
        self.assertEqual(human_move.status_code, 200, human_move.text)
        state = await self.client.post(
            "/mcp/play",
            json={"action": "state", "player_id": "Clio", "room_id": room_id},
        )
        events = state.json()["new_messages"]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event_type"], "move")
        self.assertEqual(events[0]["text"], "")
        self.assertEqual(events[0]["move_label"], "B2")

    async def test_terminal_result_event_is_written_and_delivered_to_ai(self):
        room = framework.create_room(
            "tictactoe",
            "human_first",
            "human",
            "human-one",
            "Clio",
            participant_names={
                "human-one": "南杉",
                "Clio": "clio_web",
            },
        )
        room_id = room["room_id"]

        async def human_move(row, col):
            response = await self.client.post(
                f"/api/rooms/{room_id}/move",
                json={
                    "player_id": "human-one",
                    "move": {"row": row, "col": col},
                },
            )
            self.assertEqual(response.status_code, 200, response.text)
            return response.json()

        async def ai_move(row, col):
            response = await self.client.post(
                "/mcp/play",
                json={
                    "action": "move",
                    "player_id": "Clio",
                    "room_id": room_id,
                    "move": {"row": row, "col": col},
                },
            )
            self.assertEqual(response.status_code, 200, response.text)

        await human_move(0, 0)
        await ai_move(1, 0)
        await human_move(0, 1)
        consumed = await self.client.post(
            "/mcp/play",
            json={"action": "state", "player_id": "Clio", "room_id": room_id},
        )
        self.assertEqual(
            [event["move_label"] for event in consumed.json()["new_messages"]],
            ["B1"],
        )
        waiter = asyncio.create_task(
            self.client.post(
                "/mcp/play",
                json={
                    "action": "move",
                    "player_id": "Clio",
                    "room_id": room_id,
                    "move": {"row": 1, "col": 1},
                    "wait": True,
                },
            )
        )
        await asyncio.sleep(0.03)
        self.assertFalse(waiter.done())
        terminal = await human_move(0, 2)
        self.assertEqual(terminal["room"]["winner"], "human")
        result_events = [
            event
            for event in terminal["timeline"]
            if event["event_type"] == "result"
        ]
        self.assertEqual(len(result_events), 1)
        self.assertEqual(result_events[0]["display_text"], "南杉 获胜")

        resumed = await asyncio.wait_for(waiter, timeout=1)
        self.assertEqual(resumed.status_code, 200, resumed.text)
        new_events = resumed.json()["new_messages"]
        self.assertEqual(
            [event["event_type"] for event in new_events],
            ["move", "result"],
        )
        result = new_events[-1]
        self.assertEqual(result["text"], "南杉 获胜")
        self.assertEqual(result["display_text"], "南杉 获胜")
        self.assertEqual(
            result["sender"],
            {
                "player_id": "system",
                "name": "双弈裁判",
                "role": "system",
            },
        )
        repeated = await self.client.post(
            "/mcp/play",
            json={"action": "state", "player_id": "Clio", "room_id": room_id},
        )
        self.assertEqual(repeated.json()["new_messages"], [])

    async def test_ai_resign_returns_room_level_result_event(self):
        room = framework.create_room(
            "tictactoe",
            "ai_first",
            "human",
            "human-one",
            "Clio",
            participant_names={"Clio": "clio_web"},
        )
        response = await self.client.post(
            "/mcp/play",
            json={
                "action": "resign",
                "player_id": "Clio",
                "room_id": room["room_id"],
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        result_events = [
            event
            for event in response.json()["new_messages"]
            if event["event_type"] == "result"
        ]
        self.assertEqual(len(result_events), 1)
        self.assertEqual(result_events[0]["display_text"], "clio_web 认输")

    async def test_three_participants_have_independent_ordered_cursors(self):
        room = framework.create_room(
            "tictactoe",
            "human_first",
            "human",
            "human-one",
            "Clio",
            participant_names={
                "human-one": "Human One",
                "Clio": "Clio",
            },
        )
        room_id = room["room_id"]
        with database.write_transaction() as conn:
            conn.execute(
                """
                INSERT INTO room_participants (
                    room_id, player_id, display_name, role,
                    seat_index, joined_at
                ) VALUES (?, 'Beta', 'Beta Bot', 'ai', 2, ?)
                """,
                (room_id, room["created_at"]),
            )
            conn.execute(
                """
                INSERT INTO room_event_cursors (
                    room_id, player_id, last_event_id, updated_at
                ) VALUES (?, 'Beta', 0, ?)
                """,
                (room_id, room["created_at"]),
            )

        framework.post_message(
            room_id, "human", "human-one", "大家好"
        )
        framework.post_message(room_id, "ai", "Clio", "我已就位")
        framework.post_message(room_id, "ai", "Beta", "我也到了")
        framework.play_move(
            room_id, "human", "human-one", {"row": 1, "col": 1}
        )

        clio_events = framework.read_new_room_events(room_id, "Clio")
        self.assertEqual(
            [event["sequence"] for event in clio_events], [1, 3, 4]
        )
        self.assertEqual(
            [event["event_type"] for event in clio_events],
            ["message", "message", "move"],
        )
        self.assertEqual(
            [event["sender"] for event in clio_events],
            [
                {
                    "player_id": "human-one",
                    "name": "Human One",
                    "role": "human",
                },
                {
                    "player_id": "Beta",
                    "name": "Beta Bot",
                    "role": "ai",
                },
                {
                    "player_id": "human-one",
                    "name": "Human One",
                    "role": "human",
                },
            ],
        )
        self.assertEqual(
            framework.read_new_room_events(room_id, "Clio"), []
        )

        beta_events = framework.read_new_room_events(room_id, "Beta")
        self.assertEqual(
            [event["sequence"] for event in beta_events], [1, 2, 4]
        )
        self.assertEqual(beta_events[1]["sender"]["name"], "Clio")

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

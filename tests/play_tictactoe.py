#!/usr/bin/env python3
"""In-process HTTP demo: complete Tic-Tac-Toe game including wait/wakeup."""

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

import httpx

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


def show(label: str, payload: dict) -> None:
    room = payload["room"]
    compact = {
        "status": payload["status"],
        "message": payload["message"],
        "room_id": room["room_id"],
        "revision": room["revision"],
        "turn": room["turn"],
        "game_status": room["status"],
        "winner": room["winner"],
        "board": room["board_state"]["board"],
    }
    print(f"\n[{label}]\n{json.dumps(compact, ensure_ascii=False, indent=2)}")


async def main() -> None:
    with tempfile.TemporaryDirectory(prefix="duel-selftest-") as temporary:
        os.environ["DUEL_DB_PATH"] = str(Path(temporary) / "test.db")

        from app.database import init_db
        from app.main import app

        init_db()
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://duel.test"
        ) as client:
            created = (
                await client.post(
                    "/mcp/play",
                    json={
                        "action": "new",
                        "player_id": "ai-demo",
                        "game_type": "tictactoe",
                        "mode": "ai_first",
                    },
                )
            ).json()
            assert created["ok"]
            assert created["room"]["rules_text"]
            assert created["room"]["move_format"]
            room_id = created["room"]["room_id"]
            show("AI new", created)

            joined = (
                await client.post(
                    f"/api/rooms/{room_id}/join",
                    json={"player_id": "human-demo"},
                )
            ).json()
            assert joined["room"]["status"] == "playing"
            show("human join", joined)

            async def ai_waiting_move(row: int, col: int):
                return await client.post(
                    "/mcp/play",
                    json={
                        "action": "move",
                        "player_id": "ai-demo",
                        "room_id": room_id,
                        "move": {"row": row, "col": col},
                        "wait": True,
                    },
                )

            first_waiter = asyncio.create_task(ai_waiting_move(0, 0))
            await asyncio.sleep(0.05)
            assert not first_waiter.done(), "wait=true 应在 AI 落子后挂起"
            human_one = (
                await client.post(
                    f"/api/rooms/{room_id}/move",
                    json={"player_id": "human-demo", "row": 1, "col": 0},
                )
            ).json()
            show("human move wakes AI", human_one)
            first_result = (await asyncio.wait_for(first_waiter, timeout=2)).json()
            assert first_result["room"]["revision"] == human_one["room"]["revision"]
            assert first_result["status"] == "ok"
            show("AI waiter resumed", first_result)

            second_waiter = asyncio.create_task(ai_waiting_move(0, 1))
            await asyncio.sleep(0.05)
            assert not second_waiter.done()
            human_two = (
                await client.post(
                    f"/api/rooms/{room_id}/move",
                    json={"player_id": "human-demo", "row": 1, "col": 1},
                )
            ).json()
            second_result = (await asyncio.wait_for(second_waiter, timeout=2)).json()
            assert second_result["room"]["revision"] == human_two["room"]["revision"]
            show("second wakeup", second_result)

            finished = (
                await client.post(
                    "/mcp/play",
                    json={
                        "action": "move",
                        "player_id": "ai-demo",
                        "room_id": room_id,
                        "move": {"row": 0, "col": 2},
                        "wait": False,
                    },
                )
            ).json()
            assert finished["room"]["status"] == "finished"
            assert finished["room"]["winner"] == "ai"
            show("AI wins", finished)

            state = (
                await client.post(
                    "/mcp/play",
                    json={
                        "action": "state",
                        "player_id": "ai-demo",
                        "room_id": room_id,
                    },
                )
            ).json()
            assert state["room"]["winner"] == "ai"
            print("\nSELFTEST PASSED: complete game + wait=true wakeup + final state")


if __name__ == "__main__":
    asyncio.run(main())


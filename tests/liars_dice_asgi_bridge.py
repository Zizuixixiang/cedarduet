"""Disposable real ASGI backend for the dice browser test (JSON lines over stdio).

Only the trusted proxy identity and external NPC provider are fixtures. Room
creation, persistence, listing, selection, and start all use production APIs.
"""
import asyncio
import base64
import json
import os
from pathlib import Path
import sys
import tempfile
from urllib.parse import quote
from unittest.mock import AsyncMock, patch

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


async def serve():
    with tempfile.TemporaryDirectory(prefix="duel-dice-ui-") as directory:
        os.environ["DUEL_DB_PATH"] = str(Path(directory) / "test.db")
        from app import database, main, framework
        from app.npc_personas import NpcPersona
        database.init_db()
        for _ in range(int(os.environ.get('DICE_TEST_PLAYING_ROOMS', '0'))):
            framework.create_room('tictactoe', 'human_first', 'human', 'human-1', opponent_id='ai-1')
        patches = [patch("app.main._schedule_current_system_npc", new=AsyncMock())]
        for module in ("app.main", "app.invites"):
            patches.extend([
                patch(module + ".npc_provider_capabilities", return_value={"available": True}),
                patch(module + ".select_personas", side_effect=lambda n: [
                    NpcPersona(f"fixture-{i}", f"NPC {i}", "test") for i in range(n)
                ]),
            ])
        patches.append(patch("app.npc_personas.select_personas", side_effect=lambda n: [
            NpcPersona(f"fixture-{i}", f"NPC {i}", "test") for i in range(n)
        ]))
        for item in patches:
            item.start()
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://dice.test") as client:
                async def respond(message):
                    actor = message.get("actor", "human-1")
                    headers = {"X-Duel-Human-Player": actor, "X-Duel-Human-Name": quote(f"玩家 {actor}"), "X-Duel-Bound-Ais": base64.b64encode(json.dumps([
                        {"id": f"ai-{i}", "name": f"小机{i}"} for i in range(1, 4)
                    ]).encode()).decode()}
                    body = message.get("body")
                    if isinstance(body, dict):
                        body = {**body, "player_id": actor}
                    try:
                        reply = await client.request(message.get("method", "GET"), message["path"], headers=headers, json=body)
                        result = {"id": message["id"], "status": reply.status_code, "body": reply.text}
                    except Exception as error:
                        result = {"id": message["id"], "status": 500, "body": json.dumps({"error": str(error)})}
                    print(json.dumps(result), flush=True)
                for line in sys.stdin:
                    await respond(json.loads(line))

        finally:
            for item in reversed(patches):
                item.stop()


if __name__ == "__main__":
    asyncio.run(serve())

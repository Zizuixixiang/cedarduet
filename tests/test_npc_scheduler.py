import asyncio
import base64
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from app import database, framework
from app import main as main_module
from app.games import GAMES
from app.games.base import MoveResult
from app.npc_controller import NpcTurnResult
from app.npc_providers import NpcProvider, ProviderDecision
from app.npc_scheduler import (
    NPC_GAME_MANAGED_VISIBLE_ACTION_DELAYS_SECONDS,
    NPC_VISIBLE_ACTION_DELAY_SECONDS,
    NpcTurnScheduler,
    npc_visible_action_delay_seconds,
    npc_visible_action_pacing,
)
from tests.test_npc_framework import DummyNpcMultiplayer, write_persona


class DeterministicProvider(NpcProvider):
    name = "deterministic-test"
    available = True
    max_concurrency = 4

    def __init__(self):
        self.requests = []

    async def decide(self, request):
        self.requests.append(request)
        step = next(
            item for item in request.legal_actions
            if item["action"] == {"action": "step"}
        )
        return ProviderDecision(step["action_id"], None)


class BlockingProvider(DeterministicProvider):
    def __init__(self):
        super().__init__()
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def decide(self, request):
        self.requests.append(request)
        self.started.set()
        await self.release.wait()
        step = next(
            item for item in request.legal_actions
            if item["action"] == {"action": "step"}
        )
        return ProviderDecision(step["action_id"], None)


class NpcVisibleActionPacingTests(unittest.IsolatedAsyncioTestCase):
    SUPPORTED_NPC_GAME_TYPES = {
        "aeroplane_chess",
        "banqi",
        "blackjack",
        "checkers",
        "chess",
        "chinese_checkers",
        "dots_boxes",
        "doudizhu",
        "gandengyan",
        "go",
        "guandan",
        "junqi",
        "liars_dice",
        "mahjong",
        "texas_holdem",
        "train_cards",
        "uno",
        "yahtzee",
        "zhajinhua",
    }

    @staticmethod
    def room(revision, current_player_id="npc:quiet", game_type="train_cards"):
        return {
            "room_id": "PACETEST",
            "game_type": game_type,
            "status": "playing",
            "revision": revision,
            "current_player_id": current_player_id,
            "participants": [
                {
                    "player_id": "npc:quiet",
                    "participant_kind": "system_npc",
                    "join_status": "joined",
                    "activity_state": "active",
                    "active": True,
                },
                {
                    "player_id": "human-1",
                    "participant_kind": "human",
                    "join_status": "joined",
                    "activity_state": "active",
                    "active": True,
                },
            ],
        }

    def test_every_supported_npc_game_has_one_audited_pacing_source(self):
        supported = {
            game_type for game_type, game in GAMES.items()
            if game.supports_npcs
        }
        self.assertEqual(supported, self.SUPPORTED_NPC_GAME_TYPES)
        pacing = {
            game_type: npc_visible_action_pacing({"game_type": game_type})
            for game_type, game in GAMES.items()
            if game.supports_npcs
        }
        self.assertEqual(NPC_VISIBLE_ACTION_DELAY_SECONDS, 2)
        self.assertEqual(
            npc_visible_action_delay_seconds({"game_type": "liars_dice"}),
            0,
        )
        self.assertEqual(pacing["liars_dice"].source, "none")
        self.assertEqual(pacing["liars_dice"].effective_delay_seconds, 0)
        self.assertEqual(pacing["liars_dice"].scheduler_delay_seconds, 0)

        aeroplane = pacing["aeroplane_chess"]
        self.assertEqual(aeroplane.source, "game_renderer")
        self.assertEqual(aeroplane.effective_delay_seconds, 2)
        self.assertEqual(aeroplane.scheduler_delay_seconds, 0)
        self.assertEqual(
            NPC_GAME_MANAGED_VISIBLE_ACTION_DELAYS_SECONDS,
            {"aeroplane_chess": 2.0},
        )
        renderer = (
            Path(__file__).resolve().parents[1]
            / "app/static/games/aeroplane_chess.js"
        ).read_text(encoding="utf-8")
        self.assertIn("const NPC_VISIBLE_ACTION_PAUSE_MS = 2000;", renderer)

        scheduler_paced = supported - {"aeroplane_chess", "liars_dice"}
        self.assertTrue(scheduler_paced)
        for game_type in scheduler_paced:
            with self.subTest(game_type=game_type):
                policy = pacing[game_type]
                self.assertEqual(policy.source, "npc_scheduler")
                self.assertGreaterEqual(policy.effective_delay_seconds, 1.2)
                self.assertEqual(
                    policy.scheduler_delay_seconds,
                    policy.effective_delay_seconds,
                )

    async def test_scheduler_pauses_before_each_consecutive_npc_action(self):
        delays = []
        actions = []

        async def record_delay(seconds):
            delays.append(seconds)

        async def apply_turn(room_id):
            actions.append(room_id)
            return NpcTurnResult(
                "applied", "local", {"action": "step"}, None, None
            )

        scheduler = NpcTurnScheduler(
            turn_runner=apply_turn,
            action_sleeper=record_delay,
        )
        rooms = [
            self.room(0),
            self.room(1),
            self.room(2, current_player_id="human-1"),
        ]
        with patch("app.npc_scheduler.get_room", side_effect=rooms):
            outcome = await scheduler._drain_room("PACETEST")
        self.assertEqual(outcome, "idle")
        self.assertEqual(actions, ["PACETEST", "PACETEST"])
        self.assertEqual(delays, [2, 2])

    async def test_liars_dice_keeps_its_existing_unmodified_flow(self):
        delays = []

        async def record_delay(seconds):
            delays.append(seconds)

        async def apply_turn(_room_id):
            return NpcTurnResult(
                "applied", "provider", {"action": "bid"}, None, None
            )

        scheduler = NpcTurnScheduler(
            turn_runner=apply_turn,
            action_sleeper=record_delay,
        )
        rooms = [
            self.room(0, game_type="liars_dice"),
            self.room(1, current_player_id="human-1", game_type="liars_dice"),
        ]
        with patch("app.npc_scheduler.get_room", side_effect=rooms):
            outcome = await scheduler._drain_room("PACETEST")
        self.assertEqual(outcome, "idle")
        self.assertEqual(delays, [])

    async def test_game_managed_pause_does_not_stack_with_scheduler_pause(self):
        delays = []
        actions = []

        async def record_delay(seconds):
            delays.append(seconds)

        async def apply_turn(room_id):
            actions.append(room_id)
            return NpcTurnResult(
                "applied", "provider", {"action": "roll"}, None, None
            )

        scheduler = NpcTurnScheduler(
            turn_runner=apply_turn,
            action_sleeper=record_delay,
        )
        rooms = [
            self.room(0, game_type="aeroplane_chess"),
            self.room(
                1,
                current_player_id="human-1",
                game_type="aeroplane_chess",
            ),
        ]
        with patch("app.npc_scheduler.get_room", side_effect=rooms):
            outcome = await scheduler._drain_room("PACETEST")
        self.assertEqual(outcome, "idle")
        self.assertEqual(actions, ["PACETEST"])
        self.assertEqual(delays, [])

    async def test_human_and_bound_machine_turns_are_never_delayed_or_run(self):
        delays = []
        actions = []

        async def record_delay(seconds):
            delays.append(seconds)

        async def apply_turn(room_id):
            actions.append(room_id)
            return NpcTurnResult(
                "applied", "provider", {"action": "step"}, None, None
            )

        scheduler = NpcTurnScheduler(
            turn_runner=apply_turn,
            action_sleeper=record_delay,
        )
        for participant_kind in ("human", "bound_machine"):
            with self.subTest(participant_kind=participant_kind):
                room = self.room(0)
                room["participants"][0]["participant_kind"] = participant_kind
                with patch("app.npc_scheduler.get_room", return_value=room):
                    outcome = await scheduler._drain_room("PACETEST")
                self.assertEqual(outcome, "idle")
        self.assertEqual(actions, [])
        self.assertEqual(delays, [])


class NpcHttpRuntimeContractTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="duel-npc-runtime-")
        root = Path(self.temporary.name)
        self.db_patch = patch.object(database, "DB_PATH", root / "test.db")
        self.db_patch.start()
        self.persona_dir = root / "personas"
        self.persona_dir.mkdir()
        write_persona(self.persona_dir, "quiet", "安静测试机")
        write_persona(self.persona_dir, "bright", "明亮测试机")
        self.env_patch = patch.dict(
            "os.environ", {"DUEL_NPC_PERSONAS_DIR": str(self.persona_dir)}
        )
        self.env_patch.start()
        self.game_patch = patch.dict(
            GAMES, {DummyNpcMultiplayer.game_type: DummyNpcMultiplayer()}
        )
        self.game_patch.start()
        self.capability_patch = patch.object(
            main_module,
            "npc_provider_capabilities",
            return_value={
                "provider": "deterministic-test",
                "available": True,
                "reason": None,
                "max_concurrency": 4,
            },
        )
        self.capability_patch.start()
        self.provider = DeterministicProvider()
        self.provider_patch = patch(
            "app.npc_controller.get_npc_provider",
            side_effect=lambda: self.provider,
        )
        self.provider_patch.start()
        database.init_db()
        self.scheduler = NpcTurnScheduler(
            room_changed=main_module.revision_events.notify,
            in_progress_retry_seconds=0.02,
            visible_action_delay_seconds=0,
        )
        self.scheduler_patch = patch.object(
            main_module, "npc_turn_scheduler", self.scheduler
        )
        self.scheduler_patch.start()
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=main_module.app),
            base_url="http://duel.test",
        )

    async def asyncTearDown(self):
        await self.scheduler.shutdown()
        await self.client.aclose()
        self.scheduler_patch.stop()
        self.provider_patch.stop()
        self.capability_patch.stop()
        self.game_patch.stop()
        self.env_patch.stop()
        self.db_patch.stop()
        self.temporary.cleanup()

    @staticmethod
    def headers():
        encoded = base64.urlsafe_b64encode(json.dumps([
            {"id": "ai-1", "name": "绑定小机"}
        ], ensure_ascii=False).encode("utf-8")).decode("ascii").rstrip("=")
        return {
            "X-Duel-Human-Player": "human-1",
            "X-Duel-Human-Name": "%E4%BA%BA%E7%B1%BB",
            "X-Duel-Bound-Ais": encoded,
        }

    @staticmethod
    def participants():
        return [
            {
                "player_id": "human-1", "role": "human",
                "participant_kind": "human", "display_name": "人类",
            },
            {
                "player_id": "ai-1", "role": "ai",
                "participant_kind": "bound_machine", "display_name": "绑定小机",
            },
            {
                "player_id": "npc:quiet", "role": "ai",
                "participant_kind": "system_npc", "npc_persona_id": "quiet",
                "display_name": "安静测试机",
            },
            {
                "player_id": "npc:bright", "role": "ai",
                "participant_kind": "system_npc", "npc_persona_id": "bright",
                "display_name": "明亮测试机",
            },
        ]

    def create_room(self, first_player_id="human-1"):
        return framework.create_room(
            DummyNpcMultiplayer.game_type,
            "human_first",
            "human",
            "human-1",
            opponent_id="ai-1",
            ordered_participants=self.participants(),
            enforce_trusted_pair=True,
            first_player_id=first_player_id,
        )

    async def wait_for_room(self, room_id, predicate, timeout=2.0):
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        latest = None
        while loop.time() < deadline:
            latest = framework.get_room(room_id)
            if predicate(latest):
                return latest
            await asyncio.sleep(0.01)
        self.fail(f"room {room_id} did not reach expected state: {latest}")

    async def wait_for_decision_status(self, room_id, status, timeout=2.0):
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        latest = None
        while loop.time() < deadline:
            conn = database.connect()
            try:
                row = conn.execute(
                    """
                    SELECT status FROM npc_decisions
                    WHERE room_id = ? ORDER BY revision DESC LIMIT 1
                    """,
                    (room_id,),
                ).fetchone()
            finally:
                conn.close()
            latest = row["status"] if row else None
            if latest == status:
                return
            await asyncio.sleep(0.01)
        self.fail(
            f"room {room_id} decision did not reach {status}; latest={latest}"
        )

    def decision_count(self, room_id):
        conn = database.connect()
        try:
            return conn.execute(
                "SELECT COUNT(*) FROM npc_decisions WHERE room_id = ?",
                (room_id,),
            ).fetchone()[0]
        finally:
            conn.close()

    async def test_npc_opener_is_dispatched_after_http_response(self):
        self.provider = BlockingProvider()
        await self.scheduler.start()

        def choose_last_npc(candidates):
            return next(
                player_id for player_id in reversed(candidates)
                if player_id.startswith("npc:")
            )

        with patch.object(
            main_module, "secure_choice", side_effect=choose_last_npc
        ):
            response = await asyncio.wait_for(
                self.client.post(
                    "/api/rooms",
                    headers=self.headers(),
                    json={
                        "player_id": "human-1",
                        "ai_players": ["ai-1"],
                        "game_type": DummyNpcMultiplayer.game_type,
                        "target_player_count": 4,
                        "fill_with_npcs": True,
                        "mode": "random",
                    },
                ),
                timeout=0.5,
            )
        self.assertEqual(response.status_code, 200, response.text)
        created = response.json()["room"]
        self.assertEqual(created["revision"], 0)
        self.assertEqual(
            created["current_actor"]["participant_kind"], "system_npc"
        )
        await asyncio.wait_for(self.provider.started.wait(), timeout=1)
        self.assertEqual(framework.get_room(created["room_id"])["revision"], 0)
        self.provider.release.set()
        latest = await self.wait_for_room(
            created["room_id"],
            lambda room: room["revision"] == 1,
        )
        self.assertEqual(latest["current_player_id"], "human-1")
        self.assertEqual(len(self.provider.requests), 1)
        self.assertEqual(self.decision_count(created["room_id"]), 1)

    async def test_bound_machine_move_dispatches_all_consecutive_npcs(self):
        await self.scheduler.start()
        room = self.create_room()
        human_move = await self.client.post(
            f"/api/rooms/{room['room_id']}/move",
            json={
                "player_id": "human-1",
                "revision": 0,
                "move": {"action": "step"},
            },
        )
        self.assertEqual(human_move.status_code, 200, human_move.text)
        await asyncio.sleep(0.03)
        waiting_for_bound = framework.get_room(room["room_id"])
        self.assertEqual(waiting_for_bound["revision"], 1)
        self.assertEqual(waiting_for_bound["current_player_id"], "ai-1")
        self.assertEqual(self.decision_count(room["room_id"]), 0)

        bound_move = await self.client.post(
            "/mcp/play",
            json={
                "action": "move",
                "player_id": "ai-1",
                "room_id": room["room_id"],
                "revision": 1,
                "move": {"action": "step"},
            },
        )
        self.assertEqual(bound_move.status_code, 200, bound_move.text)
        self.assertEqual(bound_move.json()["revision"], 2)
        latest = await self.wait_for_room(
            room["room_id"],
            lambda current: current["revision"] == 4,
        )
        self.assertEqual(latest["current_player_id"], "human-1")
        self.assertEqual(
            [request.persona["id"] for request in self.provider.requests],
            ["quiet", "bright"],
        )
        self.assertEqual(self.decision_count(room["room_id"]), 2)

    async def test_human_move_dispatches_all_consecutive_npcs(self):
        await self.scheduler.start()
        participants = self.participants()
        participants[1], participants[3] = participants[3], participants[1]
        room = framework.create_room(
            DummyNpcMultiplayer.game_type,
            "human_first",
            "human",
            "human-1",
            opponent_id="ai-1",
            ordered_participants=participants,
            enforce_trusted_pair=True,
            first_player_id="human-1",
        )
        response = await self.client.post(
            f"/api/rooms/{room['room_id']}/move",
            json={
                "player_id": "human-1",
                "revision": 0,
                "move": {"action": "step"},
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        latest = await self.wait_for_room(
            room["room_id"],
            lambda current: current["revision"] == 3,
        )
        self.assertEqual(latest["current_player_id"], "ai-1")
        self.assertEqual(
            [request.persona["id"] for request in self.provider.requests],
            ["bright", "quiet"],
        )
        self.assertEqual(self.decision_count(room["room_id"]), 2)

    async def test_startup_recovers_existing_active_npc_turn(self):
        with patch.object(
            framework, "_new_room_id", return_value="LC9QAEY3"
        ):
            room = self.create_room(first_player_id="npc:bright")
        self.assertEqual(room["room_id"], "LC9QAEY3")
        self.assertEqual(room["revision"], 0)
        self.assertEqual(self.decision_count(room["room_id"]), 0)
        async with main_module.lifespan(main_module.app):
            latest = await self.wait_for_room(
                room["room_id"], lambda current: current["revision"] == 1
            )
        self.assertEqual(latest["current_player_id"], "human-1")
        self.assertEqual(self.decision_count(room["room_id"]), 1)

    async def test_concurrent_get_fallback_deduplicates_same_revision(self):
        self.provider = BlockingProvider()
        await self.scheduler.start()
        room = self.create_room(first_player_id="npc:bright")
        path = f"/api/rooms/{room['room_id']}"
        responses = await asyncio.gather(*(
            self.client.get(path, headers=self.headers()) for _ in range(12)
        ))
        self.assertTrue(all(response.status_code == 200 for response in responses))
        await asyncio.wait_for(self.provider.started.wait(), timeout=1)
        await asyncio.gather(*(
            self.client.get(path, headers=self.headers()) for _ in range(12)
        ))
        self.assertEqual(len(self.provider.requests), 1)
        self.assertEqual(self.decision_count(room["room_id"]), 1)
        self.provider.release.set()
        await self.wait_for_room(
            room["room_id"], lambda current: current["revision"] == 1
        )
        self.assertEqual(len(self.provider.requests), 1)
        self.assertEqual(self.decision_count(room["room_id"]), 1)

    async def test_consecutive_npc_loop_stops_at_safety_limit(self):
        plugin = GAMES[DummyNpcMultiplayer.game_type]

        def retain_npc_turn(state, move, actor):
            state["actions"].append(actor["player_id"])
            return MoveResult(state=state, retain_turn=True)

        room = self.create_room(first_player_id="npc:bright")
        capped = NpcTurnScheduler(
            max_consecutive_turns=3,
            visible_action_delay_seconds=0,
        )
        with patch.object(plugin, "apply_action", side_effect=retain_npc_turn):
            await capped.start()
            try:
                latest = await self.wait_for_room(
                    room["room_id"], lambda current: current["revision"] == 3
                )
                await asyncio.sleep(0.05)
            finally:
                await capped.shutdown()
        self.assertEqual(latest["current_player_id"], "npc:bright")
        self.assertEqual(framework.get_room(room["room_id"])["revision"], 3)
        self.assertEqual(len(self.provider.requests), 3)
        self.assertEqual(self.decision_count(room["room_id"]), 3)

    async def test_failed_turn_releases_room_for_later_retry(self):
        (self.persona_dir / "quiet.json").unlink()
        room = self.create_room(first_player_id="npc:quiet")
        with patch("app.npc_scheduler.logger.exception"):
            await self.scheduler.start()
            await self.wait_for_decision_status(room["room_id"], "failed")
            write_persona(self.persona_dir, "quiet", "安静测试机")
            await self.scheduler.schedule(room["room_id"])
            latest = await self.wait_for_room(
                room["room_id"], lambda current: current["revision"] == 2
            )
        self.assertEqual(latest["current_player_id"], "human-1")
        conn = database.connect()
        try:
            statuses = [
                row["status"] for row in conn.execute(
                    """
                    SELECT status FROM npc_decisions
                    WHERE room_id = ? ORDER BY revision
                    """,
                    (room["room_id"],),
                )
            ]
        finally:
            conn.close()
        self.assertEqual(statuses, ["completed", "completed"])

    async def test_landed_background_speech_emits_one_room_notification(self):
        notified = []
        scheduler = NpcTurnScheduler(room_changed=notified.append)

        async def sent():
            return True

        task = asyncio.create_task(sent())
        await task
        scheduler._speech_finished("ABCDEFGH", task)
        self.assertEqual(notified, ["ABCDEFGH"])

        async def failed():
            return False

        failed_task = asyncio.create_task(failed())
        await failed_task
        scheduler._speech_finished("ABCDEFGH", failed_task)
        self.assertEqual(notified, ["ABCDEFGH"])


if __name__ == "__main__":
    unittest.main()

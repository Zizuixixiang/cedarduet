import base64
import json
import random
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from app import chips, database, framework
from app import main as main_module
from app.games import GAMES
from app.games.base import GamePlugin, MoveResult
from app.games.tools import advance_flow, ensure_flow
from app.npc_personas import (
    PersonaConfigError,
    load_personas,
    resolve_avatar_file,
    select_personas,
)
from app.npc_runtime import complete_npc_decision, reserve_npc_decision


class DummyNpcMultiplayer(GamePlugin):
    """Test-only NPC/private/settlement fixture; never registered in production."""

    game_type = "dummy_npc_multiplayer"
    display_name = "测试 NPC 多人桌"
    rules_text = "测试专用。"
    move_format = '{"action":"step"}'
    min_players = 4
    max_players = 4
    recommended_players = 4
    supports_npcs = True
    supports_stakes = True
    supports_multiplayer_stakes = True

    def initial_state(self):
        return {}

    def initialize(self, participants):
        state = {
            "actions": [],
            "secrets": {
                participant["player_id"]: f"private:{participant['player_id']}"
                for participant in participants
            },
        }
        ensure_flow(state, phase="opening")
        return state

    def public_state(self, state, participants):
        return {"actions": list(state["actions"]), "flow": dict(state["flow"])}

    def private_state(self, state, viewer, participants):
        return {
            "hand": [state["secrets"][viewer["player_id"]]],
            "legal_actions": [{"action": "step"}, {"action": "finish"}],
        }

    def npc_compact_rules(self, state, actor, participants):
        return "每回合只能从权威合法行动中选择 step 或 finish。"

    def npc_public_actions(self, state, actor, participants):
        return [{"actor": player_id} for player_id in state["actions"]]

    def npc_legal_actions(self, state, actor, participants):
        return [{"action": "step"}, {"action": "finish"}]

    def validate_move(self, state, move, mark):
        if move.get("action") not in {"step", "finish"}:
            raise ValueError("invalid action")

    def apply_move(self, state, move, mark):
        return state

    def apply_action(self, state, move, actor):
        state["actions"].append(actor["player_id"])
        return MoveResult(
            state=state,
            participant_activity=dict(move.get("activity", {})),
            result=(
                {"winner_player_id": "human-1", "draw": False}
                if move["action"] == "finish" else None
            ),
        )

    def progress_after_action(self, state, move, actor, participants, applied):
        advance_flow(
            state,
            phase=move.get("phase"),
            next_round=bool(move.get("next_round")),
        )
        return applied

    def settlement_deltas(self, state, result, participants, stake):
        return {
            "human-1": stake * 3,
            "ai-1": -stake,
            "npc:quiet": -stake,
            "npc:bright": -stake,
        }

    def check_winner(self, state):
        return None


def write_persona(
    directory: Path,
    persona_id: str,
    name: str,
    text: str = "test",
    *,
    avatar: str | None = None,
):
    payload = {"id": persona_id, "display_name": name, "persona": text}
    if avatar is not None:
        payload["avatar"] = avatar
    (directory / f"{persona_id}.json").write_text(
        json.dumps(payload, ensure_ascii=False),
        encoding="utf-8",
    )


class PersonaLoaderTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="duel-personas-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.directory = self.root / "personas"
        self.directory.mkdir()

    def test_load_and_random_unique_selection(self):
        write_persona(self.directory, "quiet", "安静测试机")
        write_persona(self.directory, "bright", "明亮测试机")
        write_persona(self.directory, "third", "第三测试机")
        loaded = load_personas(self.directory)
        self.assertEqual([item.id for item in loaded], ["bright", "quiet", "third"])
        selected = select_personas(2, path=self.directory, rng=random.Random(7))
        self.assertEqual(len({item.id for item in selected}), 2)

    def test_selection_supports_four_but_never_five_npcs(self):
        for index in range(5):
            write_persona(
                self.directory, f"persona-{index}", f"测试角色 {index}"
            )
        selected = select_personas(
            4, path=self.directory, rng=random.Random(8)
        )
        self.assertEqual(len(selected), 4)
        self.assertEqual(len({item.id for item in selected}), 4)
        with self.assertRaisesRegex(PersonaConfigError, "最多补入 4"):
            select_personas(5, path=self.directory)

    def test_duplicate_invalid_empty_long_and_insufficient_are_rejected(self):
        write_persona(self.directory, "same", "一号")
        (self.directory / "duplicate.json").write_text(
            '{"id":"same","display_name":"二号","persona":"x"}',
            encoding="utf-8",
        )
        with self.assertRaisesRegex(PersonaConfigError, "重复"):
            load_personas(self.directory)
        (self.directory / "duplicate.json").unlink()
        with self.assertRaisesRegex(PersonaConfigError, "库存不足"):
            select_personas(2, path=self.directory)
        (self.directory / "notes.txt").write_text("illegal", encoding="utf-8")
        with self.assertRaisesRegex(PersonaConfigError, "非法文件"):
            load_personas(self.directory)
        (self.directory / "notes.txt").unlink()
        write_persona(self.directory, "empty", "名字", " ")
        with self.assertRaisesRegex(PersonaConfigError, "persona 不能为空"):
            load_personas(self.directory)
        (self.directory / "empty.json").unlink()
        write_persona(self.directory, "long", "名字", "x" * 4001)
        with self.assertRaisesRegex(PersonaConfigError, "persona 过长"):
            load_personas(self.directory)

    def test_avatar_mapping_is_external_and_rejects_unsafe_paths(self):
        avatars = self.root / "avatars"
        avatars.mkdir()
        (avatars / "quiet.png").write_bytes(b"test-png-fixture")
        write_persona(
            self.directory, "quiet", "安静测试机", avatar="quiet.png"
        )
        with patch.dict("os.environ", {"DUEL_NPC_AVATARS_DIR": str(avatars)}):
            persona = load_personas(self.directory)[0]
            self.assertEqual(
                persona.public_identity()["avatar_url"],
                "/api/npc-avatars/quiet.png",
            )
            self.assertEqual(resolve_avatar_file("quiet.png"), avatars / "quiet.png")
            for invalid in ("../quiet.png", "/quiet.png", "quiet.svg", "quiet.png/next"):
                with self.subTest(invalid=invalid):
                    with self.assertRaises(PersonaConfigError):
                        resolve_avatar_file(invalid)
            outside = self.root / "outside.png"
            outside.write_bytes(b"outside")
            try:
                (avatars / "linked.png").symlink_to(outside)
            except OSError:
                pass
            else:
                with self.assertRaisesRegex(PersonaConfigError, "路径越界"):
                    resolve_avatar_file("linked.png")

        (self.directory / "quiet.json").unlink()
        write_persona(
            self.directory, "quiet", "安静测试机", avatar="../quiet.png"
        )
        with patch.dict("os.environ", {"DUEL_NPC_AVATARS_DIR": str(avatars)}):
            with self.assertRaisesRegex(PersonaConfigError, "avatar 非法"):
                load_personas(self.directory)


class NpcRoomContractTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="duel-npc-room-")
        self.addCleanup(self.temporary.cleanup)
        self.db_patch = patch.object(
            database, "DB_PATH", Path(self.temporary.name) / "test.db"
        )
        self.db_patch.start()
        self.addCleanup(self.db_patch.stop)
        self.game_patch = patch.dict(
            GAMES, {DummyNpcMultiplayer.game_type: DummyNpcMultiplayer()}
        )
        self.game_patch.start()
        self.addCleanup(self.game_patch.stop)
        database.init_db()

    @staticmethod
    def participants():
        return [
            {
                "player_id": "human-1", "display_name": "人类一号",
                "role": "human", "participant_kind": "human",
            },
            {
                "player_id": "ai-1", "display_name": "绑定小机",
                "role": "ai", "participant_kind": "bound_machine",
            },
            {
                "player_id": "npc:quiet", "display_name": "安静测试机",
                "role": "ai", "participant_kind": "system_npc",
                "npc_persona_id": "quiet",
            },
            {
                "player_id": "npc:bright", "display_name": "明亮测试机",
                "role": "ai", "participant_kind": "system_npc",
                "npc_persona_id": "bright",
            },
        ]

    def create_room(self, *, stake=5):
        return framework.create_room(
            DummyNpcMultiplayer.game_type,
            "human_first", "human", "human-1",
            opponent_id="ai-1",
            ordered_participants=self.participants(),
            stake=stake,
            require_confirmations=True,
            enforce_trusted_pair=True,
        )

    def test_complete_four_player_npc_flow_is_private_idempotent_and_wallet_safe(self):
        room = self.create_room()
        self.assertEqual(room["pending_for"], ["ai-1"])
        self.assertEqual(
            [item["confirmation_status"] for item in room["participants"]],
            ["accepted", "pending", "accepted", "accepted"],
        )
        room = framework.respond_to_invitation(
            room["room_id"], "ai", "ai-1", "accept"
        )
        self.assertEqual(room["status"], "playing")
        self.assertEqual(room["turn_order"], [
            "human-1", "ai-1", "npc:quiet", "npc:bright",
        ])
        for player_id in room["turn_order"]:
            view = framework.project_room_for_viewer(room, player_id)
            self.assertNotIn("secrets", view["board_state"])
            self.assertEqual(view["private_state"]["hand"], [f"private:{player_id}"])
            other_secrets = [
                f"private:{other}" for other in room["turn_order"]
                if other != player_id
            ]
            self.assertTrue(all(secret not in str(view) for secret in other_secrets))

        room = framework.play_move(
            room["room_id"], "human", "human-1", {"action": "step"}
        )
        room = framework.play_move(
            room["room_id"], "ai", "ai-1",
            {"action": "step", "phase": "main", "next_round": True},
        )
        self.assertEqual(room["current_player_id"], "npc:quiet")
        ticket = reserve_npc_decision(
            room["room_id"], room["revision"], "npc:quiet"
        )
        duplicate = reserve_npc_decision(
            room["room_id"], room["revision"], "npc:quiet"
        )
        self.assertTrue(ticket.created)
        self.assertFalse(duplicate.created)
        with self.assertRaisesRegex(framework.DuelError, "合法行动"):
            complete_npc_decision(
                ticket, {"action": "invented"}, [{"action": "step"}]
            )
        self.assertTrue(complete_npc_decision(
            ticket, {"action": "step"}, [{"action": "step"}], message="走这里"
        ))
        self.assertFalse(complete_npc_decision(
            ticket, {"action": "step"}, [{"action": "step"}], message="走这里"
        ))
        recovered = reserve_npc_decision(
            room["room_id"], room["revision"], "npc:quiet"
        )
        self.assertEqual(recovered.status, "completed")
        self.assertEqual(recovered.decision, {
            "action": {"action": "step"}, "message": "走这里"
        })
        room = framework.play_move(
            room["room_id"], "ai", "npc:quiet",
            {"action": "step", "activity": {"npc:bright": "eliminated"}},
        )
        self.assertEqual(room["current_player_id"], "human-1")
        room = framework.play_move(
            room["room_id"], "human", "human-1", {"action": "finish"}
        )
        self.assertEqual(room["status"], "finished")
        self.assertEqual(room["board_state"]["flow"], {
            "phase": "main", "round_number": 2, "turn_number": 2,
        })
        self.assertEqual(room["result"]["settlement_deltas"], {
            "human-1": 15, "ai-1": -5,
            "npc:quiet": -5, "npc:bright": -5,
        })
        self.assertEqual(chips.get_wallet("human", "human-1")["balance"], 215)
        self.assertEqual(chips.get_wallet("ai", "ai-1")["balance"], 195)
        conn = database.connect()
        try:
            npc_wallets = conn.execute(
                "SELECT COUNT(*) FROM chip_wallets WHERE subject_id LIKE 'npc:%'"
            ).fetchone()[0]
            batch = conn.execute(
                "SELECT deltas_json FROM chip_settlement_batches"
            ).fetchone()
        finally:
            conn.close()
        self.assertEqual(npc_wallets, 0)
        self.assertEqual(json.loads(batch["deltas_json"]), room["result"]["settlement_deltas"])
        npc_rows = [
            item for item in room["participants"]
            if item["participant_kind"] == "system_npc"
        ]
        self.assertTrue(all(item["wallet_label"] == "???" for item in npc_rows))

    def test_production_pair_and_plugin_opt_in_are_mandatory(self):
        participants = self.participants()
        with self.assertRaisesRegex(framework.DuelError, "真实绑定小机"):
            framework.create_room(
                DummyNpcMultiplayer.game_type, "human_first", "human", "human-1",
                ordered_participants=[participants[0], participants[2], participants[3]],
                enforce_trusted_pair=True,
            )
        plugin = GAMES[DummyNpcMultiplayer.game_type]
        with patch.object(plugin, "supports_npcs", False):
            with self.assertRaisesRegex(framework.DuelError, "未启用 NPC"):
                framework.create_room(
                    DummyNpcMultiplayer.game_type,
                    "human_first", "human", "human-1",
                    opponent_id="ai-1", ordered_participants=participants,
                    enforce_trusted_pair=True,
                )


class NpcFrontendContractTests(unittest.TestCase):
    def test_multiplayer_controls_roster_and_private_container_are_conditional(self):
        root = Path(__file__).resolve().parents[1] / "app" / "static"
        html = (root / "index.html").read_text(encoding="utf-8")
        script = (root / "app.js").read_text(encoding="utf-8")
        styles = (root / "styles.css").read_text(encoding="utf-8")
        for element_id in (
            "multiplayerOptions", "targetPlayerCount", "fillWithNpcs",
            "seatPreview", "roomParticipants", "privateStatePanel",
        ):
            self.assertIn(f'id="{element_id}"', html)
        self.assertIn('options.classList.toggle("hidden", !multiplayer)', script)
        self.assertIn('maxPlayers <= 2) return 2', script)
        self.assertIn(
            "requirement.supportsNpcs",
            script,
        )
        self.assertIn("requirement.npcAvailable", script)
        self.assertIn("allowedPlayerCounts.forEach((count)", script)
        self.assertIn("fill_with_npcs: selectedFillWithNpcs()", script)
        self.assertIn('selectedMachineCount >= 1', script)
        self.assertIn('participant.participant_kind === "system_npc"', script)
        self.assertIn('targetRoom.private_state', script)
        self.assertIn("grid-template-columns: repeat(2, minmax(0, 1fr))", styles)
        self.assertIn(".room-participants.count-5", styles)


class NpcApiContractTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="duel-npc-api-")
        root = Path(self.temporary.name)
        self.db_patch = patch.object(database, "DB_PATH", root / "test.db")
        self.db_patch.start()
        self.persona_dir = root / "personas"
        self.persona_dir.mkdir()
        self.avatar_dir = root / "avatars"
        self.avatar_dir.mkdir()
        (self.avatar_dir / "quiet.png").write_bytes(b"test-png-fixture")
        write_persona(
            self.persona_dir, "quiet", "安静测试机", avatar="quiet.png"
        )
        write_persona(self.persona_dir, "bright", "明亮测试机")
        self.env_patch = patch.dict(
            "os.environ",
            {
                "DUEL_NPC_PERSONAS_DIR": str(self.persona_dir),
                "DUEL_NPC_AVATARS_DIR": str(self.avatar_dir),
                "DUEL_NPC_PROVIDER": "openai_compatible",
                "DUEL_NPC_API_BASE": "https://provider.invalid/v1",
                "DUEL_NPC_API_KEY": "test-only-key",
                "DUEL_NPC_MODEL": "test-model",
            },
        )
        self.env_patch.start()
        self.game_patch = patch.dict(
            GAMES, {DummyNpcMultiplayer.game_type: DummyNpcMultiplayer()}
        )
        self.game_patch.start()
        database.init_db()
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=main_module.app),
            base_url="http://duel.test",
        )

    async def asyncTearDown(self):
        await self.client.aclose()
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
            "X-Duel-Human-Name": "%E4%BA%BA%E7%B1%BB%E4%B8%80%E5%8F%B7",
            "X-Duel-Bound-Ais": encoded,
        }

    async def test_web_fills_only_creation_seats_and_mcp_cannot_impersonate_npc(self):
        identity = await self.client.get("/api/whoami", headers=self.headers())
        self.assertTrue(identity.json()["npc_provider"]["available"])
        self.assertEqual(
            identity.json()["npc_provider"]["provider"], "openai_compatible"
        )
        created = await self.client.post(
            "/api/rooms",
            headers=self.headers(),
            json={
                "player_id": "human-1",
                "ai_players": ["ai-1"],
                "game_type": DummyNpcMultiplayer.game_type,
                "target_player_count": 4,
                "fill_with_npcs": True,
            },
        )
        self.assertEqual(created.status_code, 200, created.text)
        room = created.json()["room"]
        self.assertEqual(
            [item["participant_kind"] for item in room["participants"]],
            ["human", "bound_machine", "system_npc", "system_npc"],
        )
        self.assertEqual(
            {item["display_name"] for item in room["participants"][2:]},
            {"安静测试机", "明亮测试机"},
        )
        quiet = next(
            item for item in room["participants"]
            if item.get("npc_persona_id") == "quiet"
        )
        self.assertEqual(quiet["avatar_url"], "/api/npc-avatars/quiet.png")
        avatar = await self.client.get(quiet["avatar_url"])
        self.assertEqual(avatar.status_code, 200)
        self.assertEqual(avatar.content, b"test-png-fixture")
        self.assertEqual(avatar.headers["x-content-type-options"], "nosniff")
        npc_id = room["participants"][2]["player_id"]
        forged = await self.client.post(
            "/mcp/play",
            json={"action": "state", "player_id": npc_id, "room_id": room["room_id"]},
        )
        self.assertEqual(forged.status_code, 403)
        legacy_game = await self.client.post(
            "/api/rooms",
            headers=self.headers(),
            json={
                "player_id": "human-1", "ai_players": ["ai-1"],
                "game_type": "tictactoe", "target_player_count": 3,
                "fill_with_npcs": True,
            },
        )
        self.assertEqual(legacy_game.status_code, 400)

    async def test_disabled_provider_blocks_only_npc_fill(self):
        with patch.dict("os.environ", {"DUEL_NPC_PROVIDER": "disabled"}):
            disabled_identity = await self.client.get(
                "/api/whoami", headers=self.headers()
            )
            self.assertFalse(
                disabled_identity.json()["npc_provider"]["available"]
            )
            npc_room = await self.client.post(
                "/api/rooms",
                headers=self.headers(),
                json={
                    "player_id": "human-1",
                    "ai_players": ["ai-1"],
                    "game_type": DummyNpcMultiplayer.game_type,
                    "target_player_count": 4,
                    "fill_with_npcs": True,
                },
            )
            self.assertEqual(npc_room.status_code, 503, npc_room.text)
            self.assertIn("未配置", npc_room.text)

            ordinary = await self.client.post(
                "/api/rooms",
                headers=self.headers(),
                json={
                    "player_id": "human-1",
                    "ai_players": ["ai-1"],
                    "game_type": "tictactoe",
                    "target_player_count": 2,
                },
            )
            self.assertEqual(ordinary.status_code, 200, ordinary.text)


if __name__ == "__main__":
    unittest.main()

"""Monopoly room/API integration using an isolated SQLite database only.

Run from vendor/duel: python -m unittest tests.test_monopoly_integration.
The few position/ownership fixtures are written only into each test's temporary
database; actions, identity checks, persistence and settlement use production paths.
"""
import asyncio
import base64
import json
import random
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import httpx

from app import chips, database, framework, invites, npc_controller, takeover
from app import main as main_module
from app.games import GAMES, game_catalog
from app.games.monopoly import Monopoly
from app.npc_personas import NpcPersona
from app.local_config import LOCAL_AI_ID, LOCAL_HUMAN_ID
from app.local_mcp import forward_play, play_input_schema


class FixedDice(random.Random):
    def __init__(self, values=(1, 2)):
        super().__init__(31)
        self.values = list(values)
        self.index = 0

    def randint(self, start, stop):
        if (start, stop) == (1, 6):
            value = self.values[self.index % len(self.values)]
            self.index += 1
            return value
        return super().randint(start, stop)


class MonopolyIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="duel-monopoly-integration-")
        self.addCleanup(self.temporary.cleanup)
        self.db_patch = patch.object(database, "DB_PATH", Path(self.temporary.name) / "test.db")
        self.db_patch.start()
        self.addCleanup(self.db_patch.stop)
        database.init_db()
        self.game = Monopoly(FixedDice())
        self.games_patch = patch.dict(GAMES, {"monopoly": self.game})
        self.games_patch.start()
        self.addCleanup(self.games_patch.stop)
        self.events_patch = patch.object(main_module, "revision_events", main_module.RevisionEvents())
        self.events_patch.start()
        self.addCleanup(self.events_patch.stop)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=main_module.app), base_url="http://monopoly.test")
        self.addAsyncCleanup(self.client.aclose)

    @staticmethod
    def seats(count):
        return [{"player_id": "human-monopoly" if i == 0 else f"ai-monopoly-{i}",
                 "role": "human" if i == 0 else "ai",
                 "participant_kind": "human" if i == 0 else "bound_machine",
                 "display_name": f"测试席 {i + 1}"} for i in range(count)]

    def ordinary(self, count=2):
        members = self.seats(count)
        room = framework.create_room("monopoly", "human_first", "human", members[0]["player_id"],
                                     opponent_id=members[1]["player_id"], ordered_participants=members)
        if room.get("confirmation_required"):
            for member in members[1:]:
                room = framework.respond_to_invitation(room["room_id"], "ai", member["player_id"], "accept")
        self.assertEqual(room["status"], "playing")
        return room

    def invited(self, count=2, timeout=False):
        members = self.seats(count)
        room = invites.create_invite("monopoly", "human", members[0]["player_id"],
                                     target_player_count=count, timeout_takeover=timeout)
        for member in members[1:]:
            invites.join_invite(room["invite_code"], "ai", member["player_id"])
        with patch.object(invites.secrets, "SystemRandom") as secure_random:
            secure_random.return_value.shuffle.side_effect = lambda players: None
            return invites.start_invite(room["room_id"], "human", members[0]["player_id"])

    @staticmethod
    def actor(room):
        return next(p for p in room["participants"] if p["player_id"] == room["current_player_id"])

    def move(self, room, action, **params):
        actor = self.actor(room)
        return framework.play_move(room["room_id"], actor["role"], actor["player_id"],
                                   {"action": action, "action_seq": room["board_state"]["action_seq"], **params},
                                   expected_revision=room["revision"])

    def persist_fixture(self, room, state):
        with database.write_transaction() as conn:
            conn.execute("UPDATE rooms SET board_state=? WHERE room_id=?", (json.dumps(state), room["room_id"]))
        return framework.get_room(room["room_id"])

    def wallet_snapshot(self, room):
        return {p["player_id"]: chips.get_wallet(p["role"], p["player_id"])["balance"]
                for p in room["participants"] if p["participant_kind"] != "system_npc"}

    def expire(self, room):
        then = (datetime.now(timezone.utc) - timedelta(seconds=181)).isoformat()
        with database.write_transaction() as conn:
            conn.execute("UPDATE room_invites SET turn_started_at=? WHERE room_id=?", (then, room["room_id"]))

    async def mcp(self, **body):
        response = await self.client.post("/mcp/play", json=body)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_catalog_and_other_player_limits_are_unchanged(self):
        catalog = {item["game_type"]: item for item in game_catalog()}
        self.assertEqual(catalog["monopoly"]["display_name"], "大富翁")
        self.assertEqual(catalog["monopoly"]["allowed_player_counts"], [2, 3, 4, 5, 6])
        self.assertEqual(catalog["monopoly"]["recommended_players"], 4)
        self.assertEqual(catalog["aeroplane_chess"]["allowed_player_counts"], [2, 3, 4])
        self.assertEqual(catalog["tictactoe"]["allowed_player_counts"], [2])

    def test_two_four_six_player_ordinary_and_invite_full_round_wallet_isolation(self):
        for creator in (self.ordinary, self.invited):
            for count in (2, 4, 6):
                with self.subTest(kind=creator.__name__, count=count):
                    room = creator(count)
                    self.assertEqual(len(room["board_state"]["players"]), count)
                    self.assertEqual(len(room["board_state"]["tiles"]), 40)
                    self.assertTrue(all(p["cash"] == 1500 for p in room["board_state"]["players"]))
                    balances = self.wallet_snapshot(room)
                    order = [p["player_id"] for p in room["participants"]]
                    for expected in order:
                        self.assertEqual(room["current_player_id"], expected)
                        room = self.move(room, "roll")
                        if room["board_state"]["phase"] == "purchase":
                            room = self.move(room, "buy")
                        self.assertEqual(room["board_state"]["phase"], "manage")
                        room = self.move(room, "end_turn")
                    self.assertEqual(room["current_player_id"], order[0])
                    self.assertEqual(self.wallet_snapshot(room), balances)

    def test_concurrent_roll_and_purchase_charge_once_and_replays_are_rejected(self):
        for creator in (self.ordinary, self.invited):
            with self.subTest(kind=creator.__name__):
                room = creator()
                for action in ("roll", "buy"):
                    baseline = deepcopy(room)
                    def attempt(_):
                        try:
                            return self.move(baseline, action)
                        except framework.DuelError:
                            return None
                    with ThreadPoolExecutor(2) as pool:
                        results = list(pool.map(attempt, range(2)))
                    self.assertEqual(sum(result is not None for result in results), 1)
                    room = framework.get_room(room["room_id"])
                    self.assertEqual(room["revision"], baseline["revision"] + 1)
                    self.assertEqual(room["board_state"]["action_seq"], baseline["board_state"]["action_seq"] + 1)
                    with self.assertRaises(framework.DuelError):
                        self.move(baseline, action)
                buyer = room["board_state"]["players"][0]
                tile = room["board_state"]["tiles"][3]
                self.assertEqual(tile["owner"], buyer["player_id"])
                self.assertEqual(buyer["cash"], 1500 - tile["price"])

    def test_wrong_actor_stale_action_sequence_and_forged_money_are_atomic(self):
        room = self.invited()
        actor, other = room["participants"]
        for player, move in [(other, {"action": "roll", "action_seq": 0}),
                             (actor, {"action": "roll", "action_seq": -1}),
                             (actor, {"action": "roll"}),
                             (actor, {"action": "buy", "action_seq": 0, "cash": 999999}),
                             ({"player_id": "stranger", "role": "ai"}, {"action": "roll", "action_seq": 0})]:
            with self.subTest(player=player["player_id"], move=move):
                with self.assertRaises(framework.DuelError):
                    framework.play_move(room["room_id"], player["role"], player["player_id"], move,
                                        expected_revision=room["revision"])
                fresh = framework.get_room(room["room_id"])
                self.assertEqual(fresh["revision"], room["revision"])
                self.assertEqual(fresh["board_state"], room["board_state"])

    def test_concurrent_rent_roll_transfers_cash_once_and_wallets_never_change(self):
        room = self.invited(4)
        payer, owner = room["participants"][:2]
        state = deepcopy(room["board_state"])
        state["tiles"][3]["owner"] = owner["player_id"]
        room = self.persist_fixture(room, state)
        wallets = self.wallet_snapshot(room)
        baseline = deepcopy(room)

        def roll_once(_):
            try:
                return self.move(baseline, "roll")
            except framework.DuelError:
                return None

        with ThreadPoolExecutor(2) as pool:
            results = list(pool.map(roll_once, range(2)))
        self.assertEqual(sum(result is not None for result in results), 1)
        room = framework.get_room(room["room_id"])
        cash = {p["player_id"]: p["cash"] for p in room["board_state"]["players"]}
        self.assertLess(cash[payer["player_id"]], 1500)
        self.assertEqual(1500 - cash[payer["player_id"]], cash[owner["player_id"]] - 1500)
        self.assertEqual(sum(cash.values()), 6000)
        self.assertEqual(room["board_state"]["action_seq"], 1)
        self.assertEqual(self.wallet_snapshot(room), wallets)

    async def test_http_identity_and_participant_projections_do_not_reveal_future_cards(self):
        room = self.invited(4)
        human, machine = room["participants"][:2]
        headers = {"X-Duel-Human-Player": human["player_id"]}
        response = await self.client.get(f'/api/rooms/{room["room_id"]}', headers=headers)
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        for forbidden in ("chance_deck", "community_deck", "rng_state", "_decks", "_charges", "_resume"):
            self.assertNotIn(f'"{forbidden}"', json.dumps(payload))
        self.assertTrue(payload["room"]["private_state"]["legal_actions"])
        snapshot = await self.mcp(action="state", player_id=machine["player_id"],
                                  room_id=room["room_id"], full_state=True)
        self.assertTrue(snapshot["bootstrap"])
        self.assertFalse(snapshot["room"]["private_state"].get("legal_actions"))
        snapshot = await self.mcp(action="state", player_id=machine["player_id"],
                                  room_id=room["room_id"], full_state=True)
        self.assertFalse(snapshot["snapshot"]["legal_actions"])
        forged_view = await self.client.get(f'/api/rooms/{room["room_id"]}', headers=headers,
                                            params={"player_id": machine["player_id"]})
        self.assertEqual(forged_view.status_code, 403, forged_view.text)
        stranger = await self.client.get(f'/api/rooms/{room["room_id"]}',
                                         headers={"X-Duel-Human-Player": "outsider"})
        self.assertEqual(stranger.status_code, 403, stranger.text)
        forged_move = await self.client.post(f'/api/rooms/{room["room_id"]}/move', headers=headers,
            json={"player_id": machine["player_id"], "revision": room["revision"],
                  "move": {"action": "roll", "action_seq": 0}})
        self.assertEqual(forged_move.status_code, 403, forged_move.text)
        self.assertEqual(framework.get_room(room["room_id"])["board_state"], room["board_state"])

    def test_rejected_trade_does_not_move_assets_and_invalid_property_actions_are_atomic(self):
        room = self.move(self.move(self.invited(4), "roll"), "buy")
        owner, recipient = room["participants"][:2]
        for action, params in (("build", {"tile_id": 3}),
                               ("mortgage", {"tile_id": 1}),
                               ("redeem", {"tile_id": 3}),
                               ("sell_building", {"tile_id": 3}),
                               ("propose_trade", {"to": recipient["player_id"], "give_cash": 0,
                                  "take_cash": 0, "give_tiles": [1], "take_tiles": []})):
            with self.subTest(action=action):
                with self.assertRaises(framework.DuelError):
                    self.move(room, action, **params)
                self.assertEqual(framework.get_room(room["room_id"])["board_state"], room["board_state"])
        before = deepcopy(room["board_state"])
        room = self.move(room, "propose_trade", to=recipient["player_id"], give_cash=0,
                         take_cash=100, give_tiles=[3], take_tiles=[])
        room = self.move(room, "end_turn")
        room = self.move(room, "respond_trade", accept=False)
        self.assertEqual(room["board_state"]["players"], before["players"])
        self.assertEqual(room["board_state"]["tiles"], before["tiles"])
        self.assertEqual(room["current_player_id"], recipient["player_id"])
        self.assertEqual(room["board_state"]["phase"], "roll")

    async def test_pending_trade_mcp_bound_machine_gate_events_and_invalidation(self):
        for creator in (self.ordinary, self.invited):
            for invalidate in (False, True):
                with self.subTest(kind=creator.__name__, invalidate=invalidate):
                    room = self.move(self.move(creator(3), "roll"), "buy")
                    owner, recipient = room["participants"][:2]
                    self.assertEqual(recipient["participant_kind"], "bound_machine")
                    revision, seq = room["revision"], room["board_state"]["action_seq"]
                    room = self.move(room, "propose_trade", to=recipient["player_id"],
                                     give_cash=100, take_cash=20, give_tiles=[3], take_tiles=[])
                    self.assertEqual(room["revision"], revision + 1)
                    self.assertEqual(room["board_state"]["action_seq"], seq + 1)
                    self.assertEqual(room["current_player_id"], owner["player_id"])
                    snapshot = await self.mcp(action="state", room_id=room["room_id"],
                                              player_id=recipient["player_id"])
                    public = snapshot["room"]["board_state"]
                    self.assertEqual(public["trade"], room["board_state"]["trade"])
                    self.assertEqual(snapshot["room"]["private_state"]["legal_actions"], [])
                    waiting = await self.mcp(action="state", room_id=room["room_id"],
                                             player_id=recipient["player_id"])
                    self.assertEqual(waiting["wait"], owner["player_id"])
                    full = await self.mcp(action="state", room_id=room["room_id"],
                                          player_id=recipient["player_id"], full_state=True)
                    self.assertEqual(full["snapshot"]["board_state"]["trade"], public["trade"])
                    for key in ("_trade_assets", "_trade_proposed_turn", "_trade_return"):
                        self.assertNotIn(key, json.dumps(snapshot))
                    if invalidate:
                        state = deepcopy(room["board_state"])
                        state["players"][0]["cash"] = 99
                        room = self.persist_fixture(room, state)
                    room = self.move(room, "end_turn")
                    delta = await self.mcp(action="state", room_id=room["room_id"],
                                           player_id=recipient["player_id"])
                    self.assertNotIn("wait", delta)
                    self.assertEqual(delta["r"], revision + 2)
                    recovered = await self.mcp(action="state", room_id=room["room_id"],
                                               player_id=recipient["player_id"], full_state=True)
                    actions = recovered["snapshot"]["legal_actions"]
                    if invalidate:
                        self.assertIn("roll", [a["action"] for a in actions])
                        self.assertIsNone(room["board_state"]["trade"])
                        self.assertTrue(any(isinstance(e,list) and len(e)>2 and e[2].get("trade", "absent") is None
                                            for e in delta.get("events", [])))
                    else:
                        self.assertEqual([a["action"] for a in actions], ["respond_trade"] * 2)
                        await self.mcp(action="move", room_id=room["room_id"],
                            player_id=recipient["player_id"], revision=room["revision"], move=actions[0])
                        room = framework.get_room(room["room_id"])
                        self.assertEqual(room["current_player_id"], recipient["player_id"])
                        self.assertEqual(room["board_state"]["phase"], "roll")
                        self.assertIsNone(room["board_state"]["trade"])
                        self.assertEqual(room["revision"], revision + 3)
                        self.assertEqual(room["board_state"]["action_seq"], seq + 3)
                    self.move(room, "roll")

    def test_cross_process_restore_preserves_roll_property_auction_and_sequence(self):
        room = self.move(self.move(self.ordinary(4), "roll"), "auction")
        room = self.move(room, "bid", amount=20)
        expected = room["board_state"]
        script = """
import json, sys
from pathlib import Path
from app import database, framework
database.DB_PATH = Path(sys.argv[1])
room = framework.get_room(sys.argv[2])
print(json.dumps({'board': room['board_state'], 'current': room['current_player_id'], 'revision': room['revision']}))
"""
        result = subprocess.run([sys.executable, "-c", script, str(database.DB_PATH), room["room_id"]],
                                cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        restored = json.loads(result.stdout)
        self.assertEqual(restored["board"], expected)
        self.assertEqual(restored["current"], room["current_player_id"])
        self.assertEqual(restored["revision"], room["revision"])
        while room["board_state"]["phase"] == "auction":
            room = self.move(room, "pass_bid")
        winner = room["board_state"]["tiles"][3]["owner"]
        self.assertIsNotNone(winner)
        cash = {p["player_id"]: p["cash"] for p in room["board_state"]["players"]}
        self.assertEqual(cash[winner], 1480)
        self.assertEqual(sum(cash.values()), 5980)

    def test_concurrent_auction_bids_advance_only_once(self):
        room = self.move(self.move(self.invited(4), "roll"), "auction")
        bidder = self.actor(room)["player_id"]
        baseline = deepcopy(room)

        def bid_once(_):
            try:
                return self.move(baseline, "bid", amount=20)
            except framework.DuelError:
                return None

        with ThreadPoolExecutor(2) as pool:
            results = list(pool.map(bid_once, range(2)))
        self.assertEqual(sum(result is not None for result in results), 1)
        room = framework.get_room(room["room_id"])
        self.assertEqual(room["board_state"]["action_seq"], baseline["board_state"]["action_seq"] + 1)
        for _ in range(10):
            if room["board_state"]["phase"] != "auction":
                break
            room = self.move(room, "pass_bid")
        self.assertEqual(room["board_state"]["tiles"][3]["owner"], bidder)
        self.assertEqual(next(p for p in room["board_state"]["players"] if p["player_id"] == bidder)["cash"], 1480)

    async def test_web_ordinary_creation_and_mcp_binding_moves_for_all_table_sizes(self):
        for count in (2, 4, 6):
            with self.subTest(count=count):
                members = self.seats(count)
                machines = [{"id": p["player_id"], "name": p["display_name"]} for p in members[1:]]
                headers = {"X-Duel-Human-Player": members[0]["player_id"], "X-Duel-Bound-Ais":
                           base64.urlsafe_b64encode(json.dumps(machines).encode()).decode()}
                response = await self.client.post("/api/rooms", headers=headers, json={
                    "player_id": members[0]["player_id"], "ai_players": [p["player_id"] for p in members[1:]],
                    "game_type": "monopoly", "target_player_count": count})
                self.assertEqual(response.status_code, 200, response.text)
                room_id = response.json()["room"]["room_id"]
                room = framework.get_room(room_id)
                self.assertEqual(room["status"], "playing")
                self.assertFalse(room["confirmation_required"])
                for action in ("roll", "buy", "end_turn"):
                    response = await self.client.post(f"/api/rooms/{room_id}/move", headers=headers, json={
                        "player_id": members[0]["player_id"], "revision": room["revision"],
                        "move": {"action": action, "action_seq": room["board_state"]["action_seq"]}})
                    self.assertEqual(response.status_code, 200, response.text)
                    room = framework.get_room(room_id)
                await self.mcp(action="move", player_id=members[1]["player_id"], room_id=room_id,
                               revision=room["revision"], move={"action": "roll", "action_seq": room["board_state"]["action_seq"]})
                snapshot = await self.mcp(action="state", player_id=members[1]["player_id"], room_id=room_id, full_state=True)
                serialized = json.dumps(snapshot)
                self.assertIn("action_seq", serialized)
                self.assertIn("legal_actions", serialized)
                for key in ("chance_deck", "community_deck", "rng_state", "_decks", "_charges", "_resume"):
                    self.assertNotIn(f'"{key}"', serialized)

    async def test_mcp_delta_after_resign_or_leave_supplies_safe_auction_decision_context(self):
        for creator in (self.ordinary, self.invited):
            for lifecycle in ("resign", "leave"):
                with self.subTest(room_kind=creator.__name__, lifecycle=lifecycle):
                    room = creator(4)
                    room = self.move(self.move(self.move(room, "roll"), "buy"), "end_turn")
                    bidder = room["participants"][1]
                    outgoing = room["participants"][2]
                    self.assertEqual(room["current_player_id"], bidder["player_id"])
                    state = deepcopy(room["board_state"])
                    state["tiles"][6].update(owner=outgoing["player_id"], mortgaged=True)
                    next(p for p in state["players"] if p["player_id"] == outgoing["player_id"])["cash"] = 333
                    room = self.persist_fixture(room, state)
                    bootstrap = await self.mcp(action="state", room_id=room["room_id"], player_id=bidder["player_id"])
                    self.assertTrue(bootstrap["bootstrap"])
                    self.assertEqual(bootstrap["room"]["board_state"]["tiles"][6]["owner"], outgoing["player_id"])
                    await self.mcp(action=lifecycle, room_id=room["room_id"], player_id=outgoing["player_id"])
                    delta = await self.mcp(action="state", room_id=room["room_id"], player_id=bidder["player_id"])
                    self.assertNotIn("bootstrap", delta)
                    self.assertNotIn("full_state", delta)
                    self.assertNotIn("room", delta)
                    self.assertNotIn("wait", delta)
                    context = delta["public_state"]
                    self.assertNotIn('decision_context', delta.get('private', {}))
                    self.assertEqual(context["phase"], "auction")
                    self.assertEqual(context["auction"]["tile_id"], 6)
                    self.assertEqual(context["auction"]["bid"], 0)
                    self.assertEqual(context["current_player_id"], bidder["player_id"])
                    self.assertEqual(context["turn_player_id"], bidder["player_id"])
                    self.assertEqual(context["action_seq"], state["action_seq"] + 1)
                    self.assertEqual(context["dice"], [])
                    self.assertIsNone(context["debt"])
                    self.assertIsNone(context["trade"])
                    land = next(tile for tile in context["tiles"] if tile["id"] == 6)
                    self.assertEqual(land, {"id": 6, "owner": None, "level": 0, "mortgaged": False})
                    departed = next(p for p in context["players"] if p["player_id"] == outgoing["player_id"])
                    self.assertEqual(departed["cash"], 0)
                    self.assertTrue(departed["bankrupt"])
                    for hidden in ("_decks", "_charges", "_resume", "rng_state", "jail_cards"):
                        self.assertNotIn(f'"{hidden}"', json.dumps(context))
                    bid = dict(action="bid", amount=context["auction"]["bid"] + 1, action_seq=context["action_seq"])
                    self.assertEqual(bid["action_seq"], context["action_seq"])
                    await self.mcp(action="move", room_id=room["room_id"], player_id=bidder["player_id"],
                                   revision=delta["r"], move=bid)
                    fresh = framework.get_room(room["room_id"])
                    self.assertEqual(fresh["board_state"]["auction"]["tile_id"], 6)
                    self.assertEqual(fresh["board_state"]["auction"]["highest_bidder"], bidder["player_id"])
                    self.assertEqual(fresh["board_state"]["auction"]["bid"], bid["amount"])

    async def test_local_mcp_adapter_injects_identity_and_exposes_submit_ready_guidance(self):
        transport = httpx.ASGITransport(app=main_module.app)
        status, created = await forward_play({
            "action": "new", "game_type": "monopoly", "mode": "ai_first",
            "player_id": "forged-ai", "opponent_id": "forged-human", "target_player_count": 2,
        }, transport=transport, base_url="http://monopoly.test")
        self.assertEqual(status, 200, created)
        room_id = created["room"]["room_id"]
        room = framework.get_room(room_id)
        self.assertEqual({p["player_id"] for p in room["participants"]}, {LOCAL_HUMAN_ID, LOCAL_AI_ID})
        self.assertEqual(room["status"], "playing")
        self.assertFalse(room["confirmation_required"])
        self.assertEqual(room["current_player_id"], LOCAL_AI_ID)
        status, snapshot = await forward_play({"action": "state", "room_id": room_id, "full_state": True},
                                               transport=transport, base_url="http://monopoly.test")
        self.assertEqual(status, 200, snapshot)
        legal = snapshot["snapshot"]["legal_actions"]
        roll = next(move for move in legal if move["action"] == "roll")
        self.assertEqual(roll["action_seq"], room["board_state"]["action_seq"])
        status, result = await forward_play({"action": "move", "room_id": room_id,
            "revision": room["revision"], "move": roll, "player_id": LOCAL_HUMAN_ID},
            transport=transport, base_url="http://monopoly.test")
        self.assertEqual(status, 200, result)
        self.assertEqual(framework.get_room(room_id)["board_state"]["action_seq"], 1)
        schema = play_input_schema()
        self.assertNotIn("player_id", schema["properties"])
        self.assertNotIn("opponent_id", schema["properties"])
        for phrase in ("抵押", "交易", "拍卖", "监狱", "破产"):
            self.assertIn(phrase, self.game.rules_text)

    async def test_mcp_invite_and_web_join_start_and_move_for_all_table_sizes(self):
        for count in (2, 4, 6):
            with self.subTest(count=count):
                created = await self.mcp(action="invite", player_id="ai-inviter", game_type="monopoly", target_player_count=count)
                code, room_id = created["invite_code"], created["room_id"]
                response = await self.client.post("/api/invites/join", headers={"X-Duel-Human-Player": "human-guest"},
                                                  json={"invite_code": code, "player_id": "ignored-forgery"})
                self.assertEqual(response.status_code, 200, response.text)
                for index in range(count - 2):
                    await self.mcp(action="join", player_id=f"ai-guest-{index}", invite_code=code)
                with patch.object(invites.secrets, "SystemRandom") as rng:
                    rng.return_value.shuffle.side_effect = lambda players: None
                    await self.mcp(action="start", player_id="ai-inviter", room_id=room_id)
                room = framework.get_room(room_id)
                self.assertEqual(room["current_player_id"], "ai-inviter")
                for action in ("roll", "buy", "end_turn"):
                    await self.mcp(action="move", player_id="ai-inviter", room_id=room_id, revision=room["revision"],
                                   move={"action": action, "action_seq": room["board_state"]["action_seq"]})
                    room = framework.get_room(room_id)
                response = await self.client.post(f"/api/rooms/{room_id}/move", headers={"X-Duel-Human-Player": "human-guest"},
                    json={"player_id": "human-guest", "revision": room["revision"],
                          "move": {"action": "roll", "action_seq": room["board_state"]["action_seq"]}})
                self.assertEqual(response.status_code, 200, response.text)

    async def test_timeout_moves_once_reclaim_and_offline_presence(self):
        room = self.invited(2, timeout=True)
        balances = self.wallet_snapshot(room)
        self.expire(room)
        results = await asyncio.gather(takeover.run_timeout_turn(room["room_id"]), takeover.run_timeout_turn(room["room_id"]))
        self.assertEqual(sum(result is not None for result in results), 1)
        room = framework.get_room(room["room_id"])
        self.assertEqual(room["board_state"]["action_seq"], 1)
        self.assertEqual(self.wallet_snapshot(room), balances)
        self.expire(room)
        actor = self.actor(room)
        invites.reclaim(room["room_id"], actor["role"], actor["player_id"])
        self.assertIsNone(await takeover.run_timeout_turn(room["room_id"]))
        room = self.move(room, "buy")
        self.expire(room)
        old = (datetime.now(timezone.utc) - timedelta(minutes=20)).isoformat()
        with database.write_transaction() as conn:
            conn.execute("UPDATE room_event_cursors SET updated_at=? WHERE room_id=?", (old, room["room_id"]))
        self.assertIsNone(await takeover.run_timeout_turn(room["room_id"]))

    def test_event_payments_and_nonturn_debt_survive_restore_without_double_collection(self):
        room = self.invited(4)
        owner, debtor, third, fourth = room["participants"]
        state = deepcopy(room["board_state"])
        state["players"][0]["position"] = 39
        state["players"][1]["cash"] = 0
        # Publicly resolved birthday event: each other player owes the drawer 10.
        state["_decks"]["chest"] = [12] + [i for i in state["_decks"]["chest"] if i != 12]
        room = self.persist_fixture(room, state)
        wallets = self.wallet_snapshot(room)
        room = self.move(room, "roll")
        self.assertEqual(room["board_state"]["phase"], "debt")
        self.assertEqual(room["board_state"]["current_player_id"], owner["player_id"])
        self.assertEqual(room["current_player_id"], debtor["player_id"])
        self.assertEqual(room["board_state"]["debt"]["amount"], 10)
        state = deepcopy(room["board_state"])
        # Simulate process-local state loss: reload from the saved queue and use the normal action path.
        room = framework.get_room(room["room_id"])
        self.assertEqual(room["board_state"], state)
        original = deepcopy(room)
        room = self.move(room, "bankrupt")
        self.assertEqual(room["current_player_id"], owner["player_id"])
        self.assertEqual(room["board_state"]["phase"], "manage")
        cash = {p["player_id"]: p["cash"] for p in room["board_state"]["players"]}
        self.assertEqual(cash[owner["player_id"]], 1720)
        self.assertEqual(cash[third["player_id"]], 1490)
        self.assertEqual(cash[fourth["player_id"]], 1490)
        with self.assertRaises(framework.DuelError):
            self.move(original, "bankrupt")
        self.assertEqual(framework.get_room(room["room_id"])["board_state"], room["board_state"])
        self.assertEqual(self.wallet_snapshot(room), wallets)

    def test_jail_entry_persists_and_bail_keeps_cash_separate_from_wallet(self):
        room = self.invited(2)
        prisoner_id = room["current_player_id"]
        state = deepcopy(room["board_state"])
        state["players"][0]["position"] = 27
        room = self.persist_fixture(room, state)
        wallets = self.wallet_snapshot(room)
        room = self.move(room, "roll")
        prisoner = next(p for p in room["board_state"]["players"] if p["player_id"] == prisoner_id)
        self.assertEqual(prisoner["position"], 10)
        self.assertTrue(prisoner["jailed"])
        self.assertEqual(prisoner["cash"], 1500)
        room = self.move(room, "end_turn")
        room = self.move(room, "roll")
        if room["board_state"]["phase"] == "purchase":
            room = self.move(room, "buy")
        room = self.move(room, "end_turn")
        self.assertEqual(room["current_player_id"], prisoner_id)
        room = framework.get_room(room["room_id"])
        room = self.move(room, "pay_bail")
        prisoner = next(p for p in room["board_state"]["players"] if p["player_id"] == prisoner_id)
        self.assertFalse(prisoner["jailed"])
        self.assertEqual(prisoner["cash"], 1450)
        self.assertEqual(room["board_state"]["phase"], "roll")
        room = self.move(room, "roll")
        self.assertEqual(next(p for p in room["board_state"]["players"] if p["player_id"] == prisoner_id)["position"], 13)
        self.assertEqual(self.wallet_snapshot(room), wallets)

    def test_build_sell_mortgage_redeem_and_consensual_trade_are_persisted(self):
        room = self.move(self.move(self.invited(4), "roll"), "buy")
        actor, recipient, outsider, _ = room["participants"]
        state = deepcopy(room["board_state"])
        state["tiles"][1]["owner"] = actor["player_id"]
        room = self.persist_fixture(room, state)
        wallets = self.wallet_snapshot(room)
        room = self.move(room, "build", tile_id=1)
        self.assertEqual(room["board_state"]["tiles"][1]["level"], 1)
        room = self.move(room, "sell_building", tile_id=1)
        self.assertEqual(room["board_state"]["tiles"][1]["level"], 0)
        room = self.move(room, "mortgage", tile_id=1)
        self.assertTrue(room["board_state"]["tiles"][1]["mortgaged"])
        room = self.move(room, "redeem", tile_id=1)
        self.assertFalse(room["board_state"]["tiles"][1]["mortgaged"])
        before = {p["player_id"]: p["cash"] for p in room["board_state"]["players"]}
        room = self.move(room, "propose_trade", to=recipient["player_id"], give_cash=0, take_cash=123,
                         give_tiles=[3], take_tiles=[])
        self.assertEqual(room["board_state"]["phase"], "manage")
        self.assertEqual(room["current_player_id"], actor["player_id"])
        room = self.move(room, "end_turn")
        self.assertEqual(room["current_player_id"], recipient["player_id"])
        self.assertEqual(room["board_state"]["tiles"][3]["owner"], actor["player_id"])
        for unauthorized in (actor, outsider):
            with self.assertRaises(framework.DuelError):
                framework.play_move(room["room_id"], unauthorized["role"], unauthorized["player_id"],
                                    {"action": "respond_trade", "accept": True,
                                     "action_seq": room["board_state"]["action_seq"]}, expected_revision=room["revision"])
        original = deepcopy(room)
        def accept(_):
            try:
                return self.move(original, "respond_trade", accept=True)
            except framework.DuelError:
                return None
        with ThreadPoolExecutor(2) as pool:
            results = list(pool.map(accept, range(2)))
        self.assertEqual(sum(result is not None for result in results), 1)
        room = framework.get_room(room["room_id"])
        self.assertEqual(room["board_state"]["tiles"][3]["owner"], recipient["player_id"])
        after = {p["player_id"]: p["cash"] for p in room["board_state"]["players"]}
        self.assertEqual(after[actor["player_id"]], before[actor["player_id"]] + 123)
        self.assertEqual(after[recipient["player_id"]], before[recipient["player_id"]] - 123)
        self.assertEqual(room["current_player_id"], recipient["player_id"])
        self.assertEqual(room["board_state"]["phase"], "roll")
        self.assertEqual(self.wallet_snapshot(room), wallets)

    async def test_timeout_cannot_accept_trade_for_another_participant(self):
        room = self.move(self.move(self.invited(2, timeout=True), "roll"), "buy")
        recipient = room["participants"][1]
        room = self.move(room, "propose_trade", to=recipient["player_id"], give_cash=0, take_cash=123,
                         give_tiles=[3], take_tiles=[])
        room = self.move(room, "end_turn")
        self.expire(room)
        balances = [p["cash"] for p in room["board_state"]["players"]]
        with self.assertRaises(framework.DuelError):
            framework.play_move(room["room_id"], recipient["role"], recipient["player_id"],
                                {"action": "respond_trade", "accept": True,
                                 "action_seq": room["board_state"]["action_seq"]},
                                expected_revision=room["revision"], takeover=True)
        result = await takeover.run_timeout_turn(room["room_id"])
        self.assertIsNotNone(result)
        self.assertNotEqual(result["board_state"]["phase"], "trade")
        self.assertEqual([p["cash"] for p in result["board_state"]["players"]], balances)
        self.assertEqual(result["board_state"]["tiles"][3]["owner"], room["participants"][0]["player_id"])

    async def test_ordinary_web_npc_fill_uses_existing_six_seat_admission(self):
        members = self.seats(2)
        machine = {"id": members[1]["player_id"], "name": "绑定小机"}
        headers = {"X-Duel-Human-Player": members[0]["player_id"],
                   "X-Duel-Bound-Ais": base64.urlsafe_b64encode(json.dumps([machine]).encode()).decode()}
        personas = [NpcPersona(f"ordinary-monopoly-{i}", f"测试 NPC {i}", "测试") for i in range(4)]
        with patch.object(main_module, "select_personas", return_value=personas), \
             patch.object(main_module, "npc_provider_capabilities", side_effect=AssertionError("Local policy needs no model")):
            response = await self.client.post("/api/rooms", headers=headers, json={
                "game_type": "monopoly", "player_id": members[0]["player_id"],
                "ai_players": [members[1]["player_id"]], "mode": "human_first",
                "target_player_count": 6, "fill_with_npcs": True})
        self.assertEqual(response.status_code, 200, response.text)
        room = response.json()["room"]
        self.assertEqual(room["status"], "playing")
        self.assertEqual(len(room["participants"]), 6)
        self.assertEqual(sum(p["participant_kind"] == "system_npc" for p in room["participants"]), 4)
        self.assertEqual(room["current_player_id"], members[0]["player_id"])
        self.assertEqual(len(room["board_state"]["players"]), 6)

    async def test_web_trade_waits_for_normal_npc_turn_before_scheduler_response(self):
        from app.npc_scheduler import NpcTurnScheduler
        from unittest.mock import Mock
        for accept, requested_cash in ((True, 20), (False, 200)):
            with self.subTest(accept=accept):
                room = invites.create_invite("monopoly", "human", "owner", target_player_count=3)
                invites.join_invite(room["invite_code"], "ai", "external")
                with patch.object(invites, "select_personas", return_value=[NpcPersona("trade-npc", "交易NPC", "测试")]), \
                     patch.object(invites.secrets, "SystemRandom") as rng:
                    rng.return_value.shuffle.side_effect = lambda seats: None
                    room = invites.start_invite(room["room_id"], "human", "owner", fill_with_npcs=True)
                npc = next(p for p in room["participants"] if p["participant_kind"] == "system_npc")
                state = deepcopy(room["board_state"])
                state["tiles"][1]["owner"] = "owner"
                room = self.persist_fixture(room, state)
                before = deepcopy(room["board_state"])
                wallets = self.wallet_snapshot(room)
                gate, completed = asyncio.Event(), asyncio.Event()
                decisions = []
                continued = asyncio.Event()
                provider = Mock()
                provider.decide.side_effect = AssertionError("No model decision allowed")
                async def run(room_id):
                    if decisions:
                        await continued.wait()
                    await gate.wait()
                    result = await npc_controller.run_current_npc_turn(room_id, provider=provider)
                    decisions.append(result)
                    return result
                scheduler = NpcTurnScheduler(turn_runner=run, room_changed=lambda _: completed.set(), visible_action_delay_seconds=0)
                with patch("app.npc_scheduler.list_active_npc_turn_room_ids", return_value=[]):
                    await scheduler.start()
                try:
                    with patch.object(main_module, "npc_turn_scheduler", scheduler), \
                         patch.object(npc_controller, "_schedule_npc_speech", return_value=None):
                        response = await self.client.post(f"/api/rooms/{room['room_id']}/move",
                            headers={"X-Duel-Human-Player": "owner"}, json={"player_id": "owner", "revision": room["revision"],
                            "move": {"action": "propose_trade", "to": npc["player_id"], "give_cash": 10,
                                     "take_cash": requested_cash, "give_tiles": [1], "take_tiles": [],
                                     "action_seq": before["action_seq"]}})
                        self.assertEqual(response.status_code, 200, response.text)
                        pending = framework.get_room(room["room_id"])
                        self.assertEqual(pending["current_player_id"], "owner")
                        self.assertEqual(await scheduler._drain_room(room["room_id"]), "idle")
                        self.assertEqual(decisions, [])
                        self.assertEqual(pending["board_state"]["players"], before["players"])
                        self.assertEqual(pending["board_state"]["tiles"], before["tiles"])
                        # Both real seats finish their normal turns first.
                        for _ in range(2):
                            pending = self.move(pending, "roll")
                            if pending["board_state"]["phase"] == "purchase":
                                pending = self.move(pending, "buy")
                            pending = self.move(pending, "end_turn")
                        self.assertEqual(pending["current_player_id"], npc["player_id"])
                        self.assertEqual({a["action"] for a in self.game.legal_actions(
                            pending["board_state"], npc["player_id"])}, {"respond_trade"})
                        cash_before_response = {p["player_id"]: p["cash"] for p in pending["board_state"]["players"]}
                        await scheduler.schedule(room["room_id"])
                        gate.set()
                        await asyncio.wait_for(completed.wait(), timeout=5)
                        result = framework.get_room(room["room_id"])
                        self.assertEqual(len(decisions), 1)
                        self.assertEqual(decisions[0].status, "applied")
                        self.assertEqual(decisions[0].source, "local")
                        self.assertEqual(decisions[0].action["accept"], accept)
                        self.assertEqual(result["current_player_id"], npc["player_id"])
                        self.assertIsNone(result["board_state"]["trade"])
                        self.assertNotEqual(result["board_state"]["phase"], "trade")
                        self.assertEqual(result["revision"], pending["revision"] + 1)
                        self.assertEqual(result["board_state"]["action_seq"], pending["board_state"]["action_seq"] + 1)
                        self.assertEqual(result["board_state"]["tiles"][1]["owner"], npc["player_id"] if accept else "owner")
                        cash = {p["player_id"]: p["cash"] for p in result["board_state"]["players"]}
                        self.assertEqual(cash["owner"], cash_before_response["owner"] + (10 if accept else 0))
                        self.assertEqual(cash[npc["player_id"]], cash_before_response[npc["player_id"]] - (10 if accept else 0))
                        self.assertEqual(self.wallet_snapshot(result), wallets)
                        provider.decide.assert_not_called()
                        with self.assertRaises(framework.DuelError):
                            framework.play_move(room["room_id"], "ai", npc["player_id"], decisions[0].action,
                                                expected_revision=pending["revision"])
                        self.assertIn("roll", [a["action"] for a in self.game.legal_actions(
                            result["board_state"], npc["player_id"])])
                finally:
                    await scheduler.shutdown()

    async def test_six_player_npc_fill_runs_a_full_turn_without_network(self):
        room = invites.create_invite("monopoly", "human", "owner", target_player_count=6)
        invites.join_invite(room["invite_code"], "ai", "external")
        personas = [NpcPersona(f"monopoly-{i}", f"测试 NPC {i}", "测试") for i in range(4)]
        with patch.object(invites, "select_personas", return_value=personas), \
             patch.object(invites.secrets, "SystemRandom") as rng:
            rng.return_value.shuffle.side_effect = lambda players: players.sort(key=lambda p: p["participant_kind"] != "system_npc")
            room = invites.start_invite(room["room_id"], "human", "owner", fill_with_npcs=True)
        self.assertEqual(len(room["participants"]), 6)
        self.assertEqual(sum(p["participant_kind"] == "system_npc" for p in room["participants"]), 4)
        original_actor = room["current_player_id"]
        self.assertTrue(original_actor.startswith("npc:"))
        with patch.object(npc_controller, "get_npc_provider", side_effect=AssertionError("No network provider allowed")), \
             patch.object(npc_controller, "_finish_npc_action", return_value=None):
            for _ in range(40):
                result = await npc_controller.run_current_npc_turn(room["room_id"])
                self.assertEqual(result.status, "applied")
                room = framework.get_room(room["room_id"])
                if room["current_player_id"] != original_actor:
                    break
            else:
                self.fail("NPC did not complete its turn in 40 decisions")
        self.assertGreaterEqual(room["board_state"]["action_seq"], 2)
        forbidden = await self.client.post("/mcp/play", json={
            "action": "state", "player_id": original_actor, "room_id": room["room_id"]})
        self.assertEqual(forbidden.status_code, 403, forbidden.text)

    async def test_bankruptcy_elimination_terminal_wallet_isolation_and_mcp_rematch(self):
        for creator in (self.ordinary, self.invited):
            for count in (2, 4, 6):
                with self.subTest(kind=creator.__name__, count=count):
                    room = creator(count)
                    wallets = self.wallet_snapshot(room)
                    with database.connect() as conn:
                        ledger_before = conn.execute("SELECT COALESCE(MAX(id), 0) FROM chip_ledger").fetchone()[0]
                    for loser_index in range(count - 1):
                        state = deepcopy(room["board_state"])
                        loser = next(p for p in state["players"] if p["player_id"] == room["current_player_id"])
                        loser["cash"] = 1
                        loser["position"] = 1  # Fixed 1 + 2 lands on the income tax.
                        room = self.persist_fixture(room, state)
                        room = self.move(room, "roll")
                        self.assertEqual(room["board_state"]["phase"], "debt")
                        room = self.move(room, "bankrupt")
                        self.assertTrue(next(p for p in room["board_state"]["players"] if p["player_id"] == loser["player_id"])["bankrupt"])
                        self.assertFalse(next(p for p in room["participants"] if p["player_id"] == loser["player_id"])["active"])
                        self.assertEqual(room["status"], "finished" if loser_index == count - 2 else "playing")
                    self.assertEqual(room["winner_player_id"], self.seats(count)[-1]["player_id"])
                    self.assertEqual(room["board_state"]["phase"], "finished")
                    # Existing platform achievements still reward completed games;
                    # Monopoly cash must not create any other wallet transaction.
                    with database.connect() as conn:
                        rewards = conn.execute("""SELECT w.subject_id, l.transaction_type, l.amount
                            FROM chip_ledger l JOIN chip_wallets w ON w.id=l.wallet_id
                            WHERE l.id > ?""", (ledger_before,)).fetchall()
                    self.assertTrue(all(row["transaction_type"] == "achievement_reward" for row in rewards))
                    expected_wallets = {pid: balance + sum(row["amount"] for row in rewards if row["subject_id"] == pid)
                                        for pid, balance in wallets.items()}
                    self.assertEqual(self.wallet_snapshot(room), expected_wallets)
                    if room.get("room_kind") == "invite":
                        # The existing shared UI disables authoritative rematch
                        # for invite rooms. Preserve that contract and start a
                        # new invitation, as for every existing invite game.
                        response = await self.client.post("/mcp/play", json={"action": "rematch",
                            "room_id": room["room_id"], "player_id": self.seats(count)[1]["player_id"]})
                        self.assertEqual(response.status_code, 409, response.text)
                        rematch = self.invited(count)
                    else:
                        result = await self.mcp(action="rematch", room_id=room["room_id"], player_id=self.seats(count)[1]["player_id"])
                        rematch = framework.get_room(result["room"]["room_id"])
                        self.assertEqual(rematch["rematch_of_room_id"], room["room_id"])
                    self.assertNotEqual(rematch["room_id"], room["room_id"])
                    self.assertEqual(len(rematch["participants"]), count)
                    self.assertTrue(all(p["active"] for p in rematch["participants"]))
                    self.assertTrue(all(p["cash"] == 1500 and not p["bankrupt"] for p in rematch["board_state"]["players"]))
if __name__ == "__main__":
    unittest.main()

"""Variants' boundaries and real rooms; only disposable databases."""

import asyncio
from collections import Counter
from copy import deepcopy
import json
from pathlib import Path
import random
import tempfile
import unittest
from unittest.mock import patch
from app import database, framework, invites, mcp_minimal
from app.games import GAMES
from app.games.four_color_cards.cards import DECK
from app.games.four_color_cards.jiuzhi import Jiuzhi, score
from app.games.four_color_cards.qunhu import Qunhu
from app.games.four_color_cards.qunhu_rules import waiting_codes
from tests.test_four_color_tenhu import players, copies


def physical(s):
    cards = list(s["wall"]) + list(s["discards"]) + sum(s["hands"].values(), [])
    cards += [t for groups in s["melds"].values() for g in groups for t in g["cards"]]
    cards += [
        t for groups in s.get("closed_pairs", {}).values() for g in groups for t in g
    ]
    cards += s.get("reserve", []) + s.get("flowers", [])
    if s["pending"]:
        cards.append(s["pending"]["card"])
    return cards


def custom(game, hands, closed=None, wall=None):
    s = game.initialize(players(len(hands)))
    s["hands"] = {f"p{i}": list(h) for i, h in enumerate(hands)}
    s.update(
        melds={p: [] for p in s["hands"]},
        pending=None,
        result=None,
        discards=[],
        wall=[],
    )
    game.setup(s)
    if closed:
        s["closed_pairs"].update(closed)
    rest = [t for t in DECK if t not in physical(s)]
    s["wall"] = rest if wall is None else wall
    s["discards"] = [t for t in rest if t not in s["wall"]]
    s["turn_player_id"] = "p0"
    s["flow"]["phase"] = "discard"
    assert Counter(physical(s)) == Counter(DECK)
    return s


def offer(g, s, t, source="p0", draw=True):
    for zone in [s["wall"], s["discards"]]:
        if t in zone:
            zone.remove(t)
    g.offer(s, t, source, draw)


def act(g, s, id):
    return g.apply_action(s, g._action(id), {"player_id": s["turn_player_id"]}).state


def ready(copy=1):
    return [
        f"{c}-{copy}"
        for c in [
            "R0",
            "R1",
            "R2",
            "Y3",
            "Y4",
            "Y5",
            "G0",
            "G1",
            "G2",
            "W3",
            "W4",
            "W5",
            "R6",
            "Y6",
            "W0",
        ]
    ]


class JiuzhiBoundaries(unittest.TestCase):
    def test_deal_sealed_pairs_and_opening_win(self):
        g = Jiuzhi(random.Random(2))
        s = g.initialize(players(4))
        self.assertEqual(len(s["reserve"]), 5)
        self.assertEqual(
            [len(g.owned(s, p)) for p in s["participant_order"]], [10, 9, 9, 9]
        )
        self.assertEqual(Counter(physical(s)), Counter(DECK))
        s = custom(
            g,
            [[], ["Y1-1"], ["Y2-1"], ["Y4-1"]],
            {"p0": [copies(c, 2) for c in ("R0", "G1", "W2", "Y3", "R6")]},
        )
        s["flow"]["phase"] = "opening_hu"
        s = act(g, s, "hu")
        self.assertEqual(s["result"]["flowers"], [])

    def test_hu_precedes_forced_pair_and_discard_only_next(self):
        g = Jiuzhi()
        closed = {"p2": [copies(c, 2) for c in ("R0", "G1", "W2", "Y3")]}
        s = custom(g, [["R6-1", "Y1-1"], ["Y2-1"], ["R6-2"], ["Y4-1"]], closed)
        offer(g, s, "R6-4")
        self.assertEqual(s["turn_player_id"], "p2")
        self.assertEqual([o["id"] for o in g._options(s, "p2")], ["hu"])
        before = deepcopy(s)
        with self.assertRaises(ValueError):
            g.apply_action(s, g._action("pair:R6-1"), {"player_id": "p0"})
        self.assertEqual(s, before)
        s = act(g, s, "hu")
        self.assertEqual(s["result"]["winner_player_id"], "p2")
        s = custom(g, [["Y5-1"], ["R6-1"], ["R6-2"], ["Y4-1"]], closed)
        offer(g, s, "R6-4", draw=False)
        self.assertEqual(s["turn_player_id"], "p1")
        self.assertEqual(g._options(s, "p2"), [])
        self.assertNotIn("pass", [o["id"] for o in g._options(s, "p1")])

    def test_last_wall_shared_wait_uses_drawer_priority(self):
        g = Jiuzhi()
        closed = {
            p: [[f"{c}-{i}" for i in indexes] for c in ("R0", "G1", "W2", "Y3")]
            for p, indexes in (("p0", (1, 2)), ("p1", (3, 4)))
        }
        s = custom(g, [["R6-1"], ["R6-2"], ["G5-1"], ["W5-1"]], closed, wall=["R6-4"])
        s["flow"]["phase"] = "draw"
        s = act(g, s, "draw")
        self.assertEqual(s["wall"], [])
        self.assertEqual(s["turn_player_id"], "p0")
        self.assertIsNone(s["result"])
        s = act(g, s, "hu")
        self.assertEqual(s["result"]["winner_player_id"], "p0")
        self.assertEqual(Counter(physical(s)), Counter(DECK))
        self.assertEqual(sum(s["result"]["point_deltas"].values()), 0)

    def test_broken_queue_precedes_minimum_color(self):
        g = Jiuzhi()
        s = custom(g, [["R1-1", "G2-1"], ["R1-2", "Y1-1", "Y2-1"], ["W2-1"], ["W3-1"]])
        offer(g, s, "R1-4")
        s = act(g, s, "pair:R1-1")
        self.assertEqual(s["broken"]["p1"], ["R1-2"])
        self.assertIn("R1-2", json.dumps(g.public_state(s, [])))
        s["turn_player_id"] = "p1"
        s["flow"]["phase"] = "discard"
        s["broken"]["p1"] = ["Y2-1", "R1-2"]
        self.assertEqual([o["card"] for o in g._options(s, "p1")], ["Y2-1"])
        s["broken"]["p1"] = []
        self.assertEqual([o["card"] for o in g._options(s, "p1")], ["R1-2"])
        s["broken"]["p1"] = ["R1-2"]
        offer(g, s, "R1-3", source="p1")
        self.assertFalse(any(o["kind"] == "pair" for o in g._options(s, "p1")))

    def test_scoring_kaijiao_and_bottom_weight_and_discard_payer(self):
        hand = copies("R1", 4) + copies("G1", 4) + copies("W1", 2)
        r = score(hand, "R1-4", False, [])
        self.assertEqual(r["heads"], {"颜色": 2, "无兵卒": 1, "无将帅": 1, "开交胡": 5})
        r = score(hand, "W1-2", True, ["W1-3", "Y5-1", "Y5-2", "Y5-3", "W1-4"])
        self.assertEqual(r["heads"]["开交对子"], 2)
        self.assertEqual(r["heads"]["抽五张"], 3)
        with self.assertRaises(ValueError):
            score(hand[:-1], "W1-2", False, [])
        g = Jiuzhi()
        s = custom(
            g,
            [["Y5-1"], ["R6-1"], ["Y2-1"], ["Y4-1"]],
            {"p1": [copies(c, 2) for c in ("R0", "G1", "W2", "Y3")]},
        )
        offer(g, s, "R6-4", draw=False)
        s = act(g, s, "hu")
        self.assertEqual([s["result"]["point_deltas"][p] for p in ("p2", "p3")], [0, 0])
        self.assertEqual(sum(s["result"]["point_deltas"].values()), 0)

    def test_empty_and_wrong_hu(self):
        g = Jiuzhi()
        s = custom(g, [["R1-1"], ["Y2-1"], ["G3-1"], ["W4-1"]], wall=["W5-1"])
        s["flow"]["phase"] = "draw"
        before = deepcopy(s)
        with self.assertRaises(ValueError):
            g.apply_action(s, g._action("hu"), {"player_id": "p0"})
        self.assertEqual(s, before)
        s = act(g, s, "draw")
        self.assertEqual(s["result"]["reason"], "wall_exhausted")
        self.assertEqual(Counter(physical(s)), Counter(DECK))


class QunhuBoundaries(unittest.TestCase):
    def setup_ready(self, g, wall=None):
        s = custom(g, [ready(), ready(2), ["Y1-4"]], wall=wall)
        for p in ("p0", "p1"):
            s["listening"][p] = {
                "exhibit": [s["hands"][p][0]],
                "waits": waiting_codes(s["hands"][p]),
            }
        return s

    def test_listen_and_fixed_hand(self):
        g = Qunhu()
        s = custom(g, [ready() + ["R4-4"], ["Y1-4"], ["W1-4"]])
        self.assertIn(
            "G6",
            next(o for o in g._options(s, "p0") if o["id"] == "listen:R4-4")["waits"],
        )
        s = act(g, s, "listen:R4-4")
        s = json.loads(json.dumps(s))
        self.assertEqual(s["listening"]["p0"]["exhibit"], ["G0-1"])
        self.assertNotIn("waits", json.dumps(g.public_state(s, [])))
        # Finish the preceding offer before testing an unrelated draw.
        if s["pending"]:
            s["discards"].append(s["pending"]["card"])
            s["pending"] = None
        before = list(s["hands"]["p0"])
        offer(g, s, "R4-3", source="p0")
        self.assertEqual(s["hands"]["p0"], before)
        self.assertFalse(any(o["kind"] == "take" for o in g._options(s, "p0")))

    def test_competing_hu_drawer_first_three_points(self):
        g = Qunhu()
        s = self.setup_ready(g)
        offer(g, s, "G6-4")
        self.assertEqual(s["turn_player_id"], "p0")
        before = deepcopy(s)
        with self.assertRaises(ValueError):
            g.apply_action(s, g._action("hu"), {"player_id": "p1"})
        self.assertEqual(s, before)
        s = act(g, s, "hu")
        self.assertEqual(s["result"]["point_deltas"], {"p0": 6, "p1": -3, "p2": -3})
        self.assertEqual(s["flowers"], [])
        self.assertEqual(Counter(physical(s)), Counter(DECK))

    def test_flowers_two_one_none_and_discard_only_next(self):
        for wall, points in [
            (["W0-4", "R1-4"], 2),
            (["W0-4"], 2),
            ([], 1),
            (["W1-4", "R4-4"], 1),
        ]:
            with self.subTest(wall=wall):
                g = Qunhu()
                s = self.setup_ready(g, wall)
                offer(g, s, "G6-4", draw=False)
                self.assertEqual(s["turn_player_id"], "p1")
                s = act(g, s, "hu")
                self.assertEqual(s["result"]["scoring"]["per_opponent"], points)
                self.assertEqual(len(s["flowers"]), len(wall))
                self.assertEqual(Counter(physical(s)), Counter(DECK))

    def test_unready_wrong_hu_empty_cycle_and_no_eye(self):
        g = Qunhu()
        s = custom(g, [ready(), ["Y1-4"], ["W1-4"]], wall=[])
        offer(g, s, "G6-4", source="p2", draw=False)
        self.assertEqual([o["id"] for o in g._options(s, "p0")], ["take", "pass"])
        before = deepcopy(s)
        with self.assertRaises(ValueError):
            g.apply_action(s, g._action("hu"), {"player_id": "p0"})
        self.assertEqual(s, before)
        s = act(g, s, "pass")
        self.assertEqual(s["result"]["reason"], "wall_exhausted")
        s = custom(g, [ready() + ["R4-4"], ["Y1-4"], ["W1-4"]])
        s["exchanges_since_draw"] = 111
        s = act(g, s, "discard:R4-4")
        self.assertEqual(s["result"]["reason"], "exchange_limit")
        self.assertEqual(Counter(physical(s)), Counter(DECK))
        noeye = [f"{c}1-{i}" for i in (1, 2, 3) for c in ("R", "Y", "G")] + [
            f"{c}4-{i}" for i in (1, 2) for c in ("R", "Y", "G")
        ]
        self.assertEqual(waiting_codes(noeye), [])


class VariantRounds(unittest.TestCase):
    def test_seeded_full_hands_and_npc_uses_only_own_cards(self):
        for cls in (Jiuzhi, Qunhu):
            reasons = Counter()
            for seed in range(12):
                g = cls(random.Random(seed))
                n = 4 if cls is Jiuzhi else 3 + seed % 3
                s = g.initialize(players(n))
                for step in range(900):
                    a = {"player_id": s["turn_player_id"]}
                    before = deepcopy(s)
                    move = g.choose_local_npc_action(s, a, [])
                    hidden = deepcopy(s)
                    hidden["wall"] = "unreadable"
                    for p in hidden["hands"]:
                        if p != a["player_id"]:
                            hidden["hands"][p] = "unreadable"
                    self.assertEqual(move, g.choose_local_npc_action(hidden, a, []))
                    after = g.apply_action(s, move, a).state
                    self.assertEqual(s, before)
                    s = json.loads(json.dumps(after))
                    self.assertEqual(Counter(physical(s)), Counter(DECK))
                    if s["result"]:
                        self.assertEqual(sum(s["result"]["point_deltas"].values()), 0)
                        reasons[s["result"]["reason"]] += 1
                        break
                else:
                    self.fail(f"{cls.__name__}/{seed} did not finish")
            self.assertGreater(reasons["hu"], 0, reasons)
            print("FOUR_COLOR_ROUNDS", cls.__name__, dict(reasons))


class VariantRooms(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="duel-four-variants-")
        self.addCleanup(self.tmp.cleanup)
        p = patch.object(database, "DB_PATH", Path(self.tmp.name) / "test.db")
        p.start()
        self.addCleanup(p.stop)
        database.init_db()
        p = patch.dict(
            GAMES, {c.game_type: c(random.Random(4)) for c in (Jiuzhi, Qunhu)}
        )
        p.start()
        self.addCleanup(p.stop)

    def test_invites_complete_mcp_rebuild_and_recovery(self):
        for cls in (Jiuzhi, Qunhu):
            g = GAMES[cls.game_type]
            room = invites.create_invite(
                g.game_type, "human", "p0", target_player_count=4
            )
            for i in range(1, 4):
                invites.join_invite(room["invite_code"], "human", f"p{i}")
            room = invites.start_invite(room["room_id"], "human", "p0")
            pub = {}
            priv = {}
            sizes = []
            initial = []
            for p in room["participants"]:
                pid = p["player_id"]
                r = mcp_minimal.response(room["room_id"], pid)
                self.assertTrue(r["bootstrap"])
                initial.append(
                    len(json.dumps(r, ensure_ascii=False, separators=(",", ":")))
                )
                pub[pid] = r["room"]["board_state"]
                priv[pid] = r["room"]["private_state"]
            with self.assertRaises(framework.DuelError):
                mcp_minimal.response(room["room_id"], "outsider", full=True)
            for step in range(900):
                pid = room["current_player_id"]
                a = next(p for p in room["participants"] if p["player_id"] == pid)
                move = g.choose_local_npc_action(
                    room["board_state"], a, room["participants"]
                )
                rev = room["revision"]
                room = framework.play_move(
                    room["room_id"], a["role"], pid, move, expected_revision=rev
                )
                with self.assertRaises(framework.DuelError):
                    framework.play_move(
                        room["room_id"], a["role"], pid, move, expected_revision=rev
                    )
                self.assertEqual(room["game_type"], g.game_type)
                for p in room["participants"]:
                    pid = p["player_id"]
                    r = mcp_minimal.response(room["room_id"], pid)
                    self.assertEqual(r["from_revision"], rev)
                    sizes.append(
                        len(json.dumps(r, ensure_ascii=False, separators=(",", ":")))
                    )
                    self.assertNotIn("rules_text", json.dumps(r))
                    self.assertNotIn("room", r)
                    for event in r.get("events", []):
                        if not isinstance(event, list) or len(event) < 3:
                            continue
                        for k, v in event[2].items():
                            if k == "discards+":
                                pub[pid]["discards"].extend(v)
                            elif k == "melds+":
                                for owner, groups in v.items():
                                    pub[pid]["melds"][owner].extend(groups)
                            else:
                                pub[pid][k] = v
                    d = r.get("private", {})
                    for c in d.get("-", []):
                        priv[pid]["hand"].remove(c)
                    priv[pid]["hand"].extend(d.get("+", []))
                    priv[pid].update(
                        {k: v for k, v in d.items() if k not in ("+", "-")}
                    )
                    self.assertEqual(pub[pid], g.public_state(room["board_state"], []))
                    self.assertEqual(
                        sorted(priv[pid]["hand"]),
                        sorted(room["board_state"]["hands"][pid]),
                    )
                    self.assertEqual(
                        priv[pid]["legal_actions"],
                        [o["id"] for o in g._options(room["board_state"], pid)],
                    )
                    self.assertEqual(
                        {
                            k: priv[pid][k]
                            for k in g.private_extra(room["board_state"], pid)
                        },
                        g.private_extra(room["board_state"], pid),
                    )
                    public_ids = set(json.dumps(pub[pid]).split('"'))
                    for other in room["board_state"]["hands"]:
                        if other == pid:
                            continue
                        hidden = room["board_state"]["hands"][other] + sum(
                            room["board_state"].get("closed_pairs", {}).get(other, []),
                            [],
                        )
                        for c in hidden:
                            if c not in public_ids:
                                self.assertNotIn('"' + c + '"', json.dumps(r))
                    repeat = mcp_minimal.response(room["room_id"], pid)
                    self.assertNotIn("events", repeat)
                    self.assertNotIn("private", repeat)
                    self.assertNotIn("bootstrap", repeat)
                if step == 8:
                    r = mcp_minimal.response(room["room_id"], "p0", full=True)
                    self.assertEqual(r["snapshot"]["board_state"], pub["p0"])
                    self.assertIn("双弈平台约定", r["snapshot"]["rules_text"])
                if room["status"] != "playing":
                    break
            else:
                self.fail("room did not finish")
            self.assertLess(sum(sizes) / len(sizes), min(initial) / 2)
            print(
                "FOUR_COLOR_WIRE",
                cls.__name__,
                {
                    "bootstrap": initial,
                    "delta_min": min(sizes),
                    "delta_max": max(sizes),
                    "delta_mean": round(sum(sizes) / len(sizes), 1),
                },
            )

    def test_http_mcp_invites_authority_mode_lock_and_persistent_cursor(self):
        import os
        import subprocess
        import sys

        async def scenario():
            import httpx
            from app import main

            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=main.app), base_url="http://test"
            ) as client:
                for cls in (Jiuzhi, Qunhu):

                    async def request(**body):
                        r = await client.post("/mcp/play", json=body)
                        self.assertEqual(r.status_code, 200, r.text)
                        return r.json()

                    created = await request(
                        action="invite",
                        player_id="a0",
                        game_type=cls.game_type,
                        target_player_count=4,
                    )
                    rid = created["room_id"]
                    for i in range(1, 4):
                        await request(
                            action="join",
                            player_id=f"a{i}",
                            invite_code=created["invite_code"],
                        )
                    await request(action="start", player_id="a0", room_id=rid)
                    room = framework.get_room(rid)
                    pid = room["current_player_id"]
                    g = GAMES[cls.game_type]
                    initial = await request(
                        action="state", player_id=pid, room_id=rid, full_state=True
                    )
                    self.assertIn(g.rules_version, json.dumps(initial))
                    move = g.choose_local_npc_action(
                        room["board_state"], {"player_id": pid}, room["participants"]
                    )
                    for bad in (
                        dict(move, rules="other"),
                        dict(move, game_type="four_color_tenhu"),
                    ):
                        r = await client.post(
                            "/mcp/play",
                            json=dict(
                                action="move",
                                player_id=pid,
                                room_id=rid,
                                revision=room["revision"],
                                move=bad,
                            ),
                        )
                        self.assertGreaterEqual(r.status_code, 400)
                    r = await client.post(
                        "/mcp/play",
                        json=dict(
                            action="state",
                            player_id="outsider",
                            room_id=rid,
                            full_state=True,
                        ),
                    )
                    self.assertGreaterEqual(r.status_code, 400)

                    async def move_request():
                        return await client.post(
                            "/mcp/play",
                            json=dict(
                                action="move",
                                player_id=pid,
                                room_id=rid,
                                revision=room["revision"],
                                move=move,
                            ),
                        )

                    responses = await asyncio.gather(move_request(), move_request())
                    self.assertEqual(
                        sorted(r.status_code for r in responses), [200, 409]
                    )
                    delta = next(r.json() for r in responses if r.status_code == 200)
                    self.assertEqual(delta["r"], room["revision"] + 1)
                    self.assertNotIn("room", delta)
                    code = (
                        "import json; from app import mcp_minimal; print(json.dumps(mcp_minimal.response("
                        + repr(rid)
                        + ","
                        + repr(pid)
                        + ")))"
                    )
                    # Separate interpreter shares only this temporary SQLite, not memory/cursor.
                    env = dict(
                        os.environ,
                        DUEL_DB_PATH=str(database.DB_PATH),
                        PYTHONDONTWRITEBYTECODE="1",
                    )
                    result = subprocess.run(
                        [sys.executable, "-c", code],
                        env=env,
                        capture_output=True,
                        text=True,
                        check=True,
                    )
                    repeated = json.loads(result.stdout)
                    self.assertNotIn("bootstrap", repeated)
                    self.assertNotIn("events", repeated)
                    recovered = await request(
                        action="state", player_id=pid, room_id=rid, full_state=True
                    )
                    self.assertEqual(recovered["snapshot"]["game"], cls.game_type)
                    human = framework.create_room(
                        cls.game_type,
                        "human_first",
                        "human",
                        "p0",
                        ordered_participants=players(4),
                    )
                    move = g.choose_local_npc_action(
                        human["board_state"], {"player_id": "p0"}, human["participants"]
                    )
                    denied = await client.post(
                        "/api/rooms/" + human["room_id"] + "/move",
                        headers={"X-Duel-Human-Player": "outsider"},
                        json={
                            "player_id": "p0",
                            "move": move,
                            "revision": human["revision"],
                        },
                    )
                    self.assertEqual(denied.status_code, 403)
                    self.assertEqual(
                        framework.get_room(human["room_id"])["revision"],
                        human["revision"],
                    )
                    accepted = await client.post(
                        "/api/rooms/" + human["room_id"] + "/move",
                        headers={"X-Duel-Human-Player": "p0"},
                        json={
                            "player_id": "p0",
                            "move": move,
                            "revision": human["revision"],
                        },
                    )
                    self.assertEqual(accepted.status_code, 200, accepted.text)

        asyncio.run(scenario())

    def test_normal_room_system_npc_controller_and_stake(self):
        from app.npc_controller import run_current_npc_turn

        for cls in (Jiuzhi, Qunhu):
            ps = players(4)
            for p in ps[1:]:
                p.update(participant_kind="system_npc", npc_persona_id="test-npc")
            g = GAMES[cls.game_type]
            with self.assertRaises(framework.DuelError):
                framework.create_room(
                    g.game_type,
                    "human_first",
                    "human",
                    "p0",
                    ordered_participants=ps,
                    stake=1,
                )
            with self.assertRaises(framework.DuelError):
                invites.create_invite(
                    g.game_type, "human", "p0", target_player_count=4, stake=1
                )
            room = framework.create_room(
                g.game_type, "human_first", "human", "p0", ordered_participants=ps
            )
            with self.assertRaisesRegex(framework.DuelError, "revision"):
                framework.play_move(
                    room["room_id"],
                    "human",
                    "p0",
                    g.choose_local_npc_action(room["board_state"], ps[0], ps),
                )
            with patch("app.npc_controller._finish_npc_action"):
                for _ in range(900):
                    a = next(
                        p
                        for p in room["participants"]
                        if p["player_id"] == room["current_player_id"]
                    )
                    if a["participant_kind"] == "system_npc":
                        asyncio.run(run_current_npc_turn(room["room_id"]))
                        room = framework.get_room(room["room_id"])
                    else:
                        room = framework.play_move(
                            room["room_id"],
                            "human",
                            a["player_id"],
                            g.choose_local_npc_action(
                                room["board_state"], a, room["participants"]
                            ),
                            expected_revision=room["revision"],
                        )
                    if room["status"] != "playing":
                        break
                else:
                    self.fail("NPC hand did not finish")
            self.assertEqual(Counter(physical(room["board_state"])), Counter(DECK))


if __name__ == "__main__":
    unittest.main()

"""Only isolated state and temporary SQLite; no live service or database access."""

import asyncio
from collections import Counter
from copy import deepcopy
import json
import os
from pathlib import Path
import random
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from app import database, framework, invites
from app.games import GAMES, game_catalog
from app.games.four_color_cards.cards import DECK, CODES, code, label
from app.games.four_color_cards.tenhu_rules import (
    best_partition, claims, meld_info, response_windows, score_hand,
)
from app.games.four_color_cards.tenhu import BgaTenHu


def copies(value, n, start=1):
    return [f"{value}-{i}" for i in range(start, start + n)]


def players(n=4):
    return [{"player_id": f"p{i}", "seat_index": i, "token": f"seat-{i}",
             "role": "human" if i == 0 else "ai", "active": True,
             "display_name": f"玩家{i}", "join_status": "joined",
             "participant_kind": "human" if i == 0 else "bound_machine"} for i in range(n)]


def fixture(hands, *, wall=None):
    game = BgaTenHu(random.Random(1))
    state = game.initialize(players(len(hands)))
    state["hands"] = {f"p{i}": list(hand) for i, hand in enumerate(hands)}
    used = sum(state["hands"].values(), [])
    assert len(set(used)) == len(used), "bad fixture duplicates physical card"
    remaining = [t for t in DECK if t not in used]
    state["wall"] = remaining if wall is None else list(wall)
    state["discards"] = [t for t in remaining if t not in state["wall"]]
    state["first_discard"] = state["discards"][0] if state["discards"] else None
    return game, state


def offer(game, state, card, source="p0", from_deck=False):
    for zone in [state["wall"], state["discards"], *state["hands"].values()]:
        if card in zone:
            zone.remove(card)
    if not from_deck and state["first_discard"] is None:
        state["first_discard"] = card
    game._offer(state, card, source, from_deck)


def options(game, state):
    return game._options(state, state["turn_player_id"])


def act(game, state, option):
    return game.apply_action(state, game._action(option["id"]),
                             {"player_id": state["turn_player_id"]}).state


def physical_cards(state):
    result = list(state["wall"]) + list(state["discards"]) + sum(state["hands"].values(), [])
    result += [t for groups in state["melds"].values() for group in groups for t in group["cards"]]
    if state["pending"]:
        result.append(state["pending"]["card"])
    if state["flower"]:
        result.append(state["flower"])
    return result


class TenHuCombinations(unittest.TestCase):
    def test_physical_deck_and_labels(self):
        self.assertEqual(len(DECK), 112)
        self.assertEqual(len(set(DECK)), 112)
        self.assertEqual(Counter(map(code, DECK)), Counter({c: 4 for c in CODES}))
        self.assertEqual(label("R0-1"), "红帥")
        self.assertEqual(label("G6-4"), "绿卒")
        for bad in (True, 1, None, {}, "R0-0", "R0-5", "Z0-1", "R7-1"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                code(bad)

    def test_every_color_every_rank_melds_and_points(self):
        for value in CODES:
            rank = int(value[1])
            for size in (1, 2, 3, 4):
                cards = copies(value, size)
                valid = size == 4 or (size == 1 and rank == 0) or (
                    size == 2 and rank not in (0, 6)) or (size == 3 and rank != 0)
                with self.subTest(value=value, size=size):
                    if not valid:
                        with self.assertRaises(ValueError):
                            meld_info(cards)
                        continue
                    expected = {1: (1, 1), 2: (0, 0), 3: (3, 1),
                                4: (6, 8) if rank == 0 else (8, 6)}[size]
                    self.assertEqual(meld_info(cards)["hu"], expected[0])
                    self.assertEqual(meld_info(cards, exposed=True)["hu"], expected[1])

    def test_sequences_and_soldiers_do_not_import_other_variants(self):
        cases = [(["R0-1", "R1-1", "R2-1"], 2),
                 (["W3-1", "W4-1", "W5-1"], 1),
                 (["R6-1", "Y6-1", "G6-1"], 3),
                 (["R6-1", "Y6-1", "G6-1", "W6-1"], 5)]
        for cards, points in cases:
            for exposed in (False, True):
                self.assertEqual(meld_info(cards, exposed=exposed)["hu"], points)
        for cards in (["R0-1", "Y1-1", "R2-1"], ["R1-1", "Y1-1", "G1-1"],
                      ["R6-1", "R6-2", "Y6-1"], ["R2-1", "R3-1", "R4-1"],
                      ["R1-1", "R1-1"], []):
            with self.subTest(cards=cards), self.assertRaises(ValueError):
                meld_info(cards)

    def test_exact_cover_prefers_pongs_over_overlapping_sequences(self):
        hand = copies("R3", 3) + copies("R4", 3) + copies("R5", 3)
        result = best_partition(hand)
        self.assertEqual(result["hu"], 9)
        self.assertEqual([g["kind"] for g in result["groups"]], ["pong"] * 3)
        self.assertEqual(Counter(t for g in result["groups"] for t in g["cards"]), Counter(hand))
        self.assertEqual(best_partition(copies("R0", 3))["hu"], 3)
        self.assertEqual(best_partition(copies("R0", 4))["hu"], 6)
        self.assertIsNone(best_partition(copies("R6", 2)))
        self.assertIsNone(best_partition(["R3-1", "R4-1"]))
        self.assertEqual(best_partition([]), {"hu": 0, "groups": []})

    def test_partition_does_not_mutate_input_or_reuse_entities(self):
        hand = copies("R3", 4) + copies("R4", 2) + copies("R5", 2)
        before = list(hand)
        result = best_partition(hand)
        self.assertEqual(result["hu"], 8)
        self.assertEqual(hand, before)
        self.assertEqual(Counter(t for m in result["groups"] for t in m["cards"]), Counter(hand))
        with self.assertRaises(ValueError):
            best_partition(["R1-1", "R1-1"])

    def test_claims_require_incoming_and_never_synthesize_a_fifth_copy(self):
        result = claims(copies("R1", 3) + ["R0-1", "R2-1"], "R1-4")
        self.assertEqual({c["meld"]["kind"] for c in result}, {"pair", "pong", "kong", "command"})
        for candidate in result:
            self.assertEqual(candidate["meld"]["cards"].count("R1-4"), 1)
            self.assertEqual(len(set(candidate["meld"]["cards"])), len(candidate["meld"]["cards"]))
        with self.assertRaises(ValueError):
            claims(["R1-1"], "R1-1")

    def test_nine_ten_hu_and_flower_threshold(self):
        nine = copies("R1", 3) + copies("Y1", 3) + copies("G1", 3)
        failed = score_hand(nine, [], "R1-4")
        self.assertEqual((failed["base_hu"], failed["flower_hu"]), (9, 3))
        self.assertFalse(failed["qualified"])
        self.assertEqual(failed["per_opponent"], -11)
        passed = score_hand(nine + ["W0-1"], [], "R1-4")
        self.assertTrue(passed["qualified"])
        self.assertEqual(passed["per_opponent"], 4)
        self.assertEqual(score_hand(nine + ["W0-1"], [], "W5-1")["per_opponent"], 1)

    def test_exposed_last_claim_is_not_scored_as_concealed(self):
        remaining = copies("R1", 3) + copies("Y1", 3) + ["G0-1"]
        last = copies("W1", 3)
        self.assertEqual(score_hand(remaining, [last], "R1-4")["base_hu"], 8)
        self.assertEqual(score_hand(remaining + last, [], "R1-4")["base_hu"], 10)
        with self.assertRaisesRegex(ValueError, "重复"):
            score_hand(remaining, [copies("R1", 3)], "W1-1")

    def test_first_discard_flower_excludes_own_entity_platform_convention(self):
        self.assertEqual(score_hand(copies("R1", 3), [], "R1-1")["flower_hu"], 2)
        self.assertEqual(score_hand(copies("R1", 4), [], "R1-1")["flower_hu"], 3)

    def test_response_priority_and_seat_wrap(self):
        order = ["a", "b", "c", "d"]
        drawn = response_windows(order, "c", True)
        self.assertEqual(drawn[:4], [(p, "hu") for p in ("c", "d", "a", "b")])
        self.assertEqual(drawn[4:8], [(p, "pong") for p in ("c", "d", "a", "b")])
        self.assertEqual(drawn[8:], [("c", "chi"), ("d", "chi")])
        discarded = response_windows(order, "c", False)
        self.assertEqual(discarded[:3], [(p, "hu") for p in ("d", "a", "b")])
        self.assertEqual(discarded[-1], ("d", "chi"))
        self.assertNotIn("c", [p for p, tier in discarded])


class TenHuRound(unittest.TestCase):
    def test_packet_deal_2_3_4_and_nonfirst_seat_dealer(self):
        for n in (2, 3, 4):
            game = BgaTenHu(random.Random(2))
            state = game.initialize_for_first_player(players(n), "p1")
            self.assertEqual(state["turn_player_id"], "p1")
            self.assertEqual(len(state["hands"]["p1"]), 21)
            self.assertEqual([len(state["hands"][p]) for p in state["participant_order"][1:]], [20] * (n - 1))
            self.assertEqual(len(state["wall"]), 111 - n * 20)
            self.assertEqual(Counter(physical_cards(state)), Counter(DECK))
        waiting = BgaTenHu().initialize(players(1))
        self.assertEqual(waiting["flow"]["phase"], "waiting")
        self.assertEqual(waiting["wall"], [])
        with self.assertRaises(ValueError):
            BgaTenHu().initialize(players(5))

    def test_wrong_player_malformed_and_general_discard_are_pure(self):
        game, state = fixture([["R0-1", "R1-1"], ["G5-1"]])
        original = deepcopy(state)
        for move, pid in [({"action": "act", "action_id": "discard:R0-1"}, "p0"),
                          ({"action": "act", "action_id": "discard:R1-1"}, "p1"),
                          ({"action": "act", "action_id": "discard:R1-1", "score": 100}, "p0"),
                          ({"action": "act", "action_id": []}, "p0")]:
            with self.subTest(move=move), self.assertRaises(ValueError):
                game.apply_action(state, move, {"player_id": pid})
            self.assertEqual(state, original)

    def test_pong_beats_downstream_chi_and_skips_seats(self):
        game, state = fixture([["Y5-1"], ["R0-1", "R2-1", "W4-1"],
                               copies("R1", 2) + ["Y4-1"], ["W5-1"]])
        offer(game, state, "R1-4")
        self.assertEqual(state["turn_player_id"], "p2")
        self.assertEqual({o["kind"] for o in options(game, state)}, {"pong", "pass"})
        state = act(game, state, next(o for o in options(game, state) if o["kind"] == "pong"))
        self.assertEqual(state["turn_player_id"], "p2")
        self.assertEqual(state["flow"]["phase"], "discard")
        self.assertEqual(state["melds"]["p2"][0]["hu"], 1)

    def test_hu_beats_kong_and_nearest_hu_wins(self):
        game, state = fixture([["Y5-1"], ["R0-1", "R2-1"],
                               copies("R1", 3) + ["Y4-1"], ["R0-2", "R2-2"]])
        offer(game, state, "R1-4")
        self.assertEqual(state["turn_player_id"], "p1")
        state = act(game, state, next(o for o in options(game, state) if o["kind"] == "hu"))
        self.assertEqual(state["result"]["declarer_player_id"], "p1")
        self.assertEqual(state["result"]["reason"], "under_ten")
        self.assertEqual(state["melds"]["p2"], [])
        self.assertEqual(sum(state["result"]["point_deltas"].values()), 0)

    def test_declining_hu_preserves_later_pong_opportunity(self):
        game, state = fixture([["Y5-1"], copies("R1", 2) + ["G0-1"]])
        offer(game, state, "R1-4")
        self.assertEqual(options(game, state)[0]["kind"], "hu")
        state = act(game, state, next(o for o in options(game, state) if o["kind"] == "pass"))
        self.assertIn("pong", [o["kind"] for o in options(game, state)])

    def test_discard_chi_only_next_but_draw_chi_drawer_and_next(self):
        game, state = fixture([["R0-1", "R2-1", "Y5-1"], ["W4-1"],
                               ["R0-2", "R2-2", "Y4-1"]])
        offer(game, state, "R1-4", "p0", False)
        self.assertEqual(state["flow"]["phase"], "draw")
        self.assertEqual(state["turn_player_id"], "p1")
        offer(game, state, "R1-4", "p0", True)
        self.assertEqual(state["turn_player_id"], "p0")
        self.assertIn("chi", [o["kind"] for o in options(game, state)])

    def test_drawn_general_mandatory_chi_but_kong_can_intercept(self):
        game, state = fixture([["Y5-1"], ["W4-1"], copies("R0", 3) + ["Y4-1"]])
        offer(game, state, "R0-4", "p0", True)
        self.assertEqual(state["turn_player_id"], "p2")
        self.assertIn("kong", [o["kind"] for o in options(game, state)])
        state = act(game, state, next(o for o in options(game, state) if o["kind"] == "pass"))
        self.assertEqual(state["turn_player_id"], "p0")
        self.assertEqual([o["kind"] for o in options(game, state)], ["chi"])
        state = act(game, state, options(game, state)[0])
        self.assertEqual(state["flow"]["phase"], "discard")
        self.assertEqual(state["melds"]["p0"][0]["kind"], "general")

    def test_last_wall_card_still_claimable_before_draw(self):
        game, state = fixture([["Y5-1"], ["R0-1", "R2-1"]], wall=["R1-4"])
        state["flow"]["phase"] = "draw"
        state = act(game, state, options(game, state)[0])
        self.assertEqual(state["wall"], [])
        self.assertEqual(state["flow"]["phase"], "response")
        self.assertIn("hu", [o["kind"] for o in options(game, state)])
        state = act(game, state, next(o for o in options(game, state) if o["kind"] == "hu"))
        self.assertEqual(state["result"]["reason"], "under_ten")
        self.assertTrue(state["result"]["flower_from_first_discard"])

    def test_first_discard_flower_finishes_without_deadlock(self):
        game, state = fixture([["Y5-1"], ["R0-1", "R2-1"]], wall=[])
        state["first_discard"] = "R0-1"
        offer(game, state, "R1-4")
        before = deepcopy(state)
        winning = next(o for o in options(game, state) if o["kind"] == "hu")
        finished = act(game, state, winning)
        self.assertEqual(state, before)
        self.assertIsNotNone(finished["result"])
        self.assertEqual(sum(finished["result"]["point_deltas"].values()), 0)
        self.assertEqual(sorted(physical_cards(finished)), sorted(physical_cards(before)))

    def test_empty_wall_first_discard_inside_winners_exposed_kong(self):
        hand = copies('R1', 3) + copies('Y1', 3) + copies('G1', 3) + ['W0-1']
        game, state = fixture([hand, ['Y5-1']], wall=[])
        kong = copies('R3', 4)
        for card in kong: state['discards'].remove(card)
        state['melds']['p0'] = [meld_info(kong, exposed=True)]
        state['first_discard'] = kong[0]
        offer(game, state, 'Y0-4', from_deck=True)
        before = deepcopy(state)
        finished = act(game, state, next(o for o in options(game, state) if o['kind'] == 'hu'))
        self.assertEqual(finished['result']['reason'], 'hu')
        self.assertEqual(finished['result']['scoring']['flower_hu'], 3)
        self.assertEqual(finished['result']['flower_card'], kong[0])
        self.assertEqual(sum(finished['result']['point_deltas'].values()), 0)
        self.assertEqual(sorted(physical_cards(finished)), sorted(physical_cards(before)))
        self.assertEqual(state, before)

    def test_drawer_hu_priority_and_terminal_has_no_legal_actions(self):
        game, state = fixture([["R0-1", "R2-1"], ["R0-2", "R2-2"]])
        offer(game, state, "R1-4", from_deck=True)
        self.assertEqual(state["turn_player_id"], "p0")
        state = act(game, state, next(o for o in options(game, state) if o["kind"] == "hu"))
        self.assertEqual(state["result"]["declarer_player_id"], "p0")
        for p in players(2):
            self.assertEqual(game.private_state(state, p, players(2))["legal_actions"], [])
        public = json.dumps(game.terminal_public_state(state, players(2)))
        self.assertIn("R0-1", public)
        self.assertNotIn("R0-2", public)
        self.assertNotIn("R2-2", public)

    def test_event_redaction_never_discloses_action_ids(self):
        game = BgaTenHu()
        event = {"event_type": "move", "move": {"action": "act", "action_id": "hu:secret"},
                 "payload": {"action_id": "secret", "public": "retained"}}
        before = deepcopy(event)
        projected = game.project_event(event, {}, [])
        self.assertNotIn("secret", json.dumps(projected))
        self.assertEqual(projected["payload"]["public"], "retained")
        self.assertEqual(event, before)

    def test_wall_exhaustion_without_claim_draws_and_conserves(self):
        game, state = fixture([["Y5-1"], ["W4-1"]], wall=["R1-4"])
        state["flow"]["phase"] = "draw"
        state = act(game, state, options(game, state)[0])
        self.assertEqual(state["result"]["reason"], "wall_exhausted")
        self.assertEqual(state["result"]["point_deltas"], {"p0": 0, "p1": 0})
        self.assertEqual(Counter(physical_cards(state)), Counter(DECK))

    def test_no_discard_after_claim_auto_finishes_but_last_discard_does_not(self):
        game, state = fixture([["Y5-1"], ["R1-1"]])
        offer(game, state, "R1-4")
        state = act(game, state, next(o for o in options(game, state) if o["kind"] == "pass"))
        state = act(game, state, next(o for o in options(game, state) if o["kind"] == "chi"))
        self.assertEqual(state["result"]["reason"], "under_ten")
        game, state = fixture([["Y5-1"], ["W4-1"]])
        state = act(game, state, options(game, state)[0])
        self.assertIsNone(state["result"])
        self.assertEqual(state["hands"]["p0"], [])

    def test_private_projection_and_npc_independent_of_other_hands_and_wall(self):
        game = BgaTenHu(random.Random(4))
        state = game.initialize(players())
        public = game.public_state(state, players())
        self.assertNotIn("wall", public)
        self.assertNotIn("hands", public)
        self.assertNotIn("pending", public)
        private = game.private_state(state, players()[0], players())
        visible = json.dumps([public, private])
        for pid in ("p1", "p2", "p3"):
            for card in state["hands"][pid]:
                self.assertNotIn('"' + card + '"', visible)
        expected = game.choose_local_npc_action(state, players()[0], players())
        changed = deepcopy(state)
        for pid in ("p1", "p2", "p3"):
            changed["hands"][pid] = ["deliberately-invalid-secret"] * len(changed["hands"][pid])
        changed["wall"] = ["unreadable-secret"] * len(changed["wall"])
        self.assertEqual(game.choose_local_npc_action(changed, players()[0], players()), expected)
        self.assertEqual(game.private_state(state, {"player_id": "outsider"}, players()), {})

    def test_full_local_npc_rounds_conservation_legality_json_reconnect(self):
        reasons = Counter()
        for count in (2, 3, 4):
            for seed in range(8):
                game = BgaTenHu(random.Random(seed))
                state = game.initialize(players(count))
                for step in range(700):
                    actor = {"player_id": state["turn_player_id"]}
                    move = game.choose_local_npc_action(state, actor, players(count))
                    self.assertIsNotNone(move, (count, seed, step, state["flow"]))
                    before = deepcopy(state)
                    restored = json.loads(json.dumps(state))
                    applied = game.apply_action(restored, move, actor)
                    state = applied.state
                    self.assertEqual(restored, before)
                    self.assertEqual(Counter(physical_cards(state)), Counter(DECK))
                    self.assertNotIn("hands", applied.public_event["four_color_state"])
                    if state["result"]:
                        reasons[state["result"]["reason"]] += 1
                        self.assertEqual(sum(state["result"]["point_deltas"].values()), 0)
                        break
                else:
                    self.fail(f"round did not finish: {count=}, {seed=}")
        self.assertEqual(sum(reasons.values()), 24)
        self.assertGreater(reasons["hu"], 0, reasons)
        self.assertGreater(reasons["wall_exhausted"], 0, reasons)


class TenHuIsolatedRooms(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="duel-four-color-")
        self.addCleanup(self.tmp.cleanup)
        db_patch = patch.object(database, "DB_PATH", Path(self.tmp.name) / "test.db")
        db_patch.start()
        self.addCleanup(db_patch.stop)
        database.init_db()
        self.game = BgaTenHu(random.Random(9))
        registry = patch.dict(GAMES, {self.game.game_type: self.game})
        registry.start()
        self.addCleanup(registry.stop)

    def test_mcp_incremental_reconstructs_entire_hand_for_every_seat(self):
        from app import mcp_minimal as wire
        room = framework.create_room(self.game.game_type, 'human_first', 'human', 'p0',
                                     ordered_participants=players(4))
        public, private, sizes = {}, {}, {'bootstrap': [], 'delta': [], 'resync': []}
        for p in room['participants']:
            pid = p['player_id']
            reply = wire.response(room['room_id'], pid, consume=True)
            self.assertTrue(reply['bootstrap'])
            self.assertNotIn('move_format', reply['room'])  # wire IDs are explained once in protocol_guide
            self.assertIn('exact ID', reply['protocol_guide'])
            sizes['bootstrap'].append(len(json.dumps(reply, ensure_ascii=False, separators=(',', ':'))))
            public[pid] = reply['room']['board_state']
            private[pid] = reply['room']['private_state']
        for step in range(700):
            actor = room['current_player_id']
            move = self.game.choose_local_npc_action(room['board_state'], {'player_id': actor}, room['participants'])
            role = next(p['role'] for p in room['participants'] if p['player_id'] == actor)
            room = framework.play_move(room['room_id'], role, actor, move, expected_revision=room['revision'])
            for p in room['participants']:
                pid = p['player_id']
                reply = wire.response(room['room_id'], pid, consume=True)
                sizes['delta'].append(len(json.dumps(reply, ensure_ascii=False, separators=(',', ':'))))
                self.assertNotIn('room', reply)
                self.assertNotIn('snapshot', reply)
                self.assertNotIn('rules_text', json.dumps(reply))
                for event in reply.get('events', []):
                    if not isinstance(event, list) or len(event) < 3:
                        continue
                    for k, v in event[2].items():
                        if k == 'discards+': public[pid]['discards'].extend(v)
                        elif k == 'melds+':
                            for owner, groups in v.items(): public[pid]['melds'][owner].extend(groups)
                        else: public[pid][k] = v
                patch_private = reply.get('private', {})
                for card in patch_private.get('-', []): private[pid]['hand'].remove(card)
                private[pid]['hand'].extend(patch_private.get('+', []))
                if 'legal_actions' in patch_private:
                    private[pid]['legal_actions'] = patch_private['legal_actions']
                self.assertEqual(public[pid], self.game.public_state(room['board_state'], []))
                self.assertEqual(sorted(private[pid]['hand']), sorted(room['board_state']['hands'][pid]))
                self.assertEqual(private[pid]['legal_actions'], [o['id'] for o in self.game._options(room['board_state'], pid)])
                public_ids = set(json.dumps(public[pid]).split('"'))
                for other, hand in room['board_state']['hands'].items():
                    if other == pid: continue
                    for card in hand:
                        if card not in public_ids: self.assertNotIn('"'+card+'"', json.dumps(reply))
                repeated = wire.response(room['room_id'], pid, consume=True)
                self.assertNotIn('events', repeated)
                self.assertNotIn('private', repeated)
                self.assertNotIn('bootstrap', repeated)
            if step == 12:
                restored = wire.response(room['room_id'], 'p0', full=True)
                self.assertTrue(restored['full_state'])
                self.assertIn('rules_text', restored['snapshot'])
                self.assertEqual(restored['snapshot']['board_state'], public['p0'])
                sizes['resync'].append(len(json.dumps(restored, ensure_ascii=False, separators=(',', ':'))))
            if room['status'] != 'playing': break
        else: self.fail('MCP hand did not finish')
        self.assertLess(sum(sizes['delta']) / len(sizes['delta']), min(sizes['bootstrap']) / 3)
        print('FOUR_COLOR_RESPONSE_CHARACTERS', json.dumps({k: {'n':len(v), 'min':min(v), 'max':max(v),
              'mean':round(sum(v)/len(v), 1)} for k, v in sizes.items()}))

    def test_required_revision_concurrent_retry_and_mcp_permissions(self):
        from concurrent.futures import ThreadPoolExecutor
        from app import mcp_minimal as wire
        room = framework.create_room(self.game.game_type, 'human_first', 'human', 'p0',
                                     ordered_participants=players(2))
        move = self.game.choose_local_npc_action(room['board_state'], {'player_id':'p0'}, room['participants'])
        with self.assertRaisesRegex(framework.DuelError, 'revision'):
            framework.play_move(room['room_id'], 'human', 'p0', move)
        with self.assertRaises(framework.DuelError): wire.response(room['room_id'], 'outsider', full=True)
        def submit():
            try:
                return framework.play_move(room['room_id'], 'human', 'p0', move,
                                           expected_revision=room['revision'])['revision']
            except framework.DuelError as exc: return exc.status_code
        with ThreadPoolExecutor(max_workers=2) as pool: results = list(pool.map(lambda _: submit(), range(2)))
        self.assertCountEqual(results, [room['revision'] + 1, 409])
        self.assertEqual(framework.get_room(room['room_id'])['revision'], room['revision'] + 1)

    def test_mcp_lost_response_detectable_and_cursor_survives_process_restart(self):
        from app import mcp_minimal as wire
        room = framework.create_room(self.game.game_type, 'human_first', 'human', 'p0',
                                     ordered_participants=players(2))
        first = wire.response(room['room_id'], 'p0')
        retained = first['r']
        for step in range(2):
            pid = room['current_player_id']
            actor = next(p for p in room['participants'] if p['player_id'] == pid)
            move = self.game.choose_local_npc_action(room['board_state'], actor, room['participants'])
            room = framework.play_move(room['room_id'], actor['role'], pid, move, expected_revision=room['revision'])
            reply = wire.response(room['room_id'], 'p0')
            if step == 0: self.assertEqual(reply['from_revision'], retained)  # response lost in transport
        self.assertNotEqual(reply['from_revision'], retained)
        recovered = wire.response(room['room_id'], 'p0', full=True)
        self.assertEqual(recovered['snapshot']['board_state'], self.game.public_state(room['board_state'], []))
        self.assertEqual(framework.get_room(room['room_id'])['revision'], room['revision'])
        script = '''import json,sys
from app.games import GAMES
from app.games.four_color_cards.tenhu import BgaTenHu
from app import mcp_minimal
GAMES['four_color_tenhu']=BgaTenHu()
print(json.dumps(mcp_minimal.response(sys.argv[1], 'p0')))
'''
        result = subprocess.run([sys.executable, '-c', script, room['room_id']],
                                env={**os.environ, 'DUEL_DB_PATH':str(database.DB_PATH), 'PYTHONDONTWRITEBYTECODE':'1'},
                                capture_output=True, text=True, check=True, timeout=20)
        restarted = json.loads(result.stdout)
        self.assertEqual(restarted['from_revision'], recovered['r'])
        self.assertNotIn('bootstrap', restarted)
        self.assertNotIn('events', restarted)
        self.assertNotIn('private', restarted)

    def test_temporary_normal_room_full_round_revision_privacy_and_wallet_policy(self):
        with self.assertRaises(framework.DuelError):
            framework.create_room(self.game.game_type, "human_first", "human", "p0",
                                  ordered_participants=players(2), stake=1)
        room = framework.create_room(self.game.game_type, "human_first", "human", "p0",
                                     ordered_participants=players(2), first_player_id="p0")
        for step in range(700):
            actor = next(p for p in room["participants"] if p["player_id"] == room["current_player_id"])
            view = framework.project_room_for_viewer(room, actor["player_id"])
            for other, hand in room["board_state"]["hands"].items():
                if other != actor["player_id"]:
                    for card in hand:
                        self.assertNotIn('"' + card + '"', json.dumps(view))
            move = self.game.choose_local_npc_action(room["board_state"], actor, room["participants"])
            old_revision = room["revision"]
            room = framework.play_move(room["room_id"], actor["role"], actor["player_id"], move,
                                       expected_revision=old_revision)
            if step == 0:
                with self.assertRaises(framework.DuelError):
                    framework.play_move(room["room_id"], actor["role"], actor["player_id"], move,
                                        expected_revision=old_revision)
            self.assertEqual(framework.get_room(room["room_id"])["board_state"], room["board_state"])
            if room["status"] == "finished":
                break
        else:
            self.fail("framework round never finished")
        self.assertEqual(room["stake"], 0)
        self.assertEqual(sum(room["board_state"]["result"]["point_deltas"].values()), 0)

    def test_invites_wait_join_start_2_3_4(self):
        for count in (2, 3, 4):
            owner = f"owner-{count}"
            room = invites.create_invite(self.game.game_type, "human", owner, target_player_count=count)
            self.assertEqual(room["board_state"], {})
            for i in range(count - 1):
                invites.join_invite(room["invite_code"], "human", f"guest-{count}-{i}")
            room = invites.start_invite(room["room_id"], "human", owner)
            state = room["board_state"]
            self.assertEqual(sorted(map(len, state["hands"].values())), [20] * (count - 1) + [21])
            actor = next(p for p in room["participants"] if p["player_id"] == room["current_player_id"])
            move = self.game.choose_local_npc_action(state, actor, room["participants"])
            with self.assertRaisesRegex(framework.DuelError, "revision"):
                framework.play_move(room["room_id"], actor["role"], actor["player_id"], move)
            room = framework.play_move(room["room_id"], actor["role"], actor["player_id"], move,
                                       expected_revision=room["revision"])
            self.assertEqual(Counter(physical_cards(room["board_state"])), Counter(DECK))

    def test_cross_process_reconnect_does_not_redeal(self):
        room = framework.create_room(self.game.game_type, "human_first", "human", "p0",
                                     ordered_participants=players(2))
        for _ in range(100):
            if room["board_state"]["flow"]["phase"] == "response":
                break
            actor = next(p for p in room["participants"] if p["player_id"] == room["current_player_id"])
            move = self.game.choose_local_npc_action(room["board_state"], actor, room["participants"])
            room = framework.play_move(room["room_id"], actor["role"], actor["player_id"], move,
                                       expected_revision=room["revision"])
        self.assertEqual(room["board_state"]["flow"]["phase"], "response")
        script = """
import json, sys
from app.games import GAMES
from app.games.four_color_cards.tenhu import BgaTenHu
from app import framework
GAMES[BgaTenHu.game_type] = BgaTenHu()
print(json.dumps(framework.get_room(sys.argv[1])['board_state']))
"""
        result = subprocess.run([sys.executable, "-c", script, room["room_id"]],
                                env={**os.environ, "DUEL_DB_PATH": str(database.DB_PATH)},
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), room["board_state"])

    def test_departure_ends_partial_hand_without_recycling_or_scoring(self):
        for count in (2, 3, 4):
            ps = players(count)
            for p in ps:
                p["player_id"] += f"-leave-{count}"
            room = framework.create_room(self.game.game_type, "human_first", "human", ps[0]["player_id"],
                                         ordered_participants=ps)
            before = deepcopy(room["board_state"])
            room = framework.resign(room["room_id"], "human", ps[0]["player_id"])
            self.assertEqual(room["status"], "finished")
            self.assertEqual(room["board_state"]["result"]["reason"], "hand_cancelled_by_departure")
            self.assertEqual(set(room["board_state"]["result"]["point_deltas"].values()), {0})
            self.assertEqual(room["board_state"]["hands"], before["hands"])
            self.assertEqual(room["board_state"]["wall"], before["wall"])

    def test_real_npc_controller_mixed_room_completes_without_model_calls(self):
        from app.npc_controller import run_current_npc_turn
        ps = players(4)
        for p in ps[2:]:
            p.update(participant_kind="system_npc", npc_persona_id="test-npc")
        room = framework.create_room(self.game.game_type, "human_first", "human", "p0",
                                     ordered_participants=ps)
        npc_turns = 0
        with patch("app.npc_controller._finish_npc_action", return_value=None):
            for _ in range(700):
                actor = next(p for p in room["participants"] if p["player_id"] == room["current_player_id"])
                if actor["participant_kind"] == "system_npc":
                    turn = asyncio.run(run_current_npc_turn(room["room_id"]))
                    self.assertEqual(turn.source, "local")
                    room = turn.room
                    npc_turns += 1
                else:
                    move = self.game.choose_local_npc_action(room["board_state"], actor, room["participants"])
                    room = framework.play_move(room["room_id"], actor["role"], actor["player_id"], move,
                                               expected_revision=room["revision"])
                if room["status"] == "finished":
                    break
            else:
                self.fail("real NPC controller did not finish")
        self.assertGreater(npc_turns, 0)
        with database.connect() as conn:
            completed = conn.execute("SELECT COUNT(*) FROM npc_decisions WHERE status='completed'").fetchone()[0]
        self.assertEqual(completed, npc_turns)

    def test_internal_mcp_invite_join_state_move_and_web_identity(self):
        async def scenario():
            import httpx
            from app import main
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app),
                                         base_url="http://test") as client:
                async def request(**body):
                    response = await client.post("/mcp/play", json=body)
                    self.assertEqual(response.status_code, 200, response.text)
                    return response.json()
                created = await request(action="invite", player_id="ai-one", game_type=self.game.game_type,
                                        target_player_count=2)
                await request(action="join", player_id="ai-two", invite_code=created["invite_code"])
                await request(action="start", player_id="ai-one", room_id=created["room_id"])
                room = framework.get_room(created["room_id"])
                actor = room["current_player_id"]
                view = await request(action="state", player_id=actor, room_id=room["room_id"], full_state=True)
                self.assertIn("legal_actions", json.dumps(view))
                self.assertIn("bga-zh-1851-tenhu", json.dumps(view))
                for pid, hand in room["board_state"]["hands"].items():
                    if pid != actor:
                        for card in hand:
                            self.assertNotIn('"' + card + '"', json.dumps(view))
                move = self.game.choose_local_npc_action(room["board_state"], {"player_id": actor}, room["participants"])
                moved = await request(action="move", player_id=actor, room_id=room["room_id"],
                                      revision=room["revision"], move=move)
                self.assertIn("r", moved, moved)
                self.assertEqual(moved["r"], room["revision"] + 1)
                rejected = await client.post("/mcp/play", json={"action": "move", "player_id": actor,
                    "room_id": room["room_id"], "revision": room["revision"], "move": move})
                self.assertEqual(rejected.status_code, 409)
                rejected = await client.post(f"/api/rooms/{room['room_id']}/move",
                    headers={"X-Duel-Human-Player": "outsider"},
                    json={"move": move, "revision": room["revision"] + 1, "player_id": actor})
                self.assertEqual(rejected.status_code, 403, rejected.text)
                self.assertIn("身份不匹配", rejected.text)
                human_room = framework.create_room(self.game.game_type, "human_first", "human", "p0",
                                                   ordered_participants=players(2))
                human_move = self.game.choose_local_npc_action(human_room["board_state"],
                                    {"player_id": "p0"}, human_room["participants"])
                rejected = await client.post(f"/api/rooms/{human_room['room_id']}/move",
                    headers={"X-Duel-Human-Player": "outsider"},
                    json={"move": human_move, "revision": human_room["revision"], "player_id": "outsider"})
                self.assertEqual(rejected.status_code, 403, rejected.text)
                self.assertEqual(framework.get_room(human_room["room_id"])["revision"], human_room["revision"])
        asyncio.run(scenario())


class TenHuReleaseGate(unittest.TestCase):
    def test_three_independent_modes_are_registered_without_stakes(self):
        modes = [g for g in game_catalog() if g["game_type"].startswith("four_color")]
        self.assertEqual({g["game_type"] for g in modes}, {"four_color_tenhu", "four_color_jiuzhi", "four_color_qunhu"})
        self.assertEqual(len({type(GAMES[g["game_type"]]) for g in modes}), 3)
        self.assertTrue(all(not g["supports_stakes"] for g in modes))


if __name__ == "__main__":
    unittest.main()

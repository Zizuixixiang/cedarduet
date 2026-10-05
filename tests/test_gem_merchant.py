"""宝石商人（gem_merchant）：规则单测、随机整局、隐私与 MCP 集成。"""
import json
import random
import re
import tempfile
import unittest
from collections import Counter
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import httpx

from app import database, framework, invites, takeover
from app import main as main_module
from app.games import game_catalog, get_game
from app.games.gem_merchant import (
    CARDS, INITIAL_TOKENS, ROYALS, SPIRAL, TOKEN_COLORS, GemMerchant, line_error,
    take_lines, win_reason,
)

LETTERS = {"W": "white", "U": "blue", "G": "green", "R": "red", "K": "black",
           "P": "pearl", "O": "gold", ".": None}


def seats():
    return [
        {"player_id": "p0", "seat_index": 0, "role": "human", "participant_kind": "human",
         "display_name": "南山", "token": "X", "active": True, "join_status": "joined"},
        {"player_id": "p1", "seat_index": 1, "role": "ai", "participant_kind": "bound_machine",
         "display_name": "小机", "token": "O", "active": True, "join_status": "joined"},
    ]


class NoShuffle(random.Random):
    def shuffle(self, x):  # deterministic fixture: keep list order
        return None


def new_game(seed=7, opener="p0", rng=None):
    game = GemMerchant(rng or random.Random(seed))
    state = game.initialize(seats())
    game.prepare_opening_state(state, opener, seats())
    return game, state


def set_board(state, rows):
    state["board"] = [[LETTERS[ch] for ch in row] for row in rows]


def tokens(**amounts):
    return {color: amounts.get(color, 0) for color in TOKEN_COLORS}


def put_in_pyramid(state, card_id, level=None, index=0):
    level = str(level or CARDS[card_id]["level"])
    for lv in ("1", "2", "3"):
        if card_id in state["decks"][lv]:
            state["decks"][lv].remove(card_id)
        if card_id in state["pyramid"][lv]:
            state["pyramid"][lv][state["pyramid"][lv].index(card_id)] = None
    old = state["pyramid"][level][index]
    if old:
        state["decks"][level].append(old)
    state["pyramid"][level][index] = card_id


def remove_everywhere(state, card_id):
    for lv in ("1", "2", "3"):
        if card_id in state["decks"][lv]:
            state["decks"][lv].remove(card_id)
        row = state["pyramid"][lv]
        if card_id in row:
            row[row.index(card_id)] = state["decks"][lv].pop(0) if state["decks"][lv] else None


def give_cards(state, pid, *items):
    for item in items:
        item = item if isinstance(item, dict) else {"id": item}
        remove_everywhere(state, item["id"])
        state["players"][pid]["cards"].append(item)


def play(game, state, pid, move):
    game.validate_action(state, move, {"player_id": pid})
    return game.apply_action(state, move, {"player_id": pid})


def card_ids_in(value):
    found = set()
    if isinstance(value, dict):
        if isinstance(value.get("id"), int) and "level" in value and "cost" in value:
            found.add(value["id"])
        for child in value.values():
            found |= card_ids_in(child)
    elif isinstance(value, list):
        for child in value:
            found |= card_ids_in(child)
    return found


def conservation(state):
    total = Counter()
    for row in state["board"]:
        total.update(t for t in row if t)
    for color, n in state["bag"].items():
        total[color] += n
    for player in state["players"].values():
        for color, n in player["tokens"].items():
            assert n >= 0, (color, n)
            total[color] += n
    ids = [c for lv in state["decks"].values() for c in lv]
    ids += [c for lv in state["pyramid"].values() for c in lv if c]
    for player in state["players"].values():
        ids += [x["id"] for x in player["cards"]] + [x["id"] for x in player["reserved"]]
    privileges = state["privileges_on_table"] + sum(p["privileges"] for p in state["players"].values())
    return total, sorted(ids), privileges


class GemMerchantDataTests(unittest.TestCase):
    def test_catalog_entry_and_capabilities(self):
        entry = next(g for g in game_catalog() if g["game_type"] == "gem_merchant")
        self.assertEqual(entry["display_name"], "宝石商人")
        self.assertEqual(entry["category"], "tabletop")
        self.assertEqual(entry["allowed_player_counts"], [2])
        self.assertFalse(entry["supports_npcs"])
        self.assertTrue(entry["supports_stakes"])
        game = get_game("gem_merchant")
        for heading in ("【目标】", "【行动】", "【特殊规则】", "【胜负】"):
            self.assertIn(heading, game.rules_text)
        self.assertIn("Splendor Duel", game.rules_text)
        self.assertIn('"action":"take"', game.move_format)

    def test_card_table_matches_reference_counts_and_disputed_card(self):
        self.assertEqual(len(CARDS), 67)
        by_level = Counter(c["level"] for c in CARDS.values())
        self.assertEqual(by_level, {1: 30, 2: 24, 3: 13})
        self.assertEqual(sum(c["joker"] for c in CARDS.values()), 9)
        self.assertEqual(sorted(c["points"] for c in CARDS.values() if not c["bonus"]), [3, 5, 6])
        self.assertEqual(sorted(c["id"] for c in CARDS.values() if c["bonus"] == 2), [32, 36, 40, 44, 48])
        # BGG v3 与 CGY 唯一分歧：一级 1 冠百搭卡，卡面为“白4 珍珠1”。
        self.assertEqual(CARDS[28]["cost"], {"white": 4, "pearl": 1})
        self.assertTrue(CARDS[28]["joker"])
        self.assertEqual(CARDS[28]["crowns"], 1)
        self.assertEqual(sum(c["crowns"] for c in CARDS.values()), 28)
        for card in CARDS.values():
            self.assertTrue(set(card["cost"]) <= set(TOKEN_COLORS) - {"gold"})
            self.assertTrue(all(n > 0 for n in card["cost"].values()))

    def test_royal_titles_are_original_names_with_reference_values(self):
        self.assertEqual(
            [(r["name"], r["points"], r["ability"]) for r in ROYALS.values()],
            [("糖霜女王", 3, None), ("斗篷小偷", 2, "steal"),
             ("风车旅人", 2, "extra_turn"), ("卷轴管家", 2, "privilege")],
        )
        self.assertEqual(Counter(INITIAL_TOKENS), Counter(
            white=4, blue=4, green=4, red=4, black=4, pearl=2, gold=3))


class GemMerchantSetupTests(unittest.TestCase):
    def test_setup_fills_board_pyramid_and_gives_second_player_a_privilege(self):
        game, state = new_game()
        flat = [t for row in state["board"] for t in row]
        self.assertEqual(Counter(flat), Counter(INITIAL_TOKENS))
        self.assertEqual({lv: len(row) for lv, row in state["pyramid"].items()}, {"1": 5, "2": 4, "3": 3})
        self.assertEqual({lv: len(d) for lv, d in state["decks"].items()}, {"1": 25, "2": 20, "3": 10})
        self.assertEqual(state["turn_player_id"], "p0")
        self.assertEqual(state["players"]["p1"]["privileges"], 1)
        self.assertEqual(state["players"]["p0"]["privileges"], 0)
        self.assertEqual(state["privileges_on_table"], 2)
        game.prepare_opening_state(state, "p1", seats())
        self.assertEqual(state["turn_player_id"], "p1")
        self.assertEqual(state["players"]["p0"]["privileges"], 1)
        self.assertEqual(state["players"]["p1"]["privileges"], 0)
        self.assertEqual(state["privileges_on_table"], 2)

    def test_initial_layout_follows_spiral_from_center(self):
        _game, state = new_game(rng=NoShuffle())
        for (r, c), token in zip(SPIRAL, INITIAL_TOKENS):
            self.assertEqual(state["board"][r][c], token)
        self.assertEqual(SPIRAL[0], (2, 2))

    def test_only_two_players(self):
        with self.assertRaisesRegex(ValueError, "2 名"):
            GemMerchant().initialize(seats()[:1])


class GemMerchantTurnRuleTests(unittest.TestCase):
    def assert_invalid(self, game, state, pid, move, message=""):
        before = deepcopy(state)
        with self.assertRaisesRegex(ValueError, message):
            game.apply_action(state, move, {"player_id": pid})
        self.assertEqual(state, before, "invalid moves must not touch the state")

    def test_take_lines_in_all_four_directions_and_rejections(self):
        game, state = new_game()
        set_board(state, ["RRRUO", "G.GUK", "WWWPP", "KUKUK", "GUGGG"])
        for cells in ([[0, 0], [0, 1], [0, 2]], [[2, 0], [3, 0], [4, 0]],
                      [[2, 0], [3, 1], [4, 2]], [[2, 2], [3, 1], [4, 0]],
                      [[0, 2], [0, 0], [0, 1]], [[3, 3]]):
            self.assertIsNone(line_error(state, [tuple(c) for c in cells]), cells)
        self.assert_invalid(game, state, "p0", {"action": "take", "cells": [[1, 0], [1, 2]]}, "不相邻")
        self.assert_invalid(game, state, "p0", {"action": "take", "cells": [[1, 0], [1, 1], [1, 2]]}, "空格")
        self.assert_invalid(game, state, "p0", {"action": "take", "cells": [[0, 3], [0, 4]]}, "金不能直接拿")
        self.assert_invalid(game, state, "p0", {"action": "take", "cells": [[4, 0], [4, 1], [4, 2], [4, 3]]}, "1 到 3")
        self.assert_invalid(game, state, "p0", {"action": "take", "cells": [[0, 0], [1, 1], [0, 2]]}, "")
        self.assert_invalid(game, state, "p0", {"action": "take", "cells": [[0, 0], [0, 0]]}, "两次")
        self.assert_invalid(game, state, "p1", {"action": "take", "cells": [[0, 0]]}, "还没轮到")
        self.assert_invalid(game, state, "p0", {"action": "take", "cells": [[9, 9]]}, "越界")
        self.assert_invalid(game, state, "p0", {"action": "take", "cells": [[0, 0]], "x": 1}, "参数")
        self.assert_invalid(game, state, "p0", {"action": "fly"}, "未知动作")
        legal = {tuple(map(tuple, a["cells"])) for a in game._legal_actions_for(state, "p0") if a["action"] == "take"}
        self.assertEqual(legal, set(take_lines(state)))

    def test_taking_three_same_or_two_pearls_gifts_a_privilege(self):
        game, state = new_game()
        set_board(state, ["RRRUO", "PPWUK", "WWWPP", "KUKUK", "GUGGG"])
        result = play(game, state, "p0", {"action": "take", "cells": [[0, 0], [0, 1], [0, 2]]})
        self.assertEqual(state["players"]["p0"]["tokens"]["red"], 3)
        self.assertEqual(state["players"]["p1"]["privileges"], 2)
        self.assertEqual(state["privileges_on_table"], 1)
        self.assertEqual(result.next_player_id, "p1")
        self.assertIn("gem_merchant_delta", result.public_event)
        play(game, state, "p1", {"action": "take", "cells": [[1, 0], [1, 1]]})
        self.assertEqual(state["players"]["p0"]["privileges"], 1)
        self.assertEqual(state["privileges_on_table"], 0)
        # Table empty: the opponent's privilege is taken instead.
        play(game, state, "p0", {"action": "take", "cells": [[2, 0], [2, 1], [2, 2]]})
        self.assertEqual(state["players"]["p1"]["privileges"], 3)
        self.assertEqual(state["players"]["p0"]["privileges"], 0)
        # The receiver already holds all three: nothing changes.
        play(game, state, "p1", {"action": "take", "cells": [[4, 0]]})
        play(game, state, "p0", {"action": "take", "cells": [[4, 2], [4, 3], [4, 4]]})
        self.assertEqual(state["players"]["p1"]["privileges"], 3)
        self.assertEqual(state["players"]["p0"]["privileges"], 0)
        self.assertEqual(state["privileges_on_table"], 0)

    def test_privilege_must_precede_refill_and_refill_gifts_opponent(self):
        game, state = new_game(rng=NoShuffle())
        state["players"]["p0"]["privileges"] = 2
        state["players"]["p1"]["privileges"] = 0
        state["privileges_on_table"] = 1
        set_board(state, ["RRRUO", "PPWUK", "W.WPP", "KU.UK", "GUGG."])
        state["bag"] = tokens(red=2, blue=1)
        result = play(game, state, "p0", {"action": "use_privilege", "cell": [0, 0]})
        self.assertTrue(result.retain_turn)
        self.assertEqual(state["players"]["p0"]["tokens"]["red"], 1)
        self.assertEqual(state["privileges_on_table"], 2)
        self.assert_invalid(game, state, "p0", {"action": "use_privilege", "cell": [0, 4]}, "金")
        result = play(game, state, "p0", {"action": "refill"})
        self.assertTrue(result.retain_turn)
        # Pool order white..gold, popped from the end: red, red, blue along the spiral.
        self.assertEqual(state["board"][2][1], "red")
        self.assertEqual(state["board"][3][2], "red")
        self.assertEqual(state["board"][0][0], "blue")
        self.assertIsNone(state["board"][4][4])
        self.assertEqual(sum(state["bag"].values()), 0)
        self.assertEqual(state["players"]["p1"]["privileges"], 1)
        self.assert_invalid(game, state, "p0", {"action": "use_privilege", "cell": [0, 1]}, "补过盘")
        self.assert_invalid(game, state, "p0", {"action": "refill"}, "补过盘")
        self.assertFalse(any(a["action"] in {"use_privilege", "refill"}
                             for a in game._legal_actions_for(state, "p0")))

    def test_reserve_from_pyramid_is_public_and_blind_reserve_is_level_only(self):
        game, state = new_game()
        visible = state["pyramid"]["2"][1]
        refill_card = state["decks"]["2"][0]
        gold = next([r, c] for r in range(5) for c in range(5) if state["board"][r][c] == "gold")
        play(game, state, "p0", {"action": "reserve", "gold": gold, "card_id": visible})
        self.assertEqual(state["pyramid"]["2"][1], refill_card)
        self.assertEqual(state["players"]["p0"]["tokens"]["gold"], 1)
        public = game.public_state(state, seats())
        self.assertEqual(public["players"]["p0"]["reserved"][0]["id"], visible)
        self.assertFalse(public["players"]["p0"]["reserved"][0]["blind"])

        secret = state["decks"]["3"][0]
        result = play(game, state, "p1", {"action": "reserve", "level": 3})
        self.assertEqual(state["players"]["p1"]["reserved"], [{"id": secret, "blind": True}])
        public = game.public_state(state, seats())
        self.assertEqual(public["players"]["p1"]["reserved"], [{"hidden": True, "blind": True, "level": 3}])
        self.assertNotIn(secret, card_ids_in(public))
        self.assertNotIn(secret, card_ids_in(game.private_state(state, seats()[0], seats())))
        self.assertNotIn(secret, card_ids_in(result.public_event))
        self.assertNotIn("card_id", state["last_action"])
        self.assertEqual(game.private_state(state, seats()[1], seats())["reserved"][0]["id"], secret)
        terminal = game.terminal_public_state(state, seats())
        self.assertEqual(terminal["players"]["p1"]["reserved"][0]["id"], secret)
        self.assertTrue(terminal["players"]["p1"]["reserved"][0]["blind"])
        self.assertEqual(game.format_action(state, {"action": "reserve", "level": 3}, {}), "拿金并盲抽保留三级卡")

    def test_reserve_limits(self):
        game, state = new_game()
        state["players"]["p0"]["reserved"] = [{"id": i, "blind": True} for i in (state["decks"]["1"].pop(),
                                                                                   state["decks"]["1"].pop(),
                                                                                   state["decks"]["1"].pop())]
        self.assertFalse(any(a["action"] == "reserve" for a in game._legal_actions_for(state, "p0")))
        self.assert_invalid(game, state, "p0", {"action": "reserve", "level": 1}, "已满")
        state["players"]["p0"]["reserved"] = []
        set_board(state, ["RRRUW", "PPWUK", "W.WPP", "KU.UK", "GUGG."])
        self.assert_invalid(game, state, "p0", {"action": "reserve", "level": 1}, "没有金")
        set_board(state, ["RRRUO", "PPWUK", "W.WPP", "KU.UK", "GUGG."])
        self.assert_invalid(game, state, "p0", {"action": "reserve", "level": 1, "card_id": 1}, "二选一")
        self.assert_invalid(game, state, "p0", {"action": "reserve", "gold": [0, 0], "level": 1}, "不是金")
        play(game, state, "p0", {"action": "reserve", "level": 1})  # gold omitted → first gold
        self.assertIsNone(state["board"][0][4])

    def test_buy_uses_bonus_then_tokens_then_gold_and_refills_slot(self):
        game, state = new_game()
        put_in_pyramid(state, 4, index=2)
        next_card = state["decks"]["1"][0]
        state["players"]["p0"]["tokens"] = tokens(blue=1, green=3, gold=1, white=2)
        result = play(game, state, "p0", {"action": "buy", "card_id": 4})
        me = state["players"]["p0"]
        self.assertEqual(me["tokens"], tokens(white=2))
        self.assertEqual(state["bag"], tokens(blue=1, green=3, gold=1))
        self.assertEqual(me["cards"], [{"id": 4}])
        self.assertEqual(state["pyramid"]["1"][2], next_card)
        self.assertEqual(result.next_player_id, "p1")
        public = game.public_state(state, seats())["players"]["p0"]
        self.assertEqual((public["points"], public["bonuses"]["black"]), (1, 1))
        # A black bonus now discounts card 1's cost? No black in card 1 cost; card 3 needs green/red only.
        give_cards(state, "p0", 21)  # white bonus
        put_in_pyramid(state, 31, index=0)
        state["turn_player_id"] = "p0"
        state["players"]["p0"]["tokens"] = tokens(white=2, green=2, gold=1)
        self.assert_invalid(game, state, "p0", {"action": "buy", "card_id": 31}, "买不起")
        state["players"]["p0"]["tokens"]["gold"] = 2
        play(game, state, "p0", {"action": "buy", "card_id": 31})
        self.assertEqual(state["players"]["p0"]["tokens"], tokens())
        self.assert_invalid(game, state, "p1", {"action": "buy", "card_id": 999}, "不在场上")

    def test_joker_needs_a_bonus_card_and_counts_as_chosen_color(self):
        game, state = new_game()
        put_in_pyramid(state, 27, index=0)
        state["players"]["p0"]["tokens"] = tokens(black=4, pearl=1)
        self.assert_invalid(game, state, "p0", {"action": "buy", "card_id": 27}, "百搭卡要压")
        give_cards(state, "p0", 10, 15)
        self.assert_invalid(game, state, "p0", {"action": "buy", "card_id": 27}, "joker_color")
        self.assert_invalid(game, state, "p0", {"action": "buy", "card_id": 27, "joker_color": "blue"}, "joker_color")
        legal = [a for a in game._legal_actions_for(state, "p0") if a["action"] == "buy" and a["card_id"] == 27]
        self.assertEqual({a["joker_color"] for a in legal}, {"red", "green"})
        play(game, state, "p0", {"action": "buy", "card_id": 27, "joker_color": "red"})
        public = game.public_state(state, seats())["players"]["p0"]
        self.assertEqual(public["bonuses"]["red"], 2)
        self.assertEqual(public["color_points"]["red"], 1)
        self.assertIn({"id": 27, "as": "red"}, public["purchased"])

    def test_take_gem_ability_waits_only_when_there_is_a_choice(self):
        game, state = new_game()
        set_board(state, ["RKRUO", "PPWUK", "W.WPP", "GU.UG", "GUGG."])
        put_in_pyramid(state, 3, index=0)
        state["players"]["p0"]["tokens"] = tokens(green=2, red=2)
        result = play(game, state, "p0", {"action": "buy", "card_id": 3})
        self.assertTrue(result.retain_turn)
        self.assertEqual(state["flow"]["phase"], "resolve")
        legal = game._legal_actions_for(state, "p0")
        self.assertEqual(legal, [{"action": "take_bonus_gem", "cell": [0, 1]},
                                 {"action": "take_bonus_gem", "cell": [1, 4]}])
        self.assert_invalid(game, state, "p0", {"action": "take", "cells": [[0, 0]]}, "先完成结算")
        self.assert_invalid(game, state, "p0", {"action": "take_bonus_gem", "cell": [0, 0]}, "不可用")
        result = play(game, state, "p0", {"action": "take_bonus_gem", "cell": [1, 4]})
        self.assertEqual(result.next_player_id, "p1")
        self.assertEqual(state["players"]["p0"]["tokens"]["black"], 1)
        # Single option resolves automatically inside the purchase.
        game, state = new_game()
        set_board(state, ["RKRUO", "PPWUW", "W.WPP", "GU.UG", "GUGG."])
        put_in_pyramid(state, 3, index=0)
        state["players"]["p0"]["tokens"] = tokens(green=2, red=2)
        result = play(game, state, "p0", {"action": "buy", "card_id": 3})
        self.assertEqual(result.next_player_id, "p1")
        self.assertIsNone(state["board"][0][1])

    def test_steal_privilege_and_extra_turn_abilities(self):
        game, state = new_game()
        put_in_pyramid(state, 31, index=0)
        state["players"]["p0"]["tokens"] = tokens(white=4, green=3)
        state["players"]["p1"]["tokens"] = tokens(red=1, pearl=1, gold=2)
        play(game, state, "p0", {"action": "buy", "card_id": 31})
        self.assertEqual(game._legal_actions_for(state, "p0"),
                         [{"action": "steal", "gem": "red"}, {"action": "steal", "gem": "pearl"}])
        self.assert_invalid(game, state, "p0", {"action": "steal", "gem": "gold"}, "gem")
        play(game, state, "p0", {"action": "steal", "gem": "pearl"})
        self.assertEqual(state["players"]["p0"]["tokens"]["pearl"], 1)
        self.assertEqual(state["players"]["p1"]["tokens"]["pearl"], 0)

        game, state = new_game()
        put_in_pyramid(state, 34, index=0)
        state["players"]["p0"]["tokens"] = tokens(red=2, black=4, pearl=1)
        play(game, state, "p0", {"action": "buy", "card_id": 34})
        self.assertEqual(state["players"]["p0"]["privileges"], 1)

        game, state = new_game()
        put_in_pyramid(state, 2, index=0)
        state["players"]["p0"]["tokens"] = tokens(white=2, blue=2, pearl=1)
        result = play(game, state, "p0", {"action": "buy", "card_id": 2})
        self.assertTrue(result.retain_turn)
        self.assertEqual(state["turn_player_id"], "p0")
        self.assertEqual(state["flow"]["phase"], "optional")
        self.assertEqual(state["flow"]["turn_number"], 1)

    def test_third_crown_grants_a_royal_choice_and_royal_ability(self):
        game, state = new_game()
        give_cards(state, "p0", 5, 10)
        put_in_pyramid(state, 15, index=0)
        state["players"]["p0"]["tokens"] = tokens(red=3)
        state["players"]["p1"]["tokens"] = tokens(blue=1, white=1)
        play(game, state, "p0", {"action": "buy", "card_id": 15})
        self.assertEqual(game.public_state(state, seats())["pending"], {"kind": "royal"})
        self.assertEqual([a["royal_id"] for a in game._legal_actions_for(state, "p0")], [1, 2, 3, 4])
        play(game, state, "p0", {"action": "choose_royal", "royal_id": 2})
        self.assertEqual(state["royals"], [1, 3, 4])
        self.assertEqual(game._legal_actions_for(state, "p0"),
                         [{"action": "steal", "gem": "white"}, {"action": "steal", "gem": "blue"}])
        result = play(game, state, "p0", {"action": "steal", "gem": "blue"})
        self.assertEqual(result.next_player_id, "p1")
        public = game.public_state(state, seats())["players"]["p0"]
        self.assertEqual((public["points"], public["crowns"]), (2, 3))
        self.assertEqual(public["royals"][0]["name"], "斗篷小偷")

    def test_discard_one_at_a_time_down_to_ten(self):
        game, state = new_game()
        set_board(state, ["RRRUO", "PPWUK", "W.WPP", "KU.UK", "GUGG."])
        state["players"]["p0"]["tokens"] = tokens(white=4, blue=3, gold=3)
        result = play(game, state, "p0", {"action": "take", "cells": [[0, 0], [0, 1]]})
        self.assertTrue(result.retain_turn)
        self.assertEqual(game.public_state(state, seats())["pending"], {"kind": "discard", "count": 2})
        self.assertEqual({a["gem"] for a in game._legal_actions_for(state, "p0")}, {"white", "blue", "red", "gold"})
        play(game, state, "p0", {"action": "discard", "gem": "gold"})
        self.assertEqual(state["flow"]["phase"], "resolve")
        result = play(game, state, "p0", {"action": "discard", "gem": "white"})
        self.assertEqual(result.next_player_id, "p1")
        self.assertEqual(sum(state["players"]["p0"]["tokens"].values()), 10)
        self.assertEqual(state["bag"]["gold"] + state["bag"]["white"], 2)

    def test_three_victory_conditions(self):
        _game, state = new_game()
        me = state["players"]["p0"]
        self.assertIsNone(win_reason(state, "p0"))
        me["cards"], me["royals"] = [{"id": 65}, {"id": 51}, {"id": 26}], [1, 3]
        self.assertIsNone(win_reason(state, "p0"))  # 6 + 5 + 3 + 3 + 2 = 19
        me["royals"] = [1, 3, 4]
        self.assertEqual(win_reason(state, "p0"), "声望 21 分")
        me["royals"] = []
        me["cards"] = [{"id": 66, "as": "red"}, {"id": 55}, {"id": 57}, {"id": 59}, {"id": 5}]
        self.assertEqual(win_reason(state, "p0"), "10 顶皇冠")
        me["cards"] = [{"id": 64}, {"id": 63}, {"id": 49}, {"id": 47}]
        self.assertEqual(win_reason(state, "p0"), "白色卡共 10 分")
        me["cards"] = [{"id": 65}, {"id": 64}]
        self.assertIsNone(win_reason(state, "p0"), "纯分卡不算任何颜色")
        me["cards"] = [{"id": 67, "as": "white"}, {"id": 64}, {"id": 63}]
        self.assertEqual(win_reason(state, "p0"), "白色卡共 10 分")

    def test_purchase_reaching_twenty_points_ends_the_game(self):
        game, state = new_game()
        give_cards(state, "p0", 65, 51, 26)
        state["players"]["p0"]["royals"] = [1]
        state["royals"] = [2, 3, 4]
        put_in_pyramid(state, 4, index=0)
        state["players"]["p0"]["tokens"] = tokens(blue=2, green=3)
        result = play(game, state, "p0", {"action": "buy", "card_id": 4})
        self.assertIsNone(result.result)  # 6 + 5 + 3 + 3 + 1 = 18
        state["turn_player_id"] = "p0"
        give_cards(state, "p0", 34)
        put_in_pyramid(state, 9, index=0)
        state["players"]["p0"]["tokens"] = tokens(white=2, blue=3)
        result = play(game, state, "p0", {"action": "buy", "card_id": 9})
        self.assertEqual(result.result, {"winner_player_id": "p0", "draw": False, "reason": "声望 21 分"})
        self.assertEqual(state["flow"]["phase"], "finished")
        self.assertEqual(game._legal_actions_for(state, "p0"), [])
        self.assertEqual(game._legal_actions_for(state, "p1"), [])

    def test_must_refill_when_stuck_then_pass_and_double_pass_ends(self):
        game, state = new_game()
        set_board(state, [".....", ".....", ".....", ".....", "....."])
        state["bag"] = tokens(red=2)
        state["players"]["p0"]["privileges"] = 1
        self.assertEqual(game._legal_actions_for(state, "p0"), [{"action": "refill"}])
        self.assert_invalid(game, state, "p0", {"action": "pass"}, "先补盘")
        state["bag"] = tokens()
        self.assertEqual(game._legal_actions_for(state, "p0"), [{"action": "pass"}])
        result = play(game, state, "p0", {"action": "pass"})
        self.assertEqual(result.next_player_id, "p1")
        give_cards(state, "p1", 4)
        result = play(game, state, "p1", {"action": "pass"})
        self.assertEqual(result.result["winner_player_id"], "p1")
        self.assertIn("1", result.result["reason"])

    def test_npc_assist_actions_skip_optional_steps(self):
        game, state = new_game()
        state["players"]["p0"]["privileges"] = 1
        actions = game.npc_legal_actions(state, {"player_id": "p0"}, seats())
        self.assertTrue(actions)
        self.assertFalse(any(a["action"] in {"use_privilege", "refill"} for a in actions))


class GemMerchantRandomPlayTests(unittest.TestCase):
    def test_seeded_random_games_terminate_and_conserve_everything(self):
        endings = Counter()
        for seed in range(60):
            rng = random.Random(seed)
            game, state = new_game(seed=seed, opener="p0" if seed % 2 else "p1")
            current = state["turn_player_id"]
            for step in range(4000):
                actions = game._legal_actions_for(state, current)
                self.assertTrue(actions, (seed, step))
                other = "p1" if current == "p0" else "p0"
                self.assertEqual(game._legal_actions_for(state, other), [])
                if step % 7 == 0:
                    for candidate in rng.sample(actions, min(5, len(actions))):
                        game.validate_action(state, candidate, {"player_id": current})
                    bogus = {"action": "take", "cells": [[rng.randrange(5), rng.randrange(5)] for _ in range(4)]}
                    before = deepcopy(state)
                    with self.assertRaises(ValueError):
                        game.apply_action(state, bogus, {"player_id": current})
                    self.assertEqual(state, before)
                move = rng.choice(actions)
                result = play(game, state, current, move)
                total, ids, privileges = conservation(state)
                self.assertEqual(total, Counter(INITIAL_TOKENS), (seed, step))
                self.assertEqual(ids, list(range(1, 68)))
                self.assertEqual(privileges, 3)
                self.assertTrue(all(len(p["reserved"]) <= 3 for p in state["players"].values()))
                if result.result:
                    break
                if not result.retain_turn:
                    # Over-limit tokens are discarded before the turn ends.
                    self.assertLessEqual(sum(state["players"][current]["tokens"].values()), 10)
                    self.assertEqual(result.next_player_id, other)
                    current = other
                self.assertEqual(state["turn_player_id"], current)
            else:
                self.fail(f"seed {seed} did not finish")
            endings["draw" if state["result"].get("draw") else "win"] += 1
        self.assertGreater(endings["win"], 50)

    def test_validator_accepts_exactly_the_published_actions(self):
        rng = random.Random(5)
        for seed in range(12):
            game, state = new_game(seed=seed)
            current = state["turn_player_id"]
            for _ in range(rng.randrange(5, 40)):
                actions = game._legal_actions_for(state, current)
                result = play(game, state, current, rng.choice(actions))
                if result.result:
                    break
                current = state["turn_player_id"]
            if state.get("result"):
                continue
            legal = game._legal_actions_for(state, current)
            for _ in range(300):
                kind = rng.choice(["take", "reserve", "buy", "use_privilege"])
                if kind == "take":
                    move = {"action": "take", "cells": sorted(
                        [[rng.randrange(5), rng.randrange(5)] for _ in range(rng.randint(1, 3))])}
                elif kind == "reserve":
                    move = {"action": "reserve", "gold": [rng.randrange(5), rng.randrange(5)],
                            **({"level": rng.randint(1, 3)} if rng.random() < .4 else {"card_id": rng.randint(1, 67)})}
                elif kind == "buy":
                    move = {"action": "buy", "card_id": rng.randint(1, 67)}
                    if CARDS[move["card_id"]]["joker"]:
                        move["joker_color"] = rng.choice(["white", "blue", "green", "red", "black"])
                else:
                    move = {"action": "use_privilege", "cell": [rng.randrange(5), rng.randrange(5)]}
                try:
                    game.validate_action(state, move, {"player_id": current})
                    accepted = True
                except ValueError:
                    accepted = False
                self.assertEqual(accepted, move in legal, move)


class GemMerchantIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="duel-gem-merchant-")
        self.db_patch = patch.object(database, "DB_PATH", Path(self.temporary.name) / "test.db")
        self.db_patch.start()
        database.init_db()
        self.original_events = main_module.revision_events
        main_module.revision_events = main_module.RevisionEvents()
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=main_module.app),
                                        base_url="http://duel.test")

    async def asyncTearDown(self):
        await self.client.aclose()
        main_module.revision_events = self.original_events
        self.db_patch.stop()
        self.temporary.cleanup()

    def assert_no_secret(self, payload, secret):
        self.assertNotIn(secret, card_ids_in(payload))
        self.assertIsNone(re.search(rf"#{secret}(?!\d)", json.dumps(payload, ensure_ascii=False)))

    async def test_catalog_web_rules_and_resources(self):
        credit = "制作：顾屿、相顾｜小红书：苏苏脆脆"
        catalog = await self.client.post("/mcp/play", json={"action": "catalog", "player_id": "a1"})
        self.assertEqual(catalog.status_code, 200, catalog.text)
        entry = next(g for g in catalog.json()["games"] if g["game_type"] == "gem_merchant")
        self.assertEqual(entry["allowed_player_counts"], [2])
        self.assertNotIn("author", entry)
        room = framework.create_room("gem_merchant", "human_first", "human", "h1", "a1",
                                     require_confirmations=False)
        web = await self.client.get(f"/api/rooms/{room['room_id']}",
                                    headers={"X-Duel-Human-Player": "h1"})
        self.assertEqual(web.status_code, 200, web.text)
        self.assertIn(credit, web.json()["room"]["rules_text"])
        action = web.json()["room"]["private_state"]["legal_actions"][0]
        moved = await self.client.post(f"/api/rooms/{room['room_id']}/move",
            headers={"X-Duel-Human-Player": "h1"},
            json={"player_id": "h1", "revision": room["revision"], "move": action})
        self.assertEqual(moved.status_code, 200, moved.text)
        self.assertEqual(moved.json()["room"]["revision"], room["revision"] + 1)
        for suffix, content_type in (("js", "javascript"), ("css", "text/css")):
            resource = await self.client.get(f"/static/games/gem_merchant.{suffix}")
            self.assertEqual(resource.status_code, 200)
            self.assertIn(content_type, resource.headers["content-type"])

    async def test_timeout_takeover_uses_safe_authoritative_actions(self):
        with patch.object(invites, "npc_provider_capabilities", return_value={"available": True}):
            room = invites.create_invite("gem_merchant", "human", "h1", target_player_count=2,
                                         timeout_takeover_seconds=90)
        invites.join_invite(room["invite_code"], "human", "h2")
        room = invites.start_invite(room["room_id"], "human", "h1")
        owner = room["current_player_id"]
        room = framework.play_move(room["room_id"], "human", owner,
                                   {"action": "reserve", "level": 2}, expected_revision=room["revision"])
        secret = room["board_state"]["players"][owner]["reserved"][0]["id"]
        actor = next(p for p in room["participants"] if p["player_id"] == room["current_player_id"])
        actions = takeover.timeout_legal_actions(room, actor)
        self.assertTrue(actions)
        for action in actions:
            get_game("gem_merchant").validate_action(room["board_state"], action, actor)
        with database.write_transaction() as conn:
            conn.execute("UPDATE room_invites SET turn_started_at=? WHERE room_id=?",
                         ((datetime.now(timezone.utc) - timedelta(seconds=91)).isoformat(), room["room_id"]))
        requests = []
        class FailingProvider:
            async def decide(inner, request):
                requests.append(request)
                raise RuntimeError("exercise authoritative fallback")
        moved = await takeover.run_timeout_turn(room["room_id"], FailingProvider())
        self.assertEqual(len(requests), 1)
        self.assert_no_secret(requests[0].payload(), secret)
        self.assertEqual(moved["revision"], room["revision"] + 1)
        self.assertEqual(moved["participants"], room["participants"])

    async def test_framework_room_plays_to_terminal_and_hides_blind_reserves(self):
        rng = random.Random(3)
        room = framework.create_room("gem_merchant", "human_first", "human", "h1", "a1",
                                     require_confirmations=False)
        roles = {p["player_id"]: p["role"] for p in room["participants"]}
        blind_seen = 0
        for _ in range(3000):
            if room["status"] != "playing":
                break
            pid = room["current_player_id"]
            other = next(p for p in roles if p != pid)
            actions = framework.project_room_for_viewer(room, pid)["private_state"]["legal_actions"]
            self.assertEqual(framework.project_room_for_viewer(room, other)["private_state"]["legal_actions"], [])
            blind = [a for a in actions if a["action"] == "reserve" and "level" in a]
            move = blind[0] if blind and rng.random() < .3 else rng.choice(actions)
            room = framework.play_move(room["room_id"], roles[pid], pid, move,
                                       expected_revision=room["revision"])
            if "level" in move and move["action"] == "reserve":
                secret = room["board_state"]["players"][pid]["reserved"][-1]["id"]
                if secret in [c for row in room["board_state"]["pyramid"].values() for c in row]:
                    continue
                blind_seen += 1
                if room["status"] == "playing":
                    view = framework.project_room_for_viewer(room, other)
                    self.assert_no_secret(view["board_state"], secret)
                    self.assert_no_secret(view["private_state"], secret)
                    timeline = framework.list_timeline(room["room_id"], viewer_player_id=other)
                    self.assertIsNone(re.search(rf"#{secret}(?!\d)", json.dumps(timeline, ensure_ascii=False)))
                    self.assert_no_secret(timeline, secret)
        self.assertEqual(room["status"], "finished")
        self.assertGreater(blind_seen, 0)
        self.assertTrue(room["winner_player_id"] or (room["result"] or {}).get("draw"))
        final = framework.project_room_for_viewer(room, "h1")["board_state"]
        for player in final["players"].values():
            self.assertTrue(all(not c.get("hidden") for c in player["reserved"]))

    async def test_resignation_finishes_for_the_opponent(self):
        room = framework.create_room("gem_merchant", "human_first", "human", "h1", "a1",
                                     require_confirmations=False)
        room = framework.resign(room["room_id"], "human", "h1")
        self.assertEqual(room["status"], "finished")
        self.assertEqual(room["winner_player_id"], "a1")

    async def test_mcp_bootstrap_delta_and_blind_privacy(self):
        response = await self.client.post("/mcp/play", json={
            "action": "new", "player_id": "ai-1", "opponent_id": "human-1",
            "game_type": "gem_merchant", "mode": "ai_first", "stake": 0})
        self.assertEqual(response.status_code, 200, response.text)
        boot = response.json()
        self.assertTrue(boot["bootstrap"])
        room = boot["room"]
        self.assertTrue(room["rules_text"].startswith(get_game("gem_merchant").rules_text))
        self.assertIn("制作：顾屿、相顾｜小红书：苏苏脆脆", room["rules_text"])
        self.assertIn("legal_summary", room["private_state"])
        self.assertNotIn("legal_actions", room["private_state"])
        self.assertIn("delta_format", room["board_state"])
        self.assertNotIn("decks", json.dumps(room["board_state"]))
        board = room["board_state"]["board"]
        self.assertEqual([len(row) for row in board], [5] * 5)
        self.assertTrue(all(isinstance(card, str) for row in room["board_state"]["pyramid"].values() for card in row))
        cell = next([r, c] for r, row in enumerate(board) for c, v in enumerate(row) if v not in ".O")
        moved = (await self.client.post("/mcp/play", json={
            "action": "move", "player_id": "ai-1", "room_id": room["room_id"],
            "revision": room["revision"], "move": {"action": "take", "cells": [cell]}})).json()
        delta = next(e["gem_merchant_delta"] for e in moved["events"] if "gem_merchant_delta" in e)
        self.assertEqual(delta["board_set"], [[*cell, "."]])
        self.assertIn("ai-1", delta["players"])

        current = framework.get_room(room["room_id"])
        legal = framework.project_room_for_viewer(current, "human-1")["private_state"]["legal_actions"]
        blind = next(a for a in legal if a["action"] == "reserve" and a.get("level") == 2)
        current = framework.play_move(current["room_id"], "human", "human-1", blind,
                                      expected_revision=current["revision"], message="盲抽")
        secret = current["board_state"]["players"]["human-1"]["reserved"][0]["id"]
        state = (await self.client.post("/mcp/play", json={
            "action": "state", "player_id": "ai-1", "room_id": room["room_id"]})).json()
        self.assertTrue(state["your_turn"])
        self.assert_no_secret(state, secret)
        self.assertIn({"name": "human-1", "move": blind, "message": "盲抽"},
                      [e for e in state["events"] if "move" in e])
        delta = next(e["gem_merchant_delta"] for e in state["events"] if "gem_merchant_delta" in e)
        self.assertEqual(delta["players"]["human-1"]["reserved"], ["hidden L2"])
        framework.post_message(room["room_id"], "human", "human-1", "重查前聊天")
        full = (await self.client.post("/mcp/play", json={
            "action": "state", "player_id": "ai-1", "room_id": room["room_id"], "full_state": True})).json()
        self.assertEqual(full["snapshot"]["rules_text"], get_game("gem_merchant").rules_text)
        self.assertIn("制作：顾屿、相顾｜小红书：苏苏脆脆", full["snapshot"]["rules_text"])
        self.assert_no_secret(full, secret)
        self.assertNotIn("action_history", full["snapshot"]["board_state"])
        self.assertEqual([e["message"] for e in full["events"]], ["重查前聊天"])
        repeated = (await self.client.post("/mcp/play", json={
            "action": "state", "player_id": "ai-1", "room_id": room["room_id"],
            "full_state": True, "wait": True})).json()
        self.assertEqual(full["snapshot"], repeated["snapshot"])
        self.assertNotIn("events", repeated)
        following = (await self.client.post("/mcp/play", json={
            "action": "state", "player_id": "ai-1", "room_id": room["room_id"]})).json()
        self.assertNotIn("events", following)
        # The owner sees the blind card in its own private projection.
        own = framework.project_mcp_snapshot_for_viewer(framework.get_room(room["room_id"]), "human-1")
        self.assertTrue(own["private_state"]["reserved"][0].startswith(f"#{secret} L2 "))
        self.assertTrue(own["private_state"]["reserved"][0].endswith(" blind"))
        # A face-up reservation stays visible, including in the MCP delta.
        summary = full["snapshot"]["private_state"]["legal_summary"]["reserve"]
        public_card = summary["card_ids"][0]
        response = await self.client.post("/mcp/play", json={
            "action": "move", "player_id": "ai-1", "room_id": room["room_id"],
            "revision": full["snapshot"]["revision"],
            "move": {"action": "reserve", "card_id": public_card, "gold": summary["gold"][0]}})
        self.assertEqual(response.status_code, 200, response.text)
        delta = next(e["gem_merchant_delta"] for e in response.json()["events"] if "gem_merchant_delta" in e)
        face = delta["players"]["ai-1"]["reserved"][0]
        self.assertTrue(face.startswith(f"#{public_card} "))
        opponent = framework.project_mcp_snapshot_for_viewer(framework.get_room(room["room_id"]), "human-1")
        self.assertEqual(opponent["board_state"]["players"]["ai-1"]["reserved"], [face])


if __name__ == "__main__":
    unittest.main()

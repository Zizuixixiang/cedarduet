"""Public provenance only; no live rooms or databases."""
from copy import deepcopy
import random
import unittest

from app.games.four_color_cards.common import public_discard_sources, record_discard
from app.games.four_color_cards.cards import code
from app.games.four_color_cards.tenhu import BgaTenHu
from app.games.four_color_cards.jiuzhi import Jiuzhi
from app.games.four_color_cards.qunhu import Qunhu
from app.games.four_color_cards.mcp import delta
from app.games.four_color_cards.qunhu_rules import waiting_codes, exhibit
from tests.test_four_color_tenhu import players
from tests.test_four_color_variants import custom, ready


class DiscardSources(unittest.TestCase):
    def test_old_saves_stay_unknown_and_projection_filters_unsettled_cards(self):
        state = {"discards": ["R1-1"], "participant_order": ["p0", "p1"]}
        self.assertEqual(public_discard_sources(state), {})
        record_discard(state, {"card": "Y1-1", "source": "p1", "from_deck": False})
        record_discard(state, {"card": "G1-1", "source": "p0", "from_deck": True})
        state["discard_sources"]["W1-1"] = "p0"  # not in public discards
        self.assertEqual(public_discard_sources(state), {"Y1-1": "p1", "G1-1": None})
        self.assertEqual(state["discards"], ["R1-1", "Y1-1", "G1-1"])

    def test_real_rounds_preserve_rules_private_projection_and_delta_replay(self):
        for cls, count in [(BgaTenHu, 4), (Jiuzhi, 4), (Qunhu, 4), (Qunhu, 5)]:
            with self.subTest(game=cls.game_type, count=count):
                game = cls(random.Random(9))
                ps = players(count)
                state = game.initialize(ps)
                for _ in range(700):
                    actor = {"player_id": state["turn_player_id"]}
                    move = game.choose_local_npc_action(state, actor, ps)
                    before = game.public_state(state, ps)
                    pending = state["pending"]
                    legacy = deepcopy(state)
                    legacy.pop("discard_sources", None)
                    applied = game.apply_action(state, move, actor)
                    without_metadata = game.apply_action(legacy, move, actor)
                    following = applied.state
                    a, b = deepcopy(following), deepcopy(without_metadata.state)
                    a.pop("discard_sources", None); b.pop("discard_sources", None)
                    self.assertEqual(a, b, "provenance never changes game decisions")
                    self.assertEqual(applied.next_player_id, without_metadata.next_player_id)
                    self.assertEqual(applied.result, without_metadata.result)
                    for p in ps:
                        self.assertEqual(game.private_state(following, p, ps),
                                         game.private_state(without_metadata.state, p, ps))
                    after = game.public_state(following, ps)
                    new_cards = after["discards"][len(before["discards"]):]
                    for card in new_cards:
                        action = move["action_id"].split(":")[0]
                        source = actor["player_id"] if action in ("discard", "listen", "draw") else pending["source"]
                        from_deck = action == "draw" or (action == "pass" and pending["from_deck"])
                        # Qunhu draws enter a hand, or are re-offered by a locked listener.
                        expected = None if from_deck and cls is not Qunhu else source
                        self.assertEqual(after["discard_sources"][card], expected)
                    self.assertEqual(set(after["discard_sources"]), set(after["discards"]))
                    restored = deepcopy(before)
                    for key, value in delta(after, before).items():
                        if key == "discards+":
                            restored["discards"].extend(value)
                        elif key == "melds+":
                            for pid, groups in value.items():
                                restored["melds"][pid].extend(groups)
                        else:
                            restored[key] = value
                    self.assertEqual(restored, after)
                    state = following
                    if state["result"]:
                        break
                else:
                    self.fail("round did not finish")

    def test_qunhu_locked_draw_is_a_player_discard_after_reoffer(self):
        game = Qunhu(random.Random(9))
        state = custom(game, [ready(), ["R5-4"], ["W5-4"]])
        state["listening"]["p0"] = {"waits": waiting_codes(ready()), "exhibit": exhibit(ready())}
        card = next(t for t in state["wall"] if code(t) not in state["listening"]["p0"]["waits"])
        state["wall"].remove(card)
        game.offer(state, card, "p0", True)
        self.assertFalse(state["pending"]["from_deck"])
        self.assertNotIn(card, public_discard_sources(state))
        state = game.apply_action(state, game._action("pass"), {"player_id": state["turn_player_id"]}).state
        self.assertEqual(public_discard_sources(state)[card], "p0")


if __name__ == "__main__":
    unittest.main()

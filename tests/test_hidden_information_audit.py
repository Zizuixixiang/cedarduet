from copy import deepcopy
import unittest

from app.games import GAMES
from app.games.base import GamePlugin


# Final hidden-information audit. Keeping every catalog entry in exactly one
# bucket makes a newly added game fail this test until its terminal policy has
# been reviewed instead of silently inheriting an unsuitable reveal rule.
FULL_TERMINAL_REVIEW_REASONS = {
    "bomb_plane": "opponent planes remain hidden until the terminal participant-only review",
    "banqi": "unflipped piece identities become reviewable only after the room is terminal",
    "blackjack": "the dealer hole is hidden during play and revealed after settlement or room termination",
    "doudizhu": "opponent hands stay private during play and remaining hands are shown at terminal",
    "gem_merchant": "blind-reserved card faces stay private during play and are reviewable at terminal; deck order never leaves the server",
    "gandengyan": "opponent hands stay private during play and remaining hands are shown at terminal",
    "guandan": "opponent hands stay private during play and remaining hands are shown at terminal",
    "junqi": "unrevealed ranks stay private during play and the terminal board is reviewable",
    "liars_dice": "current dice stay private during bidding and terminal dice are reviewable",
    "mahjong": "concealed hands and concealed-kong faces become reviewable only at terminal",
    "train_cards": "future personal pile order stays private during play and is reviewable at terminal",
    "uno": "opponent hands stay private during play and remaining hands are shown at terminal",
}

RULE_SCOPED_REVEAL_REASONS = {
    "carcassonne": "placed/current terrain is public; the server deck order stays hidden even after resignation or terminal scoring",
    "monopoly": "cash, assets and revealed events are public; future decks, internal continuations and held jail-card sources stay hidden even at terminal",
    "monopoly_plus": "cash, assets, bets, loans and revealed events are public; held item cards show only a count and, like future decks and bus tickets order, stay hidden even at terminal",
    "rummikub": "only played tiles and terminal numeric scores are public; opponent racks and pool order remain private",
    "texas_holdem": "showdown holes are public, but fold/muck endings must not force a reveal",
    "zhajinhua": "forced showdown hands are public, but folded or unshown hands must stay hidden",
}

PUBLIC_INFORMATION_REASONS = {
    "aeroplane_chess": "the roll and every plane position are public after each action",
    "checkers": "the complete board is public",
    "chess": "the complete board is public",
    "chinese_checkers": "the complete board is public",
    "connect4": "the complete board is public",
    "dots_boxes": "all edges and claimed boxes are public",
    "go": "the board, history-dependent legality, and score confirmations contain no opponent secret",
    "gomoku": "the complete board is public",
    "jungle": "the complete board is public",
    "othello": "the complete board is public",
    "tictactoe": "the complete board is public",
    "xiangqi": "the complete board is public",
    "yahtzee": "the active roll, held dice, scorecards, and score previews are intentionally public",
}


class HiddenInformationCatalogAuditTests(unittest.TestCase):
    def test_every_catalog_game_has_an_explicit_terminal_privacy_classification(self):
        buckets = (
            set(FULL_TERMINAL_REVIEW_REASONS),
            set(RULE_SCOPED_REVEAL_REASONS),
            set(PUBLIC_INFORMATION_REASONS),
        )
        self.assertFalse(buckets[0] & buckets[1])
        self.assertFalse(buckets[0] & buckets[2])
        self.assertFalse(buckets[1] & buckets[2])
        self.assertEqual(set(GAMES), set().union(*buckets))
        self.assertTrue(all(
            isinstance(reason, str) and reason
            for reasons in (
                FULL_TERMINAL_REVIEW_REASONS,
                RULE_SCOPED_REVEAL_REASONS,
                PUBLIC_INFORMATION_REASONS,
            )
            for reason in reasons.values()
        ))

    def test_full_review_games_override_the_safe_default_terminal_projection(self):
        for game_type in FULL_TERMINAL_REVIEW_REASONS:
            with self.subTest(game_type=game_type):
                self.assertIsNot(
                    type(GAMES[game_type]).terminal_public_state,
                    GamePlugin.terminal_public_state,
                )

    def test_poker_and_rummikub_keep_safe_default_terminal_projection(self):
        for game_type in ("texas_holdem", "zhajinhua", "rummikub"):
            with self.subTest(game_type=game_type):
                self.assertIs(
                    type(GAMES[game_type]).terminal_public_state,
                    GamePlugin.terminal_public_state,
                )

    def test_monopoly_terminal_and_viewer_projections_keep_future_information_hidden(self):
        game = GAMES["monopoly"]
        participants = [{"player_id": f"audit-{i}", "role": "human",
                         "display_name": f"玩家{i}"} for i in range(4)]
        state = game.initialize(participants)
        state["players"][0]["jail_cards"] = ["chance"]
        state["_decks"]["chance"].remove(0)
        before = deepcopy(state)

        def assert_no_internal(value):
            if isinstance(value, dict):
                self.assertFalse(any(k.startswith("_") for k in value))
                self.assertFalse({"rng_state", "random_state", "seed"} & value.keys())
                for child in value.values():
                    assert_no_internal(child)
            elif isinstance(value, list):
                for child in value:
                    assert_no_internal(child)

        for terminal in (False, True):
            with self.subTest(terminal=terminal):
                public = (game.terminal_public_state if terminal else game.public_state)(state, participants)
                assert_no_internal(public)
                self.assertTrue(all("jail_cards" not in p for p in public["players"]))
                self.assertEqual([p["cash"] for p in public["players"]], [1500] * 4)
                self.assertEqual(len(public["tiles"]), 40)
                if terminal:
                    self.assertEqual(public["phase"], "finished")
                    self.assertEqual(public["legal_actions"], [])
                for i, viewer in enumerate(participants):
                    private = game.private_state(state, viewer, participants)
                    assert_no_internal(private)
                    self.assertEqual(private["jail_cards"], 1 if i == 0 else 0)
                    self.assertTrue(all("jail_cards" not in p for p in private["decision_context"]["players"]))
        self.assertEqual(state, before, "projection must not alter persisted decks or held cards")


if __name__ == "__main__":
    unittest.main()

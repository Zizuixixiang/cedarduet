import unittest

from app.games import GAMES
from app.games.base import GamePlugin


# Final hidden-information audit. Keeping every catalog entry in exactly one
# bucket makes a newly added game fail this test until its terminal policy has
# been reviewed instead of silently inheriting an unsuitable reveal rule.
FULL_TERMINAL_REVIEW_REASONS = {
    "banqi": "unflipped piece identities become reviewable only after the room is terminal",
    "blackjack": "the dealer hole is hidden during play and revealed after settlement or room termination",
    "doudizhu": "opponent hands stay private during play and remaining hands are shown at terminal",
    "gandengyan": "opponent hands stay private during play and remaining hands are shown at terminal",
    "guandan": "opponent hands stay private during play and remaining hands are shown at terminal",
    "junqi": "unrevealed ranks stay private during play and the terminal board is reviewable",
    "liars_dice": "current dice stay private during bidding and terminal dice are reviewable",
    "mahjong": "concealed hands and concealed-kong faces become reviewable only at terminal",
    "train_cards": "future personal pile order stays private during play and is reviewable at terminal",
    "uno": "opponent hands stay private during play and remaining hands are shown at terminal",
}

RULE_SCOPED_REVEAL_REASONS = {
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

    def test_poker_muck_games_keep_rule_scoped_public_projection(self):
        for game_type in RULE_SCOPED_REVEAL_REASONS:
            with self.subTest(game_type=game_type):
                self.assertIs(
                    type(GAMES[game_type]).terminal_public_state,
                    GamePlugin.terminal_public_state,
                )


if __name__ == "__main__":
    unittest.main()

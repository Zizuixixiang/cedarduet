import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
GAMES = ROOT / "app" / "static" / "games"
PUBLIC_STYLES = (ROOT / "app" / "static" / "styles.css").read_text(
    encoding="utf-8"
)
APP_SCRIPT = (ROOT / "app" / "static" / "app.js").read_text(encoding="utf-8")
HTML = (ROOT / "app" / "static" / "index.html").read_text(encoding="utf-8")


def css_rule(source: str, selector: str) -> str:
    match = re.search(rf"{re.escape(selector)}\s*\{{([^}}]*)\}}", source)
    if match is None:
        raise AssertionError(f"missing CSS rule: {selector}")
    return match.group(1)


class MultiplayerUiRegressionTests(unittest.TestCase):
    def test_long_board_wrappers_do_not_inherit_the_generic_square_or_clip(self):
        long_boards = (
            "texas_holdem",
            "train_cards",
            "gandengyan",
            "zhajinhua",
            "guandan",
            "doudizhu",
            "aeroplane_chess",
        )
        for game_type in long_boards:
            with self.subTest(game_type=game_type):
                source = (GAMES / f"{game_type}.css").read_text(encoding="utf-8")
                rule = css_rule(source, f".board.{game_type}")
                self.assertIn("aspect-ratio: auto;", rule)
                self.assertIn("height: auto;", rule)
                self.assertIn("max-width: 100%;", rule)
                self.assertIn("overflow: visible;", rule)

        aeroplane = (GAMES / "aeroplane_chess.css").read_text(encoding="utf-8")
        self.assertIn("aspect-ratio: 1;", css_rule(aeroplane, ".aeroplane-board-shell"))
        chinese_checkers = (GAMES / "chinese_checkers.css").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            "aspect-ratio: auto;",
            css_rule(chinese_checkers, ".board.chinese_checkers.multiplayer-board"),
        )
        self.assertIn(
            "aspect-ratio: .88 / 1;",
            css_rule(chinese_checkers, ".board.chinese_checkers .cc-playfield"),
        )

    def test_mahjong_layout_class_is_really_applied_and_mobile_height_is_content_driven(self):
        script = (GAMES / "mahjong.js").read_text(encoding="utf-8")
        styles = (GAMES / "mahjong.css").read_text(encoding="utf-8")
        self.assertIn('context.board.classList.add("mahjong-board-layout")', script)
        outer = css_rule(styles, ".mahjong-board-layout")
        self.assertIn("aspect-ratio: auto;", outer)
        self.assertIn("height: auto;", outer)
        self.assertIn("overflow: visible;", outer)
        mobile = styles[styles.index("@media (max-width: 375px)") :]
        self.assertIn("min-height: 0;", mobile)
        self.assertIn("grid-template-rows: auto minmax(230px, auto) auto auto;", mobile)

    def test_uno_and_blackjack_own_their_private_state_presentation(self):
        for game_type in ("uno", "blackjack"):
            with self.subTest(game_type=game_type):
                script = (GAMES / f"{game_type}.js").read_text(encoding="utf-8")
                self.assertIn("ownsPrivateStatePresentation: true", script)
        self.assertIn("renderer.ownsPrivateStatePresentation === true", APP_SCRIPT)

    def test_multiplayer_chat_stays_after_private_and_action_regions(self):
        stage = HTML[HTML.index('<section class="battle-stage') : HTML.index("historyDrawerTab")]
        self.assertLess(stage.index('class="board-zone"'), stage.index('id="privateStatePanel"'))
        self.assertLess(stage.index('id="privateStatePanel"'), stage.index('id="recentChatFeed"'))
        self.assertLess(stage.index('id="recentChatFeed"'), stage.index('id="chatInput"'))
        self.assertIn('id="gameChatArea" class="game-chat-area"', stage)
        self.assertIn('id="chatInput"', stage)
        self.assertIn('feed.classList.toggle("hidden", messages.length === 0)', APP_SCRIPT)
        board_zone = css_rule(PUBLIC_STYLES, ".layout-multiplayer .board-zone")
        self.assertIn("overflow: visible;", board_zone)
        chat_area = css_rule(PUBLIC_STYLES, ".multiplayer-presentation .game-chat-area")
        self.assertIn("overflow: visible;", chat_area)
        composer = css_rule(PUBLIC_STYLES, ".multiplayer-presentation .game-compose")
        self.assertIn("min-width: 0;", composer)


if __name__ == "__main__":
    unittest.main()

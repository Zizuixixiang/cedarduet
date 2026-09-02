import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
GAMES = ROOT / "app" / "static" / "games"
APP_SCRIPT = (ROOT / "app" / "static" / "app.js").read_text(encoding="utf-8")
HTML = (ROOT / "app" / "static" / "index.html").read_text(encoding="utf-8")
STYLES = (ROOT / "app" / "static" / "styles.css").read_text(encoding="utf-8")


def css_rule(source: str, selector: str) -> str:
    match = re.search(rf"{re.escape(selector)}\s*\{{([^}}]*)\}}", source)
    if match is None:
        raise AssertionError(f"missing CSS rule: {selector}")
    return match.group(1)


class MultiplayerUiRegressionTests(unittest.TestCase):
    def test_desktop_chat_rail_is_multiplayer_only_at_every_width(self):
        desktop_start = STYLES.index("@media (min-width: 1024px)")
        wide_start = STYLES.index("@media (min-width: 1280px)", desktop_start)
        desktop_end = wide_start
        desktop = STYLES[desktop_start:desktop_end]
        wide_end = STYLES.index("@media (max-width: 1100px)", wide_start)
        wide = STYLES[wide_start:wide_end]
        mobile = STYLES[STYLES.index("@media (max-width: 599px)") :]

        self.assertIn('class="battle-main-column"', HTML)
        self.assertLess(
            HTML.index('class="battle-main-column"'),
            HTML.index('id="gameChatArea"'),
        )
        self.assertIn(".battle-stage.multiplayer-presentation {", desktop)
        self.assertIn(
            "grid-template-columns: minmax(0, 1fr) "
            "clamp(240px, 23vw, 300px);",
            desktop,
        )
        self.assertIn(".multiplayer-presentation .game-chat-area {", desktop)
        self.assertIn("grid-column: 2;", desktop)
        self.assertIn(".battle-stage.multiplayer-presentation {", wide)
        self.assertIn(
            "grid-template-columns: minmax(0, 1fr) "
            "clamp(280px, 22vw, 320px);",
            wide,
        )
        self.assertNotIn("\n  .battle-stage {", wide)
        self.assertNotIn(".game-chat-area,", wide)
        self.assertNotIn("\n  .game-chat-area {", wide)
        mobile_chat_selectors = [
            selector.strip()
            for selector_group in re.findall(r"([^{}]+)\{", mobile)
            for selector in selector_group.split(",")
            if ".game-chat-area" in selector
        ]
        self.assertTrue(mobile_chat_selectors)
        for selector in mobile_chat_selectors:
            self.assertIn(".multiplayer-presentation", selector)

        render_recent = APP_SCRIPT[
            APP_SCRIPT.index("function renderRecentChat") :
            APP_SCRIPT.index("function renderPlayers")
        ]
        self.assertIn("const multiplayer = isMultiplayerRoom(room);", render_recent)
        self.assertIn(
            "const messages = multiplayer ? recentSpeechEvents(timeline) : [];",
            render_recent,
        )
        self.assertIn('feed.classList.toggle("hidden", !multiplayer);', render_recent)
        self.assertNotIn("desktopChatRail", APP_SCRIPT)
        self.assertNotIn("DESKTOP_GAME_LAYOUT_MEDIA", APP_SCRIPT)

    def test_desktop_board_and_table_limits_are_shared_across_games(self):
        desktop_start = STYLES.index("@media (min-width: 1024px)")
        desktop_end = STYLES.index("@media (min-width: 1280px)", desktop_start)
        desktop = STYLES[desktop_start:desktop_end]

        self.assertIn("width: min(1200px, 100%);", desktop)
        self.assertIn(".battle-main-column .board-frame {", desktop)
        self.assertIn("justify-items: center;", desktop)
        self.assertIn("--desktop-board-max: 500px;", desktop)
        self.assertIn(
            "--desktop-board-viewport-max: "
            "clamp(360px, calc(100vh - 390px), 500px);",
            desktop,
        )
        self.assertIn("--desktop-table-max: 760px;", desktop)
        self.assertIn(".battle-stage .board-frame > .board {", desktop)
        self.assertIn("var(--desktop-board-max)", desktop)
        self.assertIn("var(--desktop-board-viewport-max)", desktop)
        self.assertIn(
            '.battle-stage[data-game-category="card"] '
            ".board-frame > .board {",
            desktop,
        )
        self.assertIn("width: min(100%, var(--desktop-table-max));", desktop)
        self.assertIn(
            '.battle-stage[data-game-category="dice"] '
            ".board-frame > .board {",
            desktop,
        )
        self.assertIn("width: min(100%, 640px);", desktop)
        self.assertIn(
            "$(\"battleStage\").dataset.gameCategory = "
            "roomGameCategory(targetRoom);",
            APP_SCRIPT,
        )
        self.assertIn(".board.gomoku", STYLES)
        self.assertNotIn("610px", css_rule(
            desktop, ".battle-stage .board-frame > .board"
        ))
        for tall_board in ("jungle", "xiangqi", "junqi"):
            with self.subTest(tall_board=tall_board):
                self.assertIn(f".board.{tall_board} {{", desktop)
        for table in (
            ".mahjong-table", ".guandan-table", ".texas-table",
            ".zhajinhua-table",
        ):
            with self.subTest(table=table):
                self.assertIn(table, desktop)
                self.assertIn("min-height:", css_rule(desktop, f".battle-stage {table}"))
        self.assertIn(
            "max-height: clamp(320px, calc(100vh - 560px), 460px);",
            css_rule(desktop, ".battle-stage .yahtzee-scorecard-scroll"),
        )

        mobile = STYLES[STYLES.index("@media (max-width: 599px)") :]
        self.assertNotIn("--desktop-board-max", mobile)
        self.assertNotIn("--desktop-table-max", mobile)
        self.assertNotIn("data-game-category", mobile)

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
        mobile = styles[styles.index("@media (max-width: 600px)") :]
        self.assertIn("min-height: 0;", mobile)
        self.assertIn("grid-template-rows: auto minmax(270px, auto) auto auto;", mobile)

    def test_uno_and_blackjack_own_their_private_state_presentation(self):
        for game_type in ("uno", "blackjack"):
            with self.subTest(game_type=game_type):
                script = (GAMES / f"{game_type}.js").read_text(encoding="utf-8")
                self.assertIn("ownsPrivateStatePresentation: true", script)
        self.assertIn("renderer.ownsPrivateStatePresentation === true", APP_SCRIPT)


if __name__ == "__main__":
    unittest.main()

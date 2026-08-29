import shutil
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "app" / "static" / "games" / "doudizhu.js"
STYLE_PATH = ROOT / "app" / "static" / "games" / "doudizhu.css"
SCRIPT = SCRIPT_PATH.read_text(encoding="utf-8")
STYLES = STYLE_PATH.read_text(encoding="utf-8")
HTML = (ROOT / "app" / "static" / "index.html").read_text(encoding="utf-8")
NODE = shutil.which("node")


class DoudizhuFrontendTests(unittest.TestCase):
    def test_independent_registry_renderer_and_lazy_styles(self):
        for expected in (
            'window.DuelGameUI.register("doudizhu", renderer);',
            'participantPresentation: "embedded"',
            "function renderBoard(context)",
            "function renderControls(context)",
            "usesStandardMoveConfirmation: false",
            "ownsPrivateStatePresentation: true",
            'const STYLE_HREF = "/static/games/doudizhu.css?v=0.1.0";',
            'link.dataset.duelGameStyle = "doudizhu";',
        ):
            self.assertIn(expected, SCRIPT)
        self.assertNotIn("/static/games/doudizhu.js", HTML)
        self.assertNotIn("doudizhu.css", HTML)

    def test_client_only_selects_authoritative_actions_and_handles_ambiguity(self):
        for expected in (
            "context.legalActions",
            "action.action_id",
            "exactSelectedAction(context)",
            "context.helpers.submitMove({...action})",
            'action.action === "bid"',
            'action.action === "pass"',
            "matches.length > 1",
            "selectedActionId",
        ):
            self.assertIn(expected, SCRIPT)
        for forbidden in (
            "function classifyCards", "function canBeat", "RANK_VALUE",
            "BOMB_STRENGTH", "classify_ranks", "legal_rank_plays",
        ):
            self.assertNotIn(forbidden, SCRIPT)

    def test_three_seat_identity_bottom_trick_pass_and_private_hand_are_visible(self):
        for expected in (
            "doudizhu-opponents", "doudizhu-seat", "doudizhu-avatar",
            "roles_by_player", "role-landlord", "farmerPartnerId", "对家",
            "bottom_revealed", "bottom_cards", "doudizhu-trick-cards",
            "pass_player_ids", "doudizhu-hand-scroll", "我的手牌",
        ):
            self.assertIn(expected, SCRIPT + STYLES)
        self.assertIn(".filter((item) => item.player_id !== viewerId)", SCRIPT)
        self.assertIn("renderHand(documentRef, context, shell)", SCRIPT)

    def test_mobile_hand_scroll_has_320_and_375_guards(self):
        for expected in (
            ".doudizhu-hand-scroll {",
            "overflow-x: auto;",
            "touch-action: pan-x;",
            "overscroll-behavior-x: contain;",
            "max-width: 100%;",
            "min-height: 44px;",
            "@media (max-width: 375px)",
            "@media (max-width: 320px)",
            "button.doudizhu-card:focus-visible",
        ):
            self.assertIn(expected, STYLES)

    def test_cards_are_css_text_not_images_or_emoji_faces(self):
        self.assertIn("const SUIT_TEXT", SCRIPT)
        self.assertIn("doudizhu-card-rank", SCRIPT + STYLES)
        self.assertIn("doudizhu-card-suit", SCRIPT + STYLES)
        self.assertIn("doudizhu-card-back", SCRIPT + STYLES)
        self.assertNotIn("<img", SCRIPT.lower())
        self.assertNotIn("url(", STYLES.lower())
        for emoji in ("🃏", "🎴", "♠️", "♥️", "♣️", "♦️", "💣", "🔥"):
            self.assertNotIn(emoji, SCRIPT + STYLES)

    @unittest.skipUnless(NODE, "node is required for renderer syntax check")
    def test_renderer_parses_in_node(self):
        completed = subprocess.run(
            [NODE, "--check", str(SCRIPT_PATH)],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)


if __name__ == "__main__":
    unittest.main()

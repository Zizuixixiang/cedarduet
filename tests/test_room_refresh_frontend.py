import re
import shutil
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (ROOT / "app" / "static" / "app.js").read_text(encoding="utf-8")
STYLES = (ROOT / "app" / "static" / "styles.css").read_text(encoding="utf-8")
NODE = shutil.which("node")


def function_source(name: str) -> str:
    match = re.search(rf"(?:async\s+)?function\s+{re.escape(name)}\(", SCRIPT)
    if match is None:
        raise AssertionError(f"missing function {name}")
    following = re.search(r"\n(?:async\s+)?function\s+", SCRIPT[match.end():])
    if following is None:
        listener_start = SCRIPT.find('\n\n$("', match.end())
        end = len(SCRIPT) if listener_start < 0 else listener_start
    else:
        end = match.end() + following.start()
    return SCRIPT[match.start():end]


def style_rule(selector: str, source: str = STYLES) -> str:
    start = source.index(f"{selector} {{")
    end = source.index("}", start)
    return source[start:end]


class RoomRefreshFrontendContractTests(unittest.TestCase):
    def test_room_wait_chain_uses_revision_cursor_and_cancellable_generation(self):
        refresh = function_source("refreshRoom")
        submit = function_source("submitMove")
        acknowledgement = function_source("acknowledgeLiarsRound")
        opener = function_source("openRoom")
        poll = function_source("pollRoom")
        starter = function_source("startRoomPolling")
        stopper = function_source("stopPolling")

        self.assertIn("?wait=true&after_revision=", refresh)
        self.assertIn("signal: controller.signal", refresh)
        self.assertGreaterEqual(refresh.count("roomSyncIsCurrent("), 4)
        self.assertIn("wait: shouldLongPollRoom()", poll)
        self.assertIn("scheduleRoomPoll(", poll)
        self.assertIn("shouldLongPollRoom()", starter)
        self.assertNotIn("setInterval", starter)
        self.assertIn("stopPolling();", submit)
        self.assertGreaterEqual(submit.count("roomSyncIsCurrent("), 3)
        self.assertIn("startRoomPolling();", acknowledgement)
        self.assertIn("new AbortController()", opener)
        self.assertIn("roomRequestController.abort()", stopper)
        self.assertIn("roomSyncGeneration += 1", stopper)

    @unittest.skipUnless(NODE, "node is required for polling lifecycle tests")
    def test_visible_room_chain_cancellation_and_rate_limit_recovery(self):
        subprocess.run(
            [NODE, str(ROOT / "tests" / "room_refresh_lifecycle.cjs")],
            check=True, cwd=ROOT,
        )


class SharedTurnLayoutContractTests(unittest.TestCase):
    def test_turn_and_message_keep_one_box_model_across_turn_states(self):
        turn = style_rule("#turn")
        current = style_rule("#turn.my-turn")
        for expected in (
            "height: 29px",
            "min-height: 29px",
            "padding: 3px 9px",
            "border: 2px solid transparent",
            "font-size: 15px",
        ):
            self.assertIn(expected, turn)
        for geometry in (
            "height:", "min-height:", "padding:", "border:", "font-size:",
            "font-weight:", "letter-spacing:",
        ):
            self.assertNotIn(geometry, current)
        self.assertIn("border-color:", current)

        message = style_rule("#gameMessage")
        self.assertIn("min-height: 35px", message)
        self.assertIn("padding: 7px 12px", message)
        self.assertIn("border-left: 4px solid transparent", message)
        self.assertIn("#gameMessage:empty {", STYLES)
        self.assertIn("display: none", style_rule("#gameMessage:empty"))
        render = function_source("renderGame")
        self.assertIn('"embedded-action-feedback"', render)
        self.assertIn("renderer.usesEmbeddedActionFeedback === true", render)

    def test_mobile_participant_detail_and_common_breakpoints_stay_stable(self):
        participant = style_rule(".room-participant")
        self.assertIn("height: 60px", participant)
        self.assertIn("min-height: 60px", participant)
        self.assertIn("grid-template-rows: 26px 14px", participant)
        badge = function_source("createParticipantBadge")
        self.assertIn('detail.classList.toggle("hidden", !fragments.length)', badge)

        mobile = STYLES[STYLES.index("@media (max-width: 599px)"):]
        self.assertIn("#turn { padding: 3px 7px; font-size: 14px; }", mobile)
        self.assertIn(
            "#gameMessage { min-height: 31px; padding: 6px 9px; font-size: 13px; }",
            mobile,
        )
        self.assertIn("@media (max-width: 340px)", mobile)
        for viewport in (320, 375, 430):
            with self.subTest(viewport=viewport):
                self.assertLessEqual(viewport, 599)


if __name__ == "__main__":
    unittest.main()

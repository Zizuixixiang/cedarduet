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
        self.assertIn("wait: true", poll)
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
    def test_external_revisions_chain_serially_and_human_turn_does_not_poll(self):
        functions = "\n".join(function_source(name) for name in (
            "roomSyncIsCurrent",
            "shouldLongPollRoom",
            "scheduleRoomPoll",
            "pollRoom",
            "startRoomPolling",
            "stopPolling",
        ))
        harness = f"""
const assert = require("node:assert/strict");
let room = {{room_id: "ROOM1", revision: 1, status: "playing", current_player_id: "npc:1"}};
let pollTimer = null;
let roomSyncGeneration = 0;
let roomRequestController = null;
const timers = [];
const refreshCalls = [];
const revisions = [
  {{revision: 2, current_player_id: "npc:2"}},
  {{revision: 3, current_player_id: "npc:3"}},
  {{revision: 4, current_player_id: "human-1"}},
];
const setTimeout = (callback, delay) => {{
  const timer = {{callback, delay, cancelled: false}};
  timers.push(timer);
  return timer;
}};
const clearTimeout = (timer) => {{ timer.cancelled = true; }};
const isTerminal = (target) => ["finished", "archived"].includes(target.status);
const canHumanMove = () => (
  room.status === "playing" && room.current_player_id === "human-1"
);
async function refreshRoom(options) {{
  refreshCalls.push({{...options, afterRevision: room.revision}});
  const next = revisions.shift();
  room = {{...room, ...next}};
  return {{stale: false, retryAfterMs: 0}};
}}
{functions}

(async () => {{
  startRoomPolling();
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(refreshCalls.length, 1);
  assert.equal(refreshCalls[0].wait, true);
  assert.equal(refreshCalls[0].afterRevision, 1);

  while (timers.some((timer) => !timer.cancelled)) {{
    const timer = timers.find((candidate) => !candidate.cancelled);
    timer.cancelled = true;
    timer.callback();
    await new Promise((resolve) => setImmediate(resolve));
  }}
  assert.deepEqual(refreshCalls.map((call) => call.afterRevision), [1, 2, 3]);
  assert.equal(room.revision, 4);
  assert.equal(canHumanMove(), true);
  assert.equal(timers.filter((timer) => !timer.cancelled).length, 0);

  const beforeHumanStart = refreshCalls.length;
  startRoomPolling();
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(refreshCalls.length, beforeHumanStart);

  let aborted = false;
  room.current_player_id = "npc:4";
  roomRequestController = {{abort() {{ aborted = true; }}}};
  const oldGeneration = roomSyncGeneration;
  stopPolling();
  assert.equal(aborted, true);
  assert.ok(roomSyncGeneration > oldGeneration);

  const callsBeforeRoomSwitch = refreshCalls.length;
  room = {{...room, room_id: "ROOM2"}};
  await pollRoom(oldGeneration, "ROOM1");
  assert.equal(refreshCalls.length, callsBeforeRoomSwitch);
  scheduleRoomPoll(roomSyncGeneration, "ROOM2", 3000);
  assert.equal(timers[timers.length - 1].delay, 3000);
}})().catch((error) => {{ console.error(error); process.exitCode = 1; }});
"""
        subprocess.run([NODE, "-e", harness], check=True, cwd=ROOT)


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
        self.assertIn("display: block", style_rule("#gameMessage:empty"))
        self.assertIn(
            "#gameMessage.embedded-action-feedback:empty { display: none; }",
            STYLES,
        )
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

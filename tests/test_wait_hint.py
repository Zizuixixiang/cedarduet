import shutil
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (ROOT / "app" / "static" / "app.js").read_text(encoding="utf-8")
NODE = shutil.which("node")


def function_source(name: str) -> str:
    start = SCRIPT.index(f"function {name}(")
    end = SCRIPT.find("\nfunction ", start + 1)
    return SCRIPT[start:] if end < 0 else SCRIPT[start:end]


CONSTANTS = "\n".join(
    line
    for line in SCRIPT.splitlines()
    if line.startswith("const WAIT_HINT_")
)
FUNCTIONS = "\n".join(
    function_source(name)
    for name in (
        "localDateString",
        "waitHintHumanId",
        "waitHintPreferenceKey",
        "readWaitHintPreference",
        "shouldShowWaitModeHint",
        "saveWaitHintPreference",
        "hideWaitModeModal",
        "closeWaitModeModal",
        "showWaitModeModalOnce",
        "isTerminal",
    )
)
HARNESS = f"""
const assert = require("node:assert/strict");
let room = null;
let visibleWaitModalRoomId = null;
const waitHintShownRooms = new Set();
const modal = {{
  hidden: true,
  ariaHidden: "true",
  classList: {{
    add(name) {{ if (name === "hidden") modal.hidden = true; }},
    remove(name) {{ if (name === "hidden") modal.hidden = false; }},
  }},
  setAttribute(name, value) {{
    if (name === "aria-hidden") modal.ariaHidden = value;
  }},
}};
const dismissButton = {{
  focused: false,
  focus() {{ dismissButton.focused = true; }},
}};
const $ = (id) => {{
  if (id === "waitModeModal") return modal;
  if (id === "dismissWaitModeModalButton") return dismissButton;
  assert.fail(`unexpected element id: ${{id}}`);
}};
function makeStorage() {{
  const values = new Map();
  return {{
    getItem(key) {{ return values.has(key) ? values.get(key) : null; }},
    setItem(key, value) {{ values.set(key, value); }},
    values,
  }};
}}
{CONSTANTS}
{FUNCTIONS}
"""


@unittest.skipUnless(NODE, "node is required for frontend behavior tests")
class WaitModeModalBehaviorTests(unittest.TestCase):
    def run_javascript(self, assertions: str) -> None:
        completed = subprocess.run(
            [NODE, "-e", f"{HARNESS}\n{assertions}"],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(
            completed.returncode,
            0,
            f"JavaScript assertion failed:\n{completed.stderr}",
        )

    def test_today_close_uses_local_date_and_expires_next_day(self):
        self.run_javascript(
            """
const storage = makeStorage();
const first = {room_id: "ROOM0001", status: "playing", human_player_id: "human-a"};
const second = {room_id: "ROOM0002", status: "playing", human_player_id: "human-a"};
assert.equal(localDateString(new Date(2026, 7, 25, 23, 59)), "2026-08-25");
closeWaitModeModal(false, first, storage, "2026-08-25");
assert.equal(storage.getItem(waitHintPreferenceKey(first)), "2026-08-25");
assert.equal(shouldShowWaitModeHint(second, storage, "2026-08-25"), false);
assert.equal(shouldShowWaitModeHint(second, storage, "2026-08-26"), true);
"""
        )

    def test_permanent_close_remains_closed_on_later_dates(self):
        self.run_javascript(
            """
const storage = makeStorage();
const target = {room_id: "ROOM0001", status: "playing", human_player_id: "human-a"};
closeWaitModeModal(true, target, storage, "2026-08-25");
assert.equal(storage.getItem(waitHintPreferenceKey(target)), "forever");
assert.equal(shouldShowWaitModeHint(target, storage, "2026-08-25"), false);
assert.equal(shouldShowWaitModeHint(target, storage, "2027-01-01"), false);
"""
        )

    def test_same_room_does_not_repeat_but_new_room_does(self):
        self.run_javascript(
            """
const storage = makeStorage();
const first = {room_id: "ROOM0001", status: "playing", human_player_id: "human-a"};
const second = {room_id: "ROOM0002", status: "playing", human_player_id: "human-a"};
assert.equal(showWaitModeModalOnce(first, storage, "2026-08-25"), true);
hideWaitModeModal();
assert.equal(showWaitModeModalOnce(first, storage, "2026-08-26"), false);
assert.equal(showWaitModeModalOnce(second, storage, "2026-08-26"), true);
assert.equal(waitHintShownRooms.size, 2);
"""
        )

    def test_modal_stays_visible_until_dismissed_without_saving_preference(self):
        self.run_javascript(
            """
const storage = makeStorage();
const target = {room_id: "ROOM0001", status: "playing", human_player_id: "human-a"};
assert.equal(showWaitModeModalOnce(target, storage, "2026-08-25"), true);
assert.equal(modal.hidden, false);
assert.equal(modal.ariaHidden, "false");
assert.equal(dismissButton.focused, true);
hideWaitModeModal();
assert.equal(modal.hidden, true);
assert.equal(modal.ariaHidden, "true");
assert.equal(readWaitHintPreference(target, storage), null);
assert.equal(showWaitModeModalOnce(target, storage, "2026-08-25"), false);
"""
        )

    def test_terminal_transition_hides_an_existing_hint(self):
        self.run_javascript(
            """
const storage = makeStorage();
const active = {room_id: "ROOM0001", status: "playing", human_player_id: "human-a"};
const terminal = {room_id: "ROOM0001", status: "finished", human_player_id: "human-a"};
assert.equal(showWaitModeModalOnce(active, storage, "2026-08-25"), true);
assert.equal(modal.hidden, false);
assert.equal(showWaitModeModalOnce(terminal, storage, "2026-08-25"), false);
assert.equal(modal.hidden, true);
"""
        )

    def test_preferences_and_visit_tracking_are_isolated_by_account(self):
        self.run_javascript(
            """
const storage = makeStorage();
const firstUser = {room_id: "ROOM0001", status: "playing", human_player_id: "human-a"};
const secondUser = {room_id: "ROOM0001", status: "playing", human_player_id: "human-b"};
closeWaitModeModal(true, firstUser, storage, "2026-08-25");
assert.notEqual(waitHintPreferenceKey(firstUser), waitHintPreferenceKey(secondUser));
assert.equal(shouldShowWaitModeHint(firstUser, storage, "2026-08-25"), false);
assert.equal(showWaitModeModalOnce(secondUser, storage, "2026-08-25"), true);
"""
        )

    def test_participant_identity_and_browser_fallback_keys(self):
        self.run_javascript(
            """
const participantRoom = {
  room_id: "ROOM0001",
  status: "playing",
  participants: [{role: "human", player_id: "human-from-participant"}],
};
const legacyRoom = {room_id: "ROOM0002", status: "playing"};
assert.equal(
  waitHintPreferenceKey(participantRoom),
  "duel:wait-mode-hint:human-from-participant"
);
assert.equal(waitHintPreferenceKey(legacyRoom), "duel:wait-mode-hint:browser");
"""
        )


if __name__ == "__main__":
    unittest.main()

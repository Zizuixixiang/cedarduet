import shutil
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "app" / "static" / "index.html").read_text(encoding="utf-8")
SCRIPT = (ROOT / "app" / "static" / "app.js").read_text(encoding="utf-8")
STYLES = (ROOT / "app" / "static" / "styles.css").read_text(encoding="utf-8")
NODE = shutil.which("node")


def function_source(name: str) -> str:
    start = SCRIPT.index(f"function {name}(")
    if SCRIPT[start - 6:start] == "async ":
        start -= 6
    candidates = (
        SCRIPT.find("\nfunction ", start + 1),
        SCRIPT.find("\nasync function ", start + 1),
    )
    valid_candidates = [candidate for candidate in candidates if candidate >= 0]
    end = min(valid_candidates) if valid_candidates else len(SCRIPT)
    return SCRIPT[start:end]


def css_rule(selector: str) -> str:
    start = STYLES.index(f"{selector} {{")
    end = STYLES.index("}", start) + 1
    return STYLES[start:end]


class ResultModalLayoutTests(unittest.TestCase):
    def test_terminal_modal_uses_one_checkbox_and_one_two_button_row(self):
        modal = HTML[
            HTML.index('<div id="resultModal"'):
            HTML.index('<div id="toast"')
        ]
        retention_start = modal.index('<label class="result-preserve-option"')
        retention_option = modal[
            retention_start:modal.index("</label>", retention_start)
        ]
        followup_start = modal.index(
            '<div class="result-modal-actions result-action-row"'
        )
        followup_row = modal[
            followup_start:modal.index("</div>", followup_start)
        ]

        self.assertIn('id="resultPreserveCheckbox" type="checkbox"', retention_option)
        self.assertIn("保留本局棋谱和聊天记录", retention_option)
        self.assertIn('id="resultRetentionHint"', retention_option)
        self.assertIn("终局 7 天后自动删除", retention_option)
        self.assertNotIn("preserveResultButton", modal)
        self.assertNotIn("skipPreserveButton", modal)
        self.assertLess(
            followup_row.index("rematchButton"),
            followup_row.index("finishGameButton"),
        )
        self.assertEqual(followup_row.count("<button"), 2)
        self.assertIn("再来一局", followup_row)
        self.assertIn("结束对局", followup_row)

    def test_modal_stays_compact_and_two_columns_on_mobile(self):
        desktop = css_rule(".result-action-row")
        self.assertIn("grid-template-columns: 1fr 1fr", desktop)
        self.assertIn(
            ".result-action-row { grid-template-columns: "
            "minmax(0, 1fr) minmax(0, 1fr); gap: 7px; }",
            STYLES,
        )
        self.assertNotIn(
            ".result-modal-actions { grid-template-columns: 1fr; }",
            STYLES,
        )
        self.assertIn("min-height: 42px", css_rule(".result-action-row .pixel-btn"))
        self.assertIn("white-space: normal", css_rule(".result-action-row .pixel-btn"))
        self.assertIn("min-height: 52px", STYLES)
        self.assertIn("font-size: 10px", STYLES)

    @unittest.skipUnless(NODE, "node is required for frontend behavior tests")
    def test_retention_checkbox_syncs_and_rolls_back_on_api_failure(self):
        functions = "\n".join(
            (
                function_source("isTerminal"),
                function_source("retentionTextFor"),
                function_source("retentionDeadlineTitle"),
                function_source("updateRoomPreservation"),
                function_source("syncResultPreservationChoice"),
                function_source("changeResultPreservation"),
                function_source("renderRetention"),
            )
        )
        harness = f"""
const assert = require("node:assert/strict");
let room = {{room_id: "ROOM1", status: "finished", preserved: false}};
let shouldFail = true;
let requested = null;
const elements = {{
  roomRetentionStatus: {{textContent: "", title: ""}},
  togglePreserveButton: {{textContent: "", disabled: false}},
  resultPreserveCheckbox: {{checked: false, disabled: false}},
  resultRetentionHint: {{textContent: ""}},
  resultModalMessage: {{textContent: ""}},
}};
const $ = (id) => elements[id];
const request = async (path, options) => {{
  requested = {{path, options}};
  if (shouldFail) throw new Error("网络失败");
  return {{
    room: {{room_id: "ROOM1", status: "finished", preserved: true}},
    message: "已保留",
    timeline: [],
  }};
}};
const renderGame = (nextRoom) => {{ room = nextRoom; }};
const loadIdentity = async () => {{}};
const toast = () => {{}};
{functions}
(async () => {{
  renderRetention(room);
  assert.equal(elements.resultPreserveCheckbox.checked, false);
  assert.equal(elements.resultRetentionHint.textContent, "终局 7 天后自动删除");

  elements.resultPreserveCheckbox.checked = true;
  await changeResultPreservation();
  assert.equal(elements.resultPreserveCheckbox.checked, false);
  assert.equal(elements.resultPreserveCheckbox.disabled, false);
  assert.equal(elements.resultRetentionHint.textContent, "终局 7 天后自动删除");
  assert.equal(elements.resultModalMessage.textContent, "网络失败");

  shouldFail = false;
  elements.resultPreserveCheckbox.checked = true;
  await changeResultPreservation();
  assert.equal(requested.path, "/api/rooms/ROOM1/retention");
  assert.deepEqual(JSON.parse(requested.options.body), {{preserved: true}});
  assert.equal(elements.resultPreserveCheckbox.checked, true);
  assert.equal(elements.resultRetentionHint.textContent, "已保留，不会自动删除");
  assert.equal(elements.resultModalMessage.textContent, "");
}})().catch((error) => {{ console.error(error); process.exit(1); }});
"""
        completed = subprocess.run(
            [NODE, "-e", harness],
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


@unittest.skipUnless(NODE, "node is required for frontend rendering tests")
class LastMoveMarkerTests(unittest.TestCase):
    def test_authoritative_last_move_marks_exact_target_and_flashes_once(self):
        functions = "\n".join(
            (
                function_source("latestMoveEvent"),
                function_source("authoritativeLastMove"),
                function_source("renderLastMoveMarker"),
            )
        )
        harness = f"""
const assert = require("node:assert/strict");
let lastMoveMarkerKey = null;
let room = {{
  room_id: "C4ROOM",
  revision: 12,
  game_type: "connect4",
  board_state: {{last_move: {{row: 5, col: 3, mark: "X"}}}},
}};
const timeline = [
  {{sequence: 1, event_type: "move", revision_at_send: 12, move: {{col: 3}}}},
  {{sequence: 2, event_type: "message", revision_at_send: 12, text: "好棋"}},
];
class Target {{
  constructor() {{
    this.classes = new Set();
    this.classList = {{add: (name) => this.classes.add(name)}};
    this.children = [];
    this.ariaLabel = "第 4 列棋子";
  }}
  appendChild(child) {{ this.children.push(child); }}
}}
const document = {{
  createElement: () => ({{className: "", setAttribute() {{}}}}),
}};
let target = new Target();
let selector = "";
const board = {{querySelector: (value) => {{ selector = value; return target; }}}};
{functions}
assert.deepEqual(
  authoritativeLastMove(room, timeline),
  {{row: 5, col: 3, orientation: null, revision: 12}},
);
renderLastMoveMarker(board, timeline);
assert.match(selector, /data-move-row="5"/);
assert.match(selector, /data-move-col="3"/);
assert.equal(target.classes.has("last-move-target"), true);
assert.equal(target.classes.has("last-move-fresh"), true);
assert.equal(target.children[0].className, "last-move-marker");
assert.match(target.ariaLabel, /上一手/);

target = new Target();
renderLastMoveMarker(board, timeline);
assert.equal(target.classes.has("last-move-target"), true);
assert.equal(target.classes.has("last-move-fresh"), false);

room.board_state.last_move = {{row: 4, col: 3, mark: "O"}};
room.revision = 13;
target = new Target();
renderLastMoveMarker(board, [
  ...timeline,
  {{sequence: 3, event_type: "move", revision_at_send: 13, move: {{col: 3}}}},
]);
assert.match(selector, /data-move-row="4"/);
assert.equal(target.classes.has("last-move-fresh"), true);

room = {{
  room_id: "JUNGLE",
  revision: 4,
  game_type: "jungle",
  board_state: {{last_move: {{from_row: 6, from_col: 0, to_row: 5, to_col: 0}}}},
}};
assert.deepEqual(
  authoritativeLastMove(room, []),
  {{row: 5, col: 0, orientation: null, revision: 4}},
);
const dots = authoritativeLastMove(
  {{
    room_id: "DOTS",
    revision: 7,
    game_type: "dots_boxes",
    board_state: {{last_move: {{orientation: "v", row: 2, col: 4}}}},
  }},
  [],
);
assert.deepEqual(dots, {{row: 2, col: 4, orientation: "v", revision: 7}});
const gomoku = authoritativeLastMove(
  {{
    room_id: "GOMOKU",
    revision: 9,
    game_type: "gomoku",
    board_state: {{last_move: {{row: 7, col: 8, mark: "X"}}}},
  }},
  [],
);
assert.deepEqual(gomoku, {{row: 7, col: 8, orientation: null, revision: 9}});
const fallback = authoritativeLastMove(
  {{room_id: "TTT", revision: 2, game_type: "tictactoe", board_state: {{}}}},
  [{{event_type: "move", revision_at_send: 2, move: {{row: 1, col: 2}}}}],
);
assert.deepEqual(fallback, {{row: 1, col: 2, orientation: null, revision: 2}});
const noGuess = authoritativeLastMove(
  {{room_id: "C4", revision: 2, game_type: "connect4", board_state: {{}}}},
  [{{event_type: "move", revision_at_send: 2, move: {{col: 2}}}}],
);
assert.equal(noGuess, null);
"""
        completed = subprocess.run(
            [NODE, "-e", harness],
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

    def test_marker_contract_is_small_persistent_and_reduced_motion_safe(self):
        marker = css_rule(".last-move-marker")
        self.assertIn("width: 8px", marker)
        self.assertIn("height: 8px", marker)
        self.assertIn("border: 2px solid", marker)
        self.assertIn("animation: last-move-flash .3s", STYLES)
        self.assertIn("@media (prefers-reduced-motion: reduce)", STYLES)
        self.assertIn("cell.dataset.moveRow", SCRIPT)
        self.assertIn("edge.dataset.moveOrientation", SCRIPT)


class MobileOperationSizingTests(unittest.TestCase):
    def test_mobile_chat_and_action_controls_stay_compact_and_tappable(self):
        self.assertIn(
            ".chat-compose input { min-height: 38px; padding: 6px 9px; }",
            STYLES,
        )
        self.assertIn("min-width: 64px", STYLES)
        self.assertIn("min-height: 40px", STYLES)
        self.assertIn(".game-toolbar-actions {", STYLES)
        self.assertIn("grid-template-columns: repeat(3, minmax(0, 1fr))", STYLES)
        self.assertIn(".game-toolbar-actions .pixel-btn {", STYLES)
        self.assertNotIn(".game-actions", STYLES)
        self.assertLess(HTML.index('id="refreshButton"'), HTML.index('id="chatInput"'))
        self.assertLess(HTML.index('id="resignButton"'), HTML.index('id="chatInput"'))
        self.assertIn(
            ".move-confirm span { flex: 1; align-self: center; font-size: 12px; }",
            STYLES,
        )


@unittest.skipUnless(NODE, "node is required for frontend rendering tests")
class TimelineRenderingTests(unittest.TestCase):
    def test_chat_move_and_result_events_use_distinct_rendering_branches(self):
        functions = "\n".join(
            (
                function_source("timelineEventKind"),
                function_source("createChatTimelineItem"),
                function_source("renderTimeline"),
            )
        )
        harness = f"""
const assert = require("node:assert/strict");
class Element {{
  constructor(tag) {{
    this.tag = tag;
    this.className = "";
    this.textContent = "";
    this.children = [];
    this.attributes = {{}};
    this.scrollTop = 0;
    this.scrollHeight = 100;
  }}
  replaceChildren(...children) {{ this.children = children; }}
  append(...children) {{ this.children.push(...children); }}
  appendChild(child) {{ this.children.push(child); }}
  setAttribute(name, value) {{ this.attributes[name] = value; }}
}}
const timelineList = new Element("ol");
const document = {{createElement: (tag) => new Element(tag)}};
const $ = (id) => {{
  assert.equal(id, "timeline");
  return timelineList;
}};
const aiNameFor = () => "小机";
{functions}
const events = [
  {{sequence: 1, event_type: "message", sender_role: "human", sender: {{role: "human", name: "阿甲"}}, text: "你好"}},
  {{sequence: 2, event_type: "message", sender_role: "ai", sender: {{role: "ai", name: "小机"}}, text: "轮到我想想"}},
  {{sequence: 3, event_type: "move", sender_role: "ai", sender: {{role: "ai", name: "小机"}}, move_label: "(0, 1)", text: "我走这里"}},
  {{sequence: 4, event_type: "resign", sender_role: "human", sender: {{role: "human", name: "阿甲"}}, text: "这局认输"}},
  {{sequence: 5, event_type: "result", sender_role: "system", sender: {{role: "system", name: "裁判"}}, text: "小机获胜", display_text: "小机获胜"}},
];
renderTimeline(events);
assert.equal(timelineList.children.length, 6);
const [humanChat, aiChat, move, moveComment, resign, result] = timelineList.children;
for (const chat of [humanChat, aiChat]) {{
  assert.match(chat.className, /history-chat-event/);
  assert.doesNotMatch(chat.className, /history-move-event|history-result-event/);
  assert.deepEqual(chat.children.map((item) => item.className), ["history-speaker", "history-copy", "history-meta"]);
}}
assert.match(humanChat.className, /human message/);
assert.equal(humanChat.children[0].textContent, "阿甲");
assert.equal(humanChat.children[1].textContent, "你好");
assert.match(aiChat.className, /ai message/);
assert.equal(aiChat.children[0].textContent, "小机");
assert.equal(aiChat.children[1].textContent, "轮到我想想");
assert.match(move.className, /history-move-event/);
assert.equal(move.children[1].className, "history-move-body");
assert.deepEqual(move.children[1].children.map((item) => item.className), ["history-action-label"]);
assert.equal(move.children[1].children[0].textContent, "小机 落 (0, 1)");
assert.match(moveComment.className, /history-chat-event ai move-comment/);
assert.doesNotMatch(moveComment.className, /history-move-event/);
assert.deepEqual(moveComment.children.map((item) => item.className), ["history-speaker", "history-copy", "history-meta"]);
assert.equal(moveComment.children[0].textContent, "小机");
assert.equal(moveComment.children[1].textContent, "我走这里");
assert.equal(moveComment.children[2].textContent, "#3 · 附言");
assert.match(resign.className, /history-result-event/);
assert.match(resign.children[1].textContent, /阿甲 认输/);
assert.match(result.className, /history-result-event/);
assert.equal(result.children[1].textContent, "小机获胜");
assert.equal(timelineEventKind("message"), "chat");
assert.equal(timelineEventKind("move"), "move");
assert.equal(timelineEventKind("resign"), "result");
assert.equal(timelineEventKind("result"), "result");
"""
        completed = subprocess.run(
            [NODE, "-e", harness],
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

    def test_visual_emphasis_increases_from_chat_to_move_to_result(self):
        chat = css_rule(".history-chat-event")
        move = css_rule(".history-move-event")
        result_start = STYLES.index(".history-result-event {\n  padding: 9px;")
        result = STYLES[result_start:STYLES.index("}", result_start) + 1]
        self.assertNotIn("background:", chat)
        self.assertNotIn("border:", chat)
        self.assertIn("display: flex", chat)
        self.assertNotIn("grid-template-columns", chat)
        self.assertIn("background:", move)
        self.assertIn("border: 1px", move)
        self.assertIn("background:", result)
        self.assertIn("border: 2px", result)
        self.assertIn("overflow-wrap: anywhere", css_rule(".history-event p"))

    def test_human_and_ai_speakers_share_one_font_hierarchy(self):
        speaker = css_rule(".history-speaker")
        copy = css_rule(".history-chat-event .history-copy")
        human_override = css_rule(".history-chat-event.human .history-speaker")
        self.assertIn("font-family: system-ui, sans-serif", speaker)
        self.assertIn("font-size: 12px", speaker)
        self.assertIn("font-weight: 700", speaker)
        self.assertIn("font-family: system-ui, sans-serif", copy)
        self.assertIn("font-weight: 400", copy)
        for forbidden in ("font-size", "font-family", "font-weight"):
            self.assertNotIn(forbidden, human_override)
        self.assertIn(
            ".history-speaker { max-width: 92px; font-size: 12px; }",
            STYLES,
        )

    def test_terminal_turn_and_move_hint_never_use_in_progress_copy(self):
        functions = "\n".join(
            (
                function_source("isTerminal"),
                function_source("roomTurnText"),
                function_source("updateMoveConfirmation"),
            )
        )
        render_game = function_source("renderGame")
        self.assertIn('$("turn").textContent = roomTurnText(room)', render_game)
        self.assertIn("isTerminal(room)", render_game)
        harness = f"""
const assert = require("node:assert/strict");
let room = {{status: "finished", turn: "human", ai_player_id: "ai-1"}};
let pendingMove = null;
const elements = {{
  confirmMoveButton: {{disabled: false}},
  selectionHint: {{textContent: ""}},
}};
const $ = (id) => elements[id];
const canHumanMove = () => room.status === "playing" && room.turn === "human";
const turnLabel = () => "轮到你";
{functions}
assert.equal(roomTurnText(room), "对局已结束");
updateMoveConfirmation();
assert.equal(elements.selectionHint.textContent, "对局已结束");
assert.doesNotMatch(elements.selectionHint.textContent, /等待|轮到/);
room = {{status: "archived", turn: "ai", ai_player_id: "ai-1"}};
assert.equal(roomTurnText(room), "对局已归档");
updateMoveConfirmation();
assert.equal(elements.selectionHint.textContent, "对局已归档");
assert.doesNotMatch(elements.selectionHint.textContent, /等待|轮到/);
"""
        completed = subprocess.run(
            [NODE, "-e", harness],
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


if __name__ == "__main__":
    unittest.main()

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
    end = SCRIPT.find("\nfunction ", start + 1)
    return SCRIPT[start:] if end < 0 else SCRIPT[start:end]


def css_rule(selector: str) -> str:
    start = STYLES.index(f"{selector} {{")
    end = STYLES.index("}", start) + 1
    return STYLES[start:end]


class ResultModalLayoutTests(unittest.TestCase):
    def test_terminal_actions_are_two_ordered_two_button_rows(self):
        modal = HTML[
            HTML.index('<div id="resultModal"'):
            HTML.index('<div id="toast"')
        ]
        retention_start = modal.index(
            '<div class="result-retention-actions result-action-row"'
        )
        retention_row = modal[
            retention_start:modal.index("</div>", retention_start)
        ]
        followup_start = modal.index(
            '<div class="result-modal-actions result-action-row"'
        )
        followup_row = modal[
            followup_start:modal.index("</div>", followup_start)
        ]

        self.assertLess(
            retention_row.index("preserveResultButton"),
            retention_row.index("skipPreserveButton"),
        )
        self.assertNotIn("rematchButton", retention_row)
        self.assertIn("保留此对局", retention_row)
        self.assertIn("不保留（7天后删除）", retention_row)
        self.assertLess(
            followup_row.index("rematchButton"),
            followup_row.index("finishGameButton"),
        )
        self.assertNotIn("preserveResultButton", followup_row)
        self.assertIn("再来一局", followup_row)
        self.assertIn("结束对局", followup_row)

    def test_action_rows_remain_two_columns_on_mobile(self):
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
        self.assertIn("white-space: normal", css_rule(".result-action-row .pixel-btn"))


class MobileOperationSizingTests(unittest.TestCase):
    def test_mobile_chat_and_action_controls_stay_compact_and_tappable(self):
        self.assertIn(
            ".chat-compose input { min-height: 38px; padding: 6px 9px; }",
            STYLES,
        )
        self.assertIn("min-width: 64px", STYLES)
        self.assertIn("min-height: 40px", STYLES)
        self.assertIn("width: min(320px, 100%)", STYLES)
        self.assertIn(".game-actions .pixel-btn {", STYLES)
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

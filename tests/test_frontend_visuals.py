import shutil
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (ROOT / "app" / "static" / "app.js").read_text(encoding="utf-8")
STYLES = (ROOT / "app" / "static" / "styles.css").read_text(encoding="utf-8")
HTML = (ROOT / "app" / "static" / "index.html").read_text(encoding="utf-8")
NODE = shutil.which("node")


def function_source(name: str) -> str:
    start = SCRIPT.index(f"function {name}(")
    end = SCRIPT.find("\nfunction ", start + 1)
    return SCRIPT[start:] if end < 0 else SCRIPT[start:end]


class FrontendBoardVisualTests(unittest.TestCase):
    def test_only_tictactoe_renders_raw_marks_as_text(self):
        board_cell = function_source("boardCell")
        self.assertIn(
            'if (room.game_type === "tictactoe") cell.textContent = mark || "";',
            board_cell,
        )
        self.assertEqual(SCRIPT.count("cell.textContent = mark"), 1)
        self.assertNotIn("box.textContent", function_source("renderDotsBoard"))

    def test_board_renderer_dispatches_by_game_type(self):
        renderer = function_source("renderBoard")
        self.assertIn("board.classList.add(room.game_type)", renderer)
        self.assertIn('room.game_type === "dots_boxes" ? 9 : rows', renderer)
        self.assertIn('room.game_type === "dots_boxes" ? 9 : cols', renderer)
        self.assertIn("renderGomokuBoard(board, state)", renderer)
        self.assertIn("renderConnect4Board(board, state)", renderer)
        self.assertIn("renderDotsBoard(board, state)", renderer)
        self.assertIn("renderJungleBoard(board, state)", renderer)

    def test_gomoku_uses_intersections_stones_and_mobile_hit_cells(self):
        renderer = function_source("renderGomokuBoard")
        for edge_class in ("edge-top", "edge-bottom", "edge-left", "edge-right"):
            self.assertIn(edge_class, renderer)
        self.assertIn("star-point", renderer)
        self.assertIn("boardCell(mark", renderer)
        self.assertIn(".board.gomoku .cell::before", STYLES)
        self.assertIn(".board.gomoku .cell::after", STYLES)
        self.assertIn(".cell.edge-left::before { left: 50%; }", STYLES)
        self.assertIn(".cell.edge-top::after { top: 50%; }", STYLES)
        self.assertIn("touch-action: manipulation", STYLES)
        self.assertIn(".board.gomoku { width: min(88vw, 560px);", STYLES)

    def test_othello_and_connect4_use_character_free_discs(self):
        board_cell = function_source("boardCell")
        self.assertIn('["gomoku", "othello", "connect4"]', board_cell)
        self.assertIn('piece.className = "piece"', board_cell)
        self.assertIn(".board.othello .cell.mark-x .piece", STYLES)
        self.assertIn(".board.othello .cell.mark-o .piece", STYLES)
        self.assertIn(".board.connect4 .cell.human-piece .piece", STYLES)
        connect4 = function_source("renderConnect4Board")
        self.assertIn("rowIndex === landingRow", connect4)
        self.assertNotIn("textContent", connect4)

    def test_dots_boxes_ownership_is_visual_and_accessible(self):
        renderer = function_source("renderDotsBoard")
        self.assertIn('box.setAttribute("role", "img")', renderer)
        self.assertIn("格归${ownerDescription(owner)}所有", renderer)
        self.assertIn(".box.owned.human-piece", STYLES)
        self.assertIn(".box.owned.ai-piece", STYLES)
        self.assertIn("--rows: 9", STYLES)
        self.assertIn('content: "●"', STYLES)
        self.assertIn('content: "◆"', STYLES)

    def test_jungle_keeps_beast_names_without_raw_side_marks(self):
        renderer = function_source("renderJungleBoard")
        self.assertIn("JUNGLE_SYMBOLS[beast]", renderer)
        self.assertIn('pieceOwner === humanMark ? "●" : "○"', renderer)
        self.assertNotIn("cell.textContent = piece", renderer)

    def test_multiplayer_roster_is_compact_and_seat_colored(self):
        self.assertIn(".room-participant {", STYLES)
        self.assertIn("width: 128px", STYLES)
        self.assertIn("min-height: 66px", STYLES)
        for seat in range(6):
            self.assertIn(f".seat-{seat} {{ --seat-color:", STYLES)
        self.assertIn(
            ".edge.drawn.participant-piece { background: var(--seat-color); }",
            STYLES,
        )
        self.assertIn(
            ".box.owned.participant-piece { background-color: var(--seat-color); }",
            STYLES,
        )
        roster = function_source("renderParticipantRoster")
        self.assertIn("participant.game_metadata", roster)
        self.assertIn("dice_count: \"剩余骰子\"", roster)
        self.assertIn("score: \"得分\"", roster)
        self.assertIn("▶ 正在行动", roster)
        self.assertNotIn("筹码", roster)

    def test_two_player_rows_remain_and_multiplayer_uses_compact_roster(self):
        self.assertIn('id="opponentRow" class="player-row opponent-row"', HTML)
        self.assertIn('id="humanRow" class="player-row human-row"', HTML)
        players = function_source("renderPlayers")
        self.assertIn('participants.length > 2', players)
        self.assertIn('classList.toggle("hidden", multiplayer)', players)
        self.assertEqual(players.count("renderSpeechBubble"), 2)
        self.assertIn('[["ai", "aiSpeech"], ["human", "humanSpeech"]]', players)
        self.assertIn('bubble: $("sharedSpeech")', players)

    def test_private_state_stays_below_the_table_and_legacy_rows(self):
        stage = HTML[HTML.index('<section class="battle-stage'):HTML.index("historyDrawerTab")]
        self.assertLess(stage.index('id="opponentRow"'), stage.index('id="tableLayout"'))
        self.assertLess(stage.index('id="tableLayout"'), stage.index('id="humanRow"'))
        self.assertLess(stage.index('id="humanRow"'), stage.index('id="privateStatePanel"'))
        self.assertIn("我的信息 · PRIVATE", stage)

    def test_liars_dice_has_public_controls_private_dice_and_revision_guard(self):
        renderer = function_source("renderLiarsDice")
        self.assertIn('challenge.textContent = "质疑上一手"', renderer)
        self.assertIn('chooseBid.textContent = "选择叫点"', renderer)
        self.assertIn("revealed_dice_by_player", renderer)
        private_renderer = function_source("renderPrivateState")
        self.assertIn('key === "dice"', private_renderer)
        self.assertIn("my-dice", private_renderer)
        confirm = SCRIPT[SCRIPT.index("async function confirmMove("):]
        self.assertIn("revision: room.revision", confirm)
        self.assertIn('value="liars_dice"', HTML)


@unittest.skipUnless(NODE, "node is required for multiplayer layout tests")
class MultiplayerTableRenderingTests(unittest.TestCase):
    def run_node(self, harness: str) -> None:
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

    def test_two_through_six_players_apply_stable_layout_classes(self):
        functions = "\n".join((
            function_source("participantLayoutClass"),
            function_source("applyParticipantLayout"),
        ))
        harness = f"""
const assert = require("node:assert/strict");
class ClassList {{
  constructor() {{ this.names = new Set(); }}
  toggle(name, force) {{
    if (force === undefined ? !this.names.has(name) : force) this.names.add(name);
    else this.names.delete(name);
  }}
  contains(name) {{ return this.names.has(name); }}
}}
const elements = {{
  tableLayout: {{className: "", dataset: {{}}}},
  battleStage: {{dataset: {{}}}},
  sharedSpeechSlot: {{classList: new ClassList()}},
}};
const $ = (id) => elements[id];
{functions}
const expected = new Map([
  [2, "layout-duel"],
  [3, "layout-triangle"],
  [4, "layout-corners"],
  [5, "layout-top-row"],
  [6, "layout-top-row"],
]);
for (const [count, layout] of expected) {{
  applyParticipantLayout({{participants: Array.from({{length: count}}, () => ({{}}))}});
  assert.equal(elements.tableLayout.className, `table-layout ${{layout}} count-${{count}}`);
  assert.equal(elements.tableLayout.dataset.playerCount, String(count));
  assert.equal(elements.battleStage.dataset.playerCount, String(count));
  assert.equal(elements.sharedSpeechSlot.classList.contains("hidden"), count === 2);
}}
"""
        self.run_node(harness)

    def test_roster_marks_current_actor_and_keeps_game_values_compact(self):
        functions = "\n".join((
            function_source("participantAvatarFallback"),
            function_source("renderParticipantAvatar"),
            function_source("renderParticipantRoster"),
        ))
        harness = f"""
const assert = require("node:assert/strict");
class ClassList {{
  constructor(owner) {{ this.owner = owner; this.names = new Set(); }}
  reset(value) {{ this.names = new Set(value.split(/\\s+/).filter(Boolean)); }}
  sync() {{ this.owner._className = [...this.names].join(" "); }}
  add(...names) {{ names.forEach((name) => this.names.add(name)); this.sync(); }}
  remove(...names) {{ names.forEach((name) => this.names.delete(name)); this.sync(); }}
  toggle(name, force) {{
    if (force === undefined ? !this.names.has(name) : force) this.names.add(name);
    else this.names.delete(name);
    this.sync();
  }}
  contains(name) {{ return this.names.has(name); }}
  [Symbol.iterator]() {{ return this.names[Symbol.iterator](); }}
}}
class Element {{
  constructor(tag) {{
    this.tag = tag;
    this.children = [];
    this.attributes = {{}};
    this.textContent = "";
    this.classList = new ClassList(this);
    this._className = "";
  }}
  set className(value) {{ this._className = value; this.classList.reset(value); }}
  get className() {{ return this._className; }}
  replaceChildren(...children) {{ this.children = children; this.textContent = ""; }}
  append(...children) {{ this.children.push(...children); }}
  appendChild(child) {{ this.children.push(child); }}
  setAttribute(name, value) {{ this.attributes[name] = value; }}
  addEventListener() {{}}
}}
const roster = new Element("div");
roster.className = "room-participants hidden count-2";
const document = {{createElement: (tag) => new Element(tag)}};
const $ = (id) => {{ assert.equal(id, "roomParticipants"); return roster; }};
const apiPath = (path) => path;
{functions}
renderParticipantRoster({{
  current_player_id: "p2",
  participants: [
    {{player_id: "p1", seat_index: 0, display_name: "甲", role: "human", participant_kind: "human", active: true, activity_state: "active", join_status: "joined", confirmation_status: "accepted", game_metadata: {{score: 2}}}},
    {{player_id: "p2", seat_index: 1, display_name: "乙", role: "ai", participant_kind: "bound_machine", active: true, activity_state: "active", join_status: "joined", confirmation_status: "accepted", game_metadata: {{dice_count: 4}}}},
    {{player_id: "p3", seat_index: 2, display_name: "丙", role: "ai", participant_kind: "system_npc", active: false, activity_state: "eliminated", join_status: "joined", confirmation_status: "accepted", game_metadata: {{dice_count: 0}}}},
  ],
}});
assert.equal(roster.children.length, 3);
assert.match(roster.children[0].className, /seat-0/);
assert.doesNotMatch(roster.children[0].children[2].textContent, /正在行动|存活/);
const current = roster.children[1];
assert.match(current.className, /seat-1/);
assert.ok(current.classList.contains("current"));
assert.equal(current.attributes["aria-current"], "true");
assert.match(current.children[2].textContent, /▶ 正在行动/);
assert.match(current.children[2].textContent, /剩余骰子 4/);
assert.equal(current.children[0].textContent, "乙");
assert.match(roster.children[2].children[2].textContent, /已淘汰/);
"""
        self.run_node(harness)

    def test_shared_bubble_updates_speaker_avatar_message_and_seat_color(self):
        functions = "\n".join((
            function_source("participantByPlayerId"),
            function_source("participantAvatarFallback"),
            function_source("renderParticipantAvatar"),
            function_source("speechSenderRole"),
            function_source("latestSpeechEvent"),
            function_source("renderSpeechBubble"),
        ))
        harness = f"""
const assert = require("node:assert/strict");
class ClassList {{
  constructor() {{ this.names = new Set(["speech-bubble", "shared-speech", "empty"]); }}
  add(...names) {{ names.forEach((name) => this.names.add(name)); }}
  remove(...names) {{ names.forEach((name) => this.names.delete(name)); }}
  toggle(name, force) {{
    if (force === undefined ? !this.names.has(name) : force) this.names.add(name);
    else this.names.delete(name);
  }}
  contains(name) {{ return this.names.has(name); }}
  [Symbol.iterator]() {{ return this.names[Symbol.iterator](); }}
}}
class Element {{
  constructor() {{ this.classList = new ClassList(); this.textContent = ""; this.attributes = {{}}; this.children = []; }}
  replaceChildren(...children) {{ this.children = children; this.textContent = ""; }}
  setAttribute(name, value) {{ this.attributes[name] = value; }}
  addEventListener() {{}}
}}
const document = {{createElement: () => new Element()}};
const apiPath = (path) => path;
let room = {{participants: [
  {{player_id: "p1", seat_index: 0, display_name: "甲"}},
  {{player_id: "p2", seat_index: 1, display_name: "乙"}},
  {{player_id: "p3", seat_index: 2, display_name: "丙"}},
]}};
{functions}
const bubble = new Element();
const textTarget = new Element();
const nameTarget = new Element();
const avatarTarget = new Element();
const events = [
  {{event_type: "message", text: "第一句", sender_role: "human", sender: {{player_id: "p1", name: "甲", role: "human", seat: 0}}}},
  {{event_type: "message", text: "第二句", sender_role: "ai", sender: {{player_id: "p2", name: "乙", role: "ai", seat: 1}}}},
  {{event_type: "result", text: "不应进入气泡", sender_role: "system", sender: {{player_id: "system", name: "裁判", role: "system"}}}},
];
renderSpeechBubble({{bubble, event: latestSpeechEvent(events), textTarget, nameTarget, avatarTarget, reserveSpace: true}});
assert.equal(nameTarget.textContent, "乙");
assert.equal(textTarget.textContent, "第二句");
assert.equal(avatarTarget.textContent, "乙");
assert.ok(bubble.classList.contains("seat-1"));
assert.ok(!bubble.classList.contains("empty"));
events.push({{event_type: "move", text: "连续更新", sender_role: "ai", sender: {{player_id: "p3", name: "丙", role: "ai", seat: 2}}}});
renderSpeechBubble({{bubble, event: latestSpeechEvent(events), textTarget, nameTarget, avatarTarget, reserveSpace: true}});
assert.equal(nameTarget.textContent, "丙");
assert.equal(textTarget.textContent, "连续更新");
assert.ok(bubble.classList.contains("seat-2"));
assert.ok(!bubble.classList.contains("seat-1"));
"""
        self.run_node(harness)

    def test_table_dom_and_narrow_screen_contracts_keep_board_usable(self):
        table = HTML[HTML.index('id="tableLayout"'):HTML.index('id="humanRow"')]
        self.assertIn('id="roomParticipants"', table)
        self.assertIn('id="sharedSpeech"', table)
        self.assertIn('id="sharedSpeechName"', table)
        self.assertIn('id="sharedSpeechAvatar"', table)
        self.assertIn('id="board"', table)
        self.assertIn(".layout-triangle .board-zone", STYLES)
        self.assertIn(".layout-corners .board-zone", STYLES)
        self.assertIn(".layout-top-row .room-participants", STYLES)
        self.assertIn("overflow-x: auto", STYLES)
        self.assertIn("overscroll-behavior-inline: contain", STYLES)
        self.assertNotIn(".layout-top-row .room-participants { flex-wrap:", STYLES)
        mobile = STYLES[STYLES.index("@media (max-width: 860px)"):]
        self.assertIn("grid-column: 1 / -1", mobile)
        self.assertIn(".layout-top-row .room-participants { justify-content: flex-start; }", mobile)
        self.assertIn(".shared-speech {\n    width: min(430px, 100%);", mobile)


if __name__ == "__main__":
    unittest.main()

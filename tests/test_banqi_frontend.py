import shutil
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "app" / "static" / "app.js").read_text(encoding="utf-8")
BANQI = (ROOT / "app" / "static" / "games" / "banqi.js").read_text(encoding="utf-8")
STYLES = (ROOT / "app" / "static" / "styles.css").read_text(encoding="utf-8")
HTML = (ROOT / "app" / "static" / "index.html").read_text(encoding="utf-8")
NODE = shutil.which("node")


class BanqiFrontendStructureTests(unittest.TestCase):
    def test_banqi_uses_the_independent_renderer_extension(self):
        self.assertIn("registeredGameUIRenderer(room.game_type)", APP)
        self.assertIn("loadCatalogGameRenderers(data.games || [])", APP)
        self.assertIn('window.DuelGameUI.register("banqi", {renderBoard});', BANQI)
        self.assertIn('value="banqi"', HTML)
        self.assertIn('/static/game_ui_registry.js?v=0.9.1', HTML)
        self.assertNotIn('/static/games/banqi.js', HTML)
        self.assertLess(HTML.index("/static/game_ui_registry.js"), HTML.index("/static/app.js"))

    def test_renderer_consumes_server_actions_and_never_names_a_hidden_piece(self):
        self.assertIn("state.legal_actions", BANQI)
        self.assertIn('value === "hidden"', BANQI)
        self.assertIn('"暗子，身份未公开"', BANQI)
        self.assertIn("setPendingMove(targetAction)", BANQI)
        self.assertIn("setPendingMove(flipActions.get(key))", BANQI)
        self.assertNotIn("ranks", BANQI)
        self.assertNotIn("Math.random", BANQI)
        self.assertNotIn("😀", BANQI)
        self.assertNotIn("🀄", BANQI)

    def test_visual_states_and_mobile_hit_area_are_explicit(self):
        for selector in (
            ".board.banqi {",
            ".banqi-piece.is-hidden {",
            ".banqi-piece.is-revealed {",
            ".banqi-piece.color-r {",
            ".banqi-piece.color-b {",
            ".board.banqi .banqi-cell.selected-origin",
            ".banqi-legal-marker.move {",
            ".banqi-legal-marker.capture {",
            ".board.banqi .banqi-cell.just-revealed",
            ".board.banqi .banqi-cell.last-action-origin",
            ".board.banqi .banqi-cell.last-action-target",
        ):
            self.assertIn(selector, STYLES)
        self.assertIn("repeating-conic-gradient", STYLES)
        mobile = STYLES[STYLES.index("@media (max-width: 599px)"):]
        self.assertIn("width: min(92vw, 340px);", mobile)
        for viewport in (320, 375):
            board_width = min(viewport * 0.92, 340)
            cell_width = (board_width - 8 - 18 - 12) / 4
            self.assertGreaterEqual(cell_width, 64)


@unittest.skipUnless(NODE, "node is required for Banqi renderer behavior tests")
class BanqiFrontendBehaviorTests(unittest.TestCase):
    def test_renderer_marks_flip_move_capture_and_last_action_states(self):
        script_path = ROOT / "app" / "static" / "games" / "banqi.js"
        harness = f"""
const assert = require("node:assert/strict");
class ClassList {{
  constructor() {{ this.names = new Set(); }}
  add(...names) {{ names.forEach((name) => this.names.add(name)); }}
  contains(name) {{ return this.names.has(name); }}
}}
class Element {{
  constructor(tag) {{
    this.tag = tag;
    this.children = [];
    this.classList = new ClassList();
    this.dataset = {{}};
    this.listeners = {{}};
    this.disabled = false;
    this.textContent = "";
    this.ariaLabel = "";
  }}
  set className(value) {{
    this.classList = new ClassList();
    value.split(/\\s+/).filter(Boolean).forEach((name) => this.classList.add(name));
  }}
  get className() {{ return [...this.classList.names].join(" "); }}
  appendChild(child) {{ this.children.push(child); return child; }}
  append(...children) {{ children.forEach((child) => this.appendChild(child)); }}
  setAttribute(name, value) {{ this[name] = value; }}
  addEventListener(name, callback) {{ this.listeners[name] = callback; }}
  click() {{ if (!this.disabled && this.listeners.click) this.listeners.click(); }}
}}
const document = {{createElement: (tag) => new Element(tag)}};
let renderer = null;
const window = {{DuelGameUI: {{register: (name, value) => {{
  assert.equal(name, "banqi");
  renderer = value;
}}}}}};
global.document = document;
global.window = window;
require({str(script_path)!r});
assert.equal(typeof renderer.renderBoard, "function");

const boardData = Array.from({{length: 8}}, () => Array(4).fill(null));
boardData[0][0] = "r:c";
boardData[0][1] = "hidden";
boardData[0][2] = "hidden";
const state = {{
  board: boardData,
  color_by_player: {{"human-1": "r", "ai-1": "b"}},
  legal_actions: [
    {{action: "flip", row: 0, col: 1}},
    {{action: "flip", row: 0, col: 2}},
    {{action: "move", from_row: 0, from_col: 0, to_row: 1, to_col: 0}},
    {{action: "move", from_row: 0, from_col: 0, to_row: 0, to_col: 2}},
  ],
  last_action: {{action: "flip", row: 0, col: 1}},
}};
const room = {{
  room_id: "BANQI001", revision: 3,
  viewer: {{player_id: "human-1"}},
}};
let chosen = null;
let cleared = 0;
const render = (pendingMove = null) => {{
  const board = new Element("board");
  renderer.renderBoard({{
    board, state, room, canMove: true, pendingMove,
    setPendingMove: (action) => {{ chosen = action; }},
    clearPendingMove: () => {{ cleared += 1; }},
  }});
  return board;
}};
const cellAt = (board, row, col) => board.children.find((cell) => (
  cell.dataset.moveRow === String(row) && cell.dataset.moveCol === String(col)
));

let board = render();
assert.equal(board.children.length, 32);
assert.match(cellAt(board, 0, 1).ariaLabel, /身份未公开/);
assert.equal(cellAt(board, 0, 1).classList.contains("just-revealed"), true);
cellAt(board, 0, 0).click();
assert.equal(cleared, 1);

board = render();
assert.equal(cellAt(board, 0, 0).classList.contains("selected-origin"), true);
assert.equal(cellAt(board, 1, 0).classList.contains("legal-target"), true);
assert.equal(cellAt(board, 0, 2).classList.contains("legal-capture"), true);
cellAt(board, 0, 2).click();
assert.deepEqual(chosen, {{
  action: "move", from_row: 0, from_col: 0, to_row: 0, to_col: 2,
}});

room.revision = 4;
state.last_action = {{
  action: "move", from_row: 0, from_col: 0, to_row: 1, to_col: 0,
}};
board = render();
assert.equal(cellAt(board, 0, 0).classList.contains("selected-origin"), false);
assert.equal(cellAt(board, 0, 0).classList.contains("last-action-origin"), true);
assert.equal(cellAt(board, 1, 0).classList.contains("last-action-target"), true);
"""
        completed = subprocess.run(
            [NODE, "-e", harness],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_renderer_file_has_valid_javascript_syntax(self):
        completed = subprocess.run(
            [NODE, "--check", str(ROOT / "app" / "static" / "games" / "banqi.js")],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)


if __name__ == "__main__":
    unittest.main()

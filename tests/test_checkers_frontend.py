import shutil
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (ROOT / "app" / "static" / "games" / "checkers.js").read_text(
    encoding="utf-8"
)
STYLES = (ROOT / "app" / "static" / "games" / "checkers.css").read_text(
    encoding="utf-8"
)
HTML = (ROOT / "app" / "static" / "index.html").read_text(encoding="utf-8")
NODE = shutil.which("node")


class CheckersFrontendStructureTests(unittest.TestCase):
    def test_renderer_uses_independent_registry_contract_and_server_moves(self):
        self.assertIn("function renderBoard(context)", SCRIPT)
        self.assertIn("window.DuelGameUI.register('checkers', renderer)", SCRIPT)
        self.assertIn('window.DuelGameUIPending.push(["checkers", renderer])', SCRIPT)
        self.assertIn("state.legal_moves", SCRIPT)
        self.assertIn("state.forced_piece", SCRIPT)
        self.assertIn("state.last_move", SCRIPT)
        self.assertNotIn("function legalMoves", SCRIPT)
        self.assertNotIn("piece.textContent", SCRIPT)
        self.assertNotIn("innerHTML", SCRIPT)

    def test_visual_css_is_scoped_square_mobile_safe_and_emoji_free(self):
        self.assertIn(".board.checkers {", STYLES)
        self.assertIn("aspect-ratio: 1;", STYLES)
        self.assertIn("repeat(8, minmax(0, 1fr))", STYLES)
        self.assertIn(".board.checkers .light-square", STYLES)
        self.assertIn(".board.checkers .dark-square", STYLES)
        self.assertIn(".checkers-piece.side-x", STYLES)
        self.assertIn(".checkers-piece.side-o", STYLES)
        self.assertIn(".checkers-king-mark", STYLES)
        self.assertIn("clip-path: polygon", STYLES)
        self.assertIn(".board.checkers .selected-origin", STYLES)
        self.assertIn(".checkers-legal-marker", STYLES)
        self.assertIn(".board.checkers .last-move-from", STYLES)
        self.assertIn(".board.checkers .last-move-to", STYLES)
        self.assertIn("touch-action: manipulation", STYLES)
        self.assertIn("@media (max-width: 599px)", STYLES)
        self.assertIn("width: min(94vw, 520px);", STYLES)
        self.assertNotIn("url(", STYLES)
        for emoji in ("👑", "⚫", "⚪", "🔴"):
            self.assertNotIn(emoji, SCRIPT + STYLES)

    def test_page_loads_low_conflict_game_assets_and_has_fallback_option(self):
        self.assertIn(
            '<link rel="stylesheet" href="/static/games/checkers.css?v=0.1.0">',
            HTML,
        )
        self.assertIn('/static/game_ui_registry.js?v=0.9.1', HTML)
        self.assertNotIn('/static/games/checkers.js', HTML)
        self.assertIn('<option value="checkers">西洋跳棋 / 2人</option>', HTML)
        self.assertLess(HTML.index("/static/game_ui_registry.js"), HTML.index("/static/app.js"))

    @unittest.skipUnless(NODE, "node is required for renderer DOM behavior test")
    def test_renderer_builds_64_cells_rotates_coordinates_and_submits_only_legal_action(self):
        harness = f"""
const assert = require("node:assert/strict");
const vm = require("node:vm");

class ClassList {{
  constructor(owner) {{ this.owner = owner; this.values = new Set(); }}
  sync(value) {{ this.values = new Set(String(value || "").split(/\\s+/).filter(Boolean)); }}
  add(...names) {{ names.forEach((name) => this.values.add(name)); this.owner._className = [...this.values].join(" "); }}
  remove(...names) {{ names.forEach((name) => this.values.delete(name)); this.owner._className = [...this.values].join(" "); }}
  contains(name) {{ return this.values.has(name); }}
}}
class Element {{
  constructor(tag = "div") {{
    this.tagName = tag.toUpperCase();
    this.children = [];
    this.dataset = {{}};
    this.attributes = {{}};
    this.listeners = {{}};
    this.disabled = false;
    this.style = {{values: {{}}, setProperty: (key, value) => {{ this.style.values[key] = String(value); }}}};
    this.classList = new ClassList(this);
    this._className = "";
  }}
  set className(value) {{ this._className = value; this.classList.sync(value); }}
  get className() {{ return this._className; }}
  appendChild(child) {{ this.children.push(child); return child; }}
  replaceChildren(...children) {{ this.children = [...children]; }}
  setAttribute(key, value) {{ this.attributes[key] = String(value); }}
  addEventListener(type, handler) {{ this.listeners[type] = handler; }}
  click() {{ if (this.listeners.click) this.listeners.click(); }}
}}

const document = {{
  createElement: (tag) => new Element(tag),
  getElementById: () => null,
}};
const window = {{}};
vm.runInNewContext({SCRIPT!r}, {{window, document, Math, Set, Map, Array, String, Boolean, Number}});
assert.equal(window.DuelGameUIPending.length, 1);
const [gameType, renderer] = window.DuelGameUIPending[0];
assert.equal(gameType, "checkers");

const blank = () => Array.from({{length: 8}}, () => Array(8).fill(null));
const board = new Element("section");
const grid = blank();
grid[5][0] = "X:m";
grid[6][1] = "X:k";
grid[4][1] = "O:m";
let chosen = null;
const state = {{
  board: grid,
  turn_mark: "X",
  marks_by_player: {{human: "X"}},
  legal_moves: [{{from_row: 5, from_col: 0, to_row: 3, to_col: 2}}],
  forced_piece: {{row: 5, col: 0}},
  must_capture: true,
  last_move: {{from_row: 6, from_col: 1, to_row: 5, to_col: 2}},
}};
const context = {{
  board,
  state,
  room: {{room_id: "ROOM", revision: 7, status: "playing", current_player_id: "human", viewer: {{player_id: "human"}}}},
  canMove: true,
  onSelectMove: (move) => {{ chosen = move; }},
}};
assert.equal(renderer.renderBoard(context), true);
assert.equal(board.children.length, 64);
assert.equal(board.dataset.viewMark, "X");
assert.equal(board.dataset.forced, "true");
const cellAt = (row, col) => board.children.find((cell) => cell.dataset.moveRow === String(row) && cell.dataset.moveCol === String(col));
assert.equal(board.children.filter((cell) => cell.classList.contains("dark-square")).length, 32);
assert.equal(cellAt(5, 0).classList.contains("selected-origin"), true);
assert.equal(cellAt(3, 2).classList.contains("capture-target"), true);
assert.equal(cellAt(6, 1).classList.contains("last-move-from"), true);
assert.equal(cellAt(5, 2).classList.contains("last-move-to"), true);
const kingPiece = cellAt(6, 1).children.find((child) => child.classList.contains("checkers-piece"));
assert.equal(kingPiece.children[0].classList.contains("checkers-king-mark"), true);
assert.equal(cellAt(0, 0).disabled, true);
cellAt(3, 2).click();
assert.equal(
  JSON.stringify(chosen),
  JSON.stringify({{from_row: 5, from_col: 0, to_row: 3, to_col: 2}})
);

const oBoard = new Element("section");
const oGrid = blank();
oGrid[0][1] = "O:m";
renderer.renderBoard({{
  board: oBoard,
  state: {{board: oGrid, turn_mark: "O", marks_by_player: {{human: "O"}}, legal_moves: [], forced_piece: null}},
  room: {{room_id: "O-ROOM", revision: 1, status: "playing", current_player_id: "human", viewer: {{player_id: "human"}}}},
  canMove: false,
}});
assert.equal(oBoard.dataset.viewMark, "O");
assert.equal(oBoard.children[0].dataset.moveRow, "7");
assert.equal(oBoard.children[0].dataset.moveCol, "7");
assert.equal(oBoard.children[63].dataset.moveRow, "0");
assert.equal(oBoard.children[63].dataset.moveCol, "0");
"""
        completed = subprocess.run(
            [NODE, "-e", harness],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)


if __name__ == "__main__":
    unittest.main()

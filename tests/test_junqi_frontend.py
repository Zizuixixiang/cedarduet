import shutil
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "app" / "static" / "app.js").read_text(encoding="utf-8")
JUNQI = (ROOT / "app" / "static" / "games" / "junqi.js").read_text(encoding="utf-8")
CSS = (ROOT / "app" / "static" / "games" / "junqi.css").read_text(encoding="utf-8")
HTML = (ROOT / "app" / "static" / "index.html").read_text(encoding="utf-8")
NODE = shutil.which("node")


class JunqiFrontendStructureTests(unittest.TestCase):
    def test_independent_dynamic_renderer_and_stylesheet(self):
        self.assertIn('window.DuelGameUI.register("junqi", renderer)', JUNQI)
        self.assertIn('value="junqi"', HTML)
        self.assertNotIn('/static/games/junqi.js', HTML)
        self.assertNotIn('/static/games/junqi.css', HTML)
        self.assertNotIn('room.game_type === "junqi"', APP)
        self.assertNotIn("renderJunqi", APP)
        self.assertIn("/static/games/junqi.css?v=0.1.0", JUNQI)

    def test_hidden_pieces_are_flat_dom_css_without_emoji_or_randomness(self):
        self.assertIn('hidden ? " is-hidden"', JUNQI)
        self.assertIn('hidden ? "军"', JUNQI)
        self.assertIn("对手暗子，身份未公开", JUNQI)
        self.assertNotIn("Math.random", JUNQI)
        for emoji in ("😀", "🎖", "💣", "🚩"):
            self.assertNotIn(emoji, JUNQI)
        self.assertIn(".junqi-piece.is-hidden", CSS)
        self.assertIn("repeating-linear-gradient", CSS)

    def test_mobile_320_and_375_keep_useful_square_hit_areas(self):
        self.assertIn("@media (max-width: 599px)", CSS)
        self.assertIn("width: min(96vw, 360px);", CSS)
        self.assertIn("@media (max-width: 340px)", CSS)
        self.assertIn("width: min(97vw, 310px);", CSS)
        for viewport in (320, 375):
            outer = min(viewport * (0.97 if viewport <= 340 else 0.96), 310 if viewport <= 340 else 360)
            field_width = outer - 14
            horizontal = field_width / 5
            vertical = (field_width * 10.35 / 5) / 12
            self.assertGreaterEqual(horizontal, 55)
            self.assertGreaterEqual(vertical, 50)


@unittest.skipUnless(NODE, "node is required for Junqi renderer behavior tests")
class JunqiFrontendBehaviorTests(unittest.TestCase):
    def test_viewer_rotation_embedded_players_and_authoritative_selection(self):
        script_path = ROOT / "app" / "static" / "games" / "junqi.js"
        harness = f"""
const assert = require("node:assert/strict");
class ClassList {{
  constructor() {{ this.names = new Set(); }}
  add(...names) {{ names.forEach((name) => this.names.add(name)); }}
  toggle(name, force) {{
    const add = force === undefined ? !this.names.has(name) : Boolean(force);
    if (add) this.names.add(name); else this.names.delete(name);
    return add;
  }}
  contains(name) {{ return this.names.has(name); }}
}}
class Style {{ setProperty(name, value) {{ this[name] = String(value); }} }}
class Element {{
  constructor(tag, ownerDocument) {{
    this.tag = tag; this.ownerDocument = ownerDocument; this.children = [];
    this.classList = new ClassList(); this.dataset = {{}}; this.listeners = {{}};
    this.style = new Style(); this.disabled = false; this.textContent = "";
  }}
  set className(value) {{
    this.classList = new ClassList();
    String(value).split(/\\s+/).filter(Boolean).forEach((name) => this.classList.add(name));
  }}
  get className() {{ return [...this.classList.names].join(" "); }}
  appendChild(child) {{ this.children.push(child); return child; }}
  append(...children) {{ children.forEach((child) => this.appendChild(child)); }}
  setAttribute(name, value) {{ this[name] = String(value); }}
  addEventListener(name, callback) {{ this.listeners[name] = callback; }}
  click() {{ if (!this.disabled && this.listeners.click) this.listeners.click(); }}
}}
const document = {{
  head: null,
  createElement(tag) {{ return new Element(tag, this); }},
  getElementById() {{ return null; }},
}};
document.head = new Element("head", document);
let renderer = null;
const window = {{DuelGameUI: {{register: (name, value) => {{
  assert.equal(name, "junqi"); renderer = value;
}}}}}};
global.document = document; global.window = window;
require({str(script_path)!r});
assert.equal(typeof renderer.renderBoard, "function");
assert.equal(renderer.participantPresentation, "embedded");

const publicBoard = {{}};
for (let row = 1; row <= 12; row += 1) {{
  for (const col of ["a", "b", "c", "d", "e"]) publicBoard[`${{col}}${{row}}`] = null;
}}
publicBoard.a1 = {{color: "b"}}; publicBoard.a2 = {{color: "b"}};
publicBoard.e12 = {{color: "r"}};
const state = {{
  phase: "setup", active_player_id: "human-1", board: publicBoard,
  color_by_player: {{"human-1": "b", "ai-1": "r"}},
  setup_ready: {{"human-1": false, "ai-1": false}},
  bunkers: ["b3"], headquarters: ["b1", "d1", "b12", "d12"],
  rail_lines: [["a2", "a3", "a4", "a5", "a6"]], last_action: null,
}};
const participants = [
  {{player_id: "human-1", display_name: "阿青", seat_index: 0}},
  {{player_id: "ai-1", display_name: "小杉", seat_index: 1}},
];
const uiState = {{}};
let chosen = null; let rerenders = 0;
function contextFor(viewer, camp, pieces) {{
  const board = new Element("board", document);
  const controls = new Element("controls", document);
  return {{
    board, controls, state, participants, uiState, canMove: viewer === "human-1",
    room: {{current_player_id: "human-1", viewer: {{player_id: viewer}}}},
    viewer: {{player_id: viewer, seat: viewer === "human-1" ? 0 : 1}},
    privateState: {{camp, pieces, legal_actions: [
      {{action: "swap", from: "a1", to: "a2"}},
      {{action: "shuffle"}}, {{action: "ready"}}, {{action: "auto_setup"}},
    ]}},
    legalActions: [
      {{action: "swap", from: "a1", to: "a2"}},
      {{action: "shuffle"}}, {{action: "ready"}}, {{action: "auto_setup"}},
    ],
    pendingMove: null,
    helpers: {{
      setBoardLayout() {{}},
      renderParticipantAvatar(target, item) {{ target.textContent = item.display_name[0]; }},
      clearSelection() {{ Object.keys(uiState).forEach((key) => delete uiState[key]); }},
      rerender() {{ rerenders += 1; }},
      selectMove(move) {{ chosen = move; }},
    }},
  }};
}}

const blue = contextFor("human-1", "b", {{a1: 0, a2: 9}});
renderer.renderBoard(blue);
assert.equal(blue.board.dataset.rotation, "0");
assert.equal(blue.board.children.length, 3);
const blueField = blue.board.children[1];
assert.equal(blueField.children.length, 60);
assert.equal(blueField.children[0].dataset.square, "a12");
assert.equal(blue.board.children[2].classList.contains("viewer"), true);
const square = (field, key) => field.children.find((cell) => cell.dataset.square === key);
assert.match(square(blueField, "e12").ariaLabel, /身份未公开/);
assert.match(square(blueField, "a1").ariaLabel, /蓝方炸弹/);
square(blueField, "a1").click();
assert.equal(uiState.selectedSquare, "a1");
assert.equal(rerenders, 1);

const blueSelected = contextFor("human-1", "b", {{a1: 0, a2: 9}});
renderer.renderBoard(blueSelected);
square(blueSelected.board.children[1], "a2").click();
assert.deepEqual(chosen, {{action: "swap", from: "a1", to: "a2"}});

Object.keys(uiState).forEach((key) => delete uiState[key]);
state.active_player_id = "ai-1";
const red = contextFor("ai-1", "r", {{e12: 1}});
renderer.renderBoard(red);
assert.equal(red.board.dataset.rotation, "180");
assert.equal(red.board.children[1].children[0].dataset.square, "e1");
assert.equal(red.board.children[2].classList.contains("viewer"), true);
assert.match(square(red.board.children[1], "a1").ariaLabel, /身份未公开/);
assert.match(square(red.board.children[1], "e12").ariaLabel, /红方司令/);
"""
        completed = subprocess.run(
            [NODE, "-e", harness], cwd=ROOT, text=True, capture_output=True, check=False
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_renderer_file_has_valid_javascript_syntax(self):
        completed = subprocess.run(
            [NODE, "--check", str(ROOT / "app" / "static" / "games" / "junqi.js")],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)


if __name__ == "__main__":
    unittest.main()

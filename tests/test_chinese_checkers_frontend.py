import json
import shutil
import subprocess
import unittest
from pathlib import Path

from app.games.chinese_checkers import ChineseCheckers


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "app" / "static" / "games" / "chinese_checkers.js"
STYLE_PATH = ROOT / "app" / "static" / "games" / "chinese_checkers.css"
SCRIPT = SCRIPT_PATH.read_text(encoding="utf-8")
STYLES = STYLE_PATH.read_text(encoding="utf-8")
HTML = (ROOT / "app" / "static" / "index.html").read_text(encoding="utf-8")
NODE = shutil.which("node")


class ChineseCheckersFrontendStructureTests(unittest.TestCase):
    def test_independent_renderer_registers_and_loads_stylesheet_idempotently(self):
        self.assertIn("function ensureStylesheet()", SCRIPT)
        self.assertIn('const STYLE_ID = "duel-chinese-checkers-styles"', SCRIPT)
        self.assertIn(
            'const STYLE_HREF = "/static/games/chinese_checkers.css?v=0.1.0"',
            SCRIPT,
        )
        self.assertIn('document.getElementById(STYLE_ID)', SCRIPT)
        self.assertIn(
            'window.DuelGameUI.register("chinese_checkers", renderer)', SCRIPT
        )
        self.assertIn("function renderBoard(context)", SCRIPT)
        self.assertIn("state.legal_moves", SCRIPT)
        self.assertIn("movePayload(targetMove)", SCRIPT)
        self.assertNotIn("function legalMoves", SCRIPT)
        self.assertNotIn("/static/games/chinese_checkers.js", HTML)
        self.assertNotIn(
            '<link rel="stylesheet" href="/static/games/chinese_checkers.css',
            HTML,
        )
        self.assertIn(
            '<option value="chinese_checkers">中国跳棋 / 2、3、4、6人</option>',
            HTML,
        )

    def test_star_glass_marbles_move_states_and_mobile_layout_are_explicit(self):
        for selector in (
            ".board.chinese_checkers",
            ".cc-star-surface",
            ".cc-hole.camp-0",
            ".cc-hole.viewer-target-camp",
            ".cc-marble",
            ".cc-hole.selected-origin",
            ".cc-legal-marker.step-marker",
            ".cc-legal-marker.jump-marker",
            ".cc-hole.last-move-from",
            ".cc-hole.last-move-to",
            ".cc-path-preview",
            ".cc-progress-badge",
        ):
            self.assertIn(selector, STYLES)
        self.assertIn("clip-path: polygon", STYLES)
        self.assertIn("radial-gradient", STYLES)
        self.assertIn("cc-marble-gleam", SCRIPT + STYLES)
        self.assertIn("touch-action: manipulation", STYLES)
        self.assertIn("@media (max-width: 375px)", STYLES)
        self.assertIn("@media (max-width: 320px)", STYLES)
        self.assertIn("width: min(98vw, 314px)", STYLES)
        self.assertNotIn("url(", STYLES)
        for emoji in ("🔴", "🔵", "🟢", "🟡", "⚪", "⚫", "🔮"):
            self.assertNotIn(emoji, SCRIPT + STYLES)

    def test_renderer_has_stable_view_rotation_and_canonical_path_preview(self):
        self.assertIn("function rotateAxial(q, r, steps)", SCRIPT)
        self.assertIn("(3 - startCamp + 6) % 6", SCRIPT)
        self.assertIn("board.dataset.rotationSteps", SCRIPT)
        self.assertIn('document.createElementNS(SVG_NS, "polyline")', SCRIPT)
        self.assertIn("uiState.previewPath", SCRIPT)
        self.assertIn('targetMove.kind === "jump"', SCRIPT)
        self.assertIn('targetMove.kind === "jump" ? "跳跃" : "相邻一步"', SCRIPT)


@unittest.skipUnless(NODE, "node is required for Chinese Checkers renderer test")
class ChineseCheckersFrontendRuntimeTests(unittest.TestCase):
    def run_node(self, assertions: str) -> None:
        game = ChineseCheckers()
        participants = [
            {
                "player_id": "human-1",
                "display_name": "南山",
                "role": "human",
                "seat_index": 0,
                "token": "P1",
            },
            {
                "player_id": "ai-1",
                "display_name": "小机",
                "role": "ai",
                "seat_index": 1,
                "token": "P2",
            },
        ]
        state = game.initialize(participants)
        camp_zero = state["camps"]["0"]
        central = [node["id"] for node in state["nodes"] if node["camp"] is None]
        origin = camp_zero[0]
        midpoint = central[len(central) // 2]
        target = central[len(central) // 2 + 1]
        state["pieces"] = {origin: "P1"}
        state["legal_moves"] = [{
            "from": origin,
            "to": target,
            "kind": "jump",
            "path": [origin, midpoint, target],
        }]
        state["last_move"] = {
            "from": camp_zero[1],
            "to": camp_zero[2],
            "kind": "step",
            "path": [camp_zero[1], camp_zero[2]],
        }
        state_json = json.dumps(state, ensure_ascii=False, separators=(",", ":"))
        harness = r'''
const assert = require("node:assert/strict");
const vm = require("node:vm");
const fs = require("node:fs");

class ClassList {
  constructor(owner) { this.owner = owner; this.names = new Set(); }
  set(value) { this.names = new Set(String(value || "").split(/\s+/).filter(Boolean)); }
  add(...names) { names.forEach((name) => this.names.add(name)); }
  remove(...names) { names.forEach((name) => this.names.delete(name)); }
  toggle(name, force) {
    const enabled = force === undefined ? !this.names.has(name) : Boolean(force);
    if (enabled) this.names.add(name); else this.names.delete(name);
    return enabled;
  }
  contains(name) { return this.names.has(name); }
}
class Element {
  constructor(tag = "div") {
    this.tag = tag.toLowerCase();
    this.children = [];
    this.dataset = {};
    this.attributes = {};
    this.listeners = {};
    this.style = {setProperty: (name, value) => { this.style[name] = String(value); }};
    this.classList = new ClassList(this);
    this.disabled = false;
    this.textContent = "";
    this.id = "";
    this.rel = "";
    this.href = "";
  }
  set className(value) { this.classList.set(value); }
  get className() { return [...this.classList.names].join(" "); }
  appendChild(child) { this.children.push(child); return child; }
  append(...children) { children.forEach((child) => this.appendChild(child)); }
  replaceChildren(...children) { this.children = [...children]; }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  addEventListener(name, listener) { this.listeners[name] = listener; }
  click() { if (!this.disabled && this.listeners.click) this.listeners.click(); }
}
const headChildren = [];
const document = {
  head: {appendChild(element) { headChildren.push(element); return element; }},
  createElement(tag) { return new Element(tag); },
  createElementNS(_namespace, tag) { return new Element(tag); },
  getElementById(id) { return headChildren.find((item) => item.id === id) || null; },
};
let renderer = null;
const window = {DuelGameUI: {register(gameType, candidate) {
  assert.equal(gameType, "chinese_checkers");
  renderer = candidate;
}}};
vm.runInNewContext(
  fs.readFileSync("app/static/games/chinese_checkers.js", "utf8"),
  {window, document, console, Math, Number, String, Boolean, Array, Map, Set}
);
assert.ok(renderer);
assert.equal(renderer.usesStandardMoveConfirmation, true);
assert.equal(headChildren.length, 1);
assert.equal(headChildren[0].href, "/static/games/chinese_checkers.css?v=0.1.0");

const state = JSON.parse(STATE_JSON);
function createHarness(viewerId, canMove) {
  const board = new Element("div");
  const controls = new Element("div");
  const uiState = {};
  let selectedMove = null;
  let context = null;
  const helpers = {
    setBoardLayout(options) {
      board.style.setProperty("--cols", options.visualCols || options.cols);
      board.style.setProperty("--rows", options.visualRows || options.rows);
      board.setAttribute("aria-label", options.ariaLabel);
    },
    canMove() { return canMove; },
    clearSelection({render = true} = {}) {
      selectedMove = null;
      Object.keys(uiState).forEach((key) => delete uiState[key]);
      if (render) helpers.rerender();
      return true;
    },
    rerender() {
      board.replaceChildren();
      controls.replaceChildren();
      context.pendingMove = selectedMove;
      renderer.renderBoard(context);
      renderer.renderControls(context);
      return true;
    },
    selectMove(payload) {
      selectedMove = {...payload};
      helpers.rerender();
      return true;
    },
  };
  context = {
    board, controls, state, legalMoves: state.legal_moves, uiState, helpers,
    room: {room_id: "ROOM", revision: 7, viewer: {player_id: viewerId}},
    viewer: {player_id: viewerId}, canMove, pendingMove: null,
  };
  helpers.rerender();
  const hole = (nodeId) => board.children.find(
    (item) => item.classList.contains("cc-hole") && item.dataset.nodeId === nodeId
  );
  return {board, controls, uiState, helpers, hole, selectedMove: () => selectedMove};
}
''' + assertions
        harness = harness.replace("STATE_JSON", repr(state_json))
        completed = subprocess.run(
            [NODE, "-e", harness],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_builds_121_holes_rotates_view_highlights_and_previews_path(self):
        self.run_node(r'''
const own = createHarness("human-1", true);
const holes = own.board.children.filter((item) => item.classList.contains("cc-hole"));
assert.equal(holes.length, 121);
assert.equal(own.board.dataset.rotationSteps, "3");
const ownStart = holes.filter((hole) => hole.classList.contains("viewer-start-camp"));
const ownTarget = holes.filter((hole) => hole.classList.contains("viewer-target-camp"));
assert.equal(ownStart.length, 10);
assert.equal(ownTarget.length, 10);
assert.ok(ownStart.every((hole) => Number(hole.dataset.displayR) >= 5));
assert.ok(ownTarget.every((hole) => Number(hole.dataset.displayR) <= -5));

const move = state.legal_moves[0];
assert.equal(own.hole(move.from).disabled, false);
assert.ok(own.hole(move.from).children.some((item) => item.classList.contains("cc-marble")));
own.hole(move.from).click();
assert.equal(own.uiState.selectedNode, move.from);
assert.equal(own.hole(move.to).classList.contains("jump-target"), true);
assert.equal(own.hole(move.to).disabled, false);
own.hole(move.to).click();
assert.deepEqual(own.selectedMove(), {from: move.from, to: move.to, kind: "jump"});
assert.equal(own.hole(move.to).classList.contains("selected-target"), true);
const preview = own.board.children.find((item) => item.classList.contains("cc-path-preview"));
assert.ok(preview);
assert.ok(preview.children.some((item) => item.tag === "polyline"));
assert.equal(own.board.children.filter((item) => item.classList.contains("cc-hole")).length, 121);
assert.equal(headChildren.length, 1);

const opposite = createHarness("ai-1", false);
assert.equal(opposite.board.dataset.rotationSteps, "0");
const oppositeHoles = opposite.board.children.filter((item) => item.classList.contains("cc-hole"));
assert.ok(
  oppositeHoles
    .filter((hole) => hole.classList.contains("viewer-start-camp"))
    .every((hole) => Number(hole.dataset.displayR) >= 5)
);
assert.ok(
  oppositeHoles
    .filter((hole) => hole.classList.contains("viewer-target-camp"))
    .every((hole) => Number(hole.dataset.displayR) <= -5)
);
''')

    def test_source_has_valid_javascript_syntax(self):
        completed = subprocess.run(
            [NODE, "--check", str(SCRIPT_PATH)],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)


if __name__ == "__main__":
    unittest.main()

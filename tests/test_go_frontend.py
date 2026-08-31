import json
import shutil
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "app" / "static" / "games" / "go.js"
STYLE_PATH = ROOT / "app" / "static" / "games" / "go.css"
SCRIPT = SCRIPT_PATH.read_text(encoding="utf-8")
STYLES = STYLE_PATH.read_text(encoding="utf-8")
HTML = (ROOT / "app" / "static" / "index.html").read_text(encoding="utf-8")
NODE = shutil.which("node")


class GoFrontendStructureTests(unittest.TestCase):
    def test_independent_authoritative_renderer_and_no_images(self):
        self.assertIn('window.DuelGameUI.register("go", renderer)', SCRIPT)
        self.assertIn('const STYLE_HREF = "/static/games/go.css?v=0.1.1"', SCRIPT)
        self.assertIn("context.legalActions", SCRIPT)
        self.assertNotIn("function isLegal", SCRIPT)
        self.assertNotIn("libert", SCRIPT.lower())
        self.assertNotIn("capture", SCRIPT.lower().replace("captures", ""))
        self.assertNotIn("url(", STYLES)
        self.assertNotIn("/static/games/go.js", HTML)
        self.assertIn('<option value="go">围棋 / 2人</option>', HTML)

    def test_direct_selection_rotation_roster_and_board_styles(self):
        for value in (
            "function visualPoint(row, col, rotated)",
            'board.dataset.rotationDegrees = rotated ? "180" : "0"',
            "go-player-avatar",
            "提子",
            "当前行动",
        ):
            self.assertIn(value, SCRIPT)
        for value in (
            ".go-board-surface",
            ".go-board-surface::before",
            "repeating-linear-gradient",
            ".go-point",
            ".go-stone.black",
            ".go-stone.white",
            "@media (max-width: 375px)",
            "@media (max-width: 320px)",
            "width: min(96vw, 306px)",
            "touch-action: manipulation",
            ".board-zone:has(.go-phase-status.play)",
            "#confirmMoveButton",
        ):
            self.assertIn(value, STYLES)
        for value in (
            "isPrecisionAssistDevice",
            "appendPrecisionLoupe",
            "focusPoint",
            "go-precision-panel",
            "go-loupe-cell",
        ):
            self.assertNotIn(value, SCRIPT + STYLES)
        self.assertNotIn('.go-point.legal:not(:has(.go-stone))::after', STYLES)
        self.assertIn('"PASS"', SCRIPT)


@unittest.skipUnless(NODE, "node is required for Go renderer test")
class GoFrontendRuntimeTests(unittest.TestCase):
    def test_mobile_tap_directly_selects_legal_intersection(self):
        state = {
            "size": 19,
            "board": [[None for _ in range(19)] for _ in range(19)],
            "phase": "play",
            "to_play": "black",
            "move_number": 0,
            "captures": {"black": 0, "white": 0},
            "dead_stones": [],
            "legal_actions": [
                {"action": "play", "row": 9, "col": 9},
                {"action": "pass"},
            ],
        }
        participants = [
            {
                "player_id": "human-go", "display_name": "南山",
                "seat_index": 0, "token": "black",
            },
            {
                "player_id": "ai-go", "display_name": "小机",
                "seat_index": 1, "token": "white",
            },
        ]
        harness = r'''
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
class ClassList {
  constructor() { this.names = new Set(); }
  set(value) { this.names = new Set(String(value || "").split(/\s+/).filter(Boolean)); }
  add(...names) { names.forEach((name) => this.names.add(name)); }
  contains(name) { return this.names.has(name); }
}
class Element {
  constructor(tag = "div") {
    this.tag = tag; this.children = []; this.dataset = {}; this.listeners = {};
    this.attributes = {}; this.disabled = false; this.textContent = "";
    this.classList = new ClassList(); this.style = {setProperty() {}};
    this.id = ""; this.rel = ""; this.href = "";
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
  getElementById(id) { return headChildren.find((item) => item.id === id) || null; },
};
let renderer = null;
const window = {
  DuelGameUI: {register(type, value) { assert.equal(type, "go"); renderer = value; }},
};
vm.runInNewContext(fs.readFileSync("app/static/games/go.js", "utf8"), {
  window, document, console, Number, String, Boolean, Array, Map, Set,
});
assert.ok(renderer);
const state = JSON.parse(STATE_JSON);
const participants = JSON.parse(PARTICIPANTS_JSON);
const board = new Element("div");
const controls = new Element("div");
const uiState = {};
let selected = null;
let context;
function descendants(root) {
  const found = [];
  const visit = (item) => { found.push(item); item.children.forEach(visit); };
  root.children.forEach(visit); return found;
}
const helpers = {
  setBoardLayout() {},
  renderParticipantAvatar(target, participant) { target.textContent = participant.display_name; },
  selectMove(action) { selected = {...action}; return true; },
  rerender() { board.replaceChildren(); renderer.renderBoard(context); return true; },
};
context = {
  board, controls, state, participants, legalActions: state.legal_actions,
  privateState: {legal_actions: state.legal_actions}, uiState, helpers,
  canMove: true, pendingMove: null,
  room: {current_player_id: "human-go", viewer: {player_id: "human-go"}},
  viewer: {player_id: "human-go"},
};
helpers.rerender();
assert.equal(board.dataset.rotationDegrees, "0");
assert.equal(
  descendants(board).filter((item) => item.classList.contains("go-hoshi")).length,
  9
);
const point = descendants(board).find(
  (item) => item.classList.contains("go-point")
    && item.dataset.row === "9" && item.dataset.col === "9"
);
assert.ok(point); point.click();
assert.equal(JSON.stringify(selected), '{"action":"play","row":9,"col":9}');
assert.equal(Object.hasOwn(uiState, "focusPoint"), false);
assert.equal(
  descendants(board).some((item) => item.classList.contains("go-precision-panel")),
  false
);
renderer.renderControls(context);
const phaseStatus = descendants(controls).find(
  (item) => item.classList.contains("go-phase-status")
);
assert.ok(phaseStatus); assert.equal(phaseStatus.classList.contains("play"), true);
const passButton = descendants(controls).find(
  (item) => item.classList.contains("go-pass")
);
assert.ok(passButton); assert.equal(passButton.textContent, "PASS");
assert.equal(passButton.classList.contains("secondary"), true);
const viewerCard = descendants(board).find(
  (item) => item.classList.contains("go-player-card") && item.classList.contains("bottom")
);
assert.ok(viewerCard); assert.equal(viewerCard.dataset.playerId, "human-go");
'''
        harness = harness.replace(
            "STATE_JSON", repr(json.dumps(state, separators=(",", ":")))
        ).replace(
            "PARTICIPANTS_JSON",
            repr(json.dumps(participants, ensure_ascii=False, separators=(",", ":"))),
        )
        completed = subprocess.run(
            [NODE, "-e", harness], cwd=ROOT, text=True,
            capture_output=True, check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)


if __name__ == "__main__":
    unittest.main()

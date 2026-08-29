import json
import shutil
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "app" / "static" / "games" / "train_cards.js"
STYLE_PATH = ROOT / "app" / "static" / "games" / "train_cards.css"
SCRIPT = SCRIPT_PATH.read_text(encoding="utf-8")
STYLES = STYLE_PATH.read_text(encoding="utf-8")
APP_SCRIPT = (ROOT / "app" / "static" / "app.js").read_text(encoding="utf-8")
HTML = (ROOT / "app" / "static" / "index.html").read_text(encoding="utf-8")
NODE = shutil.which("node")


class TrainCardsFrontendStructureTests(unittest.TestCase):
    def test_independent_renderer_uses_generic_multiplayer_contract(self):
        self.assertIn('window.DuelGameUI.register("train_cards", renderer);', SCRIPT)
        self.assertIn('participantPresentation: "generic"', SCRIPT)
        self.assertIn("function renderBoard(context)", SCRIPT)
        self.assertIn("function renderControls(context)", SCRIPT)
        self.assertIn("usesStandardMoveConfirmation: false", SCRIPT)
        self.assertIn("ownsPrivateStatePresentation: true", SCRIPT)
        self.assertIn(
            'const STYLE_HREF = "/static/games/train_cards.css?v=1.0.0";', SCRIPT
        )
        self.assertIn('link.dataset.duelGameStyle = "train_cards";', SCRIPT)
        self.assertNotIn("/static/games/train_cards.js", HTML)
        self.assertNotIn("train_cards.css", HTML)
        self.assertNotIn("train-cards-opponent", SCRIPT)
        self.assertNotIn("train-cards-seat", SCRIPT)

    def test_renderer_submits_exact_authoritative_flip_without_guessing_card(self):
        for expected in (
            "context.legalActions",
            'action.action === "flip"',
            "context.helpers.submitMove({...flipAction})",
            "牌面由裁判安全翻开",
        ):
            self.assertIn(expected, SCRIPT)
        for forbidden in (
            "matchingRank",
            "match_index",
            "card_id:",
            "Math.random",
        ):
            self.assertNotIn(forbidden, SCRIPT)

    def test_card_faces_are_dom_css_not_images_or_emoji(self):
        self.assertIn("const SUIT_TEXT", SCRIPT)
        self.assertIn("train-card-rank", SCRIPT + STYLES)
        self.assertIn("train-card-suit", SCRIPT + STYLES)
        self.assertIn("train-card-center", SCRIPT + STYLES)
        self.assertNotIn("<img", SCRIPT.lower())
        self.assertNotIn("url(", STYLES.lower())
        for emoji in ("🚂", "🃏", "🎴", "♠️", "♥️", "♣️", "♦️"):
            self.assertNotIn(emoji, SCRIPT + STYLES)

    def test_320_and_375_mobile_layout_keeps_scroll_and_touch_targets(self):
        self.assertIn(".train-cards-track {", STYLES)
        self.assertIn("overflow-x: auto;", STYLES)
        self.assertIn("touch-action: pan-x;", STYLES)
        self.assertIn("max-width: 100%;", STYLES)
        self.assertIn("min-height: 44px;", STYLES)
        self.assertIn("@media (max-width: 375px)", STYLES)
        self.assertIn("@media (max-width: 320px)", STYLES)
        self.assertIn(".train-cards-flip-button:focus-visible", STYLES)

    def test_shared_frontend_understands_local_npc_availability(self):
        requirement_start = APP_SCRIPT.index("function selectedGameRequirement()")
        requirement_end = APP_SCRIPT.index(
            "function selectedTargetPlayerCount()", requirement_start
        )
        source = APP_SCRIPT[requirement_start:requirement_end]
        self.assertIn("declared.uses_local_npc_strategy", source)
        self.assertIn("providerAvailable || localNpcStrategy", source)


@unittest.skipUnless(NODE, "node is required for renderer DOM tests")
class TrainCardsFrontendRuntimeTests(unittest.TestCase):
    def test_render_and_click_submit_forced_action(self):
        state = {
            "board_kind": "train_cards",
            "flow": {"phase": "playing", "round_number": 1, "turn_number": 4},
            "table_cards": [
                {"id": "S3", "suit": "spades", "rank": "3"},
                {"id": "H7", "suit": "hearts", "rank": "7"},
                {"id": "JOKER-S", "suit": "joker", "rank": "small_joker"},
            ],
            "hand_counts": {"human-1": 13, "ai-1": 14, "ai-2": 12},
            "current_player_id": "human-1",
            "active_player_ids": ["human-1", "ai-1", "ai-2"],
            "eliminated_player_ids": [],
            "last_action": {
                "action": "flip",
                "player_id": "ai-2",
                "revealed_card": {"id": "JOKER-S", "suit": "joker", "rank": "small_joker"},
                "collected_cards": [],
                "collected_count": 0,
                "eliminated_player_id": None,
            },
            "last_collection": None,
            "winner_player_id": None,
            "draw_reason": None,
        }
        harness = r'''
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

class ClassList {
  constructor() { this.names = new Set(); }
  set(value) { this.names = new Set(String(value || "").split(/\s+/).filter(Boolean)); }
  contains(name) { return this.names.has(name); }
  toggle(name, force) {
    const enabled = force === undefined ? !this.names.has(name) : Boolean(force);
    if (enabled) this.names.add(name); else this.names.delete(name);
    return enabled;
  }
}
class Element {
  constructor(tag, documentRef) {
    this.tag = tag;
    this.ownerDocument = documentRef;
    this.children = [];
    this.dataset = {};
    this.attributes = {};
    this.listeners = {};
    this.style = {setProperty(name, value) { this[name] = String(value); }};
    this.classList = new ClassList();
    this.disabled = false;
    this.textContent = "";
    this.id = "";
  }
  set className(value) { this.classList.set(value); }
  get className() { return [...this.classList.names].join(" "); }
  appendChild(child) { this.children.push(child); return child; }
  append(...children) { this.children.push(...children); }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  addEventListener(name, listener) { this.listeners[name] = listener; }
}
const styleNodes = new Map();
const document = {
  head: {appendChild(node) { if (node.id) styleNodes.set(node.id, node); }},
  createElement(tag) { return new Element(tag, document); },
  getElementById(id) { return styleNodes.get(id) || null; },
};
let renderer = null;
const window = {document, DuelGameUI: {register(gameType, value) {
  assert.equal(gameType, "train_cards"); renderer = value;
}}};
vm.runInNewContext(fs.readFileSync("app/static/games/train_cards.js", "utf8"), {
  window, document, console, Math, Set, Map, Number, String, Boolean, Array, Object, Promise,
});
assert.ok(renderer);
assert.equal(renderer.participantPresentation, "generic");

function descendants(root) {
  const result = [];
  const visit = (node) => { result.push(node); node.children.forEach(visit); };
  root.children.forEach(visit);
  return result;
}
function hasClass(node, name) { return node.classList && node.classList.contains(name); }

const board = new Element("div", document);
const controls = new Element("div", document);
const submitted = [];
const context = {
  board,
  controls,
  state: STATE_JSON,
  privateState: {legal_actions: [{action: "flip"}]},
  legalActions: [{action: "flip"}],
  participants: [
    {player_id: "human-1", display_name: "南山"},
    {player_id: "ai-1", display_name: "小机一号"},
    {player_id: "ai-2", display_name: "小机二号"},
  ],
  viewer: {player_id: "human-1"},
  room: {current_player_id: "human-1"},
  canMove: true,
  uiState: {},
  helpers: {
    setBoardLayout(options) { board.layout = options; },
    canMove() { return true; },
    rerender() {},
    async submitMove(move) { submitted.push(move); return true; },
  },
};
renderer.renderBoard(context);
renderer.renderControls(context);
const all = descendants(board);
assert.equal(all.filter((node) => hasClass(node, "train-card")).length, 3);
assert.equal(all.filter((node) => hasClass(node, "train-cards-wagon")).length, 3);
assert.equal(all.filter((node) => /opponent|seat/.test(node.className)).length, 0);
assert.equal(board.layout.ariaLabel, "开火车公开牌列");
assert.equal(styleNodes.get("duel-game-train-cards-styles").href, "/static/games/train_cards.css?v=1.0.0");
const button = descendants(controls).find((node) => hasClass(node, "train-cards-flip-button"));
assert.ok(button);
assert.equal(button.disabled, false);
(async () => {
  await button.listeners.click();
  assert.equal(JSON.stringify(submitted), JSON.stringify([{action: "flip"}]));
  process.stdout.write("ok");
})().catch((error) => { console.error(error); process.exitCode = 1; });
'''.replace("STATE_JSON", json.dumps(state, ensure_ascii=False))
        completed = subprocess.run(
            [NODE, "-e", harness],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout, "ok")

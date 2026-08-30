import json
import shutil
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "app" / "static" / "games" / "gandengyan.js"
STYLE_PATH = ROOT / "app" / "static" / "games" / "gandengyan.css"
SCRIPT = SCRIPT_PATH.read_text(encoding="utf-8")
STYLES = STYLE_PATH.read_text(encoding="utf-8")
APP_SCRIPT = (ROOT / "app" / "static" / "app.js").read_text(encoding="utf-8")
HTML = (ROOT / "app" / "static" / "index.html").read_text(encoding="utf-8")
NODE = shutil.which("node")


class GandengyanFrontendStructureTests(unittest.TestCase):
    def test_renderer_is_independent_registry_autoloaded_and_css_is_idempotent(self):
        self.assertIn('window.DuelGameUI.register("gandengyan", renderer);', SCRIPT)
        self.assertIn('participantPresentation: "embedded"', SCRIPT)
        self.assertIn("function renderBoard(context)", SCRIPT)
        self.assertIn("function renderControls(context)", SCRIPT)
        self.assertIn("usesStandardMoveConfirmation: false", SCRIPT)
        self.assertIn("ownsPrivateStatePresentation: true", SCRIPT)
        self.assertIn("function ensureStylesheet(documentRef)", SCRIPT)
        self.assertIn(
            'const STYLE_HREF = "/static/games/gandengyan.css?v=0.1.2";', SCRIPT
        )
        self.assertIn('link.dataset.duelGameStyle = "gandengyan";', SCRIPT)
        self.assertNotIn("gandengyan", APP_SCRIPT)
        self.assertIn("renderer.ownsPrivateStatePresentation === true", APP_SCRIPT)
        self.assertNotIn("/static/games/gandengyan.js", HTML)
        self.assertNotIn("gandengyan.css", HTML)

    def test_client_consumes_authoritative_actions_without_reimplementing_rules(self):
        for expected in (
            "context.legalActions",
            "exact.pattern_label",
            "exactSelectedAction(context)",
            "context.helpers.submitMove({...action})",
            'action.action === "pass"',
        ):
            self.assertIn(expected, SCRIPT)
        for forbidden in (
            "function classifyCards",
            "function canBeat",
            "RANK_VALUE",
            "BOMB_STRENGTH",
        ):
            self.assertNotIn(forbidden, SCRIPT)

    def test_cards_are_text_css_dom_without_images_or_emoji(self):
        self.assertIn("const SUIT_TEXT", SCRIPT)
        self.assertIn("gandengyan-card-rank", SCRIPT + STYLES)
        self.assertIn("gandengyan-card-suit", SCRIPT + STYLES)
        self.assertIn("gandengyan-card-back", SCRIPT + STYLES)
        self.assertNotIn("<img", SCRIPT.lower())
        self.assertNotIn("url(", STYLES.lower())
        for emoji in ("🃏", "🎴", "♠️", "♥️", "♣️", "♦️", "💣", "🔥"):
            self.assertNotIn(emoji, SCRIPT + STYLES)

    def test_mobile_internal_hand_scroll_touch_and_keyboard_targets(self):
        self.assertIn(".gandengyan-hand-scroll {", STYLES)
        self.assertIn("overflow-x: auto;", STYLES)
        self.assertIn("touch-action: pan-x;", STYLES)
        self.assertIn("max-width: 100%;", STYLES)
        self.assertIn("min-height: 44px;", STYLES)
        self.assertIn("@media (max-width: 375px)", STYLES)
        self.assertIn("button.gandengyan-card:focus-visible", STYLES)
        self.assertIn('node.setAttribute("aria-pressed"', SCRIPT)
        self.assertIn('status.setAttribute("aria-live", "polite")', SCRIPT)
        self.assertIn("button.gandengyan-card.selectable {", STYLES)
        self.assertNotIn("filter: saturate(.55) brightness(.82);", STYLES)


@unittest.skipUnless(NODE, "node is required for renderer DOM tests")
class GandengyanFrontendRuntimeTests(unittest.TestCase):
    def run_node(self, assertions):
        state = {
            "board_kind": "gandengyan",
            "flow": {"phase": "following", "round_number": 2, "turn_number": 1},
            "current_trick": {
                "number": 2,
                "leader_player_id": "ai-1",
                "last_play": {
                    "player_id": "ai-1",
                    "cards": [{"id": "S3", "suit": "spades", "rank": "3"}],
                    "pattern": {"type": "single", "label": "单张", "count": 1},
                },
                "pass_player_ids": ["ai-2"],
            },
            "deck_count": 31,
            "hand_counts": {"human-1": 4, "ai-1": 5, "ai-2": 3},
            "multiplier": 4,
            "max_multiplier": 16,
        }
        private_state = {
            "hand": [
                {"id": "S4", "suit": "spades", "rank": "4"},
                {"id": "H4", "suit": "hearts", "rank": "4"},
                {"id": "C8", "suit": "clubs", "rank": "8"},
                {"id": "JOKER-S", "suit": "joker", "rank": "small_joker"},
            ],
            "legal_actions": [
                {"action": "play", "card_ids": ["S4"], "pattern_type": "single", "pattern_label": "单张"},
                {"action": "play", "card_ids": ["S4", "H4"], "pattern_type": "pair", "pattern_label": "对子"},
                {"action": "pass"},
            ],
        }
        harness = r'''
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

class ClassList {
  constructor() { this.names = new Set(); }
  set(value) { this.names = new Set(String(value || "").split(/\s+/).filter(Boolean)); }
  add(...names) { names.forEach((name) => this.names.add(name)); }
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
  replaceChildren(...children) { this.children = children; }
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
  assert.equal(gameType, "gandengyan");
  renderer = value;
}}};
vm.runInNewContext(fs.readFileSync("app/static/games/gandengyan.js", "utf8"), {
  window, document, console, Math, Set, Map, Number, String, Boolean, Array, Object, Promise,
});
assert.ok(renderer);
function descendants(root) {
  const result = [];
  const visit = (node) => { result.push(node); node.children.forEach(visit); };
  root.children.forEach(visit);
  return result;
}
function hasClass(node, name) { return node.classList && node.classList.contains(name); }
const state = STATE_JSON;
const privateState = PRIVATE_JSON;
const participants = [
  {player_id: "human-1", display_name: "南山", seat_index: 0},
  {player_id: "ai-1", display_name: "小机一号", seat_index: 1},
  {player_id: "ai-2", display_name: "小机二号", seat_index: 2},
];
function makeContext(legalActions = privateState.legal_actions) {
  const board = new Element("div", document);
  const controls = new Element("div", document);
  const submitted = [];
  const uiState = {};
  let rerenders = 0;
  const context = {
    board, controls, state, privateState: {...privateState, legal_actions: legalActions},
    participants, viewer: {player_id: "human-1"},
    room: {current_player_id: "human-1", status: "playing"},
    canMove: true, isTerminal: false, legalActions, uiState,
    helpers: {
      setBoardLayout(options) { board.attributes.ariaLabel = options.ariaLabel; },
      canMove() { return true; },
      rerender() { rerenders += 1; return true; },
      async submitMove(move) { submitted.push(move); return true; },
    },
  };
  return {context, board, controls, submitted, uiState, rerenders: () => rerenders};
}
'''.replace("STATE_JSON", json.dumps(state, ensure_ascii=False)).replace(
            "PRIVATE_JSON", json.dumps(private_state, ensure_ascii=False)
        ) + assertions
        completed = subprocess.run(
            [NODE, "-e", harness],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_board_renders_private_hand_public_trick_and_opponent_backs(self):
        self.run_node(r'''
const value = makeContext();
assert.equal(renderer.renderBoard(value.context), true);
const nodes = descendants(value.board);
assert.equal(nodes.filter((node) => hasClass(node, "gandengyan-card") && node.tag === "button").length, 4);
assert.equal(nodes.filter((node) => hasClass(node, "gandengyan-card") && node.tag === "span").length, 1);
assert.equal(JSON.stringify(nodes.filter(
  (node) => hasClass(node, "gandengyan-card") && node.tag === "button"
).map((node) => node.dataset.cardId)), JSON.stringify(["JOKER-S", "C8", "H4", "S4"]));
assert.equal(JSON.stringify(privateState.hand.map((card) => card.id)), JSON.stringify(["S4", "H4", "C8", "JOKER-S"]));
assert.equal(nodes.filter((node) => hasClass(node, "gandengyan-opponent")).length, 2);
assert.equal(nodes.filter((node) => hasClass(node, "gandengyan-card-back")).length, 8);
assert.equal(nodes.filter((node) => hasClass(node, "gandengyan-opponent") && hasClass(node, "passed")).length, 1);
assert.equal(value.board.dataset.multiplier, "4");
assert.equal(styleNodes.size, 1);
renderer.renderBoard(makeContext().context);
assert.equal(styleNodes.size, 1);
assert.equal(styleNodes.get("duel-game-gandengyan-styles").href, "/static/games/gandengyan.css?v=0.1.2");
''')

    def test_multi_selection_submits_exact_server_action_and_pass_is_separate(self):
        self.run_node(r'''
(async () => {
  const value = makeContext();
  renderer.renderBoard(value.context);
  let cards = descendants(value.board).filter(
    (node) => hasClass(node, "gandengyan-card") && node.tag === "button"
  );
  let scroller = descendants(value.board).find((node) => hasClass(node, "gandengyan-hand-scroll"));
  scroller.scrollLeft = 137;
  cards.find((node) => node.dataset.cardId === "S4").listeners.click();
  assert.equal(value.uiState.gandengyanHandScrollLeft, 137);
  value.board.replaceChildren();
  renderer.renderBoard(value.context);
  scroller = descendants(value.board).find((node) => hasClass(node, "gandengyan-hand-scroll"));
  assert.equal(scroller.scrollLeft, 137);
  cards = descendants(value.board).filter(
    (node) => hasClass(node, "gandengyan-card") && node.tag === "button"
  );
  scroller.scrollLeft = 153;
  cards.find((node) => node.dataset.cardId === "H4").listeners.click();
  value.board.replaceChildren();
  renderer.renderBoard(value.context);
  scroller = descendants(value.board).find((node) => hasClass(node, "gandengyan-hand-scroll"));
  assert.equal(scroller.scrollLeft, 153);
  cards = descendants(value.board).filter(
    (node) => hasClass(node, "gandengyan-card") && node.tag === "button"
  );
  assert.equal(cards.find((node) => node.dataset.cardId === "S4").classList.contains("selected"), true);
  assert.equal(cards.find((node) => node.dataset.cardId === "H4").classList.contains("selected"), true);
  assert.equal(JSON.stringify(value.uiState.selectedCardIds), JSON.stringify(["S4", "H4"]));
  assert.equal(value.rerenders(), 2);
  renderer.renderControls(value.context);
  let controls = descendants(value.controls);
  const play = controls.find((node) => hasClass(node, "gandengyan-play-button"));
  assert.equal(play.disabled, false);
  await play.listeners.click();
  assert.equal(JSON.stringify(value.submitted[0]), JSON.stringify({
    action: "play", card_ids: ["S4", "H4"], pattern_type: "pair", pattern_label: "对子",
  }));

  const passValue = makeContext([{action: "pass"}]);
  renderer.renderControls(passValue.context);
  controls = descendants(passValue.controls);
  const pass = controls.find((node) => hasClass(node, "gandengyan-pass-button"));
  assert.equal(pass.disabled, false);
  await pass.listeners.click();
  assert.equal(JSON.stringify(passValue.submitted), JSON.stringify([{action: "pass"}]));
})().catch((error) => { console.error(error); process.exitCode = 1; });
''')

    def test_source_is_valid_javascript(self):
        completed = subprocess.run(
            [NODE, "--check", str(SCRIPT_PATH)],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)


if __name__ == "__main__":
    unittest.main()

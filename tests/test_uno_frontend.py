import shutil
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "app" / "static" / "games" / "uno.js"
STYLE_PATH = ROOT / "app" / "static" / "games" / "uno.css"
SCRIPT = SCRIPT_PATH.read_text(encoding="utf-8")
STYLES = STYLE_PATH.read_text(encoding="utf-8")
APP_SCRIPT = (ROOT / "app" / "static" / "app.js").read_text(encoding="utf-8")
PUBLIC_STYLES = (ROOT / "app" / "static" / "styles.css").read_text(encoding="utf-8")
HTML = (ROOT / "app" / "static" / "index.html").read_text(encoding="utf-8")
NODE = shutil.which("node")


class UnoFrontendContractTests(unittest.TestCase):
    def test_independent_registry_renderer_and_idempotent_css_loading(self):
        self.assertIn('window.DuelGameUI.register("uno", renderer);', SCRIPT)
        self.assertIn('participantPresentation: "embedded"', SCRIPT)
        self.assertIn("ownsPrivateStatePresentation: true", SCRIPT)
        self.assertIn("usesStandardMoveConfirmation: false", SCRIPT)
        self.assertIn("function renderBoard(context)", SCRIPT)
        self.assertIn("function renderControls(context)", SCRIPT)
        self.assertIn("function ensureStylesheet(documentRef)", SCRIPT)
        self.assertIn('const STYLE_HREF = "/static/games/uno.css?v=1.0.1";', SCRIPT)
        self.assertIn('link.dataset.duelGameStyle = "uno";', SCRIPT)
        self.assertNotIn("uno", APP_SCRIPT.lower())
        self.assertNotIn("uno", PUBLIC_STYLES.lower())
        self.assertNotIn("/static/games/uno.js", HTML)
        self.assertNotIn("uno.css", HTML)

    def test_visuals_are_dom_css_text_without_images_or_emoji(self):
        for required in (
            'className = `uno-card${colorClass}',
            'backMark.textContent = "UNO"',
            'face.textContent = cardSymbol(card)',
            'seat.className = "uno-opponent"',
            'scroller.className = "uno-hand-scroll"',
            'colorChoices.className = "uno-color-choices"',
        ):
            self.assertIn(required, SCRIPT)
        self.assertNotIn("<img", SCRIPT.lower())
        self.assertNotIn("createelement(\"img\")", SCRIPT.lower())
        self.assertNotIn("url(", STYLES.lower())
        for emoji in ("🃏", "🔴", "🟡", "🟢", "🔵", "↻", "⛔"):
            self.assertNotIn(emoji, SCRIPT + STYLES)

    def test_mobile_scroll_touch_targets_and_no_page_width_overflow(self):
        self.assertIn("overflow-x: auto;", STYLES)
        self.assertIn("overscroll-behavior-x: contain;", STYLES)
        self.assertIn("min-width: 44px;", STYLES)
        self.assertIn("min-height: 44px;", STYLES)
        self.assertIn("touch-action: manipulation;", STYLES)
        mobile = STYLES[STYLES.index("@media (max-width: 599px)"):]
        self.assertIn(".board.uno { width: 100%; max-width: 100%;", mobile)
        self.assertIn("@media (max-width: 359px)", mobile)
        board_start = STYLES.index(".board.uno {")
        board_rule = STYLES[board_start:STYLES.index("}", board_start + 1)]
        self.assertIn("max-width: 100%;", board_rule)
        self.assertIn("min-width: 0;", board_rule)
        self.assertIn("overflow: hidden;", board_rule)
        self.assertIn(".uno-hand-scroll .uno-card.legal { border-color:", STYLES)
        self.assertNotIn("filter: saturate(.62) brightness(.75);", STYLES)


@unittest.skipUnless(NODE, "node is required for UNO renderer DOM tests")
class UnoFrontendRuntimeTests(unittest.TestCase):
    def run_node(self, assertions):
        harness = r'''
const assert = require("node:assert/strict");
const vm = require("node:vm");
const fs = require("node:fs");

class ClassList {
  constructor() { this.names = new Set(); }
  set(value) { this.names = new Set(String(value || "").split(/\s+/).filter(Boolean)); }
  add(...names) { names.forEach((name) => this.names.add(name)); }
  contains(name) { return this.names.has(name); }
}
class Element {
  constructor(tag, ownerDocument) {
    this.tag = tag; this.ownerDocument = ownerDocument; this.children = [];
    this.dataset = {}; this.attributes = {}; this.listeners = {}; this.disabled = false;
    this.textContent = ""; this.id = ""; this.classList = new ClassList();
    this.style = {values: {}, setProperty(name, value) { this.values[name] = String(value); }};
  }
  set className(value) { this.classList.set(value); }
  get className() { return [...this.classList.names].join(" "); }
  appendChild(child) { this.children.push(child); return child; }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  addEventListener(name, listener) { this.listeners[name] = listener; }
}
const styles = new Map();
const document = {
  head: {appendChild(node) { if (node.id) styles.set(node.id, node); }},
  createElement(tag) { return new Element(tag, document); },
  getElementById(id) { return styles.get(id) || null; },
};
let renderer = null;
const window = {document, DuelGameUI: {register(gameType, candidate) {
  assert.equal(gameType, "uno"); renderer = candidate;
}}};
vm.runInNewContext(fs.readFileSync("app/static/games/uno.js", "utf8"), {
  window, document, console, Math, Number, String, Boolean, Array, Object, Map, Set, Promise,
});
assert.ok(renderer);
function descendants(root) {
  const values = [];
  const visit = (node) => { values.push(node); node.children.forEach(visit); };
  root.children.forEach(visit); return values;
}
function hasClass(node, name) { return node.classList && node.classList.contains(name); }
function makeContext(legalActions) {
  const board = new Element("div", document); const controls = new Element("div", document);
  const submitted = []; const uiState = {};
  const state = {
    hand_counts: {"human-1": 2, "ai-1": 5}, deck_count: 77,
    top_discard: {id: "red-number-5-1", color: "red", kind: "number", value: 5},
    current_color: "red", direction: 1, turn_player_id: "human-1",
    penalty_state: {pending_wild_draw_four: null, last_challenge: null},
    uno_state: {window: null}, winner_player_id: null,
  };
  const context = {
    board, controls, state, uiState, legalActions, canMove: true,
    room: {current_player_id: "human-1", status: "playing"},
    viewer: {player_id: "human-1"},
    participants: [
      {player_id: "human-1", display_name: "南山"},
      {player_id: "ai-1", display_name: "小机"},
    ],
    privateState: {hand: [
      {id: "red-number-7-1", color: "red", kind: "number", value: 7},
      {id: "wild-1", color: null, kind: "wild"},
    ], legal_actions: legalActions},
    helpers: {
      canMove() { return true; }, rerender() { return true; },
      async submitMove(action) { submitted.push(action); return true; },
    },
  };
  return {context, board, controls, submitted};
}
''' + assertions
        completed = subprocess.run(
            [NODE, "-e", harness], cwd=ROOT, check=False, capture_output=True, text=True
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_only_own_real_hand_is_rendered_and_selection_is_highlighted(self):
        self.run_node(r'''
const legal = [
  {action: "play", card_id: "red-number-7-1", uno: true},
  {action: "play", card_id: "red-number-7-1"},
  {action: "draw"},
];
const view = makeContext(legal);
renderer.renderBoard(view.context);
let nodes = descendants(view.board);
assert.equal(nodes.filter((node) => hasClass(node, "uno-opponent")).length, 1);
assert.equal(nodes.filter((node) => node.dataset.cardId === "red-number-7-1").length, 1);
assert.equal(nodes.filter((node) => node.dataset.cardId === "wild-1").length, 1);
assert.equal(nodes.filter((node) => node.dataset.cardId && node.dataset.cardId.includes("ai")).length, 0);
const playable = nodes.find((node) => node.dataset.cardId === "red-number-7-1");
assert.equal(playable.disabled, false);
let scroller = nodes.find((node) => hasClass(node, "uno-hand-scroll"));
scroller.scrollLeft = 119;
scroller.listeners.scroll();
playable.listeners.click();
assert.equal(view.context.uiState.selectedCardId, "red-number-7-1");
assert.equal(view.context.uiState.unoHandScrollLeft, 119);
view.board.replaceChildren();
renderer.renderBoard(view.context);
nodes = descendants(view.board);
scroller = nodes.find((node) => hasClass(node, "uno-hand-scroll"));
assert.equal(scroller.scrollLeft, 119);
assert.equal(nodes.find((node) => node.dataset.cardId === "red-number-7-1").classList.contains("selected"), true);
assert.equal(styles.size, 1);
assert.equal(styles.get("duel-game-uno-styles").href, "/static/games/uno.css?v=1.0.1");
''')

    def test_wild_color_and_uno_submit_exact_authoritative_action(self):
        self.run_node(r'''
(async () => {
  const legal = [
    {action: "play", card_id: "wild-1", color: "red"},
    {action: "play", card_id: "wild-1", color: "red", uno: true},
    {action: "play", card_id: "wild-1", color: "blue"},
    {action: "play", card_id: "wild-1", color: "blue", uno: true},
  ];
  const view = makeContext(legal);
  view.context.uiState.selectedCardId = "wild-1";
  renderer.renderControls(view.context);
  let nodes = descendants(view.controls);
  const blue = nodes.find((node) => hasClass(node, "color-blue"));
  assert.ok(blue); blue.listeners.click();
  view.controls.replaceChildren();
  renderer.renderControls(view.context);
  nodes = descendants(view.controls);
  const declare = nodes.find((node) => node.textContent === "宣告 UNO 并出牌");
  assert.ok(declare); await declare.listeners.click();
  assert.equal(JSON.stringify(view.submitted[0]), JSON.stringify({
    action: "play", card_id: "wild-1", color: "blue", uno: true,
  }));
})().catch((error) => { console.error(error); process.exitCode = 1; });
''')

    def test_challenge_catch_and_draw_are_independent_exact_actions(self):
        self.run_node(r'''
(async () => {
  const legal = [
    {action: "catch_uno"},
    {action: "challenge_wild_draw_four"},
    {action: "accept_draw_four"},
  ];
  const view = makeContext(legal);
  view.context.state.uno_state.window = {
    offender_player_id: "ai-1", catcher_player_id: "human-1",
  };
  view.context.state.penalty_state.pending_wild_draw_four = {
    offender_player_id: "ai-1", challenger_player_id: "human-1",
  };
  renderer.renderControls(view.context);
  const nodes = descendants(view.controls);
  const catchButton = nodes.find((node) => hasClass(node, "catch"));
  const challengeButton = nodes.find((node) => hasClass(node, "challenge"));
  const acceptButton = nodes.find((node) => hasClass(node, "accept"));
  await catchButton.listeners.click(); await challengeButton.listeners.click(); await acceptButton.listeners.click();
  assert.equal(JSON.stringify(view.submitted), JSON.stringify(legal));

  const drawView = makeContext([{action: "draw"}]);
  renderer.renderBoard(drawView.context);
  const deck = descendants(drawView.board).find((node) => hasClass(node, "uno-deck"));
  assert.equal(deck.disabled, false); await deck.listeners.click();
  assert.equal(JSON.stringify(drawView.submitted[0]), JSON.stringify({action: "draw"}));
})().catch((error) => { console.error(error); process.exitCode = 1; });
''')


if __name__ == "__main__":
    unittest.main()

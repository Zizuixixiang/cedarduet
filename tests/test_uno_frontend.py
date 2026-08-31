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
        self.assertIn('const STYLE_HREF = "/static/games/uno.css?v=1.0.5";', SCRIPT)
        self.assertIn('link.dataset.duelGameStyle = "uno";', SCRIPT)
        self.assertIn("terminal_hands", SCRIPT)
        self.assertIn(".uno-terminal-cards", STYLES)
        self.assertNotIn('window.DuelGameUI.register("uno"', APP_SCRIPT)
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
        terminal_cards = STYLES[STYLES.index(".uno-terminal-cards {"):]
        self.assertIn("--terminal-card-width: 36px;", terminal_cards)
        self.assertIn("minmax(17px, 1fr)", terminal_cards)
        self.assertIn("contain: inline-size;", terminal_cards)
        self.assertIn("overflow-x: auto;", terminal_cards)
        self.assertIn("touch-action: pan-x pan-y;", terminal_cards)
        mobile = STYLES[STYLES.index("@media (max-width: 599px)"):]
        self.assertIn(".uno-hand-scroll { min-height: 96px; padding: 9px 5px 5px; }", mobile)
        self.assertIn(".uno-hand-scroll .uno-card { width: 56px; height: 82px; }", mobile)
        self.assertIn("margin-left: -21px;", mobile)
        self.assertIn(".uno-hand-scroll .uno-card { width: 52px; height: 78px; }", mobile)
        self.assertIn("margin-left: -22px;", mobile)

    def test_compact_opponents_and_explicit_acting_state(self):
        self.assertIn('state.textContent = "行动中";', SCRIPT)
        self.assertIn(".uno-player-state {", STYLES)
        self.assertIn(".uno-opponent > .uno-player-state {", STYLES)
        self.assertIn(".uno-opponent-backs .uno-card.compact { width: 36px; height: 50px; }", STYLES)
        mobile = STYLES[STYLES.index("@media (max-width: 599px)"):]
        self.assertIn(".uno-opponent { min-height: 60px; padding: 4px; }", mobile)
        self.assertIn(
            ".uno-opponent-backs .uno-card.compact { width: 34px; height: 48px; }",
            mobile,
        )

    def test_hand_scroller_keeps_scroll_position_contract(self):
        self.assertIn("function saveHandScroll(context, scroller)", SCRIPT)
        self.assertIn("function restoreHandScroll(context, scroller)", SCRIPT)
        self.assertIn("context.uiState.unoHandScrollLeft", SCRIPT)
        self.assertIn(
            'scroller.addEventListener("scroll", () => saveHandScroll(context, scroller), {passive: true});',
            SCRIPT,
        )
        hand_scroll = STYLES[STYLES.index(".uno-hand-scroll {"):]
        hand_scroll = hand_scroll[:hand_scroll.index("}")]
        self.assertIn("overflow-x: auto;", hand_scroll)
        self.assertIn("overflow-y: hidden;", hand_scroll)

    def test_opponent_avatars_use_the_shared_helper_with_compact_fallback(self):
        self.assertIn("context.helpers.renderParticipantAvatar(avatar, participant)", SCRIPT)
        self.assertIn('avatar.textContent = Array.from(String(name).trim())[0] || "?";', SCRIPT)
        self.assertIn(".uno-opponent-avatar {", STYLES)
        self.assertIn("max-width: 22px;", STYLES)
        self.assertIn("max-height: 22px;", STYLES)
        mobile = STYLES[STYLES.index("@media (max-width: 599px)"):]
        self.assertIn(
            ".uno-opponent-avatar { width: 20px; height: 20px; max-width: 20px; max-height: 20px; }",
            mobile,
        )


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
  const submitted = []; const uiState = {}; const avatarCalls = [];
  const state = {
    hand_counts: {"human-1": 2, "ai-1": 5, "ai-2": 3}, deck_count: 77,
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
      {player_id: "ai-2", display_name: "北辰"},
    ],
    privateState: {hand: [
      {id: "red-number-7-1", color: "red", kind: "number", value: 7},
      {id: "wild-1", color: null, kind: "wild"},
    ], legal_actions: legalActions},
    helpers: {
      canMove() { return true; }, rerender() { return true; },
      async submitMove(action) { submitted.push(action); return true; },
      renderParticipantAvatar(target, participant) {
        avatarCalls.push(participant.player_id);
        target.textContent = `avatar:${participant.player_id}`;
      },
    },
  };
  return {context, board, controls, submitted, avatarCalls};
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
assert.equal(nodes.filter((node) => hasClass(node, "uno-opponent")).length, 2);
assert.equal(JSON.stringify(view.avatarCalls), JSON.stringify(["ai-1", "ai-2", "human-1"]));
assert.equal(nodes.filter((node) => hasClass(node, "uno-opponent-avatar")).length, 3);
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
const fallback = makeContext(legal);
delete fallback.context.helpers.renderParticipantAvatar;
renderer.renderBoard(fallback.context);
assert.equal(JSON.stringify(descendants(fallback.board).filter(
  (node) => hasClass(node, "uno-opponent-avatar")
).map((node) => node.textContent)), JSON.stringify(["小", "北", "南"]));
assert.equal(styles.get("duel-game-uno-styles").href, "/static/games/uno.css?v=1.0.5");
''')

    def test_acting_state_follows_opponent_and_own_turns(self):
        self.run_node(r'''
const ownTurn = makeContext([]);
renderer.renderBoard(ownTurn.context);
let nodes = descendants(ownTurn.board);
let states = nodes.filter((node) => hasClass(node, "uno-player-state"));
assert.equal(states.length, 1);
assert.equal(states[0].textContent, "行动中");
const ownHeading = nodes.find((node) => hasClass(node, "uno-hand-heading"));
assert.equal(ownHeading.children.includes(states[0]), true);
assert.equal(nodes.filter((node) => hasClass(node, "uno-opponent") && hasClass(node, "current")).length, 0);

const opponentTurn = makeContext([]);
opponentTurn.context.room.current_player_id = "ai-1";
renderer.renderBoard(opponentTurn.context);
nodes = descendants(opponentTurn.board);
states = nodes.filter((node) => hasClass(node, "uno-player-state"));
assert.equal(states.length, 1);
assert.equal(states[0].textContent, "行动中");
const currentSeat = nodes.find(
  (node) => hasClass(node, "uno-opponent") && hasClass(node, "current")
);
assert.ok(currentSeat);
assert.equal(currentSeat.children.includes(states[0]), true);
assert.equal(currentSeat.attributes["aria-label"], "小机，手牌 5 张，行动中");

const finished = makeContext([]);
finished.context.room.status = "finished";
renderer.renderBoard(finished.context);
assert.equal(descendants(finished.board).filter(
  (node) => hasClass(node, "uno-player-state")
).length, 0);
''')

    def test_terminal_review_keeps_natural_order_and_playing_shows_only_backs(self):
        self.run_node(r'''
const playing = makeContext([]);
renderer.renderBoard(playing.context);
let nodes = descendants(playing.board);
assert.equal(nodes.filter((node) => hasClass(node, "uno-terminal-review")).length, 0);
assert.equal(nodes.filter((node) => hasClass(node, "uno-opponent-backs")).length, 2);
assert.equal(nodes.filter((node) => hasClass(node, "is-back")).length, 7);

const terminal = makeContext([]);
terminal.context.state = {...terminal.context.state, terminal_hands: {
  "human-1": [],
  "ai-1": [
    {id: "blue-number-9-1", color: "blue", kind: "number", value: 9},
    {id: "red-draw-two-1", color: "red", kind: "draw_two"},
    {id: "yellow-skip-1", color: "yellow", kind: "skip"},
  ],
  "ai-2": [
    {id: "wild-1", color: null, kind: "wild"},
    {id: "green-number-2-1", color: "green", kind: "number", value: 2},
  ],
}};
terminal.context.room.status = "finished";
terminal.context.canMove = false;
renderer.renderBoard(terminal.context);
nodes = descendants(terminal.board);
const rows = nodes.filter((node) => hasClass(node, "uno-terminal-row"));
assert.deepEqual(rows.map((node) => node.dataset.playerId), ["human-1", "ai-1", "ai-2"]);
assert.equal(nodes.filter((node) => hasClass(node, "uno-terminal-empty")).length, 1);
const aiOne = rows.find((node) => node.dataset.playerId === "ai-1");
assert.deepEqual(aiOne.children[1].children.map((node) => node.dataset.cardId), [
  "blue-number-9-1", "red-draw-two-1", "yellow-skip-1",
]);
assert.equal(aiOne.children[1].style.values["--terminal-leading-card-count"], "2");
assert.equal(aiOne.children[1].attributes["aria-label"], "3 张终局剩余手牌");
assert.equal(hasClass(aiOne.children[1], "stacked"), true);
const emptyHand = rows.find((node) => node.dataset.playerId === "human-1").children[1];
assert.equal(hasClass(emptyHand, "empty"), true);
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

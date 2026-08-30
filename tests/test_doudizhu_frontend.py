import json
import shutil
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "app" / "static" / "games" / "doudizhu.js"
STYLE_PATH = ROOT / "app" / "static" / "games" / "doudizhu.css"
SCRIPT = SCRIPT_PATH.read_text(encoding="utf-8")
STYLES = STYLE_PATH.read_text(encoding="utf-8")
HTML = (ROOT / "app" / "static" / "index.html").read_text(encoding="utf-8")
NODE = shutil.which("node")


class DoudizhuFrontendTests(unittest.TestCase):
    def test_independent_registry_renderer_and_lazy_styles(self):
        for expected in (
            'window.DuelGameUI.register("doudizhu", renderer);',
            'participantPresentation: "embedded"',
            "function renderBoard(context)",
            "function renderControls(context)",
            "usesStandardMoveConfirmation: false",
            "ownsPrivateStatePresentation: true",
            'const STYLE_HREF = "/static/games/doudizhu.css?v=0.1.4";',
            'link.dataset.duelGameStyle = "doudizhu";',
        ):
            self.assertIn(expected, SCRIPT)
        self.assertNotIn("/static/games/doudizhu.js", HTML)
        self.assertNotIn("doudizhu.css", HTML)

    def test_client_only_selects_authoritative_actions_and_handles_ambiguity(self):
        for expected in (
            "context.legalActions",
            "action.action_id",
            "exactSelectedAction(context)",
            "context.helpers.submitMove({...action})",
            'action.action === "bid"',
            'action.action === "pass"',
            "matches.length > 1",
            "selectedActionId",
        ):
            self.assertIn(expected, SCRIPT)
        for forbidden in (
            "function classifyCards", "function canBeat", "RANK_VALUE",
            "BOMB_STRENGTH", "classify_ranks", "legal_rank_plays",
        ):
            self.assertNotIn(forbidden, SCRIPT)

    def test_three_seat_identity_bottom_trick_pass_and_private_hand_are_visible(self):
        for expected in (
            "doudizhu-opponents", "doudizhu-seat", "doudizhu-avatar",
            "roles_by_player", "role-landlord", "farmerPartnerId", "对家",
            "bottom_revealed", "bottom_cards", "doudizhu-trick-cards",
            "pass_player_ids", "doudizhu-hand-scroll", "我的手牌",
        ):
            self.assertIn(expected, SCRIPT + STYLES)
        self.assertIn(".filter((item) => item.player_id !== viewerId)", SCRIPT)
        self.assertIn("renderHand(documentRef, context, shell)", SCRIPT)

    def test_mobile_hand_scroll_has_320_and_375_guards(self):
        for expected in (
            ".board.doudizhu {",
            "height: auto;",
            "aspect-ratio: auto;",
            "overflow: visible;",
            ".doudizhu-hand-scroll {",
            "overflow-x: auto;",
            "touch-action: pan-x;",
            "overscroll-behavior-x: contain;",
            "max-width: 100%;",
            "min-height: 44px;",
            "@media (max-width: 599px)",
            "@media (max-width: 375px)",
            "@media (max-width: 320px)",
            "button.doudizhu-card:focus-visible",
        ):
            self.assertIn(expected, STYLES)
        board_rule = STYLES[
            STYLES.index(".board.doudizhu {"):STYLES.index("}", STYLES.index(".board.doudizhu {"))
        ]
        self.assertNotIn("aspect-ratio: var(", board_rule)
        self.assertNotIn("overflow: hidden", board_rule)

    def test_local_selection_disables_scroll_anchoring_without_locking_page_scroll(self):
        anchor_rule = STYLES[
            STYLES.index(".board.doudizhu,\n.doudizhu-hand-zone,\n.doudizhu-controls {"):
            STYLES.index(
                "}",
                STYLES.index(".board.doudizhu,\n.doudizhu-hand-zone,\n.doudizhu-controls {")
            )
        ]
        self.assertIn("overflow-anchor: none;", anchor_rule)
        self.assertNotIn("position: fixed", anchor_rule)
        local_rerender = SCRIPT[
            SCRIPT.index("function localRerender(context)"):
            SCRIPT.index("function createCard", SCRIPT.index("function localRerender(context)"))
        ]
        self.assertIn("windowRef.scrollY ?? windowRef.pageYOffset", local_rerender)
        self.assertIn("windowRef.scrollTo(scrollX, scrollY)", local_rerender)
        self.assertIn("windowRef.requestAnimationFrame(restore)", local_rerender)
        self.assertIn(
            "button.doudizhu-card.selected {\n  transform: translateY(-11px);",
            STYLES,
        )
        submit_action = SCRIPT[
            SCRIPT.index("function submitAction(context, action)"):
            SCRIPT.index("function renderBidControls", SCRIPT.index("function submitAction(context, action)"))
        ]
        self.assertNotIn("localRerender(context)", submit_action)

    def test_disabled_private_cards_stay_fully_legible(self):
        disabled_rule = STYLES[
            STYLES.index("button.doudizhu-card:disabled {"):
            STYLES.index("}", STYLES.index("button.doudizhu-card:disabled {"))
        ]
        self.assertIn("cursor: default;", disabled_rule)
        self.assertIn("opacity: 1;", disabled_rule)
        self.assertIn("filter: none;", disabled_rule)
        self.assertNotIn("opacity: .72;", STYLES)

    def test_mobile_table_is_compact_without_clipping_private_or_bottom_cards(self):
        mobile = STYLES[
            STYLES.index("@media (max-width: 599px)"):
            STYLES.index("@media (max-width: 375px)")
        ]
        narrow = STYLES[
            STYLES.index("@media (max-width: 375px)"):
            STYLES.index("@media (max-width: 320px)")
        ]
        smallest = STYLES[
            STYLES.index("@media (max-width: 320px)"):
            STYLES.index("@media (prefers-reduced-motion: reduce)")
        ]
        for expected in (
            "gap: 4px;",
            "padding: 6px 5px 5px;",
            "width: 29px;",
            "min-height: 60px;",
            "min-height: 88px;",
            "height: 73px;",
        ):
            self.assertIn(expected, mobile)
        for expected in (
            "gap: 3px;",
            "padding: 5px 4px 4px;",
            "width: 27px;",
            "min-height: 57px;",
            "min-height: 84px;",
            "height: 70px;",
        ):
            self.assertIn(expected, narrow)
        for expected in (
            "width: 25px;",
            "min-height: 37px;",
            "width: 26px;",
            "min-height: 54px;",
            "min-height: 80px;",
            "height: 67px;",
        ):
            self.assertIn(expected, smallest)
        self.assertIn("overflow-x: auto;", STYLES)
        self.assertIn("overflow-y: hidden;", STYLES)
        self.assertNotIn("max-height:", mobile + narrow + smallest)

    def test_bottom_cards_are_three_complete_non_overlapping_cards(self):
        bottom_styles = STYLES[
            STYLES.index(".doudizhu-bottom-cards {"):STYLES.index(".doudizhu-trick {")
        ]
        self.assertIn("gap: 4px;", bottom_styles)
        self.assertIn("min-width: 31px;", bottom_styles)
        self.assertIn("margin: 0;", bottom_styles)
        self.assertNotIn("margin-left: -", bottom_styles)

    def test_bidding_uses_explicit_compact_confirmation(self):
        for expected in (
            "selectedBidActionId",
            "doudizhu-bid-options",
            "doudizhu-bid-confirm",
            "doudizhu-bid-confirm-button",
            "确认后才会提交",
            "确认不叫",
            "确认叫 ${score} 分",
            "context.uiState.submitting",
        ):
            self.assertIn(expected, SCRIPT + STYLES)
        self.assertNotIn(
            'button.addEventListener("click", () => submitAction(context, action));',
            SCRIPT,
        )

    def test_mobile_play_actions_are_compact_and_have_clear_enabled_states(self):
        mobile = STYLES[
            STYLES.index("@media (max-width: 599px)"):
            STYLES.index("@media (max-width: 375px)")
        ]
        narrow = STYLES[
            STYLES.index("@media (max-width: 375px)"):
            STYLES.index("@media (max-width: 320px)")
        ]
        for expected in (
            ".doudizhu-controls.is-playing {",
            "min-height: 30px;",
            "grid-template-columns: minmax(0, 1.35fr) minmax(74px, .65fr);",
            "min-height: 36px;",
            "padding: 5px 10px;",
        ):
            self.assertIn(expected, mobile)
        self.assertIn("min-height: 28px;", narrow)
        self.assertIn("min-height: 34px;", narrow)
        self.assertIn(".doudizhu-play-button:not(:disabled)", STYLES)
        self.assertIn(".doudizhu-pass-button:not(:disabled)", STYLES)
        self.assertIn(
            "background: linear-gradient(145deg, #c33b46, #92262f) !important;",
            STYLES,
        )
        self.assertIn(
            "background: linear-gradient(145deg, #3b8b76, #1d6254);",
            STYLES,
        )
        disabled = STYLES[
            STYLES.index(
                ".doudizhu-controls.is-playing .doudizhu-action-buttons button:disabled {"
            ):
            STYLES.index(
                "}",
                STYLES.index(
                    ".doudizhu-controls.is-playing .doudizhu-action-buttons button:disabled {"
                ),
            )
        ]
        self.assertIn("background: #ecefed;", disabled)
        self.assertIn("opacity: 1;", disabled)

    def test_cards_are_css_text_not_images_or_emoji_faces(self):
        self.assertIn("const SUIT_TEXT", SCRIPT)
        self.assertIn("doudizhu-card-rank", SCRIPT + STYLES)
        self.assertIn("doudizhu-card-suit", SCRIPT + STYLES)
        self.assertIn("doudizhu-card-back", SCRIPT + STYLES)
        self.assertNotIn("<img", SCRIPT.lower())
        self.assertNotIn("url(", STYLES.lower())
        for emoji in ("🃏", "🎴", "♠️", "♥️", "♣️", "♦️", "💣", "🔥"):
            self.assertNotIn(emoji, SCRIPT + STYLES)

    @unittest.skipUnless(NODE, "node is required for renderer syntax check")
    def test_renderer_parses_in_node(self):
        completed = subprocess.run(
            [NODE, "--check", str(SCRIPT_PATH)],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)


@unittest.skipUnless(NODE, "node is required for renderer DOM tests")
class DoudizhuFrontendRuntimeTests(unittest.TestCase):
    def run_node(self, assertions):
        ranks = ["3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K", "A", "2"]
        suits = ["spades", "hearts", "clubs", "diamonds"]
        hand = [
            {
                "id": f"HAND-{index}",
                "suit": suits[index % len(suits)],
                "rank": ranks[index % len(ranks)],
            }
            for index in range(17)
        ]
        state = {
            "board_kind": "doudizhu",
            "flow": {"phase": "bidding", "round_number": 1, "turn_number": 0},
            "bidding": {"highest_score": 0},
            "roles_by_player": {
                "human-1": "unassigned",
                "ai-1": "unassigned",
                "ai-2": "unassigned",
            },
            "bottom_revealed": False,
            "bottom_card_count": 3,
            "bottom_cards": [],
            "current_trick": {
                "leader_player_id": "human-1",
                "last_play": None,
                "pass_player_ids": [],
            },
            "hand_counts": {"human-1": 17, "ai-1": 17, "ai-2": 17},
            "multiplier": 1,
            "bomb_count": 0,
        }
        legal_actions = [
            {"action": "bid", "action_id": f"bid:{score}", "score": score,
             "label": "不叫" if score == 0 else f"{score}分"}
            for score in range(4)
        ]
        private_state = {"hand": hand, "legal_actions": legal_actions}
        bottom_cards = [
            {"id": "BOTTOM-S3", "suit": "spades", "rank": "3"},
            {"id": "BOTTOM-H7", "suit": "hearts", "rank": "7"},
            {"id": "BOTTOM-DA", "suit": "diamonds", "rank": "A"},
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
    this.scrollLeft = 0;
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
let pageScrollX = 0;
let pageScrollY = 0;
const scrollCalls = [];
const window = {
  document,
  get scrollX() { return pageScrollX; },
  get scrollY() { return pageScrollY; },
  get pageXOffset() { return pageScrollX; },
  get pageYOffset() { return pageScrollY; },
  scrollCalls,
  scrollTo(left, top) {
    pageScrollX = Number(left) || 0;
    pageScrollY = Number(top) || 0;
    scrollCalls.push([pageScrollX, pageScrollY]);
  },
  requestAnimationFrame(callback) { callback(); return scrollCalls.length; },
  DuelGameUI: {register(gameType, value) {
    assert.equal(gameType, "doudizhu");
    renderer = value;
  }},
};
document.defaultView = window;
vm.runInNewContext(fs.readFileSync("app/static/games/doudizhu.js", "utf8"), {
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
const bottomCards = BOTTOM_JSON;
const participants = [
  {player_id: "human-1", display_name: "南山", seat_index: 0},
  {player_id: "ai-1", display_name: "小机一号", seat_index: 1},
  {player_id: "ai-2", display_name: "小机二号", seat_index: 2},
];
function makeContext(stateOverrides = {}) {
  const board = new Element("div", document);
  const controls = new Element("div", document);
  const submitted = [];
  const uiState = {};
  let rerenders = 0;
  const context = {
    board, controls, state: {...state, ...stateOverrides}, privateState,
    participants, viewer: {player_id: "human-1"},
    room: {current_player_id: "human-1", status: "playing"},
    canMove: true, isTerminal: false,
    legalActions: privateState.legal_actions, uiState,
    helpers: {
      setBoardLayout(options) { board.attributes.ariaLabel = options.ariaLabel; },
      canMove() { return context.canMove; },
      rerender() { rerenders += 1; return true; },
      async submitMove(move) { submitted.push(move); return true; },
    },
  };
  return {context, board, controls, submitted, uiState, rerenders: () => rerenders};
}
'''.replace("STATE_JSON", json.dumps(state, ensure_ascii=False)).replace(
            "PRIVATE_JSON", json.dumps(private_state, ensure_ascii=False)
        ).replace("BOTTOM_JSON", json.dumps(bottom_cards, ensure_ascii=False)) + assertions
        completed = subprocess.run(
            [NODE, "-e", harness],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_private_hand_and_complete_hidden_or_revealed_bottom_render(self):
        self.run_node(r'''
const hidden = makeContext();
assert.equal(renderer.renderBoard(hidden.context), true);
const hiddenNodes = descendants(hidden.board);
const seats = hiddenNodes.filter((node) => hasClass(node, "doudizhu-seat"));
assert.deepEqual(seats.map((node) => node.dataset.playerId).sort(), ["ai-1", "ai-2"]);
const privateScroller = hiddenNodes.find((node) => hasClass(node, "doudizhu-hand-scroll"));
assert.ok(privateScroller);
assert.equal(privateScroller.attributes["aria-label"], "我的私密手牌，可横向滚动并多选");
const privateCards = privateScroller.children.filter((node) => hasClass(node, "doudizhu-card"));
assert.equal(privateCards.length, 17);
assert.deepEqual(
  privateCards.map((node) => node.dataset.cardId).sort(),
  privateState.hand.map((card) => card.id).sort()
);
const hiddenBottom = hiddenNodes.find((node) => hasClass(node, "doudizhu-bottom-cards"));
assert.equal(hiddenBottom.children.length, 3);
assert.ok(hiddenBottom.children.every((node) => hasClass(node, "doudizhu-card-back")));

const revealed = makeContext({bottom_revealed: true, bottom_cards: bottomCards});
renderer.renderBoard(revealed.context);
const revealedBottom = descendants(revealed.board).find(
  (node) => hasClass(node, "doudizhu-bottom-cards")
);
assert.equal(revealedBottom.children.length, 3);
assert.ok(revealedBottom.children.every((node) => hasClass(node, "doudizhu-card")));
assert.deepEqual(
  revealedBottom.children.map((node) => node.dataset.cardId),
  bottomCards.map((card) => card.id)
);
assert.equal(styleNodes.get("duel-game-doudizhu-styles").href, "/static/games/doudizhu.css?v=0.1.4");
''')

    def test_private_hand_is_displayed_big_to_small_without_mutating_projection(self):
        self.run_node(r'''
privateState.hand = [
  {id: "three", suit: "clubs", rank: "3"},
  {id: "ace", suit: "diamonds", rank: "A"},
  {id: "small", suit: "joker", rank: "small_joker"},
  {id: "ten", suit: "hearts", rank: "10"},
  {id: "big", suit: "joker", rank: "big_joker"},
  {id: "king", suit: "spades", rank: "K"},
  {id: "two", suit: "clubs", rank: "2"},
];
const sourceOrder = privateState.hand.map((card) => card.id);
const value = makeContext();
renderer.renderBoard(value.context);
const scroller = descendants(value.board).find((node) => hasClass(node, "doudizhu-hand-scroll"));
assert.deepEqual(
  scroller.children.map((node) => node.dataset.cardId),
  ["big", "small", "two", "ace", "king", "ten", "three"]
);
assert.deepEqual(privateState.hand.map((card) => card.id), sourceOrder);
''')

    def test_card_selection_rerender_preserves_horizontal_scroll_and_action_states(self):
        self.run_node(r'''
privateState.legal_actions = [
  {
    action: "play", action_id: "pair-3", card_ids: ["HAND-0", "HAND-13"],
    pattern_label: "对子", main_rank: "3",
  },
  {action: "pass", action_id: "pass"},
];
const value = makeContext({
  flow: {phase: "playing", round_number: 1, turn_number: 3},
  current_trick: {
    leader_player_id: "ai-1",
    last_play: {
      player_id: "ai-1", cards: [{id: "TABLE-4", suit: "spades", rank: "4"}],
      pattern: {label: "单张"},
    },
    pass_player_ids: [],
  },
});
value.context.helpers.rerender = () => {
  value.board.replaceChildren();
  value.controls.replaceChildren();
  renderer.renderBoard(value.context);
  renderer.renderControls(value.context);
  return true;
};
renderer.renderBoard(value.context);
let scroller = descendants(value.board).find((node) => hasClass(node, "doudizhu-hand-scroll"));
scroller.scrollLeft = 137;
scroller.children.find((node) => node.dataset.cardId === "HAND-13").listeners.click();
scroller = descendants(value.board).find((node) => hasClass(node, "doudizhu-hand-scroll"));
assert.equal(scroller.scrollLeft, 137);
assert.equal(JSON.stringify(value.uiState.selectedCardIds), JSON.stringify(["HAND-13"]));

scroller.scrollLeft = 164;
scroller.children.find((node) => node.dataset.cardId === "HAND-0").listeners.click();
scroller = descendants(value.board).find((node) => hasClass(node, "doudizhu-hand-scroll"));
assert.equal(scroller.scrollLeft, 164);
assert.equal(
  JSON.stringify(value.uiState.selectedCardIds),
  JSON.stringify(["HAND-13", "HAND-0"])
);
let controls = descendants(value.controls);
assert.equal(
  controls.find((node) => hasClass(node, "doudizhu-controls")).classList.contains("is-playing"),
  true
);
assert.equal(controls.find((node) => hasClass(node, "doudizhu-play-button")).disabled, false);
assert.equal(controls.find((node) => hasClass(node, "doudizhu-pass-button")).disabled, false);

scroller.scrollLeft = 151;
scroller.children.find((node) => node.dataset.cardId === "HAND-13").listeners.click();
scroller = descendants(value.board).find((node) => hasClass(node, "doudizhu-hand-scroll"));
assert.equal(scroller.scrollLeft, 151);
assert.equal(JSON.stringify(value.uiState.selectedCardIds), JSON.stringify(["HAND-0"]));
controls = descendants(value.controls);
assert.equal(controls.find((node) => hasClass(node, "doudizhu-play-button")).disabled, true);
assert.equal(controls.find((node) => hasClass(node, "doudizhu-pass-button")).disabled, false);
''')

    def test_local_selection_and_pattern_rerenders_preserve_page_scroll_y(self):
        self.run_node(r'''
privateState.legal_actions = [
  {
    action: "play", action_id: "pair-3-a", card_ids: ["HAND-0", "HAND-13"],
    pattern_label: "对子解释 A", main_rank: "3",
  },
  {
    action: "play", action_id: "pair-3-b", card_ids: ["HAND-0", "HAND-13"],
    pattern_label: "对子解释 B", main_rank: "3",
  },
  {action: "pass", action_id: "pass"},
];
const value = makeContext({
  flow: {phase: "playing", round_number: 1, turn_number: 3},
  current_trick: {
    leader_player_id: "ai-1",
    last_play: {
      player_id: "ai-1", cards: [{id: "TABLE-4", suit: "spades", rank: "4"}],
      pattern: {label: "单张"},
    },
    pass_player_ids: [],
  },
});
value.context.helpers.rerender = () => {
  window.scrollTo(0, 19);
  value.board.replaceChildren();
  value.controls.replaceChildren();
  renderer.renderBoard(value.context);
  renderer.renderControls(value.context);
  return true;
};
renderer.renderBoard(value.context);

function handScroller() {
  return descendants(value.board).find((node) => hasClass(node, "doudizhu-hand-scroll"));
}
function clickCard(cardId, expectedY) {
  window.scrollTo(0, expectedY);
  window.scrollCalls.length = 0;
  handScroller().children.find((node) => node.dataset.cardId === cardId).listeners.click();
  assert.equal(window.scrollY, expectedY);
  assert.deepEqual(window.scrollCalls.at(-1), [0, expectedY]);
}

clickCard("HAND-13", 641);
assert.equal(JSON.stringify(value.uiState.selectedCardIds), JSON.stringify(["HAND-13"]));
clickCard("HAND-13", 641);
assert.equal(JSON.stringify(value.uiState.selectedCardIds), JSON.stringify([]));

clickCard("HAND-13", 688);
clickCard("HAND-0", 688);
let controls = descendants(value.controls);
const choices = controls.filter((node) => hasClass(node, "doudizhu-pattern-choice"));
assert.equal(choices.length, 2);
window.scrollTo(0, 733);
window.scrollCalls.length = 0;
choices[1].listeners.click();
assert.equal(window.scrollY, 733);
assert.deepEqual(window.scrollCalls.at(-1), [0, 733]);
assert.equal(value.uiState.selectedActionId, "pair-3-b");
''')

    def test_bid_option_only_selects_then_confirmation_submits_once(self):
        self.run_node(r'''
(async () => {
  const value = makeContext();
  renderer.renderControls(value.context);
  let nodes = descendants(value.controls);
  assert.equal(nodes.filter((node) => hasClass(node, "doudizhu-bid-confirm")).length, 0);
  const twoPoints = nodes.find(
    (node) => hasClass(node, "doudizhu-bid-button") && node.dataset.actionId === "bid:2"
  );
  twoPoints.listeners.click();
  assert.equal(value.submitted.length, 0);
  assert.equal(value.uiState.selectedBidActionId, "bid:2");

  value.controls.replaceChildren();
  renderer.renderControls(value.context);
  nodes = descendants(value.controls);
  const confirm = nodes.find((node) => hasClass(node, "doudizhu-bid-confirm-button"));
  assert.ok(confirm);
  assert.equal(confirm.textContent, "确认叫 2 分");
  window.scrollCalls.length = 0;
  const firstSubmission = confirm.listeners.click();
  const duplicateSubmission = confirm.listeners.click();
  assert.equal(duplicateSubmission, false);
  await firstSubmission;
  assert.equal(window.scrollCalls.length, 0);
  assert.equal(value.submitted.length, 1);
  assert.equal(JSON.stringify(value.submitted[0]), JSON.stringify({
    action: "bid", action_id: "bid:2", score: 2, label: "2分",
  }));

  const cancelled = makeContext();
  renderer.renderControls(cancelled.context);
  nodes = descendants(cancelled.controls);
  nodes.find((node) => node.dataset.actionId === "bid:0").listeners.click();
  cancelled.controls.replaceChildren();
  renderer.renderControls(cancelled.context);
  nodes = descendants(cancelled.controls);
  const cancel = nodes.find((node) => hasClass(node, "doudizhu-bid-cancel-button"));
  assert.equal(nodes.find((node) => hasClass(node, "doudizhu-bid-confirm-button")).textContent, "确认不叫");
  cancel.listeners.click();
  assert.equal(cancelled.uiState.selectedBidActionId, null);
  assert.equal(cancelled.submitted.length, 0);
})().catch((error) => { console.error(error); process.exitCode = 1; });
''')


if __name__ == "__main__":
    unittest.main()

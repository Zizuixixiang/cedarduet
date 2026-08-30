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
            'const STYLE_HREF = "/static/games/doudizhu.css?v=0.1.2";',
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
  assert.equal(gameType, "doudizhu");
  renderer = value;
}}};
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
  privateCards.map((node) => node.dataset.cardId),
  privateState.hand.map((card) => card.id)
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
assert.equal(styleNodes.get("duel-game-doudizhu-styles").href, "/static/games/doudizhu.css?v=0.1.2");
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
  const firstSubmission = confirm.listeners.click();
  const duplicateSubmission = confirm.listeners.click();
  assert.equal(duplicateSubmission, false);
  await firstSubmission;
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

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
        self.assertIn("usesEmbeddedActionFeedback: true", SCRIPT)
        self.assertIn("usesStandardMoveConfirmation: false", SCRIPT)
        self.assertIn("ownsPrivateStatePresentation: true", SCRIPT)
        self.assertIn(
            'const STYLE_HREF = "/static/games/train_cards.css?v=1.0.8";', SCRIPT
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

    def test_public_track_is_one_compact_scrollable_row(self):
        self.assertIn(".train-cards-track {", STYLES)
        track = STYLES[
            STYLES.index(".train-cards-track {"):
            STYLES.index("}", STYLES.index(".train-cards-track {"))
        ]
        for expected in (
            "max-width: 100%;",
            "flex-wrap: nowrap;",
            "overflow-x: auto;",
            "overflow-y: hidden;",
            "touch-action: pan-x;",
        ):
            self.assertIn(expected, track)
        self.assertIn("width: clamp(44px, 6vw, 52px);", STYLES)
        self.assertIn("height: clamp(66px, 9vw, 76px);", STYLES)
        self.assertIn("margin-left: clamp(-32px, -3.8vw, -24px);", STYLES)
        self.assertIn("z-index: calc(var(--wagon-index, 0) + 1);", STYLES)
        self.assertEqual(STYLES.count("overflow-x: auto;"), 2)

    def test_2_to_6_player_mobile_layout_stays_inside_page(self):
        self.assertIn("@media (max-width: 599px)", STYLES)
        self.assertIn("@media (max-width: 320px)", STYLES)
        self.assertIn(".train-cards-flip-button:focus-visible", STYLES)
        self.assertIn(
            '#battleStage[data-game-type="train_cards"] .board-frame', STYLES
        )
        self.assertIn("z-index: calc(var(--wagon-index, 0) + 1);", STYLES)
        mobile = STYLES[
            STYLES.index("@media (max-width: 599px)"):
            STYLES.index("@media (max-width: 320px)")
        ]
        self.assertIn("grid-template-columns: repeat(3, minmax(0, 1fr));", mobile)
        self.assertIn("grid-template-columns: repeat(2, minmax(0, 1fr));", mobile)
        self.assertIn("overflow-x: visible;", mobile)
        self.assertIn("min-height: 29px;", mobile)
        self.assertIn("width: 100%;", mobile)
        self.assertIn("padding: 6px 3px 14px;", mobile)
        self.assertIn("min-height: 82px;", mobile)
        self.assertIn("width: 44px;", mobile)
        self.assertIn("height: 66px;", mobile)
        self.assertIn("display: flex;", mobile)
        self.assertIn(".battle-main-column", mobile)
        self.assertIn(".viewer-participant-row", mobile)
        participant = mobile[
            mobile.index(
                '#battleStage[data-game-type="train_cards"] '
                ".layout-multiplayer .room-participant {"
            ):
            mobile.index(
                "}",
                mobile.index(
                    '#battleStage[data-game-type="train_cards"] '
                    ".layout-multiplayer .room-participant {"
                ),
            )
        ]
        self.assertIn("height: 60px;", participant)
        self.assertIn("grid-template-rows: 26px 13px;", participant)
        detail = mobile[
            mobile.index(
                '#battleStage[data-game-type="train_cards"] '
                ".room-participant-detail {"
            ):
            mobile.index(
                "}",
                mobile.index(
                    '#battleStage[data-game-type="train_cards"] '
                    ".room-participant-detail {"
                ),
            )
        ]
        self.assertIn("height: 13px;", detail)
        self.assertIn("line-height: 13px;", detail)
        mobile_button = mobile[
            mobile.index(".train-cards-flip-button {"):
            mobile.index("}", mobile.index(".train-cards-flip-button {"))
        ]
        self.assertIn("min-width: 112px;", mobile_button)
        self.assertIn("min-height: 29px;", mobile_button)
        self.assertIn("padding: 3px 16px;", mobile_button)

    def test_train_cards_frames_stay_stable_during_submit_and_animation(self):
        for expected in (
            "height: 30px;",
            "height: 96px;",
            "height: 42px;",
            "height: 24px;",
            "height: 82px;",
            "height: 39px;",
            "@keyframes train-cards-reveal",
            "@keyframes train-cards-collect",
            "animation: train-cards-collect 220ms",
        ):
            self.assertIn(expected, STYLES)
        self.assertNotIn("context.uiState.submitting = true;\n      context.helpers.rerender();", SCRIPT)
        self.assertIn('panel.setAttribute("aria-busy", "true");', SCRIPT)
        self.assertIn("documentRef.activeElement === button", SCRIPT)
        self.assertIn('typeof button.blur === "function"', SCRIPT)
        self.assertIn("button.disabled = true;", SCRIPT)
        self.assertNotIn("window.scrollTo", SCRIPT)
        self.assertNotIn("windowRef.scrollTo", SCRIPT)

    def test_turn_header_and_embedded_feedback_do_not_change_page_height(self):
        turn_scope = (
            '.game-view:has(#battleStage[data-game-type="train_cards"]) #turn'
        )
        turn = STYLES[
            STYLES.index(f"{turn_scope} {{"):
            STYLES.index("}", STYLES.index(f"{turn_scope} {{"))
        ]
        for expected in (
            "height: 29px;",
            "min-height: 29px;",
            "padding: 3px 9px;",
            "border: 2px solid transparent;",
        ):
            self.assertIn(expected, turn)
        self.assertIn(
            '.game-view:has(#battleStage[data-game-type="train_cards"]) '
            ".game-meta-secondary {",
            STYLES,
        )
        current_turn = STYLES[
            STYLES.index(f"{turn_scope}.my-turn {{"):
            STYLES.index("}", STYLES.index(f"{turn_scope}.my-turn {{"))
        ]
        self.assertIn("border-color: rgba(204, 112, 132, .55);", current_turn)
        self.assertIn("usesEmbeddedActionFeedback: true", SCRIPT)

    def test_compact_controls_keep_a_large_invisible_hit_area(self):
        button = STYLES[
            STYLES.index(".train-cards-flip-button {"):
            STYLES.index("}", STYLES.index(".train-cards-flip-button {"))
        ]
        self.assertIn("min-height: 30px;", button)
        self.assertIn("#4f796d", button)
        self.assertNotIn("#bf4650", button)
        hit_area = STYLES[
            STYLES.index(".train-cards-flip-button::before {"):
            STYLES.index("}", STYLES.index(".train-cards-flip-button::before {"))
        ]
        self.assertIn("inset: -7px -2px;", hit_area)

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
    this.scrollLeft = 0;
    this.scrollWidth = 720;
    this.clientWidth = 240;
    this.blurCount = 0;
  }
  set className(value) { this.classList.set(value); }
  get className() { return [...this.classList.names].join(" "); }
  appendChild(child) { this.children.push(child); return child; }
  append(...children) { this.children.push(...children); }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  addEventListener(name, listener) { this.listeners[name] = listener; }
  blur() {
    this.blurCount += 1;
    if (this.ownerDocument.activeElement === this) this.ownerDocument.activeElement = null;
  }
}
const styleNodes = new Map();
const timers = [];
const document = {
  activeElement: null,
  head: {appendChild(node) { if (node.id) styleNodes.set(node.id, node); }},
  createElement(tag) { return new Element(tag, document); },
  getElementById(id) { return styleNodes.get(id) || null; },
};
let renderer = null;
const window = {
  document,
  requestAnimationFrame(callback) { callback(); },
  setTimeout(callback, delay) { timers.push({callback, delay}); return timers.length; },
  DuelGameUI: {register(gameType, value) {
  assert.equal(gameType, "train_cards"); renderer = value;
}}};
document.defaultView = window;
vm.runInNewContext(fs.readFileSync("app/static/games/train_cards.js", "utf8"), {
  window, document, console, Math, Set, Map, Number, String, Boolean, Array, Object, Promise,
});
assert.ok(renderer);
assert.equal(renderer.participantPresentation, "generic");
assert.equal(renderer.usesEmbeddedActionFeedback, true);

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
let rerenderCount = 0;
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
  room: {room_id: "room-train", revision: 1, current_player_id: "human-1"},
  canMove: true,
  uiState: {},
  helpers: {
    setBoardLayout(options) { board.layout = options; },
    canMove() { return true; },
    rerender() {
      rerenderCount += 1;
      board.children = [];
      controls.children = [];
      renderer.renderBoard(context);
      renderer.renderControls(context);
    },
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
assert.equal(styleNodes.get("duel-game-train-cards-styles").href, "/static/games/train_cards.css?v=1.0.8");
let track = all.find((node) => hasClass(node, "train-cards-track"));
assert.equal(track.scrollLeft, 480);
track.scrollLeft = 117;
track.listeners.scroll();
board.children = [];
renderer.renderBoard(context);
track = descendants(board).find((node) => hasClass(node, "train-cards-track"));
assert.equal(track.scrollLeft, 117);
assert.equal(descendants(track).filter((node) => hasClass(node, "is-revealed")).length, 0);
context.state.table_cards.push({id: "D9", suit: "diamonds", rank: "9"});
context.state.last_action = {
  action: "flip",
  player_id: "human-1",
  revealed_card: {id: "D9", suit: "diamonds", rank: "9"},
  collected_cards: [],
  collected_count: 0,
  eliminated_player_id: null,
};
board.children = [];
renderer.renderBoard(context);
track = descendants(board).find((node) => hasClass(node, "train-cards-track"));
assert.equal(track.scrollLeft, 480);
assert.equal(descendants(track).filter((node) => hasClass(node, "is-revealed")).length, 1);

// A collection first reconstructs and holds the complete old-match-to-new-card segment.
context.room.revision = 2;
context.state.table_cards = [{id: "S3", suit: "spades", rank: "3"}];
context.state.last_action = {
  action: "flip",
  player_id: "ai-1",
  revealed_card: {id: "D7", suit: "diamonds", rank: "7"},
  collected_cards: [
    {id: "H7", suit: "hearts", rank: "7"},
    {id: "C4", suit: "clubs", rank: "4"},
    {id: "D7", suit: "diamonds", rank: "7"},
  ],
  collected_count: 3,
  eliminated_player_id: null,
};
context.state.last_collection = {
  player_id: "ai-1",
  rank: "7",
  cards: context.state.last_action.collected_cards,
  count: 3,
};
board.children = [];
renderer.renderBoard(context);
track = descendants(board).find((node) => hasClass(node, "train-cards-track"));
assert.equal(track.dataset.transitionPhase, "holding");
assert.deepEqual(
  descendants(track).filter((node) => hasClass(node, "train-card")).map((node) => node.dataset.cardId),
  ["S3", "H7", "C4", "D7"]
);
assert.equal(descendants(track).filter((node) => hasClass(node, "is-collected-segment")).length, 3);
assert.equal(descendants(track).filter((node) => hasClass(node, "is-revealed")).length, 1);
assert.equal(timers.length, 1);
assert.equal(timers[0].delay, 600);

// An ordinary rerender neither replays the reveal nor schedules the action twice.
board.children = [];
renderer.renderBoard(context);
track = descendants(board).find((node) => hasClass(node, "train-cards-track"));
assert.equal(descendants(track).filter((node) => hasClass(node, "is-revealed")).length, 0);
assert.equal(timers.length, 1);

timers.shift().callback();
track = descendants(board).find((node) => hasClass(node, "train-cards-track"));
assert.equal(track.dataset.transitionPhase, "collecting");
assert.equal(descendants(track).filter((node) => hasClass(node, "is-collecting")).length, 3);
assert.equal(timers.length, 1);
assert.equal(timers[0].delay, 220);

timers.shift().callback();
track = descendants(board).find((node) => hasClass(node, "train-cards-track"));
assert.equal(track.dataset.transitionPhase, "final");
assert.deepEqual(
  descendants(track).filter((node) => hasClass(node, "train-card")).map((node) => node.dataset.cardId),
  ["S3"]
);
assert.equal(timers.length, 0);

// A later ordinary flip appears once at the right edge and stays final on rerender.
context.room.revision = 3;
context.state.table_cards = [
  {id: "S3", suit: "spades", rank: "3"},
  {id: "C5", suit: "clubs", rank: "5"},
];
context.state.last_action = {
  action: "flip",
  player_id: "human-1",
  revealed_card: {id: "C5", suit: "clubs", rank: "5"},
  collected_cards: [],
  collected_count: 0,
  eliminated_player_id: null,
};
context.state.last_collection = null;
board.children = [];
renderer.renderBoard(context);
track = descendants(board).find((node) => hasClass(node, "train-cards-track"));
assert.equal(track.children.length, 2);
assert.equal(hasClass(track.children[1], "is-revealed"), true);
board.children = [];
renderer.renderBoard(context);
track = descendants(board).find((node) => hasClass(node, "train-cards-track"));
assert.equal(descendants(track).filter((node) => hasClass(node, "is-revealed")).length, 0);
assert.equal(track.children.length, 2);
assert.equal(timers.length, 0);

context.state.flow.phase = "finished";
context.state.terminal_hands = {
  "human-1": [{id:"D4",suit:"diamonds",rank:"4"}],
  "ai-1": [{id:"C5",suit:"clubs",rank:"5"},{id:"H6",suit:"hearts",rank:"6"}],
  "ai-2": [],
};
board.children = [];
renderer.renderBoard(context);
const terminalNodes = descendants(board);
const review = terminalNodes.find((node) => hasClass(node, "train-cards-terminal-review"));
assert.ok(review);
assert.equal(descendants(review).filter((node) => hasClass(node, "train-cards-terminal-row")).length, 3);
assert.equal(descendants(review).filter((node) => hasClass(node, "train-card")).length, 3);
assert.equal(descendants(review).filter((node) => hasClass(node, "train-cards-terminal-empty")).length, 1);
const button = descendants(controls).find((node) => hasClass(node, "train-cards-flip-button"));
assert.ok(button);
assert.equal(button.disabled, false);
let controlCopy = descendants(controls).find((node) => hasClass(node, "train-cards-control-copy"));
assert.equal(controlCopy.children[0].textContent, "轮到你发车");
(async () => {
  const rerendersBeforeSubmit = rerenderCount;
  document.activeElement = button;
  await button.listeners.click();
  assert.equal(JSON.stringify(submitted), JSON.stringify([{action: "flip"}]));
  assert.equal(rerenderCount, rerendersBeforeSubmit);
  assert.equal(button.blurCount, 1);
  assert.equal(document.activeElement, null);
  assert.equal(button.disabled, true);
  controls.children = [];
  context.state.flow.phase = "playing";
  context.room.status = "playing";
  context.room.current_player_id = "ai-2";
  context.canMove = false;
  renderer.renderControls(context);
  controlCopy = descendants(controls).find((node) => hasClass(node, "train-cards-control-copy"));
  assert.equal(controlCopy.children[0].textContent, "等待 小机二号 翻牌");
  const waitingButton = descendants(controls)
    .find((node) => hasClass(node, "train-cards-flip-button"));
  assert.equal(waitingButton.disabled, true);
  controls.children = [];
  context.state.flow.phase = "finished";
  context.room.status = "finished";
  renderer.renderControls(context);
  controlCopy = descendants(controls).find((node) => hasClass(node, "train-cards-control-copy"));
  assert.equal(controlCopy.children[0].textContent, "本局已结束");
  assert.equal(controlCopy.children[1].textContent, "牌局已结算，不再翻牌");
  assert.equal(
    descendants(controls).some((node) => hasClass(node, "train-cards-flip-button")),
    false
  );
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

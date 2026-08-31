import json
import shutil
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "app" / "static" / "games" / "texas_holdem.js"
STYLE_PATH = ROOT / "app" / "static" / "games" / "texas_holdem.css"
HTML_PATH = ROOT / "app" / "static" / "index.html"
SCRIPT = SCRIPT_PATH.read_text(encoding="utf-8")
STYLE = STYLE_PATH.read_text(encoding="utf-8")
HTML = HTML_PATH.read_text(encoding="utf-8")
NODE = shutil.which("node")
PORTRAIT_MOBILE_STYLE = STYLE.split(
    "@media (orientation: portrait) and (max-width: 620px)", 1
)[1].split("@media (orientation: portrait) and (max-width: 375px)", 1)[0]


class TexasHoldemFrontendStaticTests(unittest.TestCase):
    def test_renderer_is_independent_embedded_and_lazily_styled(self):
        self.assertIn('window.DuelGameUI.register("texas_holdem"', SCRIPT)
        self.assertIn('participantPresentation: "embedded"', SCRIPT)
        self.assertIn("ownsPrivateStatePresentation: true", SCRIPT)
        self.assertIn(
            'const STYLE_HREF = "/static/games/texas_holdem.css?v=1.1.0";',
            SCRIPT,
        )
        self.assertNotIn("/static/games/texas_holdem.js", HTML)
        self.assertNotIn("/static/games/texas_holdem.css", HTML)

    def test_mobile_table_and_controls_are_compact_in_portrait(self):
        self.assertIn(".texas-table-scroll", STYLE)
        self.assertIn("overflow-x: auto", STYLE)
        self.assertIn(".board.texas_holdem .texas-table", STYLE)
        self.assertIn("min-width: 0", STYLE)
        self.assertIn(
            "@media (orientation: portrait) and (max-width: 620px)",
            STYLE,
        )
        self.assertIn(
            "\n".join((
                '  #battleStage[data-game-type="texas_holdem"] .board-frame,',
                '  #battleStage[data-game-type="texas_holdem"] .board.texas_holdem {',
                "    width: 100%;",
                "    min-width: 0;",
                "  }",
            )),
            STYLE,
        )
        self.assertIn("height: clamp(306px, min(86vw, 44dvh), 350px)", STYLE)
        self.assertIn('data-player-count="6"', STYLE)
        self.assertIn(
            "@media (orientation: portrait) and (max-width: 375px)",
            STYLE,
        )
        self.assertIn("touch-action: pan-y", STYLE)
        self.assertNotIn("@media (orientation: landscape)", STYLE)

    def test_portrait_mobile_actions_form_a_compact_aligned_two_column_grid(self):
        self.assertIn(
            "grid-template-columns: repeat(2, minmax(0, 1fr))",
            PORTRAIT_MOBILE_STYLE,
        )
        self.assertIn(
            ".texas-actions > .action-check,\n"
            "  .texas-actions > .action-call,\n"
            "  .texas-actions > .texas-range-action { grid-column: 1; }",
            PORTRAIT_MOBILE_STYLE,
        )
        self.assertIn(
            ".texas-actions > .action-fold,\n"
            "  .texas-actions > .action-all_in { grid-column: 2; }",
            PORTRAIT_MOBILE_STYLE,
        )
        self.assertIn("height: 35px", PORTRAIT_MOBILE_STYLE)
        self.assertIn("min-height: 35px", PORTRAIT_MOBILE_STYLE)
        self.assertIn(
            "grid-template-columns: minmax(68px, 1fr) minmax(64px, 42%)",
            PORTRAIT_MOBILE_STYLE,
        )
        self.assertIn("grid-template-rows: 13px 16px", PORTRAIT_MOBILE_STYLE)
        self.assertNotIn("min-height: 40px", PORTRAIT_MOBILE_STYLE)
        self.assertNotIn("grid-column: span 2", PORTRAIT_MOBILE_STYLE)

    def test_card_faces_use_text_suit_codes_not_emoji_glyphs(self):
        for glyph in ("♠", "♥", "♦", "♣", "🂡", "🃁"):
            self.assertNotIn(glyph, SCRIPT)


@unittest.skipUnless(NODE, "node is required for Texas Hold'em renderer tests")
class TexasHoldemFrontendNodeTests(unittest.TestCase):
    def run_node(self, assertions, participant_count=3):
        participants = [
            {
                "player_id": "human-1" if index == 0 else f"ai-{index}",
                "display_name": "你" if index == 0 else f"小机{index}",
                "seat_index": index,
            }
            for index in range(participant_count)
        ]
        players = {
            item["player_id"]: {
                "stack": 1000 - item["seat_index"] * 5,
                "current_bet": item["seat_index"] * 5,
                "contribution": item["seat_index"] * 5,
                "status": "active",
            }
            for item in participants
        }
        state = {
            "street": "flop",
            "turn_player_id": "human-1",
            "dealer_player_id": "human-1",
            "small_blind_player_id": participants[1]["player_id"],
            "big_blind_player_id": participants[-1]["player_id"],
            "players": players,
            "board": [
                {"rank": "A", "suit": "spades"},
                {"rank": "10", "suit": "hearts"},
                {"rank": "7", "suit": "clubs"},
            ],
            "pot": 45,
            "total_pot": 45,
            "pots": [
                {"name": "main", "amount": 30},
                {"name": "side_1", "amount": 15},
            ],
            "showdown": {},
            "game_result": None,
        }
        private_state = {
            "hand": [
                {"rank": "K", "suit": "diamonds"},
                {"rank": "Q", "suit": "clubs"},
            ],
            "legal_actions": [
                {"action": "call", "amount": 10, "to_amount": 20, "all_in": False},
                {"action": "fold"},
                {"action": "raise", "amount": 30, "min_amount": 30, "max_amount": 1000},
                {"action": "all_in", "amount": 1000, "cost": 980, "short_raise": False},
            ],
        }
        harness = r'''
const assert = require("assert");
const fs = require("fs");
const vm = require("vm");
class ClassList {
  constructor() { this.names = new Set(); }
  reset(value) { this.names = new Set(String(value || "").split(/\s+/).filter(Boolean)); }
  add(...names) { names.forEach((name) => this.names.add(name)); }
  contains(name) { return this.names.has(name); }
  toggle(name, force) {
    const enabled = force === undefined ? !this.names.has(name) : Boolean(force);
    if (enabled) this.names.add(name); else this.names.delete(name);
    return enabled;
  }
}
class Element {
  constructor(tag, ownerDocument) {
    this.tagName = tag.toUpperCase(); this.ownerDocument = ownerDocument;
    this.children = []; this.dataset = {}; this.attributes = {}; this.listeners = {};
    this.style = {}; this.classList = new ClassList(); this.textContent = "";
    this.disabled = false; this.value = ""; this.type = ""; this.tabIndex = -1;
    this.scrollLeft = 0;
  }
  set className(value) { this.classList.reset(value); }
  get className() { return [...this.classList.names].join(" "); }
  appendChild(child) { this.children.push(child); return child; }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  addEventListener(name, listener) { this.listeners[name] = listener; }
  click() { return this.listeners.click ? this.listeners.click() : undefined; }
}
const styleNodes = new Map();
const document = {
  head: {appendChild(node) { if (node.id) styleNodes.set(node.id, node); }},
  createElement(tag) { return new Element(tag, document); },
  getElementById(id) { return styleNodes.get(id) || null; },
};
let renderer = null;
const window = {document, DuelGameUI: {register(gameType, value) {
  assert.equal(gameType, "texas_holdem"); renderer = value;
}}};
vm.runInNewContext(fs.readFileSync("app/static/games/texas_holdem.js", "utf8"), {
  window, document, console, Math, Set, Map, Number, String, Boolean, Array, Object, Promise,
});
function descendants(root) {
  const result = [];
  const visit = (node) => { result.push(node); node.children.forEach(visit); };
  root.children.forEach(visit); return result;
}
function hasClass(node, name) { return node.classList && node.classList.contains(name); }
const participants = PARTICIPANTS;
const state = STATE;
const privateState = PRIVATE;
function makeContext() {
  const board = new Element("div", document);
  const controls = new Element("div", document);
  const submitted = [];
  const uiState = {};
  let rerenders = 0;
  const context = {
    board, controls, document, state, privateState, participants,
    viewer: {player_id: "human-1"}, canMove: true, isTerminal: false,
    room: {current_player_id: "human-1", status: "playing"},
    legalActions: privateState.legal_actions, uiState,
    helpers: {
      canMove() { return true; },
      rerender() { rerenders += 1; },
      renderParticipantAvatar(target, participant) {
        target.textContent = String(participant.display_name || "?")[0]; return true;
      },
      async submitMove(move) { submitted.push(move); return true; },
    },
  };
  return {context, board, controls, submitted, uiState, rerenders: () => rerenders};
}
'''.replace("PARTICIPANTS", json.dumps(participants, ensure_ascii=False)).replace(
            "STATE", json.dumps(state, ensure_ascii=False)
        ).replace("PRIVATE", json.dumps(private_state, ensure_ascii=False)) + assertions
        completed = subprocess.run(
            [NODE, "-e", harness],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_two_three_and_six_player_tables_keep_viewer_below_the_ring(self):
        for count in (2, 3, 6):
            with self.subTest(count=count):
                self.run_node(r'''
const value = makeContext();
assert.equal(renderer.participantPresentation, "embedded");
assert.equal(renderer.ownsPrivateStatePresentation, true);
assert.equal(renderer.renderBoard(value.context), true);
const nodes = descendants(value.board);
assert.equal(nodes.filter((node) => hasClass(node, "texas-seat")).length, participants.length);
assert.equal(nodes.filter((node) => hasClass(node, "is-viewer")).length, 1);
assert.equal(nodes.filter((node) => hasClass(node, "is-opponent")).length, participants.length - 1);
const acting = nodes.filter((node) => hasClass(node, "is-acting"));
assert.equal(acting.length, 1);
assert.equal(acting[0].textContent, "行动中");
const viewerSeat = nodes.find((node) => hasClass(node, "is-viewer"));
assert.equal(descendants(viewerSeat).includes(acting[0]), true);
assert.equal(nodes.filter((node) => hasClass(node, "texas-card-back")).length, (participants.length - 1) * 2);
assert.equal(nodes.filter((node) => hasClass(node, "texas-card-rank")).length, 5);
assert.equal(nodes.filter((node) => hasClass(node, "texas-card is-empty")).length, 0);
assert.equal(nodes.filter((node) => hasClass(node, "is-empty")).length, 2);
assert.equal(value.board.dataset.playerCount, String(participants.length));
assert.equal(styleNodes.size, 1);
renderer.renderBoard(makeContext().context);
assert.equal(styleNodes.size, 1);
''', participant_count=count)

    def test_action_text_follows_other_current_seat_and_disappears_at_terminal(self):
        self.run_node(r'''
const other = makeContext();
other.context.room.current_player_id="ai-1";
other.context.canMove=false;
renderer.renderBoard(other.context);
let nodes=descendants(other.board);
let acting=nodes.filter(n=>hasClass(n,"is-acting"));
assert.equal(acting.length,1);
assert.equal(acting[0].textContent,"行动中");
const aiSeat=nodes.find(n=>hasClass(n,"texas-seat")&&n.dataset.playerId==="ai-1");
assert.equal(descendants(aiSeat).includes(acting[0]),true);

const pending=makeContext();
pending.context.canMove=false;
pending.context.room.status="pending";
renderer.renderBoard(pending.context);
nodes=descendants(pending.board);
assert.equal(nodes.some(n=>hasClass(n,"is-acting")),false);
assert.equal(nodes.some(n=>hasClass(n,"texas-seat")&&hasClass(n,"is-current")),false);

const terminal=makeContext();
terminal.context.isTerminal=true;
terminal.context.canMove=false;
terminal.context.room.status="finished";
state.street="finished";
state.game_result={result_text:"已结算",payout_by_player:{}};
renderer.renderBoard(terminal.context);
nodes=descendants(terminal.board);
assert.equal(nodes.some(n=>hasClass(n,"is-acting")),false);
assert.equal(nodes.some(n=>hasClass(n,"texas-seat")&&hasClass(n,"is-current")),false);
''')

    def test_terminal_showdown_is_labeled_but_folded_holes_stay_hidden(self):
        self.run_node(r'''
state.street="finished";state.game_result={result_text:"摊牌结算",payout_by_player:{"ai-1":45}};
state.showdown={
 "human-1":{cards:privateState.hand,hand_type_label:"一对"},
 "ai-1":{cards:[{rank:"A",suit:"hearts"},{rank:"A",suit:"clubs"}],hand_type_label:"三条"},
};
state.players["ai-2"].status="folded";
const value=makeContext();value.context.canMove=false;value.context.isTerminal=true;value.context.room.status="finished";
renderer.renderBoard(value.context);const nodes=descendants(value.board);
assert.equal(nodes.filter(n=>hasClass(n,"texas-showdown-tag")).length,2);
assert.equal(nodes.filter(n=>hasClass(n,"texas-card-back")).length,2);
const folded=nodes.find(n=>hasClass(n,"texas-seat")&&n.dataset.playerId==="ai-2");
assert.equal(folded.classList.contains("is-folded"),true);
assert.equal(descendants(folded).filter(n=>hasClass(n,"texas-card-back")).length,2);
assert.equal(nodes.some(n=>hasClass(n,"is-acting")),false);
''')

    def test_risky_actions_require_confirmation_can_cancel_and_do_not_double_submit(self):
        self.run_node(r'''
(async () => {
  const value = makeContext();
  renderer.renderBoard(value.context);
  let tableScroll = descendants(value.board).find((node) => hasClass(node, "texas-table-scroll"));
  tableScroll.scrollLeft = 96;
  tableScroll.listeners.scroll();
  renderer.renderControls(value.context);
  let nodes = descendants(value.controls);
  let call = nodes.find((node) => node.dataset.action === "call");
  await call.click();
  assert.equal(value.submitted.length, 0);
  assert.equal(value.uiState.texasPendingAction, "call");
  assert.equal(value.uiState.texasTableScrollLeft, 96);
  assert.equal(value.rerenders(), 1);
  renderer.renderBoard(value.context);
  tableScroll = descendants(value.board).find((node) => hasClass(node, "texas-table-scroll"));
  assert.equal(tableScroll.scrollLeft, 96);

  renderer.renderControls(value.context);
  nodes = descendants(value.controls);
  const cancel = nodes.find((node) => node.classList.contains("texas-confirm-cancel"));
  await cancel.click();
  assert.equal(value.submitted.length, 0);
  assert.equal(value.uiState.texasPendingAction, undefined);
  renderer.renderBoard(value.context);
  tableScroll = descendants(value.board).find((node) => hasClass(node, "texas-table-scroll"));
  assert.equal(tableScroll.scrollLeft, 96);

  renderer.renderControls(value.context);
  nodes = descendants(value.controls);
  call = nodes.find((node) => node.dataset.action === "call");
  await call.click();
  renderer.renderControls(value.context);
  nodes = descendants(value.controls);
  const confirm = nodes.find((node) => node.classList.contains("texas-confirm-submit"));
  await Promise.all([confirm.click(), confirm.click()]);
  assert.equal(JSON.stringify(value.submitted), JSON.stringify([
    {action: "call", amount: 10, to_amount: 20, all_in: false}
  ]));

  const ranged = makeContext();
  renderer.renderControls(ranged.context);
  nodes = descendants(ranged.controls);
  const slider = nodes.find((node) => node.dataset.actionAmount === "raise");
  slider.value = "45";
  const raise = nodes.find((node) => node.dataset.action === "raise");
  await raise.click();
  assert.equal(JSON.stringify(ranged.submitted), JSON.stringify([
    {action: "raise", amount: 45},
  ]));
})().catch((error) => { console.error(error); process.exitCode = 1; });
''')

    def test_control_grid_markup_supports_check_call_bet_and_raise_variants(self):
        self.run_node(r'''
function actionClassesFor(legalActions) {
  privateState.legal_actions = legalActions;
  const value = makeContext();
  renderer.renderControls(value.context);
  const actions = descendants(value.controls).find((node) => hasClass(node, "texas-actions"));
  return actions.children.map((node) => node.className);
}

assert.deepEqual(actionClassesFor([
  {action: "check"},
  {action: "fold"},
  {action: "bet", amount: 10, min_amount: 10, max_amount: 1000},
  {action: "all_in", amount: 1000, cost: 1000, short_raise: false},
]), [
  "texas-action action-check",
  "texas-action action-fold",
  "texas-range-action action-bet",
  "texas-action action-all_in",
]);

assert.deepEqual(actionClassesFor([
  {action: "call", amount: 10, to_amount: 20, all_in: false},
  {action: "fold"},
  {action: "raise", amount: 30, min_amount: 30, max_amount: 1000},
  {action: "all_in", amount: 1000, cost: 980, short_raise: false},
]), [
  "texas-action action-call",
  "texas-action action-fold",
  "texas-range-action action-raise",
  "texas-action action-all_in",
]);
''')


if __name__ == "__main__":
    unittest.main()

import json
import shutil
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "app" / "static" / "games" / "zhajinhua.js"
STYLE_PATH = ROOT / "app" / "static" / "games" / "zhajinhua.css"
SCRIPT = SCRIPT_PATH.read_text(encoding="utf-8")
STYLES = STYLE_PATH.read_text(encoding="utf-8")
APP_SCRIPT = (ROOT / "app" / "static" / "app.js").read_text(encoding="utf-8")
HTML = (ROOT / "app" / "static" / "index.html").read_text(encoding="utf-8")
NODE = shutil.which("node")


class ZhajinhuaFrontendStructureTests(unittest.TestCase):
    def test_renderer_is_independent_embedded_for_multiplayer_and_keeps_duel_outer(self):
        self.assertIn('window.DuelGameUI.register("zhajinhua", {', SCRIPT)
        self.assertIn('participantPresentation: "embedded"', SCRIPT)
        self.assertIn("ownsPrivateStatePresentation: true", SCRIPT)
        self.assertIn("usesStandardMoveConfirmation: false", SCRIPT)
        self.assertIn("function renderBoard(context)", SCRIPT)
        self.assertIn("function renderControls(context)", SCRIPT)
        self.assertIn("if (participants.length > 2)", SCRIPT)
        self.assertIn('if (!isMultiplayerRoom(targetRoom)) return "duel";', APP_SCRIPT)
        self.assertNotIn("zhajinhua", APP_SCRIPT)
        self.assertNotIn("/static/games/zhajinhua.js", HTML)
        self.assertNotIn("zhajinhua.css", HTML)

    def test_renderer_only_submits_authoritative_actions(self):
        for expected in (
            "context.legalActions",
            "context.helpers.submitMove(move)",
            'action.action === "compare"',
            "action.target_player_id",
            "action.cost",
            "action.unit",
        ):
            self.assertIn(expected, SCRIPT)
        for forbidden in (
            "function evaluateHand",
            "function compareHands",
            "HAND_TYPE_STRENGTH",
            "RANK_VALUE",
        ):
            self.assertNotIn(forbidden, SCRIPT)

    def test_cards_are_text_css_dom_and_mobile_safe_without_emoji_faces(self):
        self.assertIn("const SUIT_TEXT", SCRIPT)
        self.assertIn('spades: "\\u2660\\uFE0E"', SCRIPT)
        self.assertIn("zhajinhua-card-back", SCRIPT + STYLES)
        self.assertNotIn("createElement(\"img\")", SCRIPT)
        self.assertNotIn("url(", STYLES.lower())
        self.assertIn("max-width: 100%;", STYLES)
        self.assertIn("min-height: 44px;", STYLES)
        self.assertIn("@media (max-width: 375px)", STYLES)
        self.assertIn("@media (max-width: 320px)", STYLES)
        for emoji in ("🃏", "🎴", "♠️", "♥️", "♣️", "♦️", "🪙"):
            self.assertNotIn(emoji, SCRIPT + STYLES)


@unittest.skipUnless(NODE, "node is required for renderer DOM tests")
class ZhajinhuaFrontendRuntimeTests(unittest.TestCase):
    def run_node(self, assertions, *, participant_count=3, revealed=False):
        participants = [
            {"player_id": "human-1", "display_name": "南山", "seat_index": 0},
            {"player_id": "ai-1", "display_name": "小机一号", "seat_index": 1},
            {"player_id": "ai-2", "display_name": "小机二号", "seat_index": 2},
            {"player_id": "ai-3", "display_name": "小机三号", "seat_index": 3},
            {"player_id": "ai-4", "display_name": "小机四号", "seat_index": 4},
            {"player_id": "ai-5", "display_name": "小机五号", "seat_index": 5},
        ][:participant_count]
        players = {
            item["player_id"]: {
                "seen": item["player_id"] == "ai-1",
                "status": "active",
                "contribution": index + 2,
            }
            for index, item in enumerate(participants)
        }
        state = {
            "board_kind": "zhajinhua",
            "flow": {"phase": "betting", "round_number": 2, "turn_number": 1},
            "players": players,
            "pot": sum(value["contribution"] for value in players.values()),
            "blind_unit": 2,
            "max_rounds": 20,
            "revealed_hands": {},
            "last_compare": {
                "initiator_player_id": "ai-1",
                "target_player_id": "ai-2" if participant_count > 2 else "human-1",
                "winner_player_id": "ai-1",
                "loser_player_id": "ai-2" if participant_count > 2 else "human-1",
                "tied": False,
                "cards_revealed": False,
            },
        }
        private_state = {
            "hand": (
                [
                    {"rank": "A", "suit": "hearts"},
                    {"rank": "K", "suit": "clubs"},
                    {"rank": "J", "suit": "spades"},
                ]
                if revealed else [{"hidden": True}] * 3
            ),
            "hand_revealed": revealed,
            "hand_type_label": "散牌" if revealed else None,
            "legal_actions": [
                {"action": "peek"},
                {"action": "call", "cost": 2},
                {"action": "raise", "unit": 4, "cost": 4},
                {
                    "action": "compare",
                    "target_player_id": "ai-1",
                    "cost": 2,
                },
                {"action": "fold"},
            ],
        }
        harness = r'''
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
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
  constructor(tag, documentRef) {
    this.tag = tag; this.ownerDocument = documentRef; this.children = [];
    this.dataset = {}; this.attributes = {}; this.listeners = {};
    this.disabled = false; this.textContent = ""; this.id = "";
    this.classList = new ClassList();
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
  assert.equal(gameType, "zhajinhua"); renderer = value;
}}};
vm.runInNewContext(fs.readFileSync("app/static/games/zhajinhua.js", "utf8"), {
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
    board, controls, state, privateState, participants,
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

    def test_three_player_table_renders_viewer_below_and_opponents_with_backs(self):
        self.run_node(r'''
const value = makeContext();
assert.equal(renderer.renderBoard(value.context), true);
const nodes = descendants(value.board);
assert.equal(nodes.filter((node) => hasClass(node, "zhajinhua-seat")).length, 3);
assert.equal(nodes.filter((node) => hasClass(node, "is-viewer")).length, 1);
assert.equal(nodes.filter((node) => hasClass(node, "is-opponent")).length, 2);
assert.equal(nodes.filter((node) => hasClass(node, "zhajinhua-card-back")).length, 9);
assert.equal(nodes.filter((node) => hasClass(node, "zhajinhua-avatar")).length, 3);
assert.equal(value.board.dataset.playerCount, "3");
assert.equal(styleNodes.size, 1);
renderer.renderBoard(makeContext().context);
assert.equal(styleNodes.size, 1);
''')

    def test_seen_cards_and_risky_action_confirmation_cancel_and_submit_guard(self):
        self.run_node(r'''
(async () => {
  const value = makeContext();
  renderer.renderBoard(value.context);
  renderer.renderControls(value.context);
  let nodes = [...descendants(value.board), ...descendants(value.controls)];
  assert.equal(nodes.filter((node) => hasClass(node, "zhajinhua-card-back")).length, 6);
  assert.equal(nodes.filter((node) => hasClass(node, "zhajinhua-card-rank")).length, 3);
  let compare = nodes.find((node) => node.dataset.action === "compare");
  assert.equal(compare.dataset.targetPlayerId, "ai-1");
  await compare.click();
  assert.equal(value.submitted.length, 0);
  assert.equal(value.uiState.zhajinhuaPendingActionKey, "compare:ai-1");

  renderer.renderControls(value.context);
  nodes = descendants(value.controls);
  const cancel = nodes.find((node) => hasClass(node, "zhajinhua-confirm-cancel"));
  await cancel.click();
  assert.equal(value.submitted.length, 0);
  assert.equal(value.uiState.zhajinhuaPendingActionKey, undefined);

  renderer.renderControls(value.context);
  nodes = descendants(value.controls);
  compare = nodes.find((node) => node.dataset.action === "compare");
  await compare.click();
  renderer.renderControls(value.context);
  nodes = descendants(value.controls);
  const confirm = nodes.find((node) => hasClass(node, "zhajinhua-confirm-submit"));
  await Promise.all([confirm.click(), confirm.click()]);
  assert.equal(JSON.stringify(value.submitted), JSON.stringify([
    {action: "compare", target_player_id: "ai-1", cost: 2}
  ]));
})().catch((error) => { console.error(error); process.exitCode = 1; });
''', revealed=True)

    def test_six_player_table_keeps_five_opponents_in_the_embedded_ring(self):
        self.run_node(r'''
const value = makeContext();
renderer.renderBoard(value.context);
const nodes = descendants(value.board);
assert.equal(nodes.filter((node) => hasClass(node, "zhajinhua-seat")).length, 6);
assert.equal(nodes.filter((node) => hasClass(node, "is-viewer")).length, 1);
assert.equal(nodes.filter((node) => hasClass(node, "is-opponent")).length, 5);
assert.equal(nodes.filter((node) => hasClass(node, "zhajinhua-card-back")).length, 18);
assert.equal(value.board.dataset.playerCount, "6");
''', participant_count=6)

    def test_two_player_renderer_does_not_duplicate_outer_opponent_seat(self):
        self.run_node(r'''
const value = makeContext();
renderer.renderBoard(value.context);
const nodes = descendants(value.board);
assert.equal(nodes.filter((node) => hasClass(node, "zhajinhua-opponents")).length, 0);
assert.equal(nodes.filter((node) => hasClass(node, "zhajinhua-seat")).length, 1);
''', participant_count=2)


if __name__ == "__main__":
    unittest.main()

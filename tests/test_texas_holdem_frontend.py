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


class TexasHoldemFrontendStaticTests(unittest.TestCase):
    def test_renderer_is_independent_embedded_and_lazily_styled(self):
        self.assertIn('window.DuelGameUI.register("texas_holdem"', SCRIPT)
        self.assertIn('participantPresentation: "embedded"', SCRIPT)
        self.assertIn("ownsPrivateStatePresentation: true", SCRIPT)
        self.assertIn(
            'const STYLE_HREF = "/static/games/texas_holdem.css?v=1.0.0";',
            SCRIPT,
        )
        self.assertNotIn("/static/games/texas_holdem.js", HTML)
        self.assertNotIn("/static/games/texas_holdem.css", HTML)

    def test_mobile_table_is_compact_and_internally_scrollable(self):
        self.assertIn(".texas-table-scroll", STYLE)
        self.assertIn("overflow-x: auto", STYLE)
        self.assertIn("min-width: 600px", STYLE)
        self.assertIn("@media (max-width: 375px)", STYLE)
        self.assertIn("touch-action: pan-x", STYLE)

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
                "stack": 200 - item["seat_index"] * 5,
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
                {"action": "raise", "amount": 30, "min_amount": 30, "max_amount": 200},
                {"action": "all_in", "amount": 200, "cost": 180, "short_raise": False},
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
  const context = {
    board, controls, document, state, privateState, participants,
    viewer: {player_id: "human-1"}, canMove: true, isTerminal: false,
    room: {current_player_id: "human-1", status: "playing"},
    legalActions: privateState.legal_actions,
    helpers: {
      canMove() { return true; },
      renderParticipantAvatar(target, participant) {
        target.textContent = String(participant.display_name || "?")[0]; return true;
      },
      async submitMove(move) { submitted.push(move); return true; },
    },
  };
  return {context, board, controls, submitted};
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
assert.equal(nodes.filter((node) => hasClass(node, "texas-card-back")).length, (participants.length - 1) * 2);
assert.equal(nodes.filter((node) => hasClass(node, "texas-card-rank")).length, 5);
assert.equal(nodes.filter((node) => hasClass(node, "texas-card is-empty")).length, 0);
assert.equal(nodes.filter((node) => hasClass(node, "is-empty")).length, 2);
assert.equal(value.board.dataset.playerCount, String(participants.length));
assert.equal(styleNodes.size, 1);
renderer.renderBoard(makeContext().context);
assert.equal(styleNodes.size, 1);
''', participant_count=count)

    def test_controls_submit_authoritative_call_and_bounded_raise(self):
        self.run_node(r'''
(async () => {
  const value = makeContext();
  renderer.renderControls(value.context);
  const nodes = descendants(value.controls);
  const call = nodes.find((node) => node.dataset.action === "call");
  await call.click();
  const slider = nodes.find((node) => node.dataset.actionAmount === "raise");
  slider.value = "45";
  const raise = nodes.find((node) => node.dataset.action === "raise");
  await raise.click();
  assert.equal(JSON.stringify(value.submitted), JSON.stringify([
    {action: "call", amount: 10, to_amount: 20, all_in: false},
    {action: "raise", amount: 45},
  ]));
})().catch((error) => { console.error(error); process.exitCode = 1; });
''')


if __name__ == "__main__":
    unittest.main()

import json
import shutil
import subprocess
import unittest
from pathlib import Path

from app.games.aeroplane_chess import AeroplaneChess


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "app" / "static" / "games" / "aeroplane_chess.js"
STYLE_PATH = ROOT / "app" / "static" / "games" / "aeroplane_chess.css"
SCRIPT = SCRIPT_PATH.read_text(encoding="utf-8")
STYLES = STYLE_PATH.read_text(encoding="utf-8")
APP_SCRIPT = (ROOT / "app" / "static" / "app.js").read_text(encoding="utf-8")
PUBLIC_STYLES = (ROOT / "app" / "static" / "styles.css").read_text(
    encoding="utf-8"
)
HTML = (ROOT / "app" / "static" / "index.html").read_text(encoding="utf-8")
NODE = shutil.which("node")


class SixRng:
    def randint(self, minimum, maximum):
        del minimum, maximum
        return 6


def rolled_state():
    participants = [
        {"player_id": "human-1", "token": "red"},
        {"player_id": "ai-1", "token": "blue"},
    ]
    game = AeroplaneChess(SixRng())
    state = game.initialize(participants)
    game.apply_action(state, {"action": "roll"}, participants[0])
    return state


class AeroplaneChessFrontendStructureTests(unittest.TestCase):
    def test_renderer_and_styles_are_independent_and_registry_loaded(self):
        self.assertIn(
            'window.DuelGameUI.register("aeroplane_chess", renderer);', SCRIPT
        )
        self.assertIn("function renderBoard(context)", SCRIPT)
        self.assertIn("function renderControls(context)", SCRIPT)
        self.assertIn("usesStandardMoveConfirmation: false", SCRIPT)
        self.assertIn('participantPresentation: "board-edge"', SCRIPT)
        self.assertIn("helpers.submitMove", SCRIPT)
        self.assertIn("context.legalActions", SCRIPT)
        self.assertIn("context.legalMoves", SCRIPT)
        self.assertNotIn("aeroplane_chess", APP_SCRIPT)
        self.assertNotIn("aeroplane", PUBLIC_STYLES.lower())
        self.assertNotIn("/static/games/aeroplane_chess.js", HTML)

    def test_stylesheet_is_loaded_idempotently_from_the_renderer(self):
        self.assertIn("function ensureStylesheet(documentRef)", SCRIPT)
        self.assertIn(
            'const STYLE_HREF = "/static/games/aeroplane_chess.css?v=0.1.1";',
            SCRIPT,
        )
        self.assertIn('link.rel = "stylesheet";', SCRIPT)
        self.assertIn('link.dataset.duelGameStyle = "aeroplane_chess";', SCRIPT)
        self.assertNotIn("aeroplane_chess.css", HTML)

    def test_board_is_vector_dom_not_image_or_emoji(self):
        for required in (
            'createElementNS(SVG_NS, tag)',
            'class: "aeroplane-board-base"',
            'class: `aeroplane-airport color-${color}`',
            'class: `aeroplane-home-lane color-${color}`',
            'class: `aeroplane-shortcut-line color-${color}`',
            'class: "aeroplane-token-body"',
            'className = "aeroplane-legal-target"',
            'board.dataset.viewerRotation',
        ):
            self.assertIn(required, SCRIPT)
        self.assertNotIn("<img", SCRIPT.lower())
        self.assertNotIn("background-image: url", STYLES.lower())
        for emoji in ("✈", "🛩", "🎲", "🚀", "🔴", "🟡", "🔵", "🟢"):
            self.assertNotIn(emoji, SCRIPT + STYLES)

    def test_dice_feedback_touch_targets_and_mobile_layout_are_explicit(self):
        self.assertIn("const PIP_POSITIONS", SCRIPT)
        self.assertIn(
            'pip.className = `aeroplane-pip${active.has(position) ? " on" : ""}`;',
            SCRIPT,
        )
        self.assertIn(".aeroplane-pip.on { background: currentColor; }", STYLES)
        self.assertIn(".aeroplane-token.legal {", STYLES)
        self.assertIn("min-width: 44px;", STYLES)
        self.assertIn("min-height: 44px;", STYLES)
        self.assertIn("touch-action: manipulation;", STYLES)
        mobile = STYLES[STYLES.index("@media (max-width: 599px)"):]
        self.assertIn("width: min(96vw, 680px);", mobile)
        self.assertIn("max-width: 100%;", mobile)
        self.assertIn("@media (max-width: 359px)", mobile)
        self.assertNotIn("overflow-x: auto", STYLES)


@unittest.skipUnless(NODE, "node is required for renderer DOM tests")
class AeroplaneChessFrontendRuntimeTests(unittest.TestCase):
    def run_node(self, assertions, *, state=None, participants=None):
        state = state or rolled_state()
        participants = participants or [
            {
                "player_id": "human-1", "display_name": "南山", "token": "red",
                "seat_index": 0, "participant_kind": "human",
            },
            {
                "player_id": "ai-1", "display_name": "小机", "token": "blue",
                "seat_index": 1, "participant_kind": "system_npc",
            },
        ]
        state_json = json.dumps(state, ensure_ascii=False)
        participants_json = json.dumps(participants, ensure_ascii=False)
        harness = r'''
const assert = require("node:assert/strict");
const vm = require("node:vm");
const fs = require("node:fs");

class ClassList {
  constructor(owner) { this.owner = owner; this.names = new Set(); }
  set(value) { this.names = new Set(String(value || "").split(/\s+/).filter(Boolean)); }
  add(...names) { names.forEach((name) => this.names.add(name)); }
  contains(name) { return this.names.has(name); }
}
class Element {
  constructor(tag) {
    this.tag = tag;
    this.children = [];
    this.dataset = {};
    this.attributes = {};
    this.listeners = {};
    this.style = {setProperty(name, value) { this[name] = String(value); }};
    this.classList = new ClassList(this);
    this.disabled = false;
    this.textContent = "";
    this.id = "";
  }
  set className(value) { this.classList.set(value); }
  get className() { return [...this.classList.names].join(" "); }
  appendChild(child) { this.children.push(child); return child; }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; }
  setAttribute(name, value) {
    this.attributes[name] = String(value);
    if (name === "class") this.className = value;
  }
  addEventListener(name, listener) { this.listeners[name] = listener; }
}
const styleNodes = new Map();
const document = {
  head: {appendChild(element) { if (element.id) styleNodes.set(element.id, element); }},
  createElement(tag) { return new Element(tag); },
  createElementNS(_namespace, tag) { return new Element(tag); },
  getElementById(id) { return styleNodes.get(id) || null; },
};
let renderer = null;
const window = {document, DuelGameUI: {register(gameType, candidate) {
  assert.equal(gameType, "aeroplane_chess");
  renderer = candidate;
}}};
vm.runInNewContext(fs.readFileSync("app/static/games/aeroplane_chess.js", "utf8"), {
  window, document, console, Math, Set, Map, Number, String, Boolean, Array, Object, Promise,
});
assert.ok(renderer);

function descendants(root) {
  const result = [];
  const visit = (node) => {
    result.push(node);
    node.children.forEach(visit);
  };
  root.children.forEach(visit);
  return result;
}
function hasClass(node, name) { return node.classList && node.classList.contains(name); }
const state = STATE_JSON;
const participants = PARTICIPANTS_JSON;
function makeContext(viewerId, canMove = true) {
  const board = new Element("div");
  const controls = new Element("div");
  const submitted = [];
  const context = {
    board, controls, state, participants,
    room: {current_player_id: participants[0].player_id, status: "playing"},
    viewer: {player_id: viewerId}, canMove, isTerminal: false,
    legalActions: state.legal_actions, legalMoves: state.legal_moves,
    helpers: {
      setBoardLayout(options) { board.attributes.ariaLabel = options.ariaLabel; },
      renderParticipantAvatar(target, participant) {
        target.textContent = `avatar:${participant.player_id}`;
      },
      canMove() { return canMove; },
      async submitMove(move) { submitted.push(move); return true; },
    },
  };
  return {context, board, controls, submitted};
}
'''.replace("STATE_JSON", state_json).replace(
            "PARTICIPANTS_JSON", participants_json
        ) + assertions
        completed = subprocess.run(
            [NODE, "-e", harness],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_full_board_rotates_view_and_only_authoritative_planes_submit(self):
        self.run_node(r'''
(async () => {
  const first = makeContext("human-1", true);
  assert.equal(renderer.renderBoard(first.context), true);
  renderer.renderControls(first.context);
  let nodes = descendants(first.board);
  assert.equal(nodes.filter((node) => hasClass(node, "aeroplane-track-cell")).length, 52);
  assert.equal(nodes.filter((node) => hasClass(node, "aeroplane-home-lane")).length, 24);
  assert.equal(nodes.filter((node) => hasClass(node, "aeroplane-airport")).length, 4);
  assert.equal(nodes.filter((node) => hasClass(node, "aeroplane-launch")).length, 4);
  assert.equal(nodes.filter((node) => hasClass(node, "aeroplane-shortcut-line")).length, 4);
  const tokens = nodes.filter((node) => hasClass(node, "aeroplane-token"));
  assert.equal(tokens.length, 8);
  const legalTokens = tokens.filter((node) => hasClass(node, "legal"));
  assert.equal(legalTokens.length, 4);
  assert.equal(tokens.filter((node) => node.dataset.playerId === "ai-1" && !node.disabled).length, 0);
  assert.equal(nodes.filter((node) => hasClass(node, "aeroplane-legal-target")).length, 1);
  legalTokens[2].listeners.click();
  await Promise.resolve();
  assert.equal(JSON.stringify(first.submitted[0]), JSON.stringify({
    action: "move", plane_id: "red-2", plane_index: 2,
  }));
  const rollButton = descendants(first.controls).find(
    (node) => hasClass(node, "aeroplane-roll-button")
  );
  assert.equal(rollButton.disabled, true);

  const rotated = makeContext("ai-1", false);
  renderer.renderBoard(rotated.context);
  assert.equal(rotated.board.dataset.viewerColor, "blue");
  assert.equal(rotated.board.dataset.viewerRotation, "180");
  assert.equal(styleNodes.size, 1);
  const stylesheet = styleNodes.get("duel-game-aeroplane-chess-styles");
  assert.equal(stylesheet.href, "/static/games/aeroplane_chess.css?v=0.1.1");
  assert.equal(stylesheet.dataset.duelGameStyle, "aeroplane_chess");
})().catch((error) => { console.error(error); process.exitCode = 1; });
''')

    def test_awaiting_roll_control_submits_only_roll_action(self):
        self.run_node(r'''
(async () => {
  state.flow.phase = "awaiting_roll";
  state.legal_actions = [{action: "roll"}];
  state.legal_moves = [];
  state.turn_player_id = null;
  const harness = makeContext("human-1", true);
  harness.context.legalActions = state.legal_actions;
  harness.context.legalMoves = [];
  renderer.renderControls(harness.context);
  const rollButton = descendants(harness.controls).find(
    (node) => hasClass(node, "aeroplane-roll-button")
  );
  assert.equal(rollButton.disabled, false);
  rollButton.listeners.click();
  await Promise.resolve();
  assert.equal(JSON.stringify(harness.submitted), JSON.stringify([{action: "roll"}]));
})().catch((error) => { console.error(error); process.exitCode = 1; });
''')

    def test_multiplayer_edge_identities_follow_rotated_airports(self):
        participants = [
            {
                "player_id": f"p-{index}",
                "display_name": f"玩家{index}",
                "token": color,
                "seat_index": index,
                "role": "human" if index == 0 else "ai",
                "participant_kind": "human" if index == 0 else "system_npc",
            }
            for index, color in enumerate(("red", "yellow", "blue", "green"))
        ]
        state = AeroplaneChess().initialize(participants)
        self.run_node(r'''
for (const participant of participants) {
  const harness = makeContext(participant.player_id, false);
  renderer.renderBoard(harness.context);
  renderer.renderControls(harness.context);
  const nodes = descendants(harness.board);
  const identities = nodes.filter((node) => hasClass(node, "aeroplane-edge-participant"));
  assert.equal(identities.length, 4);
  const viewerIdentity = identities.find(
    (node) => node.dataset.playerId === participant.player_id
  );
  assert.equal(viewerIdentity.dataset.visualEdge, "bottom-left");
  assert.equal(viewerIdentity.dataset.color, state.color_by_player[participant.player_id]);
  assert.equal(viewerIdentity.children[0].textContent, `avatar:${participant.player_id}`);
  assert.match(viewerIdentity.children[1].children[1].textContent, /方$/);

  const shell = nodes.find((node) => hasClass(node, "aeroplane-board-shell"));
  assert.ok(shell);
  assert.equal(
    descendants(shell).some((node) => hasClass(node, "board-edge-participant")),
    false
  );
  assert.equal(
    descendants(harness.controls).filter((node) => hasClass(node, "aeroplane-roster-item")).length,
    0
  );
}
''', state=state, participants=participants)
        self.assertIn("grid-template-columns: repeat(2, minmax(0, 1fr));", STYLES)
        self.assertIn('data-visual-edge$="-right"', STYLES)
        for viewport in (320, 375):
            self.assertLessEqual(min(viewport * 0.96, 680), viewport)

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

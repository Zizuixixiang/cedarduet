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
            'const STYLE_HREF = "/static/games/aeroplane_chess.css?v=0.2.2";',
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
            'class: `aeroplane-shortcut-arrow color-${color}`',
            'class: "aeroplane-token-body"',
            'className = "aeroplane-legal-target"',
            'board.dataset.viewerRotation',
        ):
            self.assertIn(required, SCRIPT)
        self.assertNotIn("<img", SCRIPT.lower())
        self.assertNotIn("background-image: url", STYLES.lower())
        self.assertIn('"data-ring-index": ringIndex', SCRIPT)
        self.assertIn("const TRACK_MIN = 18;", SCRIPT)
        self.assertIn("const TRACK_MAX = 82;", SCRIPT)
        self.assertNotIn("aeroplane-last-route", SCRIPT + STYLES)
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
        activity_copy = STYLES[
            STYLES.index(".aeroplane-activity > span:last-child {"):
            STYLES.index(".aeroplane-die {")
        ]
        self.assertNotIn("max-height:", activity_copy)
        self.assertNotIn("overflow:", activity_copy)
        self.assertIn("white-space: normal;", STYLES)
        self.assertIn("min-height: 34px;", mobile)
        self.assertIn(":has(.board.aeroplane_chess)", STYLES)
        self.assertIn(
            "--aeroplane-token-size: clamp(13px, 3.2vw, 21px);", STYLES
        )
        self.assertIn(
            "--aeroplane-token-size: clamp(12px, 3.65vw, 17px);", mobile
        )
        self.assertIn("width: var(--aeroplane-token-size);", STYLES)
        self.assertIn(".aeroplane-token.legal .aeroplane-token-svg", STYLES)
        for viewport in (320, 360, 375, 390, 430, 599):
            board_width = min(viewport * 0.96, 680)
            cell_width = board_width * 0.05
            token_width = min(max(12, viewport * 0.0365), 17)
            self.assertLess(token_width, cell_width)
        for viewport in (360, 375, 390, 430):
            board_width = viewport * 0.96
            track_side = board_width * 0.64
            airport_width = board_width * 0.12
            airport_slot_spacing = board_width * 0.055
            token_width = min(max(12, viewport * 0.0365), 17)
            airport_outer_slot_center = board_width * 0.0675
            self.assertGreater(track_side, airport_width * 5)
            self.assertGreater(airport_slot_spacing, token_width)
            self.assertGreaterEqual(airport_outer_slot_center, 22)


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
  assert.equal(nodes.filter((node) => hasClass(node, "aeroplane-shortcut-arrow")).length, 4);
  const airports = nodes.filter((node) => hasClass(node, "aeroplane-airport"));
  assert.equal(airports.every((node) => Number(node.attributes.width) === 12), true);
  assert.equal(airports.every((node) => Number(node.attributes.height) === 12), true);
  const airportSlots = nodes.filter((node) => hasClass(node, "aeroplane-airport-slot"));
  assert.equal(airportSlots.length, 16);
  assert.equal(airportSlots.every((node) => Number(node.attributes.r) === 2.35), true);
  const ringCells = nodes.filter((node) => hasClass(node, "aeroplane-track-cell"));
  const ringCenter = (index) => {
    const cell = ringCells.find((node) => node.attributes["data-ring-index"] === String(index));
    return [
      Number(cell.attributes.x) + Number(cell.attributes.width) / 2,
      Number(cell.attributes.y) + Number(cell.attributes.height) / 2,
    ];
  };
  assert.deepEqual(ringCenter(0), [50, 82]);
  assert.deepEqual(ringCenter(13), [18, 50]);
  assert.deepEqual(ringCenter(26), [50, 18]);
  assert.deepEqual(ringCenter(39), [82, 50]);
  const redFirstHome = nodes.find((node) => (
    hasClass(node, "aeroplane-home-lane")
      && hasClass(node, "color-red")
      && node.attributes["data-lane-index"] === "1"
  ));
  assert.deepEqual([
    Number(redFirstHome.attributes.x) + Number(redFirstHome.attributes.width) / 2,
    Number(redFirstHome.attributes.y) + Number(redFirstHome.attributes.height) / 2,
  ], [50, 77]);
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
  assert.equal(stylesheet.href, "/static/games/aeroplane_chess.css?v=0.2.2");
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
  assert.equal(rollButton.textContent, "掷骰");
  assert.equal(rollButton.classList.contains("ready"), true);
  const controlCopy = descendants(harness.controls).find(
    (node) => hasClass(node, "aeroplane-action-copy")
  );
  assert.equal(controlCopy.children[0].textContent, "轮到你掷骰");
  rollButton.listeners.click();
  await Promise.resolve();
  assert.equal(JSON.stringify(harness.submitted), JSON.stringify([{action: "roll"}]));
})().catch((error) => { console.error(error); process.exitCode = 1; });
''')

    def test_waiting_copy_names_player_and_duel_stats_are_removed(self):
        self.run_node(r'''
state.flow.phase = "awaiting_roll";
state.legal_actions = [{action: "roll"}];
state.legal_moves = [];
const harness = makeContext("ai-1", false);
harness.context.legalActions = state.legal_actions;
harness.context.legalMoves = [];
renderer.renderBoard(harness.context);
renderer.renderControls(harness.context);
const boardStatus = descendants(harness.board).find(
  (node) => hasClass(node, "aeroplane-board-heading")
).children[1];
assert.equal(boardStatus.textContent, "等待南山掷骰");
const actionCopy = descendants(harness.controls).find(
  (node) => hasClass(node, "aeroplane-action-copy")
);
assert.equal(actionCopy.children[0].textContent, "等待南山掷骰");
const rollButton = descendants(harness.controls).find(
  (node) => hasClass(node, "aeroplane-roll-button")
);
assert.equal(rollButton.textContent, "等待");
assert.equal(rollButton.disabled, true);
assert.equal(
  descendants(harness.controls).some((node) => hasClass(node, "aeroplane-roster-item")),
  false
);
assert.equal(
  descendants(harness.controls).some((node) => /到家|机场/.test(node.textContent)),
  false
);
''')

    def test_same_cell_planes_are_compact_and_visibly_offset(self):
        state = rolled_state()
        for plane in state["planes"]["human-1"]:
            plane.update({
                "zone": "launch",
                "route_step": 0,
                "ring_index": None,
                "home_lane_index": None,
            })
        state["flow"]["phase"] = "awaiting_roll"
        state["legal_actions"] = [{"action": "roll"}]
        state["legal_moves"] = []
        self.run_node(r'''
const harness = makeContext("ai-1", false);
harness.context.legalActions = state.legal_actions;
harness.context.legalMoves = [];
renderer.renderBoard(harness.context);
const stacked = descendants(harness.board).filter(
  (node) => hasClass(node, "aeroplane-token")
    && node.dataset.playerId === "human-1"
    && node.dataset.logicalZone === "launch"
);
assert.equal(stacked.length, 4);
assert.equal(stacked.every((node) => node.dataset.stackSize === "4"), true);
assert.equal(new Set(stacked.map((node) => `${node.style.left}:${node.style.top}`)).size, 4);
''', state=state)

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
        self.assertIn("overflow: hidden;", STYLES)
        self.assertIn("text-overflow: ellipsis;", STYLES)
        self.assertIn(".aeroplane-edge-participant .board-edge-avatar", STYLES)
        self.assertIn(".aeroplane-edge-participant .board-edge-copy", STYLES)
        for viewport in (320, 360, 375, 390, 430):
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

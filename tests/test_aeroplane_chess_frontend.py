import json
import shutil
import subprocess
import unittest
from pathlib import Path

from app.games.aeroplane_chess import FINISH_ROUTE_STEP, AeroplaneChess


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


class ThreeRng:
    def randint(self, minimum, maximum):
        del minimum, maximum
        return 3


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
        self.assertIn("usesEmbeddedActionFeedback: true", SCRIPT)
        self.assertIn('participantPresentation: "board-edge"', SCRIPT)
        self.assertIn("helpers.submitMove", SCRIPT)
        self.assertIn("context.legalActions", SCRIPT)
        self.assertIn("context.legalMoves", SCRIPT)
        self.assertNotIn("aeroplane_chess", APP_SCRIPT)
        self.assertNotIn("aeroplane", PUBLIC_STYLES.lower())
        self.assertNotIn("/static/games/aeroplane_chess.js", HTML)
        self.assertEqual(APP_SCRIPT.count("await showRoomTransitionFeedback("), 2)
        action_notice = APP_SCRIPT[
            APP_SCRIPT.index("function roomActionNotice("):
            APP_SCRIPT.index("function renderGame(")
        ]
        self.assertIn("renderer.usesEmbeddedActionFeedback === true", action_notice)
        self.assertIn('return "";', action_notice)
        for function_name, end_name in (
            ("async function refreshRoom(", "async function submitMove("),
            ("async function submitMove(", "async function acknowledgeLiarsRound("),
        ):
            section = APP_SCRIPT[
                APP_SCRIPT.index(function_name):APP_SCRIPT.index(end_name)
            ]
            self.assertLess(
                section.index("await showRoomTransitionFeedback("),
                section.index("renderGame("),
            )
            for unexpected in ("scrollIntoView", "window.scrollTo", ".focus("):
                self.assertNotIn(unexpected, section)

    def test_stylesheet_is_loaded_idempotently_from_the_renderer(self):
        self.assertIn("function ensureStylesheet(documentRef)", SCRIPT)
        self.assertIn(
            'const STYLE_HREF = "/static/games/aeroplane_chess.css?v=0.2.7";',
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
        self.assertNotIn("aeroplane-token-cockpit", SCRIPT + STYLES)
        self.assertIn(
            'd: "M32 4 C35 4 37 8 37 13 L37 23 L58 34 L58 40 L37 35 '
            'L36 51 L45 57 L45 61 L32 57 L19 61 L19 57 L28 51 L27 35 L6 '
            '40 L6 34 L27 23 L27 13 C27 8 29 4 32 4 Z"',
            SCRIPT,
        )
        self.assertIn(
            'd: "M32 3 C35 3 36 8 36 13 L36 24 L57 34 L57 38 L36 34 '
            'L35 51 L44 57 L44 59 L32 56 L20 59 L20 57 L29 51 L28 34 L7 '
            '38 L7 34 L28 24 L28 13 C28 8 29 3 32 3 Z"',
            SCRIPT,
        )
        self.assertIn('"data-ring-index": ringIndex', SCRIPT)
        self.assertIn("const TRACK_EDGE_MIN = 8;", SCRIPT)
        self.assertIn("const TRACK_EDGE_MAX = 92;", SCRIPT)
        self.assertIn("const TRACK_QUADRANT_POINTS", SCRIPT)
        self.assertIn('"data-corner-cut": "45deg"', SCRIPT)
        self.assertNotIn("const TRACK_MIN = 18;", SCRIPT)
        self.assertNotIn("const TRACK_MAX = 82;", SCRIPT)
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
        disabled_rule = STYLES[
            STYLES.index(".aeroplane-token:disabled {"):
            STYLES.index(".aeroplane-token.legal {")
        ]
        self.assertIn("pointer-events: none;", disabled_rule)
        legal_rule = STYLES[
            STYLES.index(".aeroplane-token.legal {"):
            STYLES.index(".aeroplane-token.legal::before {")
        ]
        self.assertIn("z-index: calc(40 + var(--stack-order, 0));", legal_rule)
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
        self.assertIn("width: calc(var(--aeroplane-token-size) + 3px);", STYLES)
        token_base = STYLES[
            STYLES.index(".aeroplane-token::before {"):
            STYLES.index(".aeroplane-token.color-red")
        ]
        self.assertIn("background: transparent;", token_base)
        self.assertIn("border: 1.5px solid var(--plane-dark);", token_base)
        self.assertIn("0 0 0 1px rgba(255, 255, 255, .76)", token_base)
        self.assertNotIn("rgba(255, 253, 247, .72)", token_base)
        legal_base = STYLES[
            STYLES.index(".aeroplane-token.legal::before {"):
            STYLES.index(".aeroplane-token.legal:hover")
        ]
        self.assertIn("background: transparent;", legal_base)
        self.assertIn("border: 2px solid #a96516;", legal_base)
        self.assertIn("0 0 0 2px", legal_base)
        last_moved = STYLES[
            STYLES.index(".aeroplane-token.last-moved {"):
            STYLES.index(".aeroplane-token.recently-returned")
        ]
        self.assertIn("box-shadow: none;", last_moved)
        self.assertIn(".aeroplane-token.last-moved::before", last_moved)
        self.assertIn("stroke-width: 2.6;", STYLES)
        self.assertIn("paint-order: stroke fill;", STYLES)
        self.assertIn("drop-shadow(0 0 .35px", STYLES)
        self.assertNotIn("drop-shadow(0 0 3px", STYLES)
        self.assertNotIn("drop-shadow(0 0 8px", STYLES)
        self.assertNotIn(".aeroplane-token.arrived-home", STYLES)
        self.assertNotIn("aeroplane-move-badge", SCRIPT + STYLES)
        self.assertIn(".aeroplane-activity.npc-feedback-active {", STYLES)
        home_runway = STYLES[
            STYLES.index(".aeroplane-home-runway {"):
            STYLES.index(".aeroplane-home-runway.color-red")
        ]
        self.assertIn("stroke-linecap: butt;", home_runway)
        for viewport in (320, 360, 375, 390, 430, 599):
            board_width = min(viewport * 0.96, 680)
            cell_width = board_width * 0.052
            token_width = min(max(12, viewport * 0.0365), 17)
            self.assertLess(token_width, cell_width)
            self.assertLessEqual(token_width + 3, cell_width)
        for viewport in (360, 375, 390, 430):
            board_width = viewport * 0.96
            track_side = board_width * 0.84
            airport_width = board_width * 0.12
            airport_slot_spacing = board_width * 0.055
            token_width = min(max(12, viewport * 0.0365), 17)
            airport_outer_slot_center = board_width * 0.0675
            track_edge_center = board_width * 0.08
            self.assertGreater(track_side, airport_width * 5)
            self.assertGreater(airport_slot_spacing, token_width)
            self.assertGreaterEqual(airport_outer_slot_center, 22)
            self.assertGreater(track_edge_center, 22)
            self.assertGreater(track_side / board_width, .8)
        for viewport in (320, 360, 375, 390, 430):
            board_width = viewport * 0.96
            final_cell_to_center_shape = board_width * 0.015
            final_cell_to_home_core = board_width * 0.053
            self.assertGreaterEqual(final_cell_to_center_shape, 4.5)
            self.assertGreaterEqual(final_cell_to_home_core, 16)


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
  remove(...names) { names.forEach((name) => this.names.delete(name)); }
  toggle(name, force) {
    const enabled = force === undefined ? !this.names.has(name) : Boolean(force);
    if (enabled) this.names.add(name); else this.names.delete(name);
    return enabled;
  }
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
const idNodes = new Map();
const queryNodes = new Map();
const feedbackDelays = [];
let feedbackTimeoutObserver = null;
const document = {
  head: {appendChild(element) { if (element.id) styleNodes.set(element.id, element); }},
  createElement(tag) { return new Element(tag); },
  createElementNS(_namespace, tag) { return new Element(tag); },
  getElementById(id) { return styleNodes.get(id) || idNodes.get(id) || null; },
  querySelector(selector) { return queryNodes.get(selector) || null; },
};
for (const id of ["aiName", "humanName"]) {
  const name = new Element("strong");
  name.id = id;
  name.textContent = id === "aiName" ? "小机" : "南山";
  idNodes.set(id, name);
}
let renderer = null;
const window = {
  document,
  setTimeout(callback, delay) {
    feedbackDelays.push(delay);
    if (feedbackTimeoutObserver) feedbackTimeoutObserver(delay);
    callback();
  },
  DuelGameUI: {register(gameType, candidate) {
    assert.equal(gameType, "aeroplane_chess");
    renderer = candidate;
  }},
};
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
  assert.equal(airports.every((node) => node.tag === "polygon"), true);
  assert.equal(airports.every((node) => node.attributes["data-corner-cut"] === "45deg"), true);
  assert.equal(airports.every((node) => node.attributes.points.split(" ").length === 5), true);
  assert.equal(
    airports.find((node) => hasClass(node, "color-red")).attributes.points,
    "3.5,84.5 9.5,84.5 15.5,90.5 15.5,96.5 3.5,96.5"
  );
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
  assert.deepEqual(ringCenter(0), [50, 92]);
  assert.deepEqual(ringCenter(13), [8, 50]);
  assert.deepEqual(ringCenter(26), [50, 8]);
  assert.deepEqual(ringCenter(39), [92, 50]);
  const diagonal = [6, 7, 8, 9].map(ringCenter);
  for (let index = 1; index < diagonal.length; index += 1) {
    const dx = diagonal[index][0] - diagonal[index - 1][0];
    const dy = diagonal[index][1] - diagonal[index - 1][1];
    assert.equal(Math.abs(dx), Math.abs(dy));
  }
  const redFirstHome = nodes.find((node) => (
    hasClass(node, "aeroplane-home-lane")
      && hasClass(node, "color-red")
      && node.attributes["data-lane-index"] === "1"
  ));
  assert.deepEqual([
    Number(redFirstHome.attributes.x) + Number(redFirstHome.attributes.width) / 2,
    Number(redFirstHome.attributes.y) + Number(redFirstHome.attributes.height) / 2,
  ], [50, 85.5]);
  const laneCenter = (color, index) => {
    const cell = nodes.find((node) => (
      hasClass(node, "aeroplane-home-lane")
        && hasClass(node, `color-${color}`)
        && node.attributes["data-lane-index"] === String(index)
    ));
    return [
      Number(cell.attributes.x) + Number(cell.attributes.width) / 2,
      Number(cell.attributes.y) + Number(cell.attributes.height) / 2,
    ];
  };
  const finalLaneCenters = {
    red: laneCenter("red", 6),
    yellow: laneCenter("yellow", 6),
    blue: laneCenter("blue", 6),
    green: laneCenter("green", 6),
  };
  assert.deepEqual(finalLaneCenters, {
    red: [50, 60],
    yellow: [40, 50],
    blue: [50, 40],
    green: [60, 50],
  });
  assert.deepEqual(
    Object.values(finalLaneCenters).map(([x, y]) => Math.hypot(x - 50, y - 50)),
    [10, 10, 10, 10]
  );
  for (const color of ["red", "yellow", "blue", "green"]) {
    const radialDistances = Array.from({length: 6}, (_, index) => {
      const [x, y] = laneCenter(color, index + 1);
      return Math.hypot(x - 50, y - 50);
    });
    const gaps = radialDistances.slice(0, -1).map(
      (distance, index) => Number((distance - radialDistances[index + 1]).toFixed(1))
    );
    assert.deepEqual(gaps, [5.1, 5.1, 5.1, 5.1, 5.1]);
  }
  const finalCellHalfSize = 2.5;
  const centerShapeRadius = 6;
  const homeCoreRadius = 2.2;
  assert.equal(10 - finalCellHalfSize - centerShapeRadius, 1.5);
  assert.equal(10 - finalCellHalfSize - homeCoreRadius, 5.3);
  const tokens = nodes.filter((node) => hasClass(node, "aeroplane-token"));
  assert.equal(tokens.length, 8);
  assert.equal(tokens.every((node) => node.tag === "button"), true);
  assert.equal(tokens.every((node) => node.children.length === 1), true);
  assert.equal(tokens.every((node) => hasClass(node.children[0], "aeroplane-token-svg")), true);
  assert.equal(tokens.every((node) => node.children[0].children.length === 3), true);
  assert.equal(
    tokens.some((node) => descendants(node.children[0]).some(
      (child) => hasClass(child, "aeroplane-token-cockpit")
    )),
    false
  );
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
  assert.equal(stylesheet.href, "/static/games/aeroplane_chess.css?v=0.2.7");
  assert.equal(stylesheet.dataset.duelGameStyle, "aeroplane_chess");
})().catch((error) => { console.error(error); process.exitCode = 1; });
''')

    def test_npc_transition_feedback_keeps_roll_and_move_results_visible(self):
        self.run_node(r'''
(async () => {
const previousTimeline = [{sequence: 20, event_type: "message", text: "此前"}];
const nextTimeline = [
  ...previousTimeline,
  {
    sequence: 21, revision_at_send: 8, event_type: "move",
    sender: {name: "许知衡", participant_kind: "system_npc"},
    move: {action: "roll"},
  },
  {
    sequence: 22, revision_at_send: 8, event_type: "result",
    text: "掷出 4 点，请从 2 架可移动飞机中选择。",
    move: {aeroplane_delta: {action: "roll", value: 4, auto_pass: false}},
  },
  {
    sequence: 23, revision_at_send: 9, event_type: "move",
    sender: {name: "许知衡", participant_kind: "system_npc"},
    move: {action: "move", plane_id: "blue-0"},
  },
  {
    sequence: 24, revision_at_send: 9, event_type: "result",
    text: "蓝方 1 号机前进 4 点，并触发跳跃。",
    move: {aeroplane_delta: {action: "move", die: 4, plane_id: "blue-0"}},
  },
  {
    sequence: 25, revision_at_send: 10, event_type: "move",
    sender: {name: "许知衡", participant_kind: "system_npc"},
    move: {action: "roll"},
  },
  {
    sequence: 26, revision_at_send: 10, event_type: "result",
    text: "掷出 2 点但没有可移动飞机，服务端已自动结束本回合。",
    move: {aeroplane_delta: {action: "roll", value: 2, auto_pass: true}},
  },
  {
    sequence: 27, revision_at_send: 11, event_type: "move",
    sender: {name: "真人", participant_kind: "human"},
    move: {action: "roll"},
  },
  {
    sequence: 28, revision_at_send: 11, event_type: "result",
    text: "掷出 6 点。",
    move: {aeroplane_delta: {action: "roll", value: 6}},
  },
];
const beats = renderer.transitionFeedbackBeats({previousTimeline, nextTimeline});
assert.deepEqual(JSON.parse(JSON.stringify(beats)), [
  {phase: "roll", text: "许知衡掷出 4 点", dieValue: 4, durationMs: 1200},
  {
    phase: "move",
    text: "许知衡：蓝方 1 号机前进 4 点，并触发跳跃。",
    dieValue: 4,
    durationMs: 1200,
  },
  {phase: "roll", text: "许知衡掷出 2 点", dieValue: 2, durationMs: 1200},
  {
    phase: "result",
    text: "许知衡：没有可移动飞机，服务端已自动结束本回合。",
    dieValue: 2,
    durationMs: 1200,
  },
]);
assert.equal(typeof renderer.transitionFeedback, "function");

const activity = new Element("div");
activity.className = "aeroplane-activity";
const die = new Element("span");
die.className = "aeroplane-die compact empty";
for (let index = 0; index < 9; index += 1) {
  const pip = new Element("span");
  pip.className = "aeroplane-pip";
  die.appendChild(pip);
}
const activityCopy = new Element("span");
const headingCopy = new Element("span");
activity.append(die, activityCopy);
queryNodes.set(".aeroplane-activity", activity);
queryNodes.set(".aeroplane-activity > span:last-child", activityCopy);
queryNodes.set(".aeroplane-board-heading > span:last-child", headingCopy);
queryNodes.set(".aeroplane-activity .aeroplane-die", die);

const snapshots = [];
feedbackTimeoutObserver = () => snapshots.push({
  text: activityCopy.textContent,
  heading: headingCopy.textContent,
  phase: activity.dataset.npcFeedbackPhase,
  pips: die.children.map((pip, index) => (
    pip.classList.contains("on") ? index + 1 : null
  )).filter(Boolean),
  active: activity.classList.contains("npc-feedback-active"),
});
await renderer.transitionFeedback({document, previousTimeline, nextTimeline});
assert.deepEqual(feedbackDelays, [1200, 1200, 1200, 1200]);
assert.equal(
  JSON.stringify(snapshots.map((item) => item.text)),
  JSON.stringify(Array.from(beats, (item) => item.text))
);
assert.equal(
  JSON.stringify(snapshots.map((item) => item.heading)),
  JSON.stringify(Array.from(beats, (item) => item.text))
);
assert.deepEqual(snapshots.map((item) => item.phase), ["roll", "move", "roll", "result"]);
assert.deepEqual(snapshots[0].pips, [1, 3, 7, 9]);
assert.deepEqual(snapshots[2].pips, [1, 9]);
assert.equal(snapshots.every((item) => item.active), true);
assert.equal(die.attributes["aria-label"], "2 点");
assert.equal(activity.classList.contains("npc-feedback-active"), false);
assert.equal("npcFeedbackPhase" in activity.dataset, false);
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
  const pendingRoll = rollButton.listeners.click();
  assert.equal(rollButton.disabled, true);
  assert.equal(rollButton.textContent, "掷骰");
  await pendingRoll;
  assert.equal(JSON.stringify(harness.submitted), JSON.stringify([{action: "roll"}]));
})().catch((error) => { console.error(error); process.exitCode = 1; });
''')

    def test_bounce_legal_target_uses_authoritative_return_cell(self):
        participants = [
            {
                "player_id": "human-1", "display_name": "南山",
                "token": "red", "seat_index": 0,
                "participant_kind": "human",
            },
            {
                "player_id": "ai-1", "display_name": "小机",
                "token": "blue", "seat_index": 1,
                "participant_kind": "system_npc",
            },
        ]
        game = AeroplaneChess(ThreeRng())
        state = game.initialize(participants)
        game._set_plane_step(
            state["planes"]["human-1"][0],
            "red",
            FINISH_ROUTE_STEP - 1,
        )
        game.apply_action(state, {"action": "roll"}, participants[0])
        self.run_node(r'''
const harness = makeContext("human-1", true);
renderer.renderBoard(harness.context);
const nodes = descendants(harness.board);
const legal = nodes.filter(
  (node) => hasClass(node, "aeroplane-token") && hasClass(node, "legal")
);
assert.equal(legal.length, 1);
assert.equal(legal[0].dataset.planeId, "red-0");
assert.equal(legal[0].children.length, 1);
const target = nodes.find((node) => hasClass(node, "aeroplane-legal-target"));
assert.equal(target.style.left, "50%");
assert.equal(target.style.top, "65.1%");
assert.equal(state.legal_moves[0].bounced, true);
assert.equal(state.legal_moves[0].bounce_steps, 2);
assert.equal(state.legal_moves[0].to.home_lane_index, 5);
''', state=state, participants=participants)

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

    def test_legal_planes_below_disabled_stack_tokens_keep_exact_click_targets(self):
        state = rolled_state()
        for plane_id, route_step, ring_index in (
            ("red-0", 5, 5),
            ("red-1", 5, 5),
            ("blue-3", 31, 5),
        ):
            player_id = "human-1" if plane_id.startswith("red") else "ai-1"
            plane = next(
                item for item in state["planes"][player_id]
                if item["plane_id"] == plane_id
            )
            plane.update({
                "zone": "track",
                "route_step": route_step,
                "ring_index": ring_index,
                "home_lane_index": None,
            })
        state["movable_plane_ids"] = ["red-0", "red-1"]
        state["legal_actions"] = [
            {"action": "move", "plane_id": "red-0", "plane_index": 0},
            {"action": "move", "plane_id": "red-1", "plane_index": 1},
        ]
        state["legal_moves"] = []
        state["last_roll"]["value"] = 4
        self.run_node(r'''
(async () => {
const harness = makeContext("human-1", true);
renderer.renderBoard(harness.context);
const stack = descendants(harness.board).filter(
  (node) => hasClass(node, "aeroplane-token")
    && node.dataset.logicalZone === "track"
    && ["red-0", "red-1", "blue-3"].includes(node.dataset.planeId)
);
assert.equal(stack.length, 3);
const legal = stack.filter((node) => hasClass(node, "legal"));
const blocker = stack.find((node) => node.dataset.planeId === "blue-3");
assert.equal(legal.length, 2);
assert.equal(blocker.disabled, true);
assert.equal(blocker.listeners.click, undefined);
assert.equal(Number(blocker.dataset.stackIndex), 2);
assert.equal(legal.every(
  (node) => Number(node.dataset.stackIndex) < Number(blocker.dataset.stackIndex)
), true);
await legal.find((node) => node.dataset.planeId === "red-0").listeners.click();
await legal.find((node) => node.dataset.planeId === "red-1").listeners.click();
assert.equal(JSON.stringify(harness.submitted), JSON.stringify([
  {action: "move", plane_id: "red-0", plane_index: 0},
  {action: "move", plane_id: "red-1", plane_index: 1},
]));
})().catch((error) => { console.error(error); process.exitCode = 1; });
''', state=state)

    def test_home_planes_leave_board_and_live_counts_cover_two_three_four_players(self):
        home_counts_by_player_count = {
            2: (0, 4),
            3: (0, 1, 4),
            4: (0, 1, 2, 4),
        }
        for count, expected_counts in home_counts_by_player_count.items():
            with self.subTest(count=count):
                participants = [
                    {
                        "player_id": f"p-{index}",
                        "display_name": f"玩家{index}",
                        "token": color,
                        "seat_index": index,
                        "role": "human" if index == 0 else "ai",
                        "participant_kind": (
                            "human" if index == 0 else "system_npc"
                        ),
                    }
                    for index, color in enumerate(
                        AeroplaneChess._colors_for_count(count)
                    )
                ]
                game = AeroplaneChess()
                state = game.initialize(participants)
                for participant, home_count in zip(
                    participants, expected_counts, strict=True
                ):
                    color = state["color_by_player"][participant["player_id"]]
                    for plane_index in range(home_count):
                        game._set_plane_step(
                            state["planes"][participant["player_id"]][plane_index],
                            color,
                            FINISH_ROUTE_STEP,
                        )
                state["last_action_note"] = (
                    f"{participants[-1]['display_name']}的 4 号机到达终点。"
                )
                self.run_node(r'''
const harness = makeContext(participants[0].player_id, false);
renderer.renderBoard(harness.context);
const nodes = descendants(harness.board);
const tokens = nodes.filter((node) => hasClass(node, "aeroplane-token"));
const totalHome = EXPECTED_COUNTS.reduce((total, value) => total + value, 0);
assert.equal(tokens.length, participants.length * 4 - totalHome);
assert.equal(tokens.some((node) => node.dataset.logicalZone === "home"), false);
assert.equal(tokens.some((node) => hasClass(node, "arrived-home")), false);
const activity = nodes.find((node) => hasClass(node, "aeroplane-activity"));
assert.equal(
  activity.children[1].textContent,
  `${participants[participants.length - 1].display_name}的 4 号机到达终点。`
);

if (participants.length === 2) {
  assert.equal(
    descendants(harness.board).filter(
      (node) => hasClass(node, "aeroplane-edge-participant")
    ).length,
    0
  );
  const humanName = idNodes.get("humanName");
  const aiName = idNodes.get("aiName");
  assert.equal(humanName.dataset.aeroplaneHomeCount, `${EXPECTED_COUNTS[0]}/4`);
  assert.equal(aiName.dataset.aeroplaneHomeCount, `${EXPECTED_COUNTS[1]}/4`);
  humanName.textContent = "轮询后的人类姓名";
  aiName.textContent = "轮询后的小机姓名";
  assert.equal(humanName.dataset.aeroplaneHomeCount, `${EXPECTED_COUNTS[0]}/4`);
  assert.equal(aiName.dataset.aeroplaneHomeCount, `${EXPECTED_COUNTS[1]}/4`);
} else {
  const identities = nodes.filter(
    (node) => hasClass(node, "aeroplane-edge-participant")
  );
  assert.equal(identities.length, participants.length);
  identities.forEach((identity) => {
    const playerIndex = participants.findIndex(
      (participant) => participant.player_id === identity.dataset.playerId
    );
    const nameRow = identity.children[1].children[0];
    const badge = nameRow.children.find(
      (node) => hasClass(node, "aeroplane-home-count")
    );
    assert.equal(badge.textContent, `${EXPECTED_COUNTS[playerIndex]}/4`);
    assert.match(identity.attributes["aria-label"], /已到家 [0-4]\/4/);
  });
}
'''.replace("EXPECTED_COUNTS", json.dumps(expected_counts)),
                    state=state,
                    participants=participants,
                )

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
  assert.equal(
    viewerIdentity.children[1].children[0].children[1].textContent,
    "0/4"
  );
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
        self.assertIn(".aeroplane-edge-name-row", STYLES)
        self.assertIn(".aeroplane-home-count", STYLES)
        self.assertIn(".player-name[data-aeroplane-home-count]", STYLES)
        self.assertIn("content: attr(data-aeroplane-home-count);", STYLES)
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

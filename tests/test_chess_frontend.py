import shutil
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "app" / "static" / "games" / "chess.js"
SCRIPT = SCRIPT_PATH.read_text(encoding="utf-8")
NODE = shutil.which("node")


class ChessFrontendStructureTests(unittest.TestCase):
    def test_renderer_uses_extension_contract_and_server_legal_moves(self):
        self.assertIn('window.DuelGameUI.register("chess"', SCRIPT)
        self.assertIn("function renderBoard(context)", SCRIPT)
        self.assertIn("function renderControls(context)", SCRIPT)
        self.assertIn("legalMoves.filter", SCRIPT)
        self.assertIn('action.action === "claim_draw"', SCRIPT)
        self.assertIn("helpers.selectMove(movePayload", SCRIPT)
        self.assertIn("helpers.submitMove({...claimAction})", SCRIPT)
        self.assertIn("helpers.setBoardLayout", SCRIPT)
        self.assertNotIn("new Chess(", SCRIPT)
        self.assertNotIn("generateMoves", SCRIPT)

    def test_pieces_are_inline_svg_paths_not_emoji(self):
        self.assertIn('document.createElementNS(SVG_NS, "svg")', SCRIPT)
        self.assertIn('document.createElementNS(SVG_NS, "path")', SCRIPT)
        for piece_type in ("p", "n", "b", "r", "q", "k"):
            self.assertIn(f"    {piece_type}: [", SCRIPT)
        for unicode_piece in "♔♕♖♗♘♙♚♛♜♝♞♟":
            self.assertNotIn(unicode_piece, SCRIPT)

    def test_visual_states_promotion_and_mobile_layout_are_explicit(self):
        for selector in (
            ".board.chess .chess-cell.selected-origin",
            ".board.chess .legal-target-dot",
            ".board.chess .legal-capture .legal-target-dot",
            ".board.chess .chess-cell.in-check",
            ".board.chess .chess-cell.last-move-from",
            ".board.chess .chess-cell.last-move-to",
            ".board.chess .chess-promotion-panel",
            ".chess-claim-controls",
            ".chess-claim-button",
        ):
            self.assertIn(selector, SCRIPT)
        self.assertIn('@media (max-width: 599px)', SCRIPT)
        self.assertIn("width: min(92vw, 560px)", SCRIPT)
        self.assertIn('panel.setAttribute("role", "dialog")', SCRIPT)
        self.assertIn('aria-label", "选择兵升变棋子"', SCRIPT)


@unittest.skipUnless(NODE, "node is required for chess renderer tests")
class ChessFrontendRuntimeTests(unittest.TestCase):
    def run_node(self, assertions: str) -> None:
        harness = r'''
const assert = require("node:assert/strict");
const vm = require("node:vm");
const fs = require("node:fs");

class ClassList {
  constructor(owner) { this.owner = owner; this.names = new Set(); }
  set(value) { this.names = new Set(String(value).split(/\s+/).filter(Boolean)); }
  add(...names) { names.forEach((name) => this.names.add(name)); }
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
    this.style = {setProperty: (name, value) => { this.style[name] = String(value); }};
    this.classList = new ClassList(this);
    this.disabled = false;
    this.textContent = "";
    this.id = "";
  }
  set className(value) { this.classList.set(value); }
  get className() { return [...this.classList.names].join(" "); }
  appendChild(child) { this.children.push(child); return child; }
  replaceChildren(...children) { this.children = children; }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  addEventListener(name, listener) { this.listeners[name] = listener; }
  querySelectorAll(selector) {
    const result = [];
    const visit = (element) => {
      const matches = selector === "button" && element.tag === "button";
      if (matches) result.push(element);
      element.children.forEach(visit);
    };
    this.children.forEach(visit);
    return result;
  }
}
const styleNodes = new Map();
const document = {
  head: {appendChild(element) { if (element.id) styleNodes.set(element.id, element); }},
  createElement(tag) { return new Element(tag); },
  createElementNS(_namespace, tag) { return new Element(tag); },
  getElementById(id) { return styleNodes.get(id) || null; },
};
let renderer = null;
const window = {DuelGameUI: {register(gameType, candidate) {
  assert.equal(gameType, "chess");
  renderer = candidate;
}}};
const sandbox = {window, document, console};
vm.runInNewContext(fs.readFileSync("app/static/games/chess.js", "utf8"), sandbox);
assert.ok(renderer);
assert.equal(renderer.usesStandardMoveConfirmation, true);

function emptyBoard() { return Array.from({length: 8}, () => Array(8).fill(null)); }
function createHarness(state, viewerToken = "X") {
  const board = new Element("div");
  const uiState = {};
  let selectedMove = null;
  let currentContext = null;
  const helpers = {
    setBoardLayout(options) {
      board.style.setProperty("--cols", options.visualCols || options.cols);
      board.style.setProperty("--rows", options.visualRows || options.rows);
      board.setAttribute("aria-label", options.ariaLabel);
    },
    canMove() { return true; },
    clearSelection({render = true} = {}) {
      selectedMove = null;
      Object.keys(uiState).forEach((key) => delete uiState[key]);
      if (render) helpers.rerender();
      return true;
    },
    rerender() {
      board.replaceChildren();
      currentContext.pendingMove = selectedMove;
      renderer.renderBoard(currentContext);
      return true;
    },
    selectMove(payload) {
      selectedMove = {...payload};
      helpers.rerender();
      return true;
    },
  };
  currentContext = {
    board,
    state,
    legalMoves: state.legal_moves,
    uiState,
    helpers,
    viewer: {token: viewerToken},
    participants: [
      {player_id: "white-1", display_name: "白棋手", token: "X"},
      {player_id: "black-1", display_name: "黑棋手", token: "O"},
    ],
    room: {
      status: state.game_over ? "finished" : "playing",
      winner_player_id: state.winner_mark === "X"
        ? "white-1" : state.winner_mark === "O" ? "black-1" : null,
      winner: state.winner_mark === "draw" ? "draw" : null,
    },
    isTerminal: Boolean(state.game_over),
    canMove: !state.game_over,
    pendingMove: null,
  };
  helpers.rerender();
  const cell = (name) => board.children.find(
    (element) => element.dataset && element.dataset.square === name
  );
  return {board, uiState, helpers, cell, selectedMove: () => selectedMove};
}
''' + assertions
        completed = subprocess.run(
            [NODE, "-e", harness],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(
            completed.returncode,
            0,
            f"JavaScript assertion failed:\n{completed.stderr}",
        )

    def test_select_origin_uses_legal_targets_and_marks_check_last_move(self):
        self.run_node(r'''
const boardState = emptyBoard();
boardState[0][4] = "b:k";
boardState[6][4] = "w:p";
boardState[7][4] = "w:k";
const state = {
  board: boardState,
  turn_color: "w",
  in_check: true,
  last_move: {from_row: 0, from_col: 1, to_row: 2, to_col: 2},
  legal_moves: [
    {from_row: 6, from_col: 4, to_row: 5, to_col: 4, captured: null},
    {from_row: 6, from_col: 4, to_row: 4, to_col: 4, captured: null},
  ],
};
const harness = createHarness(state, "X");
assert.equal(harness.board.children.filter((item) => item.tag === "button").length, 64);
assert.equal(harness.cell("e2").disabled, false);
assert.equal(harness.cell("e4").disabled, true);
assert.equal(harness.cell("e1").classList.contains("in-check"), true);
assert.equal(harness.cell("b8").classList.contains("last-move-from"), true);
assert.equal(harness.cell("c6").classList.contains("last-move-to"), true);
harness.cell("e2").listeners.click();
assert.equal(harness.uiState.selectedSquare.row, 6);
assert.equal(harness.cell("e4").classList.contains("legal-target"), true);
assert.equal(harness.cell("e4").disabled, false);
harness.cell("e4").listeners.click();
assert.deepEqual(harness.selectedMove(), {
  from_row: 6, from_col: 4, to_row: 4, to_col: 4,
});
assert.equal(harness.cell("e4").classList.contains("selected-target"), true);

const rotated = createHarness(state, "O");
assert.equal(rotated.board.children[0].dataset.square, "h1");
assert.equal(rotated.board.dataset.viewColor, "b");
''')

    def test_promotion_requires_an_explicit_piece_choice(self):
        self.run_node(r'''
const boardState = emptyBoard();
boardState[0][4] = "b:k";
boardState[1][0] = "w:p";
boardState[7][4] = "w:k";
const state = {
  board: boardState, turn_color: "w", in_check: false, last_move: null,
  legal_moves: ["q", "r", "b", "n"].map((promotion) => ({
    from_row: 1, from_col: 0, to_row: 0, to_col: 0,
    promotion, captured: null,
  })),
};
const harness = createHarness(state, "X");
harness.cell("a7").listeners.click();
harness.cell("a8").listeners.click();
assert.equal(harness.selectedMove(), null);
assert.equal(harness.uiState.promotionMoves.length, 4);
const panel = harness.board.children.find(
  (element) => element.classList.contains("chess-promotion-panel")
);
assert.ok(panel);
assert.equal(panel.attributes.role, "dialog");
const choices = panel.children.filter(
  (element) => element.classList.contains("chess-promotion-choice")
);
assert.equal(choices.length, 4);
choices[0].listeners.click();
assert.deepEqual(harness.selectedMove(), {
  from_row: 1, from_col: 0, to_row: 0, to_col: 0, promotion: "q",
});
''')

    def test_claim_draw_control_uses_authoritative_action_and_mobile_safe_bar(self):
        self.run_node(r'''
const controls = new Element("div");
controls.classList.add("hidden");
let submitted = null;
renderer.renderControls({
  controls,
  state: {claimable_draw_reasons: ["threefold_repetition"]},
  legalActions: [{action: "claim_draw"}],
  canMove: true,
  helpers: {
    canMove() { return true; },
    submitMove(payload) { submitted = payload; return Promise.resolve(true); },
  },
});
assert.equal(controls.classList.contains("hidden"), false);
assert.equal(controls.children.length, 1);
const buttons = controls.querySelectorAll("button");
assert.equal(buttons.length, 1);
assert.equal(buttons[0].textContent, "申和");
assert.equal(buttons[0].disabled, false);
buttons[0].listeners.click();
assert.equal(submitted.action, "claim_draw");
assert.deepEqual(Object.keys(submitted), ["action"]);

const unavailable = new Element("div");
renderer.renderControls({
  controls: unavailable,
  state: {claimable_draw_reasons: []},
  legalActions: [],
  canMove: true,
  helpers: {},
});
assert.equal(unavailable.classList.contains("hidden"), true);
assert.equal(unavailable.children.length, 0);

const intendedControls = new Element("div");
submitted = null;
const intendedAction = {
  action: "claim_draw",
  from_row: 0, from_col: 6, to_row: 2, to_col: 5,
};
renderer.renderControls({
  controls: intendedControls,
  state: {
    claimable_draw_reasons: [],
    intended_draw_claims: [{...intendedAction, reasons: ["threefold_repetition"]}],
  },
  legalActions: [intendedAction],
  canMove: true,
  helpers: {
    canMove() { return true; },
    submitMove(payload) { submitted = payload; return Promise.resolve(true); },
  },
});
assert.match(intendedControls.children[0].children[0].textContent, /声明下一手.*三次重复/);
intendedControls.querySelectorAll("button")[0].listeners.click();
assert.equal(JSON.stringify(submitted), JSON.stringify(intendedAction));
''')
        self.assertIn(".chess-claim-button { width: 100%; min-height: 44px; }", SCRIPT)

    def test_checkmate_replaces_check_with_terminal_winner_status(self):
        self.run_node(r'''
const boardState = emptyBoard();
boardState[0][4] = "b:k";
boardState[7][4] = "w:k";
const state = {
  board: boardState,
  turn_color: "b",
  in_check: true,
  in_checkmate: true,
  game_over: true,
  winner_mark: "X",
  terminal_reason: "checkmate",
  last_move: null,
  legal_moves: [],
};
const harness = createHarness(state, "X");
const notice = harness.board.children.find(
  (element) => element.classList.contains("chess-check-notice")
);
assert.ok(notice);
assert.equal(notice.classList.contains("terminal"), true);
assert.equal(notice.textContent, "将死 · 对局结束 · 白棋手获胜");
assert.equal(harness.cell("e8").classList.contains("in-check"), false);
assert.equal(
  harness.board.children.some((element) => element.textContent.includes("将军")),
  false
);
''')

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

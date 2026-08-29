import shutil
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RENDERER_PATH = ROOT / "app" / "static" / "games" / "yahtzee.js"
RENDERER = RENDERER_PATH.read_text(encoding="utf-8")
STYLES = (ROOT / "app" / "static" / "styles.css").read_text(encoding="utf-8")
NODE = shutil.which("node")


class YahtzeeFrontendStructureTests(unittest.TestCase):
    def test_renderer_uses_the_game_extension_contract(self):
        self.assertIn("window.DuelGameUI.register('yahtzee', renderer);", RENDERER)
        self.assertIn('participantPresentation: "embedded"', RENDERER)
        self.assertIn("renderBoard,", RENDERER)
        self.assertIn("usesStandardMoveConfirmation: false", RENDERER)
        self.assertIn("helpers.submitMove", RENDERER)
        self.assertIn("Boolean(context.canMove)", RENDERER)
        self.assertIn('glyph: "艇"', RENDERER)
        app_script = (ROOT / "app" / "static" / "app.js").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("renderYahtzee", app_script)

    def test_five_real_dice_use_css_pips_and_obvious_hold_state(self):
        self.assertIn('die.className = `yahtzee-die${held ? " held" : ""}', RENDERER)
        self.assertIn(
            'pip.className = `yahtzee-pip${activePips.has(position) ? " on" : ""}`',
            RENDERER,
        )
        self.assertIn('badge.textContent = "保留"', RENDERER)
        self.assertIn('die.setAttribute("aria-pressed", String(held))', RENDERER)
        self.assertNotIn("🎲", RENDERER)
        self.assertIn(".yahtzee-die {", STYLES)
        self.assertIn("border-radius: 12px;", STYLES)
        self.assertIn(".yahtzee-pip.on { background: currentColor;", STYLES)
        self.assertIn(".yahtzee-die.held {", STYLES)
        self.assertIn(".yahtzee-die.held .yahtzee-hold-badge { display: block; }", STYLES)

    def test_scorecard_has_thirteen_rows_all_players_previews_and_zero_toggle(self):
        category_keys = (
            "ones", "twos", "threes", "fours", "fives", "sixes",
            "three_of_a_kind", "four_of_a_kind", "full_house",
            "small_straight", "large_straight", "yahtzee", "chance",
        )
        for category in category_keys:
            self.assertIn(f'"{category}"', RENDERER)
        self.assertIn("participants.forEach((participant) =>", RENDERER)
        self.assertIn("state.scorecards", RENDERER)
        self.assertIn("state.score_previews", RENDERER)
        self.assertIn("context.legalActions", RENDERER)
        self.assertIn("hasAuthoritativeActions", RENDERER)
        self.assertIn("scratchText.textContent = jokerActive", RENDERER)
        self.assertIn('"Joker 回合按规则计分"', RENDERER)
        self.assertIn('"划掉类别，记 0 分"', RENDERER)
        self.assertIn('action: "score"', RENDERER)
        self.assertIn('...(scratch.checked ? {zero: true} : {})', RENDERER)
        self.assertIn('action: "roll", held_mask: heldMask', RENDERER)
        self.assertIn('"上半区奖励", "upper_bonus"', RENDERER)
        self.assertIn('"重复快艇奖励", "yahtzee_bonus"', RENDERER)
        self.assertIn('"总分", "total"', RENDERER)

    def test_joker_and_pending_bonus_have_clear_non_emoji_ui(self):
        self.assertIn("const jokerActive = Boolean(state.joker_active);", RENDERER)
        self.assertIn("const pendingYahtzeeBonus", RENDERER)
        self.assertIn('jokerNotice.className = "yahtzee-joker-notice";', RENDERER)
        self.assertIn('"每次 +100"', RENDERER)
        self.assertIn(".yahtzee-joker-notice {", STYLES)
        self.assertNotIn("🎲", RENDERER)

    def test_scorecard_and_dice_are_desktop_and_mobile_safe(self):
        self.assertIn(".board.yahtzee {", STYLES)
        self.assertIn("width: min(920px, 92vw);", STYLES)
        self.assertIn("max-width: 100%;", STYLES)
        self.assertIn(".yahtzee-scorecard-scroll {", STYLES)
        self.assertIn("overflow-x: auto;", STYLES)
        mobile = STYLES[STYLES.index("@media (max-width: 599px)"):]
        self.assertIn(".board.yahtzee { width: 100%; max-width: 100%; }", mobile)
        self.assertIn(".yahtzee-roll-panel { grid-template-columns: 1fr;", mobile)
        self.assertIn("width: clamp(45px, 14.5vw, 58px);", mobile)
        self.assertIn("touch-action: pan-x;", STYLES)
        self.assertIn(
            ".yahtzee-joker-notice { padding: 6px 7px; font-size: 10px; }",
            mobile,
        )

    @unittest.skipUnless(NODE, "node is required for JavaScript syntax validation")
    def test_renderer_is_valid_javascript(self):
        completed = subprocess.run(
            [NODE, "--check", str(RENDERER_PATH)],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(
            completed.returncode,
            0,
            f"JavaScript syntax check failed:\n{completed.stderr}",
        )

    @unittest.skipUnless(NODE, "node is required for renderer interaction tests")
    def test_renderer_submits_held_mask_and_clickable_score_preview(self):
        harness = r'''
const assert = require("node:assert/strict");
const vm = require("node:vm");
const fs = require("node:fs");
const source = fs.readFileSync(0, "utf8");
class ClassList {
  constructor(owner) { this.owner = owner; this.names = new Set(); }
  reset(value) { this.names = new Set(String(value).split(/\s+/).filter(Boolean)); }
  sync() { this.owner._className = [...this.names].join(" "); }
  add(...names) { names.forEach((name) => this.names.add(name)); this.sync(); }
  toggle(name, force) {
    if (force === undefined ? !this.names.has(name) : force) this.names.add(name);
    else this.names.delete(name);
    this.sync();
  }
  contains(name) { return this.names.has(name); }
}
class Element {
  constructor(tag) {
    this.tag = tag; this.children = []; this.dataset = {}; this.attributes = {};
    this.listeners = {}; this.disabled = false; this.checked = false; this.textContent = "";
    this._className = ""; this.classList = new ClassList(this);
    this.style = {values: {}, setProperty: (name, value) => {
      this.style.values[name] = String(value);
    }};
  }
  set className(value) { this._className = value; this.classList.reset(value); }
  get className() { return this._className; }
  append(...children) { children.forEach((child) => this.appendChild(child)); }
  appendChild(child) { this.children.push(child); return child; }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  addEventListener(name, listener) { this.listeners[name] = listener; }
  click() { return this.listeners.click ? this.listeners.click() : undefined; }
}
const document = {createElement: (tag) => new Element(tag)};
const sandbox = {
  window: {document, DuelGameUI: {register(type, renderer) {
    assert.equal(type, "yahtzee"); sandbox.renderer = renderer; return renderer;
  }}},
};
vm.runInNewContext(source, sandbox);
assert.equal(sandbox.renderer.usesStandardMoveConfirmation, false);
const board = new Element("board");
const submitted = [];
const participants = [
  {player_id: "human-1", display_name: "人类"},
  {player_id: "ai-1", display_name: "小机"},
];
const categories = [
  "ones", "twos", "threes", "fours", "fives", "sixes",
  "three_of_a_kind", "four_of_a_kind", "full_house", "small_straight",
  "large_straight", "yahtzee", "chance",
];
const state = {
  flow: {round_number: 4}, dice: [1, 2, 3, 4, 5], held_mask: [false, false, false, false, false],
  rolls_used: 1, max_rolls: 3,
  scorecards: {"human-1": {}, "ai-1": {ones: 2}},
  score_previews: Object.fromEntries(categories.map((key, index) => [key, index])),
  totals_by_player: {
    "human-1": {upper_subtotal: 0, upper_bonus: 0, total: 0},
    "ai-1": {upper_subtotal: 2, upper_bonus: 0, total: 2},
  },
};
const context = {
  board, state, participants, canMove: true,
  room: {current_player_id: "human-1", participants},
  helpers: {
    submitMove: async (move) => { submitted.push(move); return true; },
    canMove: () => true,
  },
};
const descendants = (root) => [root, ...root.children.flatMap(descendants)];
(async () => {
  sandbox.renderer.renderBoard(context);
  const all = descendants(board);
  const dice = all.filter((item) => (
    item.classList.contains("yahtzee-die") && item.tag === "button"
  ));
  assert.equal(dice.length, 5);
  assert.equal(all.filter((item) => (
    item.classList.contains("yahtzee-pip") && item.classList.contains("on")
  )).length, 15);
  await dice[0].click();
  const roll = all.find((item) => item.classList.contains("yahtzee-roll-button"));
  await roll.click();
  assert.equal(
    JSON.stringify(submitted[0]),
    JSON.stringify({action: "roll", held_mask: [true, false, false, false, false]})
  );
  const scratch = all.find((item) => item.tag === "input");
  scratch.checked = true;
  const score = all.find((item) => item.classList.contains("yahtzee-score-button"));
  await score.click();
  assert.equal(submitted[1].action, "score");
  assert.equal(submitted[1].category, "ones");
  assert.equal(submitted[1].zero, true);

  const jokerBoard = new Element("board");
  const jokerState = {
    ...state,
    dice: [4, 4, 4, 4, 4],
    scorecards: {"human-1": {yahtzee: 50}, "ai-1": {ones: 2}},
    score_previews: {fours: 20},
    joker_active: true,
    pending_yahtzee_bonus: 100,
    totals_by_player: {
      "human-1": {upper_subtotal: 0, upper_bonus: 0, yahtzee_bonus: 100, total: 150},
      "ai-1": {upper_subtotal: 2, upper_bonus: 0, yahtzee_bonus: 0, total: 2},
    },
  };
  sandbox.renderer.renderBoard({
    ...context,
    board: jokerBoard,
    state: jokerState,
    legalActions: [{action: "score", category: "fours"}],
  });
  const jokerAll = descendants(jokerBoard);
  const jokerScores = jokerAll.filter((item) => (
    item.classList.contains("yahtzee-score-button")
  ));
  assert.equal(jokerScores.length, 1);
  assert.equal(jokerScores[0].textContent, "20 分");
  assert.equal(jokerAll.find((item) => item.tag === "input").disabled, true);
  assert.match(
    jokerAll.find((item) => item.classList.contains("yahtzee-joker-notice")).textContent,
    /另加 100 分/
  );
  const bonusRow = jokerAll.find((item) => (
    item.classList.contains("yahtzee-summary-yahtzee_bonus")
  ));
  assert.equal(bonusRow.children[1].textContent, "100");
})().catch((error) => { console.error(error); process.exitCode = 1; });
'''
        completed = subprocess.run(
            [NODE, "-e", harness],
            cwd=ROOT,
            input=RENDERER,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(
            completed.returncode,
            0,
            f"Renderer interaction failed:\n{completed.stderr}",
        )


if __name__ == "__main__":
    unittest.main()

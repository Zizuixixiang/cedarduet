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
        self.assertIn('face.className = "yahtzee-die-face";', RENDERER)
        self.assertNotIn("🎲", RENDERER)
        self.assertIn(".yahtzee-die {", STYLES)
        self.assertIn(".yahtzee-die-face {", STYLES)
        self.assertIn("border-radius: 7px;", STYLES)
        self.assertIn(".yahtzee-pip.on { background: currentColor;", STYLES)
        self.assertIn(".yahtzee-die.held .yahtzee-die-face {", STYLES)
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
        self.assertIn('isViewer ? "（你）" : ""', RENDERER)
        self.assertIn("state.scorecards", RENDERER)
        self.assertIn("state.score_previews", RENDERER)
        self.assertIn("context.legalActions", RENDERER)
        self.assertIn("hasAuthoritativeActions", RENDERER)
        self.assertIn("scratchText.textContent = jokerActive", RENDERER)
        self.assertIn('"Joker 回合按规则计分"', RENDERER)
        self.assertIn('"划掉类别，记 0 分"', RENDERER)
        self.assertIn('action: "score"', RENDERER)
        self.assertIn('...(uiState.yahtzeeScratch ? {zero: true} : {})', RENDERER)
        self.assertIn('action: "roll", held_mask: heldMask', RENDERER)
        self.assertIn('"上半区奖励", "upper_bonus"', RENDERER)
        self.assertIn('"重复快艇奖励", "yahtzee_bonus"', RENDERER)
        self.assertIn('"总分", "total"', RENDERER)
        self.assertIn('scoreOptions.className = "yahtzee-score-options";', RENDERER)
        self.assertIn('option.className = [', RENDERER)
        self.assertIn('value.textContent = `已用 · ${currentCard[category.key]} 分`;', RENDERER)
        self.assertIn('scorecardDetails.className = "yahtzee-scorecard-details";', RENDERER)
        self.assertIn("scorecardDetails.open = Boolean(uiState.yahtzeeScorecardOpen);", RENDERER)
        self.assertIn('"完整 13 项计分卡 · 展开查看"', RENDERER)
        self.assertIn('totalsOverview.className = "yahtzee-totals-overview";', RENDERER)

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
        self.assertIn(".yahtzee-die {\n    width: 44px;\n    height: 44px;", mobile)
        self.assertIn("width: clamp(26px, 8vw, 31px);", mobile)
        self.assertIn(".yahtzee-score-options { grid-template-columns: repeat(3, minmax(0, 1fr)); }", mobile)
        self.assertIn(".yahtzee-totals-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }", mobile)
        self.assertIn("grid-template-columns: repeat(auto-fit, minmax(116px, 1fr));", STYLES)
        self.assertIn("touch-action: auto;", STYLES)
        self.assertIn(
            ".yahtzee-joker-notice { padding: 6px 7px; font-size: 10px; }",
            mobile,
        )
        self.assertIn(".yahtzee-player-avatar {", STYLES)
        self.assertIn("max-width: 22px;", STYLES)
        self.assertIn("max-height: 22px;", STYLES)
        self.assertIn(
            ".yahtzee-player-avatar { width: 20px; height: 20px; max-width: 20px; max-height: 20px; }",
            mobile,
        )

    def test_scorecard_player_avatars_use_shared_helper_and_local_fallback(self):
        self.assertIn("context.helpers.renderParticipantAvatar(avatar, participant)", RENDERER)
        self.assertIn('avatar.textContent = Array.from(String(playerName(participant)).trim())[0] || "?";', RENDERER)
        self.assertIn('identity.className = "yahtzee-player-heading";', RENDERER)

    def test_scorecard_details_width_and_header_avatars_are_contained(self):
        details_start = STYLES.index(".yahtzee-scorecard-details {")
        details = STYLES[details_start:STYLES.index("}", details_start)]
        self.assertIn("width: 100%;", details)
        self.assertIn("min-width: 0;", details)
        self.assertIn("max-width: 100%;", details)
        self.assertIn("contain: inline-size;", details)
        self.assertIn("overflow: hidden;", details)

        summary_start = STYLES.index(".yahtzee-scorecard-summary {")
        summary = STYLES[summary_start:STYLES.index("}", summary_start)]
        self.assertIn("width: 100%;", summary)
        self.assertIn("max-width: 100%;", summary)
        self.assertIn("display: list-item;", summary)

        scroll_start = STYLES.index(".yahtzee-scorecard-scroll {")
        scroll = STYLES[scroll_start:STYLES.index("}", scroll_start)]
        self.assertIn("width: 100%;", scroll)
        self.assertIn("min-width: 0;", scroll)
        self.assertIn("max-width: 100%;", scroll)
        self.assertIn("overflow-x: auto;", scroll)

        header_start = STYLES.index(".yahtzee-scorecard thead th {")
        header = STYLES[header_start:STYLES.index("}", header_start)]
        self.assertIn("vertical-align: middle;", header)
        self.assertIn(
            ".yahtzee-player-avatar img { width: 100%; height: 100%; display: block; object-fit: cover; }",
            STYLES,
        )
        current_avatar_start = STYLES.index(
            ".yahtzee-scorecard thead .yahtzee-player-avatar.current-turn-avatar {"
        )
        current_avatar = STYLES[
            current_avatar_start:STYLES.index("}", current_avatar_start)
        ]
        self.assertIn("position: static;", current_avatar)
        self.assertIn("z-index: auto;", current_avatar)
        self.assertIn("overflow: hidden;", current_avatar)
        self.assertIn("outline: 0;", current_avatar)
        self.assertIn("box-shadow: none;", current_avatar)
        self.assertIn("transform: none;", current_avatar)
        self.assertIn(
            ".yahtzee-scorecard thead .yahtzee-player-avatar.current-turn-avatar::after { display: none; }",
            STYLES,
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
const avatarCalls = [];
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
  uiState: {},
  helpers: {
    submitMove: async (move) => { submitted.push(move); return true; },
    canMove: () => true,
    rerender: () => true,
    renderParticipantAvatar: (target, participant) => {
      avatarCalls.push(participant.player_id);
      target.textContent = `avatar:${participant.player_id}`;
    },
  },
};
const descendants = (root) => [root, ...root.children.flatMap(descendants)];
(async () => {
  sandbox.renderer.renderBoard(context);
  const all = descendants(board);
  assert.equal(
    JSON.stringify(avatarCalls),
    JSON.stringify(["human-1", "ai-1", "human-1", "ai-1"])
  );
  assert.equal(all.filter((item) => item.classList.contains("yahtzee-player-avatar")).length, 4);
  const scoreOptions = all.filter((item) => item.classList.contains("yahtzee-score-option"));
  assert.equal(scoreOptions.length, 13);
  assert.equal(scoreOptions.filter((item) => item.classList.contains("selectable")).length, 13);
  const scorecardDetails = all.find((item) => item.classList.contains("yahtzee-scorecard-details"));
  assert.equal(scorecardDetails.tag, "details");
  assert.equal(scorecardDetails.open, false);
  assert.equal(all.filter((item) => item.classList.contains("yahtzee-total-player")).length, 2);
  const fallbackBoard = new Element("board");
  sandbox.renderer.renderBoard({
    ...context,
    board: fallbackBoard,
    helpers: {
      submitMove: async () => true,
      canMove: () => true,
      rerender: () => true,
    },
  });
  assert.equal(JSON.stringify(descendants(fallbackBoard).filter(
    (item) => item.classList.contains("yahtzee-player-avatar")
  ).map((item) => item.textContent)), JSON.stringify(["人", "小", "人", "小"]));
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
  let score = all.find((item) => (
    item.classList.contains("yahtzee-score-option")
    && item.classList.contains("selectable")
  ));
  await score.click();
  assert.equal(submitted.length, 1);
  assert.equal(context.uiState.yahtzeePendingCategory, "ones");
  assert.equal(context.uiState.yahtzeeScratch, true);

  board.children = [];
  sandbox.renderer.renderBoard(context);
  let next = descendants(board);
  const cancel = next.find((item) => item.classList.contains("yahtzee-score-cancel"));
  await cancel.click();
  assert.equal(submitted.length, 1);
  assert.equal(context.uiState.yahtzeePendingCategory, undefined);

  board.children = [];
  sandbox.renderer.renderBoard(context);
  next = descendants(board);
  score = next.find((item) => (
    item.classList.contains("yahtzee-score-option")
    && item.classList.contains("selectable")
  ));
  await score.click();
  board.children = [];
  sandbox.renderer.renderBoard(context);
  next = descendants(board);
  const confirm = next.find((item) => item.classList.contains("yahtzee-score-submit"));
  await Promise.all([confirm.click(), confirm.click()]);
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
    uiState: {},
    legalActions: [{action: "score", category: "fours"}],
  });
  const jokerAll = descendants(jokerBoard);
  const jokerOptions = jokerAll.filter((item) => (
    item.classList.contains("yahtzee-score-option")
  ));
  assert.equal(jokerOptions.length, 13);
  assert.equal(jokerOptions.filter((item) => item.classList.contains("selectable")).length, 1);
  assert.equal(
    jokerOptions.find((item) => item.classList.contains("selectable")).children[1].textContent,
    "20 分"
  );
  const usedOption = jokerOptions.find((item) => item.classList.contains("used"));
  assert.equal(usedOption.disabled, true);
  assert.equal(usedOption.children[1].textContent, "已用 · 50 分");
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

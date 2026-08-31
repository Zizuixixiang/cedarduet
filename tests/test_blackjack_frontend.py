import shutil
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RENDERER_PATH = ROOT / "app" / "static" / "games" / "blackjack.js"
STYLES_PATH = ROOT / "app" / "static" / "games" / "blackjack.css"
RENDERER = RENDERER_PATH.read_text(encoding="utf-8")
STYLES = STYLES_PATH.read_text(encoding="utf-8")
APP = (ROOT / "app" / "static" / "app.js").read_text(encoding="utf-8")
HTML = (ROOT / "app" / "static" / "index.html").read_text(encoding="utf-8")
NODE = shutil.which("node")


class BlackjackFrontendTests(unittest.TestCase):
    def test_renderer_is_registry_loaded_and_owns_idempotent_css(self):
        self.assertIn('window.DuelGameUI.register("blackjack", {', RENDERER)
        self.assertIn('participantPresentation: "embedded"', RENDERER)
        self.assertIn("ownsPrivateStatePresentation: true", RENDERER)
        self.assertIn("usesStandardMoveConfirmation: false", RENDERER)
        self.assertIn("renderBoard,", RENDERER)
        self.assertIn("renderControls,", RENDERER)
        self.assertIn('const STYLE_ID = "duel-game-blackjack-styles";', RENDERER)
        self.assertIn('const STYLE_HREF = "/static/games/blackjack.css?v=1.0.1";', RENDERER)
        self.assertIn("getElementById(STYLE_ID)", RENDERER)
        self.assertNotIn("renderBlackjack", APP)
        self.assertNotIn('"blackjack"', APP)
        self.assertIn("registry.load(gameType)", APP)
        self.assertIn("loadCatalogGameRenderers", APP)
        self.assertNotIn("/static/games/blackjack.js", HTML)
        self.assertNotIn("/static/games/blackjack.css", HTML)

    def test_cards_are_css_and_text_symbols_with_a_uniform_back(self):
        self.assertIn('spades: "\\u2660"', RENDERER)
        self.assertIn('hearts: "\\u2665"', RENDERER)
        self.assertIn('node.className = hidden ? "blackjack-card is-hidden"', RENDERER)
        self.assertIn('node.setAttribute("aria-label", `${ownerLabel}暗牌`)', RENDERER)
        self.assertIn(".blackjack-card-back {", STYLES)
        self.assertNotIn("createElement(\"img\")", RENDERER)
        self.assertNotIn("http://", RENDERER)
        self.assertNotIn("https://", RENDERER)

    def test_mobile_layout_has_internal_scroll_responsive_cards_and_touch_targets(self):
        self.assertIn(".board.blackjack {", STYLES)
        self.assertIn("max-width: 100%;", STYLES)
        self.assertIn(".blackjack-player-region {", STYLES)
        self.assertIn("overflow: auto;", STYLES)
        self.assertIn("width: clamp(42px, 6.8vw, 62px);", STYLES)
        self.assertIn("min-height: 48px;", STYLES)
        self.assertIn("touch-action: manipulation;", STYLES)
        mobile = STYLES[STYLES.index("@media (max-width: 599px)"):]
        self.assertIn(".board.blackjack { width: 100%; max-width: 100%;", mobile)
        self.assertIn(".blackjack-players { grid-template-columns: 1fr;", mobile)
        self.assertIn("width: clamp(38px, 13vw, 50px);", mobile)
        self.assertIn("@media (max-width: 360px)", mobile)

    def test_seat_avatars_use_the_shared_helper_with_compact_fallback(self):
        self.assertIn("context.helpers.renderParticipantAvatar(avatar, participant)", RENDERER)
        self.assertIn('avatar.textContent = Array.from(String(playerName(participant)).trim())[0] || "?";', RENDERER)
        self.assertIn(".blackjack-seat-avatar {", STYLES)
        self.assertIn("max-width: 24px;", STYLES)
        self.assertIn("max-height: 24px;", STYLES)
        mobile = STYLES[STYLES.index("@media (max-width: 599px)"):]
        self.assertIn(
            ".blackjack-seat-avatar { width: 22px; height: 22px; max-width: 22px; max-height: 22px; }",
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
        self.assertEqual(completed.returncode, 0, completed.stderr)

    @unittest.skipUnless(NODE, "node is required for renderer DOM tests")
    def test_renderer_builds_table_and_only_submits_authoritative_actions(self):
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
  contains(name) { return this.names.has(name); }
}
class Element {
  constructor(tag) {
    this.tag = tag; this.children = []; this.dataset = {}; this.attributes = {};
    this.listeners = {}; this.disabled = false; this.textContent = ""; this.tabIndex = -1;
    this._className = ""; this.classList = new ClassList(this);
  }
  set className(value) { this._className = value; this.classList.reset(value); }
  get className() { return this._className; }
  append(...children) { children.forEach((child) => this.appendChild(child)); }
  appendChild(child) { this.children.push(child); return child; }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  addEventListener(name, listener) { this.listeners[name] = listener; }
  click() { return this.listeners.click ? this.listeners.click() : undefined; }
}
const head = new Element("head");
const document = {
  head,
  createElement: (tag) => new Element(tag),
  getElementById(id) {
    return head.children.find((child) => child.id === id) || null;
  },
};
const sandbox = {window: {document, DuelGameUI: {register(type, renderer) {
  assert.equal(type, "blackjack"); sandbox.renderer = renderer; return renderer;
}}}};
vm.runInNewContext(source, sandbox);
const participants = [
  {player_id: "human-1", display_name: "人类", seat_index: 0},
  {player_id: "ai-1", display_name: "小机", seat_index: 1},
  {player_id: "ai-2", display_name: "小机二号", seat_index: 2},
];
const state = {
  shoe_decks: 4, flow: {phase: "player_turns"}, turn_player_id: "human-1",
  dealer: {
    hand: [{rank: "10", suit: "spades"}, {hidden: true}],
    value: {total: 10, soft: false}, hole_hidden: true, status: "hidden",
  },
  players: {
    "human-1": {
      hand: [{rank: "A", suit: "hearts"}, {rank: "6", suit: "clubs"}],
      value: {total: 17, soft: true, blackjack: false, bust: false},
      status: "playing", status_label: "行动中", outcome: null,
    },
    "ai-1": {
      hand: [{rank: "9", suit: "diamonds"}, {rank: "8", suit: "spades"}],
      value: {total: 17, soft: false, blackjack: false, bust: false},
      status: "playing", status_label: "行动中", outcome: null,
    },
    "ai-2": {
      hand: [{rank: "10", suit: "clubs"}, {rank: "7", suit: "hearts"}],
      value: {total: 17, soft: false, blackjack: false, bust: false},
      status: "playing", status_label: "行动中", outcome: null,
    },
  },
};
const board = new Element("board");
const controls = new Element("controls");
const submitted = [];
const avatarCalls = [];
const context = {
  board, controls, state, participants, legalActions: [{action: "hit"}, {action: "stand"}],
  viewer: {player_id: "human-1"}, canMove: true, isTerminal: false,
  room: {current_player_id: "human-1", status: "playing"},
  helpers: {
    canMove: () => true,
    submitMove: async (move) => { submitted.push(move); return true; },
    renderParticipantAvatar: (target, participant) => {
      avatarCalls.push(participant.player_id);
      target.textContent = `avatar:${participant.player_id}`;
    },
  },
};
const descendants = (root) => [root, ...root.children.flatMap(descendants)];
(async () => {
  sandbox.renderer.renderBoard(context);
  sandbox.renderer.renderControls(context);
  assert.equal(head.children.length, 1);
  assert.equal(head.children[0].href, "/static/games/blackjack.css?v=1.0.1");
  const all = [...descendants(board), ...descendants(controls)];
  assert.ok(all.some((node) => node.classList.contains("blackjack-table")));
  const hidden = all.find((node) => node.classList.contains("is-hidden"));
  assert.equal(hidden.attributes["aria-label"], "庄家暗牌");
  const seats = all.filter((node) => node.classList.contains("blackjack-seat"));
  assert.equal(seats.length, 3);
  assert.equal(JSON.stringify(avatarCalls), JSON.stringify(["human-1", "ai-1", "ai-2"]));
  assert.equal(all.filter((node) => node.classList.contains("blackjack-seat-avatar")).length, 3);
  const actingBadges = all.filter((node) => (
    node.classList.contains("blackjack-badge") && node.classList.contains("acting")
  ));
  assert.equal(actingBadges.length, 1);
  assert.equal(actingBadges[0].textContent, "行动中");
  assert.equal(seats.find((node) => node.dataset.playerId === "human-1").classList.contains("is-current"), true);
  assert.equal(descendants(seats.find((node) => node.dataset.playerId === "human-1")).includes(actingBadges[0]), true);
  assert.equal(seats.find((node) => node.dataset.playerId === "ai-1").attributes["aria-label"].endsWith("等待"), true);

  const opponentBoard = new Element("board");
  sandbox.renderer.renderBoard({
    ...context,
    board: opponentBoard,
    canMove: false,
    room: {current_player_id: "ai-1", status: "playing"},
  });
  const opponentNodes = descendants(opponentBoard);
  const opponentActing = opponentNodes.find((node) => node.classList.contains("acting"));
  const opponentSeat = opponentNodes.find((node) => (
    node.classList.contains("blackjack-seat") && node.dataset.playerId === "ai-1"
  ));
  assert.ok(opponentActing);
  assert.equal(descendants(opponentSeat).includes(opponentActing), true);

  const terminalBoard = new Element("board");
  sandbox.renderer.renderBoard({
    ...context,
    board: terminalBoard,
    canMove: false,
    isTerminal: true,
    room: {current_player_id: "human-1", status: "finished"},
  });
  const terminalNodes = descendants(terminalBoard);
  assert.equal(terminalNodes.some((node) => node.classList.contains("acting")), false);
  assert.equal(terminalNodes.some((node) => node.classList.contains("is-current")), false);

  const pendingBoard = new Element("board");
  sandbox.renderer.renderBoard({
    ...context,
    board: pendingBoard,
    canMove: false,
    room: {current_player_id: "human-1", status: "pending"},
  });
  const pendingNodes = descendants(pendingBoard);
  assert.equal(pendingNodes.some((node) => node.classList.contains("acting")), false);
  assert.equal(pendingNodes.some((node) => node.classList.contains("is-current")), false);
  const fallbackBoard = new Element("board");
  sandbox.renderer.renderBoard({
    ...context,
    board: fallbackBoard,
    helpers: {canMove: () => true, submitMove: async () => true},
  });
  const fallbackAvatars = descendants(fallbackBoard).filter(
    (node) => node.classList.contains("blackjack-seat-avatar")
  );
  assert.equal(JSON.stringify(fallbackAvatars.map((node) => node.textContent)), JSON.stringify(["人", "小", "小"]));
  const hit = all.find((node) => node.dataset.action === "hit");
  const stand = all.find((node) => node.dataset.action === "stand");
  assert.equal(hit.disabled, false);
  assert.equal(stand.disabled, false);
  await hit.click();
  assert.equal(JSON.stringify(submitted), JSON.stringify([{action: "hit"}]));
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
        self.assertEqual(completed.returncode, 0, completed.stderr)


if __name__ == "__main__":
    unittest.main()

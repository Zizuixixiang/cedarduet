import json
import os
import random
import shutil
import subprocess
import unittest
from pathlib import Path

from app.games.gem_merchant import GemMerchant

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (ROOT / "app" / "static" / "games" / "gem_merchant.js").read_text(encoding="utf-8")
STYLES = (ROOT / "app" / "static" / "games" / "gem_merchant.css").read_text(encoding="utf-8")
APP_SCRIPT = (ROOT / "app" / "static" / "app.js").read_text(encoding="utf-8")
HTML = (ROOT / "app" / "static" / "index.html").read_text(encoding="utf-8")
NODE = shutil.which("node")

SEATS = [
    {"player_id": "human", "seat_index": 0, "role": "human", "display_name": "南山"},
    {"player_id": "machine", "seat_index": 1, "role": "ai", "display_name": "小机"},
]


class NoShuffle(random.Random):
    def shuffle(self, x):
        return None


def fixture(kind="opening"):
    """Real server projections for the renderer, never hand-written rules."""
    game = GemMerchant(NoShuffle())
    state = game.initialize(SEATS)
    game.prepare_opening_state(state, "human", SEATS)
    actor = {"player_id": "human"}
    if kind in ("reserved", "steal", "terminal"):
        visible = state["pyramid"]["1"][0]
        game.apply_action(state, {"action": "reserve", "card_id": visible}, actor)
        game.apply_action(state, {"action": "reserve", "level": 3}, {"player_id": "machine"})
        if kind != "steal":
            game.apply_action(state, {"action": "reserve", "level": 2}, actor)
    if kind == "steal":
        state["pyramid"]["2"][0] = 31
        state["decks"]["2"] = [c for c in state["decks"]["2"] if c != 31]
        state["players"]["human"]["tokens"].update(white=4, green=3)
        state["players"]["machine"]["tokens"].update(red=1, pearl=1)
        game.apply_action(state, {"action": "buy", "card_id": 31}, actor)
    terminal = kind == "terminal"
    public = (game.terminal_public_state if terminal else game.public_state)(state, SEATS)
    privates = {s["player_id"]: game.private_state(state, s, SEATS) for s in SEATS}
    return {"state": public, "private": privates, "current": state["turn_player_id"] or "human",
            "terminal": terminal,
            "secret": state["players"]["machine"]["reserved"][0]["id"] if state["players"]["machine"]["reserved"] else None}


class GemMerchantFrontendContractTests(unittest.TestCase):
    def test_registry_renderer_and_scoped_stylesheet(self):
        self.assertIn('window.DuelGameUI.register("gem_merchant", {', SCRIPT)
        self.assertIn("usesStandardMoveConfirmation: false", SCRIPT)
        self.assertIn("ownsPrivateStatePresentation: true", SCRIPT)
        self.assertIn("function renderBoard(context)", SCRIPT)
        self.assertIn("function renderControls(context)", SCRIPT)
        self.assertIn('const STYLE_HREF = "/static/games/gem_merchant.css?v=1";', SCRIPT)
        self.assertIn('link.dataset.duelGameStyle = "gem_merchant";', SCRIPT)
        self.assertIn('gem_merchant: "约300–900 token/轮",', APP_SCRIPT)
        self.assertNotIn('register("gem_merchant"', APP_SCRIPT)
        self.assertNotIn("gem_merchant", HTML)

    def test_visuals_are_original_css_without_images_emoji_or_original_names(self):
        self.assertNotIn("<img", SCRIPT.lower())
        self.assertNotIn("url(", STYLES.lower())
        self.assertNotIn("innerHTML", SCRIPT)
        for original in ("Splendor", "璀璨", "皇室"):
            self.assertNotIn(original, SCRIPT + STYLES)
        for name in ("糖霜女王", "斗篷小偷", "风车旅人", "卷轴管家"):
            self.assertNotIn(name, SCRIPT, "称号卡名称由服务端下发")
        for emoji in ("💎", "👑", "📜", "⭐"):
            self.assertNotIn(emoji, SCRIPT + STYLES)

    def test_uses_duel_palette_and_mobile_touch_targets(self):
        for token in ("var(--purple", "var(--pink", "var(--text"):
            self.assertIn(token, STYLES)
        self.assertIn("min-width: 44px;", STYLES)
        self.assertIn("min-height: 44px;", STYLES)
        self.assertIn("touch-action: manipulation;", STYLES)
        self.assertIn("@media (max-width: 599px)", STYLES)
        self.assertIn("@media (max-width: 359px)", STYLES)
        board_rule = STYLES[STYLES.index(".board.gem_merchant {"):]
        board_rule = board_rule[:board_rule.index("}")]
        self.assertIn("max-width: 100%;", board_rule)
        self.assertIn("min-width: 0;", board_rule)


@unittest.skipUnless(NODE, "node is required for gem_merchant renderer DOM tests")
class GemMerchantFrontendRuntimeTests(unittest.TestCase):
    HARNESS = r'''
const assert = require("node:assert/strict");
const vm = require("node:vm");
const fs = require("node:fs");
class ClassList {
  constructor() { this.names = new Set(); }
  set(value) { this.names = new Set(String(value || "").split(/\s+/).filter(Boolean)); }
  add(...names) { names.forEach((n) => this.names.add(n)); }
  contains(name) { return this.names.has(name); }
}
class Element {
  constructor(tag, doc) {
    this.tag = tag; this.ownerDocument = doc; this.children = []; this.dataset = {};
    this.attributes = {}; this.listeners = {}; this.disabled = false; this.textContent = "";
    this.classList = new ClassList();
    this.style = {values: {}, setProperty(n, v) { this.values[n] = String(v); }};
  }
  set className(v) { this.classList.set(v); }
  get className() { return [...this.classList.names].join(" "); }
  appendChild(c) { this.children.push(c); return c; }
  append(...c) { this.children.push(...c); }
  setAttribute(n, v) { this.attributes[n] = String(v); }
  addEventListener(n, f) { this.listeners[n] = f; }
}
const styles = new Map();
const document = {
  head: {appendChild(node) { styles.set(node.id, node); }},
  createElement(tag) { return new Element(tag, document); },
  getElementById(id) { return styles.get(id) || null; },
};
let renderer = null;
const window = {document, DuelGameUI: {register(type, r) { assert.equal(type, "gem_merchant"); renderer = r; }}};
vm.runInNewContext(fs.readFileSync("app/static/games/gem_merchant.js", "utf8"),
  {window, document, console, Math, Number, String, Boolean, Array, Object, Map, Set, Promise, JSON});
assert.ok(renderer);
const all = (root) => { const out = []; const v = (n) => { out.push(n); n.children.forEach(v); }; root.children.forEach(v); return out; };
const cls = (n, c) => n.classList && n.classList.contains(c);
const text = (n) => [n.textContent, ...n.children.map(text)].join("");
function view(fx, viewer) {
  const board = new Element("div", document), controls = new Element("div", document);
  const submitted = [];
  const priv = fx.private[viewer];
  const context = {
    board, controls, state: fx.state, privateState: priv, legalActions: priv.legal_actions,
    uiState: {}, canMove: !fx.terminal && fx.current === viewer, isTerminal: fx.terminal,
    room: {current_player_id: fx.current, status: fx.terminal ? "finished" : "playing"},
    viewer: {player_id: viewer},
    participants: [{player_id: "human", display_name: "南山"}, {player_id: "machine", display_name: "小机"}],
    helpers: {
      canMove() { return context.canMove; },
      rerender() { board.children = []; controls.children = []; renderer.renderBoard(context); renderer.renderControls(context); },
      submitMove(move) { submitted.push(move); return Promise.resolve(true); },
    },
  };
  renderer.renderBoard(context); renderer.renderControls(context);
  return {context, board, controls, submitted, nodes: () => all(board), ctl: () => all(controls)};
}
const FX = JSON.parse(process.env.GM_FIXTURES);
'''

    def run_node(self, body):
        fixtures = {kind: fixture(kind) for kind in ("opening", "reserved", "steal", "terminal")}
        completed = subprocess.run(
            [NODE, "-e", self.HARNESS + body], cwd=ROOT, capture_output=True, text=True,
            env={"GM_FIXTURES": json.dumps(fixtures, ensure_ascii=False), "PATH": "/usr/bin:/bin"},
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_opening_table_renders_board_pyramid_royals_and_one_stylesheet(self):
        self.run_node(r'''
const v = view(FX.opening, "human");
const nodes = v.nodes();
assert.equal(nodes.filter((n) => cls(n, "gm-cell")).length, 25);
assert.equal(nodes.filter((n) => cls(n, "gm-card") && n.dataset.cardId).length, 12);
assert.equal(nodes.filter((n) => cls(n, "gm-deck")).length, 3);
assert.equal(nodes.filter((n) => cls(n, "gm-royal")).length, 4);
assert.ok(text(v.board).includes("糖霜女王"));
assert.equal(nodes.filter((n) => cls(n, "gm-player")).length, 2);
assert.ok(cls(v.context.board, "gem_merchant"));
assert.equal(styles.get("duel-game-gem-merchant-styles").href, "/static/games/gem_merchant.css?v=1");
view(FX.opening, "machine");
assert.equal(styles.size, 1);
// The opponent cannot press anything on the board.
const waiting = view(FX.opening, "machine");
assert.ok(waiting.nodes().filter((n) => cls(n, "gm-cell")).every((n) => n.disabled));
''')

    def test_take_selection_submits_the_exact_authoritative_action(self):
        self.run_node(r'''
(async () => {
  const v = view(FX.opening, "human");
  const legal = v.context.legalActions.filter((a) => a.action === "take" && a.cells.length === 2);
  const target = legal[0].cells;
  const cell = (rc) => v.nodes().find((n) => n.dataset.cell === "ABCDE"[rc[0]] + (rc[1] + 1));
  cell(target[1]).listeners.click();
  assert.ok(v.nodes().some((n) => cls(n, "is-option")), "extendable cells are highlighted");
  cell(target[0]).listeners.click();
  const take = v.ctl().find((n) => n.tag === "button" && n.textContent === "拿！");
  assert.equal(take.disabled, false);
  take.listeners.click();
  await Promise.resolve();
  assert.deepEqual(v.submitted, [legal[0]]);
})().catch((e) => { console.error(e); process.exit(1); });
''')

    def test_reserved_cards_visible_or_hidden_by_rule(self):
        self.run_node(r'''
const fx = FX.reserved;
const mine = view(fx, "machine");
const opp = view(fx, "human");
const reservedOf = (v, viewerSide) => v.nodes().filter((n) => cls(n, "gm-player") && cls(n, viewerSide))[0];
// The machine sees the human's pyramid reservation face-up and the blind one as a back.
const humanPanel = reservedOf(mine, "is-opponent");
const faces = all(humanPanel).filter((n) => cls(n, "gm-card") && n.dataset.cardId);
const backs = all(humanPanel).filter((n) => cls(n, "is-back"));
assert.equal(faces.length, 1);
assert.equal(backs.length, 1);
// The human never sees the machine's blind card; the machine sees its own face-up.
assert.ok(!opp.nodes().some((n) => n.dataset.cardId === String(fx.secret)));
const machineOwn = reservedOf(mine, "is-viewer");
assert.ok(all(machineOwn).some((n) => n.dataset.cardId === String(fx.secret) && cls(n, "is-blind-own")));
''')

    def test_card_detail_offers_buy_only_when_published(self):
        self.run_node(r'''
const v = view(FX.opening, "human");
const card = v.nodes().find((n) => cls(n, "gm-card") && n.dataset.cardId);
card.listeners.click();
const buttons = v.ctl().filter((n) => n.tag === "button");
const buy = buttons.find((n) => n.textContent === "买下");
const reserve = buttons.find((n) => n.textContent === "拿金保留");
assert.equal(buy.disabled, !v.context.legalActions.some((a) => a.action === "buy" && String(a.card_id) === card.dataset.cardId));
assert.equal(reserve.disabled, false);
assert.ok(v.ctl().some((n) => cls(n, "gm-detail")));
''')

    def test_pending_steal_choice_submits_selected_gem(self):
        self.run_node(r'''
(async () => {
  const v = view(FX.steal, "human");
  assert.deepEqual(v.context.legalActions.map((a) => a.gem), ["red", "pearl"]);
  const choice = v.ctl().find((n) => cls(n, "gm-choice") && n.dataset.gem === "pearl");
  choice.listeners.click();
  const confirm = v.ctl().find((n) => n.tag === "button" && n.textContent === "拿这枚");
  assert.equal(confirm.disabled, false);
  confirm.listeners.click();
  await Promise.resolve();
  assert.deepEqual(v.submitted, [{action: "steal", gem: "pearl"}]);
})().catch((e) => { console.error(e); process.exit(1); });
''')

    def test_terminal_review_reveals_blind_reservations(self):
        self.run_node(r'''
const v = view(FX.terminal, "human");
const review = v.nodes().find((n) => cls(n, "gm-review"));
assert.ok(review);
assert.ok(all(review).some((n) => n.dataset.cardId === String(FX.terminal.secret)));
assert.ok(v.nodes().some((n) => cls(n, "gm-status") && cls(n, "is-final")));
assert.ok(!v.nodes().some((n) => cls(n, "gm-cell") && !n.disabled));
''')


@unittest.skipUnless(NODE, "node is required for browser tutorial tests")
class GemMerchantTutorialBrowserTests(unittest.TestCase):
    def test_tutorial_lifecycle_and_mobile_geometry(self):
        available = subprocess.run(
            [NODE, "-e", "require.resolve('playwright')"], cwd=ROOT,
            capture_output=True, text=True,
        )
        if available.returncode:
            self.skipTest("Playwright is required; install it or set NODE_PATH")
        fixtures = {kind: fixture(kind) for kind in ("opening", "reserved", "terminal")}
        completed = subprocess.run(
            [NODE, str(ROOT / "tests/gem_merchant_tutorial_browser.cjs")], cwd=ROOT,
            input=json.dumps(fixtures, ensure_ascii=False), capture_output=True, text=True,
            env=os.environ.copy(), timeout=180,
        )
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)


if __name__ == "__main__":
    unittest.main()

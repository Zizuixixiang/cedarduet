"""大富翁·改 renderer：注册契约、样式约定与 node 伪 DOM 运行时检查。"""
import json
import os
import random
import shutil
import subprocess
import unittest
from pathlib import Path

from app.games.monopoly_plus import MonopolyPlus

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (ROOT / "app/static/games/monopoly_plus.js").read_text(encoding="utf-8")
STYLES = (ROOT / "app/static/games/monopoly_plus.css").read_text(encoding="utf-8")
APP_SCRIPT = (ROOT / "app/static/app.js").read_text(encoding="utf-8")
NODE = shutil.which("node")
SEATS = [dict(player_id=pid, seat_index=i, display_name=name, participant_kind='human' if i == 0 else 'system_npc')
         for i, (pid, name) in enumerate([('h', '欣欣'), ('a', '小机'), ('n3', '下棋助手 3'), ('n4', '下棋助手 4')])]


class NoShuffle(random.Random):
    def shuffle(self, x):
        return None


class Dice(NoShuffle):
    def __new__(cls, *values):
        # Python 3.10 Random.__new__ otherwise treats dice values as seed args.
        return super().__new__(cls)

    def __init__(self, *values):
        super().__init__(1)
        self.values = list(values)

    def randint(self, a, b):
        return self.values.pop(0) if self.values else a


def fixture(kind):
    g = MonopolyPlus(NoShuffle(3))
    s = g.prepare_opening_state(g.initialize(SEATS), 'h', SEATS)
    if kind != 'setup':
        s = g.apply_action(s, dict(action='choose_edition', edition='classic_plus', action_seq=0), SEATS[0]).state
        s['tiles'][6]['owner'] = 'a'
        s['tiles'][6].update(title='小窝', motto='欢迎')
    if kind == 'vote':
        chat = next(i for i, c in enumerate(g.cards(s, 'event')) if c[0] == 'chat')
        s['_decks']['event'] = [chat] + [i for i in s['_decks']['event'] if i != chat]
        s['players'][0]['position'] = 12
        g.rng = Dice(2, 3)
        s = g.apply_action(s, dict(action='roll', action_seq=s['action_seq']), SEATS[0]).state
        s = g.apply_action(s, dict(action='perform', text='从前有座山，山里有个大富翁。', action_seq=s['action_seq']), SEATS[0]).state
    public = g.public_state(s, SEATS)
    privates = {p['player_id']: g.private_state(s, p, SEATS) for p in SEATS}
    return dict(state=public, private=privates, current=s['turn_player_id'])


@unittest.skipUnless(NODE, "node is required for the monopoly_plus renderer DOM tests")
class MonopolyPlusFrontendTests(unittest.TestCase):
    HARNESS = r'''
const assert = require("node:assert/strict");
const vm = require("node:vm");
const fs = require("node:fs");
class ClassList {
  constructor() { this.names = new Set(); }
  set(v) { this.names = new Set(String(v || "").split(/\s+/).filter(Boolean)); }
  add(...n) { n.forEach(x => this.names.add(x)); }
  remove(...n) { n.forEach(x => this.names.delete(x)); }
  toggle(n, on) { if (on === undefined ? !this.names.has(n) : on) this.names.add(n); else this.names.delete(n); }
  contains(n) { return this.names.has(n); }
}
class Element {
  constructor(tag) {
    this.tag = tag; this.children = []; this.dataset = {}; this.attributes = {}; this.listeners = {};
    this.disabled = false; this.textContent = ""; this.classList = new ClassList(); this.value = "";
    this.style = {values: {}, setProperty(n, v) { this.values[n] = String(v); }};
  }
  set className(v) { this.classList.set(v); }
  get className() { return [...this.classList.names].join(" "); }
  get childElementCount() { return this.children.length; }
  append(...c) { this.children.push(...c); }
  appendChild(c) { this.children.push(c); return c; }
  setAttribute(n, v) { this.attributes[n] = String(v); if (n === "class") this.classList.set(v); }
  addEventListener(n, f) { this.listeners[n] = f; }
  querySelectorAll() { return []; }
}
const styles = new Map();
const document = {
  head: {append(n) { styles.set(n.id, n); }},
  createElement: t => new Element(t),
  createElementNS: (_, t) => new Element(t),
  getElementById: id => styles.get(id) || null,
};
let renderer = null;
const window = {document, setTimeout, clearTimeout, DuelGameUI: {register(type, r) { assert.equal(type, "monopoly_plus"); renderer = r; }}};
vm.runInNewContext(fs.readFileSync("app/static/games/monopoly_plus.js", "utf8"),
  {window, document, console, Math, Number, String, Boolean, Array, Object, Map, Set, Promise, JSON, Date});
assert.ok(renderer);
const all = root => { const out = []; const v = n => { out.push(n); n.children.forEach(v); }; root.children.forEach(v); return out; };
const cls = (n, c) => n.classList && n.classList.contains(c);
const text = n => [n.textContent, ...n.children.map(text)].join("");
const FX = JSON.parse(process.env.MP_FIXTURES);
function view(fx, viewer) {
  const board = new Element("div"), controls = new Element("div");
  const submitted = [], side = [];
  const priv = fx.private[viewer];
  const context = {
    board, controls, state: fx.state, privateState: priv, legalActions: priv.legal_actions, uiState: {},
    canMove: fx.current === viewer, isTerminal: false, room: {room_id: "R1", revision: 3, current_player_id: fx.current},
    viewer: {player_id: viewer},
    participants: [["h", "欣欣", 0], ["a", "小机", 1], ["n3", "下棋助手 3", 2], ["n4", "下棋助手 4", 3]].map(([id, n, i]) => ({player_id: id, display_name: n, seat_index: i})),
    helpers: {
      canMove: () => context.canMove, renderParticipantAvatar() { return true; }, announce() {},
      submitMove(m) { submitted.push(m); return Promise.resolve(true); },
      submitSideMove(m) { side.push(m); return Promise.resolve(true); },
    },
  };
  renderer.renderBoard(context); renderer.renderControls(context);
  return {context, board, controls, submitted, side, nodes: () => all(board), ctl: () => all(controls)};
}
'''

    def run_node(self, body):
        fixtures = {k: fixture(k) for k in ('setup', 'opening', 'vote')}
        done = subprocess.run([NODE, '-e', self.HARNESS + body], cwd=ROOT, capture_output=True, text=True,
                              env={'MP_FIXTURES': json.dumps(fixtures, ensure_ascii=False), 'PATH': '/usr/bin:/bin'})
        self.assertEqual(done.returncode, 0, done.stderr)

    def test_board_separates_pawns_from_ownership(self):
        self.run_node(r'''
const v = view(FX.opening, "h");
const nodes = v.nodes();
assert.equal(nodes.filter(n => cls(n, "mp-tile")).length, 40);
assert.equal(nodes.filter(n => cls(n, "mp-seat")).length, 4);
// Pawns never sit inside a tile: they stand on the inner track next to it.
assert.ok(nodes.filter(n => cls(n, "mp-tile")).every(t => !all(t).some(n => cls(n, "mp-pawn"))));
const spot = nodes.find(n => cls(n, "mp-spot") && n.dataset.trackTile === 0);
assert.equal(spot.dataset.edge, "corner-br");
const pawns = all(spot).filter(n => cls(n, "mp-pawn") && cls(n, "on-track"));
assert.equal(pawns.length, 4);
assert.equal(new Set(pawns.map(p => [...p.classList.names].find(c => c.startsWith("seat-")))).size, 4);
// Letters de-duplicate clashing first characters: 下棋助手 3/4 → 3/4.
assert.equal(pawns.map(p => text(p)).join(","), "欣,小,3,4");
// A tile shows only its colour band and, when owned, a small stamp.
const owned = nodes.find(n => cls(n, "mp-tile") && n.dataset.tileId === 6);
const stamp = all(owned).find(n => cls(n, "mp-stamp"));
assert.ok(stamp && cls(stamp, "seat-1") && cls(stamp, "signed"));
assert.equal(text(stamp), "小");
assert.ok(!cls(owned, "owned") && ![...owned.classList.names].some(c => c.startsWith("seat-")));
const free = nodes.find(n => cls(n, "mp-tile") && n.dataset.tileId === 8);
assert.ok(!all(free).some(n => cls(n, "mp-stamp")));
assert.ok(!nodes.some(n => cls(n, "mp-flag")));
assert.ok(text(nodes.find(n => cls(n, "mp-turn-banner"))).includes("轮到你了"));
assert.equal(styles.get("duel-game-monopoly-plus-styles").href, "/static/games/monopoly_plus.css?v=3");
''')

    def test_setup_cards_and_placeholders(self):
        self.run_node(r'''
(async () => {
  const v = view(FX.setup, "h");
  const cards = v.ctl().filter(n => cls(n, "mp-edition"));
  assert.equal(cards.length, 3);
  assert.equal(JSON.stringify(cards.map(c => c.disabled)), "[false,true,true]");
  assert.ok(cards.slice(1).every(c => text(c).includes("敬请期待")));
  cards[0].listeners.click(); await Promise.resolve();
  assert.equal(JSON.stringify(v.submitted), JSON.stringify([{action: "choose_edition", edition: "classic_plus", action_seq: 0}]));
  const other = view(FX.setup, "a");
  assert.ok(other.ctl().filter(n => cls(n, "mp-edition")).every(c => c.disabled));
})().catch(e => { console.error(e); process.exit(1); });
''')

    def test_spectator_bet_uses_the_side_move_helper(self):
        self.run_node(r'''
(async () => {
  const v = view(FX.opening, "a");
  const chips = v.ctl().filter(n => cls(n, "mp-chip"));
  assert.equal(chips.length, 3);
  assert.ok(chips.every(c => !c.disabled));
  chips[2].listeners.click(); await Promise.resolve();
  assert.equal(JSON.stringify(v.side), JSON.stringify([{action: "bet", choice: "seven", action_seq: 1}]));
  assert.equal(v.submitted.length, 0);
  const me = view(FX.opening, "h");
  assert.equal(me.ctl().filter(n => cls(n, "mp-chip")).length, 0);
})().catch(e => { console.error(e); process.exit(1); });
''')

    def test_chat_card_vote_buttons(self):
        self.run_node(r'''
(async () => {
  const voter = FX.vote.current;
  const v = view(FX.vote, voter);
  assert.ok(text(v.controls).includes("从前有座山"));
  const yes = v.ctl().find(n => n.tag === "button" && text(n).includes("赞成"));
  yes.listeners.click(); await Promise.resolve();
  assert.equal(v.submitted[0].action, "vote");
  assert.equal(v.submitted[0].accept, true);
})().catch(e => { console.error(e); process.exit(1); });
''')


class MonopolyPlusFrontendContractTests(unittest.TestCase):
    def test_registration_and_site_styling_contract(self):
        self.assertIn('window.DuelGameUI.register(GAME, {', SCRIPT)
        self.assertIn('const GAME = "monopoly_plus";', SCRIPT)
        for flag in ('multiplayerTable: true', 'usesStandardMoveConfirmation: false', 'ownsPrivateStatePresentation: true'):
            self.assertIn(flag, SCRIPT)
        self.assertNotIn('innerHTML', SCRIPT)
        for token in ('var(--purple', 'var(--pink', 'var(--text', 'var(--seat-color', 'var(--seat-soft'):
            self.assertIn(token, STYLES)
        self.assertIn('prefers-reduced-motion', STYLES)
        self.assertIn('pixel-btn', SCRIPT)
        self.assertIn('monopoly_plus: "约150–700 token/轮",', APP_SCRIPT)
        self.assertIn('submitSideMove', APP_SCRIPT)
        # The classic renderer is untouched by the new game.
        classic = (ROOT / 'app/static/games/monopoly.js').read_text(encoding='utf-8')
        self.assertNotIn('monopoly_plus', classic)

    def test_tutorial_contract(self):
        self.assertIn('const TUTORIAL_KEY = "cedarduet.monopoly_plus.tutorial.v1";', SCRIPT)
        self.assertIn('["completed", "dismissed"].includes(window.localStorage.getItem(TUTORIAL_KEY))', SCRIPT)
        steps = SCRIPT.split('const TUTORIAL_STEPS = [', 1)[1].split('\n  ];', 1)[0]
        self.assertEqual(steps.count('{title: '), 9)
        for target in ('.mp-turn-banner', '.mp-ring', '.mp-dice', '.mp-hand', '.mp-choice', '.mp-loan', '.mp-bets',
                       '.mp-controls .mp-actions.tools'):
            self.assertIn(f'"{target}"', steps)
        self.assertIn('getElementById("rulesButton")', SCRIPT)
        self.assertIn('"重新打开新手教程"', SCRIPT)
        for cls in ('.mp-tutorial {', '.mp-tutorial-spotlight {', '.mp-tutorial-panel {'):
            self.assertIn(cls, STYLES)


@unittest.skipUnless(NODE, "node is required for browser tutorial tests")
class MonopolyPlusTutorialBrowserTests(unittest.TestCase):
    def test_tutorial_lifecycle_and_mobile_geometry(self):
        available = subprocess.run(
            [NODE, "-e", "require.resolve('playwright')"], cwd=ROOT,
            capture_output=True, text=True,
        )
        if available.returncode:
            self.skipTest("Playwright is required; install it or set NODE_PATH")
        fixtures = {kind: fixture(kind) for kind in ("setup", "opening", "vote")}
        completed = subprocess.run(
            [NODE, str(ROOT / "tests/monopoly_plus_tutorial_browser.cjs")], cwd=ROOT,
            input=json.dumps(fixtures, ensure_ascii=False), capture_output=True, text=True,
            env=os.environ.copy(), timeout=240,
        )
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)

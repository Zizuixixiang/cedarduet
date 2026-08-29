import json
import shutil
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "app" / "static" / "games" / "guandan.js"
STYLE_PATH = ROOT / "app" / "static" / "games" / "guandan.css"
SCRIPT = SCRIPT_PATH.read_text(encoding="utf-8")
STYLES = STYLE_PATH.read_text(encoding="utf-8")
APP_SCRIPT = (ROOT / "app" / "static" / "app.js").read_text(encoding="utf-8")
HTML = (ROOT / "app" / "static" / "index.html").read_text(encoding="utf-8")
NODE = shutil.which("node")


class GuandanFrontendStructureTests(unittest.TestCase):
    def test_independent_embedded_renderer_autoloads_without_shared_app_edits(self):
        self.assertIn('window.DuelGameUI.register("guandan"', SCRIPT)
        self.assertIn('participantPresentation: "embedded"', SCRIPT)
        self.assertIn("usesStandardMoveConfirmation: false", SCRIPT)
        self.assertIn("ownsPrivateStatePresentation: true", SCRIPT)
        self.assertIn('const STYLE_HREF = "/static/games/guandan.css?v=0.1.0";', SCRIPT)
        self.assertNotIn("guandan", APP_SCRIPT)
        self.assertNotIn("guandan.js", HTML)
        self.assertNotIn("guandan.css", HTML)

    def test_four_relative_seats_partner_team_and_public_center_are_renderer_owned(self):
        for expected in (
            'return ["bottom", "right", "top", "left"][difference]',
            "context.helpers.renderParticipantAvatar",
            "guandan-team-chip",
            'position === "top"',
            "context.state.hand_counts",
            "context.state.current_trick",
            "guandan-tribute-band",
        ):
            self.assertIn(expected, SCRIPT)
        self.assertIn("grid-area: top", STYLES)
        self.assertIn("grid-area: bottom", STYLES)
        self.assertIn("grid-area: left", STYLES)
        self.assertIn("grid-area: right", STYLES)

    def test_client_only_submits_authoritative_action_id(self):
        self.assertIn("context.legalActions", SCRIPT)
        self.assertIn('action: "act", action_id: action.action_id', SCRIPT)
        self.assertIn('action: "act", action_id: pass.action_id', SCRIPT)
        for forbidden in ("function canBeat", "function classify", "RANK_VALUE", "BOMB"):
            self.assertNotIn(forbidden, SCRIPT)

    def test_cards_are_css_text_not_emoji_and_mobile_targets_cover_320_375(self):
        self.assertIn("\\u2660\\uFE0E", SCRIPT)
        self.assertIn(".guandan-hand-scroll {", STYLES)
        self.assertIn("overflow-x: auto;", STYLES)
        self.assertIn("touch-action: pan-x;", STYLES)
        self.assertIn("@media (max-width: 375px)", STYLES)
        self.assertIn("@media (max-width: 320px)", STYLES)
        self.assertIn("min-height: 44px;", STYLES)
        self.assertIn('node.setAttribute("aria-pressed"', SCRIPT)
        for emoji in ("🃏", "🎴", "♠️", "♥️", "♣️", "♦️", "💣", "🔥"):
            self.assertNotIn(emoji, SCRIPT + STYLES)


@unittest.skipUnless(NODE, "node is required for renderer DOM tests")
class GuandanFrontendRuntimeTests(unittest.TestCase):
    def run_node(self, assertions):
        state = {
            "phase": "playing", "phase_label": "出牌", "deal_number": 3,
            "level_rank": "7", "team_levels": {"A": "7", "B": "5"},
            "teams": {"human": "A", "right": "B", "partner": "A", "left": "B"},
            "hand_counts": {"human": 3, "right": 12, "partner": 8, "left": 16},
            "current_trick": {
                "number": 4, "leader_player_id": "right",
                "last_play": {
                    "player_id": "right",
                    "cards": [{"id": "d1-S-3", "suit": "spades", "rank": "3"}],
                    "pattern": {"type": "single", "label": "单张"},
                },
                "pass_player_ids": ["left"], "wind_follow": False,
            },
            "tribute": {"status": "complete", "mode": "single", "tributes": [], "returns": []},
        }
        private = {
            "hand": [
                {"id": "d1-S-4", "suit": "spades", "rank": "4", "wild": False},
                {"id": "d1-H-7", "suit": "hearts", "rank": "7", "wild": True},
                {"id": "d1-C-4", "suit": "clubs", "rank": "4", "wild": False},
                {"id": "d2-S-4", "suit": "spades", "rank": "4", "wild": False},
            ],
            "legal_actions": [
                {"action_id": "g_single", "kind": "play", "card_ids": ["d1-S-4"], "label": "单张", "pattern_type": "single"},
                {"action_id": "g_pair", "kind": "play", "card_ids": ["d1-S-4", "d1-H-7"], "label": "对子", "pattern_type": "pair"},
                {"action_id": "g_pass", "kind": "pass", "card_ids": [], "label": "过"},
            ],
        }
        harness = r'''
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
class ClassList {
  constructor() { this.names = new Set(); }
  set(value) { this.names = new Set(String(value || "").split(/\s+/).filter(Boolean)); }
  contains(name) { return this.names.has(name); }
  toggle(name, force) { const on = force === undefined ? !this.names.has(name) : Boolean(force); if (on) this.names.add(name); else this.names.delete(name); return on; }
}
class Element {
  constructor(tag, doc) { this.tag = tag; this.ownerDocument = doc; this.children = []; this.dataset = {}; this.attributes = {}; this.listeners = {}; this.style = {setProperty(n,v){this[n]=String(v);}}; this.classList = new ClassList(); this.disabled = false; this.textContent = ""; this.id = ""; this.value = ""; }
  set className(value) { this.classList.set(value); }
  get className() { return [...this.classList.names].join(" "); }
  appendChild(child) { this.children.push(child); return child; }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  addEventListener(name, listener) { this.listeners[name] = listener; }
}
const styles = new Map();
const document = {head:{appendChild(n){if(n.id)styles.set(n.id,n);}},createElement(t){return new Element(t,document);},getElementById(id){return styles.get(id)||null;}};
let renderer = null;
const window = {document,DuelGameUI:{register(type,value){assert.equal(type,"guandan");renderer=value;}}};
vm.runInNewContext(fs.readFileSync("app/static/games/guandan.js","utf8"),{window,document,console,Math,Set,Map,Number,String,Boolean,Array,Object,Promise});
function descendants(root){const out=[];const visit=n=>{out.push(n);n.children.forEach(visit);};root.children.forEach(visit);return out;}
function hasClass(node,name){return node.classList&&node.classList.contains(name);}
const state=STATE_JSON;const privateState=PRIVATE_JSON;
const participants=[
 {player_id:"human",display_name:"南山",seat_index:2,game_metadata:{team:"甲队",level:"7",deal_status:"在局"}},
 {player_id:"right",display_name:"右手",seat_index:3,game_metadata:{team:"乙队",level:"5",deal_status:"在局"}},
 {player_id:"partner",display_name:"对家",seat_index:0,game_metadata:{team:"甲队",level:"7",deal_status:"在局"}},
 {player_id:"left",display_name:"左手",seat_index:1,game_metadata:{team:"乙队",level:"5",deal_status:"已过"}},
];
function makeContext(legal=privateState.legal_actions){
 const board=new Element("div",document),controls=new Element("div",document),submitted=[],uiState={};let rerenders=0;
 const context={board,controls,state,privateState:{...privateState,legal_actions:legal},participants,viewer:{player_id:"human",seat:2},room:{current_player_id:"human",status:"playing"},canMove:true,legalActions:legal,uiState,
 helpers:{setBoardLayout(o){board.attributes.ariaLabel=o.ariaLabel;},canMove(){return true;},rerender(){rerenders+=1;return true;},async submitMove(m){submitted.push(m);return true;},renderParticipantAvatar(target,item){target.textContent=item.display_name[0];return true;}}};
 return {context,board,controls,submitted,uiState,rerenders:()=>rerenders};
}
'''.replace("STATE_JSON", json.dumps(state, ensure_ascii=False)).replace(
            "PRIVATE_JSON", json.dumps(private, ensure_ascii=False)
        ) + assertions
        completed = subprocess.run(
            [NODE, "-e", harness], cwd=ROOT, check=False, capture_output=True, text=True
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_runtime_places_all_four_seats_and_submits_only_id(self):
        self.run_node(r'''
(async()=>{
 const value=makeContext();renderer.renderBoard(value.context);const nodes=descendants(value.board);
 assert.equal(nodes.filter(n=>hasClass(n,"guandan-seat")).length,4);
 assert.equal(nodes.filter(n=>hasClass(n,"position-bottom")).length,1);
 assert.equal(nodes.filter(n=>hasClass(n,"position-top")).length,1);
 assert.equal(nodes.filter(n=>hasClass(n,"position-left")).length,1);
 assert.equal(nodes.filter(n=>hasClass(n,"position-right")).length,1);
 assert.equal(nodes.filter(n=>hasClass(n,"guandan-card")&&n.tag==="button").length,4);
 const handCards=nodes.filter(n=>hasClass(n,"guandan-card")&&n.tag==="button");
 // d2-S-4 is the other deck copy; the core action intentionally carries the
 // rule-equivalent canonical d1-S-4 representative.
 handCards.find(n=>n.dataset.cardId==="d2-S-4").listeners.click();
 handCards.find(n=>n.dataset.cardId==="d1-H-7").listeners.click();
 renderer.renderControls(value.context);const controls=descendants(value.controls);
 const play=controls.find(n=>hasClass(n,"guandan-play-button"));assert.equal(play.disabled,false);await play.listeners.click();
 assert.equal(JSON.stringify(value.submitted[0]),JSON.stringify({action:"act",action_id:"g_pair"}));
 const passValue=makeContext();renderer.renderControls(passValue.context);
 const pass=descendants(passValue.controls).find(n=>hasClass(n,"guandan-pass-button"));await pass.listeners.click();
 assert.equal(JSON.stringify(passValue.submitted[0]),JSON.stringify({action:"act",action_id:"g_pass"}));
 assert.equal(styles.size,1);
})().catch(e=>{console.error(e);process.exitCode=1;});
''')

    def test_source_is_valid_javascript(self):
        completed = subprocess.run(
            [NODE, "--check", str(SCRIPT_PATH)], cwd=ROOT,
            check=False, capture_output=True, text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)


if __name__ == "__main__":
    unittest.main()

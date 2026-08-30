import json
import shutil
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "app" / "static" / "games" / "mahjong.js"
STYLE_PATH = ROOT / "app" / "static" / "games" / "mahjong.css"
SCRIPT = SCRIPT_PATH.read_text(encoding="utf-8")
STYLES = STYLE_PATH.read_text(encoding="utf-8")
APP_SCRIPT = (ROOT / "app" / "static" / "app.js").read_text(encoding="utf-8")
HTML = (ROOT / "app" / "static" / "index.html").read_text(encoding="utf-8")
NODE = shutil.which("node")


class MahjongFrontendStructureTests(unittest.TestCase):
    def test_independent_board_edge_renderer_autoloads(self):
        self.assertIn('window));', SCRIPT)
        self.assertIn('global.DuelGameUI.register("mahjong"', SCRIPT)
        self.assertIn('participantPresentation: "board-edge"', SCRIPT)
        self.assertIn("ownsPrivateStatePresentation: true", SCRIPT)
        self.assertIn("usesStandardMoveConfirmation: false", SCRIPT)
        self.assertIn('const STYLE_HREF = "/static/games/mahjong.css?v=0.1.2";', SCRIPT)
        self.assertNotIn("mahjong", APP_SCRIPT)
        self.assertNotIn("mahjong.js", HTML)
        self.assertNotIn("mahjong.css", HTML)

    def test_four_edges_real_winds_center_discards_and_private_hand(self):
        for expected in (
            'const POSITION_ORDER = ["bottom", "right", "top", "left"]',
            "Number(participant.seat_index) - viewerSeat(context) + 4",
            "context.helpers.renderParticipantAvatar",
            "state.seat_winds",
            "state.dealer_player_id",
            "state.turn_player_id",
            "mahjong-discard-table",
            "mahjong-own-hand-scroll",
            "context.privateState.hand",
            "state.melds",
        ):
            self.assertIn(expected, SCRIPT)
        for area in ("grid-area: top", "grid-area: bottom", "grid-area: left", "grid-area: right"):
            self.assertIn(area, STYLES)

    def test_authoritative_actions_mobile_widths_and_no_page_overflow(self):
        self.assertIn("context.legalActions", SCRIPT)
        self.assertIn('action_id: action.action_id', SCRIPT)
        self.assertIn('context.board.classList.add("mahjong-board-layout")', SCRIPT)
        self.assertNotIn("MahjongFanCalculator", SCRIPT)
        self.assertNotIn("function canHu", SCRIPT)
        self.assertIn("overflow-x: auto;", STYLES)
        self.assertIn("touch-action: pan-x;", STYLES)
        self.assertIn("@media (max-width: 375px)", STYLES)
        self.assertIn("@media (max-width: 320px)", STYLES)
        self.assertIn("min-width: 0;", STYLES)
        self.assertIn("overflow: hidden;", STYLES)
        self.assertIn("min-height: 44px;", STYLES)
        self.assertIn("button.mahjong-tile:disabled { cursor: default; opacity: 1; }", STYLES)


@unittest.skipUnless(NODE, "node is required for renderer DOM tests")
class MahjongFrontendRuntimeTests(unittest.TestCase):
    def run_node(self, assertions):
        state = {
            "phase": "discard", "round_label": "东一局", "prevalent_wind": "东",
            "turn_player_id": "viewer", "dealer_player_id": "east", "wall_remaining": 71,
            "seat_winds": {"east": "东", "south": "南", "viewer": "西", "north": "北"},
            "hand_counts": {"east": 13, "south": 12, "viewer": 14, "north": 13},
            "melds": {
                "east": [], "south": [{"kind": "peng", "tiles": [
                    {"id": "f1", "code": "F1", "label": "东", "suit": "F", "rank": 1},
                    {"id": "f2", "code": "F1", "label": "东", "suit": "F", "rank": 1},
                    {"id": "f3", "code": "F1", "label": "东", "suit": "F", "rank": 1},
                ]}], "viewer": [], "north": [],
            },
            "discards": {
                "east": [{"id": "d1", "code": "W1", "label": "1万", "suit": "W", "rank": 1}],
                "south": [], "viewer": [], "north": [],
            },
            "last_discard": {"player_id": "east", "tile": {"id": "d1"}},
            "response_window": None, "game_result": None,
        }
        private = {
            "hand": [
                {"id": "h1", "code": "W2", "label": "2万", "suit": "W", "rank": 2},
                {"id": "h2", "code": "J1", "label": "中", "suit": "J", "rank": 1},
            ],
            "drawn_tile_id": "h2", "own_melds": [], "shanten": 1,
            "shanten_basis": "after_best_discard",
            "legal_actions": [
                {"action": "act", "action_id": "discard:h1", "kind": "discard", "label": "打 2万"},
                {"action": "act", "action_id": "concealed:1", "kind": "concealed_gang", "label": "暗杠 中"},
            ],
        }
        harness = r'''
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
class ClassList {
 constructor(){this.names=new Set();}
 set(value){this.names=new Set(String(value||"").split(/\s+/).filter(Boolean));}
 contains(name){return this.names.has(name);}
 toggle(name,force){const on=force===undefined?!this.names.has(name):Boolean(force);if(on)this.names.add(name);else this.names.delete(name);return on;}
 add(...names){names.forEach(name=>this.names.add(name));}
}
class Element {
 constructor(tag,doc){this.tag=tag;this.ownerDocument=doc;this.children=[];this.dataset={};this.attributes={};this.listeners={};this.classList=new ClassList();this.disabled=false;this.textContent="";this.id="";this.type="";}
 set className(value){this.classList.set(value);} get className(){return [...this.classList.names].join(" ");}
 appendChild(child){this.children.push(child);return child;} append(...children){this.children.push(...children);} replaceChildren(...children){this.children=children;}
 setAttribute(name,value){this.attributes[name]=String(value);} addEventListener(name,listener){this.listeners[name]=listener;}
}
const styles=new Map();
const document={head:{appendChild(n){if(n.id)styles.set(n.id,n);}},createElement(t){return new Element(t,document);},getElementById(id){return styles.get(id)||null;}};
let renderer=null;
const window={document,DuelGameUI:{register(type,value){assert.equal(type,"mahjong");renderer=value;}}};
vm.runInNewContext(fs.readFileSync("app/static/games/mahjong.js","utf8"),{window,document,console,Math,Map,Set,Number,String,Boolean,Array,Object,Promise});
function descendants(root){const out=[];const visit=n=>{out.push(n);n.children.forEach(visit);};root.children.forEach(visit);return out;}
function hasClass(node,name){return node.classList&&node.classList.contains(name);}
const state=STATE_JSON, privateState=PRIVATE_JSON;
const participants=[
 {player_id:"east",display_name:"东座",seat_index:0},
 {player_id:"south",display_name:"南座",seat_index:1},
 {player_id:"viewer",display_name:"看客",seat_index:2},
 {player_id:"north",display_name:"北座",seat_index:3},
];
function makeContext(){
 const board=new Element("div",document),controls=new Element("div",document),submitted=[],uiState={};let rerenders=0;
 const context={board,controls,state,privateState,participants,viewer:{player_id:"viewer",seat:2},room:{current_player_id:"viewer",status:"playing"},canMove:true,legalActions:privateState.legal_actions,uiState,
 helpers:{setBoardLayout(value){board.attributes.ariaLabel=value.ariaLabel;},rerender(){rerenders+=1;},async submitMove(move){submitted.push(move);},renderParticipantAvatar(target,item){target.textContent=item.display_name.slice(0,1);}}};
 return {context,board,controls,submitted,uiState,rerenders:()=>rerenders};
}
'''.replace("STATE_JSON", json.dumps(state, ensure_ascii=False)).replace(
            "PRIVATE_JSON", json.dumps(private, ensure_ascii=False)
        ) + assertions
        completed = subprocess.run(
            [NODE, "-e", harness], cwd=ROOT, check=False, capture_output=True, text=True
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_viewer_is_bottom_real_winds_stay_fixed_and_only_own_faces_show(self):
        self.run_node(r'''
const value=makeContext();renderer.renderBoard(value.context);const nodes=descendants(value.board);
assert.ok(hasClass(value.board,"mahjong-board-layout"));
assert.equal(nodes.filter(n=>hasClass(n,"mahjong-seat")).length,4);
for(const position of ["bottom","right","top","left"]){assert.equal(nodes.filter(n=>hasClass(n,`position-${position}`)).length,1);}
const bottom=nodes.find(n=>hasClass(n,"position-bottom"));assert.equal(bottom.dataset.playerId,"viewer");
const top=nodes.find(n=>hasClass(n,"position-top"));assert.equal(top.dataset.playerId,"east");
assert.equal(nodes.filter(n=>hasClass(n,"mahjong-opponent-hand")).length,4);
assert.equal(nodes.filter(n=>hasClass(n,"mahjong-tile-back")).length,38);
assert.equal(nodes.filter(n=>n.tag==="button"&&hasClass(n,"mahjong-tile")).length,2);
assert.equal(nodes.filter(n=>hasClass(n,"mahjong-discards")).length,4);
assert.equal(styles.size,1);
''')

    def test_discard_is_explicit_and_kong_requires_cancelable_confirmation(self):
        self.run_node(r'''
(async()=>{
 const value=makeContext();renderer.renderBoard(value.context);let nodes=descendants(value.board);
 let scroller=nodes.find(n=>hasClass(n,"mahjong-own-hand-scroll"));scroller.scrollLeft=88;
 const tile=nodes.find(n=>n.tag==="button"&&n.dataset.cardId==="h1");tile.listeners.click();
 assert.equal(value.uiState.mahjongSelectedTileId,"h1");assert.equal(value.rerenders(),1);
 assert.equal(value.uiState.mahjongHandScrollLeft,88);
 value.board.replaceChildren();renderer.renderBoard(value.context);nodes=descendants(value.board);
 scroller=nodes.find(n=>hasClass(n,"mahjong-own-hand-scroll"));assert.equal(scroller.scrollLeft,88);
 assert.equal(nodes.find(n=>n.dataset.cardId==="h1").classList.contains("selected"),true);
 renderer.renderControls(value.context);let controls=descendants(value.controls);
 const discard=controls.find(n=>n.dataset.actionId==="discard:h1");await discard.listeners.click();
 let kong=controls.find(n=>n.dataset.actionId==="concealed:1");await kong.listeners.click();
 assert.equal(value.submitted.length,1);
 assert.equal(value.uiState.mahjongPendingActionId,"concealed:1");

 renderer.renderControls(value.context);controls=descendants(value.controls);
 const cancel=controls.find(n=>hasClass(n,"mahjong-confirm-cancel"));cancel.listeners.click();
 assert.equal(value.submitted.length,1);
 assert.equal(value.uiState.mahjongPendingActionId,undefined);

 renderer.renderControls(value.context);controls=descendants(value.controls);
 kong=controls.find(n=>n.dataset.actionId==="concealed:1");kong.listeners.click();
 renderer.renderControls(value.context);controls=descendants(value.controls);
 const confirm=controls.find(n=>hasClass(n,"mahjong-confirm-submit"));
 await Promise.all([confirm.listeners.click(),confirm.listeners.click()]);
 assert.equal(JSON.stringify(value.submitted),JSON.stringify([
  {action:"act",action_id:"discard:h1"},{action:"act",action_id:"concealed:1"}
 ]));
})().catch(error=>{console.error(error);process.exitCode=1;});
''')

    def test_source_is_valid_javascript(self):
        completed = subprocess.run(
            [NODE, "--check", str(SCRIPT_PATH)], cwd=ROOT,
            check=False, capture_output=True, text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)


if __name__ == "__main__":
    unittest.main()

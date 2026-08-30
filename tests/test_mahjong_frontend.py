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
        self.assertIn('const STYLE_HREF = "/static/games/mahjong.css?v=0.3.2";', SCRIPT)
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
            'const table = el("div", "mahjong-table")',
            'const discardTable = el("div", "mahjong-discard-table")',
            "center.appendChild(statusNode(context))",
            "mahjong-center-status",
            "mahjong-tile-back",
            "mahjong-own-hand-scroll",
            "context.privateState.hand",
            "state.melds",
        ):
            self.assertIn(expected, SCRIPT)
        for area in ("grid-area: top", "grid-area: bottom", "grid-area: left", "grid-area: right"):
            self.assertIn(area, STYLES)
        self.assertIn('"top top top"', STYLES)
        self.assertIn('"left center right"', STYLES)
        self.assertIn('"bottom bottom bottom"', STYLES)
        self.assertIn('"dl . dr"', STYLES)
        self.assertIn("flex-direction: column;", STYLES)
        self.assertIn("grid-template-rows: minmax(0, 1fr) auto auto;", STYLES)
        self.assertIn('"identity hand"', STYLES)
        self.assertIn('"hand identity"', STYLES)
        self.assertIn("grid-template-columns: 1fr;", STYLES)
        self.assertIn(".mahjong-opponent-hand:empty", STYLES)
        self.assertIn("grid-template-columns: 1fr auto;", STYLES)
        self.assertIn(".mahjong-tile.last-discard", STYLES)
        self.assertIn("--mj-side: 48px;", STYLES)
        self.assertIn("--mj-tile-w: 14.5px;", STYLES)
        self.assertIn("--mj-tile-w: 13.5px;", STYLES)
        self.assertIn("writing-mode: vertical-rl;", STYLES)
        self.assertIn("text-orientation: upright;", STYLES)
        self.assertNotIn("rotate(", STYLES)

    def test_authoritative_actions_mobile_widths_and_no_page_overflow(self):
        self.assertIn("context.legalActions", SCRIPT)
        self.assertIn('action_id: action.action_id', SCRIPT)
        self.assertIn('context.board.classList.add("mahjong-board-layout")', SCRIPT)
        self.assertNotIn("MahjongFanCalculator", SCRIPT)
        self.assertNotIn("function canHu", SCRIPT)
        self.assertIn("overflow-x: auto;", STYLES)
        self.assertIn("touch-action: pan-x;", STYLES)
        self.assertIn("@media (max-width: 375px)", STYLES)
        self.assertIn("@media (max-width: 600px)", STYLES)
        self.assertIn("@media (max-width: 340px)", STYLES)
        self.assertIn("min-width: 0;", STYLES)
        self.assertIn("overflow: hidden;", STYLES)
        self.assertIn("min-height: 44px;", STYLES)
        self.assertIn("button.mahjong-tile:disabled { cursor: default; opacity: 1; }", STYLES)
        self.assertIn(".mahjong-control-hint.state-select", STYLES)
        self.assertIn("align-items: center;", STYLES)
        self.assertIn("font: 600 9px/1.3 system-ui, sans-serif;", STYLES)

    def test_top_seat_spans_table_and_thirteen_backs_stay_on_one_row(self):
        self.assertIn(
            'grid-template-areas:\n    "top top top"\n'
            '    "left center right"\n    "bottom bottom bottom"\n'
            '    "hand hand hand";',
            STYLES,
        )
        top_hand_start = STYLES.index(".position-top .mahjong-opponent-hand {")
        top_hand = STYLES[top_hand_start:STYLES.index("}", top_hand_start)]
        self.assertIn("width: 100%;", top_hand)
        self.assertIn("max-width: 100%;", top_hand)
        self.assertIn("flex-wrap: nowrap;", top_hand)
        self.assertIn("overflow: hidden;", top_hand)
        self.assertNotIn("flex-direction: column;", top_hand)
        self.assertIn(".mahjong-table {", STYLES)
        self.assertIn("width: 100%;", STYLES)
        self.assertIn("min-width: 0;", STYLES)
        top_row_width_at_390 = 13 * 14 + 12
        conservative_available_width_at_390 = 350
        self.assertLess(top_row_width_at_390, conservative_available_width_at_390)

        identity_start = STYLES.index(".position-top .mahjong-seat-identity {")
        identity = STYLES[identity_start:STYLES.index("}", identity_start)]
        self.assertIn("width: min(100%, 320px);", identity)
        mobile = STYLES[STYLES.index("@media (max-width: 600px)"):]
        self.assertIn(
            ".position-top .mahjong-seat-identity { width: min(100%, 220px); }",
            mobile,
        )

    def test_mobile_side_hands_are_vertical_rails_of_physically_sideways_backs(self):
        rail_selector = (
            ".position-left .mahjong-opponent-hand,\n"
            ".position-right .mahjong-opponent-hand {"
        )
        rail_start = STYLES.index(rail_selector)
        rail = STYLES[rail_start:STYLES.index("}", rail_start)]
        self.assertIn("width: var(--mj-back-h);", rail)
        self.assertIn("max-height: 100%;", rail)
        self.assertIn("flex-direction: column;", rail)
        self.assertIn("flex-wrap: nowrap;", rail)
        self.assertIn("gap: 0;", rail)
        self.assertIn("overflow: hidden;", rail)
        self.assertNotIn("\n  height: 100%;", rail)
        self.assertNotIn("flex-direction: row;", rail)
        self.assertNotIn("mahjong-tile-back + .mahjong-tile-back", STYLES)

        side_back_selector = (
            ".position-left .mahjong-opponent-hand .mahjong-tile-back,\n"
            ".position-right .mahjong-opponent-hand .mahjong-tile-back {"
        )
        side_back_start = STYLES.index(side_back_selector)
        side_back = STYLES[side_back_start:STYLES.index("}", side_back_start)]
        self.assertIn("width: var(--mj-back-h);", side_back)
        self.assertIn("height: var(--mj-back-w);", side_back)
        mobile_start = STYLES.index("@media (max-width: 600px)")
        mobile = STYLES[mobile_start:]
        self.assertIn("--mj-back-w: 14px;", mobile)
        self.assertIn("--mj-back-h: 22px;", mobile)
        side_back_width = 22
        side_back_height = 14
        self.assertGreater(side_back_width, side_back_height)
        self.assertEqual(13 * side_back_height, 182)
        self.assertIn("grid-template-rows: auto minmax(270px, auto) auto auto;", STYLES)

        name_selector = (
            ".position-left .mahjong-seat-name,\n"
            ".position-right .mahjong-seat-name {"
        )
        name_start = STYLES.index(name_selector)
        name = STYLES[name_start:STYLES.index("}", name_start)]
        self.assertIn("min-height: 0;", name)
        self.assertIn("max-height: 100%;", name)
        self.assertIn("writing-mode: vertical-rl;", name)
        self.assertIn("text-orientation: upright;", name)
        self.assertIn("line-height: 1;", name)
        self.assertEqual(STYLES.count("writing-mode:"), 1)
        self.assertEqual(STYLES.count("text-orientation:"), 1)

        identity_selector = (
            ".position-left .mahjong-seat-identity,\n"
            ".position-right .mahjong-seat-identity {"
        )
        identity_start = STYLES.index(identity_selector)
        identity = STYLES[identity_start:STYLES.index("}", identity_start)]
        self.assertIn("display: grid;", identity)
        self.assertIn("height: 100%;", identity)
        self.assertIn("grid-template-rows: auto minmax(0, 1fr) auto;", identity)
        self.assertIn('"avatar"', identity)
        self.assertIn('"name"', identity)
        self.assertIn('"badges"', identity)
        self.assertIn("grid-area: avatar;", STYLES)
        self.assertIn("grid-area: name;", name)
        self.assertIn("grid-area: badges;", STYLES)
        self.assertNotIn("display: none;", identity)

    def test_side_identity_and_hand_columns_are_mirrored_without_competing_for_width(self):
        left_start = STYLES.index(".mahjong-seat.position-left {")
        left = STYLES[left_start:STYLES.index("}", left_start)]
        self.assertIn("grid-template-columns: minmax(0, 1fr) var(--mj-back-h);", left)
        self.assertIn('"identity hand"', left)
        self.assertIn('"melds melds"', left)
        self.assertIn('"turn turn"', left)

        right_selector = ".mahjong-seat.position-right {\n  grid-area: right;"
        right_start = STYLES.index(right_selector)
        right = STYLES[right_start:STYLES.index("}", right_start)]
        self.assertIn("grid-template-columns: var(--mj-back-h) minmax(0, 1fr);", right)
        self.assertIn('"hand identity"', right)
        self.assertIn('"melds melds"', right)
        self.assertIn('"turn turn"', right)

        mobile = STYLES[STYLES.index("@media (max-width: 600px)"):]
        self.assertIn(
            ".mahjong-seat.position-left,\n"
            "  .mahjong-seat.position-right { column-gap: 0; }",
            mobile,
        )
        side_track_at_390 = 390 * 0.14
        side_content_at_390 = side_track_at_390 - 8 - 2
        identity_width_at_390 = side_content_at_390 - 22
        self.assertGreaterEqual(identity_width_at_390, 20)

    def test_turn_indicator_is_independent_and_positioned_by_seat_edge(self):
        self.assertIn('el("span", "mahjong-turn-indicator", "行动")', SCRIPT)
        self.assertIn('turnIndicator.setAttribute("aria-label", "当前行动玩家")', SCRIPT)
        self.assertIn("if (turnIndicator) seat.appendChild(turnIndicator);", SCRIPT)
        self.assertNotIn('badges.appendChild(el("i", "mahjong-turn", "行动"))', SCRIPT)
        self.assertIn(".mahjong-seat.current { border-color: #c89427;", STYLES)
        self.assertIn(
            ".position-top .mahjong-turn-indicator,\n"
            ".position-bottom .mahjong-turn-indicator {",
            STYLES,
        )
        self.assertIn("position: absolute;\n  top: 4px;\n  right: 4px;", STYLES)
        self.assertIn(
            ".position-left .mahjong-turn-indicator,\n"
            ".position-right .mahjong-turn-indicator {",
            STYLES,
        )
        self.assertIn("grid-area: turn;\n  align-self: end;", STYLES)
        mobile = STYLES[STYLES.index("@media (max-width: 600px)"):]
        self.assertIn(
            ".mahjong-seat.position-bottom.viewer { min-height: 0; padding-block: 3px; }",
            mobile,
        )
        self.assertNotIn(".mahjong-seat-badges .mahjong-turn", STYLES)

    def test_unicode_tile_faces_keep_text_labels_for_accessibility(self):
        expected = {
            "W1": "🀇", "W2": "🀈", "W3": "🀉", "W4": "🀊", "W5": "🀋",
            "W6": "🀌", "W7": "🀍", "W8": "🀎", "W9": "🀏",
            "T1": "🀐", "T2": "🀑", "T3": "🀒", "T4": "🀓", "T5": "🀔",
            "T6": "🀕", "T7": "🀖", "T8": "🀗", "T9": "🀘",
            "B1": "🀙", "B2": "🀚", "B3": "🀛", "B4": "🀜", "B5": "🀝",
            "B6": "🀞", "B7": "🀟", "B8": "🀠", "B9": "🀡",
            "F1": "🀀", "F2": "🀁", "F3": "🀂", "F4": "🀃",
            "J1": "🀄", "J2": "🀅", "J3": "🀆",
        }
        for code, face in expected.items():
            self.assertIn(f'{code}: "{face}"', SCRIPT)
        self.assertIn('TILE_UNICODE[code] || label', SCRIPT)
        self.assertIn('node.setAttribute("aria-label", label)', SCRIPT)

        tile_start = STYLES.index(".mahjong-tile {")
        tile_rule = STYLES[tile_start:STYLES.index("}", tile_start)]
        self.assertIn("width: var(--mj-tile-w);", tile_rule)
        self.assertIn("height: var(--mj-tile-h);", tile_rule)
        self.assertIn("padding: 1px;", tile_rule)
        self.assertIn("border: 1px solid #b7c4bb;", tile_rule)
        self.assertIn("border-radius: 3px;", tile_rule)
        self.assertIn(
            "background: linear-gradient(145deg, #fffef5 10%, #f7f5e9 62%, #e3e5d9);",
            tile_rule,
        )
        self.assertIn("box-shadow: 0 1px 1px rgb(28 61 49 / 23%);", tile_rule)
        self.assertIn(
            "font: 700 clamp(8px, 1.35vw, 13px)/1 system-ui, sans-serif;",
            tile_rule,
        )
        self.assertNotIn("background: transparent;", tile_rule)
        face_start = STYLES.index(".mahjong-tile-face {")
        face_rule = STYLES[face_start:STYLES.index("}", face_start)]
        self.assertIn("font-size: clamp(18px, 3.2vw, 28px);", face_rule)
        self.assertIn("overflow: hidden;", face_rule)
        self.assertIn('font-family: "Noto Sans Symbols 2", "Segoe UI Symbol", "Apple Symbols"', STYLES)
        self.assertIn("font-variant-emoji: text;", STYLES)
        self.assertIn("line-height: 1;", STYLES)
        self.assertIn("place-items: center;", STYLES)
        self.assertIn(
            ".mahjong-discard-grid .mahjong-tile {\n"
            "  width: var(--mj-discard-w);\n"
            "  height: var(--mj-discard-h);\n"
            "}",
            STYLES,
        )
        self.assertIn(
            ".mahjong-own-hand-scroll .mahjong-tile { width: clamp(26px, 6vw, 41px); "
            "height: clamp(37px, 8.4vw, 58px); font-size: clamp(12px, 2.6vw, 19px); }",
            STYLES,
        )
        self.assertIn(
            ".mahjong-own-hand-scroll .mahjong-tile-face { font-size: clamp(33px, 7.8vw, 53px); transform: translateY(-4px); overflow: visible; }",
            STYLES,
        )
        self.assertIn(
            ".mahjong-terminal-hand .mahjong-tile { width: 22px; height: 31px; font-size: 9px; }",
            STYLES,
        )
        self.assertIn(
            ".mahjong-terminal-hand .mahjong-tile-face { font-size: 23px; }",
            STYLES,
        )
        self.assertIn(
            ".mahjong-own-hand-scroll .mahjong-tile.drawn { margin-left: 5px; }",
            STYLES,
        )
        self.assertIn(
            ".mahjong-own-hand-scroll .mahjong-tile.selected { transform: translateY(-4px); "
            "outline: 3px solid #d59a22; }",
            STYLES,
        )
        self.assertIn(
            ".mahjong-tile.last-discard { outline: 2px solid #d29518; outline-offset: 1px; "
            "box-shadow: 0 0 0 3px rgb(255 250 220 / 75%); }",
            STYLES,
        )

        table_start = STYLES.index(".mahjong-table {")
        table_rule = STYLES[table_start:STYLES.index("}", table_start)]
        self.assertIn("--mj-tile-w: clamp(17px, 3vw, 27px);", table_rule)
        self.assertIn("--mj-tile-h: clamp(24px, 4.1vw, 37px);", table_rule)
        self.assertIn("--mj-back-w: clamp(14px, 2.4vw, 20px);", table_rule)
        self.assertIn("--mj-back-h: clamp(23px, 3.5vw, 31px);", table_rule)

        hand_start = STYLES.index(".mahjong-own-hand-scroll {")
        hand_rule = STYLES[hand_start:STYLES.index("}", hand_start)]
        self.assertIn("gap: 1px;", hand_rule)

        mobile = STYLES[STYLES.index("@media (max-width: 600px)"):]
        self.assertIn("--mj-tile-w: clamp(15.5px, 4vw, 18px);", mobile)
        self.assertIn("--mj-tile-h: clamp(22px, 5.75vw, 26px);", mobile)
        self.assertIn("--mj-back-w: 14px;", mobile)
        self.assertIn("--mj-back-h: 22px;", mobile)
        self.assertIn(
            ".mahjong-own-hand-scroll .mahjong-tile { width: 25px; height: 36px; font-size: 12px; }",
            mobile,
        )
        self.assertIn(
            ".mahjong-own-hand-scroll .mahjong-tile-face { font-size: 34px; transform: translateY(-4px); overflow: visible; }",
            mobile,
        )
        self.assertIn(
            ".mahjong-meld .mahjong-tile-face { font-size: clamp(17px, 4.5vw, 20px); }",
            mobile,
        )

        narrow = STYLES[STYLES.index("@media (max-width: 375px)"):]
        self.assertIn(".mahjong-table { --mj-tile-w: 14.5px; --mj-tile-h: 22px; }", narrow)
        self.assertIn(
            ".mahjong-own-hand-scroll .mahjong-tile { width: 23px; height: 33px; font-size: 12px; }",
            narrow,
        )
        self.assertIn(
            ".mahjong-own-hand-scroll .mahjong-tile-face { font-size: 32px; transform: translateY(-4px); overflow: visible; }",
            narrow,
        )
        self.assertIn(
            ".mahjong-meld .mahjong-tile-face { font-size: 16px; }",
            narrow,
        )

        smallest = STYLES[STYLES.index("@media (max-width: 340px)"):]
        self.assertIn("--mj-tile-w: 13.5px;", smallest)
        self.assertIn("--mj-tile-h: 20px;", smallest)
        self.assertIn(
            ".mahjong-own-hand-scroll .mahjong-tile { width: 22px; min-width: 22px; height: 32px; }",
            smallest,
        )
        self.assertIn(
            ".mahjong-own-hand-scroll .mahjong-tile-face { font-size: 31px; transform: translateY(-4px); overflow: visible; }",
            smallest,
        )
        self.assertIn(
            ".mahjong-meld .mahjong-tile-face { font-size: 15px; }",
            smallest,
        )


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
 const context={board,controls,state,privateState,participants,viewer:{player_id:"viewer",seat:2},room:{current_player_id:"viewer",status:"playing"},canMove:true,isTerminal:false,legalActions:privateState.legal_actions,uiState,
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
const left=nodes.find(n=>hasClass(n,"position-left"));
const right=nodes.find(n=>hasClass(n,"position-right"));
assert.equal(descendants(left).find(n=>hasClass(n,"mahjong-seat-name")).textContent,"南座");
assert.equal(descendants(right).find(n=>hasClass(n,"mahjong-seat-name")).textContent,"北座");
assert.equal(nodes.filter(n=>hasClass(n,"mahjong-opponent-hand")).length,4);
assert.equal(nodes.filter(n=>hasClass(n,"mahjong-tile-back")).length,38);
assert.equal(nodes.filter(n=>n.tag==="button"&&hasClass(n,"mahjong-tile")).length,2);
assert.equal(nodes.filter(n=>hasClass(n,"mahjong-discards")).length,4);
const discardTable=nodes.find(n=>hasClass(n,"mahjong-discard-table"));assert.ok(discardTable);
const discardNodes=descendants(discardTable);
const center=nodes.find(n=>hasClass(n,"mahjong-center"));assert.ok(center);
assert.equal(center.children[0].classList.contains("mahjong-center-status"),true);
assert.equal(center.children[1],discardTable);
assert.equal(discardNodes.filter(n=>hasClass(n,"mahjong-center-status")).length,0);
for(const position of ["bottom","right","top","left"]){assert.equal(discardNodes.filter(n=>hasClass(n,`discard-${position}`)).length,1);}
assert.equal(discardNodes.filter(n=>hasClass(n,"last-discard")).length,1);
const discard=discardNodes.find(n=>n.dataset.cardId==="d1");
assert.equal(descendants(discard).find(n=>hasClass(n,"mahjong-tile-face")).textContent,"🀇");
assert.equal(discard.attributes["aria-label"],"1万");
const meld=nodes.find(n=>n.dataset.cardId==="f1");
assert.equal(descendants(meld).find(n=>hasClass(n,"mahjong-tile-face")).textContent,"🀀");
assert.equal(meld.attributes["aria-label"],"东");
const handW2=nodes.find(n=>n.dataset.cardId==="h1");
const handJ1=nodes.find(n=>n.dataset.cardId==="h2");
assert.equal(descendants(handW2).find(n=>hasClass(n,"mahjong-tile-face")).textContent,"🀈");
assert.equal(handW2.attributes["aria-label"],"2万");
assert.equal(descendants(handJ1).find(n=>hasClass(n,"mahjong-tile-face")).textContent,"🀄");
assert.equal(handJ1.attributes["aria-label"],"中");
assert.equal(styles.size,1);
assert.equal([...styles.values()][0].href,"/static/games/mahjong.css?v=0.3.2");
''')

    def test_all_tile_codes_map_to_unicode_without_replacing_aria_labels(self):
        self.run_node(r'''
const codes=[
 "W1","W2","W3","W4","W5","W6","W7","W8","W9",
 "T1","T2","T3","T4","T5","T6","T7","T8","T9",
 "B1","B2","B3","B4","B5","B6","B7","B8","B9",
 "F1","F2","F3","F4","J1","J2","J3",
];
const expected=[..."🀇🀈🀉🀊🀋🀌🀍🀎🀏🀐🀑🀒🀓🀔🀕🀖🀗🀘🀙🀚🀛🀜🀝🀞🀟🀠🀡🀀🀁🀂🀃🀄🀅🀆"];
state.terminal_hands={
 east:codes.map((code,index)=>({id:`all-${index}`,code,label:`原文-${code}`,suit:code[0]})),
 south:[],viewer:[],north:[],
};
const value=makeContext();renderer.renderBoard(value.context);const nodes=descendants(value.board);
const review=nodes.find(n=>hasClass(n,"mahjong-terminal-review"));
const visibleTiles=descendants(review).filter(n=>hasClass(n,"mahjong-tile"));
assert.deepEqual(
 visibleTiles.map(tile=>descendants(tile).find(n=>hasClass(n,"mahjong-tile-face")).textContent),
 expected
);
assert.deepEqual(visibleTiles.map(tile=>tile.attributes["aria-label"]),codes.map(code=>`原文-${code}`));
''')

    def test_turn_indicator_is_a_direct_seat_child_at_every_visual_edge(self):
        self.run_node(r'''
for(const [playerId,position] of [["east","top"],["south","left"],["viewer","bottom"],["north","right"]]){
 state.turn_player_id=playerId;
 const value=makeContext();renderer.renderBoard(value.context);const nodes=descendants(value.board);
 const seats=nodes.filter(n=>hasClass(n,"mahjong-seat"));
 const current=seats.find(n=>n.dataset.playerId===playerId);
 assert.ok(current);assert.equal(hasClass(current,`position-${position}`),true);
 assert.equal(current.classList.contains("current"),true);
 const directIndicators=current.children.filter(n=>hasClass(n,"mahjong-turn-indicator"));
 assert.equal(directIndicators.length,1);
 assert.equal(directIndicators[0].textContent,"行动");
 assert.equal(directIndicators[0].attributes["aria-label"],"当前行动玩家");
 const badges=current.children.find(n=>hasClass(n,"mahjong-seat-identity")).children.find(n=>hasClass(n,"mahjong-seat-badges"));
 assert.equal(descendants(badges).filter(n=>hasClass(n,"mahjong-turn-indicator")).length,0);
 assert.equal(seats.flatMap(seat=>seat.children).filter(n=>hasClass(n,"mahjong-turn-indicator")).length,1);
}
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

    def test_control_hints_distinguish_selection_waiting_and_terminal_states(self):
        self.run_node(r'''
const value=makeContext();
value.context.legalActions=[{action:"act",action_id:"discard:h1",kind:"discard",label:"打 2万"}];
renderer.renderControls(value.context);let nodes=descendants(value.controls);
let hint=nodes.find(n=>hasClass(n,"mahjong-control-hint"));
assert.equal(hint.textContent,"请选择一张手牌打出");
assert.equal(hint.attributes.role,"status");
assert.equal(nodes.filter(n=>hasClass(n,"mahjong-action")).length,0);

value.context.canMove=false;renderer.renderControls(value.context);nodes=descendants(value.controls);
hint=nodes.find(n=>hasClass(n,"mahjong-control-hint"));
assert.equal(hint.textContent,"等待其他玩家行动");

value.context.isTerminal=true;renderer.renderControls(value.context);nodes=descendants(value.controls);
hint=nodes.find(n=>hasClass(n,"mahjong-control-hint"));
assert.equal(hint.textContent,"本手已结束");

value.context.isTerminal=false;value.context.canMove=true;
value.context.legalActions=[{action:"act",action_id:"pass:1",kind:"pass",label:"过"}];
renderer.renderControls(value.context);nodes=descendants(value.controls);
assert.ok(nodes.find(n=>n.dataset.actionId==="pass:1"));
assert.equal(nodes.filter(n=>hasClass(n,"mahjong-control-hint")).length,0);
''')

    def test_all_authoritative_action_kinds_render_and_hu_uses_hu_copy(self):
        self.run_node(r'''
const value=makeContext();
value.context.legalActions=[
 {action:"act",action_id:"discard:h1",kind:"discard",label:"打 2万"},
 {action:"act",action_id:"chi:123",kind:"chi",label:"吃 1万 2万 3万"},
 {action:"act",action_id:"chi:234",kind:"chi",label:"吃 2万 3万 4万"},
 {action:"act",action_id:"peng:2",kind:"peng",label:"碰 2万"},
 {action:"act",action_id:"ming_gang:2",kind:"ming_gang",label:"明杠 2万"},
 {action:"act",action_id:"concealed:2",kind:"concealed_gang",label:"暗杠 2万"},
 {action:"act",action_id:"added:2",kind:"added_gang",label:"加杠 2万"},
 {action:"act",action_id:"hu:self",kind:"hu",label:"和（12 番）",public_label:"和牌",total_fan:12},
 {action:"act",action_id:"pass:2",kind:"pass",label:"过"},
];
renderer.renderControls(value.context);let nodes=descendants(value.controls);
const buttons=nodes.filter(n=>hasClass(n,"mahjong-action"));
assert.equal(buttons.length,8);
assert.deepEqual(
 buttons.map(n=>n.dataset.actionId).sort(),
 ["added:2","chi:123","chi:234","concealed:2","hu:self","ming_gang:2","pass:2","peng:2"].sort()
);
assert.equal(nodes.filter(n=>n.dataset.actionId==="discard:h1").length,0);
assert.equal(nodes.find(n=>n.dataset.actionId==="hu:self").textContent,"胡（12 番）");
assert.equal(nodes.find(n=>hasClass(n,"mahjong-control-hint")).textContent,"请选择一张手牌打出");

nodes.find(n=>n.dataset.actionId==="hu:self").listeners.click();
renderer.renderControls(value.context);nodes=descendants(value.controls);
assert.equal(nodes.find(n=>hasClass(n,"mahjong-confirmation-copy")).textContent,"已选择：胡（12 番）");
nodes.find(n=>hasClass(n,"mahjong-confirm-cancel")).listeners.click();
assert.equal(value.uiState.mahjongPendingActionId,undefined);

value.uiState.mahjongSelectedTileId="h1";
renderer.renderControls(value.context);nodes=descendants(value.controls);
assert.ok(nodes.find(n=>n.dataset.actionId==="discard:h1"));
assert.equal(nodes.filter(n=>hasClass(n,"mahjong-control-hint")).length,0);

value.context.legalActions=[{action:"act",action_id:"hu:fallback",kind:"hu",public_label:"和牌"}];
delete value.uiState.mahjongSelectedTileId;
renderer.renderControls(value.context);nodes=descendants(value.controls);
assert.equal(nodes.find(n=>n.dataset.actionId==="hu:fallback").textContent,"胡牌");
''')

    def test_terminal_hands_and_concealed_kong_faces_are_reviewable(self):
        self.run_node(r'''
state.phase="finished";
state.terminal_hands={east:privateState.hand,south:privateState.hand,viewer:privateState.hand,north:privateState.hand};
state.melds.south=[{kind:"concealed_gang",tiles:[
 {id:"g1",code:"J2",label:"发",suit:"J",rank:2},
 {id:"g2",code:"J2",label:"发",suit:"J",rank:2},
 {id:"g3",code:"J2",label:"发",suit:"J",rank:2},
 {id:"g4",code:"J2",label:"发",suit:"J",rank:2},
]}];
const value=makeContext();value.context.canMove=false;value.context.room.status="finished";
renderer.renderBoard(value.context);const nodes=descendants(value.board);
const review=nodes.find(n=>hasClass(n,"mahjong-terminal-review"));assert.ok(review);
const reviewNodes=descendants(review);
assert.equal(reviewNodes.filter(n=>hasClass(n,"mahjong-terminal-row")).length,4);
assert.equal(reviewNodes.filter(n=>hasClass(n,"mahjong-tile")&&!hasClass(n,"mahjong-tile-back")).length,8);
const south=nodes.find(n=>hasClass(n,"mahjong-seat")&&n.dataset.playerId==="south");
const southNodes=descendants(south);
assert.equal(southNodes.filter(n=>hasClass(n,"mahjong-meld")&&hasClass(n,"kind-concealed_gang")).length,1);
assert.equal(southNodes.filter(n=>hasClass(n,"mahjong-tile-back")).length,12);
assert.equal(southNodes.filter(n=>hasClass(n,"mahjong-tile")&&!hasClass(n,"mahjong-tile-back")).length,4);
''')

    def test_source_is_valid_javascript(self):
        completed = subprocess.run(
            [NODE, "--check", str(SCRIPT_PATH)], cwd=ROOT,
            check=False, capture_output=True, text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)


if __name__ == "__main__":
    unittest.main()

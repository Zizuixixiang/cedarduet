import shutil
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "app" / "static" / "games" / "liars_dice.js"
STYLE_PATH = ROOT / "app" / "static" / "games" / "liars_dice.css"
SCRIPT = SCRIPT_PATH.read_text(encoding="utf-8")
STYLES = STYLE_PATH.read_text(encoding="utf-8")
PUBLIC_STYLES = (ROOT / "app" / "static" / "styles.css").read_text(
    encoding="utf-8"
)
HTML = (ROOT / "app" / "static" / "index.html").read_text(encoding="utf-8")
NODE = shutil.which("node")


class LiarsDiceFrontendStructureTests(unittest.TestCase):
    def test_thin_renderer_reuses_legacy_table_without_modifying_app(self):
        self.assertIn('global.DuelGameUI.register("liars_dice"', SCRIPT)
        self.assertIn("global.renderLiarsDice(context.board, context.state)", SCRIPT)
        self.assertIn("usesStandardMoveConfirmation: false", SCRIPT)
        self.assertIn("state.terminal_dice", SCRIPT)
        self.assertIn('/static/games/liars_dice.js?v=0.1.10', HTML)
        self.assertIn('die.setAttribute("data-face", String(face))', SCRIPT)
        self.assertNotIn('dice.join(" · ")', SCRIPT)
        for face in range(1, 7):
            self.assertIn(f'.liars-die[data-face="{face}"]', PUBLIC_STYLES)
        self.assertIn("radial-gradient(circle at 50% 50%", PUBLIC_STYLES)
        self.assertIn("@media (max-width: 375px)", STYLES)
        self.assertIn("grid-template-columns: 1fr", STYLES)


@unittest.skipUnless(NODE, "node is required for Liars Dice renderer tests")
class LiarsDiceFrontendRuntimeTests(unittest.TestCase):
    def test_party_team_scores_render_once_with_members_and_preserve_solo_history(self):
        harness = r'''
const assert=require("node:assert/strict"),fs=require("node:fs"),vm=require("node:vm");
class Element {
 constructor(tag,doc){this.tag=tag;this.ownerDocument=doc;this.children=[];this.dataset={};this.attributes={};this.className="";this.textContent="";}
 appendChild(node){this.children.push(node);return node;} append(...nodes){nodes.forEach(n=>this.appendChild(n));}
 setAttribute(k,v){this.attributes[k]=String(v);}
}
const document={head:{appendChild(){}},createElement(t){return new Element(t,document);},getElementById(){return null;}};
let renderer;
const window={document,renderLiarsDice(){},DuelGameUI:{register(type,r){renderer=r;}}};
vm.runInNewContext(fs.readFileSync("app/static/games/liars_dice.js","utf8"),{window,document});
const participants=Array.from({length:6},(_,i)=>({player_id:`p${i}`,display_name:["南杉的很长昵称".repeat(8),"CodexLongName".repeat(8),"<Sirius>长昵称".repeat(8)][i%3]}));
const find=(node,cls)=>[...(node.className.split(" ").includes(cls)?[node]:[]),...node.children.flatMap(c=>find(c,cls))];
const render=state=>{const board=new Element("div",document),before=JSON.stringify(state);renderer.renderBoard({board,state,participants});assert.equal(JSON.stringify(state),before);return board;};
const history=[{round:1,phase:"surrendered",loser_player_id:"p0",loser_team_id:"red",score_delta:-.5,score_after:-.5}];
for(const count of [4,6]) {
 const ids=participants.slice(0,count).map(p=>p.player_id);
 const teams={red:ids.slice(0,count/2),blue:ids.slice(count/2)};
 const scores=Object.fromEntries(ids.map(id=>[id,-7]));
 const state={mode:"party",teams,scores,team_scores:{red:0,blue:-1.5},round_results:history};
 let board=render(state),cards=find(board,"liars-team-score-card");
 assert.equal(cards.length,2);
 assert.equal(find(board,"liars-score-row").length,0);
 assert.deepEqual(find(board,"liars-team-score-value").map(n=>n.textContent),["0分","-1.5分"]);
 assert.deepEqual(find(board,"liars-team-score-name").map(n=>n.textContent),["红队","蓝队"]);
 for(const [i,team] of ["red","blue"].entries()) {
   assert.ok(cards[i].className.includes(`liars-team-${team}`));
   assert.equal(cards[i].attributes["aria-label"],`${i===0?"红":"蓝"}队共享积分`);
   const members=find(cards[i],"liars-team-score-members")[0];
   assert.equal(members.tag,"ul");
   assert.deepEqual(members.children.map(n=>n.textContent),teams[team].map(id=>participants.find(p=>p.player_id===id).display_name));
   assert.ok(members.children.every(n=>n.tag==="li" && n.children.length===0));
 }
 assert.equal(find(board,"liars-reveal-details")[0].children[0].textContent,"回合扣分记录（1轮）");
 assert.ok(find(board,"liars-result-line")[0].textContent.includes("投降认罚"));
 delete state.team_scores;
 delete scores.p0; // Compatibility fallback takes a known member score, not a sum.
 board=render(state);
 assert.deepEqual(find(board,"liars-team-score-value").map(n=>n.textContent),["-7分","-7分"]);
}
for(const teams of [undefined,{}]) {
 const board=render({mode:"party",teams,scores:{p0:-.5,p1:0},round_results:history});
 assert.equal(find(board,"liars-team-score-card").length,0);
 assert.equal(find(board,"liars-score-row").length,2);
 assert.deepEqual(find(board,"liars-score-value").map(n=>n.textContent),["-0.5分","0分"]);
 assert.equal(find(board,"liars-reveal-details").length,1);
}
assert.equal(find(render({mode:"classic"}),"liars-party-scores").length,0);
'''
        completed = subprocess.run([NODE, "-e", harness], cwd=ROOT,
                                   text=True, capture_output=True, check=False)
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_terminal_dice_append_compact_named_review_only_at_terminal(self):
        harness = r'''
const assert=require("node:assert/strict"),fs=require("node:fs"),vm=require("node:vm");
class ClassList{constructor(){this.names=new Set();}set(v){this.names=new Set(String(v||"").split(/\s+/).filter(Boolean));}}
class Element{constructor(tag,doc){this.tag=tag;this.ownerDocument=doc;this.children=[];this.dataset={};this.attributes={};this.classList=new ClassList();this.textContent="";}
 set className(v){this.classList.set(v);}get className(){return [...this.classList.names].join(" ");}appendChild(n){this.children.push(n);return n;}append(...nodes){nodes.forEach((node)=>this.appendChild(node));}setAttribute(name,value){this.attributes[name]=String(value);}}
const styles=new Map();const document={head:{appendChild(n){styles.set(n.id,n);}},createElement(t){return new Element(t,document);},getElementById(id){return styles.get(id)||null;}};
let renderer=null,legacyCalls=0;
const window={document,renderLiarsDice(board){legacyCalls+=1;const legacy=new Element("section",document);legacy.className="legacy-liars-table";board.appendChild(legacy);},DuelGameUI:{register(type,value){assert.equal(type,"liars_dice");renderer=value;}}};
vm.runInNewContext(fs.readFileSync("app/static/games/liars_dice.js","utf8"),{window,document,Object,Array,String,Boolean,Number,console});
const participants=[{player_id:"human",display_name:"南山"},{player_id:"ai",display_name:"小机"}];
const render=(state)=>{const board=new Element("div",document);renderer.renderBoard({board,state,participants});return board;};
let board=render({flow:{phase:"bidding"}});assert.equal(board.children.length,1);assert.equal(legacyCalls,1);
board=render({flow:{phase:"finished"},terminal_dice:{human:[1,2,3],ai:[6,6]}});
assert.equal(legacyCalls,2);assert.equal(board.children.length,2);
const review=board.children[1];assert.equal(review.className,"liars-terminal-review");
assert.equal(review.open,true);
review.open=false;assert.equal(review.open,false);
assert.equal(review.children[0].textContent,"终局骰子复盘 · 2 家");
const rows=review.children[1].children;
assert.equal(rows[0].children[0].textContent,"南山：");
assert.deepEqual(rows[0].children[1].children.map((die)=>die.attributes["data-face"]),["1","2","3"]);
assert.equal(rows[1].children[0].textContent,"小机：");
assert.deepEqual(rows[1].children[1].children.map((die)=>die.attributes["data-face"]),["6","6"]);
assert.ok(rows.flatMap((row)=>row.children[1].children).every((die)=>die.textContent===""));
assert.equal(styles.get("duel-game-liars-dice-review-styles").href,"/static/games/liars_dice.css?v=0.1.10");
'''
        completed = subprocess.run(
            [NODE, "-e", harness], cwd=ROOT, text=True, capture_output=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_source_is_valid_javascript(self):
        completed = subprocess.run(
            [NODE, "--check", str(SCRIPT_PATH)], cwd=ROOT,
            text=True, capture_output=True, check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)


if __name__ == "__main__":
    unittest.main()

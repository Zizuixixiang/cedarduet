import shutil
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "app" / "static" / "games" / "liars_dice.js"
STYLE_PATH = ROOT / "app" / "static" / "games" / "liars_dice.css"
SCRIPT = SCRIPT_PATH.read_text(encoding="utf-8")
STYLES = STYLE_PATH.read_text(encoding="utf-8")
HTML = (ROOT / "app" / "static" / "index.html").read_text(encoding="utf-8")
NODE = shutil.which("node")


class LiarsDiceFrontendStructureTests(unittest.TestCase):
    def test_thin_renderer_reuses_legacy_table_without_modifying_app(self):
        self.assertIn('global.DuelGameUI.register("liars_dice"', SCRIPT)
        self.assertIn("global.renderLiarsDice(context.board, context.state)", SCRIPT)
        self.assertIn("usesStandardMoveConfirmation: false", SCRIPT)
        self.assertIn("state.terminal_dice", SCRIPT)
        self.assertIn('/static/games/liars_dice.js?v=0.1.0', HTML)
        self.assertIn("@media (max-width: 375px)", STYLES)
        self.assertIn("grid-template-columns: 1fr", STYLES)


@unittest.skipUnless(NODE, "node is required for Liars Dice renderer tests")
class LiarsDiceFrontendRuntimeTests(unittest.TestCase):
    def test_terminal_dice_append_compact_named_review_only_at_terminal(self):
        harness = r'''
const assert=require("node:assert/strict"),fs=require("node:fs"),vm=require("node:vm");
class ClassList{constructor(){this.names=new Set();}set(v){this.names=new Set(String(v||"").split(/\s+/).filter(Boolean));}}
class Element{constructor(tag,doc){this.tag=tag;this.ownerDocument=doc;this.children=[];this.dataset={};this.classList=new ClassList();this.textContent="";}
 set className(v){this.classList.set(v);}get className(){return [...this.classList.names].join(" ");}appendChild(n){this.children.push(n);return n;}}
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
assert.equal(review.children[0].textContent,"终局骰子复盘 · 2 家");
assert.equal(review.children[1].children[0].textContent,"南山：1 · 2 · 3");
assert.equal(review.children[1].children[1].textContent,"小机：6 · 6");
assert.equal(styles.get("duel-game-liars-dice-review-styles").href,"/static/games/liars_dice.css?v=0.1.0");
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

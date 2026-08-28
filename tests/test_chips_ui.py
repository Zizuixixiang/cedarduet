import shutil
import subprocess
import unittest
from html.parser import HTMLParser
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "app" / "static" / "chips.html").read_text(encoding="utf-8")
SCRIPT = (ROOT / "app" / "static" / "chips.js").read_text(encoding="utf-8")
NODE = shutil.which("node")


class ChipMarkupParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.elements = []

    def handle_starttag(self, tag, attrs):
        self.elements.append((tag, dict(attrs)))


def function_source(name: str) -> str:
    start = SCRIPT.index(f"function {name}(")
    end = SCRIPT.find("\nfunction ", start + 1)
    return SCRIPT[start:] if end < 0 else SCRIPT[start:end]


class ChipCenterStructureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        parser = ChipMarkupParser()
        parser.feed(HTML)
        cls.elements = parser.elements

    def attributes_for_id(self, element_id):
        return next(attrs for _, attrs in self.elements if attrs.get("id") == element_id)

    def test_daily_check_in_is_the_only_default_panel(self):
        tabs = [attrs for _, attrs in self.elements if attrs.get("role") == "tab"]
        panels = [attrs for _, attrs in self.elements if attrs.get("role") == "tabpanel"]

        self.assertEqual(len(tabs), 4)
        self.assertEqual(len(panels), 4)
        self.assertEqual(
            [tab["id"] for tab in tabs if tab.get("aria-selected") == "true"],
            ["tab-checkin"],
        )
        self.assertNotIn("hidden", self.attributes_for_id("panel-checkin"))
        for panel_id in ("panel-achievements", "panel-social", "panel-ledger"):
            self.assertIn("hidden", self.attributes_for_id(panel_id))

    def test_overview_is_not_repeated_as_a_module(self):
        self.assertEqual(HTML.count('id="myBalance"'), 1)
        self.assertEqual(HTML.count('id="bankruptcyButton"'), 1)
        self.assertEqual(HTML.count('id="subjectSelect"'), 1)
        self.assertNotIn('id="walletModuleTitle"', HTML)
        self.assertNotIn('id="walletBalance"', HTML)
        self.assertNotIn("A. 我的筹码", HTML)

    def test_subject_selector_defaults_to_me_inside_overview(self):
        select = self.attributes_for_id("subjectSelect")
        self.assertIn("disabled", select)
        self.assertIn('<option value="human">我</option>', HTML)
        self.assertLess(HTML.index('class="balance-strip"'), HTML.index('id="subjectSelect"'))
        self.assertLess(HTML.index('id="subjectSelect"'), HTML.index("</section>"))

    def test_independent_machine_switch_card_is_removed(self):
        self.assertNotIn("machine-switch", HTML)
        self.assertNotIn("machine-switch", SCRIPT)
        self.assertNotIn('id="machineSelect"', HTML)
        self.assertNotIn("查看小机", HTML)

    def test_existing_chip_api_paths_are_unchanged(self):
        for path in (
            'requestJson("/api/chips")',
            'runHumanAction("/api/chips/check-in")',
            'runHumanAction("/api/chips/bankruptcy")',
            'requestJson(`/api/chips/machines/${encodeURIComponent(machineId)}`)',
        ):
            self.assertIn(path, SCRIPT)
        self.assertIn("fetch(apiPath(url)", SCRIPT)


@unittest.skipUnless(NODE, "node is required for frontend behavior tests")
class ChipCenterTabBehaviorTests(unittest.TestCase):
    def test_clicking_a_tab_shows_only_its_panel(self):
        functions = "\n".join(
            (
                function_source("activateModuleTab"),
                function_source("handleModuleTabKeydown"),
                function_source("initModuleTabs"),
            )
        )
        harness = f"""
const assert = require("node:assert/strict");
class Element {{
  constructor(id, panel, selected = false) {{
    this.id = id;
    this.dataset = panel ? {{panel}} : {{}};
    this.hidden = false;
    this.tabIndex = selected ? 0 : -1;
    this.attributes = {{"aria-selected": String(selected)}};
    this.listeners = {{}};
  }}
  addEventListener(name, callback) {{ this.listeners[name] = callback; }}
  getAttribute(name) {{ return this.attributes[name]; }}
  setAttribute(name, value) {{ this.attributes[name] = value; }}
  focus() {{ this.focused = true; }}
}}
const tabs = [
  new Element("tab-checkin", "panel-checkin", true),
  new Element("tab-achievements", "panel-achievements"),
  new Element("tab-social", "panel-social"),
  new Element("tab-ledger", "panel-ledger"),
];
const panels = tabs.map((tab) => new Element(tab.dataset.panel));
const document = {{
  querySelectorAll(selector) {{ return selector.includes("tabpanel") ? panels : tabs; }},
}};
{functions}
initModuleTabs();
assert.deepEqual(panels.map((panel) => panel.hidden), [false, true, true, true]);
tabs[2].listeners.click();
assert.deepEqual(tabs.map((tab) => tab.attributes["aria-selected"]), ["false", "false", "true", "false"]);
assert.deepEqual(panels.map((panel) => panel.hidden), [true, true, false, true]);
"""
        completed = subprocess.run(
            [NODE, "-e", harness],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(
            completed.returncode,
            0,
            f"JavaScript assertion failed:\n{completed.stderr}",
        )

    def test_switching_subject_updates_all_panels_and_restores_human_actions(self):
        harness = f"""
import assert from "node:assert/strict";
class ClassList {{
  constructor() {{ this.values = new Set(); }}
  add(name) {{ this.values.add(name); }}
  remove(name) {{ this.values.delete(name); }}
  contains(name) {{ return this.values.has(name); }}
  toggle(name, force) {{
    const enabled = force === undefined ? !this.values.has(name) : force;
    if (enabled) this.values.add(name); else this.values.delete(name);
    return enabled;
  }}
}}
class Element {{
  constructor(id = "", panel = "", selected = false) {{
    this.id = id;
    this.dataset = panel ? {{panel}} : {{}};
    this.attributes = selected ? {{"aria-selected": "true"}} : {{}};
    this.classList = new ClassList();
    this.children = [];
    this.listeners = {{}};
    this.textContent = "";
    this.value = "";
    this.disabled = false;
    this.hidden = false;
    this.tabIndex = selected ? 0 : -1;
  }}
  addEventListener(name, callback) {{ this.listeners[name] = callback; }}
  append(...children) {{ this.children.push(...children); }}
  replaceChildren(...children) {{ this.children = [...children]; }}
  getAttribute(name) {{ return this.attributes[name]; }}
  setAttribute(name, value) {{ this.attributes[name] = value; }}
  focus() {{ this.focused = true; }}
}}
const ids = [
  "notice", "subjectSelect", "readOnlyTag", "balanceTitle", "myBalance",
  "myBankruptcyState", "myBankruptcyCount", "bankruptcyDescription",
  "bankruptcyButton", "checkInState", "checkInDescription", "checkInButton",
  "achievementDescription", "socialTitle", "socialDescription",
  "ledgerDescription", "ledgerList",
];
const elements = Object.fromEntries(ids.map((id) => [id, new Element(id)]));
elements.notice.classList.add("hidden");
elements.readOnlyTag.classList.add("hidden");
const tabs = [
  new Element("tab-checkin", "panel-checkin", true),
  new Element("tab-achievements", "panel-achievements"),
  new Element("tab-social", "panel-social"),
  new Element("tab-ledger", "panel-ledger"),
];
const panels = tabs.map((tab) => new Element(tab.dataset.panel));
const document = {{
  getElementById(id) {{ return elements[id]; }},
  createElement() {{ return new Element(); }},
  querySelectorAll(selector) {{ return selector.includes("tabpanel") ? panels : tabs; }},
}};
const window = {{location: {{pathname: "/chips"}}}};
const humanWallet = {{
  balance: 310, checked_in_today: false, bankruptcy_active: false,
  bankruptcy_badge: null, bankruptcy_count: 0, can_declare_bankruptcy: false,
}};
const machineWallet = {{
  balance: 50, checked_in_today: true, bankruptcy_active: true,
  bankruptcy_badge: {{name: "像素吃土中"}}, bankruptcy_count: 2,
  can_declare_bankruptcy: false,
}};
const payloads = {{
  "/api/chips": {{
    ok: true, human_name: "南山君", wallet: humanWallet,
    machines: [{{id: "ai-9", name: "clio_web"}}],
    ledger: [{{label: "人类流水", amount: 10, created_at: "2026-08-27T01:00:00", balance_after: 310}}],
  }},
  "/api/chips/machines/ai-9": {{
    ok: true, machine: {{id: "ai-9", name: "clio_web"}}, read_only: true,
    wallet: machineWallet,
    ledger: [{{label: "小机流水", amount: -20, created_at: "2026-08-27T02:00:00", balance_after: 50}}],
  }},
}};
const fetch = async (url) => ({{
  ok: true,
  status: 200,
  json: async () => payloads[url],
}});
{SCRIPT}
const flush = () => new Promise((resolve) => setImmediate(resolve));
await flush();
await flush();
assert.equal(elements.subjectSelect.value, "human");
assert.equal(elements.balanceTitle.textContent, "我的筹码");
assert.equal(elements.myBalance.textContent, "310");
assert.equal(elements.checkInState.textContent, "今日未签到");
assert.equal(elements.myBankruptcyCount.textContent, "破产 0 次");
assert.equal(elements.ledgerList.children[0].children[0].textContent, "人类流水");
assert.equal(elements.checkInButton.disabled, false);
assert.equal(elements.checkInButton.classList.contains("hidden"), false);

elements.subjectSelect.value = "ai:ai-9";
await elements.subjectSelect.listeners.change({{target: elements.subjectSelect}});
assert.equal(elements.balanceTitle.textContent, "clio_web 的筹码");
assert.equal(elements.myBalance.textContent, "50");
assert.equal(elements.checkInState.textContent, "今日已签到");
assert.equal(elements.myBankruptcyState.textContent, "像素吃土中");
assert.equal(elements.myBankruptcyCount.textContent, "破产 2 次");
assert.equal(elements.ledgerList.children[0].children[0].textContent, "小机流水");
assert.equal(elements.checkInDescription.textContent, "clio_web 的今日签到状态；只能由小机自己操作");
assert.equal(elements.bankruptcyDescription.textContent, "clio_web 的破产信息只读；人类不能代为宣布");
assert.equal(elements.achievementDescription.textContent, "clio_web 的对局成就与奖励正在筹备");
assert.equal(elements.socialTitle.textContent, "与 clio_web 的互动与借款");
assert.equal(elements.ledgerDescription.textContent, "clio_web 的统一账本 · 最近流水");
assert.equal(elements.readOnlyTag.classList.contains("hidden"), false);
assert.equal(elements.checkInButton.disabled, true);
assert.equal(elements.checkInButton.classList.contains("hidden"), true);
assert.equal(elements.bankruptcyButton.disabled, true);
assert.equal(elements.bankruptcyButton.classList.contains("hidden"), true);

elements.subjectSelect.value = "human";
await elements.subjectSelect.listeners.change({{target: elements.subjectSelect}});
assert.equal(elements.balanceTitle.textContent, "我的筹码");
assert.equal(elements.myBalance.textContent, "310");
assert.equal(elements.checkInState.textContent, "今日未签到");
assert.equal(elements.myBankruptcyCount.textContent, "破产 0 次");
assert.equal(elements.ledgerList.children[0].children[0].textContent, "人类流水");
assert.equal(elements.achievementDescription.textContent, "我的对局成就与奖励正在筹备");
assert.equal(elements.socialTitle.textContent, "互动与借款");
assert.equal(elements.socialDescription.textContent, "选择一只绑定小机后查看关系功能；规则筹备中");
assert.equal(elements.checkInButton.disabled, false);
assert.equal(elements.checkInButton.classList.contains("hidden"), false);
assert.equal(elements.bankruptcyButton.classList.contains("hidden"), false);
"""
        completed = subprocess.run(
            [NODE, "--input-type=module", "-e", harness],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(
            completed.returncode,
            0,
            f"JavaScript assertion failed:\n{completed.stderr}",
        )


if __name__ == "__main__":
    unittest.main()

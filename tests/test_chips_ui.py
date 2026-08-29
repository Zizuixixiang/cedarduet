import json
import os
import shutil
import subprocess
import unittest
from html.parser import HTMLParser
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "app" / "static" / "chips.html").read_text(encoding="utf-8")
SCRIPT = (ROOT / "app" / "static" / "chips.js").read_text(encoding="utf-8")
NODE = shutil.which("node")
JSDOM = bool(
    NODE
    and subprocess.run(
        [NODE, "-e", "require('jsdom')"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    ).returncode == 0
)


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

        self.assertEqual(len(tabs), 5)
        self.assertEqual(len(panels), 5)
        self.assertEqual(
            [tab["id"] for tab in tabs if tab.get("aria-selected") == "true"],
            ["tab-checkin"],
        )
        self.assertNotIn("hidden", self.attributes_for_id("panel-checkin"))
        for panel_id in (
            "panel-achievements", "panel-shop", "panel-loans", "panel-ledger"
        ):
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

    def test_achievement_panel_is_real_and_mobile_ready(self):
        self.assertIn('id="achievementSummary"', HTML)
        self.assertIn('id="achievementSections"', HTML)
        self.assertNotIn("成就与奖励正在筹备", HTML)
        achievement_panel = HTML[
            HTML.index('id="panel-achievements"'):HTML.index('id="panel-shop"')
        ]
        self.assertNotIn("即将开放", achievement_panel)
        self.assertIn("function renderAchievements(payload)", SCRIPT)
        styles = (ROOT / "app" / "static" / "chips.css").read_text(encoding="utf-8")
        self.assertIn(".achievement-item.locked", styles)
        self.assertIn(".achievement-item.unlocked", styles)
        self.assertIn("@media (max-width: 540px)", styles)

    def test_existing_chip_api_paths_are_unchanged(self):
        for path in (
            'requestJson("/api/chips")',
            'runHumanAction("/api/chips/check-in")',
            'runHumanAction("/api/chips/bankruptcy")',
            'requestJson(`/api/chips/machines/${encodeURIComponent(machineId)}`)',
        ):
            self.assertIn(path, SCRIPT)
        self.assertIn("fetch(apiPath(url)", SCRIPT)

    def test_shop_and_loan_are_separate_complete_modules(self):
        for element_id in (
            "loanCreateForm", "loanMachineSelect", "loanPrincipal", "loanRate",
            "loanDueDate", "loanCapEnabled", "loanCapWarning", "loanList",
        ):
            self.attributes_for_id(element_id)
        for element_id in (
            "exchangeCatalog", "exchangeCreateForm", "exchangeMachineSelect",
            "exchangeRequestNote", "exchangeAmount", "exchangeCustomTitle",
            "exchangePendingList", "exchangeWaitingList", "exchangeHistoryList",
        ):
            self.attributes_for_id(element_id)
        self.assertIn("互动商店", HTML)
        self.assertIn("申请方用自己承诺完成的小约定，向对方换筹码", HTML)
        self.assertIn("申请方请在常用聊天中完成约定", HTML)
        self.assertIn("发送换筹码申请", HTML)
        self.assertIn("希望对方支付的筹码数", HTML)
        self.assertNotIn("筹备中", HTML)
        self.assertNotIn("不支持人民币充值", HTML)
        self.assertIn('id="exchangeCreateForm" class="exchange-form hidden" autocomplete="off" novalidate', HTML)
        self.assertIn('id="loanCreateForm" class="loan-form" autocomplete="off" novalidate', HTML)
        self.assertIn('id="exchangeFormError"', HTML)
        self.assertIn('id="loanFormError"', HTML)
        self.assertIn("function renderLoans(loans)", SCRIPT)
        self.assertIn("function renderExchange(exchange)", SCRIPT)
        self.assertIn("function initCreateForms()", SCRIPT)
        self.assertIn("function validateExchangeCreateForm()", SCRIPT)
        self.assertIn("function validateLoanCreateForm()", SCRIPT)
        self.assertIn("daily_rate_micro_percent", SCRIPT)
        self.assertNotIn('reward.textContent = `+${achievement.reward}`;', SCRIPT.split("if (achievement.reward > 0)")[0])

    def test_exchange_has_exactly_three_compact_views_and_only_incoming_badge(self):
        exchange_buttons = [
            attrs for _, attrs in self.elements if "data-exchange-view" in attrs
        ]
        self.assertEqual(
            [attrs["data-exchange-view"] for attrs in exchange_buttons],
            ["exchange-view-shop", "exchange-view-requests", "exchange-view-history"],
        )
        self.assertEqual(HTML.count('class="exchange-badge'), 1)
        self.assertIn('id="exchangePendingBadge"', HTML)
        self.assertNotIn('id="exchangeWaitingBadge"', HTML)
        requests = HTML[
            HTML.index('id="exchange-view-requests"'):HTML.index('id="exchange-view-history"')
        ]
        self.assertLess(requests.index("待我确认"), requests.index("等待小机"))

    def test_exchange_cards_are_two_columns_and_mobile_one_column(self):
        styles = (ROOT / "app" / "static" / "chips.css").read_text(encoding="utf-8")
        self.assertIn(".exchange-product", styles)
        self.assertIn("grid-template-columns: 68px minmax(0, 1fr)", styles)
        self.assertIn(".exchange-catalog", styles)
        self.assertIn(".exchange-catalog, .exchange-form-grid", styles)
        self.assertIn(".exchange-art img", styles)
        self.assertIn("object-fit: contain", styles)
        self.assertIn(".exchange-art.has-image .exchange-art-fallback", styles)
        self.assertIn("/static/chips.css?v=1.2.1", HTML)
        self.assertIn("/static/chips.js?v=1.2.1", HTML)


@unittest.skipUnless(NODE, "node is required for frontend behavior tests")
class ChipCenterTabBehaviorTests(unittest.TestCase):
    def test_exchange_art_uses_image_path_and_keeps_symbol_fallback(self):
        renderer = function_source("renderExchangeArt")
        path_helper = function_source("apiPath")
        harness = f"""
const assert = require("node:assert/strict");
const window = {{location: {{pathname: "/chips"}}}};
class ClassList {{
  constructor() {{ this.values = new Set(); }}
  add(name) {{ this.values.add(name); }}
  remove(name) {{ this.values.delete(name); }}
  contains(name) {{ return this.values.has(name); }}
}}
class Element {{
  constructor(tag) {{
    this.tag = tag;
    this.children = [];
    this.listeners = {{}};
    this.classList = new ClassList();
    this.hidden = false;
    this.parent = null;
  }}
  append(...children) {{
    for (const child of children) child.parent = this;
    this.children.push(...children);
  }}
  addEventListener(name, callback) {{ this.listeners[name] = callback; }}
  remove() {{
    if (this.parent) this.parent.children = this.parent.children.filter((child) => child !== this);
  }}
}}
const document = {{createElement(tag) {{ return new Element(tag); }}}};
{path_helper}
{renderer}

const loaded = new Element("span");
renderExchangeArt(loaded, {{
  symbol: "☀",
  image_key: "/static/assets/exchange-shop/items/good_life.png?v=20260829",
}});
assert.equal(loaded.children[0].textContent, "☀");
assert.equal(loaded.children[1].src, "/static/assets/exchange-shop/items/good_life.png?v=20260829");
assert.equal(loaded.children[1].hidden, true);
loaded.children[1].listeners.load();
assert.equal(loaded.children[1].hidden, false);
assert.equal(loaded.classList.contains("has-image"), true);

const failed = new Element("span");
renderExchangeArt(failed, {{symbol: "♡", image_key: "/missing.png"}});
failed.children[1].listeners.error();
assert.equal(failed.children.length, 1);
assert.equal(failed.children[0].textContent, "♡");
assert.equal(failed.classList.contains("has-image"), false);

const placeholderOnly = new Element("span");
renderExchangeArt(placeholderOnly, {{symbol: "#"}});
assert.equal(placeholderOnly.children.length, 1);
assert.equal(placeholderOnly.children[0].textContent, "#");
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

    def test_utc_ledger_timestamp_is_rendered_in_browser_local_timezone(self):
        formatter = function_source("formatLedgerCreatedAt")
        harness = f"""
const assert = require("node:assert/strict");
{formatter}
assert.equal(
  formatLedgerCreatedAt("2026-08-27T10:59:00+00:00"),
  "2026/08/27 18:59"
);
"""
        completed = subprocess.run(
            [NODE, "-e", harness],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
            env={**os.environ, "TZ": "Asia/Shanghai"},
        )
        self.assertEqual(
            completed.returncode,
            0,
            f"JavaScript assertion failed:\n{completed.stderr}",
        )
        self.assertNotIn('created_at.replace("T", " ")', SCRIPT)

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
    this.style = {{}};
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
  new Element("tab-shop", "panel-shop"),
  new Element("tab-loans", "panel-loans"),
  new Element("tab-ledger", "panel-ledger"),
];
const panels = tabs.map((tab) => new Element(tab.dataset.panel));
const document = {{
  querySelectorAll(selector) {{ return selector.includes("tabpanel") ? panels : tabs; }},
}};
{functions}
initModuleTabs();
assert.deepEqual(panels.map((panel) => panel.hidden), [false, true, true, true, true]);
tabs[2].listeners.click();
assert.deepEqual(tabs.map((tab) => tab.attributes["aria-selected"]), ["false", "false", "true", "false", "false"]);
assert.deepEqual(panels.map((panel) => panel.hidden), [true, true, false, true, true]);
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
    this.style = {{}};
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
  "achievementDescription", "achievementSummary", "achievementSections",
  "exchangeTitle", "exchangeDescription", "exchangeCatalog",
  "exchangeCreateForm", "exchangeMachineSelect", "exchangeItemKey",
  "exchangeFormSymbol", "exchangeFormTitle", "exchangeFormDescription",
  "exchangeCustomTitleWrap", "exchangeCustomTitle", "exchangeRequestNote",
  "exchangeAmount", "exchangeCreateButton", "exchangeCancelButton",
  "exchangePendingCount", "exchangeWaitingCount", "exchangeHistoryCount",
  "exchangePendingBadge", "exchangePendingList", "exchangeWaitingList",
  "exchangeHistoryList", "exchange-view-shop", "exchange-view-requests",
  "exchange-view-history",
  "socialTitle", "socialDescription", "loanCreateForm", "loanMachineSelect",
  "loanPrincipal", "loanRate", "loanDueDate", "loanCapEnabled",
  "loanCapWarning", "loanCreateButton", "loanCount", "loanList",
  "ledgerDescription", "ledgerList",
];
const elements = Object.fromEntries(ids.map((id) => [id, new Element(id)]));
elements.notice.classList.add("hidden");
elements.readOnlyTag.classList.add("hidden");
const tabs = [
  new Element("tab-checkin", "panel-checkin", true),
  new Element("tab-achievements", "panel-achievements"),
  new Element("tab-shop", "panel-shop"),
  new Element("tab-loans", "panel-loans"),
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
    achievements: {{summary: {{unlocked: 1, total: 25, hidden_unlocked: 0}}, sections: [
      {{id: "human", name: "人类专属", items: [{{id: "h1", name: "人类成就", condition: "条件", reward: 5, progress: {{current: 1, target: 1}}, unlocked: true, unlocked_at: "2026-08-27T01:00:00+00:00"}}]}},
    ]}},
    loans: [],
    exchange: {{catalog: [], pending_for_me: [], waiting_for_other: [], history: []}},
    ledger: [{{label: "人类流水", amount: 10, created_at: "2026-08-27T01:00:00+00:00", balance_after: 310}}],
  }},
  "/api/chips/machines/ai-9": {{
    ok: true, machine: {{id: "ai-9", name: "clio_web"}}, read_only: true,
    wallet: machineWallet,
    achievements: {{summary: {{unlocked: 0, total: 36, hidden_unlocked: 0}}, sections: [
      {{id: "relationship", name: "你们之间", items: [{{id: "pair", name: "来都来了", condition: "完成一局", reward: 5, progress: {{current: 0, target: 1}}, unlocked: false}}]}},
    ]}},
    loans: [],
    exchange: {{catalog: [], pending_for_me: [], waiting_for_other: [], history: []}},
    ledger: [{{label: "小机流水", amount: -20, created_at: "2026-08-27T02:00:00+00:00", balance_after: 50}}],
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
assert.equal(elements.achievementSummary.textContent, "1 / 25");
assert.equal(elements.achievementSections.children[0].children[0].textContent, "人类专属");
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
assert.equal(elements.achievementSummary.textContent, "0 / 36");
assert.equal(elements.achievementSections.children[0].children[0].textContent, "你们之间");
assert.equal(elements.checkInDescription.textContent, "clio_web 的今日签到状态；只能由小机自己操作");
assert.equal(elements.bankruptcyDescription.textContent, "clio_web 的破产信息只读；人类不能代为宣布");
assert.equal(elements.achievementDescription.textContent, "clio_web 的永久成就；含你们之间的配对进度");
assert.equal(elements.exchangeTitle.textContent, "与 clio_web 的互动商店");
assert.equal(elements.socialTitle.textContent, "与 clio_web 的欠条");
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
assert.equal(elements.achievementDescription.textContent, "我的永久成就；奖励在解锁时自动到账");
assert.equal(elements.exchangeTitle.textContent, "互动商店");
assert.equal(elements.socialTitle.textContent, "欠条");
assert.equal(elements.socialDescription.textContent, "借款提案、协商与还款");
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


@unittest.skipUnless(NODE and JSDOM, "node and jsdom are required for DOM form tests")
class ChipCenterDomFormTests(unittest.TestCase):
    def run_dom(self, assertions: str, pathname: str = "/duel/chips") -> None:
        harness = f"""
const assert = require("node:assert/strict");
const {{JSDOM}} = require("jsdom");

async function main() {{
  const dom = new JSDOM({json.dumps(HTML)}, {{
    url: `https://duel.test{pathname}`,
    runScripts: "outside-only",
  }});
  const {{window}} = dom;
  const document = window.document;
  const catalog = [
    {{
      key: "good_life", title: "今天有好好生活",
      description: "分享一件今天认真生活的小事。",
      image_key: "/static/assets/exchange-shop/items/good_life.png?v=20260829",
      symbol: "☀",
    }},
    {{
      key: "custom", title: "自定义约定",
      description: "写下你们都看得懂的小约定。",
      image_key: "/static/assets/exchange-shop/items/custom.png?v=20260829",
      symbol: "+",
    }},
  ];
  const wallet = {{
    balance: 200, checked_in_today: false, bankruptcy_active: false,
    bankruptcy_badge: null, bankruptcy_count: 0, can_declare_bankruptcy: false,
  }};
  const exchange = {{
    catalog, pending_for_me: [], waiting_for_other: [], history: [],
  }};
  const basePayload = {{
    ok: true, human_name: "测试人类", wallet,
    machines: [{{id: "ai-1", name: "测试小机"}}], ledger: [], loans: [], exchange,
    achievements: {{summary: {{unlocked: 0, total: 0, hidden_unlocked: 0}}, sections: []}},
  }};
  const calls = [];
  const response = (payload) => ({{
    ok: true,
    status: 200,
    json: async () => JSON.parse(JSON.stringify(payload)),
  }});
  window.fetch = async (url, options = {{}}) => {{
    calls.push({{url: String(url), options}});
    if ((options.method || "GET") === "POST") {{
      return response({{
        ...basePayload,
        message: String(url).endsWith("/loans") ? "借款提案已发给小机" : "兑换申请已发给小机",
      }});
    }}
    return response(basePayload);
  }};
  window.eval({json.dumps(SCRIPT)});
  const waitFor = async (predicate) => {{
    for (let attempt = 0; attempt < 80; attempt += 1) {{
      if (predicate()) return;
      await new Promise((resolve) => setTimeout(resolve, 0));
    }}
    throw new Error("timed out waiting for DOM state");
  }};
  await waitFor(() => document.querySelector('[data-item-key="good_life"]'));
  {assertions}
}}

main().catch((error) => {{
  console.error(error);
  process.exitCode = 1;
}});
"""
        completed = subprocess.run(
            [NODE, "-e", harness],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
            env={**os.environ, "TZ": "Asia/Shanghai"},
        )
        self.assertEqual(
            completed.returncode,
            0,
            f"DOM JavaScript assertion failed:\n{completed.stderr}",
        )

    def test_valid_exchange_form_requests_existing_post_endpoint(self):
        self.run_dom(r"""
  document.querySelector('[data-item-key="good_life"]').click();
  document.getElementById("exchangeAmount").value = "12";
  document.getElementById("exchangeRequestNote").value = "我会发一张今天窗外的照片";
  const form = document.getElementById("exchangeCreateForm");
  form.requestSubmit(document.getElementById("exchangeCreateButton"));
  await waitFor(() => calls.some((call) => call.url.endsWith("/api/chips/exchanges") && call.options.method === "POST"));
  const call = calls.find((entry) => entry.url.endsWith("/api/chips/exchanges") && entry.options.method === "POST");
  assert.equal(call.url, "/duel/api/chips/exchanges");
  assert.deepEqual(
    Object.fromEntries(Object.entries(JSON.parse(call.options.body)).filter(([key]) => key !== "idempotency_key")),
    {
      machine_id: "ai-1", item_key: "good_life",
      request_note: "我会发一张今天窗外的照片", chip_amount: 12, custom_title: null,
    },
  );
""")

    def test_valid_loan_form_requests_existing_post_endpoint(self):
        self.run_dom(r"""
  document.getElementById("loanPrincipal").value = "30";
  document.getElementById("loanRate").value = "0.125";
  const form = document.getElementById("loanCreateForm");
  form.requestSubmit(document.getElementById("loanCreateButton"));
  await waitFor(() => calls.some((call) => call.url.endsWith("/api/chips/loans") && call.options.method === "POST"));
  const call = calls.find((entry) => entry.url.endsWith("/api/chips/loans") && entry.options.method === "POST");
  assert.equal(call.url, "/duel/api/chips/loans");
  const body = JSON.parse(call.options.body);
  assert.equal(body.machine_id, "ai-1");
  assert.equal(body.principal, 30);
  assert.equal(body.daily_rate_micro_percent, 125000);
  assert.equal(body.interest_cap_enabled, true);
  assert.match(body.due_date, /^\d{4}-\d{2}-\d{2}$/);
""")

    def test_invalid_forms_show_visible_chinese_errors_without_post(self):
        self.run_dom(r"""
  document.querySelector('[data-item-key="custom"]').click();
  document.getElementById("exchangeAmount").value = "5";
  document.getElementById("exchangeRequestNote").value = "我会完成这个约定";
  document.getElementById("exchangeCreateForm").requestSubmit(document.getElementById("exchangeCreateButton"));
  await new Promise((resolve) => setTimeout(resolve, 0));
  const exchangeError = document.getElementById("exchangeFormError");
  assert.equal(exchangeError.classList.contains("hidden"), false);
  assert.match(exchangeError.textContent, /自定义小约定标题需为 1-30 字/);
  assert.equal(calls.filter((call) => call.options.method === "POST").length, 0);

  document.getElementById("loanPrincipal").value = "0";
  document.getElementById("loanCreateForm").requestSubmit(document.getElementById("loanCreateButton"));
  await new Promise((resolve) => setTimeout(resolve, 0));
  const loanError = document.getElementById("loanFormError");
  assert.equal(loanError.classList.contains("hidden"), false);
  assert.match(loanError.textContent, /本金必须是正整数/);
  assert.equal(calls.filter((call) => call.options.method === "POST").length, 0);
""")

    def test_exchange_images_support_root_and_duel_prefixes(self):
        for pathname, expected in (
            ("/chips", "/static/assets/exchange-shop/items/good_life.png?v=20260829"),
            ("/duel/chips", "/duel/static/assets/exchange-shop/items/good_life.png?v=20260829"),
        ):
            with self.subTest(pathname=pathname):
                self.run_dom(
                    f"""
  const image = document.querySelector('[data-item-key="good_life"] img');
  assert.equal(image.getAttribute("src"), {json.dumps(expected)});
""",
                    pathname=pathname,
                )


if __name__ == "__main__":
    unittest.main()

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

    def test_achievement_cards_scroll_below_sticky_group_headings(self):
        styles = (ROOT / "app" / "static" / "chips.css").read_text(encoding="utf-8")
        section_styles = styles[
            styles.index(".achievement-sections {"):
            styles.index(".achievement-group h3 {")
        ]
        heading_styles = styles[
            styles.index(".achievement-group h3 {"):
            styles.index(".achievement-grid {")
        ]
        self.assertIn("max-height: clamp(20rem, 56vh, 40rem)", section_styles)
        self.assertIn("overflow-y: auto", section_styles)
        self.assertIn("overscroll-behavior: contain", section_styles)
        self.assertIn("scrollbar-width: thin", section_styles)
        self.assertIn("scrollbar-color: var(--lilac) transparent", section_styles)
        self.assertIn("-webkit-overflow-scrolling: touch", section_styles)
        self.assertIn("position: sticky", heading_styles)
        self.assertIn("top: 0", heading_styles)
        achievement_panel = HTML[
            HTML.index('id="panel-achievements"'):HTML.index('id="panel-shop"')
        ]
        self.assertLess(
            achievement_panel.index('id="achievementSummary"'),
            achievement_panel.index('id="achievementSections"'),
        )

    def test_achievement_description_is_short_and_single_line(self):
        styles = (ROOT / "app" / "static" / "chips.css").read_text(encoding="utf-8")
        description_styles = styles[
            styles.index(".achievements-card .module-heading p {"):
            styles.index(".achievement-sections {")
        ]
        self.assertIn(
            '<p id="achievementDescription">永久成就 · 解锁即到账</p>',
            HTML,
        )
        self.assertIn(
            '$("achievementDescription").textContent = "永久成就 · 解锁即到账"',
            SCRIPT,
        )
        self.assertNotIn("我的永久成就；奖励在解锁时自动到账", SCRIPT)
        self.assertIn("white-space: nowrap", description_styles)

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
            "loanViewCreateButton", "loanViewListButton", "loanViewCreate",
            "loanViewList", "loanCount",
        ):
            self.attributes_for_id(element_id)
        for element_id in (
            "exchangeCatalog", "exchangeCreateForm", "exchangeMachineSelect",
            "exchangeRequestNote", "exchangeAmount", "exchangeCustomTitle",
            "exchangePendingList", "exchangeWaitingList", "exchangeHistoryList",
        ):
            self.attributes_for_id(element_id)
        self.assertIn("互动商店", HTML)
        self.assertIn("先完成约定，再由对方确认并支付筹码", HTML)
        self.assertIn("双弈不保存互动内容，也不介入履约争议。", HTML)
        self.assertIn("1–100 枚 · 最多 3 张 · 每日支付上限 100 · 72 小时有效", HTML)
        self.assertNotIn("申请方完成约定并收取筹码，审批方确认后支付筹码", HTML)
        self.assertNotIn("先由申请方在常用聊天中完成约定，再由对方确认并支付筹码", HTML)
        self.assertNotIn("单次 1–100 · 每对最多 3 张待处理", HTML)
        self.assertEqual(HTML.count('class="exchange-limits"'), 1)
        self.assertIn("发送申请", HTML)
        self.assertIn("希望对方支付的筹码数", HTML)
        self.assertIn("本次说明：你会如何完成约定", HTML)
        self.assertNotIn("人类履约换筹码 · 小机付款", SCRIPT)
        self.assertNotIn("小机履约换筹码 · 人类付款", SCRIPT)
        self.assertNotIn("72 小时有效", SCRIPT)
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

    def test_loan_module_has_exactly_two_flat_views(self):
        loan_buttons = [
            attrs for _, attrs in self.elements if "data-loan-view" in attrs
        ]
        self.assertEqual(
            [attrs["data-loan-view"] for attrs in loan_buttons],
            ["create", "list"],
        )
        self.assertEqual(
            [attrs["aria-pressed"] for attrs in loan_buttons],
            ["true", "false"],
        )
        self.assertNotIn("hidden", self.attributes_for_id("loanViewCreate"))
        self.assertIn("hidden", self.attributes_for_id("loanViewList"))
        self.assertNotIn("欠条与协商", HTML)
        self.assertNotIn("我要借款", HTML)
        self.assertEqual(HTML.count("计息说明"), 1)

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
        self.assertLess(requests.index("待我确认付款"), requests.index("等待小机确认付款"))
        self.assertIn("没有需要你确认付款的申请", SCRIPT)
        self.assertIn("没有等待小机确认付款的申请", SCRIPT)

    def test_exchange_cards_are_two_columns_and_mobile_one_column(self):
        styles = (ROOT / "app" / "static" / "chips.css").read_text(encoding="utf-8")
        self.assertIn(".exchange-product", styles)
        self.assertIn("grid-template-columns: 68px minmax(0, 1fr)", styles)
        self.assertIn(".exchange-catalog", styles)
        self.assertIn(".exchange-catalog, .exchange-form-grid", styles)
        self.assertIn(".exchange-art img", styles)
        self.assertIn("object-fit: contain", styles)
        self.assertIn("opacity: 0", styles)
        self.assertIn(".exchange-art.has-image img { opacity: 1; }", styles)
        self.assertIn(".exchange-art.has-image .exchange-art-fallback", styles)
        self.assertIn("/static/chips.css?v=1.3.0", HTML)
        self.assertIn("/static/chips.js?v=1.3.0", HTML)
        self.assertIn(".exchange-request-summary", styles)
        self.assertIn(".exchange-request-detail", styles)
        self.assertIn(".exchange-request-time", styles)
        self.assertIn(".exchange-request-art", styles)
        self.assertIn(".exchange-request-content", styles)
        request_renderer = function_source("renderExchangeRequest")
        self.assertIn('art.className = "exchange-art exchange-request-art"', request_renderer)
        self.assertIn("renderExchangeArt(art, item.item)", request_renderer)
        form_actions = styles[
            styles.index(".exchange-form-actions {"):
            styles.index(".exchange-request-group +")
        ]
        request_actions = styles[
            styles.index(".exchange-actions {"):
            styles.index(".loan-form {")
        ]
        self.assertIn("flex-wrap: nowrap", form_actions)
        self.assertIn("white-space: nowrap", form_actions)
        self.assertIn("flex-wrap: nowrap", request_actions)
        self.assertIn("white-space: nowrap", request_actions)

    def test_ledger_list_scrolls_inside_its_panel(self):
        styles = (ROOT / "app" / "static" / "chips.css").read_text(encoding="utf-8")
        ledger_styles = styles[
            styles.index(".ledger-list {"):
            styles.index(".ledger-list li {")
        ]
        self.assertIn("max-height: clamp(18rem, 52vh, 38rem)", ledger_styles)
        self.assertIn("overflow-y: auto", ledger_styles)
        self.assertIn("overscroll-behavior: contain", ledger_styles)
        self.assertIn("scrollbar-width: thin", ledger_styles)
        self.assertIn("scrollbar-color: var(--lilac) transparent", ledger_styles)
        ledger_panel = HTML[HTML.index('id="panel-ledger"'):]
        self.assertLess(ledger_panel.index("<h2>筹码流水</h2>"), ledger_panel.index('id="ledgerList"'))
        self.assertIn('</div>\n          <ol id="ledgerList"', ledger_panel)



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
assert.equal(loaded.children[1].loading, "lazy");
assert.equal(loaded.children[1].hidden, false);
assert.equal(loaded.classList.contains("has-image"), false);
loaded.children[1].listeners.load();
assert.equal(loaded.children[1].hidden, false);
assert.equal(loaded.classList.contains("has-image"), true);

window.location.pathname = "/duel/chips";
const prefixed = new Element("span");
renderExchangeArt(prefixed, {{
  symbol: "☀",
  image_key: "/static/assets/exchange-shop/items/good_life.png?v=20260829",
}});
assert.equal(
  prefixed.children[1].src,
  "/duel/static/assets/exchange-shop/items/good_life.png?v=20260829",
);

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

    def test_exchange_request_renderer_reuses_exchange_art(self):
        functions = "\n".join(
            (
                function_source("apiPath"),
                function_source("renderExchangeArt"),
                function_source("renderExchangeRequest"),
            )
        )
        harness = f"""
const assert = require("node:assert/strict");
const window = {{location: {{pathname: "/duel/chips"}}}};
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
    this.parent = null;
  }}
  append(...children) {{
    for (const child of children) {{
      if (child && typeof child === "object") child.parent = this;
      this.children.push(child);
    }}
  }}
  addEventListener(name, callback) {{ this.listeners[name] = callback; }}
  setAttribute(name, value) {{ this[name] = value; }}
  remove() {{
    if (this.parent) this.parent.children = this.parent.children.filter((child) => child !== this);
  }}
}}
const document = {{
  createElement(tag) {{ return new Element(tag); }},
  createTextNode(text) {{ return {{textContent: text}}; }},
}};
const exchangeStatusLabels = {{pending: "待确认"}};
const machineName = () => "Sirius";
const formatLedgerCreatedAt = (value) => value;
const runExchangeAction = async () => {{}};
{functions}

const card = renderExchangeRequest({{
  status: "pending", ai_id: "ai-1", machine_name: "Sirius",
  chip_amount: 12, display_title: "亲亲赎回",
  initiator: {{type: "human", id: "human-1"}},
  item: {{
    description: "先完成一个亲亲约定。", symbol: "♥",
    image_key: "/static/assets/exchange-shop/items/kiss.png?v=20260829",
  }},
  request_note: "今晚在聊天里完成",
  expires_at: "2026-09-01T02:00:00+00:00",
  allowed_actions: [],
}});
assert.equal(card.children.length, 2);
const art = card.children[0];
assert.equal(art.className, "exchange-art exchange-request-art");
assert.equal(art["aria-hidden"], "true");
assert.equal(art.children[0].textContent, "♥");
assert.equal(
  art.children[1].src,
  "/duel/static/assets/exchange-shop/items/kiss.png?v=20260829",
);
assert.equal(card.children[1].className, "exchange-request-content");
art.children[1].listeners.error();
assert.equal(art.children.length, 1);
assert.equal(art.children[0].textContent, "♥");
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
  "loanViewCreateButton", "loanViewListButton", "loanViewCreate", "loanViewList",
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
assert.equal(elements.achievementDescription.textContent, "永久成就 · 解锁即到账");
assert.equal(elements.exchangeTitle.textContent, "与 clio_web 的互动商店");
assert.equal(elements.exchangeDescription.textContent, "先完成约定，再由对方确认并支付筹码");
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
assert.equal(elements.achievementDescription.textContent, "永久成就 · 解锁即到账");
assert.equal(elements.exchangeTitle.textContent, "互动商店");
assert.equal(elements.exchangeDescription.textContent, "先完成约定，再由对方确认并支付筹码");
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
    @staticmethod
    def loan_fixture(status: str, **overrides) -> dict:
        fixture = {
            "loan_id": f"loan-{status}",
            "status": status,
            "direction": "borrowing",
            "borrower": {"type": "human", "id": "human-1"},
            "lender": {"type": "ai", "id": "ai-1"},
            "human_id": "human-1",
            "ai_id": "ai-1",
            "counterparty_id": "ai-1",
            "revision": 1,
            "accepted_revision": None,
            "counter_count": 0,
            "awaiting": None,
            "principal": 10,
            "remaining_principal": 10,
            "daily_rate_micro_percent": 125000,
            "daily_rate_percent": "0.125",
            "accrued_interest": 0,
            "interest_paid": 0,
            "lifetime_interest": 0,
            "principal_paid": 0,
            "total_repaid": 0,
            "total_due": 10,
            "due_date": "2026-09-05",
            "overdue_days": 0,
            "interest_cap_enabled": True,
            "interest_cap_amount": 10,
            "interest_cap_reached": False,
            "proposal_expires_at": None,
            "accepted_at": None,
            "repaid_at": None,
            "created_at": "2026-08-29T02:00:00+00:00",
            "updated_at": "2026-08-29T03:00:00+00:00",
            "allowed_actions": [],
        }
        fixture.update(overrides)
        return fixture

    def run_dom(
        self,
        assertions: str,
        pathname: str = "/duel/chips",
        loans: list[dict] | None = None,
    ) -> None:
        loan_payload = json.dumps(loans or [], ensure_ascii=False)
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
    {{
      key: "kiss", title: "亲亲赎回",
      description: "在常用聊天里给对方一个只属于你们的亲亲。",
      image_key: "/static/assets/exchange-shop/items/kiss.png?v=20260829",
      symbol: "♥",
    }},
  ];
  const wallet = {{
    balance: 200, checked_in_today: false, bankruptcy_active: false,
    bankruptcy_badge: null, bankruptcy_count: 0, can_declare_bankruptcy: false,
  }};
  const requestBase = {{
    request_id: "ex-test", status: "pending", ai_id: "ai-1",
    request_note: "今晚在聊天里完成", chip_amount: 100,
    expires_at: "2026-09-01T02:00:00+00:00",
    created_at: "2026-08-29T02:00:00+00:00", machine_name: "Sirius",
  }};
  const exchange = {{
    catalog,
    pending_for_me: [{{
      ...requestBase, request_id: "ex-machine", display_title: "赛博小礼物",
      item: {{
        key: "cyber_gift", description: "送上一份赛博小礼物。",
        image_key: "/static/assets/exchange-shop/items/cyber_gift.png?v=20260829",
        symbol: "✦",
      }},
      initiator: {{type: "ai", id: "ai-1"}}, allowed_actions: ["confirm", "reject"],
    }}],
    waiting_for_other: [{{
      ...requestBase, request_id: "ex-human", display_title: "亲亲赎回",
      item: {{
        key: "kiss", description: "先完成一个亲亲约定。",
        image_key: "/static/assets/exchange-shop/items/kiss.png?v=20260829",
        symbol: "♥",
      }},
      initiator: {{type: "human", id: "human-1"}}, allowed_actions: ["withdraw"],
    }}],
    history: [{{
      ...requestBase, request_id: "ex-completed", status: "completed",
      display_title: "今天有好好生活",
      item: {{
        key: "good_life", description: "分享一件今天认真生活的小事。",
        image_key: "/static/assets/exchange-shop/items/good_life.png?v=20260829",
        symbol: "☀",
      }},
      initiator: {{type: "human", id: "human-1"}}, allowed_actions: [],
    }}],
  }};
  const basePayload = {{
    ok: true, human_name: "测试人类", wallet,
    machines: [{{id: "ai-1", name: "Sirius"}}, {{id: "ai-2", name: "Nova"}}],
    ledger: [], loans: {loan_payload}, exchange,

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

    def test_exchange_form_summary_tracks_item_machine_and_amount(self):
        self.run_dom(r"""
  document.querySelector('[data-item-key="kiss"]').click();
  const amount = document.getElementById("exchangeAmount");
  amount.value = "100";
  amount.dispatchEvent(new window.Event("input", {bubbles: true}));
  assert.equal(
    document.getElementById("exchangeFormTitle").textContent,
    "你用「亲亲赎回」向 Sirius 换 100 筹码",
  );
  assert.match(
    document.getElementById("exchangeFormDescription").textContent,
    /先在常用聊天中完成约定，再由 Sirius 确认并支付筹码；确认后筹码由你收取/,
  );
  const machine = document.getElementById("exchangeMachineSelect");
  machine.value = "ai-2";
  machine.dispatchEvent(new window.Event("change", {bubbles: true}));
  assert.equal(
    document.getElementById("exchangeFormTitle").textContent,
    "你用「亲亲赎回」向 Nova 换 100 筹码",
  );
""")

    def test_exchange_request_cards_use_relative_sentences_and_compact_layers(self):
        self.run_dom(r"""
  const humanCard = document.querySelector("#exchangeWaitingList .exchange-request");
  assert.equal(
    humanCard.querySelector(".exchange-request-summary").textContent,
    "你用「亲亲赎回」向 Sirius 换 100 筹码",
  );
  assert.equal(humanCard.querySelector(".exchange-request-machine").textContent, "Sirius");
  assert.equal(humanCard.querySelector(".exchange-request-amount").textContent, "100 筹码");
  assert.deepEqual(
    [...humanCard.querySelectorAll(".exchange-request-label")].map((node) => node.textContent),
    ["约定", "本次说明"],
  );
  assert.match(humanCard.querySelector(".exchange-request-time").textContent, /^有效期至 /);

  const machineCard = document.querySelector("#exchangePendingList .exchange-request");
  assert.equal(
    machineCard.querySelector(".exchange-request-summary").textContent,
    "Sirius 用「赛博小礼物」向你换 100 筹码",
  );
  assert.equal(
    machineCard.querySelector(".exchange-actions .primary").textContent,
    "确认并支付筹码",
  );
""")

    def test_exchange_request_cards_render_shared_art_for_every_list(self):
        self.run_dom(r"""
  const cards = [
    document.querySelector("#exchangePendingList .exchange-request"),
    document.querySelector("#exchangeWaitingList .exchange-request"),
    document.querySelector("#exchangeHistoryList .exchange-request"),
  ];
  assert.ok(cards.every((card) => card.querySelector(".exchange-request-art img")));
  assert.ok(cards.every((card) => card.querySelector(".exchange-art-fallback")));

  const loadedArt = cards[0].querySelector(".exchange-request-art");
  loadedArt.querySelector("img").dispatchEvent(new window.Event("load"));
  assert.equal(loadedArt.classList.contains("has-image"), true);

  const failedArt = cards[1].querySelector(".exchange-request-art");
  failedArt.querySelector("img").dispatchEvent(new window.Event("error"));
  assert.equal(failedArt.querySelector("img"), null);
  assert.equal(failedArt.querySelector(".exchange-art-fallback").textContent, "♥");
  assert.equal(failedArt.classList.contains("has-image"), false);
""")

    def test_valid_loan_form_requests_existing_post_endpoint(self):
        self.run_dom(r"""
  assert.equal(document.getElementById("loanViewCreate").hidden, false);
  assert.equal(document.getElementById("loanViewList").hidden, true);
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

    def test_loan_views_default_to_records_and_preserve_manual_choice(self):
        proposal = self.loan_fixture(
            "negotiating",
            awaiting={"type": "ai", "id": "ai-1", "you": False},
            proposal_expires_at="2026-09-01T02:00:00+00:00",
            allowed_actions=["withdraw"],
        )
        self.run_dom(r"""
  const createView = document.getElementById("loanViewCreate");
  const listView = document.getElementById("loanViewList");
  const createButton = document.getElementById("loanViewCreateButton");
  const listButton = document.getElementById("loanViewListButton");
  assert.equal(createView.hidden, true);
  assert.equal(listView.hidden, false);
  assert.equal(createButton.getAttribute("aria-pressed"), "false");
  assert.equal(listButton.getAttribute("aria-pressed"), "true");
  assert.equal(document.getElementById("loanCount").textContent, "1");

  createButton.click();
  assert.equal(createView.hidden, false);
  assert.equal(listView.hidden, true);
  document.getElementById("loanPrincipal").value = "20";
  document.getElementById("loanCreateForm").requestSubmit(document.getElementById("loanCreateButton"));
  await waitFor(() => calls.some((call) => call.url.endsWith("/api/chips/loans") && call.options.method === "POST"));
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.equal(createView.hidden, false);
  assert.equal(listView.hidden, true);

  listButton.click();
  assert.equal(createView.hidden, true);
  assert.equal(listView.hidden, false);
""", loans=[proposal])

    def test_loan_cards_are_status_specific_and_keep_actions(self):
        proposal = self.loan_fixture(
            "negotiating",
            loan_id="loan-secret-id",
            revision=2,
            counter_count=1,
            awaiting={"type": "human", "id": "human-1", "you": True},
            proposal_expires_at="2026-09-01T02:00:00+00:00",
            allowed_actions=["reject", "accept", "counter", "withdraw"],
        )
        active = self.loan_fixture(
            "active",
            loan_id="loan-active-secret",
            revision=2,
            accepted_revision=2,
            counter_count=1,
            remaining_principal=7,
            accrued_interest=2,
            interest_paid=1,
            principal_paid=3,
            total_repaid=4,
            total_due=999,
            allowed_actions=["repay"],
        )
        repaid = self.loan_fixture(
            "repaid",
            remaining_principal=0,
            principal_paid=10,
            interest_paid=2,
            total_repaid=12,
            total_due=0,
            repaid_at="2026-08-30T04:00:00+00:00",
        )
        rejected = self.loan_fixture("rejected")
        self.run_dom(r"""
  const proposal = document.querySelector('[data-loan-status="negotiating"]');
  assert.match(proposal.querySelector("h4").textContent, /向 Sirius 借 10/);
  assert.match(proposal.textContent, /等待你回应/);
  assert.match(proposal.textContent, /第 2 版方案 · 已改条件 1 次/);
  assert.match(proposal.textContent, /申请本金/);
  assert.match(proposal.textContent, /提案失效/);
  assert.doesNotMatch(proposal.textContent, /剩余本金|当前利息|累计利息|已还总额|当前应还|已还次数/);
  assert.doesNotMatch(proposal.textContent, /loan-secret-id/);
  assert.deepEqual(
    Array.from(proposal.querySelectorAll(".loan-actions button"), (button) => button.textContent),
    ["拒绝", "接受", "改条件", "撤销"],
  );

  const active = document.querySelector('[data-loan-status="active"]');
  assert.equal(active.querySelector(".loan-current-due strong").textContent, "9");
  assert.deepEqual(
    Array.from(active.querySelectorAll(".loan-debt-split > div"), (item) => item.textContent),
    ["剩余本金7", "当前利息2"],
  );
  assert.match(active.textContent, /日利率0.125%/);
  assert.match(active.textContent, /利息封顶已开启 · 上限 10/);
  assert.match(active.textContent, /已还总额4/);
  assert.doesNotMatch(active.textContent, /loan-active-secret/);
  assert.deepEqual(
    Array.from(active.querySelectorAll(".loan-actions button"), (button) => button.textContent),
    ["还款"],
  );
  active.querySelector(".loan-actions button").click();
  const repayForm = active.querySelector(".loan-repay-form");
  assert.equal(repayForm.querySelector("input").max, "9");
  repayForm.querySelector("input").value = "3";
  repayForm.requestSubmit(repayForm.querySelector("button"));
  await waitFor(() => calls.some((call) => call.url.endsWith("/api/chips/loans/loan-active-secret/repay")));
  const repayCall = calls.find((call) => call.url.endsWith("/api/chips/loans/loan-active-secret/repay"));
  assert.equal(JSON.parse(repayCall.options.body).amount, 3);

  proposal.querySelectorAll(".loan-actions button")[1].click();
  await waitFor(() => calls.some((call) => call.url.endsWith("/api/chips/loans/loan-secret-id/accept")));
  const acceptCall = calls.find((call) => call.url.endsWith("/api/chips/loans/loan-secret-id/accept"));
  assert.equal(JSON.parse(acceptCall.options.body).revision, 2);

  const repaid = document.querySelector('[data-loan-status="repaid"]');
  assert.match(repaid.textContent, /已还清/);
  assert.match(repaid.textContent, /实际已还总额 12 枚/);
  const rejected = document.querySelector('[data-loan-status="rejected"]');
  assert.match(rejected.textContent, /已拒绝/);
  assert.match(rejected.textContent, /申请于/);
  assert.doesNotMatch(rejected.textContent, /剩余本金|当前利息|当前应还/);
""", loans=[proposal, active, repaid, rejected])

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
        for pathname, catalog_expected, request_expected in (
            (
                "/chips",
                "/static/assets/exchange-shop/items/good_life.png?v=20260829",
                "/static/assets/exchange-shop/items/kiss.png?v=20260829",
            ),
            (
                "/duel/chips",
                "/duel/static/assets/exchange-shop/items/good_life.png?v=20260829",
                "/duel/static/assets/exchange-shop/items/kiss.png?v=20260829",
            ),
        ):
            with self.subTest(pathname=pathname):
                self.run_dom(
                    f"""
  const catalogImage = document.querySelector('[data-item-key="good_life"] img');
  assert.equal(catalogImage.getAttribute("src"), {json.dumps(catalog_expected)});
  const requestImage = document.querySelector('#exchangeWaitingList .exchange-request-art img');
  assert.equal(requestImage.getAttribute("src"), {json.dumps(request_expected)});
""",
                    pathname=pathname,
                )


if __name__ == "__main__":
    unittest.main()

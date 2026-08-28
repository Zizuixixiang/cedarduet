import shutil
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "app" / "static" / "index.html").read_text(encoding="utf-8")
SCRIPT = (ROOT / "app" / "static" / "app.js").read_text(encoding="utf-8")
STYLES = (ROOT / "app" / "static" / "styles.css").read_text(encoding="utf-8")
NODE = shutil.which("node")


def function_source(name: str) -> str:
    start = SCRIPT.index(f"function {name}(")
    end = SCRIPT.find("\nfunction ", start + 1)
    return SCRIPT[start:] if end < 0 else SCRIPT[start:end]


class StakeLobbyUiTests(unittest.TestCase):
    def test_header_has_separate_balance_badge_and_chip_center_button(self):
        self.assertIn('id="humanChipBalance"', HTML)
        self.assertIn('id="chipBalanceLink"', HTML)
        self.assertIn('id="chipCenterLink"', HTML)
        actions = HTML[
            HTML.index('<div class="chip-wallet-actions"'):
            HTML.index("</div>", HTML.index('<div class="chip-wallet-actions"'))
        ]
        balance = HTML[
            HTML.index('<a id="chipBalanceLink"'):
            HTML.index("</a>", HTML.index('<a id="chipBalanceLink"'))
        ]
        center = HTML[
            HTML.index('<a id="chipCenterLink"'):
            HTML.index("</a>", HTML.index('<a id="chipCenterLink"'))
        ]
        self.assertLess(actions.index("chipBalanceLink"), actions.index("chipCenterLink"))
        self.assertIn('href="/chips"', balance)
        self.assertIn('class="chip-wallet-icon"', balance)
        self.assertIn('id="humanChipBalance"', balance)
        self.assertNotIn('class="chip-center-button"', balance)
        self.assertIn('href="/chips"', center)
        self.assertIn("筹码中心", center)
        self.assertNotIn('id="humanChipBalance"', center)
        self.assertNotIn("→", actions)
        self.assertNotIn("chip-wallet-link", actions)
        self.assertIn('$("chipBalanceLink").href = apiPath("/chips")', SCRIPT)
        self.assertIn('$("chipCenterLink").href = apiPath("/chips")', SCRIPT)
        self.assertIn('class="lobby-header"', HTML)
        self.assertNotIn('class="hero pixel-card"', HTML)
        self.assertNotIn('class="hero-art"', HTML)
        self.assertIn(".topbar { min-height: 52px;", STYLES)
        self.assertIn(".lobby-header { min-height: 46px;", STYLES)

    def test_compact_chip_entries_share_height_and_mobile_row_contract(self):
        for selector in (
            ".chip-balance-link:hover",
            ".chip-center-button:hover",
            ".chip-balance-link:active",
            ".chip-center-button:active",
            ".chip-balance-link:focus-visible",
            ".chip-center-button:focus-visible",
            ".chip-balance-link.negative",
            ".chip-balance-link.long-balance .chip-balance",
        ):
            self.assertIn(selector, STYLES)
        shared_controls = STYLES[
            STYLES.index(".chip-balance-link,\n.chip-center-button {"):
            STYLES.index("}", STYLES.index(".chip-balance-link,\n.chip-center-button {"))
        ]
        self.assertIn("height: 36px", shared_controls)
        self.assertIn("min-height: 36px", shared_controls)
        self.assertNotIn("linear-gradient(135deg, #fff8d7", STYLES)
        self.assertNotIn("box-shadow: 3px 3px 0 rgba(66, 43, 71", STYLES)
        self.assertIn("font-variant-numeric: tabular-nums", STYLES)
        self.assertIn("text-overflow: ellipsis", STYLES)
        mobile = STYLES[STYLES.index("@media (max-width: 599px)"):]
        self.assertIn(".chip-wallet-actions { gap: 4px; flex-wrap: nowrap; }", mobile)
        self.assertIn("height: 34px; min-height: 34px", mobile)
        self.assertIn("max-width: 80px", mobile)
        self.assertIn(".chip-balance { max-width: 48px; font-size: 14px; }", mobile)
        self.assertIn(".chip-center-button { padding: 5px 6px; font-size: 10px; }", mobile)
        self.assertIn("white-space: nowrap", mobile)
        self.assertNotIn(".brand > span:last-child { display: none; }", mobile)

    def test_multiplayer_picker_is_collapsed_accessible_and_not_native_multiple(self):
        picker = HTML[
            HTML.index('<div class="participant-picker"'):
            HTML.index('<label class="pixel-field">\n                <span>棋种</span>')
        ]
        self.assertIn('id="aiPlayer"', picker)
        self.assertNotIn(" multiple", picker)
        self.assertIn('id="aiMultiTrigger"', picker)
        self.assertIn('aria-haspopup="listbox"', picker)
        self.assertIn('aria-expanded="false"', picker)
        self.assertIn('aria-controls="aiMultiMenu"', picker)
        self.assertIn('id="aiMultiMenu" class="ai-multi-menu hidden"', picker)
        self.assertIn('role="listbox"', picker)
        self.assertIn('aria-multiselectable="true"', picker)
        self.assertIn('id="aiMultiSummary">请选择对手', picker)
        self.assertIn('option.setAttribute("role", "option")', SCRIPT)
        self.assertIn('option.setAttribute("aria-selected"', SCRIPT)
        self.assertIn('event.key === "ArrowDown"', SCRIPT)
        self.assertIn('event.key === "ArrowUp"', SCRIPT)
        self.assertIn('event.key === "Home"', SCRIPT)
        self.assertIn('event.key === "Escape"', SCRIPT)
        self.assertIn('document.addEventListener("click"', SCRIPT)
        self.assertIn('closeMachineMultiPicker({restoreFocus: true})', SCRIPT)

    def test_opening_modes_and_compact_mobile_form_contract(self):
        mode = HTML[
            HTML.index('<select id="mode">'):
            HTML.index("</select>", HTML.index('<select id="mode">'))
        ]
        for value, label in (
            ("human_first", "你先手"),
            ("ai_first", "小机先手"),
            ("random", "随机"),
        ):
            self.assertIn(f'<option value="{value}">{label}</option>', mode)
        self.assertIn('return "先手：全桌随机"', SCRIPT)
        self.assertIn('"先手：从已选小机中随机"', SCRIPT)
        self.assertIn("mode: $(\"mode\").value", SCRIPT)
        self.assertIn("min-height: 44px", STYLES)
        self.assertIn("#aiMultiSummary", STYLES)
        self.assertIn("text-overflow: ellipsis", STYLES)
        self.assertIn("max-height: min(264px, 45vh)", STYLES)
        self.assertIn("minmax(250px, 1.4fr) auto", STYLES)
        self.assertIn("@media (max-width: 1100px)", STYLES)
        mobile = STYLES[STYLES.index("@media (max-width: 599px)"):]
        self.assertIn(
            "grid-template-columns: minmax(96px, .75fr) minmax(0, 1.25fr)",
            mobile,
        )
        self.assertNotIn(".seat-preview-item", STYLES)
        seat_preview = function_source("renderSeatPreview")
        self.assertIn('preview.textContent = `座位：', seat_preview)
        self.assertNotIn("document.createElement", seat_preview)

    @unittest.skipUnless(NODE, "node is required for multiplayer picker tests")
    def test_multiplayer_summary_and_selection_order_follow_identity_catalog(self):
        functions = "\n".join((
            function_source("selectedParticipantIds"),
            function_source("machinePickerSummary"),
            function_source("openingPreferenceText"),
        ))
        harness = f"""
const assert = require("node:assert/strict");
const selectedMachineIds = new Set(["ai-2", "ai-1"]);
const picker = {{dataset: {{selectionMode: "multiple"}}}};
const elements = {{
  aiPlayer: {{value: "ai-3", closest: () => picker}},
  mode: {{value: "ai_first"}},
}};
const identity = {{machines: [
  {{id: "ai-1", name: "甲"}},
  {{id: "ai-2", name: "乙"}},
  {{id: "ai-3", name: "丙"}},
]}};
const $ = (id) => elements[id];
{functions}
assert.deepEqual(selectedParticipantIds(), ["ai-1", "ai-2"]);
assert.equal(
  machinePickerSummary(identity.machines.slice(0, 2)),
  "已选 2 位：甲、乙",
);
assert.equal(openingPreferenceText(), "先手：从已选小机中随机");
elements.mode.value = "random";
assert.equal(openingPreferenceText(), "先手：全桌随机");
picker.dataset.selectionMode = "single";
assert.deepEqual(selectedParticipantIds(), ["ai-3"]);
elements.aiPlayer.value = "";
assert.deepEqual(selectedParticipantIds(), []);
assert.equal(machinePickerSummary([]), "请选择对手");
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

    def test_custom_integer_stake_and_pending_area_precede_room_list(self):
        self.assertIn(
            'id="stake" type="number" min="0" step="1" inputmode="numeric" value="0"',
            HTML,
        )
        self.assertIn("余额可为负", HTML)
        self.assertLess(HTML.index('id="pendingPanel"'), HTML.index("我的全部房间"))
        pending = function_source("renderPendingInvitations")
        for copy in ("发起方", "棋种", "stake_label", "接受", "拒绝"):
            self.assertIn(copy, pending)

    def test_only_selected_machine_wallet_is_loaded_and_shown(self):
        selected = function_source("renderSelectedParticipants")
        loader = SCRIPT[
            SCRIPT.index("async function machineSelectionChanged("):
            SCRIPT.index("async function loadIdentity(")
        ]
        self.assertIn("对手筹码", selected)
        self.assertIn("selectedParticipantIds()[0]", loader)
        self.assertIn("/api/chips/machines/", loader)
        self.assertNotIn("Promise.all", loader)

    def test_room_cards_show_stake_and_terminal_controls_share_one_row(self):
        rooms = function_source("renderRooms")
        self.assertIn('summary.stake_label', rooms)
        self.assertIn('"娱乐局"', rooms)
        self.assertIn('controls.append(retention, preserve, remove)', rooms)
        self.assertIn('preserve.textContent = summary.preserved ? "取消保留" : "保留"', rooms)
        self.assertIn('remove.textContent = "删除对局"', rooms)
        self.assertIn(
            "grid-template-columns: minmax(0, 1fr) auto auto", STYLES
        )
        self.assertNotIn(".room-retention { width: 100%;", STYLES)

    def test_readability_hierarchy_is_larger_without_touching_timeline_branches(self):
        self.assertIn(".room-title { display: block; color: var(--purple-darker); font-size: 14px; }", STYLES)
        self.assertIn(".player-name { max-width: 132px; padding: 6px 8px; font-size: 12px; }", STYLES)
        self.assertIn("timelineEventKind", SCRIPT)
        self.assertIn('if (eventKind === "chat")', SCRIPT)
        self.assertIn('if (eventKind === "move" && event.text)', SCRIPT)

    def test_nonzero_rematch_prefills_stake_and_waits_for_confirmation(self):
        rematch = SCRIPT[
            SCRIPT.index("async function rematch("):
            SCRIPT.index("function startRoomPolling(")
        ]
        self.assertIn("stake: previousRoom.stake || 0", rematch)
        self.assertIn('$("stake").value = String(previousRoom.stake || 0)', rematch)
        self.assertIn('data.room.status === "pending"', rematch)


@unittest.skipUnless(NODE, "node is required for chip balance rendering tests")
class ChipWalletRenderingTests(unittest.TestCase):
    def test_balance_renderer_formats_and_marks_negative_and_long_values(self):
        renderer = function_source("renderHumanChipBalance")
        harness = f"""
const assert = require("node:assert/strict");
class ClassList {{
  constructor() {{ this.names = new Set(); }}
  toggle(name, force) {{
    if (force === undefined ? !this.names.has(name) : force) this.names.add(name);
    else this.names.delete(name);
  }}
  contains(name) {{ return this.names.has(name); }}
}}
const elements = {{
  humanChipBalance: {{textContent: "", title: "", attributes: {{}}, setAttribute(name, value) {{ this.attributes[name] = value; }}}},
  chipBalanceLink: {{classList: new ClassList(), attributes: {{}}, setAttribute(name, value) {{ this.attributes[name] = value; }}}},
}};
const $ = (id) => elements[id];
{renderer}
renderHumanChipBalance(-1234567890123);
assert.equal(elements.humanChipBalance.textContent, "-1,234,567,890,123");
assert.equal(elements.humanChipBalance.title, "当前余额：-1,234,567,890,123");
assert.ok(elements.chipBalanceLink.classList.contains("negative"));
assert.ok(elements.chipBalanceLink.classList.contains("long-balance"));
assert.match(elements.chipBalanceLink.attributes["aria-label"], /余额 -1,234,567,890,123/);
renderHumanChipBalance(1280);
assert.equal(elements.humanChipBalance.textContent, "1,280");
assert.ok(!elements.chipBalanceLink.classList.contains("negative"));
assert.ok(!elements.chipBalanceLink.classList.contains("long-balance"));
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


if __name__ == "__main__":
    unittest.main()

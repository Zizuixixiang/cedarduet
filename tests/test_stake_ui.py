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
    def test_mobile_first_header_has_wallet_and_chip_center_without_hero_art(self):
        self.assertIn('id="humanChipBalance"', HTML)
        self.assertIn('id="chipCenterLink"', HTML)
        self.assertIn('href="/chips"', HTML)
        wallet = HTML[
            HTML.index('<a id="chipCenterLink"'):
            HTML.index("</a>", HTML.index('<a id="chipCenterLink"'))
        ]
        self.assertIn('class="chip-wallet-icon"', wallet)
        self.assertIn('class="chip-wallet-title">我的筹码', wallet)
        self.assertIn('id="humanChipBalance"', wallet)
        self.assertIn("筹码中心", wallet)
        self.assertIn("→", wallet)
        self.assertNotIn("pixel-btn", wallet)
        self.assertIn('class="lobby-header"', HTML)
        self.assertNotIn('class="hero pixel-card"', HTML)
        self.assertNotIn('class="hero-art"', HTML)
        self.assertIn(".topbar { min-height: 52px;", STYLES)
        self.assertIn(".lobby-header { min-height: 46px;", STYLES)

    def test_chip_wallet_link_has_prominent_interaction_and_mobile_contracts(self):
        for selector in (
            ".chip-wallet-link:hover",
            ".chip-wallet-link:active",
            ".chip-wallet-link:focus-visible",
            ".chip-wallet-link.negative",
            ".chip-wallet-link.long-balance .chip-balance",
        ):
            self.assertIn(selector, STYLES)
        self.assertIn("linear-gradient(135deg, #fff8d7", STYLES)
        self.assertIn("font-variant-numeric: tabular-nums", STYLES)
        self.assertIn("text-overflow: ellipsis", STYLES)
        mobile = STYLES[STYLES.index("@media (max-width: 599px)"):]
        self.assertIn(".chip-wallet-link {", mobile)
        self.assertIn("min-width: 132px", mobile)
        self.assertIn(".chip-wallet-title { font-size: 10px; }", mobile)
        self.assertIn(".chip-balance { max-width: 92px; font-size: 16px; }", mobile)
        self.assertIn(".chip-wallet-cta span { display: none; }", mobile)
        self.assertIn(".chip-wallet-cta b { font-size: 18px; }", mobile)

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
  chipCenterLink: {{classList: new ClassList(), attributes: {{}}, setAttribute(name, value) {{ this.attributes[name] = value; }}}},
}};
const $ = (id) => elements[id];
{renderer}
renderHumanChipBalance(-1234567890123);
assert.equal(elements.humanChipBalance.textContent, "-1,234,567,890,123");
assert.equal(elements.humanChipBalance.title, "当前余额：-1,234,567,890,123");
assert.ok(elements.chipCenterLink.classList.contains("negative"));
assert.ok(elements.chipCenterLink.classList.contains("long-balance"));
assert.match(elements.chipCenterLink.attributes["aria-label"], /余额 -1,234,567,890,123/);
renderHumanChipBalance(1280);
assert.equal(elements.humanChipBalance.textContent, "1,280");
assert.ok(!elements.chipCenterLink.classList.contains("negative"));
assert.ok(!elements.chipCenterLink.classList.contains("long-balance"));
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

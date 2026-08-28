import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "app" / "static" / "index.html").read_text(encoding="utf-8")
SCRIPT = (ROOT / "app" / "static" / "app.js").read_text(encoding="utf-8")
STYLES = (ROOT / "app" / "static" / "styles.css").read_text(encoding="utf-8")


def function_source(name: str) -> str:
    start = SCRIPT.index(f"function {name}(")
    end = SCRIPT.find("\nfunction ", start + 1)
    return SCRIPT[start:] if end < 0 else SCRIPT[start:end]


class StakeLobbyUiTests(unittest.TestCase):
    def test_mobile_first_header_has_wallet_and_chip_center_without_hero_art(self):
        self.assertIn('id="humanChipBalance"', HTML)
        self.assertIn('id="chipCenterLink"', HTML)
        self.assertIn('href="/chips"', HTML)
        self.assertIn('class="lobby-header"', HTML)
        self.assertNotIn('class="hero pixel-card"', HTML)
        self.assertNotIn('class="hero-art"', HTML)
        self.assertIn(".topbar { min-height: 52px;", STYLES)
        self.assertIn(".lobby-header { min-height: 46px;", STYLES)

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


if __name__ == "__main__":
    unittest.main()

import shutil
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (ROOT / "app" / "static" / "app.js").read_text(encoding="utf-8")
STYLES = (ROOT / "app" / "static" / "styles.css").read_text(encoding="utf-8")
HTML = (ROOT / "app" / "static" / "index.html").read_text(encoding="utf-8")
NODE = shutil.which("node")


def function_source(name: str) -> str:
    start = SCRIPT.index(f"function {name}(")
    end = SCRIPT.find("\nfunction ", start + 1)
    return SCRIPT[start:] if end < 0 else SCRIPT[start:end]


class LobbyRefreshTests(unittest.TestCase):
    def run_node(self, source: str) -> None:
        completed = subprocess.run(
            [NODE, "-e", source],
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

    def test_refresh_control_is_accessible_and_separate_from_create_form(self):
        lobby_header = HTML[
            HTML.index('<div class="lobby-header">'):
            HTML.index('<p id="notice"')
        ]
        self.assertIn('id="refreshLobbyButton"', lobby_header)
        self.assertIn('title="刷新大厅数据"', lobby_header)
        self.assertIn('aria-label="刷新大厅数据"', lobby_header)
        self.assertIn('aria-busy="false"', lobby_header)
        self.assertIn('aria-hidden="true">↻</span>', lobby_header)
        self.assertNotIn("<img", lobby_header)
        self.assertLess(
            HTML.index('id="refreshLobbyButton"'),
            HTML.index('class="create-form"'),
        )
        self.assertIn(".lobby-refresh-button {", STYLES)
        self.assertIn("min-width: 42px", STYLES)
        mobile = STYLES[STYLES.index("@media (max-width: 599px)"):]
        self.assertIn("min-width: 40px", mobile)
        self.assertIn("min-height: 40px", mobile)
        self.assertIn("@keyframes lobby-refresh-spin", STYLES)

    def test_refresh_reuses_identity_data_path_without_reloading_page(self):
        loader = function_source("loadIdentity")
        for expected in (
            'request("/api/whoami")',
            "renderHumanChipBalance(data.wallet.balance)",
            "syncGameTypeOptions(data.games || [])",
            "syncMachinePicker(data.machines || [])",
            "renderPendingInvitations(data.pending_invitations || [])",
            "renderRooms((data.rooms || [])",
        ):
            self.assertIn(expected, loader)
        refresher = function_source("refreshLobbyIdentity")
        self.assertIn("await loadIdentity()", refresher)
        self.assertIn('button.setAttribute("aria-busy", "true")', refresher)
        self.assertIn('button.classList.add("is-refreshing")', refresher)
        self.assertIn("finally", refresher)
        self.assertIn(
            '$("refreshLobbyButton").addEventListener("click", refreshLobbyIdentity)',
            SCRIPT,
        )
        self.assertNotIn("location.reload", SCRIPT)

    @unittest.skipUnless(NODE, "node is required for lobby refresh tests")
    def test_refresh_disables_button_until_identity_update_finishes(self):
        refresher = "async " + function_source("refreshLobbyIdentity")
        harness = f"""
const assert = require("node:assert/strict");
class ClassList {{
  constructor() {{ this.names = new Set(); }}
  add(name) {{ this.names.add(name); }}
  remove(name) {{ this.names.delete(name); }}
  contains(name) {{ return this.names.has(name); }}
}}
const button = {{
  disabled: false,
  classList: new ClassList(),
  attributes: {{"aria-busy": "false"}},
  setAttribute(name, value) {{ this.attributes[name] = value; }},
}};
const $ = (id) => {{ assert.equal(id, "refreshLobbyButton"); return button; }};
const notices = [];
const showNotice = (message) => notices.push(message);
let finishLoad;
let nextLoad = new Promise((resolve) => {{ finishLoad = resolve; }});
let loadCount = 0;
const loadIdentity = () => {{ loadCount += 1; return nextLoad; }};
{refresher}
(async () => {{
  const pending = refreshLobbyIdentity();
  assert.equal(button.disabled, true);
  assert.equal(button.attributes["aria-busy"], "true");
  assert.ok(button.classList.contains("is-refreshing"));
  await refreshLobbyIdentity();
  assert.equal(loadCount, 1);
  finishLoad(true);
  await pending;
  assert.equal(button.disabled, false);
  assert.equal(button.attributes["aria-busy"], "false");
  assert.ok(!button.classList.contains("is-refreshing"));
  assert.deepEqual(notices, ["大厅数据已刷新"]);

  nextLoad = Promise.resolve(false);
  await refreshLobbyIdentity();
  assert.equal(button.disabled, false);
  assert.deepEqual(notices, ["大厅数据已刷新"]);
}})().catch((error) => {{ console.error(error); process.exitCode = 1; }});
"""
        self.run_node(harness)

    @unittest.skipUnless(NODE, "node is required for lobby refresh tests")
    def test_identity_refresh_preserves_only_still_available_opponents(self):
        sync_picker = function_source("syncMachinePicker")
        harness = f"""
const assert = require("node:assert/strict");
class Option {{ constructor() {{ this.value = ""; this.textContent = ""; }} }}
class Select {{
  constructor() {{
    this.children = [];
    this.value = "ai-2";
    this.disabled = false;
    this.dataset = {{}};
  }}
  replaceChildren() {{ this.children = []; this.value = ""; }}
  appendChild(child) {{ this.children.push(child); }}
}}
const select = new Select();
const document = {{createElement: () => new Option()}};
const $ = (id) => {{ assert.equal(id, "aiPlayer"); return select; }};
const selectedMachineIds = new Set(["ai-1", "ai-3"]);
const selectedMachineWallets = new Map([["ai-1", {{balance: 20}}]]);
let machineWalletRequest = 0;
let configured = 0;
let walletLoads = 0;
const configureParticipantPicker = () => {{ configured += 1; }};
const machineSelectionChanged = () => {{ walletLoads += 1; }};
const renderMachineMultiPicker = () => {{}};
const renderCreateSeatPreview = () => {{}};
{sync_picker}
const allMachines = [
  {{id: "ai-1", name: "甲"}},
  {{id: "ai-2", name: "乙"}},
  {{id: "ai-3", name: "丙"}},
];
syncMachinePicker(allMachines);
assert.equal(select.value, "ai-2");
assert.deepEqual([...selectedMachineIds], ["ai-1", "ai-3"]);
assert.equal(selectedMachineWallets.size, 0);

syncMachinePicker(allMachines.slice(0, 2));
assert.equal(select.value, "ai-2");
assert.deepEqual([...selectedMachineIds], ["ai-1"]);
assert.equal(configured, 2);
assert.equal(walletLoads, 2);
"""
        self.run_node(harness)


if __name__ == "__main__":
    unittest.main()

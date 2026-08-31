import json
import shutil
import subprocess
import unittest
from pathlib import Path

from app.games.chinese_checkers import ChineseCheckers


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "app" / "static" / "games" / "chinese_checkers.js"
STYLE_PATH = ROOT / "app" / "static" / "games" / "chinese_checkers.css"
SCRIPT = SCRIPT_PATH.read_text(encoding="utf-8")
STYLES = STYLE_PATH.read_text(encoding="utf-8")
GLOBAL_STYLES = (ROOT / "app" / "static" / "styles.css").read_text(
    encoding="utf-8"
)
HTML = (ROOT / "app" / "static" / "index.html").read_text(encoding="utf-8")
NODE = shutil.which("node")


class ChineseCheckersFrontendStructureTests(unittest.TestCase):
    @staticmethod
    def css_rule(selector: str) -> str:
        rule = STYLES[STYLES.index(f"{selector} {{"):]
        return rule[:rule.index("}")]

    def test_independent_renderer_registers_and_loads_stylesheet_idempotently(self):
        self.assertIn("function ensureStylesheet()", SCRIPT)
        self.assertIn('const STYLE_ID = "duel-chinese-checkers-styles"', SCRIPT)
        self.assertIn(
            'const STYLE_HREF = "/static/games/chinese_checkers.css?v=0.1.17"',
            SCRIPT,
        )
        self.assertIn('document.getElementById(STYLE_ID)', SCRIPT)
        self.assertIn(
            'window.DuelGameUI.register("chinese_checkers", renderer)', SCRIPT
        )
        self.assertIn("function renderBoard(context)", SCRIPT)
        self.assertIn('participantPresentation: "board-edge"', SCRIPT)
        self.assertIn("state.legal_moves", SCRIPT)
        self.assertIn("movePayload(targetMove)", SCRIPT)
        self.assertNotIn("function legalMoves", SCRIPT)
        self.assertNotIn("/static/games/chinese_checkers.js", HTML)
        self.assertNotIn(
            '<link rel="stylesheet" href="/static/games/chinese_checkers.css',
            HTML,
        )
        self.assertIn(
            '<option value="chinese_checkers">中国跳棋 / 2、3、4、6人</option>',
            HTML,
        )

    def test_star_glass_marbles_move_states_and_mobile_layout_are_explicit(self):
        for selector in (
            ".board.chinese_checkers",
            ".cc-star-surface",
            ".cc-hole.camp-0",
            ".cc-marble",
            ".cc-hole.selected-origin",
            ".cc-legal-marker.step-marker",
            ".cc-legal-marker.jump-marker",
            ".cc-hole.last-move-from",
            ".cc-hole.last-move-to",
            ".cc-path-preview",
            ".cc-legend-item.viewer-progress",
        ):
            self.assertIn(selector, STYLES)
        self.assertIn("clip-path: polygon", STYLES)
        self.assertIn("radial-gradient", STYLES)
        self.assertIn("cc-marble-gleam", SCRIPT + STYLES)
        self.assertIn('path.textContent = "细线表示连续跳跃路径"', SCRIPT)
        self.assertNotIn("canonical path", SCRIPT)
        self.assertIn("touch-action: manipulation", STYLES)
        self.assertIn("@media (max-width: 375px)", STYLES)
        self.assertIn("@media (max-width: 320px)", STYLES)
        self.assertIn("width: min(98vw, 314px)", STYLES)
        board_rule = STYLES[STYLES.index(".board.chinese_checkers {"):]
        board_rule = board_rule[:board_rule.index("}")]
        self.assertIn("aspect-ratio: 0.88 / 1;", board_rule)
        self.assertIn("display: block;", board_rule)
        self.assertIn("overflow: hidden;", board_rule)
        self.assertNotIn("height: auto;", board_rule)
        self.assertNotIn("display: grid;", board_rule)
        self.assertIn("left: 8 + ((x + 6) / 12) * 84", SCRIPT)
        self.assertIn("top: 5 + ((y + 4 * Math.sqrt(3))", SCRIPT)
        self.assertNotIn("cc-board-geometry", SCRIPT + STYLES)
        self.assertNotIn("cc-camp-wash", SCRIPT + STYLES)
        self.assertNotIn("width: clamp(17px, 6%", STYLES)
        self.assertIn("width: clamp(17px, 5.05%, 31px);", STYLES)
        camp_rule = STYLES[STYLES.index(".board.chinese_checkers .cc-hole.camp-0,"):]
        camp_rule = camp_rule[:camp_rule.index("}")]
        self.assertIn("background:", camp_rule)
        self.assertNotIn("border:", camp_rule)
        self.assertNotIn("outline:", camp_rule)
        self.assertNotIn("box-shadow:", camp_rule)
        self.assertIn("color-mix(in srgb, #7b7180 88%, var(--camp-fill))", camp_rule)
        self.assertNotIn(".cc-hole.viewer-start-camp {", STYLES)
        self.assertNotIn(".cc-hole.viewer-target-camp {", STYLES)
        self.assertIn("linear-gradient(145deg, #f2f1ed 0%, #dfdcd6 62%", STYLES)
        marble_seat_rule = STYLES[
            STYLES.index(".board.chinese_checkers .cc-marble.seat-0,"):
        ]
        marble_seat_rule = marble_seat_rule[:marble_seat_rule.index("}")]
        for seat in range(6):
            self.assertIn(
                f".board.chinese_checkers .cc-marble.seat-{seat}",
                marble_seat_rule,
            )
        self.assertIn("var(--seat-soft)", marble_seat_rule)
        self.assertIn("var(--seat-color)", marble_seat_rule)
        self.assertIn("var(--seat-ink)", marble_seat_rule)
        self.assertIn("color-mix(in srgb", marble_seat_rule)
        self.assertNotIn(".cc-progress-row", STYLES)
        self.assertNotIn(".cc-progress-badge", STYLES)
        self.assertNotIn('progressRow.className = "cc-progress-row"', SCRIPT)
        self.assertIn("function targetProgressForPlayer(state, playerId)", SCRIPT)
        self.assertNotIn("state.target_progress_by_player", SCRIPT)
        self.assertNotIn("url(", STYLES)
        for emoji in ("🔴", "🔵", "🟢", "🟡", "⚪", "⚫", "🔮"):
            self.assertNotIn(emoji, SCRIPT + STYLES)

    def test_renderer_has_stable_view_rotation_and_canonical_path_preview(self):
        self.assertIn("function rotateAxial(q, r, steps)", SCRIPT)
        self.assertIn("(3 - startCamp + 6) % 6", SCRIPT)
        self.assertIn("board.dataset.rotationSteps", SCRIPT)
        self.assertIn('document.createElementNS(SVG_NS, "polyline")', SCRIPT)
        self.assertIn("uiState.previewPath", SCRIPT)
        self.assertIn('targetMove.kind === "jump"', SCRIPT)
        self.assertIn('targetMove.kind === "jump" ? "跳跃" : "相邻一步"', SCRIPT)

    def test_rosters_sort_visual_camps_into_fixed_row_slots(self):
        self.assertNotIn("FIXED_EDGE_LAYOUTS", SCRIPT)
        self.assertIn("function visualCampCenter(nodes, camp, rotationSteps)", SCRIPT)
        self.assertIn("function compareVisualRosterEntries(left, right)", SCRIPT)
        self.assertIn("function fixedRowSlots(edge, count)", SCRIPT)
        self.assertIn('if (count === 1) return [`${edge}-2`];', SCRIPT)
        self.assertIn(
            'if (count === 2) return [`${edge}-1`, `${edge}-3`];',
            SCRIPT,
        )
        self.assertIn(
            ".filter((entry) => entry.position.top < 50)",
            SCRIPT,
        )
        self.assertIn(
            ".filter((entry) => entry.position.top >= 50)",
            SCRIPT,
        )
        self.assertIn(".sort(compareVisualRosterEntries);", SCRIPT)
        self.assertIn(
            "const edgeRoster = edgeRosters(context, nodes, rotationSteps);",
            SCRIPT,
        )
        self.assertNotIn("position.left < 34", SCRIPT)

    def test_three_four_and_six_player_mobile_width_chain_never_overflows_card(self):
        scope = (
            '#battleStage[data-game-type="chinese_checkers"]'
            ".multiplayer-presentation"
        )
        self.assertIn(
            "*, *::before, *::after { box-sizing: border-box;",
            GLOBAL_STYLES,
        )
        pixel_card_rule = GLOBAL_STYLES[
            GLOBAL_STYLES.index(".pixel-card {"):
        ]
        pixel_card_rule = pixel_card_rule[:pixel_card_rule.index("}")]
        self.assertIn("border: 4px solid", pixel_card_rule)
        global_mobile = GLOBAL_STYLES[
            GLOBAL_STYLES.index("@media (max-width: 599px)"):
        ]
        self.assertIn(
            "padding: 8px 10px calc(var(--safe-bottom) + 20px);",
            global_mobile,
        )
        self.assertIn(
            ".battle-stage { padding: 10px 7px 12px; gap: 8px; }",
            global_mobile,
        )
        stage_rule = self.css_rule(scope)
        self.assertIn("max-width: 100%;", stage_rule)
        self.assertIn("min-width: 0;", stage_rule)
        self.assertIn("box-sizing: border-box;", stage_rule)
        container_rule = STYLES[STYLES.index(f"{scope} .battle-main-column,"):]
        container_rule = container_rule[:container_rule.index("}")]
        for selector in (
            ".battle-main-column",
            ".table-layout",
            ".board-zone",
        ):
            self.assertIn(f"{scope} {selector}", container_rule)
        self.assertIn("width: 100%;", container_rule)
        self.assertIn("max-width: 100%;", container_rule)
        self.assertIn("min-width: 0;", container_rule)
        self.assertIn("box-sizing: border-box;", container_rule)
        self.assertNotIn(":has(", STYLES)

        constrained_rule = STYLES[
            STYLES.index(
                f"{scope} .board.chinese_checkers.multiplayer-board,"
            ):
        ]
        constrained_rule = constrained_rule[:constrained_rule.index("}")]
        for selector in (
            ".board.chinese_checkers.multiplayer-board",
            ".cc-edge-roster",
            ".cc-playfield",
            ".game-controls",
            ".move-confirm",
            ".cc-legend",
        ):
            self.assertIn(f"{scope} {selector}", constrained_rule)
        self.assertIn("max-width: 100%;", constrained_rule)
        self.assertIn("min-width: 0;", constrained_rule)
        self.assertIn("box-sizing: border-box;", constrained_rule)
        self.assertNotIn("\n  width: 100%;", constrained_rule)

        frame_rule = self.css_rule(f"{scope} .board-frame")
        self.assertIn("width: 100%;", frame_rule)
        self.assertIn("max-width: 100%;", frame_rule)
        self.assertIn("min-width: 0;", frame_rule)
        self.assertIn("justify-items: center;", frame_rule)
        self.assertIn("box-sizing: border-box;", frame_rule)

        root_rule = self.css_rule(
            ".board.chinese_checkers.multiplayer-board"
        )
        self.assertIn("max-width: 100%;", root_rule)
        self.assertIn("min-width: 0;", root_rule)
        self.assertIn("grid-template-columns: minmax(0, 1fr);", root_rule)

        for selector in (
            ".cc-edge-roster",
            ".board.chinese_checkers .cc-playfield",
        ):
            rule = self.css_rule(selector)
            self.assertIn("width: 100%;", rule)
            self.assertIn("max-width: 100%;", rule)
            self.assertIn("min-width: 0;", rule)
            self.assertIn("box-sizing: border-box;", rule)

        participant_rule = self.css_rule(".cc-edge-participant")
        self.assertIn("width: 100%;", participant_rule)
        self.assertIn("max-width: 138px;", participant_rule)
        self.assertIn("height: 44px;", participant_rule)
        self.assertIn("min-width: 0;", participant_rule)
        self.assertIn("box-sizing: border-box;", participant_rule)
        self.assertIn("grid-template-rows: 24px 8px;", participant_rule)
        self.assertIn("row-gap: 1px;", participant_rule)
        self.assertNotIn("width: min(138px, 100%);", participant_rule)
        self.assertNotIn("overflow: hidden;", participant_rule)

        copy_rule = self.css_rule(
            ".cc-edge-participant .board-edge-copy"
        )
        self.assertIn("display: contents;", copy_rule)

        name_rule = self.css_rule(
            ".cc-edge-participant .board-edge-copy strong"
        )
        self.assertIn("overflow: hidden;", name_rule)
        self.assertIn("grid-row: 1;", name_rule)
        self.assertIn("display: flex;", name_rule)
        self.assertIn("gap: 3px;", name_rule)
        self.assertIn("white-space: nowrap;", name_rule)

        player_name_rule = self.css_rule(
            ".cc-edge-participant .cc-edge-player-name"
        )
        self.assertIn("min-width: 0;", player_name_rule)
        self.assertIn("flex: 1 1 auto;", player_name_rule)
        self.assertIn("overflow: hidden;", player_name_rule)
        self.assertIn("text-overflow: ellipsis;", player_name_rule)
        self.assertIn("white-space: nowrap;", player_name_rule)

        meta_rule = self.css_rule(".cc-edge-participant .cc-edge-meta")
        self.assertIn("display: flex;", meta_rule)
        self.assertIn("grid-column: 1 / -1;", meta_rule)
        self.assertIn("grid-row: 2;", meta_rule)
        self.assertIn("flex-wrap: nowrap;", meta_rule)
        self.assertIn("overflow: visible;", meta_rule)
        self.assertIn("text-overflow: initial;", meta_rule)
        self.assertIn("white-space: nowrap;", meta_rule)
        self.assertIn("font-size: 8px;", meta_rule)
        self.assertIn("line-height: 1;", meta_rule)
        meta_segment_rule = self.css_rule(
            ".cc-edge-participant .cc-edge-meta > span"
        )
        self.assertIn("flex: 0 0 auto;", meta_segment_rule)
        self.assertIn("white-space: nowrap;", meta_segment_rule)
        progress_rule = self.css_rule(
            ".cc-edge-participant .cc-edge-progress"
        )
        self.assertIn("font-weight: 700;", progress_rule)
        self.assertIn(
            ".cc-edge-participant .cc-edge-start-camp { font-weight: 400; }",
            STYLES,
        )

        mobile_start = STYLES.index("@media (max-width: 599px)")
        mobile_stage_rule = STYLES[
            STYLES.index(f"  {scope} {{", mobile_start):
        ]
        mobile_stage_rule = mobile_stage_rule[:mobile_stage_rule.index("}")]
        self.assertIn("width: 100%;", mobile_stage_rule)
        self.assertIn("max-width: 100%;", mobile_stage_rule)
        self.assertIn("min-width: 0;", mobile_stage_rule)
        self.assertIn("box-sizing: border-box;", mobile_stage_rule)
        mobile_rule = STYLES[
            STYLES.index(f"  {scope} .board-frame,", mobile_start):
        ]
        mobile_rule = mobile_rule[:mobile_rule.index("}")]
        for selector in (
            ".board-frame",
            ".board.chinese_checkers.multiplayer-board",
            ".cc-edge-roster",
            ".cc-playfield",
            ".game-controls",
            ".move-confirm",
            ".cc-legend",
        ):
            self.assertIn(f"{scope} {selector}", mobile_rule)
        self.assertIn("width: 100%;", mobile_rule)
        self.assertIn("max-width: 100%;", mobile_rule)
        self.assertIn("min-width: 0;", mobile_rule)
        self.assertIn("box-sizing: border-box;", mobile_rule)

        mobile_participant_rule = STYLES[
            STYLES.index("  .cc-edge-participant {", mobile_start):
        ]
        mobile_participant_rule = mobile_participant_rule[
            :mobile_participant_rule.index("}")
        ]
        self.assertIn("height: 42px;", mobile_participant_rule)
        self.assertIn("min-height: 42px;", mobile_participant_rule)

        narrow_start = STYLES.index("@media (max-width: 320px)")
        narrow_meta_rule = STYLES[
            STYLES.index(
                "  .cc-edge-participant .cc-edge-meta {",
                narrow_start,
            ):
        ]
        narrow_meta_rule = narrow_meta_rule[:narrow_meta_rule.index("}")]
        self.assertIn("gap: 0 1px;", narrow_meta_rule)
        self.assertIn("font-size: 7px;", narrow_meta_rule)

        # Mobile content width deducts duel-main padding, battle-stage borders,
        # and battle-stage padding. All multiplayer rosters use the same three
        # shrinkable columns and must remain inside that width.
        for player_count in (3, 4, 6):
            for viewport, available in (
                (320, 278),
                (360, 318),
                (375, 333),
                (390, 348),
                (412, 370),
                (414, 372),
            ):
                with self.subTest(
                    player_count=player_count,
                    viewport=viewport,
                ):
                    stage_content_width = viewport - 20 - 8 - 14
                    self.assertEqual(stage_content_width, available)
                    legacy_vw_width = viewport * (
                        0.98 if viewport <= 320
                        else 0.97 if viewport <= 375
                        else 0.96
                    )
                    self.assertGreater(legacy_vw_width, available)
                    # Every module box is explicitly 100% and border-box in
                    # multiplayer mobile mode. The roster itself has no
                    # padding/border; only its two 4px column gaps consume width.
                    board_frame_outer = available
                    multiplayer_outer = board_frame_outer
                    roster_outer = multiplayer_outer
                    playfield_outer = multiplayer_outer
                    controls_outer = available
                    for outer_width in (
                        board_frame_outer,
                        multiplayer_outer,
                        roster_outer,
                        playfield_outer,
                        controls_outer,
                    ):
                        self.assertLessEqual(outer_width, available)

                    roster_gap = 4
                    roster_column = (roster_outer - 2 * roster_gap) / 3
                    card_outer = min(roster_column, 138)
                    card_content = card_outer - 4 - 8
                    name_row = card_content - 24 - 5
                    current_label = 3 * 9
                    right_card_left = roster_outer - card_outer
                    right_card_right = right_card_left + card_outer
                    self.assertEqual(right_card_right, roster_outer)
                    self.assertLessEqual(right_card_right, available)
                    self.assertGreaterEqual(card_content, 78)
                    self.assertGreater(name_row, current_label)

    def test_current_player_uses_card_highlight_and_red_inline_label(self):
        current_rule = self.css_rule(".cc-edge-participant.current")
        self.assertIn("outline: 2px solid var(--seat-color);", current_rule)
        self.assertIn("outline-offset: -2px;", current_rule)
        self.assertIn("box-shadow:", current_rule)
        self.assertIn(
            "inset 0 0 0 3px rgba(255, 255, 255, .88)", current_rule
        )
        self.assertIn(
            "inset 0 0 0 4px "
            "color-mix(in srgb, var(--seat-color) 74%, transparent)",
            current_rule,
        )
        self.assertIn(
            "inset 0 0 9px "
            "color-mix(in srgb, var(--seat-color) 32%, transparent)",
            current_rule,
        )
        self.assertEqual(current_rule.count("inset "), 3)
        self.assertGreaterEqual(current_rule.count("var(--seat-color)"), 3)
        self.assertNotIn("#f0b429", current_rule)
        self.assertNotIn("rgba(240, 180, 41", current_rule)
        self.assertNotIn("2px 2px", current_rule)
        for layout_property in (
            "width:", "height:", "padding:", "margin:", "border:",
            "border-width:", "background:",
        ):
            self.assertNotIn(layout_property, current_rule)

        current_avatar_rule = self.css_rule(
            ".cc-edge-participant .board-edge-avatar.current-turn-avatar"
        )
        self.assertIn("outline: none;", current_avatar_rule)
        self.assertIn("outline-offset: 0;", current_avatar_rule)
        self.assertIn("box-shadow: none;", current_avatar_rule)
        current_avatar_marker_rule = self.css_rule(
            ".cc-edge-participant "
            ".board-edge-avatar.current-turn-avatar::after"
        )
        self.assertIn("content: none;", current_avatar_marker_rule)
        self.assertIn("display: none;", current_avatar_marker_rule)
        self.assertIn("box-shadow: none;", current_avatar_marker_rule)

        global_current_avatar_rule = GLOBAL_STYLES[
            GLOBAL_STYLES.index(
                ".current-turn-avatar.current-turn-avatar {"
            ):
        ]
        global_current_avatar_rule = global_current_avatar_rule[
            :global_current_avatar_rule.index("}")
        ]
        self.assertIn(
            "outline: 2px solid #f0b429;", global_current_avatar_rule
        )
        self.assertIn(
            "box-shadow: 0 0 0 4px rgba(255, 255, 255, .9);",
            global_current_avatar_rule,
        )
        global_current_avatar_marker_rule = GLOBAL_STYLES[
            GLOBAL_STYLES.index(
                ".current-turn-avatar.current-turn-avatar::after {"
            ):
        ]
        global_current_avatar_marker_rule = global_current_avatar_marker_rule[
            :global_current_avatar_marker_rule.index("}")
        ]
        self.assertIn('content: "";', global_current_avatar_marker_rule)
        self.assertIn(
            "background: #e59616;", global_current_avatar_marker_rule
        )

        label_rule = self.css_rule(
            ".cc-edge-participant .cc-current-turn-label"
        )
        self.assertIn("flex: 0 0 auto;", label_rule)
        self.assertIn("color: #c1121f;", label_rule)
        self.assertIn("font-size: 9px;", label_rule)
        self.assertIn("font-weight: 900;", label_rule)
        self.assertIn("line-height: 1;", label_rule)
        self.assertIn("white-space: nowrap;", label_rule)
        self.assertNotIn("position: absolute;", label_rule)
        self.assertIn('currentLabel.textContent = "行动中";', SCRIPT)

    def test_global_seat_palette_drives_card_and_marble_color_order(self):
        expected_palette = (
            "#d95f7a",
            "#6f4f95",
            "#3f8f8a",
            "#d18a37",
            "#417bb0",
            "#9b5b7f",
        )
        for seat, color in enumerate(expected_palette):
            global_rule = GLOBAL_STYLES[
                GLOBAL_STYLES.index(f".seat-{seat} {{"):
            ]
            global_rule = global_rule[:global_rule.index("}")]
            self.assertIn(f"--seat-color: {color};", global_rule)

        marble_rule = STYLES[
            STYLES.index(".board.chinese_checkers .cc-marble.seat-0,"):
        ]
        marble_rule = marble_rule[:marble_rule.index("}")]
        for seat in range(6):
            self.assertIn(f".cc-marble.seat-{seat}", marble_rule)
        self.assertIn("var(--seat-color)", marble_rule)
        self.assertIn("var(--seat-soft)", marble_rule)
        self.assertIn("var(--seat-ink)", marble_rule)


@unittest.skipUnless(NODE, "node is required for Chinese Checkers renderer test")
class ChineseCheckersFrontendRuntimeTests(unittest.TestCase):
    def run_node(self, assertions: str, *, state=None, participants=None) -> None:
        game = ChineseCheckers()
        if participants is None:
            participants = [
                {
                    "player_id": "human-1",
                    "display_name": "南山",
                    "role": "human",
                    "seat_index": 0,
                    "token": "P1",
                },
                {
                    "player_id": "ai-1",
                    "display_name": "小机",
                    "role": "ai",
                    "seat_index": 1,
                    "token": "P2",
                },
            ]
        if state is None:
            state = game.initialize(participants)
            camp_zero = state["camps"]["0"]
            central = [node["id"] for node in state["nodes"] if node["camp"] is None]
            origin = camp_zero[0]
            midpoint = central[len(central) // 2]
            target = central[len(central) // 2 + 1]
            state["pieces"] = {origin: "P1"}
            state["legal_moves"] = [{
                "from": origin,
                "to": target,
                "kind": "jump",
                "path": [origin, midpoint, target],
            }]
            state["last_move"] = {
                "from": camp_zero[1],
                "to": camp_zero[2],
                "kind": "step",
                "path": [camp_zero[1], camp_zero[2]],
            }
        state_json = json.dumps(state, ensure_ascii=False, separators=(",", ":"))
        participants_json = json.dumps(
            participants, ensure_ascii=False, separators=(",", ":")
        )
        harness = r'''
const assert = require("node:assert/strict");
const vm = require("node:vm");
const fs = require("node:fs");

class ClassList {
  constructor(owner) { this.owner = owner; this.names = new Set(); }
  set(value) { this.names = new Set(String(value || "").split(/\s+/).filter(Boolean)); }
  add(...names) { names.forEach((name) => this.names.add(name)); }
  remove(...names) { names.forEach((name) => this.names.delete(name)); }
  toggle(name, force) {
    const enabled = force === undefined ? !this.names.has(name) : Boolean(force);
    if (enabled) this.names.add(name); else this.names.delete(name);
    return enabled;
  }
  contains(name) { return this.names.has(name); }
}
class Element {
  constructor(tag = "div") {
    this.tag = tag.toLowerCase();
    this.children = [];
    this.dataset = {};
    this.attributes = {};
    this.listeners = {};
    this.style = {setProperty: (name, value) => { this.style[name] = String(value); }};
    this.classList = new ClassList(this);
    this.disabled = false;
    this.textContent = "";
    this.id = "";
    this.rel = "";
    this.href = "";
  }
  set className(value) { this.classList.set(value); }
  get className() { return [...this.classList.names].join(" "); }
  appendChild(child) { this.children.push(child); return child; }
  append(...children) { children.forEach((child) => this.appendChild(child)); }
  replaceChildren(...children) { this.children = [...children]; }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  addEventListener(name, listener) { this.listeners[name] = listener; }
  click() { if (!this.disabled && this.listeners.click) this.listeners.click(); }
}
const headChildren = [];
const document = {
  head: {appendChild(element) { headChildren.push(element); return element; }},
  createElement(tag) { return new Element(tag); },
  createElementNS(_namespace, tag) { return new Element(tag); },
  getElementById(id) { return headChildren.find((item) => item.id === id) || null; },
};
let renderer = null;
const window = {DuelGameUI: {register(gameType, candidate) {
  assert.equal(gameType, "chinese_checkers");
  renderer = candidate;
}}};
vm.runInNewContext(
  fs.readFileSync("app/static/games/chinese_checkers.js", "utf8"),
  {window, document, console, Math, Number, String, Boolean, Array, Map, Set}
);
assert.ok(renderer);
assert.equal(renderer.usesStandardMoveConfirmation, true);
assert.equal(headChildren.length, 1);
assert.equal(headChildren[0].href, "/static/games/chinese_checkers.css?v=0.1.17");

const state = JSON.parse(STATE_JSON);
const participants = JSON.parse(PARTICIPANTS_JSON);
function descendants(root) {
  const result = [];
  const visit = (node) => { result.push(node); node.children.forEach(visit); };
  root.children.forEach(visit);
  return result;
}
function createHarness(viewerId, canMove, currentPlayerId = null) {
  const board = new Element("div");
  const controls = new Element("div");
  const uiState = {};
  let selectedMove = null;
  let context = null;
  const helpers = {
    setBoardLayout(options) {
      board.style.setProperty("--cols", options.visualCols || options.cols);
      board.style.setProperty("--rows", options.visualRows || options.rows);
      board.setAttribute("aria-label", options.ariaLabel);
    },
    renderParticipantAvatar(target, participant) {
      target.textContent = `avatar:${participant.player_id}`;
      target.classList.toggle(
        "current-turn-avatar",
        participant.player_id === currentPlayerId
      );
    },
    canMove() { return canMove; },
    clearSelection({render = true} = {}) {
      selectedMove = null;
      Object.keys(uiState).forEach((key) => delete uiState[key]);
      if (render) helpers.rerender();
      return true;
    },
    rerender() {
      board.replaceChildren();
      controls.replaceChildren();
      context.pendingMove = selectedMove;
      renderer.renderBoard(context);
      renderer.renderControls(context);
      return true;
    },
    selectMove(payload) {
      selectedMove = {...payload};
      helpers.rerender();
      return true;
    },
  };
  context = {
    board, controls, state, participants, legalMoves: state.legal_moves, uiState, helpers,
    room: {
      room_id: "ROOM", revision: 7, viewer: {player_id: viewerId},
      current_player_id: currentPlayerId, status: "playing",
    },
    viewer: {player_id: viewerId}, canMove, pendingMove: null,
  };
  helpers.rerender();
  const hole = (nodeId) => descendants(board).find(
    (item) => item.classList.contains("cc-hole") && item.dataset.nodeId === nodeId
  );
  return {board, controls, uiState, helpers, hole, selectedMove: () => selectedMove};
}
''' + assertions
        harness = harness.replace("STATE_JSON", repr(state_json))
        harness = harness.replace("PARTICIPANTS_JSON", repr(participants_json))
        completed = subprocess.run(
            [NODE, "-e", harness],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_builds_121_holes_rotates_view_highlights_and_previews_path(self):
        self.run_node(r'''
const own = createHarness("human-1", true);
const holes = own.board.children.filter((item) => item.classList.contains("cc-hole"));
assert.equal(holes.length, 121);
assert.equal(own.board.dataset.rotationSteps, "3");
assert.ok(own.board.children[0].classList.contains("cc-star-surface"));
assert.equal(own.board.children.some(
  (item) => item.classList.contains("cc-playfield")
), false);
assert.equal(own.controls.children.length, 1);
assert.ok(own.controls.children[0].classList.contains("cc-legend"));
const viewerProgress = own.controls.children[0].children.find(
  (item) => item.classList.contains("viewer-progress")
);
assert.ok(viewerProgress);
assert.equal(viewerProgress.textContent, "目标营 0/10");
assert.equal(descendants(own.controls).some(
  (item) => item.classList.contains("cc-progress-row")
    || item.classList.contains("cc-progress-badge")
), false);
const ownStart = holes.filter((hole) => hole.classList.contains("viewer-start-camp"));
const ownTarget = holes.filter((hole) => hole.classList.contains("viewer-target-camp"));
assert.equal(ownStart.length, 10);
assert.equal(ownTarget.length, 10);
assert.ok(ownStart.every((hole) => Number(hole.dataset.displayR) >= 5));
assert.ok(ownTarget.every((hole) => Number(hole.dataset.displayR) <= -5));

const move = state.legal_moves[0];
assert.equal(own.hole(move.from).disabled, false);
assert.ok(own.hole(move.from).children.some((item) => item.classList.contains("cc-marble")));
own.hole(move.from).click();
assert.equal(own.uiState.selectedNode, move.from);
assert.equal(own.hole(move.to).classList.contains("jump-target"), true);
assert.equal(own.hole(move.to).disabled, false);
own.hole(move.to).click();
assert.deepEqual(own.selectedMove(), {from: move.from, to: move.to, kind: "jump"});
assert.equal(own.hole(move.to).classList.contains("selected-target"), true);
const preview = own.board.children.find(
  (item) => item.classList.contains("cc-path-preview")
);
assert.ok(preview);
assert.ok(preview.children.some((item) => item.tag === "polyline"));
assert.equal(own.board.children.filter(
  (item) => item.classList.contains("cc-hole")
).length, 121);
assert.equal(headChildren.length, 1);

const opposite = createHarness("ai-1", false);
assert.equal(opposite.board.dataset.rotationSteps, "0");
const oppositeHoles = descendants(opposite.board).filter(
  (item) => item.classList.contains("cc-hole")
);
assert.ok(
  oppositeHoles
    .filter((hole) => hole.classList.contains("viewer-start-camp"))
    .every((hole) => Number(hole.dataset.displayR) >= 5)
);
assert.ok(
  oppositeHoles
    .filter((hole) => hole.classList.contains("viewer-target-camp"))
    .every((hole) => Number(hole.dataset.displayR) <= -5)
);
''')

    def test_all_player_counts_keep_geometry_rotation_camps_and_seats_aligned(self):
        for player_count in (2, 3, 4, 6):
            participants = [
                {
                    "player_id": f"p-{index}",
                    "display_name": (
                        f"名字非常非常长的玩家-{index}"
                        if player_count in (4, 6)
                        else f"玩家{index}"
                    ),
                    "role": "human" if index == 0 else "ai",
                    "seat_index": index,
                    "token": f"P{index + 1}",
                    "participant_kind": (
                        "human" if index == 0 else "system_npc"
                    ),
                }
                for index in range(player_count)
            ]
            state = ChineseCheckers().initialize(participants)
            self.run_node(r'''
for (const participant of participants) {
  const harness = createHarness(
    participant.player_id, false, participants[0].player_id
  );
  const nodes = descendants(harness.board);
  const identities = nodes.filter((node) => node.classList.contains("cc-edge-participant"));
  const rotationSteps = Number(harness.board.dataset.rotationSteps);
  const expectedRotation = (
    3 - Number(state.start_camps_by_player[participant.player_id]) + 6
  ) % 6;
  assert.equal(rotationSteps, expectedRotation);

  if (participants.length > 2) {
    assert.ok(harness.board.classList.contains("multiplayer-board"));
    assert.equal(harness.board.children.length, 3);
    assert.ok(harness.board.children[0].classList.contains("cc-edge-roster"));
    assert.ok(harness.board.children[0].classList.contains("top"));
    assert.ok(harness.board.children[1].classList.contains("cc-playfield"));
    assert.ok(harness.board.children[2].classList.contains("cc-edge-roster"));
    assert.ok(harness.board.children[2].classList.contains("bottom"));
    assert.equal(identities.length, participants.length);
    const viewerIdentity = identities.find(
      (node) => node.dataset.playerId === participant.player_id
    );
    assert.equal(
      viewerIdentity.dataset.camp,
      String(state.start_camps_by_player[participant.player_id])
    );
    assert.equal(
      new Set(identities.map((node) => node.dataset.visualEdge)).size,
      participants.length
    );
    const currentIdentities = identities.filter(
      (identity) => identity.classList.contains("current")
    );
    assert.equal(currentIdentities.length, 1);
    assert.equal(currentIdentities[0].dataset.playerId, participants[0].player_id);
    const currentLabels = identities.flatMap(
      (identity) => descendants(identity).filter(
        (node) => node.classList.contains("cc-current-turn-label")
      )
    );
    assert.equal(currentLabels.length, 1);
    assert.equal(currentLabels[0].textContent, "行动中");
    assert.equal(
      descendants(currentIdentities[0]).includes(currentLabels[0]),
      true
    );
    assert.ok(identities
      .filter((identity) => identity !== currentIdentities[0])
      .every((identity) => descendants(identity).every(
        (node) => !node.classList.contains("cc-current-turn-label")
      )));
    assert.ok(
      currentIdentities[0].children[0].classList.contains("current-turn-avatar")
    );
    assert.ok(identities
      .filter((identity) => identity !== currentIdentities[0])
      .every((identity) => !identity.children[0].classList.contains(
        "current-turn-avatar"
      )));
    for (const identity of identities) {
      const seatedParticipant = participants.find(
        (candidate) => candidate.player_id === identity.dataset.playerId
      );
      assert.ok(seatedParticipant);
      assert.ok(identity.classList.contains(
        `seat-${seatedParticipant.seat_index}`
      ));
      const copy = identity.children[1];
      assert.equal(copy.children.length, 2);
      const name = copy.children[0];
      assert.ok(name.children[0].classList.contains("cc-edge-player-name"));
      assert.equal(name.children.length, identity === currentIdentities[0] ? 2 : 1);
      const meta = copy.children[1];
      assert.ok(meta.classList.contains("cc-edge-meta"));
      assert.equal(meta.children.length, 3);
      assert.ok(meta.children[0].classList.contains("cc-edge-progress"));
      assert.ok(meta.children[1].classList.contains("cc-edge-separator"));
      assert.ok(meta.children[2].classList.contains("cc-edge-start-camp"));
      assert.equal(
        meta.children.map((node) => node.textContent).join(" "),
        `目标${identity.dataset.targetProgress}/10 · 起始营${Number(identity.dataset.camp) + 1}`
      );
    }
    assert.equal(
      viewerIdentity.children[0].textContent,
      `avatar:${participant.player_id}`
    );
  } else {
    assert.equal(harness.board.classList.contains("multiplayer-board"), false);
    assert.equal(identities.length, 0);
    assert.equal(harness.board.children.length, 122);
  }

  const wrappedPlayfield = harness.board.children.find(
    (node) => node.classList.contains("cc-playfield")
  );
  if (participants.length > 2) assert.ok(wrappedPlayfield);
  else assert.equal(wrappedPlayfield, undefined);
  const playfield = wrappedPlayfield || harness.board;
  const holes = descendants(playfield).filter(
    (node) => node.classList.contains("cc-hole")
  );
  assert.equal(holes.length, 121);
  assert.equal(playfield.children.filter(
    (node) => node.classList.contains("cc-hole")
  ).length, 121);
  assert.ok(playfield.children[0].classList.contains("cc-star-surface"));
  assert.equal(descendants(playfield).some(
    (node) => node.classList.contains("cc-board-geometry")
      || node.classList.contains("cc-camp-wash")
  ), false);
  assert.ok(holes.every(
    (hole) => Number.parseFloat(hole.style["--node-left"]) >= 8
      && Number.parseFloat(hole.style["--node-left"]) <= 92
      && Number.parseFloat(hole.style["--node-top"]) >= 5
      && Number.parseFloat(hole.style["--node-top"]) <= 95
  ));
  const lefts = holes.map(
    (hole) => Number.parseFloat(hole.style["--node-left"])
  );
  const tops = holes.map(
    (hole) => Number.parseFloat(hole.style["--node-top"])
  );
  assert.equal(Math.min(...lefts), 8);
  assert.equal(Math.max(...lefts), 92);
  assert.equal(Math.min(...tops), 5);
  assert.equal(Math.max(...tops), 95);

  const viewerStart = holes.filter(
    (hole) => hole.classList.contains("viewer-start-camp")
  );
  const viewerTarget = holes.filter(
    (hole) => hole.classList.contains("viewer-target-camp")
  );
  assert.equal(viewerStart.length, 10);
  assert.equal(viewerTarget.length, 10);
  for (let camp = 0; camp < 6; camp += 1) {
    assert.equal(holes.filter(
      (hole) => hole.classList.contains(`camp-${camp}`)
    ).length, 10);
  }
  assert.ok(viewerStart.every((hole) => Number(hole.dataset.displayR) >= 5));
  assert.ok(viewerTarget.every((hole) => Number(hole.dataset.displayR) <= -5));

  assert.equal(harness.controls.children.length, 1);
  assert.ok(harness.controls.children[0].classList.contains("cc-legend"));
  const hasViewerProgress = harness.controls.children[0].children.some(
    (node) => node.classList.contains("viewer-progress")
  );
  assert.equal(hasViewerProgress, participants.length <= 2);
  assert.equal(descendants(harness.controls).some(
    (node) => node.classList.contains("cc-progress-row")
      || node.classList.contains("cc-progress-badge")
  ), false);

  for (const seatedParticipant of participants) {
    const marbles = descendants(playfield).filter(
      (node) => node.classList.contains("cc-marble")
        && node.dataset.owner === state.tokens_by_player[seatedParticipant.player_id]
    );
    assert.equal(marbles.length, 10);
    assert.ok(marbles.every(
      (node) => node.classList.contains(`seat-${seatedParticipant.seat_index}`)
    ));
  }
  assert.equal(
    descendants(playfield).some((node) => node.classList.contains("board-edge-participant")),
    false
  );
}
''', state=state, participants=participants)
        self.assertIn("grid-template-columns: repeat(3, minmax(0, 1fr));", STYLES)
        self.assertIn(".board.chinese_checkers .cc-playfield", STYLES)
        multiplayer_rule = STYLES[
            STYLES.index(".board.chinese_checkers.multiplayer-board {"):
        ]
        multiplayer_rule = multiplayer_rule[:multiplayer_rule.index("}")]
        self.assertIn("aspect-ratio: auto;", multiplayer_rule)
        self.assertIn("display: grid;", multiplayer_rule)
        self.assertIn("gap: 5px;", multiplayer_rule)
        self.assertNotIn("height: auto;", multiplayer_rule)
        self.assertIn(
            "grid-template-columns: minmax(0, 1fr);", multiplayer_rule
        )
        self.assertIn("grid-template-rows: auto auto auto;", multiplayer_rule)
        for viewport in (320, 375):
            board_width = min(viewport * (0.98 if viewport == 320 else 0.97), 365)
            self.assertLessEqual(board_width, viewport)

    def test_visual_camp_order_drives_three_four_and_six_player_rosters(self):
        for player_count in (3, 4, 6):
            canonical_participants = [
                {
                    "player_id": f"fixed-{index}",
                    "display_name": f"固定布局长名字玩家-{index}",
                    "role": "human" if index == 0 else "ai",
                    "seat_index": index,
                    "token": f"P{index + 1}",
                    "participant_kind": (
                        "human" if index == 0 else "system_npc"
                    ),
                }
                for index in range(player_count)
            ]
            state = ChineseCheckers().initialize(canonical_participants)
            self.run_node(r'''
function visualCampCenterForTest(camp, rotationSteps) {
  const positions = state.nodes
    .filter((node) => node.camp === camp)
    .map((node) => {
      let q = Number(node.q);
      let r = Number(node.r);
      for (let index = 0; index < rotationSteps; index += 1) {
        [q, r] = [-r, q + r];
      }
      const x = q + r / 2;
      const y = r * Math.sqrt(3) / 2;
      return {
        left: 8 + ((x + 6) / 12) * 84,
        top: 5 + ((y + 4 * Math.sqrt(3)) / (8 * Math.sqrt(3))) * 90,
      };
    });
  return positions.reduce(
    (total, position) => ({
      left: total.left + position.left / positions.length,
      top: total.top + position.top / positions.length,
    }),
    {left: 0, top: 0}
  );
}
function rowSlots(edge, count) {
  if (count === 1) return [`${edge}-2`];
  if (count === 2) return [`${edge}-1`, `${edge}-3`];
  return [`${edge}-1`, `${edge}-2`, `${edge}-3`];
}
for (const viewer of participants) {
  const harness = createHarness(viewer.player_id, false);
  const rotationSteps = Number(harness.board.dataset.rotationSteps);
  const top = harness.board.children[0];
  const bottom = harness.board.children[2];
  const cards = [...top.children, ...bottom.children];
  const expectedEntries = participants.map((participant) => {
    const camp = Number(state.start_camps_by_player[participant.player_id]);
    return {
      participant,
      camp,
      position: visualCampCenterForTest(camp, rotationSteps),
    };
  });
  const expectedTop = expectedEntries
    .filter((entry) => entry.position.top < 50)
    .sort((left, right) => left.position.left - right.position.left);
  const expectedBottom = expectedEntries
    .filter((entry) => entry.position.top >= 50)
    .sort((left, right) => left.position.left - right.position.left);
  if (participants.length === 3) {
    assert.deepEqual(
      [expectedTop.length, expectedBottom.length].sort(),
      [1, 2]
    );
  } else {
    assert.equal(expectedTop.length, participants.length / 2);
    assert.equal(expectedBottom.length, participants.length / 2);
  }
  assert.deepEqual(
    top.children.map((card) => card.dataset.playerId),
    expectedTop.map((entry) => entry.participant.player_id)
  );
  assert.deepEqual(
    bottom.children.map((card) => card.dataset.playerId),
    expectedBottom.map((entry) => entry.participant.player_id)
  );
  assert.deepEqual(
    top.children.map((card) => card.dataset.visualEdge),
    rowSlots("top", expectedTop.length)
  );
  assert.deepEqual(
    bottom.children.map((card) => card.dataset.visualEdge),
    rowSlots("bottom", expectedBottom.length)
  );
  const playfield = harness.board.children[1];
  cards.forEach((card) => {
    const seatedParticipant = participants.find(
      (participant) => participant.player_id === card.dataset.playerId
    );
    const slot = card.dataset.visualEdge;
    assert.ok(seatedParticipant);
    assert.equal(card.style["--edge-column"], slot.slice(-1));
    assert.equal(
      card.dataset.camp,
      String(state.start_camps_by_player[seatedParticipant.player_id])
    );
    assert.ok(card.classList.contains(`seat-${seatedParticipant.seat_index}`));
    const marbles = descendants(playfield).filter(
      (node) => node.classList.contains("cc-marble")
        && node.dataset.owner === state.tokens_by_player[seatedParticipant.player_id]
    );
    assert.equal(marbles.length, 10);
    assert.ok(marbles.every(
      (marble) => marble.classList.contains(`seat-${seatedParticipant.seat_index}`)
    ));
  });
}
''', state=state, participants=list(reversed(canonical_participants)))

        roster_rule = STYLES[STYLES.index(".cc-edge-roster {"):]
        roster_rule = roster_rule[:roster_rule.index("}")]
        self.assertIn(
            "grid-template-columns: repeat(3, minmax(0, 1fr));",
            roster_rule,
        )
        card_rule = STYLES[STYLES.index(".cc-edge-participant {"):]
        card_rule = card_rule[:card_rule.index("}")]
        self.assertIn("width: 100%;", card_rule)
        self.assertIn("max-width: 138px;", card_rule)
        self.assertIn("height: 44px;", card_rule)
        self.assertIn(
            '.cc-edge-participant[data-visual-edge$="-1"] '
            "{ justify-self: start; }",
            STYLES,
        )
        self.assertIn(
            '.cc-edge-participant[data-visual-edge$="-2"] '
            "{ justify-self: center; }",
            STYLES,
        )
        self.assertIn(
            '.cc-edge-participant[data-visual-edge$="-3"] '
            "{ justify-self: end; }",
            STYLES,
        )

    def test_each_player_card_computes_own_target_camp_progress(self):
        for player_count in (2, 3, 4, 6):
            participants = [
                {
                    "player_id": f"progress-{index}",
                    "display_name": f"进度玩家-{index}",
                    "role": "human" if index == 0 else "ai",
                    "seat_index": index,
                    "token": f"P{index + 1}",
                    "participant_kind": (
                        "human" if index == 0 else "system_npc"
                    ),
                }
                for index in range(player_count)
            ]
            state = ChineseCheckers().initialize(participants)
            state["pieces"] = {}
            expected_progress = {}
            for index, participant in enumerate(participants):
                player_id = participant["player_id"]
                progress = index + 1
                expected_progress[player_id] = progress
                token = state["tokens_by_player"][player_id]
                target_camp = state["target_camps_by_player"][player_id]
                target_nodes = state["camps"][str(target_camp)]
                for node_id in target_nodes[:progress]:
                    state["pieces"][node_id] = token
                foreign = participants[(index + 1) % player_count]
                state["pieces"][target_nodes[progress]] = (
                    state["tokens_by_player"][foreign["player_id"]]
                )
            # Deliberately wrong aggregate values ensure the renderer counts
            # each player's own pieces instead of reusing server/viewer progress.
            state["target_progress_by_player"] = {
                participant["player_id"]: 10 for participant in participants
            }
            expected_json = json.dumps(
                expected_progress,
                ensure_ascii=False,
                separators=(",", ":"),
            )
            assertions = r'''
const expectedProgress = JSON.parse(EXPECTED_PROGRESS_JSON);
for (const viewer of participants) {
  const harness = createHarness(viewer.player_id, false);
  assert.equal(descendants(harness.controls).some(
    (node) => node.classList.contains("cc-progress-row")
      || node.classList.contains("cc-progress-badge")
  ), false);
  if (participants.length <= 2) {
    const viewerProgress = descendants(harness.controls).find(
      (node) => node.classList.contains("viewer-progress")
    );
    assert.ok(viewerProgress);
    assert.equal(
      viewerProgress.textContent,
      `目标营 ${expectedProgress[viewer.player_id]}/10`
    );
    continue;
  }
  const cards = descendants(harness.board).filter(
    (node) => node.classList.contains("cc-edge-participant")
  );
  assert.equal(cards.length, participants.length);
  for (const card of cards) {
    const expected = expectedProgress[card.dataset.playerId];
    assert.equal(card.dataset.targetProgress, String(expected));
    const meta = card.children[1].children[1];
    assert.equal(meta.children[0].textContent, `目标${expected}/10`);
    assert.equal(meta.children[1].textContent, "·");
    assert.match(meta.children[2].textContent, /^起始营[1-6]$/);
  }
  assert.equal(descendants(harness.controls).some(
    (node) => node.classList.contains("viewer-progress")
  ), false);
}
'''.replace("EXPECTED_PROGRESS_JSON", repr(expected_json))
            self.run_node(assertions, state=state, participants=participants)

    def test_source_has_valid_javascript_syntax(self):
        completed = subprocess.run(
            [NODE, "--check", str(SCRIPT_PATH)],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)


if __name__ == "__main__":
    unittest.main()

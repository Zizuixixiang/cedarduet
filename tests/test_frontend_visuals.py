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


class FrontendBoardVisualTests(unittest.TestCase):
    def test_only_tictactoe_renders_raw_marks_as_text(self):
        board_cell = function_source("boardCell")
        self.assertIn(
            'if (room.game_type === "tictactoe") cell.textContent = mark || "";',
            board_cell,
        )
        self.assertEqual(SCRIPT.count("cell.textContent = mark"), 1)
        self.assertNotIn("box.textContent", function_source("renderDotsBoard"))

    def test_board_renderer_dispatches_by_game_type(self):
        renderer = function_source("renderBoard")
        self.assertIn("board.classList.add(room.game_type)", renderer)
        self.assertIn('room.game_type === "dots_boxes" ? 9 : rows', renderer)
        self.assertIn('room.game_type === "dots_boxes" ? 9 : cols', renderer)
        self.assertIn("renderGomokuBoard(board, state)", renderer)
        self.assertIn("renderConnect4Board(board, state)", renderer)
        self.assertIn("renderDotsBoard(board, state)", renderer)
        self.assertIn("renderJungleBoard(board, state)", renderer)
        self.assertIn("renderXiangqiBoard(board, state)", renderer)

    def test_gomoku_uses_intersections_stones_and_mobile_hit_cells(self):
        renderer = function_source("renderGomokuBoard")
        for edge_class in ("edge-top", "edge-bottom", "edge-left", "edge-right"):
            self.assertIn(edge_class, renderer)
        self.assertIn("star-point", renderer)
        self.assertIn("boardCell(mark", renderer)
        self.assertIn(".board.gomoku .cell::before", STYLES)
        self.assertIn(".board.gomoku .cell::after", STYLES)
        self.assertIn(".cell.edge-left::before { left: 50%; }", STYLES)
        self.assertIn(".cell.edge-top::after { top: 50%; }", STYLES)
        self.assertIn("touch-action: manipulation", STYLES)
        self.assertIn(".board.gomoku { width: min(88vw, 560px);", STYLES)

    def test_othello_and_connect4_use_character_free_discs(self):
        board_cell = function_source("boardCell")
        self.assertIn('["gomoku", "othello", "connect4"]', board_cell)
        self.assertIn('piece.className = "piece"', board_cell)
        self.assertIn(".board.othello .cell.mark-x .piece", STYLES)
        self.assertIn(".board.othello .cell.mark-o .piece", STYLES)
        self.assertIn(".board.connect4 .cell.human-piece .piece", STYLES)
        connect4 = function_source("renderConnect4Board")
        self.assertIn("rowIndex === landingRow", connect4)
        self.assertNotIn("textContent", connect4)

    def test_dots_boxes_ownership_is_visual_and_accessible(self):
        renderer = function_source("renderDotsBoard")
        self.assertIn('box.setAttribute("role", "img")', renderer)
        self.assertIn("格归${ownerDescription(owner)}所有", renderer)
        self.assertIn(".box.owned.human-piece", STYLES)
        self.assertIn(".box.owned.ai-piece", STYLES)
        self.assertIn("--rows: 9", STYLES)
        self.assertIn('content: "●"', STYLES)
        self.assertIn('content: "◆"', STYLES)

    def test_jungle_keeps_beast_names_without_raw_side_marks(self):
        renderer = function_source("renderJungleBoard")
        self.assertIn("JUNGLE_SYMBOLS[beast]", renderer)
        self.assertIn('pieceOwner === humanMark ? "●" : "○"', renderer)
        self.assertNotIn("cell.textContent = piece", renderer)

    def test_xiangqi_uses_server_targets_intersections_and_mobile_safe_board(self):
        renderer = function_source("renderXiangqiBoard")
        self.assertIn("state.legal_moves", renderer)
        self.assertIn('cell.classList.add(piece ? "legal-capture" : "legal-target")', renderer)
        self.assertIn("selectedXiangqiCell", renderer)
        self.assertIn("rowOrder.forEach((rowIndex, displayRow)", renderer)
        self.assertIn("colOrder.forEach((colIndex, displayCol)", renderer)
        self.assertIn('cell.dataset.moveRow = String(rowIndex)', renderer)
        self.assertIn('cell.dataset.displayRow = String(displayRow)', renderer)
        self.assertIn("XIANGQI_SYMBOLS[pieceColor][pieceType]", renderer)
        self.assertIn("XIANGQI_PALACE_LINES", renderer)
        self.assertIn("xiangqi-check-notice", renderer)
        self.assertIn("state.turn_color", renderer)
        self.assertIn(".board.xiangqi::before", STYLES)
        self.assertIn('content: "楚 河　　　　　汉 界"', STYLES)
        self.assertIn(".cell.river-top", STYLES)
        self.assertIn(".board.xiangqi .palace-diagonal.down-right", STYLES)
        self.assertIn(".board.xiangqi .xiangqi-piece.color-r", STYLES)
        self.assertIn(".board.xiangqi .xiangqi-piece.color-b", STYLES)
        xiangqi_styles = STYLES[
            STYLES.index(".board.xiangqi {"):
            STYLES.index(".board.gomoku .cell {")
        ]
        self.assertNotIn("rotate(180deg)", xiangqi_styles)
        mobile = STYLES[STYLES.index("@media (max-width: 599px)"):]
        self.assertIn(
            ".board.xiangqi { width: min(86vw, 480px); max-width: 100%;",
            mobile,
        )
        self.assertIn('value="xiangqi"', HTML)

    def test_xiangqi_last_move_and_history_use_authoritative_payloads(self):
        last_move = function_source("authoritativeLastMove")
        marker = function_source("renderLastMoveMarker")
        timeline = function_source("renderTimeline")
        self.assertIn('["jungle", "xiangqi"]', last_move)
        self.assertIn("fromRow = move.from_row", last_move)
        self.assertIn('origin.classList.add("last-move-origin")', marker)
        self.assertIn('target.classList.add("last-move-target")', marker)
        self.assertIn("event.move_label", timeline)
        self.assertNotIn("XIANGQI_SYMBOLS", timeline)
        self.assertIn(".board.xiangqi .cell.last-move-origin", STYLES)

    def test_multiplayer_roster_is_compact_and_seat_colored(self):
        self.assertIn(".room-participant {", STYLES)
        self.assertIn("width: 128px", STYLES)
        self.assertIn("min-height: 66px", STYLES)
        for seat in range(6):
            self.assertIn(f".seat-{seat} {{ --seat-color:", STYLES)
        self.assertIn(
            ".edge.drawn.participant-piece { background: var(--seat-color); }",
            STYLES,
        )
        self.assertIn(
            ".box.owned.participant-piece { background-color: var(--seat-color); }",
            STYLES,
        )
        badge = function_source("createParticipantBadge")
        self.assertIn("participant.game_metadata", badge)
        self.assertIn("dice_count: \"剩余骰子\"", badge)
        self.assertIn("score: \"得分\"", badge)
        self.assertIn("▶ 正在行动", badge)
        self.assertIn("你的席位", badge)
        self.assertNotIn("筹码", badge)

    def test_game_header_is_compact_semantic_and_hides_revision(self):
        header = HTML[
            HTML.index('<header class="game-header pixel-card">'):
            HTML.index('id="gameMessage"')
        ]
        primary = header[
            header.index('class="game-meta-line game-meta-primary"'):
            header.index('class="game-meta-line game-meta-secondary"')
        ]
        secondary = header[header.index('class="game-meta-line game-meta-secondary"'):]
        self.assertLess(primary.index('id="roomId"'), primary.index('id="status"'))
        self.assertLess(primary.index('id="status"'), primary.index('id="roomStake"'))
        self.assertLess(primary.index('id="roomId"'), primary.index('id="copyRoomButton"'))
        self.assertIn('id="copyRoomButton"', primary)
        self.assertIn("房间号", primary)
        self.assertIn("筹码", primary)
        self.assertIn('id="roundText"', secondary)
        self.assertIn('id="turn"', secondary)
        self.assertNotIn("REV", header)
        self.assertNotIn('id="revision"', header)
        render_game = function_source("renderGame")
        self.assertNotIn('$("revision")', render_game)
        self.assertIn("authoritativeRoundText(room)", render_game)
        self.assertIn('revision: room.revision', SCRIPT)

    def test_room_number_is_plain_text_with_subtle_accessible_copy_control(self):
        primary = HTML[
            HTML.index('class="game-meta-line game-meta-primary"'):
            HTML.index('class="game-meta-line game-meta-secondary"')
        ]
        copy_start = primary.index('<button id="copyRoomButton"')
        copy_end = primary.index("</button>", copy_start)
        copy_button = primary[copy_start:copy_end]
        self.assertNotIn('id="roomId"', copy_button)
        self.assertIn('type="button"', copy_button)
        self.assertIn('aria-label="复制房间号"', copy_button)
        self.assertIn('aria-hidden="true">复制</span>', copy_button)

        copy_styles = STYLES[
            STYLES.index(".room-copy-button {"):
            STYLES.index(".result-banner {")
        ]
        self.assertIn("background: transparent;", copy_styles)
        self.assertIn("border: 1px solid transparent;", copy_styles)
        self.assertNotIn("background: #fff;", copy_styles)
        self.assertIn(".room-copy-button:hover", copy_styles)
        self.assertIn(".room-copy-button:focus-visible", copy_styles)
        self.assertIn("outline: 2px solid var(--purple-dark);", copy_styles)
        self.assertIn(
            '$("copyRoomButton").setAttribute("aria-label", '
            "`复制房间号 ${room.room_id}`);",
            function_source("renderGame"),
        )

    def test_two_player_rows_remain_and_multiplayer_uses_compact_roster(self):
        self.assertIn('id="opponentRow" class="player-row opponent-row"', HTML)
        self.assertIn('id="humanRow" class="player-row human-row"', HTML)
        self.assertIn('id="viewerParticipantSlot"', HTML)
        self.assertIn(
            'id="viewerSpeech" class="speech-bubble human-speech viewer-speech hidden"',
            HTML,
        )
        players = function_source("renderPlayers")
        self.assertIn('participants.length > 2', players)
        self.assertIn('classList.toggle("hidden", multiplayer)', players)
        self.assertEqual(players.count("renderSpeechBubble"), 4)
        self.assertIn("const viewerPlayerId = viewerPlayerIdFor(room)", players)
        self.assertIn('bubble: $("viewerSpeech")', players)
        self.assertIn('bubble: $("sharedSpeech")', players)
        self.assertIn("{excludePlayerId: viewerPlayerId}", players)

    def test_ended_room_cards_have_a_prominent_non_hover_status(self):
        renderer = function_source("renderRooms")
        self.assertIn('card.className = `room-card${terminal ? " ended" : ""}`', renderer)
        self.assertIn('statusBadge.className = "room-status-badge pale"', renderer)
        self.assertIn("statusLabel(summary.status)", renderer)
        ended_style = STYLES[
            STYLES.index(".room-card.ended {"):
            STYLES.index(".room-card.ended::after")
        ]
        badge_style = STYLES[
            STYLES.index(".room-status-badge {"):
            STYLES.index(".room-status-badge.pale {")
        ]
        pale_start = STYLES.index(".room-status-badge.pale {")
        pale_style = STYLES[pale_start:STYLES.index("\n}", pale_start) + 2]
        self.assertIn("background:", ended_style)
        self.assertIn("border-color:", ended_style)
        self.assertIn("border: 2px solid var(--pink-dark);", badge_style)
        self.assertIn("font-weight: 800;", badge_style)
        self.assertIn("rgba(230, 168, 177, .22)", pale_style)
        mobile = STYLES[STYLES.index("@media (max-width: 599px)"):]
        self.assertIn(".room-status-badge {", mobile)

    def test_private_state_stays_below_the_table_and_legacy_rows(self):
        stage = HTML[HTML.index('<section class="battle-stage'):HTML.index("historyDrawerTab")]
        self.assertLess(stage.index('id="opponentRow"'), stage.index('id="tableLayout"'))
        self.assertLess(stage.index('id="tableLayout"'), stage.index('id="viewerParticipant"'))
        self.assertLess(stage.index('id="viewerParticipant"'), stage.index('id="humanRow"'))
        self.assertLess(stage.index('id="tableLayout"'), stage.index('id="humanRow"'))
        self.assertLess(stage.index('id="humanRow"'), stage.index('id="privateStatePanel"'))
        self.assertIn("我的信息 · PRIVATE", stage)

    def test_liars_dice_has_public_controls_private_dice_and_revision_guard(self):
        renderer = function_source("renderLiarsDice")
        self.assertIn('challenge.textContent = "质疑本轮上一手"', renderer)
        self.assertIn('chooseBid.textContent = "提交本轮叫点"', renderer)
        self.assertIn('bidLabel.textContent = "本轮当前叫点"', renderer)
        self.assertIn('bidValue.textContent = "轮到你叫点"', renderer)
        self.assertIn('`等待 ${starterName} 首叫`', renderer)
        self.assertIn('"本轮尚无叫点可质疑"', renderer)
        self.assertIn('outcome.round < roundNumber', renderer)
        self.assertIn('document.createElement("details")', renderer)
        self.assertIn('"查看上一轮揭骰"', renderer)
        self.assertIn("revealed_dice_by_player", renderer)
        self.assertIn(".liars-current-round {", STYLES)
        self.assertIn(".liars-round-result {", STYLES)
        self.assertIn("liarsRoundResultIsVisible(state)", renderer)
        self.assertIn("liarsRoundResultLines(outcome)", renderer)
        self.assertIn("`第 ${outcome.round} 轮结算`", renderer)
        self.assertNotIn("上一轮：", renderer)
        board_style = STYLES[
            STYLES.index(".board.liars_dice {"):
            STYLES.index(".liars-current-round {")
        ]
        result_style = STYLES[
            STYLES.index(".liars-round-result {"):
            STYLES.index(".liars-previous-round {")
        ]
        previous_style = STYLES[
            STYLES.index(".liars-previous-round {"):
            STYLES.index(".liars-result-title {")
        ]
        for style in (board_style, result_style, previous_style):
            self.assertNotRegex(style, r"(?m)^\s*(?:min-)?height\s*:")
        self.assertNotIn("dashed", result_style)
        self.assertIn("border: 1px solid var(--purple-light);", result_style)
        self.assertIn("align-self: start;", previous_style)
        private_renderer = function_source("renderPrivateState")
        self.assertIn('key === "dice"', private_renderer)
        self.assertIn("my-dice", private_renderer)
        confirm = SCRIPT[SCRIPT.index("async function confirmMove("):]
        self.assertIn("revision: room.revision", confirm)
        self.assertIn('["bid", "challenge"].includes(movePayload.action)', confirm)
        self.assertIn('value="liars_dice"', HTML)


@unittest.skipUnless(NODE, "node is required for multiplayer layout tests")
class MultiplayerTableRenderingTests(unittest.TestCase):
    def run_node(self, harness: str) -> None:
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

    def test_two_through_six_players_apply_stable_layout_classes(self):
        functions = "\n".join((
            function_source("participantLayoutClass"),
            function_source("applyParticipantLayout"),
        ))
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
  tableLayout: {{className: "", dataset: {{}}}},
  battleStage: {{dataset: {{}}}},
  sharedSpeechSlot: {{classList: new ClassList()}},
}};
const $ = (id) => elements[id];
{functions}
const expected = new Map([
  [2, "layout-duel"],
  [3, "layout-triangle"],
  [4, "layout-corners"],
  [5, "layout-top-row"],
  [6, "layout-top-row"],
]);
for (const [count, layout] of expected) {{
  applyParticipantLayout({{participants: Array.from({{length: count}}, () => ({{}}))}});
  assert.equal(elements.tableLayout.className, `table-layout ${{layout}} count-${{count}}`);
  assert.equal(elements.tableLayout.dataset.playerCount, String(count));
  assert.equal(elements.battleStage.dataset.playerCount, String(count));
  assert.equal(elements.sharedSpeechSlot.classList.contains("hidden"), count === 2);
}}
"""
        self.run_node(harness)

    def test_xiangqi_interaction_rotation_disabling_and_true_coordinates(self):
        functions = "\n".join((
            function_source("canHumanMove"),
            function_source("renderXiangqiBoard"),
            function_source("latestMoveEvent"),
            function_source("authoritativeLastMove"),
            function_source("renderLastMoveMarker"),
        ))
        harness = f"""
const assert = require("node:assert/strict");
class ClassList {{
  constructor(owner) {{ this.owner = owner; this.names = new Set(); }}
  reset(value) {{ this.names = new Set(value.split(/\\s+/).filter(Boolean)); }}
  sync() {{ this.owner._className = [...this.names].join(" "); }}
  add(...names) {{ names.forEach((name) => this.names.add(name)); this.sync(); }}
  toggle(name, force) {{
    if (force === undefined ? !this.names.has(name) : force) this.names.add(name);
    else this.names.delete(name);
    this.sync();
  }}
  contains(name) {{ return this.names.has(name); }}
}}
class Element {{
  constructor(tag = "div") {{
    this.tag = tag;
    this.children = [];
    this.dataset = {{}};
    this.attributes = {{}};
    this.listeners = {{}};
    this.disabled = false;
    this.textContent = "";
    this.ariaLabel = "";
    this._className = "";
    this.classList = new ClassList(this);
  }}
  set className(value) {{ this._className = value; this.classList.reset(value); }}
  get className() {{ return this._className; }}
  appendChild(child) {{ this.children.push(child); return child; }}
  setAttribute(name, value) {{ this.attributes[name] = value; }}
  addEventListener(name, callback) {{ this.listeners[name] = callback; }}
  click() {{ if (!this.disabled && this.listeners.click) this.listeners.click(); }}
  querySelector(selector) {{
    const row = selector.match(/data-move-row="(\\d+)"/);
    const col = selector.match(/data-move-col="(\\d+)"/);
    if (!row || !col) return null;
    return this.children.find((child) => (
      child.dataset.moveRow === row[1] && child.dataset.moveCol === col[1]
    )) || null;
  }}
}}
const document = {{createElement: (tag) => new Element(tag)}};
const XIANGQI_SYMBOLS = {{
  r: {{r: "车", n: "马", b: "相", a: "仕", k: "帅", c: "炮", p: "兵"}},
  b: {{r: "车", n: "马", b: "象", a: "士", k: "将", c: "炮", p: "卒"}},
}};
const XIANGQI_PALACE_LINES = new Map([
  ["0,3", ["down-right"]], ["0,5", ["down-left"]],
  ["1,4", ["down-left", "down-right"]],
  ["7,3", ["down-right"]], ["7,5", ["down-left"]],
  ["8,4", ["down-left", "down-right"]],
]);
const participantFor = () => ({{player_id: "human-1"}});
const pieceClass = () => "";
let selectedXiangqiCell = null;
let pendingMove = null;
let renders = 0;
let lastMoveMarkerKey = null;
const renderBoard = () => {{ renders += 1; }};
let room = {{
  room_id: "ROOM-XQ",
  revision: 3,
  game_type: "xiangqi",
  status: "playing",
  current_player_id: "human-1",
  viewer: {{player_id: "human-1"}},
  board_state: {{}},
}};
{functions}
const cellAt = (board, row, col) => board.children.find((cell) => (
  cell.dataset.moveRow === String(row) && cell.dataset.moveCol === String(col)
));
const position = (humanMark, color, originRow, targetRow) => {{
  const state = {{
    marks: {{human: humanMark, ai: humanMark === "X" ? "O" : "X"}},
    turn_color: color,
    in_check: false,
    board: Array.from({{length: 10}}, () => Array(9).fill(null)),
    legal_moves: [{{
      from_row: originRow, from_col: 0, to_row: targetRow, to_col: 0,
    }}],
  }};
  state.board[originRow][0] = `${{color}}:r`;
  state.board[originRow][1] = `${{color}}:n`;
  state.board[originRow][4] = `${{color}}:k`;
  state.board[targetRow][0] = `${{color === "r" ? "b" : "r"}}:r`;
  return state;
}};

const redState = position("X", "r", 9, 8);
let redBoard = new Element("board");
renderXiangqiBoard(redBoard, redState);
assert.equal(redBoard.children.length, 90);
assert.equal(redBoard.dataset.viewColor, "r");
assert.equal(redBoard.classList.contains("rotated-view"), false);
assert.deepEqual(
  [redBoard.children[0].dataset.moveRow, redBoard.children[0].dataset.moveCol],
  ["0", "0"]
);
cellAt(redBoard, 9, 0).click();
assert.deepEqual(selectedXiangqiCell, {{row: 9, col: 0}});
assert.equal(pendingMove, null);

redBoard = new Element("board");
renderXiangqiBoard(redBoard, redState);
assert.equal(cellAt(redBoard, 9, 0).classList.contains("selected-origin"), true);
assert.equal(cellAt(redBoard, 8, 0).classList.contains("legal-capture"), true);
cellAt(redBoard, 9, 1).click();
assert.deepEqual(selectedXiangqiCell, {{row: 9, col: 1}});
assert.equal(pendingMove, null);
redBoard = new Element("board");
renderXiangqiBoard(redBoard, redState);
cellAt(redBoard, 9, 0).click();
redBoard = new Element("board");
renderXiangqiBoard(redBoard, redState);
cellAt(redBoard, 8, 1).click();
assert.equal(pendingMove, null);
cellAt(redBoard, 8, 0).click();
assert.deepEqual(pendingMove, {{
  from_row: 9, from_col: 0, to_row: 8, to_col: 0,
}});

selectedXiangqiCell = null;
pendingMove = null;
const blackState = position("O", "b", 0, 1);
let blackBoard = new Element("board");
renderXiangqiBoard(blackBoard, blackState);
assert.equal(blackBoard.children.length, 90);
assert.equal(blackBoard.dataset.viewColor, "b");
assert.equal(blackBoard.classList.contains("rotated-view"), true);
assert.deepEqual(
  [blackBoard.children[0].dataset.moveRow, blackBoard.children[0].dataset.moveCol],
  ["9", "8"]
);
assert.deepEqual(
  [blackBoard.children[89].dataset.moveRow, blackBoard.children[89].dataset.moveCol],
  ["0", "0"]
);
assert.deepEqual(
  [cellAt(blackBoard, 0, 0).dataset.displayRow, cellAt(blackBoard, 0, 0).dataset.displayCol],
  ["9", "8"]
);
assert.equal(cellAt(blackBoard, 0, 0).children.at(-1).textContent, "车");
cellAt(blackBoard, 0, 0).click();
blackBoard = new Element("board");
renderXiangqiBoard(blackBoard, blackState);
assert.equal(cellAt(blackBoard, 1, 0).classList.contains("legal-capture"), true);
cellAt(blackBoard, 1, 0).click();
assert.deepEqual(pendingMove, {{
  from_row: 0, from_col: 0, to_row: 1, to_col: 0,
}});

selectedXiangqiCell = null;
pendingMove = null;
room.board_state.last_move = {{
  from_row: 0, from_col: 0, to_row: 1, to_col: 0,
}};
blackBoard = new Element("board");
renderXiangqiBoard(blackBoard, blackState);
renderLastMoveMarker(blackBoard, []);
assert.equal(cellAt(blackBoard, 0, 0).classList.contains("last-move-origin"), true);
assert.equal(cellAt(blackBoard, 1, 0).classList.contains("last-move-target"), true);
assert.match(cellAt(blackBoard, 0, 0).ariaLabel, /上一手起点/);
assert.match(cellAt(blackBoard, 1, 0).ariaLabel, /上一手终点/);

blackState.in_check = true;
blackBoard = new Element("board");
renderXiangqiBoard(blackBoard, blackState);
const notice = blackBoard.children.find((child) => (
  child.classList.contains("xiangqi-check-notice")
));
assert.equal(notice.textContent, "黑方 · 将军");
assert.equal(cellAt(blackBoard, 0, 4).classList.contains("in-check"), true);

room.current_player_id = "ai-1";
blackBoard = new Element("board");
renderXiangqiBoard(blackBoard, blackState);
assert.equal(blackBoard.children.filter((child) => child.tag === "button").every(
  (cell) => cell.disabled
), true);
room.current_player_id = "human-1";
room.status = "finished";
blackBoard = new Element("board");
renderXiangqiBoard(blackBoard, blackState);
assert.equal(blackBoard.children.filter((child) => child.tag === "button").every(
  (cell) => cell.disabled
), true);
"""
        self.run_node(harness)

    def test_only_liars_dice_uses_authoritative_round_number(self):
        rounds = function_source("authoritativeRoundText")
        harness = f"""
const assert = require("node:assert/strict");
{rounds}
assert.equal(authoritativeRoundText({{
  game_type: "liars_dice",
  revision: 91,
  board_state: {{flow: {{round_number: 4}}}},
}}), "第 4 轮");
assert.equal(authoritativeRoundText({{
  game_type: "liars_dice", revision: 91, board_state: {{flow: {{}}}},
}}), "");
assert.equal(authoritativeRoundText({{
  game_type: "liars_dice", revision: 7,
  board_state: {{flow: {{round_number: "7"}}}},
}}), "");
assert.equal(authoritativeRoundText({{
  game_type: "tictactoe", revision: 23,
  board_state: {{flow: {{round_number: 23}}}},
}}), "");
assert.equal(authoritativeRoundText({{
  game_type: "gomoku", revision: 12, board_state: {{}},
}}), "");
"""
        self.run_node(harness)

    def test_room_list_marks_finished_cards_with_an_always_visible_badge(self):
        renderer = function_source("renderRooms")
        harness = f"""
const assert = require("node:assert/strict");
class Element {{
  constructor(tagName) {{
    this.tagName = tagName.toUpperCase();
    this.children = [];
    this.className = "";
    this.textContent = "";
    this.attributes = {{}};
  }}
  replaceChildren(...children) {{ this.children = children; }}
  appendChild(child) {{ this.children.push(child); return child; }}
  append(...children) {{ this.children.push(...children); }}
  setAttribute(name, value) {{ this.attributes[name] = value; }}
  addEventListener() {{}}
}}
const list = new Element("div");
const $ = (id) => id === "roomList" ? list : null;
const document = {{createElement: (tagName) => new Element(tagName)}};
const GAME_GLYPHS = {{gomoku: "五"}};
const statusLabel = (status) => ({{
  pending: "待确认", waiting: "等待加入", playing: "对局中",
  finished: "已结束", archived: "已归档",
}})[status] || status;
const relativeTime = () => "刚刚";
const turnLabel = () => "轮到你";
const isTerminal = (room) => ["finished", "archived"].includes(room.status);
const retentionTextFor = () => "7 天后自动删除";
const retentionDeadlineTitle = () => "";
const updateRoomPreservation = () => {{}};
const deleteRoom = () => {{}};
const openRoom = () => {{}};
{renderer}
const base = {{
  game_type: "gomoku", game_name: "五子棋", ai_name: "小机一号",
  stake: 0, stake_label: "娱乐局", updated_at: "2026-08-29T00:00:00Z",
  preserved: false,
}};
renderRooms([
  {{...base, room_id: "END-1", status: "finished", winner: "draw"}},
  {{...base, room_id: "PLAY-1", status: "playing", winner: null}},
  {{...base, room_id: "WAIT-1", status: "waiting", winner: null}},
]);
assert.equal(list.children.length, 3);
const [endedCard, playingCard, waitingCard] = list.children;
assert.equal(endedCard.className, "room-card ended");
assert.equal(playingCard.className, "room-card");
assert.equal(waitingCard.className, "room-card");

const endedOpen = endedCard.children[0];
assert.match(endedOpen.attributes["aria-label"], /进入已结束的五子棋房间 END-1/);
const endedState = endedOpen.children[2];
assert.equal(endedState.children[0].className, "room-status-badge pale");
assert.equal(endedState.children[0].textContent, "已结束 · 和棋");
assert.equal(endedState.children[1].textContent, "进入 →");
assert.ok(!endedState.children.some((child) => child.className === "turn"));

const playingState = playingCard.children[0].children[2];
assert.equal(playingState.children[0].className, "turn");
assert.equal(playingState.children[0].textContent, "轮到你");
assert.ok(!playingState.children.some(
  (child) => child.className === "room-status-badge pale"
));
const waitingState = waitingCard.children[0].children[2];
assert.equal(waitingState.children[0].textContent, "等待加入");
"""
        self.run_node(harness)

    def test_liars_dice_separates_new_round_from_collapsed_previous_reveal(self):
        renderer = "\n".join((
            function_source("liarsParticipantName"),
            function_source("liarsRoundResultIsVisible"),
            function_source("liarsRoundResultLines"),
            function_source("liarsBidSelectionIsLegal"),
            function_source("defaultLiarsBidSelection"),
            function_source("liarsBidSelectionFor"),
            function_source("rememberLiarsBidSelection"),
            function_source("renderLiarsDice"),
        ))
        harness = f"""
const assert = require("node:assert/strict");
class Element {{
  constructor(tagName) {{
    this.tagName = tagName.toUpperCase();
    this.children = [];
    this.className = "";
    this.textContent = "";
    this.open = false;
    this.value = "";
  }}
  appendChild(child) {{ this.children.push(child); return child; }}
  append(...children) {{ this.children.push(...children); }}
  addEventListener() {{}}
}}
const document = {{createElement: (tagName) => new Element(tagName)}};
const participants = new Map([
  ["human-1", {{display_name: "人类一号"}}],
  ["ai-1", {{display_name: "小机一号"}}],
]);
const participantByPlayerId = (playerId) => participants.get(playerId) || null;
let liarsBidDraft = null;
let humanTurn = true;
const canHumanMove = () => humanTurn;
let room = {{
  room_id: "ROOM-1", revision: 2, status: "playing",
  current_player_id: "human-1",
  current_actor: {{player_id: "human-1", display_name: "人类一号"}},
}};
const selectMove = () => {{}};
const allText = (node) => [
  node.textContent,
  ...node.children.map(allText),
].filter(Boolean).join(" ");
{renderer}
const postChallengeState = {{
  flow: {{phase: "bidding", round_number: 2}},
  max_bid_quantity: 9,
  current_bid: null,
  last_round_result: {{
    round: 1,
    bid: {{quantity: 3, face: 6, bidder_player_id: "ai-1"}},
    bidder_player_id: "ai-1",
    challenger_player_id: "human-1",
    actual_count: 2,
    bid_holds: false,
    loser_player_id: "ai-1",
    loser_remaining_dice: 4,
    eliminated: false,
    eliminated_player_id: null,
    next_round: 2,
    next_starter_player_id: "ai-1",
    revealed_dice_by_player: {{"human-1": [1, 1, 2], "ai-1": [3, 4, 5, 6, 6]}},
  }},
}};
const board = new Element("div");
renderLiarsDice(board, postChallengeState);
assert.equal(board.children.length, 2);
const [currentRound, previousRound] = board.children;
assert.equal(currentRound.className, "liars-current-round");
assert.match(allText(currentRound), /第 2 轮 · 当前轮/);
assert.match(allText(currentRound), /本轮骰子已按剩余数量重新掷出并隐藏/);
assert.match(allText(currentRound), /本轮当前叫点/);
assert.match(allText(currentRound), /轮到你叫点/);
assert.match(allText(currentRound), /现在叫点/);
assert.match(allText(currentRound), /本轮尚无叫点可质疑/);
assert.doesNotMatch(allText(currentRound), /等待本轮首叫/);
assert.doesNotMatch(allText(currentRound), /人类一号：1 · 1 · 2/);
const humanControls = currentRound.children[2];
assert.equal(humanControls.children[2].disabled, false);
assert.equal(humanControls.children[3].disabled, true);

humanTurn = false;
room = {{
  room_id: "ROOM-1", revision: 2, status: "playing",
  current_player_id: "ai-1",
  current_actor: {{player_id: "ai-1", display_name: "小机一号"}},
}};
const waitingBoard = new Element("div");
renderLiarsDice(waitingBoard, postChallengeState);
const waitingRound = waitingBoard.children[0];
assert.match(allText(waitingRound), /等待 小机一号 首叫/);
assert.match(allText(waitingRound), /等待首叫/);
assert.doesNotMatch(allText(waitingRound), /轮到你叫点/);
const waitingControls = waitingRound.children[2];
assert.equal(waitingControls.children[2].disabled, true);
assert.equal(waitingControls.children[3].disabled, true);

humanTurn = true;
room.current_player_id = "human-1";
room.current_actor = {{player_id: "human-1", display_name: "人类一号"}};
const currentBidBoard = new Element("div");
renderLiarsDice(currentBidBoard, {{
  ...postChallengeState,
  current_bid: {{quantity: 3, face: 4, bidder_player_id: "ai-1"}},
}});
const currentBidRound = currentBidBoard.children[0];
assert.match(allText(currentBidRound), /3 个 4 点 · 小机一号/);
assert.match(allText(currentBidRound), /质疑本轮上一手/);
assert.doesNotMatch(allText(currentBidRound), /本轮尚无叫点可质疑/);
assert.equal(currentBidRound.children[2].children[3].disabled, false);

assert.match(previousRound.className, /liars-round-result/);
assert.match(previousRound.className, /liars-previous-round/);
assert.equal(previousRound.children[0].textContent, "第 1 轮结算");
assert.equal(previousRound.children[1].textContent, "实际 2 个 6 点 · 叫点失败");
assert.equal(previousRound.children[2].textContent, "小机一号 -1 骰 · 剩余 4");
assert.equal(previousRound.children.length, 4);
const compactText = previousRound.children.slice(0, 3)
  .map((child) => child.textContent).join(" ");
assert.doesNotMatch(compactText, /上一轮|质疑|由 .* 开叫/);
const details = previousRound.children.find((child) => child.tagName === "DETAILS");
assert.equal(details.tagName, "DETAILS");
assert.equal(details.open, false);
assert.equal(details.children[0].textContent, "查看上一轮揭骰");
assert.match(allText(details), /人类一号：1 · 1 · 2/);

const eliminatedBoard = new Element("div");
renderLiarsDice(eliminatedBoard, {{
  flow: {{phase: "bidding", round_number: 3}},
  max_bid_quantity: 5,
  current_bid: null,
  last_round_result: {{
    round: 2,
    bid: {{quantity: 2, face: 3, bidder_player_id: "ai-1"}},
    bidder_player_id: "ai-1",
    challenger_player_id: "human-1",
    actual_count: 0,
    bid_holds: false,
    loser_player_id: "ai-1",
    loser_remaining_dice: 0,
    eliminated: true,
    eliminated_player_id: "ai-1",
    revealed_dice_by_player: {{"human-1": [4, 5, 6], "ai-1": [2]}},
  }},
}});
assert.equal(eliminatedBoard.children[1].children[2].textContent, "小机一号 -1 骰 · 已淘汰");

const afterOpeningBid = new Element("div");
renderLiarsDice(afterOpeningBid, {{
  flow: {{phase: "bidding", round_number: 3}},
  max_bid_quantity: 5,
  current_bid: {{quantity: 1, face: 2, bidder_player_id: "ai-1"}},
  last_round_result: {{round: 2, bid: {{quantity: 2, face: 3}}}},
}});
assert.equal(afterOpeningBid.children.length, 1);
"""
        self.run_node(harness)

    def test_room_number_copy_uses_clipboard_and_reports_result(self):
        copy_room = "async " + function_source("copyRoomNumber")
        harness = f"""
const assert = require("node:assert/strict");
const messages = [];
const toast = (message) => messages.push(message);
{copy_room}
(async () => {{
  const copied = [];
  assert.equal(await copyRoomNumber(
    {{room_id: "ROOM-42"}}, {{writeText: async (value) => copied.push(value)}}
  ), true);
  assert.deepEqual(copied, ["ROOM-42"]);
  assert.equal(messages.at(-1), "房间号已复制");
  assert.equal(await copyRoomNumber(
    {{room_id: "ROOM-43"}}, {{writeText: async () => {{ throw new Error("denied"); }}}}
  ), false);
  assert.match(messages.at(-1), /ROOM-43/);
}})().catch((error) => {{ console.error(error); process.exitCode = 1; }});
"""
        self.run_node(harness)

    def test_three_and_four_player_tables_put_viewer_in_bottom_slot(self):
        functions = "\n".join((
            function_source("viewerParticipantFor"),
            function_source("tableParticipantsFor"),
        ))
        harness = f"""
const assert = require("node:assert/strict");
{functions}
for (const count of [3, 4]) {{
  const participants = Array.from({{length: count}}, (_, index) => ({{
    player_id: index === 0 ? "me" : `p-${{index}}`, seat_index: index,
  }}));
  const ordered = tableParticipantsFor({{
    viewer: {{player_id: "me"}}, participants,
  }});
  assert.equal(ordered.length, count);
  assert.equal(ordered.at(-1).player_id, "me");
  assert.deepEqual(
    ordered.slice(0, -1).map((item) => item.player_id),
    participants.slice(1).map((item) => item.player_id)
  );
}}
"""
        self.run_node(harness)
        self.assertIn(
            ".table-layout.count-3 .room-participant:nth-child(3) {\n"
            "  grid-column: 3;\n  grid-row: 3;",
            STYLES,
        )
        self.assertIn(
            ".table-layout.count-4 .room-participant:nth-child(4) {\n"
            "  grid-column: 3;\n  grid-row: 2;",
            STYLES,
        )

    def test_game_options_are_rebuilt_from_catalog_player_counts(self):
        functions = "\n".join((
            function_source("allowedPlayerCountsForGame"),
            function_source("gamePlayerCountLabel"),
            function_source("syncGameTypeOptions"),
        ))
        harness = f"""
const assert = require("node:assert/strict");
class Option {{ constructor() {{ this.value = ""; this.textContent = ""; }} }}
class Select {{
  constructor() {{ this.children = []; this.value = "dots"; }}
  get options() {{ return this.children; }}
  replaceChildren() {{ this.children = []; }}
  appendChild(child) {{ this.children.push(child); }}
}}
const select = new Select();
const document = {{createElement: () => new Option()}};
const $ = (id) => {{ assert.equal(id, "gameType"); return select; }};
{functions}
syncGameTypeOptions([
  {{game_type: "duel", display_name: "双人棋", allowed_player_counts: [2]}},
  {{game_type: "dots", display_name: "点格棋", allowed_player_counts: [2, 3, 4]}},
  {{game_type: "discrete", display_name: "离散桌", allowed_player_counts: [2, 4]}},
]);
assert.deepEqual(
  select.options.map((option) => option.textContent),
  ["双人棋 / 2人", "点格棋 / 2–4人", "离散桌 / 2、4人"]
);
assert.equal(select.value, "dots");
"""
        self.run_node(harness)
        game_select = HTML[
            HTML.index('<select id="gameType">'):
            HTML.index("</select>", HTML.index('<select id="gameType">'))
        ]
        for board_size in ("3×3", "15×15", "8×8", "7×6", "5×5", "7×9"):
            self.assertNotIn(board_size, game_select)
        self.assertIn("井字棋 / 2人", game_select)
        self.assertIn("点格棋 / 2–4人", game_select)
        self.assertIn("吹牛骰子 / 2–6人", game_select)
        loader = function_source("loadIdentity")
        self.assertIn("syncGameTypeOptions(data.games || [])", loader)
        self.assertLess(
            loader.index("syncGameTypeOptions(data.games || [])"),
            loader.index("syncMachinePicker(data.machines || [])"),
        )

    def test_roster_marks_current_actor_and_keeps_game_values_compact(self):
        functions = "\n".join((
            function_source("participantAvatarFallback"),
            function_source("renderParticipantAvatar"),
            function_source("viewerParticipantFor"),
            function_source("tableParticipantsFor"),
            function_source("createParticipantBadge"),
            function_source("renderParticipantRoster"),
        ))
        harness = f"""
const assert = require("node:assert/strict");
class ClassList {{
  constructor(owner) {{ this.owner = owner; this.names = new Set(); }}
  reset(value) {{ this.names = new Set(value.split(/\\s+/).filter(Boolean)); }}
  sync() {{ this.owner._className = [...this.names].join(" "); }}
  add(...names) {{ names.forEach((name) => this.names.add(name)); this.sync(); }}
  remove(...names) {{ names.forEach((name) => this.names.delete(name)); this.sync(); }}
  toggle(name, force) {{
    if (force === undefined ? !this.names.has(name) : force) this.names.add(name);
    else this.names.delete(name);
    this.sync();
  }}
  contains(name) {{ return this.names.has(name); }}
  [Symbol.iterator]() {{ return this.names[Symbol.iterator](); }}
}}
class Element {{
  constructor(tag) {{
    this.tag = tag;
    this.children = [];
    this.attributes = {{}};
    this.textContent = "";
    this.classList = new ClassList(this);
    this._className = "";
  }}
  set className(value) {{ this._className = value; this.classList.reset(value); }}
  get className() {{ return this._className; }}
  replaceChildren(...children) {{ this.children = children; this.textContent = ""; }}
  append(...children) {{ this.children.push(...children); }}
  appendChild(child) {{ this.children.push(child); }}
  setAttribute(name, value) {{ this.attributes[name] = value; }}
  addEventListener() {{}}
}}
const roster = new Element("div");
roster.className = "room-participants hidden count-2";
const viewerSlot = new Element("div");
viewerSlot.className = "viewer-participant-slot hidden";
const document = {{createElement: (tag) => new Element(tag)}};
const $ = (id) => ({{roomParticipants: roster, viewerParticipant: viewerSlot}})[id];
const apiPath = (path) => path;
{functions}
renderParticipantRoster({{
  current_player_id: "p2",
  viewer: {{player_id: "p1"}},
  participants: [
    {{player_id: "p1", seat_index: 0, display_name: "甲", role: "human", participant_kind: "human", active: true, activity_state: "active", join_status: "joined", confirmation_status: "accepted", game_metadata: {{score: 2}}}},
    {{player_id: "p2", seat_index: 1, display_name: "乙", role: "ai", participant_kind: "bound_machine", active: true, activity_state: "active", join_status: "joined", confirmation_status: "accepted", game_metadata: {{dice_count: 4}}}},
    {{player_id: "p3", seat_index: 2, display_name: "丙", role: "ai", participant_kind: "system_npc", active: false, activity_state: "eliminated", join_status: "joined", confirmation_status: "accepted", game_metadata: {{dice_count: 0}}}},
  ],
}});
assert.equal(roster.children.length, 3);
assert.deepEqual(
  roster.children.map((item) => item.children[1].children[0].textContent),
  ["乙", "丙", "甲（你）"]
);
assert.match(roster.children[2].className, /seat-0/);
assert.ok(roster.children[2].classList.contains("viewer"));
assert.match(roster.children[2].children[2].textContent, /你的席位/);
const current = roster.children[0];
assert.match(current.className, /seat-1/);
assert.ok(current.classList.contains("current"));
assert.equal(current.attributes["aria-current"], "true");
assert.match(current.children[2].textContent, /▶ 正在行动/);
assert.match(current.children[2].textContent, /剩余骰子 4/);
assert.equal(current.children[0].textContent, "乙");
assert.match(roster.children[1].children[2].textContent, /已淘汰/);
assert.ok(viewerSlot.classList.contains("hidden"));
"""
        self.run_node(harness)

    def test_five_and_six_player_top_rows_exclude_viewer(self):
        functions = "\n".join((
            function_source("participantAvatarFallback"),
            function_source("renderParticipantAvatar"),
            function_source("viewerParticipantFor"),
            function_source("tableParticipantsFor"),
            function_source("createParticipantBadge"),
            function_source("renderParticipantRoster"),
        ))
        harness = f"""
const assert = require("node:assert/strict");
class ClassList {{
  constructor(owner) {{ this.owner = owner; this.names = new Set(); }}
  reset(value) {{ this.names = new Set(value.split(/\\s+/).filter(Boolean)); }}
  sync() {{ this.owner._className = [...this.names].join(" "); }}
  add(...names) {{ names.forEach((name) => this.names.add(name)); this.sync(); }}
  remove(...names) {{ names.forEach((name) => this.names.delete(name)); this.sync(); }}
  toggle(name, force) {{
    if (force === undefined ? !this.names.has(name) : force) this.names.add(name);
    else this.names.delete(name);
    this.sync();
  }}
  contains(name) {{ return this.names.has(name); }}
  [Symbol.iterator]() {{ return this.names[Symbol.iterator](); }}
}}
class Element {{
  constructor(tag) {{
    this.tag = tag; this.children = []; this.attributes = {{}};
    this.textContent = ""; this.classList = new ClassList(this); this._className = "";
  }}
  set className(value) {{ this._className = value; this.classList.reset(value); }}
  get className() {{ return this._className; }}
  replaceChildren(...children) {{ this.children = children; this.textContent = ""; }}
  append(...children) {{ this.children.push(...children); }}
  appendChild(child) {{ this.children.push(child); }}
  setAttribute(name, value) {{ this.attributes[name] = value; }}
  addEventListener() {{}}
}}
const document = {{createElement: (tag) => new Element(tag)}};
const apiPath = (path) => path;
const roster = new Element("div");
const viewerSlot = new Element("div");
const $ = (id) => ({{roomParticipants: roster, viewerParticipant: viewerSlot}})[id];
{functions}
for (const count of [5, 6]) {{
  roster.className = "room-participants hidden count-2";
  viewerSlot.className = "viewer-participant-slot hidden";
  const participants = Array.from({{length: count}}, (_, index) => ({{
    player_id: index === 0 ? "me" : `p-${{index}}`,
    seat_index: index,
    display_name: index === 0 ? "南山" : `玩家${{index}}`,
    role: index === 0 ? "human" : "ai",
    participant_kind: index === 0 ? "human" : "system_npc",
    active: true,
    activity_state: "active",
    join_status: "joined",
    confirmation_status: "accepted",
    game_metadata: {{dice_count: 5}},
  }}));
  renderParticipantRoster({{
    viewer: {{player_id: "me"}}, current_player_id: "me", participants,
  }});
  assert.equal(roster.children.length, count - 1);
  assert.ok(roster.children.every(
    (item) => item.children[1].children[0].textContent !== "南山（你）"
  ));
  assert.equal(viewerSlot.children.length, 1);
  assert.equal(viewerSlot.children[0].children[1].children[0].textContent, "南山（你）");
  assert.ok(viewerSlot.children[0].classList.contains("current"));
  assert.ok(!viewerSlot.classList.contains("hidden"));
}}
"""
        self.run_node(harness)

    def test_shared_bubble_updates_speaker_avatar_message_and_seat_color(self):
        functions = "\n".join((
            function_source("participantByPlayerId"),
            function_source("participantAvatarFallback"),
            function_source("renderParticipantAvatar"),
            function_source("speechSenderRole"),
            function_source("speechSenderPlayerId"),
            function_source("latestSpeechEvent"),
            function_source("renderSpeechBubble"),
        ))
        harness = f"""
const assert = require("node:assert/strict");
class ClassList {{
  constructor() {{ this.names = new Set(["speech-bubble", "shared-speech", "empty"]); }}
  add(...names) {{ names.forEach((name) => this.names.add(name)); }}
  remove(...names) {{ names.forEach((name) => this.names.delete(name)); }}
  toggle(name, force) {{
    if (force === undefined ? !this.names.has(name) : force) this.names.add(name);
    else this.names.delete(name);
  }}
  contains(name) {{ return this.names.has(name); }}
  [Symbol.iterator]() {{ return this.names[Symbol.iterator](); }}
}}
class Element {{
  constructor() {{ this.classList = new ClassList(); this.textContent = ""; this.attributes = {{}}; this.children = []; }}
  replaceChildren(...children) {{ this.children = children; this.textContent = ""; }}
  setAttribute(name, value) {{ this.attributes[name] = value; }}
  addEventListener() {{}}
}}
const document = {{createElement: () => new Element()}};
const apiPath = (path) => path;
let room = {{participants: [
  {{player_id: "p1", seat_index: 0, display_name: "甲"}},
  {{player_id: "p2", seat_index: 1, display_name: "乙"}},
  {{player_id: "p3", seat_index: 2, display_name: "丙"}},
]}};
{functions}
const bubble = new Element();
const textTarget = new Element();
const nameTarget = new Element();
const avatarTarget = new Element();
const events = [
  {{event_type: "message", text: "第一句", sender_role: "human", sender: {{player_id: "p1", name: "甲", role: "human", seat: 0}}}},
  {{event_type: "message", text: "第二句", sender_role: "ai", sender: {{player_id: "p2", name: "乙", role: "ai", seat: 1}}}},
  {{event_type: "result", text: "不应进入气泡", sender_role: "system", sender: {{player_id: "system", name: "裁判", role: "system"}}}},
];
renderSpeechBubble({{bubble, event: latestSpeechEvent(events), textTarget, nameTarget, avatarTarget, reserveSpace: true}});
assert.equal(nameTarget.textContent, "乙");
assert.equal(textTarget.textContent, "第二句");
assert.equal(avatarTarget.textContent, "乙");
assert.ok(bubble.classList.contains("seat-1"));
assert.ok(!bubble.classList.contains("empty"));
events.push({{event_type: "move", text: "连续更新", sender_role: "ai", sender: {{player_id: "p3", name: "丙", role: "ai", seat: 2}}}});
renderSpeechBubble({{bubble, event: latestSpeechEvent(events), textTarget, nameTarget, avatarTarget, reserveSpace: true}});
assert.equal(nameTarget.textContent, "丙");
assert.equal(textTarget.textContent, "连续更新");
assert.ok(bubble.classList.contains("seat-2"));
assert.ok(!bubble.classList.contains("seat-1"));
"""
        self.run_node(harness)

    def test_multiplayer_speech_routes_by_viewer_player_id(self):
        functions = "\n".join((
            function_source("renderPlayers"),
            function_source("speechSenderRole"),
            function_source("speechSenderPlayerId"),
            function_source("latestSpeechEvent"),
            function_source("viewerPlayerIdFor"),
        ))
        harness = f"""
const assert = require("node:assert/strict");
class ClassList {{
  constructor() {{ this.names = new Set(["hidden"]); }}
  toggle(name, force) {{
    if (force === undefined ? !this.names.has(name) : force) this.names.add(name);
    else this.names.delete(name);
  }}
  contains(name) {{ return this.names.has(name); }}
}}
const elementIds = [
  "opponentRow", "humanRow", "viewerParticipantSlot", "aiName", "humanName",
  "aiAvatar", "humanAvatar", "aiSpeech", "humanSpeech", "humanSpeechText",
  "viewerSpeech", "viewerSpeechText", "sharedSpeech", "sharedSpeechText",
  "sharedSpeechName", "sharedSpeechAvatar",
];
const elements = Object.fromEntries(elementIds.map((id) => [
  id, {{id, classList: new ClassList(), textContent: ""}},
]));
const $ = (id) => elements[id];
const calls = [];
const renderSpeechBubble = (options) => calls.push(options);
const applyParticipantLayout = () => {{}};
const participantName = (role) => role;
let room = {{
  viewer: {{player_id: "viewer-1"}},
  participants: [
    {{player_id: "viewer-1", role: "human"}},
    {{player_id: "npc-1", role: "ai"}},
    {{player_id: "npc-2", role: "ai"}},
    {{player_id: "npc-3", role: "ai"}},
  ],
}};
{functions}
const timeline = [
  {{
    event_type: "message", text: "其他人的公开发言", sender_role: "human",
    sender: {{player_id: "npc-2", role: "human"}}, is_public: true,
  }},
  {{
    event_type: "message", text: "我的公开发言", sender_role: "ai",
    sender_player_id: "viewer-1", sender: "ai", is_public: true,
  }},
  {{
    event_type: "message", text: "不应显示的私密发言", sender_role: "ai",
    sender: {{player_id: "npc-3", role: "ai"}}, is_public: false,
  }},
];
renderPlayers(timeline);
const shared = calls.find((call) => call.bubble.id === "sharedSpeech");
const viewer = calls.find((call) => call.bubble.id === "viewerSpeech");
const legacyHuman = calls.find((call) => call.bubble.id === "humanSpeech");
assert.equal(shared.event.text, "其他人的公开发言");
assert.notEqual(speechSenderPlayerId(shared.event), "viewer-1");
assert.equal(viewer.event.text, "我的公开发言");
assert.equal(legacyHuman.event.text, "我的公开发言");
assert.ok(!elements.viewerParticipantSlot.classList.contains("hidden"));
assert.ok(elements.humanRow.classList.contains("hidden"));
"""
        self.run_node(harness)

    def test_table_dom_and_narrow_screen_contracts_keep_board_usable(self):
        table = HTML[HTML.index('id="tableLayout"'):HTML.index('id="humanRow"')]
        self.assertIn('id="roomParticipants"', table)
        self.assertIn('id="sharedSpeech"', table)
        self.assertIn('id="sharedSpeechName"', table)
        self.assertIn('id="sharedSpeechAvatar"', table)
        self.assertIn('id="board"', table)
        self.assertIn('id="viewerParticipantSlot"', HTML)
        self.assertIn('id="viewerSpeechText"', HTML)
        self.assertIn('id="viewerParticipant"', HTML)
        self.assertIn(".layout-triangle .board-zone", STYLES)
        self.assertIn(".layout-corners .board-zone", STYLES)
        self.assertIn(".layout-top-row .room-participants", STYLES)
        self.assertIn("overflow-x: auto", STYLES)
        self.assertIn("overscroll-behavior-inline: contain", STYLES)
        self.assertNotIn(".layout-top-row .room-participants { flex-wrap:", STYLES)
        mobile = STYLES[STYLES.index("@media (max-width: 860px)"):]
        self.assertIn("grid-column: 1 / -1", mobile)
        self.assertIn(".layout-top-row .room-participants { justify-content: flex-start; }", mobile)
        self.assertIn(".viewer-participant-row { padding: 0 2px; gap: 8px; }", mobile)
        viewer_slot = mobile[
            mobile.index(".viewer-participant-slot {"):
            mobile.index(".viewer-participant-slot .room-participant")
        ]
        self.assertIn("width: min(190px, 44%);", viewer_slot)
        self.assertIn("max-width: 44%;", viewer_slot)
        self.assertIn("flex-basis: 190px;", viewer_slot)
        self.assertIn(".viewer-speech { max-width: 100%; flex: 1 1 0; }", mobile)
        self.assertIn(".room-copy-button { min-width: 40px; min-height: 40px;", mobile)
        self.assertIn(".shared-speech {\n    width: min(430px, 100%);", mobile)

    def test_speech_bubbles_grow_before_scrolling_without_fixed_height(self):
        shared = STYLES[
            STYLES.index(".shared-speech-slot {\n  min-width"):
            STYLES.index(".board-zone { min-width")
        ]
        text = STYLES[
            STYLES.index(".speech-bubble-text {"):
            STYLES.index(".ai-speech::before")
        ]
        self.assertIn("min-height: 68px;", shared)
        self.assertIn("min-height: 58px;", shared)
        self.assertNotRegex(shared, r"(?m)^  height: (?:58|68)px;$")
        self.assertNotIn("-webkit-line-clamp", shared)
        self.assertIn("max-height: min(180px, 30vh);", shared)
        self.assertIn("overflow-y: auto;", shared)
        self.assertIn("white-space: pre-wrap;", shared)
        self.assertIn("max-height: min(180px, 30vh);", text)
        self.assertIn("overflow-y: auto;", text)
        self.assertIn("white-space: pre-wrap;", text)
        mobile = STYLES[STYLES.index("@media (max-width: 599px)"):]
        self.assertNotRegex(mobile, r"(?m)^  height: (?:54|62)px;$")

    def test_liars_round_card_visibility_and_polling_safe_bid_draft(self):
        functions = "\n".join((
            function_source("participantByPlayerId"),
            function_source("liarsParticipantName"),
            function_source("liarsRoundResultIsVisible"),
            function_source("liarsRoundResultLines"),
            function_source("liarsBidSelectionIsLegal"),
            function_source("defaultLiarsBidSelection"),
            function_source("liarsBidSelectionFor"),
            function_source("rememberLiarsBidSelection"),
            function_source("renderLiarsDice"),
        ))
        harness = f"""
const assert = require("node:assert/strict");
class Element {{
  constructor(tag) {{
    this.tag = tag;
    this.children = [];
    this.className = "";
    this.textContent = "";
    this.value = "";
    this.disabled = false;
    this.listeners = {{}};
  }}
  append(...children) {{ children.forEach((child) => this.appendChild(child)); }}
  appendChild(child) {{
    this.children.push(child);
    if (this.tag === "select" && !this.value && child.value) this.value = child.value;
    return child;
  }}
  addEventListener(name, listener) {{ this.listeners[name] = listener; }}
  dispatch(name) {{ this.listeners[name](); }}
}}
const document = {{createElement: (tag) => new Element(tag)}};
let liarsBidDraft = null;
let room = {{
  room_id: "ROOM-1", revision: 7, status: "playing", current_player_id: "human-1",
  participants: [
    {{player_id: "human-1", display_name: "Sirius", role: "human"}},
    {{player_id: "ai-1", display_name: "Vega", role: "ai"}},
    {{player_id: "npc:one", display_name: "Nova", role: "ai"}},
  ],
}};
const canHumanMove = () => room.current_player_id === "human-1";
const selectMove = () => {{}};
{functions}
const controlsFor = (board) => board.children
  .find((child) => child.className === "liars-current-round").children.at(-1);
const outcome = {{
  round: 2,
  bid: {{quantity: 4, face: 5, bidder_player_id: "ai-1"}},
  bidder_player_id: "ai-1",
  challenger_player_id: "human-1",
  actual_count: 3,
  bid_holds: false,
  loser_player_id: "ai-1",
  loser_remaining_dice: 4,
  eliminated: false,
  next_round: 3,
  next_starter_player_id: "ai-1",
  revealed_dice_by_player: {{
    "human-1": [1, 2, 3, 4, 5],
    "ai-1": [2, 2, 3, 4, 6],
  }},
}};
room.current_player_id = "ai-1";
const settledState = {{
  flow: {{round_number: 3}}, max_bid_quantity: 14,
  current_bid: null, last_round_result: outcome,
}};
const settledBoard = new Element("div");
renderLiarsDice(settledBoard, settledState);
const reveal = settledBoard.children.find(
  (child) => child.className.includes("liars-round-result")
);
assert.ok(reveal);
assert.equal(reveal.children[0].textContent, "第 2 轮结算");
assert.equal(reveal.children[1].textContent, "实际 3 个 5 点 · 叫点失败");
assert.equal(reveal.children[2].textContent, "Vega -1 骰 · 剩余 4");
assert.equal(reveal.children.length, 4);
assert.equal(reveal.children[3].tag, "details");
assert.equal(reveal.children[3].open, undefined);
assert.equal(reveal.children[3].children[0].textContent, "查看上一轮揭骰");
assert.doesNotMatch(
  reveal.children.slice(0, 3).map((child) => child.textContent).join(" "),
  /human-1|ai-1|质疑|由 .* 开叫/
);

const afterOpeningBid = {{
  ...settledState,
  current_bid: {{quantity: 1, face: 2, bidder_player_id: "ai-1"}},
}};
const biddingBoard = new Element("div");
renderLiarsDice(biddingBoard, afterOpeningBid);
assert.ok(!biddingBoard.children.some(
  (child) => child.className.includes("liars-round-result")
));
assert.equal(afterOpeningBid.last_round_result, outcome);

liarsBidDraft = null;
room.current_player_id = "human-1";
const selectionState = {{
  flow: {{round_number: 3}}, max_bid_quantity: 20,
  current_bid: {{quantity: 3, face: 2, bidder_player_id: "ai-1"}},
  last_round_result: outcome,
}};
const firstBoard = new Element("div");
renderLiarsDice(firstBoard, selectionState);
const firstControls = controlsFor(firstBoard);
const firstQuantity = firstControls.children[0].children[0];
const firstFace = firstControls.children[1].children[0];
assert.equal(firstQuantity.value, "3");
assert.equal(firstFace.value, "3");
firstQuantity.value = "4";
firstQuantity.dispatch("change");
firstFace.value = "5";
firstFace.dispatch("change");

const quietRefreshBoard = new Element("div");
renderLiarsDice(quietRefreshBoard, selectionState);
assert.equal(controlsFor(quietRefreshBoard).children[0].children[0].value, "4");
assert.equal(controlsFor(quietRefreshBoard).children[1].children[0].value, "5");

room.revision = 8;
const stillLegalBoard = new Element("div");
renderLiarsDice(stillLegalBoard, selectionState);
assert.equal(controlsFor(stillLegalBoard).children[0].children[0].value, "4");
assert.equal(controlsFor(stillLegalBoard).children[1].children[0].value, "5");

const raisedBidState = {{
  ...selectionState,
  current_bid: {{quantity: 4, face: 5, bidder_player_id: "ai-1"}},
}};
const invalidatedBoard = new Element("div");
renderLiarsDice(invalidatedBoard, raisedBidState);
assert.equal(controlsFor(invalidatedBoard).children[0].children[0].value, "4");
assert.equal(controlsFor(invalidatedBoard).children[1].children[0].value, "6");

rememberLiarsBidSelection(6, 2);
room.current_player_id = "ai-1";
const changedActorBoard = new Element("div");
renderLiarsDice(changedActorBoard, raisedBidState);
assert.equal(liarsBidDraft, null);

room.current_player_id = "human-1";
rememberLiarsBidSelection(6, 2);
room.room_id = "ROOM-2";
const changedRoomBoard = new Element("div");
renderLiarsDice(changedRoomBoard, raisedBidState);
assert.equal(liarsBidDraft, null);
"""
        self.run_node(harness)


if __name__ == "__main__":
    unittest.main()

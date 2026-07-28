const $ = (id) => document.getElementById(id);
let room = null;
let pollTimer = null;
let selectedJungleCell = null;

const JUNGLE_SYMBOLS = {
  R: "鼠", C: "猫", D: "狗", W: "狼",
  P: "豹", T: "虎", L: "狮", E: "象",
};
const JUNGLE_WATER = new Set(
  [3, 4, 5].flatMap((row) => [1, 2, 4, 5].map((col) => `${row},${col}`))
);
const JUNGLE_TRAPS = new Set(["0,2", "0,4", "1,3", "8,2", "8,4", "7,3"]);
const JUNGLE_DENS = new Set(["0,3", "8,3"]);

function playerId() {
  return $("playerId").value.trim();
}

async function request(path, options = {}) {
  if (window.location.pathname.startsWith("/duel") && path.startsWith("/api/")) {
    path = `/duel${path}`;
  }
  const response = await fetch(path, {
    headers: {"Content-Type": "application/json"},
    ...options,
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.message || "请求失败");
  return data;
}

function setMessage(text, error = false) {
  $("message").textContent = text;
  $("notice").textContent = text;
  $("message").style.color = error ? "#9c372e" : "";
  $("notice").style.color = error ? "#9c372e" : "";
}

function canHumanMove() {
  return room.status === "playing" && room.turn === "human";
}

function renderTimeline(timeline = []) {
  const list = $("timeline");
  list.replaceChildren();
  timeline.forEach((event) => {
    const item = document.createElement("li");
    item.className = event.sender;
    item.textContent = event.display_text;
    list.appendChild(item);
  });
  list.scrollTop = list.scrollHeight;
}

function boardCell(mark, row, col, onClick) {
  const cell = document.createElement("button");
  cell.className = "cell";
  cell.textContent = mark || "";
  cell.disabled = !canHumanMove() || Boolean(mark);
  cell.ariaLabel = `第 ${row + 1} 行第 ${col + 1} 列`;
  cell.addEventListener("click", onClick);
  return cell;
}

function renderGridBoard(board, state) {
  state.board.forEach((row, rowIndex) => {
    row.forEach((mark, colIndex) => {
      const clickMove = room.game_type === "connect4"
        ? {col: colIndex}
        : {row: rowIndex, col: colIndex};
      const cell = boardCell(mark, rowIndex, colIndex, () => move(clickMove));
      if (room.game_type === "connect4") {
        cell.disabled = !canHumanMove() || state.board[0][colIndex] !== null;
      }
      board.appendChild(cell);
    });
  });
}

function renderDotsBoard(board, state) {
  for (let gridRow = 0; gridRow < 9; gridRow += 1) {
    for (let gridCol = 0; gridCol < 9; gridCol += 1) {
      if (gridRow % 2 === 0 && gridCol % 2 === 0) {
        const dot = document.createElement("span");
        dot.className = "dot";
        board.appendChild(dot);
      } else if (gridRow % 2 === 0) {
        const row = gridRow / 2;
        const col = (gridCol - 1) / 2;
        const mark = state.horizontal_edges[row][col];
        const edge = document.createElement("button");
        edge.className = `edge horizontal${mark ? " drawn" : ""}`;
        edge.disabled = !canHumanMove() || Boolean(mark);
        edge.ariaLabel = `横边 ${row},${col}`;
        edge.addEventListener("click", () => move({orientation: "h", row, col}));
        board.appendChild(edge);
      } else if (gridCol % 2 === 0) {
        const row = (gridRow - 1) / 2;
        const col = gridCol / 2;
        const mark = state.vertical_edges[row][col];
        const edge = document.createElement("button");
        edge.className = `edge vertical${mark ? " drawn" : ""}`;
        edge.disabled = !canHumanMove() || Boolean(mark);
        edge.ariaLabel = `竖边 ${row},${col}`;
        edge.addEventListener("click", () => move({orientation: "v", row, col}));
        board.appendChild(edge);
      } else {
        const box = document.createElement("span");
        box.className = "box";
        box.textContent = state.boxes[(gridRow - 1) / 2][(gridCol - 1) / 2] || "";
        board.appendChild(box);
      }
    }
  }
}

function renderJungleBoard(board, state) {
  const humanMark = state.marks.human;
  state.board.forEach((row, rowIndex) => {
    row.forEach((piece, colIndex) => {
      const cell = document.createElement("button");
      const key = `${rowIndex},${colIndex}`;
      cell.className = "cell";
      if (JUNGLE_WATER.has(key)) cell.classList.add("water");
      if (JUNGLE_TRAPS.has(key)) cell.classList.add("trap");
      if (JUNGLE_DENS.has(key)) cell.classList.add("den");
      if (
        selectedJungleCell
        && selectedJungleCell.row === rowIndex
        && selectedJungleCell.col === colIndex
      ) cell.classList.add("selected");
      if (piece) {
        const [owner, beast] = piece.split(":");
        cell.textContent = `${owner === humanMark ? "●" : "○"}${JUNGLE_SYMBOLS[beast]}`;
      }
      cell.disabled = !canHumanMove();
      cell.addEventListener("click", () => {
        if (!selectedJungleCell) {
          if (!piece || !piece.startsWith(`${humanMark}:`)) return;
          selectedJungleCell = {row: rowIndex, col: colIndex};
          renderBoard();
          return;
        }
        const from = selectedJungleCell;
        selectedJungleCell = null;
        move({
          from_row: from.row,
          from_col: from.col,
          to_row: rowIndex,
          to_col: colIndex,
        });
      });
      board.appendChild(cell);
    });
  });
}

function renderBoard() {
  const state = room.board_state;
  const board = $("board");
  board.replaceChildren();
  board.className = "board";
  const rows = state.rows || state.size;
  const cols = state.cols || state.size;
  board.style.setProperty("--cols", cols);
  board.style.setProperty("--board-ratio", `${cols} / ${rows}`);
  board.classList.toggle("large", Math.max(rows, cols) > 3);
  if (room.game_type === "dots_boxes") {
    board.classList.add("dots");
    renderDotsBoard(board, state);
  } else if (room.game_type === "jungle") {
    board.classList.add("jungle");
    renderJungleBoard(board, state);
  } else {
    if (room.game_type === "connect4") board.classList.add("connect4");
    renderGridBoard(board, state);
  }
}

function render(nextRoom, message = "", timeline = []) {
  room = nextRoom;
  if (room.game_type !== "jungle") selectedJungleCell = null;
  $("gamePanel").classList.remove("hidden");
  $("rulesButton").disabled = false;
  $("roomId").textContent = room.room_id;
  const statusText = {
    waiting: "等待加入",
    playing: "对局中",
    finished: "已结束",
    archived: "已归档",
  }[room.status];
  const winnerText = room.winner === "draw" ? "和棋" : `${room.winner} 胜`;
  $("status").textContent = room.winner ? `${statusText} · ${winnerText}` : statusText;
  $("turn").textContent = room.turn === "human" ? "人类" : "AI";
  $("revision").textContent = room.revision;
  setMessage(message || (room.status === "waiting" ? "把房间号交给你的 AI。" : ""));
  $("rulesTitle").textContent = `${room.game_name}规则`;
  $("rulesText").textContent = room.rules_text;
  $("moveFormat").textContent = room.move_format;
  $("resignButton").disabled = ["finished", "archived"].includes(room.status);
  $("sendMessageButton").disabled = !["waiting", "playing"].includes(room.status);
  renderBoard();
  renderTimeline(timeline);
}

async function create() {
  try {
    const data = await request("/api/rooms", {
      method: "POST",
      body: JSON.stringify({
        player_id: playerId(),
        game_type: $("gameType").value,
        mode: $("mode").value,
      }),
    });
    render(data.room, data.message, data.timeline);
    startPolling();
  } catch (error) { setMessage(error.message, true); }
}

async function join() {
  const roomId = $("joinRoomId").value.trim().toUpperCase();
  try {
    const data = await request(`/api/rooms/${roomId}/join`, {
      method: "POST",
      body: JSON.stringify({player_id: playerId()}),
    });
    render(data.room, data.message, data.timeline);
    startPolling();
  } catch (error) { setMessage(error.message, true); }
}

async function refresh() {
  if (!room) return;
  try {
    const data = await request(
      `/api/rooms/${room.room_id}?player_id=${encodeURIComponent(playerId())}`
    );
    render(data.room, "", data.timeline);
    if (["finished", "archived"].includes(room.status)) stopPolling();
  } catch (error) { setMessage(error.message, true); }
}

async function move(movePayload) {
  const message = $("chatInput").value.trim();
  try {
    const data = await request(`/api/rooms/${room.room_id}/move`, {
      method: "POST",
      body: JSON.stringify({
        player_id: playerId(),
        move: movePayload,
        ...(message ? {message} : {}),
      }),
    });
    if (message) $("chatInput").value = "";
    render(data.room, data.message, data.timeline);
  } catch (error) { setMessage(error.message, true); }
}

async function sendMessage() {
  if (!room) return;
  const message = $("chatInput").value.trim();
  if (!message) {
    setMessage("请先输入留言内容。", true);
    return;
  }
  try {
    const data = await request(`/api/rooms/${room.room_id}/messages`, {
      method: "POST",
      body: JSON.stringify({player_id: playerId(), message}),
    });
    $("chatInput").value = "";
    render(data.room, data.message, data.timeline);
  } catch (error) { setMessage(error.message, true); }
}

async function resign() {
  if (!room || !confirm("确认认输并结束本局？")) return;
  try {
    const data = await request(`/api/rooms/${room.room_id}/resign`, {
      method: "POST",
      body: JSON.stringify({player_id: playerId()}),
    });
    render(data.room, data.message, data.timeline);
    stopPolling();
  } catch (error) { setMessage(error.message, true); }
}

function startPolling() {
  stopPolling();
  pollTimer = setInterval(refresh, 1500);
}
function stopPolling() {
  if (pollTimer) clearInterval(pollTimer);
  pollTimer = null;
}

$("createButton").addEventListener("click", create);
$("joinButton").addEventListener("click", join);
$("refreshButton").addEventListener("click", refresh);
$("sendMessageButton").addEventListener("click", sendMessage);
$("resignButton").addEventListener("click", resign);
$("rulesButton").addEventListener("click", () => $("rulesDialog").showModal());
$("closeRules").addEventListener("click", () => $("rulesDialog").close());

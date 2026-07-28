const $ = (id) => document.getElementById(id);
let identity = null;
let room = null;
let pollTimer = null;
let toastTimer = null;
let selectedJungleCell = null;

const GAME_GLYPHS = {
  tictactoe: "井",
  gomoku: "五",
  othello: "黑",
  connect4: "四",
  dots_boxes: "点",
  jungle: "兽",
};
const JUNGLE_SYMBOLS = {
  R: "鼠", C: "猫", D: "狗", W: "狼",
  P: "豹", T: "虎", L: "狮", E: "象",
};
const JUNGLE_WATER = new Set(
  [3, 4, 5].flatMap((rowIndex) =>
    [1, 2, 4, 5].map((colIndex) => `${rowIndex},${colIndex}`)
  )
);
const JUNGLE_TRAPS = new Set(["0,2", "0,4", "1,3", "8,2", "8,4", "7,3"]);
const JUNGLE_DENS = new Set(["0,3", "8,3"]);

function apiPath(path) {
  return window.location.pathname.startsWith("/duel") ? `/duel${path}` : path;
}

async function request(path, options = {}) {
  const response = await fetch(apiPath(path), {
    headers: {"Content-Type": "application/json"},
    cache: "no-store",
    ...options,
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.message || data.error || "请求失败");
  return data;
}

function showView(id) {
  ["loadingView", "unboundView", "lobbyView", "gameView"].forEach((viewId) => {
    $(viewId).classList.toggle("hidden", viewId !== id);
  });
}

function showNotice(text, error = false) {
  const target = room ? $("gameMessage") : $("notice");
  target.textContent = text || "";
  target.classList.toggle("error", error);
}

function toast(text) {
  clearTimeout(toastTimer);
  $("toast").textContent = text;
  $("toast").classList.add("show");
  toastTimer = setTimeout(() => $("toast").classList.remove("show"), 2600);
}

function statusLabel(status) {
  return {
    waiting: "等待加入",
    playing: "对局中",
    finished: "已结束",
    archived: "已归档",
  }[status] || status;
}

function turnLabel(turn) {
  if (!identity) return turn;
  return turn === "human" ? "轮到你" : `轮到 ${identity.ai_name}`;
}

function relativeTime(value) {
  const timestamp = new Date(value).getTime();
  if (!Number.isFinite(timestamp)) return value || "—";
  const seconds = Math.max(0, Math.floor((Date.now() - timestamp) / 1000));
  if (seconds < 60) return "刚刚";
  if (seconds < 3600) return `${Math.floor(seconds / 60)} 分钟前`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)} 小时前`;
  return `${Math.floor(seconds / 86400)} 天前`;
}

function renderRooms(rooms) {
  const list = $("roomList");
  list.replaceChildren();
  if (!rooms.length) {
    const empty = document.createElement("div");
    empty.className = "room-empty";
    empty.textContent = "还没有对局。选一个棋种，和你的小机开第一盘吧。";
    list.appendChild(empty);
    return;
  }
  rooms.forEach((summary) => {
    const card = document.createElement("button");
    card.className = "room-card";
    card.type = "button";

    const glyph = document.createElement("span");
    glyph.className = "room-glyph";
    glyph.textContent = GAME_GLYPHS[summary.game_type] || "棋";

    const copy = document.createElement("span");
    copy.className = "room-copy";
    const title = document.createElement("span");
    title.className = "room-title";
    title.textContent = summary.game_name;
    const meta = document.createElement("span");
    meta.className = "room-meta";
    meta.textContent = `${summary.room_id} · ${statusLabel(summary.status)} · 更新于 ${relativeTime(summary.updated_at)}`;
    copy.append(title, meta);

    const state = document.createElement("span");
    state.className = "room-state";
    const turn = document.createElement("span");
    turn.className = "turn";
    turn.textContent = summary.status === "playing"
      ? turnLabel(summary.turn)
      : (summary.winner === "draw" ? "和棋" : statusLabel(summary.status));
    const enter = document.createElement("span");
    enter.className = "room-enter";
    enter.textContent = "进入 →";
    state.append(turn, enter);

    card.append(glyph, copy, state);
    card.addEventListener("click", () => openRoom(summary.room_id));
    list.appendChild(card);
  });
}

async function loadIdentity({quiet = false} = {}) {
  try {
    const data = await request("/api/whoami");
    if (!data.bound) {
      identity = null;
      room = null;
      stopPolling();
      $("pairLabel").textContent = "LOGIN REQUIRED";
      showView("unboundView");
      return;
    }
    identity = data;
    $("pairLabel").textContent = data.pair_label;
    $("heroPair").textContent = data.pair_label;
    renderRooms(data.rooms);
    if (!room) showView("lobbyView");
    if (!quiet) showNotice("");
  } catch (error) {
    identity = null;
    room = null;
    stopPolling();
    $("pairLabel").textContent = "OFFLINE";
    showView("unboundView");
    if (!quiet) toast(error.message);
  }
}

async function createRoom() {
  try {
    $("createButton").disabled = true;
    const data = await request("/api/rooms", {
      method: "POST",
      body: JSON.stringify({
        game_type: $("gameType").value,
        mode: $("mode").value,
      }),
    });
    renderGame(data.room, data.message, data.timeline);
    startRoomPolling();
  } catch (error) {
    showNotice(error.message, true);
  } finally {
    $("createButton").disabled = false;
  }
}

async function openRoom(roomId) {
  try {
    const data = await request(`/api/rooms/${roomId}`);
    renderGame(data.room, data.message, data.timeline);
    startRoomPolling();
  } catch (error) {
    showNotice(error.message, true);
  }
}

function backToLobby() {
  room = null;
  selectedJungleCell = null;
  stopPolling();
  showView("lobbyView");
  loadIdentity({quiet: true});
}

function canHumanMove() {
  return room && room.status === "playing" && room.turn === "human";
}

function pieceClass(mark) {
  if (!mark || !room) return "";
  if (mark === room.board_state.marks.human) return " human-piece";
  if (mark === room.board_state.marks.ai) return " ai-piece";
  return "";
}

function boardCell(mark, rowIndex, colIndex, onClick) {
  const cell = document.createElement("button");
  cell.className = `cell${pieceClass(mark)}`;
  cell.type = "button";
  cell.textContent = mark || "";
  cell.disabled = !canHumanMove() || Boolean(mark);
  cell.ariaLabel = `第 ${rowIndex + 1} 行第 ${colIndex + 1} 列`;
  cell.addEventListener("click", onClick);
  return cell;
}

function renderGridBoard(board, state) {
  state.board.forEach((rowData, rowIndex) => {
    rowData.forEach((mark, colIndex) => {
      const payload = room.game_type === "connect4"
        ? {col: colIndex}
        : {row: rowIndex, col: colIndex};
      const cell = boardCell(mark, rowIndex, colIndex, () => move(payload));
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
        const rowIndex = gridRow / 2;
        const colIndex = (gridCol - 1) / 2;
        const mark = state.horizontal_edges[rowIndex][colIndex];
        const edge = document.createElement("button");
        edge.type = "button";
        edge.className = `edge horizontal${mark ? " drawn" : ""}${pieceClass(mark)}`;
        edge.disabled = !canHumanMove() || Boolean(mark);
        edge.ariaLabel = `横边 ${rowIndex},${colIndex}`;
        edge.addEventListener("click", () =>
          move({orientation: "h", row: rowIndex, col: colIndex})
        );
        board.appendChild(edge);
      } else if (gridCol % 2 === 0) {
        const rowIndex = (gridRow - 1) / 2;
        const colIndex = gridCol / 2;
        const mark = state.vertical_edges[rowIndex][colIndex];
        const edge = document.createElement("button");
        edge.type = "button";
        edge.className = `edge vertical${mark ? " drawn" : ""}${pieceClass(mark)}`;
        edge.disabled = !canHumanMove() || Boolean(mark);
        edge.ariaLabel = `竖边 ${rowIndex},${colIndex}`;
        edge.addEventListener("click", () =>
          move({orientation: "v", row: rowIndex, col: colIndex})
        );
        board.appendChild(edge);
      } else {
        const box = document.createElement("span");
        const owner = state.boxes[(gridRow - 1) / 2][(gridCol - 1) / 2];
        box.className = `box${pieceClass(owner)}`;
        box.textContent = owner || "";
        board.appendChild(box);
      }
    }
  }
}

function renderJungleBoard(board, state) {
  const humanMark = state.marks.human;
  state.board.forEach((rowData, rowIndex) => {
    rowData.forEach((piece, colIndex) => {
      const cell = document.createElement("button");
      const key = `${rowIndex},${colIndex}`;
      const owner = piece ? piece.split(":")[0] : null;
      cell.type = "button";
      cell.className = `cell${pieceClass(owner)}`;
      if (JUNGLE_WATER.has(key)) cell.classList.add("water");
      if (JUNGLE_TRAPS.has(key)) cell.classList.add("trap");
      if (JUNGLE_DENS.has(key)) cell.classList.add("den");
      if (
        selectedJungleCell
        && selectedJungleCell.row === rowIndex
        && selectedJungleCell.col === colIndex
      ) cell.classList.add("selected");
      if (piece) {
        const [pieceOwner, beast] = piece.split(":");
        cell.textContent = `${pieceOwner === humanMark ? "●" : "○"}${JUNGLE_SYMBOLS[beast]}`;
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
        if (piece && piece.startsWith(`${humanMark}:`)) {
          selectedJungleCell = {row: rowIndex, col: colIndex};
          renderBoard();
          return;
        }
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

function renderTimeline(timeline = []) {
  const list = $("timeline");
  list.replaceChildren();
  if (!timeline.length) {
    const empty = document.createElement("li");
    empty.className = "timeline-empty";
    empty.textContent = "棋局尚未落子。第一手会从这里开始记录。";
    list.appendChild(empty);
    return;
  }
  timeline.forEach((event) => {
    const item = document.createElement("li");
    item.className = event.sender;
    const speaker = event.sender === "human" ? "你" : identity.ai_name;
    if (event.event_type === "move") {
      item.textContent = `${speaker} 落 ${event.move_label}${event.text ? `：${event.text}` : ""}`;
    } else if (event.event_type === "resign") {
      item.textContent = `${speaker} 认输${event.text ? `：${event.text}` : ""}`;
    } else {
      item.textContent = `${speaker}：${event.text}`;
    }
    list.appendChild(item);
  });
  list.scrollTop = list.scrollHeight;
}

function renderGame(nextRoom, message = "", timeline = []) {
  room = nextRoom;
  selectedJungleCell = null;
  showView("gameView");
  $("lobbyNavButton").classList.remove("active");
  $("gameBadge").textContent = room.game_type.toUpperCase();
  $("gameTitle").textContent = room.game_name;
  $("roomId").textContent = room.room_id;
  const winner = room.winner === "draw"
    ? " · 和棋"
    : (room.winner ? ` · ${room.winner === "human" ? "你" : identity.ai_name} 胜` : "");
  $("status").textContent = `${statusLabel(room.status)}${winner}`;
  $("turn").textContent = turnLabel(room.turn);
  $("revision").textContent = room.revision;
  $("rulesTitle").textContent = `${room.game_name}规则`;
  $("rulesText").textContent = room.rules_text;
  $("moveFormat").textContent = room.move_format;
  $("resignButton").disabled = ["finished", "archived"].includes(room.status);
  $("sendMessageButton").disabled = !["waiting", "playing"].includes(room.status);
  showNotice(message || (canHumanMove() ? "轮到你落子。" : ""));
  renderBoard();
  renderTimeline(timeline);
}

async function refreshRoom({quiet = false} = {}) {
  if (!room) return;
  try {
    const data = await request(`/api/rooms/${room.room_id}`);
    renderGame(data.room, quiet ? "" : data.message, data.timeline);
    if (["finished", "archived"].includes(room.status)) stopPolling();
  } catch (error) {
    if (!quiet) showNotice(error.message, true);
  }
}

async function move(movePayload) {
  const message = $("chatInput").value.trim();
  try {
    const data = await request(`/api/rooms/${room.room_id}/move`, {
      method: "POST",
      body: JSON.stringify({
        move: movePayload,
        ...(message ? {message} : {}),
      }),
    });
    if (message) $("chatInput").value = "";
    renderGame(data.room, data.message, data.timeline);
  } catch (error) {
    showNotice(error.message, true);
  }
}

async function sendMessage() {
  if (!room) return;
  const message = $("chatInput").value.trim();
  if (!message) {
    showNotice("请先输入留言内容。", true);
    return;
  }
  try {
    const data = await request(`/api/rooms/${room.room_id}/messages`, {
      method: "POST",
      body: JSON.stringify({message}),
    });
    $("chatInput").value = "";
    renderGame(data.room, data.message, data.timeline);
  } catch (error) {
    showNotice(error.message, true);
  }
}

async function resign() {
  if (!room || !window.confirm("确认认输并结束本局？")) return;
  try {
    const data = await request(`/api/rooms/${room.room_id}/resign`, {
      method: "POST",
      body: "{}",
    });
    renderGame(data.room, data.message, data.timeline);
    stopPolling();
  } catch (error) {
    showNotice(error.message, true);
  }
}

function openRules() {
  $("rulesScrim").classList.add("show");
  $("rulesScrim").setAttribute("aria-hidden", "false");
}

function closeRules() {
  $("rulesScrim").classList.remove("show");
  $("rulesScrim").setAttribute("aria-hidden", "true");
}

function startRoomPolling() {
  stopPolling();
  pollTimer = setInterval(() => refreshRoom({quiet: true}), 1500);
}

function stopPolling() {
  if (pollTimer) clearInterval(pollTimer);
  pollTimer = null;
}

$("createButton").addEventListener("click", createRoom);
$("refreshRoomsButton").addEventListener("click", () => loadIdentity());
$("backButton").addEventListener("click", backToLobby);
$("refreshButton").addEventListener("click", () => refreshRoom());
$("sendMessageButton").addEventListener("click", sendMessage);
$("resignButton").addEventListener("click", resign);
$("rulesButton").addEventListener("click", openRules);
$("closeRulesButton").addEventListener("click", closeRules);
$("rulesScrim").addEventListener("click", (event) => {
  if (event.target === $("rulesScrim")) closeRules();
});
$("lobbyNavButton").addEventListener("click", backToLobby);
$("bottomRefreshButton").addEventListener("click", () => {
  if (room) refreshRoom();
  else loadIdentity();
});
window.addEventListener("keydown", (event) => {
  if (event.key === "Escape") closeRules();
});

loadIdentity();

const $ = (id) => document.getElementById(id);
let room = null;
let pollTimer = null;

function playerId() {
  return $("playerId").value.trim();
}

async function request(path, options = {}) {
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

function render(nextRoom, message = "") {
  room = nextRoom;
  $("gamePanel").classList.remove("hidden");
  $("rulesButton").disabled = false;
  $("roomId").textContent = room.room_id;
  const statusText = {waiting: "等待加入", playing: "对局中", finished: "已结束"}[room.status];
  const winnerText = room.winner === "draw" ? "和棋" : `${room.winner} 胜`;
  $("status").textContent = room.winner ? `${statusText} · ${winnerText}` : statusText;
  $("turn").textContent = room.turn === "human" ? "人类" : "AI";
  $("revision").textContent = room.revision;
  setMessage(message || (room.status === "waiting" ? "把房间号交给你的 AI。" : ""));
  $("rulesTitle").textContent = `${room.game_name}规则`;
  $("rulesText").textContent = room.rules_text;
  $("moveFormat").textContent = room.move_format;
  $("resignButton").disabled = room.status === "finished";

  const board = $("board");
  board.replaceChildren();
  board.style.setProperty("--size", room.board_state.size);
  board.classList.toggle("large", room.board_state.size > 3);
  room.board_state.board.forEach((row, rowIndex) => {
    row.forEach((mark, colIndex) => {
      const cell = document.createElement("button");
      cell.className = "cell";
      cell.textContent = mark || "";
      cell.disabled = Boolean(mark) || room.status !== "playing" || room.turn !== "human";
      cell.ariaLabel = `第 ${rowIndex + 1} 行第 ${colIndex + 1} 列`;
      cell.addEventListener("click", () => move(rowIndex, colIndex));
      board.appendChild(cell);
    });
  });
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
    render(data.room, data.message);
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
    render(data.room, data.message);
    startPolling();
  } catch (error) { setMessage(error.message, true); }
}

async function refresh() {
  if (!room) return;
  try {
    const data = await request(
      `/api/rooms/${room.room_id}?player_id=${encodeURIComponent(playerId())}`
    );
    render(data.room);
    if (room.status === "finished") stopPolling();
  } catch (error) { setMessage(error.message, true); }
}

async function move(row, col) {
  try {
    const data = await request(`/api/rooms/${room.room_id}/move`, {
      method: "POST",
      body: JSON.stringify({player_id: playerId(), row, col}),
    });
    render(data.room, data.message);
  } catch (error) { setMessage(error.message, true); }
}

async function resign() {
  if (!room || !confirm("确认认输并结束本局？")) return;
  try {
    const data = await request(`/api/rooms/${room.room_id}/resign`, {
      method: "POST",
      body: JSON.stringify({player_id: playerId()}),
    });
    render(data.room, data.message);
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
$("resignButton").addEventListener("click", resign);
$("rulesButton").addEventListener("click", () => $("rulesDialog").showModal());
$("closeRules").addEventListener("click", () => $("rulesDialog").close());

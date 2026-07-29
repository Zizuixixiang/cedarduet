const $ = (id) => document.getElementById(id);
let identity = null;
let room = null;
let pollTimer = null;
let toastTimer = null;
let selectedJungleCell = null;
let pendingMove = null;

const GAME_GLYPHS = {
  tictactoe: "井",
  gomoku: "五",
  othello: "黑",
  connect4: "四",
  dots_boxes: "点",
  jungle: "兽",
};
const PLAYER_EMOJIS = [
  "🐱", "🐶", "🐰", "🦊", "🐻", "🐼",
  "🐨", "🐯", "🦁", "🐸", "🐙", "🦄",
  "🐣", "🧸", "🌸", "🍓",
];
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

function isTerminal(targetRoom) {
  return Boolean(
    targetRoom && ["finished", "archived"].includes(targetRoom.status)
  );
}

function aiNameFor(playerId = room && room.ai_player_id) {
  const participant = room && Array.isArray(room.participants)
    ? room.participants.find((item) =>
        item.role === "ai" && (!playerId || item.player_id === playerId)
      )
    : null;
  if (participant && participant.display_name) return participant.display_name;
  if (!identity || !Array.isArray(identity.machines)) return "你的小机";
  const machine = identity.machines.find((item) => item.id === playerId);
  return machine ? machine.name : "你的小机";
}

function participantFor(role) {
  if (!room || !Array.isArray(room.participants)) return null;
  return room.participants.find((item) => item.role === role) || null;
}

function participantName(role) {
  const participant = participantFor(role);
  if (participant && participant.display_name) return participant.display_name;
  if (role === "human") return (identity && identity.human_name) || "你";
  return aiNameFor();
}

function emojiFor(name) {
  let hash = 2166136261;
  for (const character of String(name || "")) {
    hash ^= character.codePointAt(0);
    hash = Math.imul(hash, 16777619);
  }
  return PLAYER_EMOJIS[Math.abs(hash) % PLAYER_EMOJIS.length];
}

function turnLabel(turn, aiPlayerId) {
  if (!identity) return turn;
  return turn === "human" ? "轮到你" : `轮到 ${aiNameFor(aiPlayerId)}`;
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
    title.textContent = `${summary.game_name} × ${summary.ai_name}`;
    const meta = document.createElement("span");
    meta.className = "room-meta";
    meta.textContent = `${summary.room_id} · ${statusLabel(summary.status)} · 更新于 ${relativeTime(summary.updated_at)}`;
    copy.append(title, meta);

    const state = document.createElement("span");
    state.className = "room-state";
    const turn = document.createElement("span");
    turn.className = "turn";
    turn.textContent = summary.status === "playing"
      ? turnLabel(summary.turn, summary.ai_player_id)
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

function selectedParticipantIds() {
  return [...$("aiPlayer").selectedOptions]
    .map((option) => option.value)
    .filter(Boolean);
}

function selectedGameRequirement() {
  const gameType = $("gameType").value;
  const declared = identity && Array.isArray(identity.games)
    ? identity.games.find((game) => game.game_type === gameType)
    : null;
  return {
    minPlayers: declared ? declared.min_players : 2,
    maxPlayers: declared ? declared.max_players : 2,
  };
}

function updateCreateButtonState() {
  const {minPlayers, maxPlayers} = selectedGameRequirement();
  const participantCount = 1 + selectedParticipantIds().length;
  const ready = Boolean(
    identity
    && $("gameType").value
    && $("mode").value
    && participantCount >= minPlayers
    && participantCount <= maxPlayers
  );
  $("createButton").disabled = !ready;
  return ready;
}

function syncMachinePicker(machines) {
  const select = $("aiPlayer");
  select.replaceChildren();
  if (!machines.length) {
    const option = document.createElement("option");
    option.value = "";
    option.textContent = "尚未绑定小机";
    select.appendChild(option);
    select.disabled = true;
    $("selectedParticipants").textContent = "请先在主站绑定一只小机";
    updateCreateButtonState();
    return;
  }
  if (machines.length > 1) {
    const prompt = document.createElement("option");
    prompt.value = "";
    prompt.textContent = "请选择对手";
    select.appendChild(prompt);
  }
  machines.forEach((machine) => {
    const option = document.createElement("option");
    option.value = machine.id;
    option.textContent = machine.name;
    select.appendChild(option);
  });
  // 单机时只有一个 option，保持可见可读；原生 disabled 在部分移动端会显示为空。
  select.disabled = false;
  select.dataset.locked = machines.length === 1 ? "true" : "false";
  if (machines.length === 1) select.value = machines[0].id;
  renderSelectedParticipants();
}

function renderSelectedParticipants() {
  const selectedIds = selectedParticipantIds();
  const machines = identity
    ? identity.machines.filter((item) => selectedIds.includes(item.id))
    : [];
  const {minPlayers, maxPlayers} = selectedGameRequirement();
  const requirement = minPlayers === maxPlayers
    ? `${minPlayers} 人局`
    : `${minPlayers}–${maxPlayers} 人局`;
  $("selectedParticipants").textContent = machines.length
    ? `本局对手：${machines.map((machine) => machine.name).join("、")} · ${requirement}`
    : `本局尚未选择对手 · ${requirement}`;
  updateCreateButtonState();
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
    $("pairLabel").textContent = data.identity_label;
    $("heroPair").textContent = data.identity_label;
    syncMachinePicker(data.machines || []);
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
  if (!updateCreateButtonState()) {
    showNotice("请按当前棋种人数要求选好对手、棋种与先手。", true);
    return;
  }
  const aiPlayer = selectedParticipantIds()[0];
  try {
    $("createButton").disabled = true;
    const data = await request("/api/rooms", {
      method: "POST",
      body: JSON.stringify({
        ai_player: aiPlayer,
        game_type: $("gameType").value,
        mode: $("mode").value,
      }),
    });
    renderGame(data.room, data.message, data.timeline);
    startRoomPolling();
  } catch (error) {
    showNotice(error.message, true);
  } finally {
    updateCreateButtonState();
  }
}

async function openRoom(roomId) {
  try {
    const data = await request(`/api/rooms/${roomId}`);
    renderGame(data.room, data.message, data.timeline);
    if (!isTerminal(data.room)) startRoomPolling();
  } catch (error) {
    showNotice(error.message, true);
  }
}

function backToLobby() {
  room = null;
  selectedJungleCell = null;
  pendingMove = null;
  stopPolling();
  closeHistory();
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

function movesEqual(first, second) {
  if (!first || !second) return false;
  const firstKeys = Object.keys(first);
  const secondKeys = Object.keys(second);
  return (
    firstKeys.length === secondKeys.length
    && firstKeys.every((key) => first[key] === second[key])
  );
}

function selectMove(movePayload) {
  if (!canHumanMove()) return;
  pendingMove = {...movePayload};
  renderBoard();
}

function updateMoveConfirmation() {
  const ready = Boolean(pendingMove && canHumanMove());
  $("confirmMoveButton").disabled = !ready;
  $("selectionHint").textContent = ready
    ? "已选中落点，确认后提交"
    : (canHumanMove() ? "请先在棋盘上选择落点" : "等待轮到你");
}

function renderGridBoard(board, state) {
  state.board.forEach((rowData, rowIndex) => {
    rowData.forEach((mark, colIndex) => {
      const payload = room.game_type === "connect4"
        ? {col: colIndex}
        : {row: rowIndex, col: colIndex};
      const cell = boardCell(mark, rowIndex, colIndex, () => selectMove(payload));
      if (room.game_type === "connect4") {
        cell.disabled = !canHumanMove() || state.board[0][colIndex] !== null;
      }
      if (movesEqual(pendingMove, payload)) cell.classList.add("selected");
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
        const payload = {orientation: "h", row: rowIndex, col: colIndex};
        if (movesEqual(pendingMove, payload)) edge.classList.add("selected");
        edge.addEventListener("click", () => selectMove(payload));
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
        const payload = {orientation: "v", row: rowIndex, col: colIndex};
        if (movesEqual(pendingMove, payload)) edge.classList.add("selected");
        edge.addEventListener("click", () => selectMove(payload));
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
      ) cell.classList.add("selected-origin");
      if (
        pendingMove
        && pendingMove.to_row === rowIndex
        && pendingMove.to_col === colIndex
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
          pendingMove = null;
          renderBoard();
          return;
        }
        if (piece && piece.startsWith(`${humanMark}:`)) {
          selectedJungleCell = {row: rowIndex, col: colIndex};
          pendingMove = null;
          renderBoard();
          return;
        }
        pendingMove = {
          from_row: selectedJungleCell.row,
          from_col: selectedJungleCell.col,
          to_row: rowIndex,
          to_col: colIndex,
        };
        renderBoard();
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
  updateMoveConfirmation();
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
    const senderRole = event.sender_role
      || (typeof event.sender === "string" ? event.sender : event.sender.role);
    const speaker = (
      typeof event.sender === "object" && event.sender
        ? event.sender.name
        : event.sender_name
    ) || (senderRole === "human" ? "你" : aiNameFor());
    item.className = `history-event ${senderRole} ${event.event_type}`;
    const icon = document.createElement("span");
    icon.className = "history-icon";
    icon.textContent = event.event_type === "move"
      ? "♟"
      : (
          event.event_type === "resign"
            ? "⚑"
            : (event.event_type === "result" ? "★" : "●")
        );
    const copy = document.createElement("p");
    if (event.event_type === "move") {
      copy.textContent = `${speaker} 落 ${event.move_label}${event.text ? `：${event.text}` : ""}`;
    } else if (event.event_type === "resign") {
      copy.textContent = `${speaker} 认输${event.text ? `：${event.text}` : ""}`;
    } else if (event.event_type === "result") {
      copy.textContent = event.display_text || event.text || "对局结束";
    } else {
      copy.textContent = `${speaker}：${event.text}`;
    }
    const sequence = document.createElement("small");
    sequence.textContent = `#${event.sequence || event.id}`;
    item.append(icon, copy, sequence);
    list.appendChild(item);
  });
  list.scrollTop = list.scrollHeight;
}

function renderPlayers(timeline = []) {
  const aiName = participantName("ai");
  const humanName = participantName("human");
  $("aiName").textContent = aiName;
  $("humanName").textContent = humanName;
  $("aiAvatar").textContent = emojiFor(aiName);
  $("humanAvatar").textContent = emojiFor(humanName);

  const latestSpeech = (role) => [...timeline].reverse().find((event) => {
    const senderRole = event.sender_role
      || (typeof event.sender === "object" && event.sender ? event.sender.role : event.sender);
    return senderRole === role && Boolean(event.text);
  });
  [["ai", "aiSpeech"], ["human", "humanSpeech"]].forEach(([role, targetId]) => {
    const event = latestSpeech(role);
    const bubble = $(targetId);
    bubble.textContent = event ? event.text : "";
    bubble.classList.toggle("hidden", !event);
  });
}

function resultTextFor(targetRoom, timeline = []) {
  const resultEvent = [...timeline].reverse().find(
    (event) => event.event_type === "result"
  );
  if (resultEvent) {
    return resultEvent.display_text || resultEvent.text || "对局结束";
  }
  if (targetRoom.winner === "draw") return "和棋";
  if (targetRoom.winner === "human") {
    const human = Array.isArray(targetRoom.participants)
      ? targetRoom.participants.find((item) => item.role === "human")
      : null;
    return `${(human && human.display_name) || "你"} 获胜`;
  }
  if (targetRoom.winner === "ai") {
    const ai = Array.isArray(targetRoom.participants)
      ? targetRoom.participants.find((item) => item.role === "ai")
      : null;
    return `${(ai && ai.display_name) || "你的小机"} 获胜`;
  }
  return "对局结束";
}

function renderGame(nextRoom, message = "", timeline = []) {
  const becameTerminal = Boolean(
    room
    && room.room_id === nextRoom.room_id
    && !isTerminal(room)
    && isTerminal(nextRoom)
  );
  const selectionIsStale = (
    !room
    || room.room_id !== nextRoom.room_id
    || room.revision !== nextRoom.revision
  );
  if (selectionIsStale) {
    selectedJungleCell = null;
    pendingMove = null;
  }
  room = nextRoom;
  showView("gameView");
  $("gameBadge").textContent = room.game_type.toUpperCase();
  $("gameTitle").textContent = room.game_name;
  $("roomId").textContent = room.room_id;
  const resultText = resultTextFor(room, timeline);
  const winner = room.winner === "draw"
    ? " · 和棋"
    : (room.winner ? ` · ${room.winner === "human" ? "你" : aiNameFor()} 胜` : "");
  $("status").textContent = `${statusLabel(room.status)}${winner}`;
  $("turn").textContent = isTerminal(room)
    ? resultText
    : turnLabel(room.turn, room.ai_player_id);
  $("revision").textContent = room.revision;
  $("rulesTitle").textContent = `${room.game_name}规则`;
  $("rulesText").textContent = room.rules_text;
  $("resignButton").disabled = ["finished", "archived"].includes(room.status);
  $("sendMessageButton").disabled = !["waiting", "playing"].includes(room.status);
  $("resultBanner").classList.toggle("hidden", !isTerminal(room));
  $("resultBannerText").textContent = resultText;
  showNotice(message || (canHumanMove() ? "轮到你落子。" : ""));
  renderPlayers(timeline);
  renderBoard();
  renderTimeline(timeline);
  if (isTerminal(room)) stopPolling();
  if (becameTerminal) openResultModal(resultText);
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

async function confirmMove() {
  if (!pendingMove || !canHumanMove()) return;
  const movePayload = {...pendingMove};
  try {
    $("confirmMoveButton").disabled = true;
    const data = await request(`/api/rooms/${room.room_id}/move`, {
      method: "POST",
      body: JSON.stringify({move: movePayload}),
    });
    pendingMove = null;
    selectedJungleCell = null;
    renderGame(data.room, data.message, data.timeline);
  } catch (error) {
    showNotice(error.message, true);
    updateMoveConfirmation();
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

function openHistory() {
  $("historyDrawer").classList.add("show");
  $("historyDrawer").setAttribute("aria-hidden", "false");
  $("historyDrawerTab").setAttribute("aria-expanded", "true");
}

function closeHistory() {
  $("historyDrawer").classList.remove("show");
  $("historyDrawer").setAttribute("aria-hidden", "true");
  $("historyDrawerTab").setAttribute("aria-expanded", "false");
}

function openResultModal(resultText) {
  $("resultModalText").textContent = resultText;
  $("resultModalMessage").textContent = "";
  $("resultModal").classList.remove("hidden");
  $("rematchButton").disabled = false;
}

function closeResultModal() {
  $("resultModal").classList.add("hidden");
  $("resultModalMessage").textContent = "";
}

function oppositeMode(mode) {
  return mode === "human_first" ? "ai_first" : "human_first";
}

async function rematch() {
  if (!room || !isTerminal(room)) return;
  const previousRoom = room;
  $("rematchButton").disabled = true;
  $("resultModalMessage").textContent = "";
  try {
    const data = await request("/api/rooms", {
      method: "POST",
      body: JSON.stringify({
        ai_player: previousRoom.ai_player_id,
        game_type: previousRoom.game_type,
        mode: oppositeMode(previousRoom.mode),
      }),
    });
    closeResultModal();
    renderGame(data.room, data.message, data.timeline);
    startRoomPolling();
  } catch (error) {
    $("resultModalMessage").textContent = error.message;
    $("rematchButton").disabled = false;
  }
}

function startRoomPolling() {
  stopPolling();
  pollTimer = setInterval(() => refreshRoom({quiet: true}), 3000);
}

function stopPolling() {
  if (pollTimer) clearInterval(pollTimer);
  pollTimer = null;
}

$("createButton").addEventListener("click", createRoom);
$("aiPlayer").addEventListener("change", renderSelectedParticipants);
$("gameType").addEventListener("change", renderSelectedParticipants);
$("mode").addEventListener("change", updateCreateButtonState);
$("refreshRoomsButton").addEventListener("click", () => loadIdentity());
$("backButton").addEventListener("click", backToLobby);
$("refreshButton").addEventListener("click", () => refreshRoom());
$("sendMessageButton").addEventListener("click", sendMessage);
$("confirmMoveButton").addEventListener("click", confirmMove);
$("resignButton").addEventListener("click", resign);
$("rulesButton").addEventListener("click", openRules);
$("closeRulesButton").addEventListener("click", closeRules);
$("historyDrawerTab").addEventListener("click", openHistory);
$("closeHistoryButton").addEventListener("click", closeHistory);
$("historyDrawer").addEventListener("click", (event) => {
  if (event.target === $("historyDrawer")) closeHistory();
});
$("rematchButton").addEventListener("click", rematch);
$("finishGameButton").addEventListener("click", closeResultModal);
$("rulesScrim").addEventListener("click", (event) => {
  if (event.target === $("rulesScrim")) closeRules();
});
$("chatInput").addEventListener("keydown", (event) => {
  if (event.key === "Enter") {
    event.preventDefault();
    sendMessage();
  }
});
window.addEventListener("keydown", (event) => {
  if (event.key === "Escape") {
    closeRules();
    closeHistory();
  }
});

loadIdentity();

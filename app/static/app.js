const $ = (id) => document.getElementById(id);
let identity = null;
let room = null;
let pollTimer = null;
let toastTimer = null;
let visibleWaitModalRoomId = null;
const waitHintShownRooms = new Set();
let selectedJungleCell = null;
let pendingMove = null;
let currentTimeline = [];
let lastMoveMarkerKey = null;
let selectedMachineWallet = null;
let machineWalletRequest = 0;

const WAIT_HINT_STORAGE_PREFIX = "duel:wait-mode-hint";
const WAIT_HINT_FOREVER = "forever";

const GAME_GLYPHS = {
  tictactoe: "井",
  gomoku: "五",
  othello: "黑",
  connect4: "四",
  dots_boxes: "点",
  liars_dice: "骰",
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

function localDateString(date = new Date()) {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function waitHintHumanId(targetRoom) {
  if (targetRoom && targetRoom.human_player_id) {
    return targetRoom.human_player_id;
  }
  const participant = targetRoom && Array.isArray(targetRoom.participants)
    ? targetRoom.participants.find((item) => item.role === "human")
    : null;
  return participant ? participant.player_id : "browser";
}

function waitHintPreferenceKey(targetRoom) {
  return `${WAIT_HINT_STORAGE_PREFIX}:${waitHintHumanId(targetRoom)}`;
}

function readWaitHintPreference(targetRoom, storage = window.localStorage) {
  try {
    return storage.getItem(waitHintPreferenceKey(targetRoom));
  } catch (_error) {
    return null;
  }
}

function shouldShowWaitModeHint(
  targetRoom,
  storage = window.localStorage,
  today = localDateString()
) {
  const preference = readWaitHintPreference(targetRoom, storage);
  return preference !== WAIT_HINT_FOREVER && preference !== today;
}

function saveWaitHintPreference(
  targetRoom,
  value,
  storage = window.localStorage
) {
  try {
    storage.setItem(waitHintPreferenceKey(targetRoom), value);
  } catch (_error) {
    // Storage can be unavailable in strict privacy modes; closing still works now.
  }
}

function hideWaitModeModal() {
  visibleWaitModalRoomId = null;
  $("waitModeModal").classList.add("hidden");
  $("waitModeModal").setAttribute("aria-hidden", "true");
}

function closeWaitModeModal(
  permanently,
  targetRoom = room,
  storage = window.localStorage,
  today = localDateString()
) {
  if (targetRoom) {
    saveWaitHintPreference(
      targetRoom,
      permanently ? WAIT_HINT_FOREVER : today,
      storage
    );
  }
  hideWaitModeModal();
}

function showWaitModeModalOnce(
  targetRoom,
  storage = window.localStorage,
  today = localDateString()
) {
  const visitKey = `${waitHintPreferenceKey(targetRoom)}:${targetRoom.room_id}`;
  if (targetRoom.status !== "playing" || !shouldShowWaitModeHint(targetRoom, storage, today)) {
    hideWaitModeModal();
    return false;
  }
  if (waitHintShownRooms.has(visitKey)) {
    if (visibleWaitModalRoomId !== targetRoom.room_id) hideWaitModeModal();
    return false;
  }
  waitHintShownRooms.add(visitKey);
  visibleWaitModalRoomId = targetRoom.room_id;
  $("waitModeModal").classList.remove("hidden");
  $("waitModeModal").setAttribute("aria-hidden", "false");
  $("dismissWaitModeModalButton").focus();
  return true;
}

function statusLabel(status) {
  return {
    pending: "待确认",
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

function participantByPlayerId(playerId) {
  if (!room || !Array.isArray(room.participants)) return null;
  return room.participants.find((item) => item.player_id === playerId) || null;
}

function participantForOwner(owner) {
  if (!owner || !room || !Array.isArray(room.participants)) return null;
  const direct = room.participants.find(
    (item) => item.player_id === owner || item.token === owner
  );
  if (direct) return direct;
  const marks = room.board_state && room.board_state.marks;
  if (marks && owner === marks.human) return participantFor("human");
  if (marks && owner === marks.ai) return participantFor("ai");
  return null;
}

function participantAvatarFallback(participant) {
  const name = participant && String(participant.display_name || "").trim();
  if (name) return Array.from(name)[0];
  const seatIndex = participant ? Number(participant.seat_index) : Number.NaN;
  return Number.isInteger(seatIndex) ? String(seatIndex + 1) : "?";
}

function renderParticipantAvatar(target, participant) {
  target.replaceChildren();
  const fallback = participantAvatarFallback(participant);
  target.textContent = fallback;
  target.setAttribute(
    "aria-label",
    participant && participant.display_name ? `${participant.display_name}的头像` : "玩家头像"
  );
  if (!participant || !participant.avatar_url) return;
  const avatar = document.createElement("img");
  avatar.src = apiPath(participant.avatar_url);
  avatar.alt = "";
  avatar.loading = "lazy";
  avatar.addEventListener("error", () => {
    avatar.remove();
    target.textContent = fallback;
  }, {once: true});
  target.replaceChildren(avatar);
}

function turnLabel(turn, aiPlayerId, currentActor = null) {
  if (!identity) return turn;
  if (currentActor) {
    const humanId = (room && room.human_player_id)
      || (currentActor.role === "human" ? currentActor.player_id : null);
    return currentActor.player_id === humanId
      ? "轮到你"
      : `轮到 ${currentActor.display_name || currentActor.player_id}`;
  }
  return turn === "human" ? "轮到你" : `轮到 ${aiNameFor(aiPlayerId)}`;
}

function roomTurnText(targetRoom) {
  if (isTerminal(targetRoom)) {
    return targetRoom.status === "archived" ? "对局已归档" : "对局已结束";
  }
  if (targetRoom.status === "pending") return "等待对方确认";
  return turnLabel(
    targetRoom.turn, targetRoom.ai_player_id, targetRoom.current_actor
  );
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

function retentionTextFor(targetRoom) {
  if (!isTerminal(targetRoom)) return "";
  if (targetRoom.preserved) return "已保留 · 不会自动删除";
  const deadline = new Date(targetRoom.auto_delete_at).getTime();
  if (!Number.isFinite(deadline)) return "终局 7 天后自动删除";
  const remaining = deadline - Date.now();
  if (remaining <= 0) return "即将自动删除";
  const days = Math.ceil(remaining / 86400000);
  return `${days} 天后自动删除`;
}

function retentionDeadlineTitle(targetRoom) {
  if (!targetRoom || targetRoom.preserved || !targetRoom.auto_delete_at) return "";
  const deadline = new Date(targetRoom.auto_delete_at);
  if (!Number.isFinite(deadline.getTime())) return "";
  return `预计 ${deadline.toLocaleString("zh-CN")} 自动删除`;
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
    const card = document.createElement("article");
    card.className = "room-card";

    const open = document.createElement("button");
    open.className = "room-open";
    open.type = "button";
    open.setAttribute("aria-label", `进入${summary.game_name}房间 ${summary.room_id}`);

    const glyph = document.createElement("span");
    glyph.className = "room-glyph";
    glyph.textContent = GAME_GLYPHS[summary.game_type] || "棋";

    const copy = document.createElement("span");
    copy.className = "room-copy";
    const title = document.createElement("span");
    title.className = "room-title";
    title.textContent = summary.participant_names && summary.participant_names.length > 2
      ? `${summary.game_name} × ${summary.participant_names.join(" / ")}`
      : `${summary.game_name} × ${summary.ai_name}`;
    const meta = document.createElement("span");
    meta.className = "room-meta";
    meta.textContent = `${summary.room_id} · ${statusLabel(summary.status)} · 更新于 ${relativeTime(summary.updated_at)}`;
    const stake = document.createElement("span");
    stake.className = "room-stake";
    stake.textContent = summary.stake_label || (summary.stake > 0 ? `🪙${summary.stake}/人` : "娱乐局");
    copy.append(title, meta, stake);

    const state = document.createElement("span");
    state.className = "room-state";
    const turn = document.createElement("span");
    turn.className = "turn";
    turn.textContent = summary.status === "playing"
      ? turnLabel(summary.turn, summary.ai_player_id, summary.current_actor)
      : (summary.winner === "draw" ? "和棋" : statusLabel(summary.status));
    const enter = document.createElement("span");
    enter.className = "room-enter";
    enter.textContent = "进入 →";
    state.append(turn, enter);

    open.append(glyph, copy, state);
    open.addEventListener("click", () => openRoom(summary.room_id));
    card.appendChild(open);

    if (isTerminal(summary)) {
      const controls = document.createElement("div");
      controls.className = "room-record-controls";

      const retention = document.createElement("span");
      retention.className = `room-retention${summary.preserved ? " preserved" : ""}`;
      retention.textContent = retentionTextFor(summary);
      retention.title = retentionDeadlineTitle(summary);

      const preserve = document.createElement("button");
      preserve.className = "room-record-button";
      preserve.type = "button";
      preserve.textContent = summary.preserved ? "取消保留" : "保留";
      preserve.addEventListener("click", () => {
        updateRoomPreservation(summary.room_id, !summary.preserved);
      });

      const remove = document.createElement("button");
      remove.className = "room-record-button danger";
      remove.type = "button";
      remove.textContent = "删除对局";
      remove.addEventListener("click", () => deleteRoom(summary));

      controls.append(retention, preserve, remove);
      card.appendChild(controls);
    }
    list.appendChild(card);
  });
}

function renderPendingInvitations(invitations = []) {
  const panel = $("pendingPanel");
  const list = $("pendingList");
  list.replaceChildren();
  panel.classList.toggle("hidden", invitations.length === 0);
  invitations.forEach((invitation) => {
    const card = document.createElement("article");
    card.className = "pending-card";
    const copy = document.createElement("div");
    copy.className = "pending-copy";
    const title = document.createElement("strong");
    title.className = "pending-title";
    title.textContent = `${invitation.initiator_name} 发起的${invitation.game_name}`;
    const meta = document.createElement("span");
    meta.className = "pending-meta";
    meta.textContent = `发起方：${invitation.initiator_name} · 棋种：${invitation.game_name} · ${invitation.stake_label}`;
    copy.append(title, meta);
    const accept = document.createElement("button");
    accept.className = "pixel-btn";
    accept.type = "button";
    accept.textContent = "接受";
    accept.addEventListener("click", () => respondToInvitation(invitation.room_id, "accept"));
    const reject = document.createElement("button");
    reject.className = "pixel-btn secondary";
    reject.type = "button";
    reject.textContent = "拒绝";
    reject.addEventListener("click", () => respondToInvitation(invitation.room_id, "reject"));
    card.append(copy, accept, reject);
    list.appendChild(card);
  });
}

async function respondToInvitation(roomId, decision) {
  try {
    const data = await request(`/api/rooms/${roomId}/invitation`, {
      method: "POST",
      body: JSON.stringify({decision}),
    });
    if (decision === "accept" && data.room) {
      renderGame(data.room, data.message, data.timeline);
      startRoomPolling();
      return;
    }
    await loadIdentity({quiet: true});
    toast(data.message);
  } catch (error) {
    toast(error.message);
    await loadIdentity({quiet: true});
  }
}

async function updateRoomPreservation(roomId, preserved, {fromModal = false} = {}) {
  try {
    const data = await request(`/api/rooms/${roomId}/retention`, {
      method: "POST",
      body: JSON.stringify({preserved}),
    });
    if (room && room.room_id === roomId) {
      renderGame(data.room, data.message, data.timeline);
    } else {
      await loadIdentity({quiet: true});
    }
    if (fromModal) $("resultModalMessage").textContent = data.message;
    toast(data.message);
    return true;
  } catch (error) {
    if (fromModal) $("resultModalMessage").textContent = error.message;
    else toast(error.message);
    return false;
  }
}

function syncResultPreservationChoice(preserved, disabled = false) {
  $("resultPreserveCheckbox").checked = Boolean(preserved);
  $("resultPreserveCheckbox").disabled = disabled;
  $("resultRetentionHint").textContent = preserved
    ? "已保留，不会自动删除"
    : "终局 7 天后自动删除";
}

async function changeResultPreservation() {
  const checkbox = $("resultPreserveCheckbox");
  if (!room || !isTerminal(room)) {
    syncResultPreservationChoice(Boolean(room && room.preserved), true);
    return;
  }
  const roomId = room.room_id;
  const previousValue = Boolean(room.preserved);
  const requestedValue = checkbox.checked;
  syncResultPreservationChoice(requestedValue, true);
  $("resultModalMessage").textContent = "";
  const updated = await updateRoomPreservation(
    roomId,
    requestedValue,
    {fromModal: true}
  );
  const sameTerminalRoom = Boolean(
    room && room.room_id === roomId && isTerminal(room)
  );
  const authoritativeValue = updated && sameTerminalRoom
    ? Boolean(room.preserved)
    : previousValue;
  syncResultPreservationChoice(authoritativeValue, !sameTerminalRoom);
  if (updated && sameTerminalRoom) {
    $("resultModalMessage").textContent = "";
  }
}

async function deleteRoom(summary) {
  const confirmed = window.confirm(
    `确定删除房间 ${summary.room_id}？棋谱和聊天记录会一并永久删除，无法恢复。`
  );
  if (!confirmed) return;
  try {
    const data = await request(`/api/rooms/${summary.room_id}/delete`, {
      method: "POST",
      body: "{}",
    });
    await loadIdentity({quiet: true});
    toast(data.message);
  } catch (error) {
    toast(error.message);
  }
}

function selectedParticipantIds() {
  return [...$("aiPlayer").selectedOptions]
    .map((option) => option.value)
    .filter(Boolean);
}

function allowedPlayerCountsForGame(declared) {
  const minimum = Number.isInteger(declared && declared.min_players)
    ? declared.min_players
    : 2;
  const maximum = Number.isInteger(declared && declared.max_players)
    ? declared.max_players
    : minimum;
  const rawCounts = declared && Array.isArray(declared.allowed_player_counts)
    ? declared.allowed_player_counts
    : Array.from(
        {length: Math.max(0, maximum - minimum + 1)},
        (_item, index) => minimum + index
      );
  const normalized = [...new Set(rawCounts)]
    .filter((count) => Number.isInteger(count) && count >= 2 && count <= 6)
    .sort((left, right) => left - right);
  return normalized.length ? normalized : [2];
}

function gamePlayerCountLabel(declared) {
  const counts = allowedPlayerCountsForGame(declared);
  if (counts.length === 1) return `${counts[0]}人`;
  const continuous = counts.every(
    (count, index) => count === counts[0] + index
  );
  return continuous
    ? `${counts[0]}–${counts[counts.length - 1]}人`
    : `${counts.join("、")}人`;
}

function syncGameTypeOptions(games) {
  if (!Array.isArray(games) || !games.length) return;
  const select = $("gameType");
  const previousValue = select.value;
  select.replaceChildren();
  games.forEach((game) => {
    if (!game || !game.game_type) return;
    const option = document.createElement("option");
    option.value = game.game_type;
    option.textContent = `${game.display_name || game.game_type} / ${gamePlayerCountLabel(game)}`;
    select.appendChild(option);
  });
  const values = [...select.options].map((option) => option.value);
  if (values.includes(previousValue)) select.value = previousValue;
}

function selectedGameRequirement() {
  const gameType = $("gameType").value;
  const declared = identity && Array.isArray(identity.games)
    ? identity.games.find((game) => game.game_type === gameType)
    : null;
  const allowedPlayerCounts = allowedPlayerCountsForGame(declared);
  const providerAvailable = Boolean(identity && identity.npc_provider && identity.npc_provider.available);
  return {
    minPlayers: allowedPlayerCounts[0] || 2,
    maxPlayers: allowedPlayerCounts[allowedPlayerCounts.length - 1] || 2,
    allowedPlayerCounts,
    recommendedPlayers: declared ? declared.recommended_players : 2,
    supportsNpcs: Boolean(declared && declared.supports_npcs),
    npcAvailable: providerAvailable,
  };
}

function selectedTargetPlayerCount() {
  const {maxPlayers} = selectedGameRequirement();
  if (maxPlayers <= 2) return 2;
  return Number($("targetPlayerCount").value || 2);
}

function selectedFillWithNpcs() {
  const requirement = selectedGameRequirement();
  return requirement.maxPlayers > 2
    && requirement.supportsNpcs
    && requirement.npcAvailable
    && $("fillWithNpcs").checked;
}

function configureParticipantPicker() {
  const select = $("aiPlayer");
  const {
    maxPlayers, allowedPlayerCounts, recommendedPlayers, supportsNpcs, npcAvailable,
  } = selectedGameRequirement();
  const multiplayer = maxPlayers > 2;
  const selected = selectedParticipantIds();
  const options = $("multiplayerOptions");
  options.classList.toggle("hidden", !multiplayer);
  const targetSelect = $("targetPlayerCount");
  if (multiplayer) {
    const previousTarget = Number(targetSelect.value);
    targetSelect.replaceChildren();
    allowedPlayerCounts.forEach((count) => {
      const option = document.createElement("option");
      option.value = String(count);
      option.textContent = `${count} 人桌`;
      targetSelect.appendChild(option);
    });
    const allowed = [...targetSelect.options].map((option) => Number(option.value));
    const preferred = allowed.includes(previousTarget)
      ? previousTarget
      : (allowed.includes(recommendedPlayers) ? recommendedPlayers : allowed[0]);
    targetSelect.value = String(preferred);
    $("fillWithNpcs").disabled = !supportsNpcs || !npcAvailable;
    if (!supportsNpcs || !npcAvailable) $("fillWithNpcs").checked = false;
    $("npcProviderHint").textContent = !supportsNpcs
      ? "该游戏不提供 NPC 补位"
      : (!npcAvailable ? "部署者尚未配置 NPC 通道" : "每桌最多补入 4 名 NPC");
  }
  select.multiple = multiplayer;
  select.closest(".participant-picker").dataset.selectionMode = multiplayer
    ? "multiple"
    : "single";
  if (multiplayer) {
    select.size = Math.max(2, Math.min(selectedTargetPlayerCount() - 1, 5));
  } else {
    select.removeAttribute("size");
    if (selected.length > 1) select.value = selected[0];
  }
  renderSelectedParticipants();
}

function updateCreateButtonState() {
  const {
    allowedPlayerCounts, supportsNpcs, npcAvailable,
  } = selectedGameRequirement();
  const selectedMachineCount = selectedParticipantIds().length;
  const targetCount = selectedTargetPlayerCount();
  const npcCount = targetCount - 1 - selectedMachineCount;
  const fillWithNpcs = selectedFillWithNpcs();
  const stakeValue = Number($("stake").value);
  const validStake = Number.isInteger(stakeValue) && stakeValue >= 0;
  const ready = Boolean(
    identity
    && $("gameType").value
    && $("mode").value
    && selectedMachineCount >= 1
    && allowedPlayerCounts.includes(targetCount)
    && npcCount >= 0
    && npcCount <= 4
    && (npcCount === 0 || (fillWithNpcs && supportsNpcs && npcAvailable))
    && validStake
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
  configureParticipantPicker();
  machineSelectionChanged();
}

function renderSelectedParticipants() {
  const selectedIds = selectedParticipantIds();
  const machines = identity
    ? identity.machines.filter((item) => selectedIds.includes(item.id))
    : [];
  const {allowedPlayerCounts} = selectedGameRequirement();
  const requirement = `${allowedPlayerCounts.join("/")} 人局`;
  const machineBalance = selectedMachineWallet && machines.length === 1
    ? ` · 对手筹码：🪙${selectedMachineWallet.balance}`
    : "";
  $("selectedParticipants").textContent = machines.length
    ? `本局参与小机：${machines.map((item) => item.name).join("、")}${machineBalance} · ${requirement}`
    : `本局尚未选择对手 · ${requirement}`;
  renderSeatPreview(machines);
  updateCreateButtonState();
}

function renderSeatPreview(machines) {
  const preview = $("seatPreview");
  const {maxPlayers} = selectedGameRequirement();
  preview.replaceChildren();
  preview.classList.toggle("hidden", maxPlayers <= 2);
  if (maxPlayers <= 2) return;
  const targetCount = selectedTargetPlayerCount();
  [...preview.classList]
    .filter((name) => name.startsWith("count-"))
    .forEach((name) => preview.classList.remove(name));
  preview.classList.add(`count-${targetCount}`);
  const npcCount = Math.max(0, targetCount - 1 - machines.length);
  const fill = selectedFillWithNpcs();
  const seats = [
    {label: (identity && identity.human_name) || "你", kind: "人类"},
    ...machines.map((machine) => ({label: machine.name, kind: "小机"})),
    ...Array.from({length: npcCount}, (_item, index) => ({
      label: fill ? `随机 NPC ${index + 1}` : "空位",
      kind: fill ? "NPC" : "未补齐",
    })),
  ];
  seats.slice(0, targetCount).forEach((seat, index) => {
    const item = document.createElement("span");
    item.className = "seat-preview-item";
    item.textContent = `${index + 1} · ${seat.label}（${seat.kind}）`;
    preview.appendChild(item);
  });
}

async function machineSelectionChanged() {
  const selectedId = selectedParticipantIds()[0];
  selectedMachineWallet = null;
  const requestNumber = ++machineWalletRequest;
  renderSelectedParticipants();
  if (!selectedId || selectedParticipantIds().length !== 1) return;
  try {
    const data = await request(`/api/chips/machines/${encodeURIComponent(selectedId)}`);
    if (requestNumber !== machineWalletRequest) return;
    selectedMachineWallet = data.wallet;
    renderSelectedParticipants();
  } catch (_error) {
    if (requestNumber === machineWalletRequest) renderSelectedParticipants();
  }
}

function renderHumanChipBalance(balance) {
  const numericBalance = Number(balance);
  const isNumeric = Number.isFinite(numericBalance);
  const balanceText = isNumeric
    ? new Intl.NumberFormat("zh-CN", {maximumFractionDigits: 0}).format(numericBalance)
    : String(balance ?? "—");
  const negative = isNumeric && numericBalance < 0;
  const longBalance = balanceText.length > 10;
  const balanceTarget = $("humanChipBalance");
  const chipLink = $("chipCenterLink");
  balanceTarget.textContent = balanceText;
  balanceTarget.title = `当前余额：${balanceText}`;
  balanceTarget.setAttribute("aria-label", `当前人类筹码余额 ${balanceText}`);
  chipLink.classList.toggle("negative", negative);
  chipLink.classList.toggle("long-balance", longBalance);
  chipLink.setAttribute("aria-label", `我的筹码，余额 ${balanceText}，进入筹码中心`);
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
    renderHumanChipBalance(data.wallet.balance);
    syncGameTypeOptions(data.games || []);
    syncMachinePicker(data.machines || []);
    renderPendingInvitations(data.pending_invitations || []);
    const incoming = new Set((data.pending_invitations || []).map((item) => item.room_id));
    renderRooms((data.rooms || []).filter((item) => !incoming.has(item.room_id)));
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
  const participantIds = selectedParticipantIds();
  const aiPlayer = participantIds[0];
  const stake = Number($("stake").value);
  try {
    $("createButton").disabled = true;
    const data = await request("/api/rooms", {
      method: "POST",
      body: JSON.stringify({
        ai_player: aiPlayer,
        ai_players: participantIds,
        game_type: $("gameType").value,
        mode: $("mode").value,
        stake,
        target_player_count: selectedTargetPlayerCount(),
        fill_with_npcs: selectedFillWithNpcs(),
      }),
    });
    if (data.room.status === "pending") {
      room = null;
      await loadIdentity({quiet: true});
      showNotice(data.message);
      return;
    }
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
  hideWaitModeModal();
  closeHistory();
  showView("lobbyView");
  loadIdentity({quiet: true});
}

function canHumanMove() {
  const human = participantFor("human");
  return Boolean(
    room
    && human
    && room.status === "playing"
    && room.current_player_id === human.player_id
  );
}

function pieceClass(mark) {
  if (!mark || !room) return "";
  const owner = participantForOwner(mark);
  if (!owner) return "";
  const viewerId = room.viewer && room.viewer.player_id;
  const legacy = owner.player_id === viewerId
    ? " human-piece"
    : (owner.role === "ai" ? " ai-piece" : "");
  return `${legacy} participant-piece seat-${owner.seat_index}`;
}

function markClass(mark) {
  return mark === "X" || mark === "O" ? ` mark-${mark.toLowerCase()}` : "";
}

function ownerDescription(mark) {
  if (!mark || !room) return "空位";
  const owner = participantForOwner(mark);
  if (!owner) return "未知参与者";
  if (room.viewer && owner.player_id === room.viewer.player_id) return "你";
  return owner.display_name || `座位 ${owner.seat_index + 1}`;
}

function pieceDescription(mark) {
  if (!mark) return "空位";
  if (["gomoku", "othello"].includes(room.game_type)) {
    return `${mark === "X" ? "黑棋" : "白棋"}（${ownerDescription(mark)}）`;
  }
  if (room.game_type === "connect4") return `${ownerDescription(mark)}棋片`;
  return `${ownerDescription(mark)}棋子 ${mark}`;
}

function boardCell(mark, rowIndex, colIndex, onClick) {
  const cell = document.createElement("button");
  cell.className = `cell${mark ? " occupied" : ""}${pieceClass(mark)}${markClass(mark)}`;
  cell.type = "button";
  cell.dataset.moveRow = String(rowIndex);
  cell.dataset.moveCol = String(colIndex);
  if (room.game_type === "tictactoe") cell.textContent = mark || "";
  if (["gomoku", "othello", "connect4"].includes(room.game_type)) {
    const piece = document.createElement("span");
    piece.className = "piece";
    piece.setAttribute("aria-hidden", "true");
    cell.appendChild(piece);
  }
  cell.disabled = !canHumanMove() || Boolean(mark);
  cell.ariaLabel = (
    `第 ${rowIndex + 1} 行第 ${colIndex + 1} 列，${pieceDescription(mark)}`
  );
  cell.addEventListener("click", onClick);
  return cell;
}

function selectCell(cell, payload, state) {
  const selected = movesEqual(pendingMove, payload);
  cell.classList.toggle("selected", selected);
  cell.setAttribute("aria-pressed", String(selected));
  if (selected && !cell.classList.contains("occupied")) {
    cell.classList.add(`preview-${state.marks.human.toLowerCase()}`);
  }
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
  $("confirmMoveButton").textContent = pendingMove && pendingMove.action === "challenge"
    ? "确认质疑"
    : (pendingMove && pendingMove.action === "bid" ? "确认叫点" : "落子");
  if (isTerminal(room)) {
    $("selectionHint").textContent = roomTurnText(room);
  } else {
    const readyText = pendingMove && pendingMove.action === "challenge"
      ? "已选择质疑，确认后将公开并结算本轮骰子"
      : (pendingMove && pendingMove.action === "bid"
        ? `已选择叫 ${pendingMove.quantity} 个 ${pendingMove.face} 点`
        : "已选中落点，确认后提交");
    const waitingText = room && room.game_type === "liars_dice"
      ? "请选择叫点，已有叫点时也可质疑"
      : "请先在棋盘上选择落点";
    $("selectionHint").textContent = ready
      ? readyText
      : (canHumanMove() ? waitingText : "等待轮到你");
  }
}

function renderGridBoard(board, state) {
  state.board.forEach((rowData, rowIndex) => {
    rowData.forEach((mark, colIndex) => {
      const payload = {row: rowIndex, col: colIndex};
      const cell = boardCell(mark, rowIndex, colIndex, () => selectMove(payload));
      selectCell(cell, payload, state);
      board.appendChild(cell);
    });
  });
}

function renderGomokuBoard(board, state) {
  const lastIndex = state.size - 1;
  const starPoints = new Set(["3,3", "3,11", "7,7", "11,3", "11,11"]);
  state.board.forEach((rowData, rowIndex) => {
    rowData.forEach((mark, colIndex) => {
      const payload = {row: rowIndex, col: colIndex};
      const cell = boardCell(mark, rowIndex, colIndex, () => selectMove(payload));
      if (rowIndex === 0) cell.classList.add("edge-top");
      if (rowIndex === lastIndex) cell.classList.add("edge-bottom");
      if (colIndex === 0) cell.classList.add("edge-left");
      if (colIndex === lastIndex) cell.classList.add("edge-right");
      if (starPoints.has(`${rowIndex},${colIndex}`)) cell.classList.add("star-point");
      selectCell(cell, payload, state);
      board.appendChild(cell);
    });
  });
}

function connect4LandingRow(state, colIndex) {
  for (let rowIndex = state.rows - 1; rowIndex >= 0; rowIndex -= 1) {
    if (state.board[rowIndex][colIndex] === null) return rowIndex;
  }
  return -1;
}

function renderConnect4Board(board, state) {
  const landingRows = Array.from(
    {length: state.cols},
    (_, colIndex) => connect4LandingRow(state, colIndex)
  );
  state.board.forEach((rowData, rowIndex) => {
    rowData.forEach((mark, colIndex) => {
      const payload = {col: colIndex};
      const cell = boardCell(mark, rowIndex, colIndex, () => selectMove(payload));
      const landingRow = landingRows[colIndex];
      cell.classList.add("column-button");
      cell.disabled = !canHumanMove() || landingRow < 0;
      cell.ariaLabel = mark
        ? `第 ${colIndex + 1} 列，第 ${rowIndex + 1} 行为${pieceDescription(mark)}；选择此列`
        : `第 ${colIndex + 1} 列空位；选择后棋片落到第 ${landingRow + 1} 行`;
      const selected = movesEqual(pendingMove, payload) && rowIndex === landingRow;
      cell.classList.toggle("selected", selected);
      cell.setAttribute("aria-pressed", String(selected));
      if (selected) cell.classList.add(`preview-${state.marks.human.toLowerCase()}`);
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
        edge.dataset.moveOrientation = "h";
        edge.dataset.moveRow = String(rowIndex);
        edge.dataset.moveCol = String(colIndex);
        edge.disabled = !canHumanMove() || Boolean(mark);
        edge.ariaLabel = mark
          ? `第 ${rowIndex + 1} 行第 ${colIndex + 1} 条横边，${ownerDescription(mark)}已画`
          : `第 ${rowIndex + 1} 行第 ${colIndex + 1} 条横边，未画`;
        const payload = {orientation: "h", row: rowIndex, col: colIndex};
        const selected = movesEqual(pendingMove, payload);
        edge.classList.toggle("selected", selected);
        edge.setAttribute("aria-pressed", String(selected));
        edge.addEventListener("click", () => selectMove(payload));
        board.appendChild(edge);
      } else if (gridCol % 2 === 0) {
        const rowIndex = (gridRow - 1) / 2;
        const colIndex = gridCol / 2;
        const mark = state.vertical_edges[rowIndex][colIndex];
        const edge = document.createElement("button");
        edge.type = "button";
        edge.className = `edge vertical${mark ? " drawn" : ""}${pieceClass(mark)}`;
        edge.dataset.moveOrientation = "v";
        edge.dataset.moveRow = String(rowIndex);
        edge.dataset.moveCol = String(colIndex);
        edge.disabled = !canHumanMove() || Boolean(mark);
        edge.ariaLabel = mark
          ? `第 ${rowIndex + 1} 行第 ${colIndex + 1} 条竖边，${ownerDescription(mark)}已画`
          : `第 ${rowIndex + 1} 行第 ${colIndex + 1} 条竖边，未画`;
        const payload = {orientation: "v", row: rowIndex, col: colIndex};
        const selected = movesEqual(pendingMove, payload);
        edge.classList.toggle("selected", selected);
        edge.setAttribute("aria-pressed", String(selected));
        edge.addEventListener("click", () => selectMove(payload));
        board.appendChild(edge);
      } else {
        const box = document.createElement("span");
        const owner = state.boxes[(gridRow - 1) / 2][(gridCol - 1) / 2];
        box.className = `box${owner ? " owned" : ""}${pieceClass(owner)}`;
        if (owner) {
          const boxRow = (gridRow - 1) / 2;
          const boxCol = (gridCol - 1) / 2;
          box.setAttribute("role", "img");
          box.ariaLabel = `第 ${boxRow + 1} 行第 ${boxCol + 1} 格归${ownerDescription(owner)}所有`;
        } else {
          box.setAttribute("aria-hidden", "true");
        }
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
      cell.dataset.moveRow = String(rowIndex);
      cell.dataset.moveCol = String(colIndex);
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

function renderLiarsDice(board, state) {
  const heading = document.createElement("div");
  heading.className = "liars-current-bid";
  const bidLabel = document.createElement("span");
  bidLabel.textContent = "CURRENT BID";
  const bidValue = document.createElement("strong");
  const currentBid = state.current_bid;
  if (currentBid) {
    const bidder = participantByPlayerId(currentBid.bidder_player_id);
    bidValue.textContent = `${currentBid.quantity} 个 ${currentBid.face} 点 · ${(bidder && bidder.display_name) || currentBid.bidder_player_id}`;
  } else {
    bidValue.textContent = "等待本轮首叫";
  }
  heading.append(bidLabel, bidValue);
  board.appendChild(heading);

  if (state.last_round_result) {
    const reveal = document.createElement("section");
    reveal.className = "liars-reveal";
    const title = document.createElement("strong");
    const outcome = state.last_round_result;
    title.textContent = `上一轮：实际 ${outcome.actual_count} 个 ${outcome.bid.face} 点 · ${outcome.bid_holds ? "叫点成立" : "叫点失败"}`;
    reveal.appendChild(title);
    const diceList = document.createElement("div");
    diceList.className = "liars-revealed-dice";
    Object.entries(outcome.revealed_dice_by_player || {}).forEach(([playerId, dice]) => {
      const row = document.createElement("span");
      const participant = participantByPlayerId(playerId);
      row.textContent = `${(participant && participant.display_name) || playerId}：${dice.join(" · ") || "已淘汰"}`;
      diceList.appendChild(row);
    });
    reveal.appendChild(diceList);
    board.appendChild(reveal);
  }

  const controls = document.createElement("div");
  controls.className = "liars-controls";
  const quantityLabel = document.createElement("label");
  quantityLabel.textContent = "数量";
  const quantity = document.createElement("select");
  quantity.ariaLabel = "叫点数量";
  const faceLabel = document.createElement("label");
  faceLabel.textContent = "点数";
  const face = document.createElement("select");
  face.ariaLabel = "叫点点数";
  const maximum = Number(state.max_bid_quantity || 0);
  for (let value = 1; value <= maximum; value += 1) {
    const option = document.createElement("option");
    option.value = String(value);
    option.textContent = String(value);
    quantity.appendChild(option);
  }
  for (let value = 1; value <= 6; value += 1) {
    const option = document.createElement("option");
    option.value = String(value);
    option.textContent = String(value);
    face.appendChild(option);
  }
  if (currentBid) {
    quantity.value = String(currentBid.quantity);
    face.value = String(Math.min(6, currentBid.face + 1));
    if (currentBid.face === 6 && currentBid.quantity < maximum) {
      quantity.value = String(currentBid.quantity + 1);
      face.value = "1";
    }
  }
  quantityLabel.appendChild(quantity);
  faceLabel.appendChild(face);
  const chooseBid = document.createElement("button");
  chooseBid.type = "button";
  chooseBid.className = "pixel-btn compact";
  chooseBid.textContent = "选择叫点";
  const selectionIsHigher = () => !currentBid
    || Number(quantity.value) > currentBid.quantity
    || (
      Number(quantity.value) === currentBid.quantity
      && Number(face.value) > currentBid.face
    );
  const updateBidAvailability = () => {
    chooseBid.disabled = !canHumanMove() || !selectionIsHigher();
  };
  updateBidAvailability();
  quantity.addEventListener("change", updateBidAvailability);
  face.addEventListener("change", updateBidAvailability);
  chooseBid.addEventListener("click", () => selectMove({
    action: "bid",
    quantity: Number(quantity.value),
    face: Number(face.value),
  }));
  const challenge = document.createElement("button");
  challenge.type = "button";
  challenge.className = "pixel-btn danger compact";
  challenge.textContent = "质疑上一手";
  challenge.disabled = !canHumanMove() || !currentBid;
  challenge.addEventListener("click", () => selectMove({action: "challenge"}));
  controls.append(quantityLabel, faceLabel, chooseBid, challenge);
  board.appendChild(controls);
}

function latestMoveEvent(timeline = []) {
  return [...timeline].reverse().find(
    (event) => event.event_type === "move" && event.move && typeof event.move === "object"
  ) || null;
}

function authoritativeLastMove(targetRoom, timeline = []) {
  if (!targetRoom || targetRoom.game_type === "liars_dice") return null;
  const stateMove = targetRoom.board_state && targetRoom.board_state.last_move;
  const event = latestMoveEvent(timeline);
  const move = stateMove || (event && event.move);
  if (!move || typeof move !== "object") return null;

  let row = move.row;
  let col = move.col;
  let orientation = null;
  if (targetRoom.game_type === "jungle") {
    row = move.to_row;
    col = move.to_col;
  } else if (targetRoom.game_type === "dots_boxes") {
    orientation = move.orientation;
    if (!["h", "v"].includes(orientation)) return null;
  }
  if (!Number.isInteger(row) || !Number.isInteger(col)) return null;

  return {
    row,
    col,
    orientation,
    revision: event && Number.isInteger(event.revision_at_send)
      ? event.revision_at_send
      : targetRoom.revision,
  };
}

function renderLastMoveMarker(board, timeline = []) {
  const move = authoritativeLastMove(room, timeline);
  if (!move) return;
  const selector = move.orientation
    ? `.edge[data-move-orientation="${move.orientation}"][data-move-row="${move.row}"][data-move-col="${move.col}"]`
    : `.cell[data-move-row="${move.row}"][data-move-col="${move.col}"]`;
  const target = board.querySelector(selector);
  if (!target) return;

  const markerKey = [
    room.room_id,
    move.revision,
    move.orientation || "cell",
    move.row,
    move.col,
  ].join(":");
  const isNewMove = markerKey !== lastMoveMarkerKey;
  lastMoveMarkerKey = markerKey;
  target.classList.add("last-move-target");
  if (isNewMove) target.classList.add("last-move-fresh");
  const marker = document.createElement("span");
  marker.className = "last-move-marker";
  marker.setAttribute("aria-hidden", "true");
  target.appendChild(marker);
  target.ariaLabel = `${target.ariaLabel || "棋盘位置"}，上一手`;
}

function renderBoard(timeline = currentTimeline) {
  const state = room.board_state;
  const board = $("board");
  board.replaceChildren();
  board.className = "board";
  if (room.game_type === "liars_dice") {
    board.classList.add("liars_dice");
    board.removeAttribute("style");
    board.setAttribute("aria-label", "吹牛骰子公共桌面与叫点操作");
    renderLiarsDice(board, state);
    updateMoveConfirmation();
    return;
  }
  const rows = state.rows || state.size;
  const cols = state.cols || state.size;
  const visualRows = room.game_type === "dots_boxes" ? 9 : rows;
  const visualCols = room.game_type === "dots_boxes" ? 9 : cols;
  board.style.setProperty("--cols", visualCols);
  board.style.setProperty("--rows", visualRows);
  board.style.setProperty("--board-ratio", `${visualCols} / ${visualRows}`);
  board.classList.toggle("large", Math.max(rows, cols) > 3);
  board.classList.add(room.game_type);
  board.setAttribute("aria-label", `${room.game_name}棋盘`);
  if (room.game_type === "dots_boxes") {
    renderDotsBoard(board, state);
  } else if (room.game_type === "jungle") {
    renderJungleBoard(board, state);
  } else if (room.game_type === "gomoku") {
    renderGomokuBoard(board, state);
  } else if (room.game_type === "connect4") {
    renderConnect4Board(board, state);
  } else {
    renderGridBoard(board, state);
  }
  renderLastMoveMarker(board, timeline);
  updateMoveConfirmation();
}

function timelineEventKind(eventType) {
  if (eventType === "move") return "move";
  if (eventType === "resign" || eventType === "result") return "result";
  return "chat";
}

function createChatTimelineItem(event, speaker, senderRole, moveComment = false) {
  const item = document.createElement("li");
  item.className = (
    `history-event history-chat-event ${senderRole} `
    + (moveComment ? "move-comment" : event.event_type)
  );
  const speakerLabel = document.createElement("strong");
  speakerLabel.className = "history-speaker";
  speakerLabel.textContent = speaker;
  const copy = document.createElement("p");
  copy.className = "history-copy";
  copy.textContent = event.text || "";
  const sequence = document.createElement("small");
  sequence.className = "history-meta";
  sequence.textContent = `#${event.sequence || event.id}${moveComment ? " · 附言" : ""}`;
  item.append(speakerLabel, copy, sequence);
  return item;
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
    const senderRole = event.sender_role
      || (typeof event.sender === "string" ? event.sender : event.sender.role);
    const speaker = (
      typeof event.sender === "object" && event.sender
        ? event.sender.name
        : event.sender_name
    ) || (senderRole === "human" ? "你" : aiNameFor());
    const eventKind = timelineEventKind(event.event_type);
    if (eventKind === "chat") {
      list.appendChild(createChatTimelineItem(event, speaker, senderRole));
      return;
    }

    const item = document.createElement("li");
    item.className = `history-event history-${eventKind}-event ${senderRole} ${event.event_type}`;
    const copy = document.createElement("p");
    copy.className = "history-copy";
    if (event.event_type === "resign") {
      copy.textContent = `${speaker} 认输${event.text ? `：${event.text}` : ""}`;
    } else if (event.event_type === "result") {
      copy.textContent = event.display_text || event.text || "对局结束";
    } else {
      copy.textContent = `${speaker}：${event.text}`;
    }
    const sequence = document.createElement("small");
    sequence.className = "history-meta";
    sequence.textContent = `#${event.sequence || event.id}`;
    const icon = document.createElement("span");
    icon.className = "history-icon";
    icon.setAttribute("aria-hidden", "true");
    icon.textContent = event.event_type === "move"
      ? "♟"
      : (event.event_type === "resign" ? "⚑" : "★");
    if (eventKind === "move") {
      const moveBody = document.createElement("div");
      moveBody.className = "history-move-body";
      const action = document.createElement("strong");
      action.className = "history-action-label";
      action.textContent = `${speaker} 落 ${event.move_label}`;
      moveBody.append(action);
      item.append(icon, moveBody, sequence);
    } else {
      item.append(icon, copy, sequence);
    }
    list.appendChild(item);
    if (eventKind === "move" && event.text) {
      list.appendChild(createChatTimelineItem(event, speaker, senderRole, true));
    }
  });
  list.scrollTop = list.scrollHeight;
}

function renderPlayers(timeline = []) {
  const multiplayer = Boolean(room && Array.isArray(room.participants) && room.participants.length > 2);
  applyParticipantLayout(room);
  $("opponentRow").classList.toggle("hidden", multiplayer);
  $("humanRow").classList.toggle("hidden", multiplayer);
  const aiName = participantName("ai");
  const humanName = participantName("human");
  $("aiName").textContent = aiName;
  $("humanName").textContent = humanName;
  $("aiAvatar").textContent = "🤖";
  $("humanAvatar").textContent = "👤";

  [["ai", "aiSpeech"], ["human", "humanSpeech"]].forEach(([role, targetId]) => {
    renderSpeechBubble({
      bubble: $(targetId),
      event: latestSpeechEvent(timeline, role),
    });
  });
  renderSpeechBubble({
    bubble: $("sharedSpeech"),
    event: latestSpeechEvent(timeline),
    textTarget: $("sharedSpeechText"),
    nameTarget: $("sharedSpeechName"),
    avatarTarget: $("sharedSpeechAvatar"),
    reserveSpace: true,
  });
}

function participantLayoutClass(playerCount) {
  if (playerCount === 3) return "layout-triangle";
  if (playerCount === 4) return "layout-corners";
  if (playerCount >= 5) return "layout-top-row";
  return "layout-duel";
}

function applyParticipantLayout(targetRoom) {
  const playerCount = targetRoom && Array.isArray(targetRoom.participants)
    ? targetRoom.participants.length
    : 2;
  const layoutClass = participantLayoutClass(playerCount);
  const table = $("tableLayout");
  table.className = `table-layout ${layoutClass} count-${playerCount}`;
  table.dataset.playerCount = String(playerCount);
  $("battleStage").dataset.playerCount = String(playerCount);
  $("sharedSpeechSlot").classList.toggle("hidden", playerCount <= 2);
}

function speechSenderRole(event) {
  if (!event) return "";
  return event.sender_role
    || (typeof event.sender === "object" && event.sender ? event.sender.role : event.sender)
    || "";
}

function latestSpeechEvent(timeline = [], role = null) {
  return [...timeline].reverse().find((event) => (
    ["message", "move", "resign", "leave"].includes(event.event_type)
    && Boolean(event.text)
    && (!role || speechSenderRole(event) === role)
  )) || null;
}

function renderSpeechBubble({
  bubble,
  event,
  textTarget = bubble,
  nameTarget = null,
  avatarTarget = null,
  reserveSpace = false,
}) {
  const visible = Boolean(event && event.text);
  [...bubble.classList]
    .filter((name) => /^seat-\d+$/.test(name))
    .forEach((name) => bubble.classList.remove(name));
  bubble.classList.toggle("hidden", !reserveSpace && !visible);
  bubble.classList.toggle("empty", reserveSpace && !visible);
  bubble.setAttribute("aria-hidden", visible ? "false" : "true");
  textTarget.textContent = visible ? event.text : "";
  if (!nameTarget && !avatarTarget) return;

  const sender = visible && typeof event.sender === "object" && event.sender
    ? event.sender
    : {};
  const participant = visible ? participantByPlayerId(sender.player_id) : null;
  const speakerName = visible
    ? (sender.name || event.sender_name || (participant && participant.display_name) || "玩家")
    : "";
  if (nameTarget) nameTarget.textContent = speakerName;
  if (avatarTarget) {
    renderParticipantAvatar(avatarTarget, participant || {
      display_name: speakerName,
      seat_index: sender.seat,
    });
  }
  const seatIndex = participant ? participant.seat_index : sender.seat;
  if (Number.isInteger(seatIndex)) bubble.classList.add(`seat-${seatIndex}`);
}

function renderParticipantRoster(targetRoom) {
  const roster = $("roomParticipants");
  const participants = Array.isArray(targetRoom.participants)
    ? targetRoom.participants
    : [];
  roster.replaceChildren();
  [...roster.classList]
    .filter((name) => name.startsWith("count-"))
    .forEach((name) => roster.classList.remove(name));
  roster.classList.add(`count-${participants.length}`);
  roster.classList.toggle("hidden", participants.length <= 2);
  if (participants.length <= 2) return;
  participants.forEach((participant) => {
    const badge = document.createElement("article");
    badge.className = `room-participant seat-${participant.seat_index}`;
    if (participant.player_id === targetRoom.current_player_id) {
      badge.classList.add("current");
    }
    if (!participant.active || participant.activity_state !== "active") {
      badge.classList.add("inactive");
    }
    const kindLabels = {human: "人类", bound_machine: "小机", system_npc: "NPC"};
    const kind = kindLabels[participant.participant_kind] || (participant.role === "human" ? "人类" : "小机");
    const states = [];
    if (participant.join_status === "left") states.push("已离开");
    else if (participant.join_status === "invited") states.push("待加入");
    if (participant.confirmation_status === "pending") states.push("待确认");
    if (participant.activity_state === "eliminated") states.push("已淘汰");
    else if (participant.activity_state === "inactive") states.push("暂停行动");
    else if (participant.activity_state === "skipped") states.push("本轮跳过");
    badge.setAttribute("aria-current", participant.player_id === targetRoom.current_player_id ? "true" : "false");
    const avatarWrap = document.createElement("span");
    avatarWrap.className = "room-participant-avatar";
    renderParticipantAvatar(avatarWrap, participant);
    const copy = document.createElement("span");
    copy.className = "room-participant-copy";
    const name = document.createElement("strong");
    name.textContent = participant.display_name;
    const seat = document.createElement("small");
    seat.textContent = `座位 ${participant.seat_index + 1} · ${kind}`;
    copy.append(name, seat);
    const detail = document.createElement("span");
    detail.className = "room-participant-detail";
    const metadataLabels = {score: "得分", dice_count: "剩余骰子"};
    const metadata = participant.game_metadata && typeof participant.game_metadata === "object"
      ? participant.game_metadata
      : {};
    const fragments = Object.entries(metadata).map(
      ([key, value]) => `${metadataLabels[key] || key} ${value}`
    );
    if (participant.player_id === targetRoom.current_player_id) fragments.unshift("▶ 正在行动");
    if (states.length) fragments.push(states.join("/"));
    detail.textContent = fragments.join(" · ");
    detail.classList.toggle("hidden", !fragments.length);
    badge.append(avatarWrap, copy, detail);
    roster.appendChild(badge);
  });
}

function renderPrivateState(targetRoom) {
  const panel = $("privateStatePanel");
  const content = $("privateStateContent");
  const privateState = targetRoom && targetRoom.private_state;
  const visible = privateState && typeof privateState === "object"
    && !Array.isArray(privateState) && Object.keys(privateState).length > 0;
  panel.classList.toggle("hidden", !visible);
  content.replaceChildren();
  if (!visible) return;
  Object.entries(privateState).forEach(([key, value]) => {
    const row = document.createElement("div");
    row.className = "private-state-row";
    const label = document.createElement("strong");
    const labels = {hand: "我的手牌", dice: "我的骰子", rolls: "我的投掷", legal_actions: "我的合法行动"};
    label.textContent = labels[key] || key;
    const body = document.createElement("span");
    if (key === "dice" && Array.isArray(value)) {
      body.className = "my-dice";
      value.forEach((die) => {
        const item = document.createElement("i");
        item.textContent = String(die);
        item.setAttribute("aria-label", `${die} 点`);
        body.appendChild(item);
      });
      if (!value.length) body.textContent = "已淘汰";
    } else {
      body.textContent = typeof value === "string" ? value : JSON.stringify(value);
    }
    row.append(label, body);
    content.appendChild(row);
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
  if (targetRoom.winner_player_id) {
    const winner = Array.isArray(targetRoom.participants)
      ? targetRoom.participants.find((item) => item.player_id === targetRoom.winner_player_id)
      : null;
    return `${(winner && winner.display_name) || targetRoom.winner_player_id} 获胜`;
  }
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

function renderRetention(targetRoom) {
  const terminal = isTerminal(targetRoom);
  const status = retentionTextFor(targetRoom);
  $("roomRetentionStatus").textContent = status;
  $("roomRetentionStatus").title = retentionDeadlineTitle(targetRoom);
  $("togglePreserveButton").textContent = targetRoom.preserved
    ? "取消保留"
    : "保留此对局";
  $("togglePreserveButton").disabled = !terminal;
  syncResultPreservationChoice(targetRoom.preserved, !terminal);
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
  currentTimeline = timeline;
  showView("gameView");
  $("gameBadge").textContent = room.game_type.toUpperCase();
  $("gameTitle").textContent = room.game_name;
  $("roomId").textContent = room.room_id;
  const resultText = resultTextFor(room, timeline);
  const winner = room.winner === "draw"
    ? " · 和棋"
    : (room.winner_player_id
      ? ` · ${(participantByPlayerId(room.winner_player_id) || {}).display_name || room.winner_player_id} 胜`
      : (room.winner ? ` · ${room.winner === "human" ? "你" : aiNameFor()} 胜` : ""));
  $("status").textContent = `${statusLabel(room.status)}${winner}`;
  $("turn").textContent = roomTurnText(room);
  $("roomStake").textContent = room.stake_label || (room.stake > 0 ? `🪙${room.stake}/人` : "娱乐局");
  $("revision").textContent = room.revision;
  $("rulesTitle").textContent = `${room.game_name}规则`;
  $("rulesText").textContent = room.rules_text;
  $("resignButton").disabled = room.status !== "playing";
  $("sendMessageButton").disabled = !["waiting", "playing"].includes(room.status);
  $("resultBanner").classList.toggle("hidden", !isTerminal(room));
  $("resultBannerText").textContent = resultText;
  renderRetention(room);
  showWaitModeModalOnce(room);
  showNotice(
    isTerminal(room)
      ? roomTurnText(room)
      : (message || (canHumanMove() ? "轮到你落子。" : ""))
  );
  renderPlayers(timeline);
  renderParticipantRoster(room);
  renderPrivateState(room);
  renderBoard(timeline);
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
      body: JSON.stringify({move: movePayload, revision: room.revision}),
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
  renderRetention(room);
  $("resultModal").classList.remove("hidden");
  $("rematchButton").disabled = false;
  $("resultPreserveCheckbox").focus();
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
    const rematchAis = Array.isArray(previousRoom.participants)
      ? previousRoom.participants
          .filter((item) => item.participant_kind !== "system_npc" && item.role === "ai")
          .map((item) => item.player_id)
      : [previousRoom.ai_player_id].filter(Boolean);
    const data = await request("/api/rooms", {
      method: "POST",
      body: JSON.stringify({
        ai_player: rematchAis[0],
        ai_players: rematchAis,
        target_player_count: Array.isArray(previousRoom.participants)
          ? previousRoom.participants.length
          : 2,
        fill_with_npcs: Array.isArray(previousRoom.participants)
          && previousRoom.participants.some((item) => item.participant_kind === "system_npc"),
        game_type: previousRoom.game_type,
        mode: oppositeMode(previousRoom.mode),
        stake: previousRoom.stake || 0,
      }),
    });
    closeResultModal();
    $("stake").value = String(previousRoom.stake || 0);
    if (data.room.status === "pending") {
      room = null;
      stopPolling();
      showView("lobbyView");
      await loadIdentity({quiet: true});
      showNotice(data.message);
      return;
    }
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
$("aiPlayer").addEventListener("change", machineSelectionChanged);
$("targetPlayerCount").addEventListener("change", () => {
  const target = selectedTargetPlayerCount();
  const selected = selectedParticipantIds();
  if (selected.length > target - 1) {
    [...$("aiPlayer").options]
      .filter((option) => option.selected && option.value)
      .slice(target - 1)
      .forEach((option) => { option.selected = false; });
  }
  $("aiPlayer").size = Math.max(2, Math.min(target - 1, 5));
  machineSelectionChanged();
});
$("fillWithNpcs").addEventListener("change", renderSelectedParticipants);
$("gameType").addEventListener("change", configureParticipantPicker);
$("mode").addEventListener("change", updateCreateButtonState);
$("stake").addEventListener("input", updateCreateButtonState);
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
$("dismissWaitModeModalButton").addEventListener("click", hideWaitModeModal);
$("closeWaitModalTodayButton").addEventListener("click", () => {
  closeWaitModeModal(false);
});
$("closeWaitModalForeverButton").addEventListener("click", () => {
  closeWaitModeModal(true);
});
$("waitModeModal").addEventListener("click", (event) => {
  if (event.target === $("waitModeModal")) hideWaitModeModal();
});
$("historyDrawer").addEventListener("click", (event) => {
  if (event.target === $("historyDrawer")) closeHistory();
});
$("togglePreserveButton").addEventListener("click", () => {
  if (room && isTerminal(room)) {
    updateRoomPreservation(room.room_id, !room.preserved);
  }
});
$("resultPreserveCheckbox").addEventListener("change", changeResultPreservation);
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
    hideWaitModeModal();
    closeRules();
    closeHistory();
  }
});

$("chipCenterLink").href = apiPath("/chips");
loadIdentity();

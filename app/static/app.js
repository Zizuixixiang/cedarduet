const $ = (id) => document.getElementById(id);
let identity = null;
let room = null;
let pollTimer = null;
let roomSyncGeneration = 0;
let roomRequestController = null;
let roomOpenIntent = null;
let roomRefreshSuspended = false;
let roomLeaving = false;
let roomWriteRequests = 0;
let readRetryAt = 0;
let rateLimitFailures = 0;
let roomRetryAt = 0;
let roomRefreshFailures = 0;
let unreadRefreshInFlight = false;
let roomListRequest = 0;
let roomListScope = "mine";
const spectatorPerspectives = new Map();
const retentionRequests = new Set();
const deletionRequests = new Set();
const spectatorRetentionMedia = window.matchMedia?.("(hover: hover) and (pointer: fine)");
let toastTimer = null;
let visibleWaitModalRoomId = null;
const waitHintShownRooms = new Set();
let selectedJungleCell = null;
let selectedXiangqiCell = null;
let pendingMove = null;
let liarsBidDraft = null;
let currentTimeline = [];
let lastMoveMarkerKey = null;
const selectedMachineIds = new Set();
const selectedMachineWallets = new Map();
let machineWalletRequest = 0;
let registeredGameUIStateKey = null;
let registeredGameUIState = Object.create(null);

let latestUnreadRevision = -1;
let latestUnreadSummary = null;
const pendingUnreadAcks = new Map();
const deferredUnreadAcks = new Set();
let notificationHistory = [];
let notificationRefreshRequest = 0;
let notificationHistoryError = "";
let notificationModalOpenedManually = false;
const pendingNotificationIdAcks = new Map();
const automaticNotificationIds = new Set();

const WAIT_HINT_STORAGE_PREFIX = "duel:wait-mode-hint";
const WAIT_HINT_FOREVER = "forever";
const UNREAD_SYNC_STORAGE_KEY = "duel:unread-sync";
const ROOM_QUERY_PARAM = "room";
const NOTIFICATION_CATEGORY_LABELS = Object.freeze({
  game: "对局",
  loan: "借款",
  exchange: "兑换",
  achievement: "成就",
});
const LEGACY_GAME_UI_TYPES = new Set([
  "tictactoe", "gomoku", "othello", "connect4",
  "dots_boxes", "liars_dice", "jungle", "xiangqi",
]);
const PARTICIPANT_PRESENTATIONS = new Set([
  "generic", "embedded", "board-edge",
]);
const ROOM_POLL_RETRY_MS = 3000;

const GAME_GLYPHS = {
  carcassonne: "城",
  tictactoe: "井",
  gomoku: "五",
  go: "围",
  othello: "黑",
  connect4: "四",
  checkers: "跳",
  dots_boxes: "点",
  doudizhu: "斗",
  liars_dice: "骰",
  yahtzee: "艇",
  jungle: "兽",
  xiangqi: "象",
  banqi: "暗",
  chess: "♞",
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
const XIANGQI_SYMBOLS = {
  r: {r: "车", n: "马", b: "相", a: "仕", k: "帅", c: "炮", p: "兵"},
  b: {r: "车", n: "马", b: "象", a: "士", k: "将", c: "炮", p: "卒"},
};
// These keys are display coordinates. Keeping them visual makes the palace
// geometry stay correct when a black-side viewer receives the rotated order.
const XIANGQI_PALACE_LINES = new Map([
  ["0,3", ["down-right"]], ["0,5", ["down-left"]],
  ["1,4", ["down-left", "down-right"]],
  ["7,3", ["down-right"]], ["7,5", ["down-left"]],
  ["8,4", ["down-left", "down-right"]],
]);

function apiPath(path) {
  return window.location.pathname.startsWith("/duel") ? `/duel${path}` : path;
}

function roomIdFromLocation() {
  const value = new URL(window.location.href).searchParams.get(ROOM_QUERY_PARAM);
  return value ? value.trim() : "";
}

function writeRoomRoute(roomId, {mode = "push", backToLobby = false} = {}) {
  const url = new URL(window.location.href);
  const nextRoomId = String(roomId || "").trim();
  if (nextRoomId) url.searchParams.set(ROOM_QUERY_PARAM, nextRoomId);
  else url.searchParams.delete(ROOM_QUERY_PARAM);
  const nextState = {
    ...(window.history.state || {}),
    duelView: nextRoomId ? "room" : "lobby",
    duelRoomId: nextRoomId || null,
    duelBackToLobby: Boolean(nextRoomId && backToLobby),
  };
  const method = mode === "replace" ? "replaceState" : "pushState";
  window.history[method](nextState, "", url);
}

function ensureInitialRouteState() {
  if (window.history.state && window.history.state.duelView) return;
  const routeRoomId = roomIdFromLocation();
  writeRoomRoute(routeRoomId, {mode: "replace", backToLobby: false});
}

function retryAfterDelay(value) {
  if (!value) return 0;
  const seconds = Number(value);
  if (Number.isFinite(seconds) && seconds >= 0) return seconds * 1000;
  const date = Date.parse(value);
  return Number.isFinite(date) ? Math.max(0, date - Date.now()) : 0;
}

function roomRefreshDelay() {
  return Math.max(0, readRetryAt - Date.now(), roomRetryAt - Date.now());
}

function deferRoomRetry(error) {
  roomRefreshFailures = Math.min(roomRefreshFailures + 1, 6);
  const delay = Math.max(error.retryAfterMs || 0,
    Math.min(60000, ROOM_POLL_RETRY_MS * 2 ** (roomRefreshFailures - 1)));
  roomRetryAt = Math.max(roomRetryAt, Date.now() + delay);
  return roomRefreshDelay();
}

async function request(path, options = {}) {
  // Automatic read entry points share the same 429 deadline. Mutations are never retried here.
  const refreshRead = (options.method || "GET").toUpperCase() === "GET"
    && /^\/api\/(?:whoami|spectating\/rooms|notifications\/unread|rooms\/[^/?]+)(?:\?|$)/.test(path);
  if (refreshRead && Date.now() < readRetryAt) {
    const error = new Error("请求较频繁，请稍后再试");
    error.status = 429;
    error.retryAfterMs = readRetryAt - Date.now();
    throw error;
  }
  const roomWrite = (options.method || "GET").toUpperCase() !== "GET"
    && /^\/api\/rooms\/[^/?]+\//.test(path);
  if (roomWrite) roomWriteRequests += 1;
  try {
    const response = await fetch(apiPath(path), {
      headers: {"Content-Type": "application/json"},
      cache: "no-store",
      ...options,
    });
    // Gateways may return an HTML/plain-text 429; keep its status and Retry-After.
    const data = await response.json().catch((error) => {
      if (response.ok) throw error;
      return {};
    });
    if (!response.ok) {
      const error = new Error(data?.message || data?.error || "请求失败");
      error.status = response.status;
      if (response.status === 429) {
        rateLimitFailures = Math.min(rateLimitFailures + 1, 6);
        error.retryAfterMs = Math.max(retryAfterDelay(response.headers?.get("Retry-After")),
          Math.min(60000, ROOM_POLL_RETRY_MS * 2 ** (rateLimitFailures - 1)));
        readRetryAt = Math.max(readRetryAt, Date.now() + error.retryAfterMs);
      }
      throw error;
    }
    applyHumanUnreadState(data);
    return data;
  } finally {
    if (roomWrite) {
      roomWriteRequests -= 1;
      startRoomPolling();
    }
  }
}

async function loadCatalogGameRenderers(games) {
  const registry = window.DuelGameUI;
  if (
    !registry
    || typeof registry.get !== "function"
    || typeof registry.load !== "function"
  ) return;
  const gameTypes = [...new Set(
    (Array.isArray(games) ? games : [])
      .map((game) => game?.game_type?.startsWith("four_color_") ? "four_color_tenhu" : game && game.game_type)
      .filter((gameType) => (
        typeof gameType === "string"
        && !LEGACY_GAME_UI_TYPES.has(gameType)
        && !registry.get(gameType)
      ))
  )];
  await Promise.allSettled(gameTypes.map(
    (gameType) => Promise.resolve().then(() => registry.load(gameType, gameType === "aeroplane_chess" ? "/static/games/aeroplane_chess.js?v=0.9.41" : gameType === "gem_merchant" ? "/static/games/gem_merchant.js?v=3" : (gameType === "four_color_tenhu" ? "/static/games/four_color_tenhu.js?v=0.9.30" : (["mahjong", "uno"].includes(gameType) ? `/static/games/${gameType}.js?v=0.9.29` : null))))
  ));
}

function applyHumanUnreadState(payload) {
  if (!payload?.unread?.categories) return false;
  const revision = Number(payload.unread_revision);
  const hasRevision = Number.isSafeInteger(revision) && revision >= 0;
  if (!hasRevision && latestUnreadRevision >= 0) return false;
  if (hasRevision && revision < latestUnreadRevision) return false;
  if (hasRevision) latestUnreadRevision = revision;
  latestUnreadSummary = payload.unread;
  if (identity) {
    identity.unread = latestUnreadSummary;
    if (hasRevision) identity.unread_revision = revision;
    renderUnreadBadges(identity.unread);
  }
  return true;
}

function syncUnreadStateToIdentity() {
  if (!identity || !latestUnreadSummary) return;
  identity.unread = latestUnreadSummary;
  if (latestUnreadRevision >= 0) identity.unread_revision = latestUnreadRevision;
}

function publishUnreadChange() {
  try {
    window.localStorage.setItem(
      UNREAD_SYNC_STORAGE_KEY,
      `${latestUnreadRevision}:${Date.now()}:${Math.random()}`,
    );
  } catch (_error) {
    // Storage can be unavailable in hardened browsers; the server remains canonical.
  }
}

function notificationCategoryLabel(category) {
  return NOTIFICATION_CATEGORY_LABELS[category] || "通知";
}

function formatNotificationTime(value) {
  const parsed = new Date(value);
  if (!value || Number.isNaN(parsed.getTime())) return String(value || "");
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(parsed);
}

function renderNotificationItems(items) {
  const list = $("notificationList");
  list.replaceChildren();
  if (!items.length) {
    const empty = document.createElement("p");
    empty.className = "notification-empty";
    empty.textContent = notificationHistoryError || "暂无通知";
    list.appendChild(empty);
    return;
  }
  items.forEach((item) => {
    const article = document.createElement("article");
    article.className = `notification-item${item.unread ? " unread" : ""}`;
    article.dataset.notificationId = String(item.id);

    const title = document.createElement("h3");
    title.textContent = notificationCategoryLabel(item.category);
    const meta = document.createElement("time");
    meta.className = "notification-meta";
    meta.dateTime = String(item.created_at || "");
    meta.textContent = formatNotificationTime(item.created_at);
    const summary = document.createElement("p");
    summary.className = "notification-summary";
    summary.textContent = String(item.summary || "");
    article.append(title, meta, summary);
    list.appendChild(article);
  });
}

function showNotificationModal(items, automatic = false) {
  notificationModalOpenedManually = !automatic;
  $("notificationModalTitle").textContent = automatic ? "新对局通知" : "对局通知";
  $("notificationModalHint").textContent = automatic
    ? "以下通知仅自动展示一次，可随时点铃铛重新查看。"
    : "打开列表后，当时未读的通知会标记为已读。";
  renderNotificationItems(items);
  $("notificationModal").classList.remove("hidden");
  $("notificationModal").setAttribute("aria-hidden", "false");
  $("closeNotificationModalButton").focus();
}

function closeNotificationModal() {
  const wasOpen = !$("notificationModal").classList.contains("hidden");
  $("notificationModal").classList.add("hidden");
  $("notificationModal").setAttribute("aria-hidden", "true");
  if (wasOpen && notificationModalOpenedManually) $("notificationBell").focus();
  notificationModalOpenedManually = false;
}

async function markHumanNotificationIdsRead(notificationIds) {
  const uniqueIds = [...new Set(notificationIds)]
    .filter((id) => Number.isSafeInteger(id) && id > 0)
    .sort((left, right) => left - right);
  if (!identity || !uniqueIds.length) return false;
  const ackKey = uniqueIds.join(",");
  if (pendingNotificationIdAcks.has(ackKey)) {
    return pendingNotificationIdAcks.get(ackKey);
  }
  const pending = (async () => {
    try {
      await request("/api/notifications/read", {
        method: "POST",
        body: JSON.stringify({notification_ids: uniqueIds}),
      });
      const readIds = new Set(uniqueIds);
      notificationHistory.forEach((item) => {
        if (readIds.has(item.id)) item.unread = false;
      });
      publishUnreadChange();
      return true;
    } catch (error) {
      toast(error.message || "通知已读状态保存失败");
      return false;
    } finally {
      pendingNotificationIdAcks.delete(ackKey);
    }
  })();
  pendingNotificationIdAcks.set(ackKey, pending);
  return pending;
}

async function refreshHumanUnreadState({autoPopup = true} = {}) {
  if (document.hidden || !identity || unreadRefreshInFlight || Date.now() < readRetryAt) return false;
  unreadRefreshInFlight = true;
  const requestId = ++notificationRefreshRequest;
  try {
    const data = await request("/api/notifications/unread");
    const revision = Number(data.unread_revision);
    if (
      requestId !== notificationRefreshRequest
      || (Number.isSafeInteger(revision) && revision < latestUnreadRevision)
    ) return false;
    notificationHistoryError = "";
    notificationHistory = Array.isArray(data.notifications)
      ? data.notifications.filter((item) => item && item.category === "game")
      : [];
    const unread = notificationHistory.filter((item) => (
      item.unread
      && Number.isSafeInteger(item.id)
      && !automaticNotificationIds.has(item.id)
    ));
    if (autoPopup && unread.length) {
      const ids = unread.map((item) => item.id);
      ids.forEach((id) => automaticNotificationIds.add(id));
      showNotificationModal(unread, true);
      try {
        await markHumanNotificationIdsRead(ids);
      } finally {
        ids.forEach((id) => automaticNotificationIds.delete(id));
      }
    }
    return true;
  } catch (_error) {
    if (requestId === notificationRefreshRequest) {
      notificationHistoryError = "通知加载失败，请稍后再试";
    }
    // A later focus, visibility change, or explicit visit retries the refresh.
    return false;
  } finally {
    unreadRefreshInFlight = false;
  }
}

async function openNotificationList() {
  if (!identity) return;
  const refreshed = await refreshHumanUnreadState({autoPopup: false});
  if (!refreshed && !notificationHistory.length) {
    toast(notificationHistoryError || "通知加载失败，请稍后再试");
    return;
  }
  showNotificationModal(notificationHistory, false);
  const unreadIds = notificationHistory
    .filter((item) => item.unread)
    .map((item) => item.id);
  await markHumanNotificationIdsRead(unreadIds);
}

function unreadCount(summary, category) {
  return Number(summary?.categories?.[category] || 0);
}

function setUnreadBadge(elementId, count, label) {
  const badge = $(elementId);
  if (!badge) return;
  badge.textContent = count > 99 ? "99+" : String(count);
  badge.classList.toggle("hidden", count <= 0);
  badge.setAttribute("aria-label", `${label}（未读${count}）`);
}

function renderUnreadBadges(unread) {
  const game = unreadCount(unread, "game");
  const chipCenter = unreadCount(unread, "loan")
    + unreadCount(unread, "exchange")
    + unreadCount(unread, "achievement");
  setUnreadBadge("gameUnreadBadge", 0, "对局");
  setUnreadBadge("chipCenterUnreadBadge", chipCenter, "筹码中心");
  setUnreadBadge("notificationUnreadBadge", game, "对局通知");
  const bell = $("notificationBell");
  if (bell) {
    bell.disabled = !identity;
    bell.setAttribute("aria-label", `对局通知（未读${game}）`);
  }
}

async function ackHumanNotifications(category, referenceId = null) {
  const ackKey = `${category}:${referenceId || "*"}`;
  if (!identity) return;
  if (document.hidden) {
    deferredUnreadAcks.add(ackKey);
    return;
  }
  if (pendingUnreadAcks.has(ackKey)) return pendingUnreadAcks.get(ackKey);
  const pending = (async () => {
    try {
      await request("/api/notifications/read", {
        method: "POST",
        body: JSON.stringify({
          category,
          ...(referenceId ? {reference_id: referenceId} : {}),
        }),
      });
      deferredUnreadAcks.delete(ackKey);
      publishUnreadChange();
    } catch (_error) {
      // A transient ack failure must not block the room UI.
      deferredUnreadAcks.add(ackKey);
    } finally {
      pendingUnreadAcks.delete(ackKey);
    }
  })();
  pendingUnreadAcks.set(ackKey, pending);
  return pending;
}

function showView(id) {
  ["loadingView", "unboundView", "lobbyView", "gameView"].forEach((viewId) => {
    $(viewId).classList.toggle("hidden", viewId !== id);
  });
}

function showNotice(text, error = false, emphasize = false) {
  const target = isInviteWaiting(room) ? $("inviteRoomNotice") : room ? $("gameMessage") : $("notice");
  target.textContent = text || "";
  target.classList.toggle("error", error);
  target.classList.toggle("my-turn", Boolean(emphasize) && !error);
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
  if (targetRoom?.viewer?.player_id) return targetRoom.viewer.player_id;
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
    archived: "已结束",
  }[status] || status;
}

function isTerminal(targetRoom) {
  return Boolean(
    targetRoom && ["finished", "archived"].includes(targetRoom.status)
  );
}

function actualPlayerCount(targetRoom) {
  return targetRoom && Array.isArray(targetRoom.participants)
    ? targetRoom.participants.length
    : 2;
}

function isMultiplayerRoom(targetRoom) {
  return actualPlayerCount(targetRoom) > 2;
}

function participantPresentationFor(targetRoom) {
  // Renderers may ask for the multiplayer table even at two seats.
  const tableRenderer = registeredGameUIRenderer(targetRoom.game_type);
  if (!isMultiplayerRoom(targetRoom) && tableRenderer && tableRenderer.multiplayerTable === true) {
    return PARTICIPANT_PRESENTATIONS.has(tableRenderer.participantPresentation)
      ? tableRenderer.participantPresentation : "generic";
  }
  if (!isMultiplayerRoom(targetRoom) && !["monopoly", "carcassonne"].includes(targetRoom.game_type)) return "duel";
  const renderer = registeredGameUIRenderer(targetRoom.game_type);
  const presentation = renderer && renderer.participantPresentation;
  return PARTICIPANT_PRESENTATIONS.has(presentation)
    ? presentation
    : "generic";
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
  if (room.viewer?.player_id) {
    if (role === "human") return room.participants.find((item) => item.player_id === room.viewer.player_id) || null;
    if (role === "ai") return room.participants.find((item) => item.player_id !== room.viewer.player_id) || null;
  }
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

function accountAvatarForParticipant(participant) {
  const accountIdentity = typeof identity === "object" && identity ? identity : null;
  if (!participant || !accountIdentity || participant.participant_kind === "system_npc") {
    return null;
  }
  if (participant.participant_kind === "human" || participant.role === "human") {
    return participant.player_id === (accountIdentity.human_player_id || viewerPlayerIdFor(room)) ? accountIdentity.human_avatar || null : null;
  }
  const machines = Array.isArray(accountIdentity.machines)
    ? accountIdentity.machines
    : [];
  const playerId = String(participant.player_id || "");
  const basePlayerId = playerId.split(":", 1)[0];
  const machine = machines.find((item) =>
    item.id === playerId || item.id === basePlayerId
  );
  return machine ? machine.avatar || null : null;
}

function renderParticipantAvatar(target, participant, targetRoom = null) {
  target.replaceChildren();
  const fallback = participantAvatarFallback(participant);
  target.textContent = fallback;
  const isCurrentTurn = Boolean(
    targetRoom
    && targetRoom.status === "playing"
    && participant
    && participant.player_id
    && participant.player_id === targetRoom.current_player_id
  );
  target.classList.toggle("current-turn-avatar", isCurrentTurn);
  const avatarLabel = participant && participant.display_name
    ? `${participant.display_name}的头像`
    : "玩家头像";
  target.setAttribute(
    "aria-label",
    avatarLabel + (isCurrentTurn ? "，当前行动者" : "")
  );
  if (!participant) return;
  if (participant.participant_kind !== "system_npc") {
    const avatar = accountAvatarForParticipant(participant) || participant.avatar;
    if (avatar && avatar.type === "emoji" && typeof avatar.value === "string" && avatar.value) {
      target.textContent = avatar.value;
    }
    return;
  }
  if (!participant.avatar_url) return;
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

function turnLabel(turn, aiPlayerId, currentActor = null, targetRoom = null) {
  const actor = currentActor || targetRoom?.participants?.find((p) => p.player_id === targetRoom.current_player_id);
  if (actor) {
    const humanId = targetRoom?.viewer?.player_id
      || identity?.human_player_id || targetRoom?.human_player_id
      || (actor.role === "human" ? actor.player_id : null);
    return !targetRoom?.spectating && actor.player_id === humanId
      ? "轮到你"
      : `轮到 ${actor.display_name || actor.player_id}`;
  }
  const aiName = targetRoom?.participants?.find((p) => p.player_id === aiPlayerId)?.display_name
    || identity?.machines?.find((machine) => machine.id === aiPlayerId)?.name
    || targetRoom?.ai_name || "小机";
  if (targetRoom?.spectating) {
    const name = turn === "human"
      ? targetRoom.participants?.find((p) => p.role === "human")?.display_name || targetRoom.human_name || "人类玩家"
      : aiName;
    return `轮到 ${name}`;
  }
  if (!identity) return turn;
  return turn === "human" ? "轮到你" : `轮到 ${aiName}`;
}

function roomTurnText(targetRoom) {
  if (isTerminal(targetRoom)) {
    return "对局已结束";
  }
  if (targetRoom.room_kind === "invite" && targetRoom.status === "waiting") return "等待开局";
  if (targetRoom.status === "pending") return "等待对方确认";
  if (
    targetRoom.game_type === "liars_dice"
    && targetRoom.board_state
    && targetRoom.board_state.flow
    && targetRoom.board_state.flow.phase === "awaiting_round_acknowledgement"
  ) return targetRoom.spectating ? "本轮已结算 · 等待玩家确认下一轮" : "本轮已结算 · 等待你确认下一轮";
  return turnLabel(
    targetRoom.turn, targetRoom.ai_player_id, targetRoom.current_actor, targetRoom
  );
}

function authoritativeRoundText(targetRoom) {
  if (!targetRoom || targetRoom.game_type !== "liars_dice") return "";
  const flow = targetRoom.board_state && targetRoom.board_state.flow;
  const roundNumber = flow && flow.round_number;
  return Number.isInteger(roundNumber) && roundNumber >= 1
    ? `第 ${roundNumber} 轮`
    : "";
}

async function copyRoomNumber(
  targetRoom = room,
  clipboard = navigator.clipboard
) {
  if (!targetRoom || !targetRoom.room_id) return false;
  try {
    if (!clipboard || typeof clipboard.writeText !== "function") {
      throw new Error("clipboard unavailable");
    }
    await clipboard.writeText(targetRoom.room_id);
    toast("房间号已复制");
    return true;
  } catch (_error) {
    toast(`房间号 ${targetRoom.room_id}，请长按复制`);
    return false;
  }
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
  if (!targetRoom.auto_delete_at) return "自动删除时间待确认";
  const deadline = new Date(targetRoom.auto_delete_at).getTime();
  if (!Number.isFinite(deadline)) return "自动删除时间待确认";
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

function pendingConfirmationDetails(targetRoom) {
  const source = targetRoom && typeof targetRoom === "object" ? targetRoom : {};
  const participants = Array.isArray(source.participants) ? source.participants : [];
  const namesById = new Map();
  participants.forEach((participant) => {
    if (!participant || typeof participant !== "object") return;
    const playerId = typeof participant.player_id === "string"
      ? participant.player_id.trim()
      : "";
    const displayName = typeof participant.display_name === "string"
      ? participant.display_name.trim()
      : "";
    if (playerId && displayName) namesById.set(playerId, displayName);
  });

  const pendingIds = [];
  const seenIds = new Set();
  const addPendingId = (value) => {
    const playerId = typeof value === "string" ? value.trim() : "";
    if (!playerId || seenIds.has(playerId)) return;
    seenIds.add(playerId);
    pendingIds.push(playerId);
  };

  const pendingFor = Array.isArray(source.pending_for) ? source.pending_for : [];
  pendingFor.forEach(addPendingId);
  const confirmations = Array.isArray(source.confirmations) ? source.confirmations : [];
  const pendingConfirmations = confirmations.filter(
    (confirmation) => confirmation && confirmation.decision === "pending"
  );
  pendingConfirmations.forEach((confirmation) => addPendingId(confirmation.player_id));
  const pendingParticipants = participants.filter(
    (participant) => participant && participant.confirmation_status === "pending"
  );
  pendingParticipants.forEach((participant) => addPendingId(participant.player_id));

  const count = Math.max(
    pendingIds.length,
    pendingFor.length,
    pendingConfirmations.length,
    pendingParticipants.length
  );
  return {
    count,
    names: pendingIds.map((playerId) => namesById.get(playerId)).filter(Boolean),
  };
}

function pendingConfirmationText(targetRoom) {
  const {count, names} = pendingConfirmationDetails(targetRoom);
  if (!count) return "等待参与者确认";
  if (!names.length) return `待 ${count} 人确认`;
  const unnamedCount = Math.max(0, count - names.length);
  const suffix = unnamedCount ? ` / 另 ${unnamedCount} 人` : "";
  return `待 ${count} 人确认：${names.join(" / ")}${suffix}`;
}

function pendingRoomCreatedNotice(targetRoom) {
  const roomId = targetRoom && typeof targetRoom.room_id === "string"
    ? targetRoom.room_id.trim()
    : "";
  const subject = roomId ? `房间 ${roomId} ` : "房间";
  return (
    `${subject}创建成功。${pendingConfirmationText(targetRoom)}。`
    + "全部待确认参与者明确接受后，对局才会开始。"
  );
}

// These spans remain ordinary inline text on touch; desktop styles group wrapping units.
function appendRoomTextUnits(target, parts, separator, separatorClass = "") {
  parts.forEach((part, index) => {
    if (index) {
      if (separatorClass) {
        const divider = document.createElement("span");
        divider.className = separatorClass;
        divider.textContent = separator;
        target.appendChild(divider);
      } else {
        target.append(document.createTextNode(separator));
      }
    }
    const unit = document.createElement("span");
    unit.className = "room-text-unit";
    unit.textContent = part;
    target.appendChild(unit);
  });
}

function renderRooms(rooms) {
  const list = $("roomList");
  list.replaceChildren();
  if (!rooms.length) {
    const empty = document.createElement("div");
    empty.className = "room-empty";
    empty.textContent = roomListScope === "machines"
      ? "暂无可围观的自家小机房间。"
      : "还没有对局。选一个棋种，和你的小机开第一盘吧。";
    list.appendChild(empty);
    return;
  }
  // Desktop visual groups only; cards retain the original DOM/data order on touch.
  for (const [history, label] of [[false, "当前对局"], [true, "历史对局"]]) {
    if (!rooms.some((summary) => isTerminal(summary) === history)) continue;
    const heading = document.createElement("h3");
    heading.className = `room-group-heading${history ? " history" : " current"}`;
    heading.textContent = label;
    heading.setAttribute("aria-hidden", "true");
    list.appendChild(heading);
  }
  rooms.forEach((summary) => {
    const terminal = isTerminal(summary);
    const activeInvite = summary.room_kind === "invite" && !terminal;
    const card = document.createElement("article");
    card.className = `room-card${terminal ? " ended" : ""}${activeInvite ? " invite-active" : ""}`;

    const open = document.createElement("button");
    open.className = "room-open";
    open.type = "button";
    open.setAttribute(
      "aria-label",
      `进入${terminal ? `${statusLabel(summary.status)}的` : ""}${summary.game_name}房间 ${summary.room_id}`
    );

    const glyph = document.createElement("span");
    glyph.className = "room-glyph";
    glyph.textContent = GAME_GLYPHS[summary.game_type] || "棋";

    const copy = document.createElement("span");
    copy.className = "room-copy";
    const title = document.createElement("span");
    title.className = "room-title";
    const titleNames = summary.participant_names && (summary.spectating || summary.participant_names.length > 2 || summary.room_kind === "invite")
      ? summary.participant_names : [String(summary.ai_name)];
    const gameName = document.createElement("strong");
    gameName.className = "room-game-name";
    gameName.textContent = String(summary.game_name);
    const divider = document.createElement("span");
    divider.className = "room-title-separator";
    divider.textContent = " × ";
    const participants = document.createElement("span");
    participants.className = "room-title-participants";
    appendRoomTextUnits(participants, titleNames, " / ", "room-title-separator");
    title.append(gameName, divider, participants);
    title.title = title.textContent;
    const meta = document.createElement("span");
    meta.className = "room-meta";
    const waitingLabel = summary.team_selection_pending ? "等待组队"
      : summary.room_kind === "invite" && summary.status === "waiting" ? "等待开局" : statusLabel(summary.status);
    const metaParts = summary.room_kind === "invite"
      ? [...(activeInvite ? [] : ["邀请房"]), `${summary.participant_count}/${summary.target_player_count}`, waitingLabel]
      : [String(summary.room_id), waitingLabel,
        ...(summary.team_selection_pending ? [`${summary.participant_count}/${summary.target_player_count}`] : []),
        `更新于 ${relativeTime(summary.updated_at)}`];
    appendRoomTextUnits(meta, metaParts, " · ");
    const tags = document.createElement("span");
    tags.className = "room-tags";
    if (activeInvite) {
      const inviteBadge = document.createElement("span");
      inviteBadge.className = "room-status-badge room-invite-badge";
      inviteBadge.textContent = "邀请房";
      tags.appendChild(inviteBadge);
    }
    const stake = document.createElement("span");
    stake.className = "room-stake";
    const stakeLabel = summary.stake_label
      || (summary.stake > 0 ? `🪙${summary.stake}/人` : "娱乐局");
    stake.textContent = summary.stake_hint
      ? `${stakeLabel}（${summary.stake_hint}）`
      : stakeLabel;
    tags.appendChild(stake);
    copy.append(title, meta, tags);

    const state = document.createElement("span");
    state.className = "room-state";
    if (terminal) {
      const statusBadge = document.createElement("span");
      statusBadge.className = "room-status-badge pale";
      statusBadge.textContent = (
        `${statusLabel(summary.status)}${summary.winner === "draw" && !summary.party_dice ? " · 和棋" : ""}`
      );
      state.appendChild(statusBadge);
    } else {
      const turn = document.createElement("span");
      const pending = summary.status === "pending";
      turn.className = `turn${pending ? " pending-confirmations" : ""}`;
      turn.textContent = summary.status === "playing"
        ? turnLabel(summary.turn, summary.ai_player_id, summary.current_actor, summary)
        : pending
        ? pendingConfirmationText(summary)
        : waitingLabel;
      state.appendChild(turn);
    }
    const enter = document.createElement("span");
    enter.className = "room-enter";
    enter.textContent = summary.spectating ? "围观 →" : "进入 →";
    state.appendChild(enter);

    open.append(glyph, copy, state);
    open.addEventListener("click", () => openRoom(summary.room_id, {
      perspective: summary.spectating ? (spectatorPerspectives.get(summary.room_id) || "") : "",
    }));
    card.appendChild(open);

    if (terminal) {
      const controls = document.createElement("div");
      controls.className = `room-record-controls${summary.spectating ? " spectator-retention-controls" : ""}`;
      if (summary.spectating) controls.setAttribute("aria-hidden", "true");

      const retention = document.createElement("span");
      retention.className = `room-retention${summary.preserved ? " preserved" : ""}`;
      retention.textContent = retentionTextFor(summary);
      retention.title = retentionDeadlineTitle(summary);

      const preserve = document.createElement("button");
      preserve.className = "room-record-button";
      preserve.type = "button";
      preserve.textContent = summary.preserved ? "取消保留" : "保留";
      preserve.dataset.retentionRoomId = summary.room_id;
      preserve.addEventListener("click", () => {
        updateRoomPreservation(summary.room_id, !summary.preserved, {
          spectating: Boolean(summary.spectating),
          perspective: spectatorPerspectives.get(summary.room_id) || "",
        });
      });

      controls.append(retention, preserve);
      const remove = document.createElement("button");
      remove.className = "room-record-button danger";
      remove.type = "button";
      remove.textContent = "删除对局";
      remove.dataset.deleteRoomId = summary.room_id;
      remove.addEventListener("click", (event) => {
        event.stopPropagation();
        deleteRoom(summary);
      });
      controls.appendChild(remove);
      card.appendChild(controls);
    }
    list.appendChild(card);
  });
  syncSpectatorRetentionControls();
}

function setRoomListScope(scope) {
  if (scope !== "mine" && scope !== "machines") return;
  if (roomListScope !== scope) ++roomListRequest;
  roomListScope = scope;
  for (const button of $("roomListScope").querySelectorAll("[data-room-scope]")) {
    const selected = button.dataset.roomScope === scope;
    button.setAttribute("aria-selected", String(selected));
    button.tabIndex = selected ? 0 : -1;
    if (selected) $("roomScopePanel").setAttribute("aria-labelledby", button.id);
  }
  $("roomScopeHint").setAttribute("aria-hidden", String(scope !== "machines"));
}

async function loadSpectatorRooms() {
  const generation = ++roomListRequest;
  $("roomList").textContent = "正在读取房间…";
  try {
    const data = await request("/api/spectating/rooms");
    if (generation !== roomListRequest || roomListScope !== "machines") return;
    renderRooms(data.rooms || []);
  } catch (error) {
    if (generation === roomListRequest && roomListScope === "machines") {
      $("roomList").textContent = error.message;
    }
  }
}

function isOutgoingPendingRoom(summary) {
  if (!summary || summary.status !== "pending") return false;
  const participants = Array.isArray(summary.participants) ? summary.participants : [];
  return participants.some((participant) => (
    participant
    && participant.role === "human"
    && participant.player_id === summary.initiator_player_id
  ));
}

function partitionLobbyPendingRooms(rooms = [], invitations = []) {
  const incomingRoomIds = new Set(
    invitations.map((item) => item && item.room_id).filter(Boolean)
  );
  const outgoing = rooms.filter(isOutgoingPendingRoom);
  const pendingRoomIds = new Set([
    ...incomingRoomIds,
    ...outgoing.map((item) => item.room_id),
  ]);
  return {
    outgoing,
    remainingRooms: rooms.filter((item) => !pendingRoomIds.has(item.room_id)),
  };
}

function renderPendingInvitations(invitations = [], outgoingRooms = []) {
  const panel = $("pendingPanel");
  const list = $("pendingList");
  list.replaceChildren();
  panel.classList.toggle(
    "hidden", invitations.length === 0 && outgoingRooms.length === 0
  );

  const appendGroup = (headingText, items, renderItem) => {
    if (!items.length) return;
    const group = document.createElement("section");
    group.className = "pending-group";
    const heading = document.createElement("h3");
    heading.className = "pending-group-title";
    heading.textContent = headingText;
    const cards = document.createElement("div");
    cards.className = "pending-group-list";
    items.forEach((item) => cards.appendChild(renderItem(item)));
    group.append(heading, cards);
    list.appendChild(group);
  };

  appendGroup("待你确认", invitations, (invitation) => {
    const card = document.createElement("article");
    card.className = "pending-card pending-card-incoming";
    const copy = document.createElement("div");
    copy.className = "pending-copy";
    const title = document.createElement("strong");
    title.className = "pending-title";
    title.textContent = `发起方：${invitation.initiator_name}`;
    title.title = title.textContent;
    const meta = document.createElement("span");
    meta.className = "pending-meta";
    const stakeText = invitation.stake_hint
      ? `${invitation.stake_label}（${invitation.stake_hint}）`
      : invitation.stake_label;
    meta.textContent = `棋种：${invitation.game_name} · ${stakeText}`;
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
    return card;
  });

  appendGroup("等待对方确认", outgoingRooms, (summary) => {
    const card = document.createElement("article");
    card.className = "pending-card pending-card-outgoing";
    const copy = document.createElement("div");
    copy.className = "pending-copy";
    const title = document.createElement("strong");
    title.className = "pending-title";
    title.textContent = `${summary.game_name} · 房间 ${summary.room_id} 已创建`;
    const meta = document.createElement("span");
    meta.className = "pending-meta";
    const stakeText = summary.stake_hint
      ? `${summary.stake_label}（${summary.stake_hint}）`
      : summary.stake_label;
    meta.textContent = `${stakeText} · ${pendingConfirmationText(summary)}`;
    copy.append(title, meta);
    const view = document.createElement("button");
    view.className = "pixel-btn secondary";
    view.type = "button";
    view.textContent = "查看房间";
    view.addEventListener("click", () => openRoom(summary.room_id));
    card.append(copy, view);
    return card;
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
      writeRoomRoute(data.room.room_id, {mode: "push", backToLobby: true});
      await ackHumanNotifications("game", roomId);
      startRoomPolling();
      return;
    }
    await ackHumanNotifications("game", roomId);
    await loadIdentity({quiet: true});
    toast(data.message);
  } catch (error) {
    toast(error.message);
    await loadIdentity({quiet: true});
  }
}

function syncSpectatorRetentionControls() {
  const desktop = Boolean(spectatorRetentionMedia?.matches);
  document.querySelectorAll(".room-record-controls").forEach((controls) => {
    const spectator = controls.classList.contains("spectator-retention-controls");
    const visible = !spectator || desktop;
    if (spectator) {
      controls.setAttribute("aria-hidden", String(!visible));
      controls.inert = !visible;
    }
    controls.querySelectorAll("button").forEach((button) => {
      const roomId = button.dataset.retentionRoomId || button.dataset.deleteRoomId;
      button.disabled = !visible || retentionRequests.has(roomId) || deletionRequests.has(roomId);
    });
  });
  if (room?.spectating) {
    const visible = desktop && isTerminal(room);
    $("togglePreserveButton").setAttribute("aria-hidden", String(!visible));
    $("togglePreserveButton").disabled = !visible || retentionRequests.has(room.room_id);
  }
}

spectatorRetentionMedia?.addEventListener("change", syncSpectatorRetentionControls);

async function updateRoomPreservation(roomId, preserved, {
  fromModal = false, spectating = false, perspective = "",
} = {}) {
  const previousRoom = room?.room_id === roomId ? room : null;
  const spectator = Boolean(previousRoom?.spectating || spectating);
  const selectedPerspective = previousRoom?.spectating
    ? viewerPlayerIdFor(previousRoom) : perspective;
  const previousScope = roomListScope;
  const generation = roomSyncGeneration;
  if ((spectator && !spectatorRetentionMedia?.matches) || retentionRequests.has(roomId) || deletionRequests.has(roomId)) return false;
  retentionRequests.add(roomId);
  syncSpectatorRetentionControls();
  try {
    const query = spectator && selectedPerspective
      ? `?perspective=${encodeURIComponent(selectedPerspective)}` : "";
    const data = await request(`/api/rooms/${roomId}/retention${query}`, {
      method: "POST",
      body: JSON.stringify({preserved}),
    });
    if (spectator) {
      // Never turn a spectator response into a human view or restore an old perspective.
      if (!data.room?.spectating || (selectedPerspective && viewerPlayerIdFor(data.room) !== selectedPerspective)) {
        throw new Error("留存响应视角不一致，请刷新后重试。");
      }
      if (previousRoom && room?.room_id === roomId && room.spectating
          && viewerPlayerIdFor(room) === selectedPerspective && generation === roomSyncGeneration) {
        renderGame(data.room, data.message, data.timeline);
      } else if (!previousRoom && !room && previousScope === "machines" && roomListScope === previousScope) {
        await loadSpectatorRooms();
      }
    } else if (room && room.room_id === roomId) {
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
  } finally {
    retentionRequests.delete(roomId);
    syncSpectatorRetentionControls();
  }
}

function syncResultPreservationChoice(targetRoom, disabled = false) {
  const preserved = Boolean(targetRoom && targetRoom.preserved);
  $("resultPreserveCheckbox").checked = preserved;
  $("resultPreserveCheckbox").disabled = disabled;
  $("resultRetentionHint").textContent = preserved
    ? "已保留，不会自动删除"
    : retentionTextFor(targetRoom);
}

async function changeResultPreservation() {
  const checkbox = $("resultPreserveCheckbox");
  if (!room || !isTerminal(room)) {
    syncResultPreservationChoice(room, true);
    return;
  }
  const previousRoom = room;
  const roomId = room.room_id;
  const requestedValue = checkbox.checked;
  syncResultPreservationChoice({...room, preserved: requestedValue}, true);
  $("resultModalMessage").textContent = "";
  const updated = await updateRoomPreservation(
    roomId,
    requestedValue,
    {fromModal: true}
  );
  const sameTerminalRoom = Boolean(
    room && room.room_id === roomId && isTerminal(room)
  );
  const authoritativeRoom = updated && sameTerminalRoom ? room : previousRoom;
  syncResultPreservationChoice(authoritativeRoom, !sameTerminalRoom);
  if (updated && sameTerminalRoom) {
    $("resultModalMessage").textContent = "";
  }
}

async function deleteRoom(summary) {
  const roomId = summary.room_id;
  if (!isTerminal(summary) || (summary.spectating && !spectatorRetentionMedia?.matches)
      || deletionRequests.has(roomId) || retentionRequests.has(roomId)) return;
  const confirmed = window.confirm(
    `确定删除房间 ${summary.room_id}？棋谱和聊天记录会一并永久删除，无法恢复。`
  );
  if (!confirmed) return;
  deletionRequests.add(roomId);
  syncSpectatorRetentionControls();
  try {
    const data = await request(`/api/rooms/${roomId}/delete`, {
      method: "POST",
      body: "{}",
    });
    if (!room) {
      if (roomListScope === "machines") await loadSpectatorRooms();
      else await loadIdentity({quiet: true});
    }
    toast(data.message);
  } catch (error) {
    toast(error.message);
  } finally {
    deletionRequests.delete(roomId);
    syncSpectatorRetentionControls();
  }
}

function selectedParticipantIds() {
  if ($("aiPlayer").closest(".participant-picker").dataset.selectionMode !== "multiple") {
    return [$("aiPlayer").value].filter(Boolean);
  }
  const machines = identity && Array.isArray(identity.machines)
    ? identity.machines
    : [];
  return machines
    .map((machine) => machine.id)
    .filter((playerId) => selectedMachineIds.has(playerId));
}

function machinePickerSummary(machines) {
  if (!machines.length) return "请选择对手";
  return `已选 ${machines.length} 位：${machines.map((machine) => machine.name).join("、")}`;
}

function closeMachineMultiPicker({restoreFocus = false} = {}) {
  const trigger = $("aiMultiTrigger");
  trigger.setAttribute("aria-expanded", "false");
  $("aiMultiMenu").classList.add("hidden");
  $("aiMultiField").classList.remove("open");
  if (restoreFocus) trigger.focus();
}

function focusAdjacentMachineOption(current, direction) {
  const options = [...$("aiMultiMenu").querySelectorAll('[role="option"]')];
  if (!options.length) return;
  const index = options.indexOf(current);
  const nextIndex = direction === "first"
    ? 0
    : (direction === "last"
      ? options.length - 1
      : (index + direction + options.length) % options.length);
  options[nextIndex].focus();
}

function toggleMachineSelection(playerId) {
  if (selectedMachineIds.has(playerId)) {
    selectedMachineIds.delete(playerId);
  } else {
    const maximum = selectedTargetPlayerCount() - 1;
    if (selectedMachineIds.size >= maximum) {
      showNotice(`当前桌型最多选择 ${maximum} 位小机。`, true);
      return;
    }
    selectedMachineIds.add(playerId);
  }
  renderMachineMultiPicker(playerId);
  machineSelectionChanged();
}

function renderMachineMultiPicker(focusPlayerId = null) {
  const menu = $("aiMultiMenu");
  const machines = identity && Array.isArray(identity.machines)
    ? identity.machines
    : [];
  const selected = machines.filter((machine) => selectedMachineIds.has(machine.id));
  const summary = machinePickerSummary(selected);
  $("aiMultiSummary").textContent = summary;
  $("aiMultiTrigger").title = summary;
  $("aiMultiTrigger").disabled = machines.length === 0;
  menu.replaceChildren();
  machines.forEach((machine) => {
    const option = document.createElement("button");
    option.type = "button";
    option.className = "ai-multi-option";
    option.dataset.playerId = machine.id;
    option.setAttribute("role", "option");
    option.setAttribute("aria-selected", String(selectedMachineIds.has(machine.id)));
    const check = document.createElement("span");
    check.className = "ai-multi-check";
    check.textContent = selectedMachineIds.has(machine.id) ? "✓" : "";
    check.setAttribute("aria-hidden", "true");
    const name = document.createElement("span");
    name.className = "ai-multi-name";
    name.textContent = machine.name;
    option.append(check, name);
    option.addEventListener("click", () => toggleMachineSelection(machine.id));
    option.addEventListener("keydown", (event) => {
      if (event.key === "Escape") {
        event.preventDefault();
        closeMachineMultiPicker({restoreFocus: true});
      } else if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        event.preventDefault();
        focusAdjacentMachineOption(option, event.key === "ArrowDown" ? 1 : -1);
      } else if (event.key === "Home" || event.key === "End") {
        event.preventDefault();
        focusAdjacentMachineOption(option, event.key === "Home" ? "first" : "last");
      }
    });
    menu.appendChild(option);
  });
  if (focusPlayerId && !menu.classList.contains("hidden")) {
    const target = [...menu.querySelectorAll('[role="option"]')]
      .find((option) => option.dataset.playerId === focusPlayerId);
    if (target) target.focus();
  }
}

function openMachineMultiPicker() {
  if ($("aiMultiTrigger").disabled) return;
  renderMachineMultiPicker();
  $("aiMultiTrigger").setAttribute("aria-expanded", "true");
  $("aiMultiMenu").classList.remove("hidden");
  $("aiMultiField").classList.add("open");
  const selected = $("aiMultiMenu").querySelector('[aria-selected="true"]');
  const first = $("aiMultiMenu").querySelector('[role="option"]');
  if (selected || first) (selected || first).focus();
}

function toggleMachineMultiPicker() {
  if ($("aiMultiTrigger").getAttribute("aria-expanded") === "true") {
    closeMachineMultiPicker();
  } else {
    openMachineMultiPicker();
  }
}

function eventStartedInsideMachinePicker(event) {
  const field = $("aiMultiField");
  const path = typeof event.composedPath === "function"
    ? event.composedPath()
    : [];
  return path.includes(field) || field.contains(event.target);
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

function gameOptionLabel(game, games) {
  if (game.game_type === "four_color_tenhu") {
    const counts = games.filter(item => isFourColorGame(item.game_type))
      .flatMap(allowedPlayerCountsForGame);
    return `四色牌 / ${gamePlayerCountLabel({allowed_player_counts: counts})}`;
  }
  return `${game.display_name || game.game_type} / ${gamePlayerCountLabel(game)}`;
}

function gameCategoryLabel(category) {
  return {board: "棋", card: "牌", dice: "骰", tabletop: "桌游"}[category] || "游戏";
}

function gameCategoryFor(declared) {
  const category = declared && declared.category;
  return ["board", "card", "dice", "tabletop"].includes(category) ? category : "";
}

function roomGameCategory(targetRoom) {
  const games = identity && Array.isArray(identity.games) ? identity.games : [];
  return gameCategoryFor(games.find(
    (game) => game && targetRoom && game.game_type === targetRoom.game_type
  ));
}

function compareGamePlayerCounts(left, right) {
  const leftCounts = allowedPlayerCountsForGame(left);
  const rightCounts = allowedPlayerCountsForGame(right);
  const leftSpan = leftCounts[leftCounts.length - 1] - leftCounts[0];
  const rightSpan = rightCounts[rightCounts.length - 1] - rightCounts[0];
  const rangeComparison = (
    leftCounts[leftCounts.length - 1] - rightCounts[rightCounts.length - 1]
    || leftSpan - rightSpan
    || leftCounts[0] - rightCounts[0]
    || leftCounts.length - rightCounts.length
  );
  if (rangeComparison) return rangeComparison;
  for (let index = 0; index < leftCounts.length; index += 1) {
    if (leftCounts[index] !== rightCounts[index]) {
      return leftCounts[index] - rightCounts[index];
    }
  }
  return 0;
}

function compareGameDisplayNames(left, right) {
  const leftName = String(left.display_name || left.game_type || "");
  const rightName = String(right.display_name || right.game_type || "");
  return leftName.localeCompare(
    rightName, "zh-CN-u-co-pinyin", {sensitivity: "base"}
  );
}

function isFourColorGame(gameType) {
  return ["four_color_tenhu", "four_color_jiuzhi", "four_color_qunhu"].includes(gameType);
}

function gameTokenEstimateLabel(gameType) {
  return ({
    // Final MCP v2 normal-round samples; cl100k_base, minified JSON responses.
    // Normal turn_state delta + move_reply only; bootstrap/full_state are measured separately.
    // Observed normal-round min/max: monopoly 41–1960, rummikub 33–459,
    // bomb_plane 27–150, carcassonne 44–1394. Not billing caps.
    carcassonne: "约100–260 token/轮",
    monopoly: "约50–600 token/轮",
    monopoly_plus: "约150–700 token/轮",
    rummikub: "约80–300 token/轮",
    gem_merchant: "约300–600 token/轮",
    bomb_plane: "约30–50 token/轮",
    aeroplane_chess: "约40–150 token/轮",
    banqi: "约50–150 token/轮",
    blackjack: "约100–220 token/轮",
    tictactoe: "约20–60 token/轮",
    texas_holdem: "约120–300 token/轮",
    train_cards: "约40–100 token/轮",
    gomoku: "约30–100 token/轮",
    go: "约100–250 token/轮",
    gandengyan: "约220–450 token/轮",
    guandan: "约500–900 token/轮",
    othello: "约30–120 token/轮",
    connect4: "约20–70 token/轮",
    checkers: "约40–150 token/轮",
    chess: "约40–180 token/轮",
    chinese_checkers: "约40–200 token/轮",
    dots_boxes: "约30–120 token/轮",
    doudizhu: "约300–600 token/轮",
    liars_dice: "约50–150 token/轮",
    mahjong: "约600–900 token/轮",
    yahtzee: "约80–200 token/轮",
    uno: "约250–500 token/轮",
    jungle: "约30–120 token/轮",
    junqi: "约300–450 token/轮",
    xiangqi: "约30–150 token/轮",
    zhajinhua: "约120–250 token/轮",
    // Four-color: 4 seats, seeds 9/37, actual /mcp/play responses; rounded min–p95.
    // 140/73/83 rounds; p95 829/735/657, observed maxima 1741/822/1228.
    four_color_tenhu: "约100–850 token/轮",
    four_color_jiuzhi: "约100–750 token/轮",
    four_color_qunhu: "约100–700 token/轮",
  })[gameType] || "";
}

function updateGameTokenEstimate() {
  const label = $("gameTokenEstimate");
  if (!label) return;
  const estimate = gameTokenEstimateLabel(selectedGameType($("gameType")));
  label.textContent = estimate ? `（${estimate}）` : "";
  label.title = (["monopoly", "rummikub", "bomb_plane", "carcassonne"].includes(selectedGameType($("gameType"))) || isFourColorGame(selectedGameType($("gameType"))))
    ? "正常轮次MCP增量估算，首次进入/手动完整同步另计，不含guide/chat/模型思考。cl100k_base实测一次state增量与move回复；多人连续行动、合法选择与终局结算会影响长度，不是计费上限。"
    : "";
}

function selectedGameType(select) {
  return select.value === "four_color_tenhu" ? (select.dataset.fourColorMode || select.value) : select.value;
}

function selectedGameOptions(select) {
  if (select.value !== "liars_dice") return undefined;
  const mode = select.dataset.diceMode || "classic";
  return {mode, literal_dice: mode === "party" && select.dataset.literalDice === "true",
    ...(mode === "party" ? JSON.parse(select.dataset.diceRules || "{}") : {}),
    ...(mode === "party" && select.dataset.diceTeams === "teams" ? {team_mode: "teams", random_teams: select.dataset.randomTeams !== "false"} : {})};
}

function sortedGamesForCategory(games, category) {
  return (Array.isArray(games) ? games : [])
    .filter((game) => game && game.game_type && !["four_color_jiuzhi", "four_color_qunhu"].includes(game.game_type) && gameCategoryFor(game) === category)
    .map((game, index) => ({game, index}))
    .sort((left, right) => (
      compareGamePlayerCounts(left.game, right.game)
      || compareGameDisplayNames(left.game, right.game)
      || left.index - right.index
    ))
    .map(({game}) => game);
}

function syncGameTypeOptions(games, {preserveSelection = true} = {}) {
  const select = $("gameType");
  const previousValue = select.value;
  const category = $("gameCategory").value;
  const categorizedGames = sortedGamesForCategory(games, category);
  select.replaceChildren();
  categorizedGames.forEach((game) => {
    const option = document.createElement("option");
    option.value = game.game_type;
    option.textContent = gameOptionLabel(game, games);
    select.appendChild(option);
  });
  if (!categorizedGames.length) {
    const option = document.createElement("option");
    option.value = "";
    option.textContent = `${gameCategoryLabel(category)}类暂无游戏`;
    option.disabled = true;
    option.selected = true;
    select.appendChild(option);
    select.value = "";
    select.disabled = true;
    updateGameTokenEstimate();
    renderGameRulePicker(select);
    return;
  }
  select.disabled = false;
  const values = [...select.options].map((option) => option.value);
  select.value = preserveSelection && values.includes(previousValue) ? previousValue : values[0];
  updateGameTokenEstimate();
  renderGameRulePicker(select);
}

// Optional game-owned precreation choices. No live catalog entry is added here.
function renderGameRulePicker(select) {
  const id = `${select.id}RulePicker`;
  document.getElementById(id)?.remove();
  if (select.value !== "four_color_tenhu") delete select.dataset.fourColorMode;
  const renderer = registeredGameUIRenderer(selectedGameType(select));
  if (!renderer?.renderRulePicker) return;
  const target = document.createElement("div");
  target.id = id;
  select.closest(".pixel-field").after(target);
  renderer.renderRulePicker(target, select);
}

function gameCategoryChanged() {
  const games = identity && Array.isArray(identity.games) ? identity.games : [];
  syncGameTypeOptions(games);
  $("gameType").dispatchEvent(new Event("change"));
}

function selectedGameRequirement() {
  const gameType = selectedGameType($("gameType"));
  const declared = identity && Array.isArray(identity.games)
    ? identity.games.find((game) => game.game_type === gameType)
    : null;
  const allowedPlayerCounts = selectedGameOptions($("gameType"))?.team_mode === "teams"
    ? [4, 6] : allowedPlayerCountsForGame(declared);
  const providerAvailable = Boolean(identity && identity.npc_provider && identity.npc_provider.available);
  const localNpcStrategy = Boolean(declared && declared.uses_local_npc_strategy);
  return {
    minPlayers: allowedPlayerCounts[0] || 2,
    maxPlayers: allowedPlayerCounts[allowedPlayerCounts.length - 1] || 2,
    allowedPlayerCounts,
    recommendedPlayers: declared ? declared.recommended_players : 2,
    supportsNpcs: Boolean(declared && declared.supports_npcs),
    npcAvailable: providerAvailable || localNpcStrategy,
    localNpcStrategy,
  };
}

function resolvedStakeMetadata(game, stake) {
  const value = String(stake);
  return {
    label: String(game?.stake_label || "🪙X/人").replaceAll("X", value),
    hint: String(game?.stake_hint || "").replaceAll("X", value),
  };
}

function renderSelectedStakeHint() {
  const party = selectedGameOptions($("gameType"))?.mode === "party";
  $("stake").disabled = false;
  const gameType = selectedGameType($("gameType"));
  const game = identity && Array.isArray(identity.games)
    ? identity.games.find((item) => item.game_type === gameType)
    : null;
  const stake = Number($("stake").value);
  const previewStake = Number.isInteger(stake) && stake > 0 ? stake : "X";
  const metadata = resolvedStakeMetadata(game, previewStake);
  const copy = party ? "每人最多亏一份本局筹码，整桌结束时按累计积分结算。" : metadata.hint
    ? `${metadata.label}（${metadata.hint}）`
    : metadata.label;
  const hint = $("gameStakeHint");
  // Mark presentation-only copy; touch keeps the original full explanation.
  hint.dataset.defaultCopy = String(!game?.stake_hint && (!game?.stake_label || game.stake_label === "🪙X/人"));
  hint.replaceChildren();
  if (!(stake > 0)) {
    const prefix = document.createElement("span");
    prefix.className = "stake-default-copy";
    prefix.textContent = "0 为娱乐局；非零时：";
    hint.appendChild(prefix);
  }
  hint.append(document.createTextNode(copy));
}

// This wording is desktop-only; keep the original touch label and DOM structure.
{
  const media = window.matchMedia?.("(hover: hover) and (pointer: fine)");
  const label = document.querySelector("#lobbyView .first-player-label");
  const touchLabel = label.textContent;
  const syncLabel = () => { label.textContent = media?.matches ? "谁先手" : touchLabel; };
  syncLabel();
  media?.addEventListener?.("change", syncLabel);
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
    localNpcStrategy,
  } = selectedGameRequirement();
  const multiplayer = maxPlayers > 2;
  const selected = selectedParticipantIds();
  const picker = select.closest(".participant-picker");
  const wasMultiplayer = picker.dataset.selectionMode === "multiple";
  if (multiplayer && !wasMultiplayer) {
    selectedMachineIds.clear();
    selected.forEach((playerId) => selectedMachineIds.add(playerId));
  } else if (!multiplayer && wasMultiplayer) {
    select.value = selected[0] || "";
  }
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
      : (!npcAvailable
        ? "部署者尚未配置 NPC 通道"
        : (localNpcStrategy ? "本游戏 NPC 使用本地规则策略" : "每桌最多补入 4 名 NPC"));
  }
  picker.dataset.selectionMode = multiplayer ? "multiple" : "single";
  $("aiSingleField").classList.toggle("hidden", multiplayer);
  $("aiMultiField").classList.toggle("hidden", !multiplayer);
  if (multiplayer) {
    const allowedIds = new Set(selectedParticipantIds().slice(0, selectedTargetPlayerCount() - 1));
    [...selectedMachineIds].forEach((playerId) => {
      if (!allowedIds.has(playerId)) selectedMachineIds.delete(playerId);
    });
    renderMachineMultiPicker();
  } else {
    closeMachineMultiPicker();
    if (selected.length > 1) select.value = selected[0];
  }
  renderCreateSeatPreview();
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
    && selectedGameType($("gameType"))
    && $("mode").value
    && selectedMachineCount >= 1
    && allowedPlayerCounts.includes(targetCount)
    && npcCount >= 0
    && npcCount <= 4
    && (selectedGameOptions($("gameType"))?.random_teams === false || npcCount === 0 || (fillWithNpcs && supportsNpcs && npcAvailable))
    && validStake
  );
  $("createButton").disabled = !ready;
  return ready;
}

function syncMachinePicker(machines) {
  const select = $("aiPlayer");
  selectedMachineIds.clear();
  selectedMachineWallets.clear();
  machineWalletRequest += 1;
  select.replaceChildren();
  if (!machines.length) {
    const option = document.createElement("option");
    option.value = "";
    option.textContent = "尚未绑定小机";
    select.appendChild(option);
    select.disabled = true;
    renderMachineMultiPicker();
    renderCreateSeatPreview();
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

function selectedMachinesForCreate() {
  const selectedIds = selectedParticipantIds();
  return identity
    ? identity.machines.filter((item) => selectedIds.includes(item.id))
    : [];
}

function chipBalanceText(balance) {
  const numericBalance = Number(balance);
  return Number.isFinite(numericBalance)
    ? new Intl.NumberFormat("zh-CN", {maximumFractionDigits: 0}).format(numericBalance)
    : String(balance ?? "—");
}

function renderCreateSeatPreview() {
  renderSeatPreview(selectedMachinesForCreate());
  updateCreateButtonState();
}

function renderSeatPreview(machines) {
  const preview = $("seatPreview");
  preview.replaceChildren();
  preview.classList.toggle("hidden", !identity);
  if (!identity) return;
  const targetCount = selectedTargetPlayerCount();
  const npcCount = Math.max(0, targetCount - 1 - machines.length);
  const fill = selectedFillWithNpcs();
  const seats = [
    {
      label: identity.human_name || "你",
      kind: "人类",
      balance: identity.wallet && identity.wallet.balance,
    },
    ...machines.map((machine) => ({
      label: machine.name,
      kind: "小机",
      balance: selectedMachineWallets.has(machine.id)
        ? selectedMachineWallets.get(machine.id)?.balance
        : "…",
    })),
    ...Array.from({length: npcCount}, () => ({
      label: fill ? "待随机" : "空位",
      kind: fill ? "NPC" : "未补齐",
    })),
  ];
  seats.slice(0, targetCount).forEach((seat, index) => {
    const item = document.createElement("article");
    item.className = "seat-preview-item";
    const number = document.createElement("span");
    number.className = "seat-preview-number";
    number.textContent = `席位 ${index + 1}`;
    const name = document.createElement("strong");
    name.className = "seat-preview-name";
    name.textContent = seat.label;
    name.title = seat.label;
    const kind = document.createElement("span");
    kind.className = "seat-preview-kind";
    kind.textContent = seat.kind;
    item.append(number, name, kind);
    if (Object.prototype.hasOwnProperty.call(seat, "balance")) {
      const balance = document.createElement("span");
      balance.className = "seat-preview-balance";
      balance.textContent = `🪙${chipBalanceText(seat.balance)}`;
      balance.title = `当前筹码：${chipBalanceText(seat.balance)}`;
      item.appendChild(balance);
    }
    preview.appendChild(item);
  });
}

async function machineSelectionChanged() {
  const selectedIds = selectedParticipantIds();
  const requestNumber = ++machineWalletRequest;
  renderCreateSeatPreview();
  const missingIds = selectedIds.filter(
    (playerId) => !selectedMachineWallets.has(playerId)
  );
  if (!missingIds.length) return;
  const results = await Promise.all(missingIds.map(async (playerId) => {
    try {
      const data = await request(
        `/api/chips/machines/${encodeURIComponent(playerId)}`
      );
      return [playerId, data.wallet || null];
    } catch (_error) {
      return [playerId, null];
    }
  }));
  if (requestNumber !== machineWalletRequest) return;
  results.forEach(([playerId, wallet]) => {
    selectedMachineWallets.set(playerId, wallet);
  });
  renderCreateSeatPreview();
}

function renderHumanChipBalance(balance) {
  const numericBalance = Number(balance);
  const isNumeric = Number.isFinite(numericBalance);
  const balanceText = chipBalanceText(balance);
  const negative = isNumeric && numericBalance < 0;
  const longBalance = balanceText.length > 10;
  const balanceTarget = $("humanChipBalance");
  const balanceLink = $("chipBalanceLink");
  balanceTarget.textContent = balanceText;
  balanceTarget.title = `当前余额：${balanceText}`;
  balanceTarget.setAttribute("aria-label", `当前人类筹码余额 ${balanceText}`);
  balanceLink.classList.toggle("negative", negative);
  balanceLink.classList.toggle("long-balance", longBalance);
  balanceLink.setAttribute("aria-label", `我的筹码，余额 ${balanceText}，进入筹码中心`);
}

async function loadIdentity({quiet = false} = {}) {
  try {
    const data = await request("/api/whoami");
    if (!data.bound) {
      identity = null;
      notificationHistory = [];
      room = null;
      stopPolling();
      $("pairLabel").textContent = "LOGIN REQUIRED";
      renderUnreadBadges(null);
      showView("unboundView");
      return;
    }
    const initialIdentityLoad = !identity;
    identity = data;
    syncUnreadStateToIdentity();
    await loadCatalogGameRenderers(data.games || []);
    $("pairLabel").textContent = data.identity_label;
    $("heroPair").textContent = data.identity_label;
    renderHumanChipBalance(data.wallet.balance);
    renderUnreadBadges(identity.unread);
    syncGameTypeOptions(data.games || [], {preserveSelection: !initialIdentityLoad});
    syncMachinePicker(data.machines || []);
    renderSelectedStakeHint();
    const invitations = data.pending_invitations || [];
    const {outgoing, remainingRooms} = partitionLobbyPendingRooms(
      data.rooms || [], invitations
    );
    renderPendingInvitations(invitations, outgoing);
    if (roomListScope === "machines") await loadSpectatorRooms();
    else renderRooms(remainingRooms);
    if (!room) {
      showView("lobbyView");
    }
    if (!quiet) showNotice("");
    if (initialIdentityLoad) {
      await refreshHumanUnreadState({autoPopup: true});
    }
  } catch (error) {
    if (![401, 403].includes(error.status)) {
      if (!quiet) showNotice(error.message, true);
      return;
    }
    identity = null;
    notificationHistory = [];
    room = null;
    stopPolling();
    $("pairLabel").textContent = "OFFLINE";
    renderUnreadBadges(null);
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
        game_type: selectedGameType($("gameType")),
        game_options: selectedGameOptions($("gameType")),
        mode: $("mode").value,
        stake,
        target_player_count: selectedTargetPlayerCount(),
        fill_with_npcs: selectedFillWithNpcs(),
      }),
    });
    if (data.room.status === "pending") {
      room = null;
      await loadIdentity({quiet: true});
      showNotice(pendingRoomCreatedNotice(data.room));
      return;
    }
    renderGame(data.room, data.message, data.timeline);
    writeRoomRoute(data.room.room_id, {mode: "push", backToLobby: true});
    startRoomPolling();
  } catch (error) {
    showNotice(error.message, true);
  } finally {
    updateCreateButtonState();
  }
}

function scheduleRoomOpen(intent, delayMs = 0) {
  if (document.hidden || roomRefreshSuspended || roomOpenIntent !== intent || pollTimer !== null) return;
  pollTimer = setTimeout(() => {
    pollTimer = null;
    if (roomOpenIntent === intent && !document.hidden && !roomRefreshSuspended) {
      void openRoom(intent.roomId, intent.options);
    }
  }, Math.min(2147483647, Math.max(delayMs, roomRefreshDelay())));
}

async function openRoom(roomId, {historyMode = "push", routeRestore = false, perspective = ""} = {}) {
  stopPolling();
  roomLeaving = false;
  const intent = {roomId, options: {historyMode, routeRestore, perspective}};
  roomOpenIntent = intent;
  if (document.hidden || roomRefreshSuspended || roomRefreshDelay() > 0) {
    scheduleRoomOpen(intent);
    return;
  }
  const generation = roomSyncGeneration;
  const controller = new AbortController();
  roomRequestController = controller;
  try {
    const query = perspective ? `?perspective=${encodeURIComponent(perspective)}` : "";
    const data = await request(`/api/rooms/${roomId}${query}`, {signal: controller.signal});
    if (generation !== roomSyncGeneration || roomOpenIntent !== intent || document.hidden) return;
    roomOpenIntent = null;
    roomRetryAt = 0;
    roomRefreshFailures = 0;
    if (Date.now() >= readRetryAt) rateLimitFailures = 0;
    renderGame(data.room, data.message, data.timeline);
    if (historyMode === "push") {
      writeRoomRoute(data.room.room_id, {mode: "push", backToLobby: true});
    } else if (historyMode === "replace") {
      writeRoomRoute(data.room.room_id, {mode: "replace", backToLobby: false});
    }
    if (!data.room.spectating) await ackHumanNotifications("game", roomId);
  } catch (error) {
    if (error.name === "AbortError" || generation !== roomSyncGeneration) return;
    if (error.status === 429 || !error.status || error.status >= 500) {
      scheduleRoomOpen(intent, deferRoomRetry(error));
      if (!routeRestore) showNotice(error.message, true);
      return;
    }
    roomOpenIntent = null;
    if (room?.spectating && [401, 403, 404].includes(error.status)) {
      await backToLobby({fromHistory: true});
      writeRoomRoute(null, {mode: "replace"});
      toast(error.message);
      return;
    }
    if (routeRestore) {
      room = null;
      writeRoomRoute(null, {mode: "replace"});
      await loadIdentity({quiet: true});
      toast(error.message);
      return;
    }
    showNotice(error.message, true);
  } finally {
    if (roomRequestController === controller) roomRequestController = null;
    if (generation === roomSyncGeneration && !roomOpenIntent) startRoomPolling();
  }
}

async function backToLobby({fromHistory = false} = {}) {
  // Cancel immediately, including the asynchronous history.back() path.
  roomLeaving = true;
  stopPolling();
  if (
    !fromHistory
    && window.history.state
    && window.history.state.duelView === "room"
    && window.history.state.duelBackToLobby
  ) {
    window.history.back();
    return;
  }
  const wasSpectating = Boolean(room?.spectating);
  room = null;
  roomLeaving = false;
  selectedJungleCell = null;
  selectedXiangqiCell = null;
  pendingMove = null;
  liarsBidDraft = null;
  stopPolling();
  hideWaitModeModal();
  closeHistory();
  closeNotificationModal();
  if (!fromHistory) writeRoomRoute(null, {mode: "replace"});
  showView("lobbyView");
  if (wasSpectating && identity) await loadSpectatorRooms();
  else await loadIdentity({quiet: true});
}

function canHumanMove() {
  if (room?.spectating) return false;
  const human = participantFor("human");
  if (!room || !human || room.status !== "playing") return false;
  if (room.game_type === "bomb_plane" && room.board_state?.phase === "setup") {
    return Boolean(
      Array.isArray(room.private_state?.legal_actions)
      && room.private_state.legal_actions.length
    );
  }
  return room.current_player_id === human.player_id;
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
  if (!room.spectating && room.viewer && owner.player_id === room.viewer.player_id) return "你";
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
    cell.classList.add(`preview-${String(room.viewer?.token || (state.marks || {}).human || "X").toLowerCase()}`);
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
  const confirmButton = $("confirmMoveButton");
  confirmButton.disabled = !ready;
  confirmButton.classList.toggle("ready-to-submit", ready);
  confirmButton.textContent = "落子";
  if (isTerminal(room)) {
    $("selectionHint").textContent = roomTurnText(room);
  } else {
    const readyText = (
      room.game_type === "connect4" && Number.isInteger(pendingMove && pendingMove.col)
        ? `已选择第 ${pendingMove.col + 1} 列，可以落子`
        : (room.game_type === "dots_boxes"
          ? "已选择一条边，可以落子"
          : "已选中落点，可以落子")
    );
    const waitingText = room.game_type === "dots_boxes"
      ? "请选择一条边"
      : "请先在棋盘上选择落点";
    $("selectionHint").textContent = ready
      ? readyText
      : (canHumanMove() ? waitingText : "等待轮到你");
  }
}

function renderGridBoard(board, state) {
  const othelloLegal = room.game_type === "othello"
    ? new Set(
      ((state.legal_moves_by_mark || {})[
        (room.viewer || {}).token || (state.marks || {}).human
      ] || [])
        .map((move) => `${move.row},${move.col}`)
    )
    : null;
  state.board.forEach((rowData, rowIndex) => {
    rowData.forEach((mark, colIndex) => {
      const payload = {row: rowIndex, col: colIndex};
      const cell = boardCell(mark, rowIndex, colIndex, () => selectMove(payload));
      if (othelloLegal) {
        const legal = othelloLegal.has(`${rowIndex},${colIndex}`);
        cell.disabled = !canHumanMove() || !legal;
        cell.classList.toggle("legal-target", legal && canHumanMove());
      }
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
  const terminal = Boolean(
    typeof room !== "undefined"
    && room
    && ["finished", "archived"].includes(room.status)
  );
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
      cell.ariaLabel = terminal
        ? (mark
          ? `第 ${colIndex + 1} 列，第 ${rowIndex + 1} 行为${pieceDescription(mark)}`
          : `第 ${colIndex + 1} 列，第 ${rowIndex + 1} 行为空位`)
        : (mark
          ? `第 ${colIndex + 1} 列，第 ${rowIndex + 1} 行为${pieceDescription(mark)}；选择此列`
          : `第 ${colIndex + 1} 列空位；选择后棋片落到第 ${landingRow + 1} 行`);
      const selected = !terminal
        && movesEqual(pendingMove, payload)
        && rowIndex === landingRow;
      cell.classList.toggle("selected", selected);
      cell.setAttribute("aria-pressed", String(selected));
      if (selected) cell.classList.add(`preview-${String(room.viewer?.token || (state.marks || {}).human || "X").toLowerCase()}`);
      board.appendChild(cell);
    });
  });
}

function dotsPreviewSeatClass() {
  const viewerId = room && room.viewer && room.viewer.player_id;
  const participant = participantByPlayerId(viewerId) || participantFor("human");
  return participant && Number.isInteger(participant.seat_index)
    ? `seat-${participant.seat_index}`
    : "";
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
        const previewSeatClass = selected ? dotsPreviewSeatClass() : "";
        if (previewSeatClass) edge.classList.add(previewSeatClass);
        if (selected) edge.ariaLabel += "，待确认";
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
        const previewSeatClass = selected ? dotsPreviewSeatClass() : "";
        if (previewSeatClass) edge.classList.add(previewSeatClass);
        if (selected) edge.ariaLabel += "，待确认";
        edge.setAttribute("aria-pressed", String(selected));
        edge.addEventListener("click", () => selectMove(payload));
        board.appendChild(edge);
      } else {
        const box = document.createElement("span");
        const owner = state.boxes[(gridRow - 1) / 2][(gridCol - 1) / 2];
        box.className = `box${owner ? " owned" : ""}${pieceClass(owner)}`;
        const boxRow = (gridRow - 1) / 2;
        const boxCol = (gridCol - 1) / 2;
        box.dataset.boxRow = String(boxRow);
        box.dataset.boxCol = String(boxCol);
        if (owner) {
          const participant = participantForOwner(owner);
          const seatNumber = participant && Number.isInteger(participant.seat_index)
            ? participant.seat_index + 1
            : "?";
          const ownerLabel = document.createElement("span");
          ownerLabel.className = "box-owner-label";
          ownerLabel.textContent = String(seatNumber);
          ownerLabel.setAttribute("aria-hidden", "true");
          box.appendChild(ownerLabel);
          box.setAttribute("role", "img");
          box.ariaLabel = (
            `第 ${boxRow + 1} 行第 ${boxCol + 1} 格归`
            + `${ownerDescription(owner)}（座位 ${seatNumber}）所有`
          );
        } else {
          box.setAttribute("aria-hidden", "true");
        }
        board.appendChild(box);
      }
    }
  }
}

function renderJungleBoard(board, state) {
  const terminal = Boolean(
    typeof room !== "undefined"
    && room
    && ["finished", "archived"].includes(room.status)
  );
  const humanMark = (room.viewer || {}).token || (state.marks || {}).human;
  const legalMoves = ((state.legal_moves_by_mark || {})[humanMark] || []);
  const legalOrigins = new Set(
    legalMoves.map((move) => `${move.from_row},${move.from_col}`)
  );
  const rotated = humanMark === "O";
  const rowOrder = Array.from(
    {length: 9}, (_, index) => rotated ? 8 - index : index
  );
  const colOrder = Array.from(
    {length: 7}, (_, index) => rotated ? 6 - index : index
  );

  board.classList.toggle("rotated-view", rotated);
  board.dataset.viewMark = humanMark;
  rowOrder.forEach((rowIndex, displayRow) => {
    colOrder.forEach((colIndex, displayCol) => {
      const piece = state.board[rowIndex][colIndex];
      const cell = document.createElement("button");
      const key = `${rowIndex},${colIndex}`;
      const owner = piece ? piece.split(":")[0] : null;
      let terrain = null;
      if (JUNGLE_WATER.has(key)) terrain = {kind: "water", label: "河", name: "河道"};
      if (JUNGLE_TRAPS.has(key)) terrain = {kind: "trap", label: "陷", name: "陷阱"};
      if (JUNGLE_DENS.has(key)) terrain = {kind: "den", label: "穴", name: "兽穴"};
      cell.type = "button";
      cell.className = `cell jungle-cell${piece ? " occupied" : ""}${pieceClass(owner)}`;
      cell.dataset.moveRow = String(rowIndex);
      cell.dataset.moveCol = String(colIndex);
      cell.dataset.displayRow = String(displayRow);
      cell.dataset.displayCol = String(displayCol);
      if (terrain) {
        cell.classList.add(terrain.kind);
        const terrainElement = document.createElement("span");
        terrainElement.className = `jungle-terrain jungle-terrain-${terrain.kind}`;
        terrainElement.textContent = terrain.label;
        terrainElement.setAttribute("aria-hidden", "true");
        cell.appendChild(terrainElement);
      }
      if (
        !terminal
        && selectedJungleCell
        && selectedJungleCell.row === rowIndex
        && selectedJungleCell.col === colIndex
      ) cell.classList.add("selected-origin");
      if (
        !terminal
        && pendingMove
        && pendingMove.to_row === rowIndex
        && pendingMove.to_col === colIndex
      ) cell.classList.add("selected");
      if (piece) {
        const [pieceOwner, beast] = piece.split(":");
        const pieceElement = document.createElement("span");
        const side = pieceOwner === humanMark ? "human" : "ai";
        pieceElement.className = `jungle-piece jungle-piece-${side}`;
        pieceElement.textContent = JUNGLE_SYMBOLS[beast];
        pieceElement.setAttribute("aria-hidden", "true");
        cell.appendChild(pieceElement);
      }
      cell.ariaLabel = (
        `第 ${rowIndex + 1} 行第 ${colIndex + 1} 列`
        + `${terrain ? `，${terrain.name}` : "，陆地"}`
        + `${piece ? `，${ownerDescription(owner)}的${JUNGLE_SYMBOLS[piece.split(":")[1]]}` : "，空位"}`
      );
      const selectedTargets = selectedJungleCell
        ? legalMoves.filter((move) => (
          move.from_row === selectedJungleCell.row
          && move.from_col === selectedJungleCell.col
        ))
        : [];
      const legalTarget = selectedTargets.some((move) => (
        move.to_row === rowIndex && move.to_col === colIndex
      ));
      const legalOrigin = legalOrigins.has(key);
      cell.classList.toggle("legal-target", legalTarget);
      cell.disabled = (
        terminal || !canHumanMove()
        || (!legalOrigin && !legalTarget)
      );
      cell.addEventListener("click", () => {
        if (terminal || !canHumanMove()) return;
        if (!selectedJungleCell) {
          if (!legalOrigin) return;
          selectedJungleCell = {row: rowIndex, col: colIndex};
          pendingMove = null;
          renderBoard();
          return;
        }
        if (piece && piece.startsWith(`${humanMark}:`) && legalOrigin) {
          selectedJungleCell = {row: rowIndex, col: colIndex};
          pendingMove = null;
          renderBoard();
          return;
        }
        if (!legalTarget) return;
        pendingMove = selectedTargets.find((move) => (
          move.to_row === rowIndex && move.to_col === colIndex
        ));
        renderBoard();
      });
      board.appendChild(cell);
    });
  });
}

function liarsParticipantName(playerId) {
  const participant = participantByPlayerId(playerId);
  return (participant && participant.display_name) || playerId || "未知玩家";
}

function liarsRoundResultIsVisible(state) {
  return Boolean(state.last_round_result && !state.current_bid);
}

function liarsRoundResultLines(outcome) {
  const bid = outcome.bid || {};
  const loser = outcome.loser_display_name
    || liarsParticipantName(outcome.loser_player_id);
  const eliminated = outcome.eliminated
    || Boolean(outcome.eliminated_player_id)
    || outcome.loser_remaining_dice === 0;
  if (outcome.mode === "party") {
    const team = outcome.loser_team_id === "red" ? "红队"
      : outcome.loser_team_id === "blue" ? "蓝队" : loser;
    const called = `叫 ${bid.quantity} 个 ${bid.face} 点${bid.exact ? "（斋）" : ""}`;
    return {
      outcome: `${called} · ${outcome.phase === "surrendered" ? "投降认罚"
        : `实际 ${outcome.actual_count} 个 · 叫点${outcome.bid_holds ? "成功" : "失败"}`}`,
      loss: `${team} ${outcome.score_delta ?? -1} 分${Number.isFinite(outcome.score_after)
        ? ` · 累计 ${outcome.score_after} 分` : ""}`,
    };
  }
  return {
    outcome: outcome.phase === "surrendered" ? "本轮投降认罚" : (
      `实际 ${outcome.actual_count} 个 ${bid.face} 点${bid.exact ? "（斋）" : ""}`
      + ` · 叫点${outcome.bid_holds ? "成功" : "失败"}${outcome.dark_reversed ? " · 天黑黑反转胜负" : ""}`
    ),
    loss: outcome.mode === "party" ? `${loser}${outcome.loser_team_id ? "所在队" : ""} 本局积分 ${outcome.score_delta ?? -1}${Number.isFinite(outcome.score_after) ? ` · 累计 ${outcome.score_after} 分` : ""} · 骰数不变` : (
      `${loser} -1 骰`
      + ` · ${eliminated ? "已淘汰" : `剩余 ${outcome.loser_remaining_dice}`}`
    ),
  };
}

function liarsBidSelectionIsLegal(state, selection) {
  if (!selection) return false;
  const quantity = Number(selection.quantity);
  const face = Number(selection.face);
  const maximum = Number(state.max_bid_quantity || 0);
  if (
    !Number.isInteger(quantity) || !Number.isInteger(face)
    || quantity < 1 || quantity > maximum || face < 1 || face > 6
  ) return false;
  if (Array.isArray(state.bid_ranges)) {
    return state.bid_ranges.some((bid) => bid.face === face
      && Boolean(bid.exact) === Boolean(selection.exact)
      && quantity >= bid.min_quantity);
  }
  const currentBid = state.current_bid;
  return !currentBid
    || quantity > currentBid.quantity
    || (quantity === currentBid.quantity && face > currentBid.face);
}

function defaultLiarsBidSelection(state) {
  if (Array.isArray(state.bid_ranges) && state.bid_ranges.length) {
    const bid = state.bid_ranges.reduce((best, item) => item.min_quantity < best.min_quantity ? item : best);
    return {quantity: bid.min_quantity, face: bid.face, exact: Boolean(bid.exact)};
  }
  const maximum = Number(state.max_bid_quantity || 0);
  for (let quantity = 1; quantity <= maximum; quantity += 1) {
    for (let face = 1; face <= 6; face += 1) {
      const selection = {quantity, face};
      if (liarsBidSelectionIsLegal(state, selection)) return selection;
    }
  }
  return {quantity: Math.max(1, maximum), face: 6};
}

function liarsBidSelectionFor(state) {
  const reusable = Boolean(
    liarsBidDraft
    && room
    && liarsBidDraft.roomId === room.room_id
    && liarsBidDraft.actorId === room.current_player_id
    && canHumanMove()
    && liarsBidSelectionIsLegal(state, liarsBidDraft)
  );
  if (reusable) {
    return {quantity: liarsBidDraft.quantity, face: liarsBidDraft.face, exact: liarsBidDraft.exact};
  }
  liarsBidDraft = null;
  return defaultLiarsBidSelection(state);
}

function xiangqiTerminalStatusText(state, targetRoom) {
  const winnerId = targetRoom.winner_player_id
    || (targetRoom.result || {}).winner_player_id;
  const participants = Array.isArray(targetRoom.participants)
    ? targetRoom.participants
    : [];
  const winner = participants.find(
    (participant) => participant.player_id === winnerId
  ) || participants.find(
    (participant) => participant.token === state.winner_mark
  ) || null;
  const winnerName = winner
    ? (winner.display_name || winner.player_id)
    : (state.winner_mark === "X" ? "红方" : state.winner_mark === "O" ? "黑方" : "");
  const reason = state.in_checkmate || state.terminal_reason === "checkmate"
    ? "将死 · "
    : (state.in_stalemate || state.terminal_reason === "stalemate" ? "困毙 · " : "");
  if (state.winner_mark === "draw" || targetRoom.winner === "draw") {
    return `${reason}对局结束 · 和棋`;
  }
  return winnerName
    ? `${reason}对局结束 · ${winnerName}获胜`
    : `${reason}对局结束`;
}

function rememberLiarsBidSelection(quantity, face, exact = false) {
  liarsBidDraft = {
    roomId: room && room.room_id,
    revision: room && room.revision,
    actorId: room && room.current_player_id,
    quantity: Number(quantity),
    face: Number(face),
    exact,
  };
}

function renderXiangqiBoard(board, state) {
  const terminal = Boolean(
    ["finished", "archived"].includes(room.status)
    || state.in_checkmate
    || state.in_stalemate
    || state.in_draw
    || state.winner_mark
  );
  const humanColor = ((room.viewer || {}).token || (state.marks || {}).human) === "O" ? "b" : "r";
  const rotated = humanColor === "b";
  const rowOrder = Array.from(
    {length: 10}, (_, index) => rotated ? 9 - index : index
  );
  const colOrder = Array.from(
    {length: 9}, (_, index) => rotated ? 8 - index : index
  );
  const legalMoves = Array.isArray(state.legal_moves) ? state.legal_moves : [];
  const selectedMoves = !terminal && selectedXiangqiCell
    ? legalMoves.filter((candidate) => (
      candidate.from_row === selectedXiangqiCell.row
      && candidate.from_col === selectedXiangqiCell.col
    ))
    : [];

  board.classList.toggle("rotated-view", rotated);
  board.dataset.viewColor = humanColor;
  rowOrder.forEach((rowIndex, displayRow) => {
    colOrder.forEach((colIndex, displayCol) => {
      const piece = state.board[rowIndex][colIndex];
      const [pieceColor, pieceType] = piece ? piece.split(":") : [null, null];
      const ownerMark = pieceColor === "r" ? "X" : (pieceColor === "b" ? "O" : null);
      const legalTarget = selectedMoves.some((candidate) => (
        candidate.to_row === rowIndex && candidate.to_col === colIndex
      ));
      const cell = document.createElement("button");
      cell.type = "button";
      cell.className = `cell${piece ? " occupied" : ""}${pieceClass(ownerMark)}`;
      // The DOM is reordered for perspective, while these remain authoritative
      // backend coordinates used by last_move lookup and move submission.
      cell.dataset.moveRow = String(rowIndex);
      cell.dataset.moveCol = String(colIndex);
      cell.dataset.displayRow = String(displayRow);
      cell.dataset.displayCol = String(displayCol);
      if (displayRow === 0) cell.classList.add("edge-top");
      if (displayRow === 9) cell.classList.add("edge-bottom");
      if (displayCol === 0) cell.classList.add("edge-left");
      if (displayCol === 8) cell.classList.add("edge-right");
      if (displayRow === 4) cell.classList.add("river-top");
      if (displayRow === 5) cell.classList.add("river-bottom");
      if (
        !terminal
        && selectedXiangqiCell
        && selectedXiangqiCell.row === rowIndex
        && selectedXiangqiCell.col === colIndex
      ) cell.classList.add("selected-origin");
      if (
        pendingMove
        && pendingMove.to_row === rowIndex
        && pendingMove.to_col === colIndex
      ) cell.classList.add("selected");
      if (legalTarget) {
        cell.classList.add(piece ? "legal-capture" : "legal-target");
        const targetDot = document.createElement("span");
        targetDot.className = "legal-target-dot";
        targetDot.setAttribute("aria-hidden", "true");
        cell.appendChild(targetDot);
      }
      for (
        const direction of XIANGQI_PALACE_LINES.get(
          `${displayRow},${displayCol}`
        ) || []
      ) {
        const diagonal = document.createElement("span");
        diagonal.className = `palace-diagonal ${direction}`;
        diagonal.setAttribute("aria-hidden", "true");
        cell.appendChild(diagonal);
      }
      if (piece) {
        const token = document.createElement("span");
        token.className = `xiangqi-piece color-${pieceColor}`;
        token.textContent = XIANGQI_SYMBOLS[pieceColor][pieceType];
        token.setAttribute("aria-hidden", "true");
        cell.appendChild(token);
        if (
          !terminal
          && pieceType === "k"
          && pieceColor === state.turn_color
          && state.in_check
        ) {
          cell.classList.add("in-check");
        }
      }
      const pieceLabel = piece
        ? `${pieceColor === "r" ? "红" : "黑"}${XIANGQI_SYMBOLS[pieceColor][pieceType]}`
        : "空位";
      cell.ariaLabel = (
        `从己方视角第 ${displayRow + 1} 行第 ${displayCol + 1} 路，${pieceLabel}`
        + `${legalTarget ? "，合法落点" : ""}`
      );
      cell.disabled = !canHumanMove();
      cell.addEventListener("click", () => {
        if (!canHumanMove()) return;
        if (pieceColor === humanColor) {
          if (
            selectedXiangqiCell
            && selectedXiangqiCell.row === rowIndex
            && selectedXiangqiCell.col === colIndex
          ) {
            selectedXiangqiCell = null;
          } else {
            selectedXiangqiCell = {row: rowIndex, col: colIndex};
          }
          pendingMove = null;
          renderBoard();
          return;
        }
        if (!selectedXiangqiCell || !legalTarget) return;
        pendingMove = {
          from_row: selectedXiangqiCell.row,
          from_col: selectedXiangqiCell.col,
          to_row: rowIndex,
          to_col: colIndex,
        };
        renderBoard();
      });
      board.appendChild(cell);
    });
  });

  board.classList.toggle("in-check-state", Boolean(state.in_check && !terminal));
  if (terminal || state.in_check) {
    const notice = document.createElement("span");
    notice.className = `xiangqi-check-notice${terminal ? " terminal" : ""}`;
    notice.setAttribute("role", "status");
    notice.setAttribute("aria-live", "polite");
    notice.textContent = terminal
      ? xiangqiTerminalStatusText(state, room)
      : `${state.turn_color === "r" ? "红方" : "黑方"} · 将军`;
    board.appendChild(notice);
  }

}

function createLiarsDie(documentRef, value) {
  const face = Number(value);
  const die = documentRef.createElement("i");
  die.className = "liars-die";
  die.setAttribute("data-face", String(face));
  die.setAttribute("role", "img");
  die.setAttribute("aria-label", `${face} 点骰子`);
  return die;
}

function canAcknowledgeLiarsRound() {
  if (room?.spectating) return false;
  if (room?.board_state?.mode === "party") {
    const viewer = participantFor("human");
    return canHumanMove() && viewer?.role === "human"
      && (!viewer.participant_kind || viewer.participant_kind === "human")
      && viewer.join_status === "joined" && viewer.active !== false
      && (!viewer.activity_state || viewer.activity_state === "active");
  }
  return room?.room_kind !== "invite" || canHumanMove();
}

function renderLiarsDice(board, state) {
  const flow = state.flow || {};
  const terminal = Boolean(
    (typeof room !== "undefined"
      && room
      && ["finished", "archived"].includes(room.status))
    || flow.phase === "finished"
  );
  const roundNumber = flow.round_number;
  const outcome = state.last_round_result;
  const isHistoricalResult = Boolean(
    outcome
    && Number.isInteger(outcome.round)
    && Number.isInteger(roundNumber)
    && outcome.round < roundNumber
  );
  const awaitingRoundAcknowledgement = !terminal && (
    flow.phase === "awaiting_round_acknowledgement"
  );
  const showRoundResult = (
    (terminal && Boolean(outcome))
    || awaitingRoundAcknowledgement
    || liarsRoundResultIsVisible(state)
  );
  let result = null;
  if (outcome && showRoundResult) {
    result = document.createElement("section");
    result.className = isHistoricalResult
      ? "liars-round-result liars-previous-round"
      : "liars-round-result";
    result.classList.toggle(
      "awaiting-acknowledgement", awaitingRoundAcknowledgement
    );
    result.ariaLabel = awaitingRoundAcknowledgement
      ? "本轮结算，等待确认下一轮"
      : (isHistoricalResult ? "上一轮结算" : "本轮结算");
    const title = document.createElement("strong");
    title.className = "liars-result-title";
    title.textContent = `第 ${outcome.round} 轮结算`;
    const resultLines = liarsRoundResultLines(outcome);
    const outcomeLine = document.createElement("p");
    outcomeLine.className = "liars-result-line liars-result-outcome";
    outcomeLine.textContent = resultLines.outcome;
    const lossLine = document.createElement("p");
    lossLine.className = "liars-result-line liars-result-loss";
    lossLine.textContent = resultLines.loss;
    result.append(title, outcomeLine, lossLine);
    if (awaitingRoundAcknowledgement) {
      const pending = state.pending_next_round || {};
      const nextRound = Number.isInteger(pending.round_number)
        ? pending.round_number
        : Number(roundNumber) + 1;
      const acknowledgement = document.createElement("div");
      acknowledgement.className = "liars-round-acknowledgement";
      const prompt = document.createElement("p");
      prompt.textContent = outcome.preserve_dice ? "本轮已认罚，不揭骰；确认后保留骰面进入下一轮。" : "本轮已结算。确认后才会重新掷骰并开始下一轮。";
      const acknowledgeButton = document.createElement("button");
      acknowledgeButton.type = "button";
      acknowledgeButton.className = "pixel-btn";
      acknowledgeButton.textContent = state.mode === "party"
        ? `确认，开始第 ${nextRound} 轮` : `知道了，开始第 ${nextRound} 轮`;
      acknowledgeButton.disabled = !canAcknowledgeLiarsRound();
      acknowledgeButton.addEventListener("click", async () => {
        acknowledgeButton.disabled = true;
        await acknowledgeLiarsRound(acknowledgeButton);
      });
      if (state.mode === "party" && !canAcknowledgeLiarsRound()) {
        prompt.textContent = `本轮已结算，等待 ${liarsParticipantName(room.current_player_id)} 确认开始第 ${nextRound} 轮。`;
        acknowledgement.append(prompt);
      } else {
        acknowledgement.append(prompt, acknowledgeButton);
      }
      result.appendChild(acknowledgement);
    }

    if (outcome.mode === "party" && window.DuelDiceRules) {
      window.DuelDiceRules.appendPenaltyReview(result, {participants: room.participants}, outcome);
    }

    const reveal = document.createElement("details");
    reveal.className = "liars-reveal-details";
    const revealToggle = document.createElement("summary");
    revealToggle.textContent = isHistoricalResult
      ? "查看上一轮揭骰"
      : "查看本轮揭骰";
    reveal.appendChild(revealToggle);
    const diceList = document.createElement("div");
    diceList.className = "liars-revealed-dice";
    Object.entries(outcome.revealed_dice_by_player || {}).forEach(([playerId, dice]) => {
      const row = document.createElement("div");
      row.className = "liars-revealed-player";
      const participant = participantByPlayerId(playerId);
      const owner = document.createElement("span");
      owner.className = "liars-dice-owner";
      const teamIndex = Object.values(state.teams || {}).findIndex((members) => members.includes(playerId));
      owner.textContent = teamIndex >= 0 ? `${Object.keys(state.teams)[teamIndex] === "red" ? "红队" : Object.keys(state.teams)[teamIndex] === "blue" ? "蓝队" : `第${teamIndex + 1}队`}共用骰盅：` : `${(participant && participant.display_name) || playerId}：`;
      const tray = document.createElement("span");
      tray.className = "liars-dice-row";
      if (Array.isArray(dice) && dice.length) {
        dice.forEach((value) => tray.appendChild(createLiarsDie(document, value)));
      } else {
        tray.textContent = "无骰";
      }
      row.append(owner, tray);
      diceList.appendChild(row);
    });
    reveal.appendChild(diceList);
    if (!outcome.preserve_dice) result.appendChild(reveal);
  }
  if (awaitingRoundAcknowledgement) {
    if (result) board.appendChild(result);
    return;
  }

  if (terminal) {
    const terminalReview = document.createElement("section");
    terminalReview.className = "liars-current-round liars-terminal-review";
    terminalReview.ariaLabel = "吹牛骰子整局终局复盘";
    const terminalHeading = document.createElement("div");
    terminalHeading.className = "liars-round-heading";
    const terminalTitle = document.createElement("strong");
    terminalTitle.textContent = Number.isInteger(roundNumber)
      ? `第 ${roundNumber} 轮 · 已结算`
      : "本局已结束";
    const terminalStatus = document.createElement("span");
    terminalStatus.textContent = "整局已结束，仅保留终局复盘";
    terminalHeading.append(terminalTitle, terminalStatus);
    terminalReview.appendChild(terminalHeading);
    board.appendChild(terminalReview);
    if (result) board.appendChild(result);
    return;
  }

  const currentRound = document.createElement("section");
  currentRound.className = "liars-current-round";
  currentRound.ariaLabel = Number.isInteger(roundNumber)
    ? `第 ${roundNumber} 轮当前操作区`
    : "当前轮操作区";
  const humanCanMove = canHumanMove();
  const currentTurnName = (
    flow.phase !== "finished"
    && room
    && room.status === "playing"
    && room.current_player_id
  )
    ? (humanCanMove ? "你" : liarsParticipantName(room.current_player_id))
    : null;
  const roundHeading = document.createElement("div");
  roundHeading.className = "liars-round-heading";
  const roundTitle = document.createElement("strong");
  roundTitle.textContent = Number.isInteger(roundNumber)
    ? `第 ${roundNumber} 轮${flow.phase === "finished" ? " · 已结算" : " · 当前轮"}`
    : "当前轮";
  const currentTurnLine = document.createElement("div");
  currentTurnLine.className = "liars-turn-line";
  const currentTurnPrefix = document.createElement("span");
  currentTurnPrefix.className = "liars-turn-prefix";
  currentTurnPrefix.textContent = "轮到：";
  const currentTurnNameElement = document.createElement("span");
  currentTurnNameElement.className = "liars-turn-name";
  currentTurnNameElement.textContent = currentTurnName || "";
  currentTurnNameElement.title = currentTurnName || "";
  currentTurnLine.append(currentTurnPrefix, currentTurnNameElement);
  const roundStatus = document.createElement("span");
  if (isHistoricalResult && state.dice_retained) {
    roundStatus.textContent = "本轮沿用上轮骰面（投降不重摇）";
  } else if (isHistoricalResult) {
    roundStatus.textContent = state.teams ? "各队共用5骰已重摇（仅己队可见）" : "本轮骰子已按剩余数量重新掷出并隐藏（仅自己可见）";
  } else if (flow.phase === "finished") {
    roundStatus.textContent = "本轮已经结算";
  } else {
    roundStatus.textContent = state.teams ? "每队共用一盅5骰（仅己队可见）" : "本轮骰子已掷出并隐藏（仅自己可见）";
  }
  roundHeading.append(roundTitle);
  if (currentTurnName) roundHeading.append(currentTurnLine);
  roundHeading.append(roundStatus);
  currentRound.appendChild(roundHeading);

  const heading = document.createElement("div");
  heading.className = "liars-current-bid";
  const bidLabel = document.createElement("span");
  bidLabel.textContent = "本轮当前叫点";
  const bidValue = document.createElement("strong");
  const currentBid = state.current_bid;
  if (currentBid) {
    const bidder = participantByPlayerId(currentBid.bidder_player_id);
    bidValue.textContent = `${currentBid.quantity} 个 ${currentBid.face} 点${currentBid.exact ? "（斋）" : ""} · ${(bidder && bidder.display_name) || currentBid.bidder_player_id}`;
  } else if (flow.phase === "finished") {
    bidValue.textContent = "本轮已结算";
  } else if (humanCanMove) {
    bidValue.textContent = "轮到你叫点";

  } else if (room && room.status === "playing") {
    const starterName = liarsParticipantName(room.current_player_id);
    bidValue.textContent = starterName
      ? `等待 ${starterName} 首叫`
      : "等待本轮首叫";
  } else {
    bidValue.textContent = "本轮已结算";  }
  heading.append(bidLabel, bidValue);
  currentRound.appendChild(heading);

  const controls = document.createElement("div");
  controls.className = state.mode === "party" ? "liars-controls liars-party-controls" : "liars-controls";
  const quantityLabel = document.createElement("label");
  quantityLabel.textContent = "本轮数量";
  const quantity = document.createElement("select");
  quantity.ariaLabel = "本轮叫点数量";
  const faceLabel = document.createElement("label");
  faceLabel.textContent = "本轮点数";
  const face = document.createElement("select");
  face.ariaLabel = "本轮叫点点数";
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
  const selectedBid = liarsBidSelectionFor(state);
  const exactLabel = document.createElement("label");
  exactLabel.className = "liars-exact-field";
  exactLabel.textContent = "叫点规则";
  const exact = document.createElement("select");
  exact.ariaLabel = "叫点规则";
  [["false", "普通 · 1百搭"], ["true", "叫斋 · 只计骰面"]].forEach(([value, text]) => {
    const option = document.createElement("option"); option.value = value; option.textContent = text; exact.appendChild(option);
  });
  exact.value = String(Boolean(selectedBid.exact));
  exact.disabled = !humanCanMove || Boolean(currentBid?.exact) || Number(selectedBid.face) === 1;
  exactLabel.appendChild(exact);
  quantity.value = String(selectedBid.quantity);
  face.value = String(selectedBid.face);
  quantity.disabled = !canHumanMove();
  face.disabled = !canHumanMove();
  quantityLabel.appendChild(quantity);
  faceLabel.appendChild(face);
  const chooseBid = document.createElement("button");
  chooseBid.type = "button";
  chooseBid.className = "pixel-btn compact";
  chooseBid.textContent = "提交本轮叫点";
  if (!currentBid) {
    chooseBid.textContent = flow.phase === "finished"
      ? "本轮已结算"
      : (humanCanMove ? "现在叫点" : "等待首叫");
  }

  const selectionIsHigher = () => liarsBidSelectionIsLegal(state, {
    quantity: Number(quantity.value),
    face: Number(face.value),
    exact: state.mode === "party" && exact.value === "true",
  });
  const updateBidAvailability = () => {
    chooseBid.disabled = !humanCanMove || !selectionIsHigher();
  };
  const bidSelectionChanged = () => {
    if (state.mode === "party" && (Number(face.value) === 1 || currentBid?.exact)) exact.value = "true";
    exact.disabled = !humanCanMove || Boolean(currentBid?.exact) || Number(face.value) === 1;
    rememberLiarsBidSelection(quantity.value, face.value, exact.value === "true");
    updateBidAvailability();  };
  updateBidAvailability();
  quantity.addEventListener("change", bidSelectionChanged);
  face.addEventListener("change", bidSelectionChanged);
  exact.addEventListener("change", bidSelectionChanged);
  chooseBid.addEventListener("click", async () => {
    chooseBid.disabled = true;
    const submitted = await submitMove({
      action: "bid",
      quantity: Number(quantity.value),
      face: Number(face.value),
      ...(state.mode === "party" ? {exact: exact.value === "true"} : {}),
    });
    if (!submitted) updateBidAvailability();
  });
  const challenge = document.createElement("button");
  challenge.type = "button";
  challenge.className = "pixel-btn danger compact";
  challenge.textContent = "质疑本轮上一手";
  if (!currentBid) {
    challenge.textContent = flow.phase === "finished"
      ? "本轮已结算"
      : "本轮尚无叫点可质疑";
  }
  challenge.disabled = !humanCanMove || !currentBid;
  challenge.addEventListener("click", async () => {
    challenge.disabled = true;
    const submitted = await submitMove({action: "challenge"});
    if (!submitted) challenge.disabled = !canHumanMove() || !currentBid;
  });
  controls.append(quantityLabel, faceLabel);
  if (state.mode === "party") controls.appendChild(exactLabel);
  controls.append(chooseBid, challenge);
  if (state.mode === "party") {
    if (room?.private_state?.can_reroll) {
      const reroll = document.createElement("button");
      reroll.type = "button"; reroll.className = "pixel-btn secondary compact";
      reroll.textContent = "单骰重摇 · 本轮限一次"; reroll.disabled = !humanCanMove;
      reroll.addEventListener("click", async () => { reroll.disabled = true; if (!await submitMove({action:"reroll"})) reroll.disabled = !canHumanMove(); });
      controls.appendChild(reroll);
    }
    if (state.allow_surrender && currentBid) {
      const surrender = document.createElement("button");
      surrender.type = "button"; surrender.className = "pixel-btn secondary compact";
      surrender.textContent = state.surrender_no_reroll ? "投降-0.5 · 保留骰面" : "投降-0.5 · 揭盅重摇";
      surrender.disabled = !humanCanMove;
      surrender.addEventListener("click", async () => { surrender.disabled = true; if (!await submitMove({action:"surrender"})) surrender.disabled = !canHumanMove(); });
      controls.appendChild(surrender);
    }
  }
  currentRound.appendChild(controls);
  board.appendChild(currentRound);
  if (result) board.appendChild(result);
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
  if (["jungle", "xiangqi"].includes(targetRoom.game_type)) {
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
  target.ariaLabel = (
    `${target.ariaLabel || "棋盘位置"}`
    + `${room.game_type === "xiangqi" ? "，上一手终点" : "，上一手"}`
  );
}

function registeredGameUIRenderer(gameType) {
  const registry = window.DuelGameUI;
  if (!registry || typeof registry.get !== "function") return null;
  return registry.get(gameType);
}

function gameUsesStandardMoveConfirmation(
  gameType,
  renderer = registeredGameUIRenderer(gameType)
) {
  if (renderer) return renderer.usesStandardMoveConfirmation !== false;
  return gameType !== "liars_dice";
}

function syncMoveConfirmationVisibility(
  renderer = registeredGameUIRenderer(room.game_type)
) {
  $("moveConfirm").classList.toggle(
    "hidden",
    !gameUsesStandardMoveConfirmation(room.game_type, renderer)
  );
}

function registeredGameUIStateFor(targetRoom) {
  const stateKey = [
    targetRoom.game_type,
    targetRoom.room_id,
    targetRoom.revision,
    viewerPlayerIdFor(targetRoom),
  ].join(":");
  if (stateKey !== registeredGameUIStateKey) {
    registeredGameUIStateKey = stateKey;
    registeredGameUIState = Object.create(null);
  }
  return registeredGameUIState;
}

function createGameUIContext(board, controls, timeline = currentTimeline) {
  const targetRoom = room;
  const state = targetRoom.board_state || {};
  const privateState = targetRoom.private_state || null;
  const uiState = registeredGameUIStateFor(targetRoom);
  const contextIsCurrent = () => Boolean(
    room
    && room.game_type === targetRoom.game_type
    && room.room_id === targetRoom.room_id
    && room.revision === targetRoom.revision
    && viewerPlayerIdFor(room) === viewerPlayerIdFor(targetRoom)
  );
  const rerender = () => {
    if (!contextIsCurrent()) return false;
    renderBoard(currentTimeline);
    return true;
  };
  const helpers = Object.freeze({
    setBoardLayout({
      rows,
      cols,
      visualRows = rows,
      visualCols = cols,
      large = Math.max(Number(rows) || 0, Number(cols) || 0) > 3,
      ariaLabel = `${targetRoom.game_name || targetRoom.game_type}游戏区域`,
    } = {}) {
      if (Number.isFinite(Number(visualCols)) && Number(visualCols) > 0) {
        board.style.setProperty("--cols", Number(visualCols));
      }
      if (Number.isFinite(Number(visualRows)) && Number(visualRows) > 0) {
        board.style.setProperty("--rows", Number(visualRows));
      }
      if (
        Number.isFinite(Number(visualCols)) && Number(visualCols) > 0
        && Number.isFinite(Number(visualRows)) && Number(visualRows) > 0
      ) {
        board.style.setProperty(
          "--board-ratio", `${Number(visualCols)} / ${Number(visualRows)}`
        );
      }
      board.classList.toggle("large", Boolean(large));
      board.setAttribute("aria-label", ariaLabel);
    },
    selectMove: (movePayload) => {
      if (!contextIsCurrent() || !canHumanMove() || !movePayload) return false;
      selectMove(movePayload);
      return true;
    },
    clearSelection: ({render = true} = {}) => {
      if (!contextIsCurrent()) return false;
      pendingMove = null;
      Object.keys(uiState).forEach((key) => delete uiState[key]);
      if (render) rerender();
      else updateMoveConfirmation();
      return true;
    },
    submitMove: (movePayload) => (
      contextIsCurrent() ? submitMove(movePayload) : Promise.resolve(false)
    ),
    submitSideMove: (movePayload) => (
      contextIsCurrent() && room && room.status === "playing"
        ? submitMove(movePayload, {sideAction: true}) : Promise.resolve(false)
    ),
    rerender,
    isMoveSelected: (movePayload) => movesEqual(pendingMove, movePayload),
    canMove: () => contextIsCurrent() && canHumanMove(),
    participantByPlayerId,
    participantForOwner,
    renderParticipantAvatar: (target, participant) => {
      if (!target) return false;
      renderParticipantAvatar(target, participant, targetRoom);
      return true;
    },
    pieceClass,
    ownerDescription,
    announce: (message, {error = false, emphasize = false} = {}) => {
      if (contextIsCurrent()) showNotice(message, error, emphasize);
    },
  });
  const stateLegalActions = Array.isArray(state.legal_actions)
    ? state.legal_actions
    : [];
  const hasPrivateLegalActions = Boolean(
    privateState && Array.isArray(privateState.legal_actions)
  );
  const privateLegalActions = hasPrivateLegalActions
    ? privateState.legal_actions
    : [];
  return Object.freeze({
    board,
    controls,
    room: targetRoom,
    state,
    privateState,
    timeline,
    identity,
    participants: relativeParticipantsFor(targetRoom),
    viewer: targetRoom.viewer || viewerParticipantFor(targetRoom),
    canMove: canHumanMove(),
    isTerminal: isTerminal(targetRoom),
    pendingMove: pendingMove ? {...pendingMove} : null,
    legalMoves: Array.isArray(state.legal_moves) ? state.legal_moves : [],
    legalActions: hasPrivateLegalActions
      ? privateLegalActions
      : stateLegalActions,
    uiState,
    helpers,
  });
}

function renderRegisteredGameUI(renderer, board, timeline = currentTimeline) {
  const controls = $("gameControls");
  board.classList.add(room.game_type);
  board.setAttribute("aria-label", `${room.game_name || room.game_type}游戏区域`);
  const context = createGameUIContext(board, controls, timeline);
  renderer.renderBoard(context);
  const hasCustomControls = typeof renderer.renderControls === "function";
  controls.classList.toggle("hidden", !hasCustomControls);
  if (hasCustomControls) renderer.renderControls(context);
  if (room.spectating) {
    controls.querySelectorAll("button, input, select").forEach((control) => {
      control.disabled = true;
    });
  }
  syncMoveConfirmationVisibility(renderer);
  updateMoveConfirmation();
}

function renderBoard(timeline = currentTimeline) {
  const state = room.board_state;
  const board = $("board");
  const controls = $("gameControls");
  board.replaceChildren();
  board.className = "board";
  board.removeAttribute("style");
  controls.replaceChildren();
  controls.classList.add("hidden");
  const renderer = registeredGameUIRenderer(room.game_type);
  syncMoveConfirmationVisibility(renderer);
  if (renderer) {
    renderRegisteredGameUI(renderer, board, timeline);
    return;
  }
  if (room.game_type === "liars_dice") {
    board.classList.add("liars_dice");
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
  } else if (room.game_type === "xiangqi") {
    renderXiangqiBoard(board, state);
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

function recentSpeechEvents(timeline = []) {
  return timeline.filter((event) => (
    event
    && typeof event.text === "string"
    && Boolean(event.text.trim())
    && event.is_public !== false
    && speechSenderRole(event) !== "system"
    && speechSenderPlayerId(event) !== "system"
  ));
}

function timelineSpeakerName(event) {
  const sender = event && typeof event.sender === "object" && event.sender
    ? event.sender
    : {};
  const participant = participantByPlayerId(speechSenderPlayerId(event));
  return sender.name
    || event.sender_name
    || (participant && participant.display_name)
    || (speechSenderRole(event) === "human" ? "你" : "玩家");
}

function renderRecentChat(timeline = []) {
  const feed = $("recentChatFeed");
  const list = $("recentChatMessages");
  const messages = recentSpeechEvents(timeline);
  const oldScrollHeight = list.scrollHeight;
  const oldScrollTop = list.scrollTop;
  const followBottom = list.dataset.roomId !== room.room_id
    || oldScrollHeight - oldScrollTop - list.clientHeight <= 24;
  // Preserve the first visible message, including when the server's 200-event
  // window drops older entries. Appending below it must not shift reading.
  const listTop = list.getBoundingClientRect().top;
  const anchor = !followBottom && [...list.children].find(
    (item) => item.getBoundingClientRect().bottom > listTop
  );
  const anchorOffset = anchor ? anchor.getBoundingClientRect().top - listTop : 0;
  list.dataset.roomId = room.room_id;
  list.replaceChildren();
  feed.classList.remove("hidden");

  messages.forEach((event) => {
    const sender = typeof event.sender === "object" && event.sender
      ? event.sender
      : {};
    const participant = participantByPlayerId(speechSenderPlayerId(event));
    const seatIndex = participant && Number.isInteger(participant.seat_index)
      ? participant.seat_index
      : sender.seat;
    const item = document.createElement("li");
    item.className = "recent-chat-message";
    item.dataset.sequence = String(event.sequence);
    if (Number.isInteger(seatIndex)) item.classList.add(`seat-${seatIndex}`);
    const speaker = document.createElement("strong");
    speaker.className = "recent-chat-speaker";
    speaker.textContent = timelineSpeakerName(event);
    const copy = document.createElement("p");
    copy.className = "recent-chat-copy";
    copy.textContent = event.text;
    item.append(speaker, copy);
    if (!room.spectating && room.room_kind === "invite" && participant?.handle && ["human", "bound_machine"].includes(participant.participant_kind)
        && participant.player_id !== viewerPlayerIdFor(room) && speechSenderRole(event) !== "system") {
      const reply = document.createElement("button");
      reply.type = "button";
      reply.className = "recent-chat-reply";
      reply.textContent = "回复";
      reply.setAttribute("aria-label", `回复 ${timelineSpeakerName(event)}`);
      // As with mention completion, preserve input focus and the mobile keyboard.
      reply.addEventListener("pointerdown", (event) => event.preventDefault());
      reply.addEventListener("click", () => {
        const input = $("chatInput");
        const start = input.selectionStart ?? input.value.length;
        const separator = start && !/\s/u.test(input.value[start - 1]) ? " " : "";
        input.setRangeText(`${separator}@${participant.handle} `, start, start, "end");
        closeMentionOptions();
        input.focus({preventScroll: true});
      });
      item.appendChild(reply);
    }
    list.appendChild(item);
  });
  if (followBottom) {
    list.scrollTop = list.scrollHeight;
  } else {
    const nextAnchor = anchor && [...list.children].find(
      (item) => item.dataset.sequence === anchor.dataset.sequence
    );
    list.scrollTop = nextAnchor
      ? nextAnchor.getBoundingClientRect().top - list.getBoundingClientRect().top + list.scrollTop - anchorOffset
      : oldScrollTop + list.scrollHeight - oldScrollHeight;
  }
}

function renderPlayers(timeline = []) {
  const multiplayer = isMultiplayerRoom(room) || ["monopoly", "carcassonne"].includes(room.game_type)
    || (typeof registeredGameUIRenderer === "function"
      && registeredGameUIRenderer(room.game_type)?.multiplayerTable === true);
  const viewerPlayerId = viewerPlayerIdFor(room);
  const viewerParticipant = viewerParticipantFor(room);
  const viewerSpeechEvent = viewerPlayerId
    ? latestSpeechEvent(timeline, {playerId: viewerPlayerId})
    : null;
  applyParticipantLayout(room);
  $("opponentRow").classList.toggle("hidden", multiplayer);
  $("humanRow").classList.toggle("hidden", multiplayer);
  const aiName = participantName("ai");
  const humanName = (viewerParticipant && viewerParticipant.display_name)
    || participantName("human");
  $("aiName").textContent = aiName;
  $("humanName").textContent = humanName;
  for (const [id, p] of [["aiName", participantFor("ai")], ["humanName", viewerParticipant]]) {
    if (p?.handle && participantAccountId(p)) {
      const handle = document.createElement("small"); handle.className = "participant-handle";
      handle.textContent = ` ID ${participantAccountId(p)}`; $(id).appendChild(handle);
    }
  }
  renderParticipantAvatar($("aiAvatar"), participantFor("ai"), room);
  renderParticipantAvatar($("humanAvatar"), viewerParticipant, room);

  renderSpeechBubble({
    bubble: $("aiSpeech"),
    event: multiplayer ? null : latestSpeechEvent(timeline, {playerId: participantFor("ai")?.player_id}),
  });
  renderSpeechBubble({
    bubble: $("humanSpeech"),
    event: multiplayer
      ? null
      : (viewerPlayerId
        ? viewerSpeechEvent
        : latestSpeechEvent(timeline, "human")),
    textTarget: $("humanSpeechText"),
  });
  renderSpeechBubble({
    bubble: $("viewerSpeech"),
    event: null,
    textTarget: $("viewerSpeechText"),
  });
  renderSpeechBubble({
    bubble: $("sharedSpeech"),
    event: null,
    textTarget: $("sharedSpeechText"),
    nameTarget: $("sharedSpeechName"),
    avatarTarget: $("sharedSpeechAvatar"),
    reserveSpace: !multiplayer,
  });
}

function participantLayoutClass(playerCount) {
  return playerCount > 2 ? "layout-multiplayer" : "layout-duel";
}

function applyParticipantLayout(targetRoom) {
  const playerCount = actualPlayerCount(targetRoom);
  const presentation = participantPresentationFor(targetRoom);
  const layoutClass = participantLayoutClass(isFourColorGame(targetRoom.game_type) ? Math.max(3, playerCount) : playerCount);
  const table = $("tableLayout");
  table.className = `table-layout ${layoutClass} count-${playerCount}`;
  table.dataset.playerCount = String(playerCount);
  table.dataset.participantPresentation = presentation;
  $("battleStage").dataset.playerCount = String(playerCount);
  $("battleStage").dataset.participantPresentation = presentation;
  $("battleStage").dataset.gameCategory = roomGameCategory(targetRoom);
  $("battleStage").dataset.gameType = targetRoom.game_type;
  $("battleStage").classList.toggle("multiplayer-presentation", playerCount > 2);
  $("sharedSpeechSlot").classList.toggle("hidden", true);
}

function speechSenderRole(event) {
  if (!event) return "";
  return event.sender_role
    || (typeof event.sender === "object" && event.sender ? event.sender.role : event.sender)
    || "";
}

function speechSenderPlayerId(event) {
  if (!event) return "";

  if (event.sender_player_id) return event.sender_player_id;
  return typeof event.sender === "object" && event.sender
    ? (event.sender.player_id || "")
    : "";
}

function latestSpeechEvent(timeline = [], filter = null) {
  const options = typeof filter === "string" ? {role: filter} : (filter || {});
  return [...timeline].reverse().find((event) => (
    ["message", "move", "resign", "leave"].includes(event.event_type)
    && Boolean(event.text)
    && event.is_public !== false
    && (!options.role || speechSenderRole(event) === options.role)
    && (!options.playerId || speechSenderPlayerId(event) === options.playerId)
    && (
      !options.excludePlayerId
      || speechSenderPlayerId(event) !== options.excludePlayerId
    )  )) || null;
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
  const participant = visible
    ? participantByPlayerId(speechSenderPlayerId(event))
    : null;
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

function viewerPlayerIdFor(targetRoom) {
  if (!targetRoom) return "";
  return (targetRoom.viewer && targetRoom.viewer.player_id)
    || targetRoom.human_player_id
    || "";
}

function viewerParticipantFor(targetRoom) {
  const participants = Array.isArray(targetRoom.participants)
    ? targetRoom.participants
    : [];
  const viewerId = targetRoom.viewer && targetRoom.viewer.player_id;
  if (viewerId) {
    return participants.find((item) => item.player_id === viewerId) || null;
  }
  if (targetRoom.human_player_id) {
    return participants.find(
      (item) => item.player_id === targetRoom.human_player_id
    ) || null;
  }
  return participants.find((item) => item.role === "human") || null;
}

function relativeParticipantsFor(targetRoom) {
  const participants = [...(targetRoom.participants || [])].sort((a, b) => a.seat_index - b.seat_index);
  const index = participants.findIndex((p) => p.player_id === viewerPlayerIdFor(targetRoom));
  return index < 0 ? participants : [...participants.slice(index), ...participants.slice(0, index)];
}

function tableParticipantsFor(targetRoom) {
  const participants = Array.isArray(targetRoom.participants)
    ? targetRoom.participants
    : [];
  const viewer = viewerParticipantFor(targetRoom);
  if (!viewer || (participants.length <= 2 && targetRoom.game_type !== "carcassonne" && !isFourColorGame(targetRoom.game_type))) return participants;
  const ordered = [...participants].sort((a, b) => a.seat_index - b.seat_index);
  const start = ordered.findIndex((p) => p.player_id === viewer.player_id);
  return [...ordered.slice(start + 1), ...ordered.slice(0, start)].filter(
    (item) => item.player_id !== viewer.player_id
  );
}

function createParticipantBadge(participant, targetRoom) {
  const viewer = viewerParticipantFor(targetRoom);
  const isViewer = Boolean(viewer && participant.player_id === viewer.player_id);
  const isCurrent = Boolean(
    targetRoom.status === "playing"
    && participant.player_id === targetRoom.current_player_id
  );
  const badge = document.createElement("article");
  badge.className = `room-participant seat-${participant.seat_index}`;
  badge.classList.toggle("viewer", isViewer);
  if (isCurrent) {
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
  badge.setAttribute("aria-current", isCurrent ? "true" : "false");
  if (isViewer) badge.setAttribute("aria-label", `${participant.display_name || participant.player_id}，${targetRoom.spectating ? "观战视角" : "我的席位"}`);
  const avatarWrap = document.createElement("span");
  avatarWrap.className = "room-participant-avatar";
  renderParticipantAvatar(avatarWrap, participant, targetRoom);
  const copy = document.createElement("span");
  copy.className = "room-participant-copy";
  const name = document.createElement("strong");
  name.textContent = `${participant.display_name || participant.player_id}${isViewer ? "（你）" : ""}`;
  const seat = document.createElement("small");
  seat.textContent = participantAccountId(participant) ? `ID ${participantAccountId(participant)} · 座位 ${participant.seat_index + 1} · ${kind}` : `座位 ${participant.seat_index + 1} · ${kind}`;
  copy.append(name, seat);
  const detail = document.createElement("span");
  detail.className = "room-participant-detail";
  const metadataLabels = {
    score: "得分",
    dice_count: "剩余骰子",
    hand_count: "剩余手牌",
    ...(targetRoom.game_type === "liars_dice" && targetRoom.board_state?.mode === "party"
      ? {score: targetRoom.board_state.teams ? "共用队分" : "本局积分", team: "队伍", dice_count: targetRoom.board_state.teams ? "共用骰子" : "骰子"} : {}),
  };
  const metadata = participant.game_metadata && typeof participant.game_metadata === "object"
    ? participant.game_metadata
    : {};
  const fragments = Object.entries(metadata).map(
    ([key, value]) => `${metadataLabels[key] || key} ${value}`
  );
  if (isCurrent) fragments.unshift("▶ 正在行动");
  if (isViewer) fragments.unshift("你的席位");
  if (states.length) fragments.push(states.join("/"));
  if (Number.isInteger(participant.settlement_delta)) {
    const delta = participant.settlement_delta;
    fragments.push(`结算 ${delta > 0 ? "+" : ""}${delta}`);
  }
  detail.textContent = fragments.join(" · ");
  detail.classList.toggle("hidden", !fragments.length);
  badge.append(avatarWrap, copy);
  if (isFourColorGame(targetRoom.game_type)) {
    const state = targetRoom.board_state || {}, pid = participant.player_id;
    const stats = document.createElement("span");
    stats.className = "fc-seat-stats";
    const info = [];
    if (Number.isInteger(state.hand_counts?.[pid])) info.push(`手牌 ${state.hand_counts[pid]}`);
    if (Number.isInteger(state.closed_pair_counts?.[pid])) info.push(`扣置 ${state.closed_pair_counts[pid]} 对`);
    if (state.listening?.[pid]) info.push("已听牌");
    const points = state.result?.point_deltas?.[pid];
    if (Number.isInteger(points)) info.push(`${points > 0 ? "+" : ""}${points} 分`);
    stats.textContent = info.join(" · ");
    if (info.length) badge.append(stats);
  }
  badge.append(detail);
  return badge;
}

function renderParticipantRoster(targetRoom) {
  const roster = $("roomParticipants");
  const viewerSlot = $("viewerParticipant");
  const participants = Array.isArray(targetRoom.participants)
    ? targetRoom.participants
    : [];
  const presentation = participantPresentationFor(targetRoom);
  const showGenericRoster = (participants.length > 2 || targetRoom.game_type === "carcassonne" || isFourColorGame(targetRoom.game_type)) && presentation === "generic";
  const viewer = viewerParticipantFor(targetRoom);
  const tableParticipants = tableParticipantsFor(targetRoom);
  roster.replaceChildren();
  viewerSlot.replaceChildren();
  [...roster.classList]
    .filter((name) => name.startsWith("count-"))
    .forEach((name) => roster.classList.remove(name));
  roster.classList.add(`count-${participants.length}`);
  roster.classList.toggle("hidden", !showGenericRoster);
  $("viewerParticipantSlot").classList.toggle("hidden", !showGenericRoster);
  viewerSlot.classList.toggle("hidden", !showGenericRoster || !viewer);
  if (!showGenericRoster) return;
  tableParticipants.forEach((participant) => {
    roster.appendChild(createParticipantBadge(participant, targetRoom));
  });
  if (viewer) {
    viewerSlot.appendChild(createParticipantBadge(viewer, targetRoom));
  }
}

function renderPrivateState(targetRoom) {
  const panel = $("privateStatePanel");
  const content = $("privateStateContent");
  const privateState = targetRoom && targetRoom.private_state;
  const renderer = targetRoom
    ? registeredGameUIRenderer(targetRoom.game_type)
    : null;
  const rendererOwnsPresentation = Boolean(
    renderer && renderer.ownsPrivateStatePresentation === true
  );
  const visible = !rendererOwnsPresentation
    && privateState && typeof privateState === "object"
    && !Array.isArray(privateState) && Object.keys(privateState).length > 0;
  panel.classList.toggle("liars-dice-private", targetRoom?.game_type === "liars_dice");
  panel.classList.toggle(
    "compact-dice-private",
    isMultiplayerRoom(targetRoom) && targetRoom.game_type === "liars_dice"
  );
  panel.classList.toggle("hidden", !visible);
  content.replaceChildren();
  if (!visible) return;
  Object.entries(privateState).forEach(([key, value]) => {
    if (targetRoom.game_type === "liars_dice" && ["team_id", "cup_owner_player_id", "can_reroll"].includes(key)) return;
    if (targetRoom.game_type === "liars_dice" && targetRoom.board_state?.mode === "party" && key === "legal_actions") return;
    const row = document.createElement("div");
    row.className = "private-state-row";
    const label = document.createElement("strong");
    const labels = {hand: "我的手牌", dice: "我的骰子", rolls: "我的投掷", legal_actions: "我的合法行动"};
    label.textContent = key === "dice" && privateState.team_id ? "我队共用骰子" : labels[key] || key;
    const body = document.createElement("span");
    if (key === "dice" && Array.isArray(value)) {
      body.className = "my-dice liars-dice-row";
      value.forEach((die) => {
        body.appendChild(createLiarsDie(document, die));
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
  if (targetRoom.game_type === "liars_dice" && targetRoom.board_state?.mode === "party" && isTerminal(targetRoom)) {
    return targetRoom.result?.result_text || "酒桌局已结束，本局积分和回合记录已保留。";
  }
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
  $("togglePreserveButton").disabled = !terminal || targetRoom.spectating;
  $("togglePreserveButton").removeAttribute("aria-hidden");
  syncSpectatorRetentionControls();
  syncResultPreservationChoice(targetRoom, !terminal || targetRoom.spectating);
}

function renderRulesText(value) {
  const container = $("rulesText");
  const fragment = document.createDocumentFragment();
  const lines = String(value ?? "").replace(/\r\n?/g, "\n").split("\n");
  let group = null;
  let list = null;

  const currentGroup = () => {
    if (!group) {
      group = document.createElement("div");
      group.className = "rules-group";
      fragment.appendChild(group);
    }
    return group;
  };

  lines.forEach((rawLine) => {
    const line = rawLine.trim();
    if (!line) {
      group = null;
      list = null;
      return;
    }

    const heading = line.match(/^【([^【】]+)】$/);
    if (heading) {
      const title = document.createElement("h3");
      title.className = "rules-section-title";
      title.textContent = heading[1].trim();
      currentGroup().appendChild(title);
      list = null;
      return;
    }

    if (line.startsWith("- ")) {
      if (!list) {
        list = document.createElement("ul");
        list.className = "rules-list";
        currentGroup().appendChild(list);
      }
      const item = document.createElement("li");
      item.textContent = line.slice(2).trim();
      list.appendChild(item);
      return;
    }

    const paragraph = document.createElement("p");
    paragraph.textContent = line;
    currentGroup().appendChild(paragraph);
    list = null;
  });

  container.replaceChildren(fragment);
}

function roomActionNotice(targetRoom, message, humanCanMove) {
  // During play, the header already shows room/turn status. Keep this space
  // for explicit feedback from showNotice, not routine render/poll messages.
  if (targetRoom.status === "playing") return "";
  const renderer = registeredGameUIRenderer(targetRoom.game_type);
  if (renderer && renderer.usesEmbeddedActionFeedback === true) return "";
  if (isTerminal(targetRoom) || targetRoom.spectating) return roomTurnText(targetRoom);
  const humanTurnNotice = ["zhajinhua", "monopoly"].includes(targetRoom.game_type)
    ? "现在轮到你行动"
    : "现在轮到你落子";
  if (
    humanCanMove
    && ["zhajinhua", "monopoly"].includes(targetRoom.game_type)
    && message === "现在轮到你落子"
  ) {
    return humanTurnNotice;
  }
  return message || (humanCanMove ? humanTurnNotice : "");
}

function renderSpectatorPerspective() {
  if (room.spectating) {
    setRoomListScope("machines");
    spectatorPerspectives.set(room.room_id, viewerPlayerIdFor(room));
  }
  $("gameView").classList.toggle("spectating-view", Boolean(room.spectating));
  $("spectatorPerspective").classList.toggle("hidden", !room.spectating);
  const choices = room.spectator_choices || [];
  const select = $("spectatorSelect");
  const signature = JSON.stringify(choices);
  if (select.dataset.choices !== signature) {
    select.replaceChildren(...choices.map((choice) => {
      const option = document.createElement("option");
      option.value = choice.id;
      option.textContent = choice.name;
      return option;
    }));
    select.dataset.choices = signature;
  }
  select.value = viewerPlayerIdFor(room);
  select.title = choices.find((choice) => choice.id === select.value)?.name || "";
  select.classList.toggle("hidden", choices.length < 2);
  $("spectatorName").classList.toggle("hidden", choices.length > 1);
  $("spectatorName").textContent = choices[0]?.name || "";
  $("spectatorName").title = $("spectatorName").textContent;
  $("spectatorName").setAttribute("aria-label", `${$("spectatorName").textContent}，查看完整姓名`);
}

function renderGame(nextRoom, message = "", timeline = []) {
  if (!room || room.room_id !== nextRoom.room_id || viewerPlayerIdFor(room) !== viewerPlayerIdFor(nextRoom)) {
    delete $("recentChatMessages").dataset.roomId;
    $("chatInput").value = "";
    $("mentionOptions").replaceChildren();
    closeMentionOptions();
  }
  const renderer = registeredGameUIRenderer(nextRoom.game_type);
  const becameTerminal = Boolean(
    isTerminal(nextRoom) && (
      (room && room.room_id === nextRoom.room_id && !isTerminal(room))
      || (nextRoom.game_type === "liars_dice" && nextRoom.board_state?.mode === "party"
        && (!room || room.room_id !== nextRoom.room_id))
    )
  );
  const boardStateChanged = (
    !room
    || room.room_id !== nextRoom.room_id
    || room.revision !== nextRoom.revision
    || room.status !== nextRoom.status
    || viewerPlayerIdFor(room) !== viewerPlayerIdFor(nextRoom)
    || Boolean(room.spectating) !== Boolean(nextRoom.spectating)
  );
  if (boardStateChanged) {
    selectedJungleCell = null;
    selectedXiangqiCell = null;
    pendingMove = null;
  }
  room = nextRoom;
  renderSpectatorPerspective();
  syncChatRoomMode();
  currentTimeline = timeline;
  const humanCanMove = canHumanMove();
  showView("gameView");
  $("gameView").classList.toggle("invite-waiting-view", isInviteWaiting(room));
  renderInviteRoomPanel();
  $("reclaimControl").classList.toggle("hidden", !(room.room_kind === "invite"
    && room.status === "playing" && room.viewer?.temporary_takeover_active === true
    && room.current_player_id === viewerPlayerIdFor(room)));
  syncMoveConfirmationVisibility();
  $("gameBadge").textContent = room.game_type.startsWith("four_color_") ? "四色牌" : room.game_type.toUpperCase();
  $("gameTitle").textContent = room.game_name;
  $("roomId").textContent = room.room_id;
  $("copyRoomButton").setAttribute("aria-label", `复制房间号 ${room.room_id}`);
  $("copyRoomButton").title = `复制房间号 ${room.room_id}`;
  const resultText = resultTextFor(room, timeline);
  const winner = room.game_type === "liars_dice" && room.board_state.mode === "party" ? "" : room.winner === "draw"
    ? " · 和棋"
    : (room.winner_player_id
      ? ` · ${(participantByPlayerId(room.winner_player_id) || {}).display_name || room.winner_player_id} 胜`
      : (room.winner ? ` · ${room.winner === "human" ? "你" : aiNameFor()} 胜` : ""));
  $("status").textContent = `${statusLabel(room.status)}${winner}`;
  $("turn").textContent = roomTurnText(room);
  $("turn").title = $("turn").textContent;
  if (room.spectating) {
    $("turn").setAttribute("role", "button");
    $("turn").tabIndex = 0;
    $("turn").setAttribute("aria-label", `${$("turn").textContent}，查看完整姓名`);
  } else {
    for (const attribute of ["role", "tabindex", "aria-label"]) $("turn").removeAttribute(attribute);
  }
  $("turn").classList.toggle("my-turn", humanCanMove);
  const roomStakeLabel = room.stake_label
    || (room.stake > 0 ? `🪙${room.stake}/人` : "娱乐局");
  $("roomStake").textContent = room.stake_hint
    ? `${roomStakeLabel}（${room.stake_hint}）`
    : roomStakeLabel;
  const roundText = authoritativeRoundText(room);
  $("roundText").textContent = roundText;
  $("roundMeta").classList.toggle("hidden", !roundText);
  $("roundSeparator").classList.toggle("hidden", !roundText);
  $("rulesTitle").textContent = `${room.game_name}规则`;
  renderRulesText(room.rules_text);
  if (isInviteWaiting(room)) {
    closeHistory();
    closeRules();
    closeResultModal();
    hideWaitModeModal();
    closeMentionOptions();
    $("board").replaceChildren();
    if (message) showNotice(message);
    return;
  }
  $("resignButton").disabled = room.status !== "playing";
  $("resignButton").textContent = room.game_type === "liars_dice" && room.board_state.mode === "party" ? "结束酒桌局" : "认输";
  $("sendMessageButton").disabled = Boolean(room.spectating);
  $("resultBanner").classList.toggle("hidden", !isTerminal(room));
  const settlementEntries = isTerminal(room) && Array.isArray(room.participants)
    ? room.participants.filter(
      (participant) => Number.isInteger(participant.settlement_delta)
    )
    : [];
  const settlementText = settlementEntries.length
    ? `筹码结算 ${settlementEntries.map((participant) => {
      const delta = participant.settlement_delta;
      return `${participant.display_name || participant.player_id} ${delta > 0 ? "+" : ""}${delta}`;
    }).join(" / ")}`
    : "";
  $("resultBannerText").textContent = settlementText
    ? `${resultText} · ${settlementText}`
    : resultText;
  renderRetention(room);
  if (!room.spectating && room.room_kind !== "invite") showWaitModeModalOnce(room);
  $("gameMessage").classList.toggle(
    "embedded-action-feedback",
    Boolean(renderer && renderer.usesEmbeddedActionFeedback === true)
  );
  showNotice(
    roomActionNotice(room, message, humanCanMove),
    false,
    humanCanMove && !isTerminal(room)
  );
  renderPlayers(timeline);
  renderParticipantRoster(room);
  renderPrivateState(room);
  renderRecentChat(timeline);
  if (boardStateChanged) renderBoard(timeline);
  renderTimeline(timeline);
  if (isTerminal(room) && !room.spectating) stopPolling();
  if (becameTerminal && !room.spectating) openResultModal(resultText);
}

async function showRoomTransitionFeedback(
  previousRoom, previousTimeline, nextRoom, nextTimeline
) {
  if (
    !previousRoom
    || !nextRoom
    || previousRoom.spectating
    || nextRoom.spectating
    || previousRoom.room_id !== nextRoom.room_id
    || previousRoom.revision === nextRoom.revision
  ) return;
  const renderer = registeredGameUIRenderer(nextRoom.game_type);
  if (!renderer || typeof renderer.transitionFeedback !== "function") return;
  await renderer.transitionFeedback({
    document,
    previousRoom,
    previousTimeline,
    nextRoom,
    nextTimeline,
  });
}

function roomSyncIsCurrent(generation, roomId) {
  return Boolean(
    generation === roomSyncGeneration
    && room
    && room.room_id === roomId
  );
}

async function refreshRoom({quiet = false, wait = false, generation = null} = {}) {
  if (!roomIsVisible() || roomRequestController) return {stale: true};
  if (roomRefreshDelay() > 0) {
    startRoomPolling();
    return {stale: false, retryAfterMs: roomRefreshDelay()};
  }
  const ownsGeneration = generation === null;
  if (ownsGeneration) {
    if (pollTimer !== null) clearTimeout(pollTimer);
    pollTimer = null;
    generation = roomSyncGeneration;
  }
  if (!roomSyncIsCurrent(generation, room.room_id)) return {stale: true};
  const previousRoom = room;
  const previousTimeline = currentTimeline;
  const targetRoomId = room.room_id;
  const previousRevision = room.revision;
  const previousStatus = room.status;
  const controller = new AbortController();
  roomRequestController = controller;
  try {
    const query = previousRoom.spectating
      ? `?perspective=${encodeURIComponent(viewerPlayerIdFor(previousRoom))}`
      : wait ? `?wait=true&after_revision=${encodeURIComponent(previousRevision)}` : "";
    const data = await request(`/api/rooms/${targetRoomId}${query}`, {
      signal: controller.signal,
    });
    if (!roomSyncIsCurrent(generation, targetRoomId)) return {stale: true};
    if (data.room.revision < room.revision) return {stale: false, retryAfterMs: ROOM_POLL_RETRY_MS};
    const visibleStateChanged = (
      data.room.revision !== previousRevision || data.room.status !== previousStatus
    );
    await showRoomTransitionFeedback(
      previousRoom, previousTimeline, data.room, data.timeline
    );
    if (!roomSyncIsCurrent(generation, targetRoomId)) return {stale: true};
    if (data.room.revision < room.revision) return {stale: false, retryAfterMs: ROOM_POLL_RETRY_MS};
    roomRetryAt = 0;
    roomRefreshFailures = 0;
    if (Date.now() >= readRetryAt) rateLimitFailures = 0;
    renderGame(data.room, quiet ? "" : data.message, data.timeline);
    if (!data.room.spectating && (!quiet || visibleStateChanged)) {
      await ackHumanNotifications("game", data.room.room_id);
    }
    if (["finished", "archived"].includes(room.status) && !room.spectating) stopPolling();
    return {
      stale: false,
      retryAfterMs: data.room.spectating ? ROOM_POLL_RETRY_MS : data.wait_downgraded ? ROOM_POLL_RETRY_MS : 0,
    };
  } catch (error) {
    if (error.name === "AbortError" || !roomSyncIsCurrent(generation, targetRoomId)) {
      return {stale: true};
    }
    if ([401, 403].includes(error.status) || (previousRoom.spectating && error.status === 404)) {
      await backToLobby({fromHistory: true});
      writeRoomRoute(null, {mode: "replace"});
      toast(error.message);
      return {stale: true};
    }
    if (!quiet) showNotice(error.message, true);
    return {stale: false, retryAfterMs: deferRoomRetry(error)};
  } finally {
    if (roomRequestController === controller) roomRequestController = null;
    if (ownsGeneration && roomSyncIsCurrent(generation, targetRoomId)) {
      startRoomPolling();
    }
  }
}

async function submitMove(movePayload, {sideAction = false} = {}) {
  // Side actions (e.g. spectator bets) are validated by the plugin/server;
  // they never require or change the turn holder.
  if (room?.spectating || !movePayload || (!sideAction && !canHumanMove())) return false;
  const targetRoomId = room.room_id;
  stopPolling();
  const generation = roomSyncGeneration;
  const previousRoom = room;
  const previousTimeline = currentTimeline;
  try {
    const data = await request(`/api/rooms/${targetRoomId}/move`, {
      method: "POST",
      body: JSON.stringify({move: movePayload, revision: room.revision}),
    });
    if (!roomSyncIsCurrent(generation, targetRoomId)) return false;
    if (["bid", "challenge"].includes(movePayload.action)) liarsBidDraft = null;
    pendingMove = null;
    selectedJungleCell = null;
    selectedXiangqiCell = null;
    await showRoomTransitionFeedback(
      previousRoom, previousTimeline, data.room, data.timeline
    );
    if (!roomSyncIsCurrent(generation, targetRoomId)) return false;
    renderGame(data.room, data.message, data.timeline);
    return true;
  } catch (error) {
    if (roomSyncIsCurrent(generation, targetRoomId)) {
      showNotice(error.message, true);
      updateMoveConfirmation();
    }
    return false;
  } finally {
    if (roomSyncIsCurrent(generation, targetRoomId)) startRoomPolling();
  }
}

async function acknowledgeLiarsRound(button) {
  if (
    !room
    || room.game_type !== "liars_dice"
    || !room.board_state
    || !room.board_state.flow
    || room.board_state.flow.phase !== "awaiting_round_acknowledgement"
    || !canAcknowledgeLiarsRound()
  ) return false;
  try {
    const data = await request(`/api/rooms/${room.room_id}/move`, {
      method: "POST",
      body: JSON.stringify({
        move: {action: "acknowledge_round"},
        revision: room.revision,
      }),
    });
    renderGame(data.room, data.message, data.timeline);
    startRoomPolling();
    return true;
  } catch (error) {
    showNotice(error.message, true);
    if (button) button.disabled = false;
    return false;
  }
}

async function confirmMove() {
  if (!pendingMove || !canHumanMove()) return;
  $("confirmMoveButton").disabled = true;
  await submitMove({...pendingMove});
}

async function sendMessage() {
  if (!room || room.spectating) return;
  const message = $("chatInput").value.trim();
  if (!message) {
    showNotice("请先输入留言内容。", true);
    return;
  }
  const targetRoomId = room.room_id;
  stopPolling();
  const generation = roomSyncGeneration;
  try {
    const data = await request(`/api/rooms/${targetRoomId}/messages`, {
      method: "POST",
      body: JSON.stringify({message}),
    });
    if (!roomSyncIsCurrent(generation, targetRoomId)) return;
    // Capture at response time so scrolling while the request is in flight
    // (including keyboard dismissal) is not undone by the UI update.
    const {scrollX, scrollY} = window;
    $("chatInput").value = "";
    closeMentionOptions();
    if (
      data.room.room_id === room.room_id
      && data.room.revision === room.revision
      && data.room.status === room.status
      && !isInviteWaiting(room)
    ) {
      // Chat does not advance the room revision. Keep the board, controls and
      // composer mounted, and reuse the chat feed's own scroll-follow policy.
      room = data.room;
      currentTimeline = data.timeline || [];
      renderPlayers(currentTimeline);
      renderRecentChat(currentTimeline);
      renderTimeline(currentTimeline);
      const humanCanMove = canHumanMove();
      showNotice(
        roomActionNotice(room, data.message, humanCanMove),
        false,
        humanCanMove && !isTerminal(room)
      );
    } else {
      // A concurrent move or room transition still needs the full update.
      renderGame(data.room, data.message, data.timeline);
    }
    // Restore only the page, without moving focus or reopening the keyboard.
    window.scrollTo({left: scrollX, top: scrollY, behavior: "instant"});
  } catch (error) {
    if (roomSyncIsCurrent(generation, targetRoomId)) {
      showNotice(error.message, true);
    }
  } finally {
    if (roomSyncIsCurrent(generation, targetRoomId)) startRoomPolling();
  }
}

async function resign() {
  if (room?.game_type === "liars_dice" && room.board_state.mode === "party") {
    if (!room.spectating && window.confirm("结束整桌酒桌局？已完成的积分和回合记录会保留，本轮未开骰不扣分。" + (room.stake > 0 ? "将按当前分数结算平台筹码。" : ""))) {
      await submitMove({action: "end_session"}, {sideAction: true});
    }
    return;
  }
  if (!room || room.spectating || !window.confirm("确认认输并结束本局？")) return;
  const targetRoomId = room.room_id;
  stopPolling();
  const generation = roomSyncGeneration;
  try {
    const data = await request(`/api/rooms/${targetRoomId}/resign`, {
      method: "POST",
      body: "{}",
    });
    if (!roomSyncIsCurrent(generation, targetRoomId)) return;
    renderGame(data.room, data.message, data.timeline);
    stopPolling();
  } catch (error) {
    if (roomSyncIsCurrent(generation, targetRoomId)) {
      showNotice(error.message, true);
      startRoomPolling();
    }
  }
}

function openRules() {
  $("rulesScrim").classList.add("show");
  $("rulesScrim").setAttribute("aria-hidden", "false");
  $("rulesButton").setAttribute("aria-expanded", "true");
  $("rulesText").scrollTop = 0;
  $("closeRulesButton").focus();
}

function closeRules() {
  const wasOpen = $("rulesScrim").classList.contains("show");
  $("rulesScrim").classList.remove("show");
  $("rulesScrim").setAttribute("aria-hidden", "true");
  $("rulesButton").setAttribute("aria-expanded", "false");
  if (wasOpen) $("rulesButton").focus();
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
  const party = room?.game_type === "liars_dice" && room.board_state?.mode === "party";
  $("resultModal").classList.toggle("liars-result-active", party);
  $("partyResultRanking").replaceChildren();
  $("partyResultRanking").classList.toggle("hidden", !party);
  if (party) window.DuelDiceResults.render($("partyResultRanking"), room, renderParticipantAvatar);
  $("resultModalMessage").textContent = "";
  renderRetention(room);
  $("resultModal").classList.remove("hidden");
  $("rematchButton").disabled = room?.room_kind === "invite";
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
  if (!room || room.spectating || !isTerminal(room)) return;
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
        rematch_of_room_id: previousRoom.room_id,
        game_options: previousRoom.game_options,
      }),
    });
    closeResultModal();
    $("stake").value = String(previousRoom.stake || 0);
    if (data.room.status === "pending") {
      room = null;
      stopPolling();
      writeRoomRoute(null, {mode: "replace"});
      showView("lobbyView");
      await loadIdentity({quiet: true});
      showNotice(data.message);
      return;
    }
    renderGame(data.room, data.message, data.timeline);
    writeRoomRoute(data.room.room_id, {
      mode: "replace",
      backToLobby: Boolean(window.history.state && window.history.state.duelBackToLobby),
    });
    startRoomPolling();
  } catch (error) {
    $("resultModalMessage").textContent = error.message;
    $("rematchButton").disabled = false;
  }
}

function roomIsVisible() {
  return Boolean(!document.hidden && !roomRefreshSuspended && !roomLeaving && !roomOpenIntent
    && roomWriteRequests === 0
    && room && !$('gameView').classList.contains("hidden"));
}

function shouldPollRoom() {
  return roomIsVisible() && (room.spectating || !isTerminal(room));
}

function shouldLongPollRoom() {
  return shouldPollRoom() && (room.spectating || !canHumanMove());
}

function scheduleRoomPoll(generation, roomId, delayMs = 0) {
  if (!roomSyncIsCurrent(generation, roomId) || !shouldPollRoom()
      || pollTimer !== null || roomRequestController) return;
  pollTimer = setTimeout(() => {
    pollTimer = null;
    void pollRoom(generation, roomId);
  }, Math.min(2147483647, Math.max(delayMs, roomRefreshDelay())));
}

async function pollRoom(generation, roomId) {
  if (!roomSyncIsCurrent(generation, roomId) || !shouldPollRoom() || roomRequestController) return;
  if (roomRefreshDelay() > 0) {
    scheduleRoomPoll(generation, roomId);
    return;
  }
  const outcome = await refreshRoom({quiet: true, wait: shouldLongPollRoom(), generation});
  if (!roomSyncIsCurrent(generation, roomId) || !shouldPollRoom() || outcome?.stale) return;
  scheduleRoomPoll(generation, roomId,
    Math.max(outcome?.retryAfterMs || 0, shouldLongPollRoom() ? 0 : ROOM_POLL_RETRY_MS));
}

function startRoomPolling({immediate = false} = {}) {
  if (!shouldPollRoom()) return;
  scheduleRoomPoll(roomSyncGeneration, room.room_id,
    immediate || shouldLongPollRoom() ? 0 : ROOM_POLL_RETRY_MS);
}

function stopPolling({keepOpen = false} = {}) {
  roomSyncGeneration += 1;
  if (!keepOpen) roomOpenIntent = null;
  if (pollTimer !== null) clearTimeout(pollTimer);
  pollTimer = null;
  if (roomRequestController) roomRequestController.abort();
  roomRequestController = null;
}

function suspendRoomRefresh() {
  roomRefreshSuspended = true;
  stopPolling({keepOpen: true});
}

function resumeRoomRefresh() {
  if (document.hidden) return;
  roomRefreshSuspended = false;
  if (roomOpenIntent) {
    if (!roomRequestController) scheduleRoomOpen(roomOpenIntent);
  } else {
    startRoomPolling({immediate: true});
  }
  void refreshHumanUnreadState({autoPopup: true}).then(() => {
    if (identity && !document.hidden && !roomRefreshSuspended
        && deferredUnreadAcks.size && !room && !roomOpenIntent) void loadIdentity({quiet: true});
  });
}

let inviteMode = "create";
let previewedInviteCode = null;
function showInviteStakePreview(code, invitation) {
  const i = invitation;
  const seconds = Number(i.timeout_takeover_seconds || (i.timeout_takeover ? 90 : 0));
  $("invitePreview").textContent = `${i.game_name} · ${i.participant_count}/${i.target_player_count} · 本局筹码 ${i.stake}/人${seconds ? ` · ${seconds} 秒超时接管` : ""}`;
  previewedInviteCode = code;
  $("inviteSubmit").textContent = i.stake > 0 ? `同意押注 ${i.stake} 筹码并加入` : "确认加入";
}

let inviteMachineIds = new Set();
function setInviteMachinePickerOpen(open) {
  open = open && !$("inviteAiTrigger").disabled;
  $("inviteAiTrigger").setAttribute("aria-expanded", String(open));
  $("inviteAiMenu").classList.toggle("hidden", !open);
  $("inviteAiField").classList.toggle("open", open);
}
function renderInviteMachinePicker(focusPlayerId = null) {
  const machines = identity?.machines || [];
  const maximum = Math.max(0, Number($("inviteCount").value || 2) - 2);
  // Keep this selection separate from the ordinary create-room picker.
  inviteMachineIds = new Set([...inviteMachineIds]
    .filter((id) => machines.some((machine) => machine.id === id)).slice(0, maximum));
  const selected = machines.filter((machine) => inviteMachineIds.has(machine.id));
  const summary = selected.length ? machinePickerSummary(selected) : "可选，不带小机";
  $("inviteAiSummary").textContent = summary;
  $("inviteAiTrigger").title = summary;
  $("inviteAiTrigger").disabled = maximum === 0 || machines.length === 0;
  $("inviteAiHint").textContent = "至少留 1 个邀请席位。";
  if ($("inviteAiTrigger").disabled) setInviteMachinePickerOpen(false);
  const menu = $("inviteAiMenu");
  menu.replaceChildren();
  for (const machine of machines) {
    const checked = inviteMachineIds.has(machine.id);
    const option = document.createElement("button");
    option.type = "button";
    option.className = "ai-multi-option";
    option.dataset.playerId = machine.id;
    option.setAttribute("role", "option");
    option.setAttribute("aria-selected", String(checked));
    option.disabled = !checked && selected.length >= maximum;
    const check = document.createElement("span");
    check.className = "ai-multi-check";
    check.textContent = checked ? "✓" : "";
    check.setAttribute("aria-hidden", "true");
    const name = document.createElement("span");
    name.className = "ai-multi-name";
    name.textContent = machine.name;
    option.append(check, name);
    option.addEventListener("click", () => {
      if (inviteMachineIds.has(machine.id)) inviteMachineIds.delete(machine.id);
      else if (inviteMachineIds.size < maximum) inviteMachineIds.add(machine.id);
      renderInviteMachinePicker(machine.id);
    });
    menu.appendChild(option);
    if (focusPlayerId === machine.id) option.focus();
  }
}
function updateInviteStakeAvailability() {
  const game = (identity?.games || []).find((g) => g.game_type === selectedGameType($("inviteGame")));
  const target = Number($("inviteCount").value || 2);
  const supportsStake = Boolean(game?.supports_stakes);
  const supportsTable = target === 2 || Boolean(game?.supports_multiplayer_stakes);
  const enabled = supportsStake && supportsTable;
  $("inviteStake").disabled = !enabled;
  if (!enabled) $("inviteStake").value = "0";
  $("inviteStakeHint").textContent = enabled
    ? "0=娱乐局；非 0 按游戏规则结算。"
    : "当前桌型仅支持 0 筹码娱乐局。";
}
function inviteCounts() {
  renderGameRulePicker($("inviteGame"));
  const game = (identity?.games || []).find((g) => g.game_type === selectedGameType($("inviteGame")));
  $("inviteCount").replaceChildren(...(selectedGameOptions($("inviteGame"))?.team_mode === "teams" ? [4, 6] : allowedPlayerCountsForGame(game)).map((n) => new Option(String(n), String(n))));
  $("inviteTimeout").disabled = false;
  $("inviteTimeoutHint").textContent = "超时由 NPC 临时代操作，可随时接回。";
  updateInviteStakeAvailability();
  renderInviteMachinePicker();
}
function syncInviteGameOptions({preserveSelection = true} = {}) {
  const games = identity && Array.isArray(identity.games) ? identity.games : [];
  const select = $("inviteGame");
  const previousValue = select.value;
  const category = $("inviteCategory").value;
  const categorizedGames = sortedGamesForCategory(games, category);
  select.replaceChildren();
  categorizedGames.forEach((game) => {
    const option = document.createElement("option");
    option.value = game.game_type;
    option.textContent = gameOptionLabel(game, games);
    select.appendChild(option);
  });
  if (!categorizedGames.length) {
    const option = document.createElement("option");
    option.value = "";
    option.textContent = `${gameCategoryLabel(category)}类暂无游戏`;
    option.disabled = true;
    option.selected = true;
    select.appendChild(option);
    select.disabled = true;
  } else {
    select.disabled = false;
    const values = [...select.options].map((option) => option.value);
    select.value = preserveSelection && values.includes(previousValue) ? previousValue : values[0];
  }
  inviteCounts();
}
function inviteCategoryChanged() {
  syncInviteGameOptions();
}
async function showInviteDialog(mode, code = "") {
  inviteMode = mode;
  previewedInviteCode = null;
  inviteMachineIds.clear();
  setInviteMachinePickerOpen(false);
  $("inviteError").textContent = "";
  $("inviteDialogTitle").textContent = mode === "create" ? "邀请联机" : "加入联机房间";
  if (mode === "create") $("inviteTimeout").value = "0";
  $("inviteSubmit").textContent = mode === "create" ? "创建邀请房" : "确认加入";
  $("inviteCreateFields").classList.toggle("hidden", mode !== "create");
  $("inviteJoinFields").classList.toggle("hidden", mode !== "join");
  syncInviteGameOptions({preserveSelection: false});
  $("inviteCodeInput").value = code;
  $("invitePreview").textContent = "";
  $("inviteDialog").showModal();
  if (code) {
    const normalized = code.trim().toUpperCase();
    $("inviteSubmit").disabled = true;
    try {
      const data = await request(`/api/invites/${encodeURIComponent(normalized)}`);
      if (inviteMode === "join" && $("inviteCodeInput").value.trim().toUpperCase() === normalized) {
        showInviteStakePreview(normalized, data.invitation);
      }
    } catch (error) { $("inviteError").textContent = error.message; }
    finally { $("inviteSubmit").disabled = false; }
  }
}
function isInviteWaiting(targetRoom) {
  return targetRoom?.status === "waiting" && (targetRoom.room_kind === "invite" || Boolean(targetRoom.team_selection));
}

function confirmWaitingRoomDissolve(roomKind) {
  const dialog = $("dissolveRoomDialog");
  if (dialog.open) return Promise.resolve(false);
  const invite = roomKind === "invite";
  $("dissolveRoomTitle").textContent = invite ? "确定解散邀请房间？" : "确定解散组队房间？";
  $("dissolveRoomDescription").textContent = "解散后，全部成员将离开房间，无法重新进入。"
    + (invite ? "邀请码也将失效。" : "");
  dialog.returnValue = "";
  return new Promise((resolve) => {
    dialog.onclose = () => resolve(dialog.returnValue === "confirm");
    dialog.oncancel = (event) => {
      event.preventDefault();
      dialog.close("cancel");
    };
    $("dissolveRoomCancel").onclick = () => dialog.close("cancel");
    $("dissolveRoomConfirm").onclick = () => dialog.close("confirm");
    dialog.showModal();
    $("dissolveRoomCancel").focus();
  });
}

function participantAccountId(participant) {
  if (!participant || participant.participant_kind === "system_npc") return "";
  // Display account IDs independently of the canonical mention handle.
  const id = String(participant.player_id || "");
  return id.startsWith("human:") ? id.slice(6) : id.replace(/:[2-5]$/, "");
}

let diceSetupRefreshTimer = null;
async function chooseDiceTeamSeat(team, seat) {
  try {
    const data = await request(`/api/rooms/${room.room_id}/move`, {method: "POST", body: JSON.stringify({move: {action: "choose_team_seat", team, seat}, revision: room.revision})});
    renderGame(data.room, data.message, data.timeline);
  } catch (error) { showNotice(error.message, true); }
}
function renderInviteRoomPanel() {
  const panel = $("inviteRoomPanel");
  const signature = JSON.stringify([room?.room_id, room?.revision, room?.status, room?.team_selection?.can_start, room?.spectating, viewerPlayerIdFor(room), identity?.npc_provider?.available]);
  if (room?.team_selection && panel.dataset.diceSignature === signature) return;
  panel.dataset.diceSignature = signature;
  clearTimeout(diceSetupRefreshTimer);
  panel.replaceChildren();
  const waiting = isInviteWaiting(room);
  panel.classList.toggle("hidden", !waiting);
  panel.classList.toggle("liars-team-waiting", waiting && room.game_type === "liars_dice" && Boolean(room.team_selection));
  if (!waiting) return;
  $("gameView").prepend(panel);

  const addButton = (text, action, parent = panel, primary = false) => {
    const b = document.createElement("button");
    b.type = "button";
    b.className = `pixel-btn ${primary ? "" : "secondary"} compact invite-room-action`.trim();
    b.textContent = text;
    b.dataset.action = text;
    b.title = text;
    b.addEventListener("click", async () => {
      b.disabled = true;
      try { await action(); } catch (e) { showNotice(e.message, true); }
      finally { b.disabled = false; }
    });
    parent.appendChild(b);
    return b;
  };

  const label = document.createElement("h2");
  label.className = "invite-room-title";
  label.textContent = room.room_kind === "invite" ? "邀请房" : "开局前组队";
  panel.appendChild(label);
  addButton("返回大厅", () => backToLobby());
  if (waiting) {
    const meta = document.createElement("p");
    meta.className = "invite-room-meta";
    const timeoutSeconds = Number(room.timeout_takeover_seconds || (room.timeout_takeover ? 90 : 0));
    meta.textContent = `${room.game_name} · ${room.participants.length}/${room.target_player_count} 人 · ${room.stake > 0 ? `🪙${room.stake}/人` : "娱乐局"} · ${timeoutSeconds ? `${timeoutSeconds} 秒超时接管` : "不自动接管"}`;
    panel.appendChild(meta);
  }
  const members = document.createElement("section");
  members.className = "invite-room-members";
  const memberLabel = document.createElement("h3");
  memberLabel.textContent = "已加入";
  members.appendChild(memberLabel);
  for (const p of room.participants) {
    const tag = document.createElement("span");
    tag.className = "invite-member-tag";
    const id = participantAccountId(p);
    tag.textContent = `${p.display_name}${id ? ` · ID ${id}` : ""}`;
    members.appendChild(tag);
  }
  panel.appendChild(members);
  if (room.spectating) return;
  if (waiting) {
    const invitation = document.createElement("section");
    invitation.className = "invite-code-section";
    const codeLabel = document.createElement("h3");
    codeLabel.textContent = "邀请码";
    const code = document.createElement("strong");
    code.className = "invite-code-line";
    code.textContent = room.invite_code;
    const copies = document.createElement("div");
    copies.className = "invite-copy-actions";
    invitation.append(codeLabel, code, copies);
    if (room.room_kind === "invite") panel.appendChild(invitation);
    addButton("复制邀请码", async () => {
      await navigator.clipboard.writeText(room.invite_code);
      showNotice("邀请码已复制");
    }, copies);
    addButton("复制邀请链接", async () => {
      await navigator.clipboard.writeText(new URL(room.invite_link, location.origin).href);
      showNotice("邀请链接已复制");
    }, copies);
    if (room.team_selection) {
      window.DuelDiceTeams.renderSelection(panel, room, {viewer: viewerPlayerIdFor(room), choose: chooseDiceTeamSeat, avatar: renderParticipantAvatar, accountId: participantAccountId});
      const remaining = Date.parse(room.team_selection.start_after) - Date.now();
      if (remaining > 0) diceSetupRefreshTimer = setTimeout(() => { if (isInviteWaiting(room)) refreshRoom({quiet:true}); }, Math.min(remaining + 100, 21000));
    }
    const owner = viewerPlayerIdFor(room) === room.initiator_player_id;
    const hasInvitee = room.room_kind !== "invite" || room.participants.filter((p) => p.participant_kind !== "system_npc" && (!p.join_status || p.join_status === "joined")).length > (room.household_count ?? 1);
    const actions = document.createElement("div");
    actions.className = "invite-start-actions";
    panel.appendChild(actions);
    const start = async (fillWithNpcs) => {
      const data = await request(`/api/rooms/${room.room_id}/start`, {method: "POST", body: JSON.stringify({fill_with_npcs: fillWithNpcs})});
      renderGame(data.room, data.message, data.timeline);
      startRoomPolling();
    };
    const selectionReady = !room.team_selection || room.team_selection.can_start;
    const teamSetup = room.game_type === "liars_dice" && Boolean(room.team_selection);
    const game = identity?.games?.find((g) => g.game_type === room.game_type);
    const canFill = game?.supports_npcs && (game.uses_local_npc_strategy || identity?.npc_provider?.available);
    const missing = room.target_player_count - room.participants.length;
    const npcCountAfterFill = missing + room.participants.filter(p => p.participant_kind === "system_npc").length;
    const ready = owner && hasInvitee && selectionReady
      && (room.room_ready || (teamSetup && missing > 0 && npcCountAfterFill <= 4 && canFill));
    const startButton = addButton("开始游戏", () => start(teamSetup && missing > 0), actions, true);
    startButton.disabled = !ready;
    const blockedReason = !owner ? "等待房主开始游戏"
      : !hasInvitee ? "至少需要1名外部受邀玩家加入"
      : !selectionReady ? "组队开始约20秒后可开局"
      : teamSetup && missing > 0 && npcCountAfterFill > 4 ? "每桌最多4名NPC，请等待更多玩家加入"
      : teamSetup && missing > 0 && !canFill ? (game?.supports_npcs ? "NPC补位当前不可用，请等待玩家坐满" : "该游戏不支持NPC补位，请等待玩家坐满")
      : "等待玩家坐满后开始";
    if (!ready) startButton.title = blockedReason;
    if (!owner || (teamSetup && !ready)) {
      const hint = document.createElement("span");
      hint.id = "inviteStartHint";
      hint.className = "invite-note";
      hint.textContent = blockedReason;
      startButton.setAttribute("aria-describedby", hint.id);
      actions.appendChild(hint);
    }
    if (owner) {
      const dissolve = addButton("解散房间", async () => {
        const targetRoomId = room?.room_id;
        const canDissolve = () => room?.room_id === targetRoomId && isInviteWaiting(room)
          && !room.spectating && Boolean(room.initiator_player_id)
          && viewerPlayerIdFor(room) === room.initiator_player_id;
        if (!canDissolve()) return;
        try {
          if (!await confirmWaitingRoomDissolve(room.room_kind) || !canDissolve()) return;
          await request(`/api/rooms/${targetRoomId}/leave`, {method: "POST", body: "{}"});
          if (room?.room_id === targetRoomId) await backToLobby();
        } finally {
          // Polling may have replaced the original trigger while the dialog was open.
          dissolve.disabled = false;
          if (room?.room_id === targetRoomId) {
            panel.querySelector('[data-action="解散房间"]')?.focus();
          }
        }
      }, actions);
      dissolve.classList.remove("secondary");
      dissolve.classList.add("danger");
      if (!teamSetup && hasInvitee && room.participants.length < room.target_player_count && canFill) {
        const fillButton = addButton("NPC 补满并开始", () => start(true), actions);
        fillButton.disabled = !selectionReady;
      }
    }
    const notice = document.createElement("p");
    notice.id = "inviteRoomNotice";
    notice.className = "notice";
    notice.setAttribute("role", "status");
    panel.appendChild(notice);
  }
}
$("reclaimControl").addEventListener("click", async () => {
  const control = $("reclaimControl");
  const targetRoomId = room?.room_id;
  control.disabled = true;
  try {
    const data = await request(`/api/rooms/${targetRoomId}/reclaim`, {method: "POST", body: "{}"});
    if (room?.room_id === targetRoomId) renderGame(data.room, data.message, data.timeline);
  } catch (e) { showNotice(e.message, true); }
  finally { control.disabled = false; }
});
$("inviteCreateEntry").addEventListener("click", () => showInviteDialog("create"));
$("inviteJoinEntry").addEventListener("click", () => showInviteDialog("join"));
$("inviteDismiss").addEventListener("click", () => $("inviteDialog").close());
$("inviteCategory").addEventListener("change", inviteCategoryChanged);
$("inviteGame").addEventListener("change", inviteCounts);
$("inviteCount").addEventListener("change", () => {
  updateInviteStakeAvailability();
  renderInviteMachinePicker();
});
$("inviteAiTrigger").addEventListener("click", () => {
  setInviteMachinePickerOpen($("inviteAiTrigger").getAttribute("aria-expanded") !== "true");
});
$("inviteAiField").addEventListener("keydown", (event) => {
  if (event.key === "Escape" && $("inviteAiTrigger").getAttribute("aria-expanded") === "true") {
    event.preventDefault();
    setInviteMachinePickerOpen(false);
    $("inviteAiTrigger").focus();
  } else if (["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) {
    event.preventDefault();
    setInviteMachinePickerOpen(true);
    const options = [...$("inviteAiMenu").querySelectorAll("button:not(:disabled)")];
    const index = options.indexOf(document.activeElement);
    const next = event.key === "Home" ? 0 : event.key === "End" ? options.length - 1
      : (index + (event.key === "ArrowDown" ? 1 : -1) + options.length) % options.length;
    options[next]?.focus();
  }
});
document.addEventListener("click", (event) => {
  const field = $("inviteAiField");
  const path = typeof event.composedPath === "function" ? event.composedPath() : [];
  if (!path.includes(field) && !field.contains(event.target)) setInviteMachinePickerOpen(false);
});
$("inviteCodeInput").addEventListener("input", () => {
  previewedInviteCode = null;
  $("invitePreview").textContent = "";
  $("inviteSubmit").textContent = "确认加入";
});
$("inviteSubmit").addEventListener("click", async () => {
  $("inviteSubmit").disabled = true;
  try {
    if (inviteMode === "join") {
      const code = $("inviteCodeInput").value.trim().toUpperCase();
      if (previewedInviteCode !== code) {
        const data = await request(`/api/invites/${encodeURIComponent(code)}`);
        showInviteStakePreview(code, data.invitation);
        // For a paid invite, the first click previews the amount; the next
        // explicit click agrees to that displayed stake and joins.
        if (data.invitation.stake > 0) {
          $("inviteError").textContent = "请核对本局筹码，再点击同意押注加入。";
          return;
        }
      }
    }
    $("inviteError").textContent = "";
    if (inviteMode === "create") renderInviteMachinePicker();
    const body = inviteMode === "create"
      ? {game_type: selectedGameType($("inviteGame")), game_options: selectedGameOptions($("inviteGame")), target_player_count: Number($("inviteCount").value), ai_players: [...inviteMachineIds], stake: Number($("inviteStake").value), timeout_takeover_seconds: Number($("inviteTimeout").value)}
      : {invite_code: $("inviteCodeInput").value.trim().toUpperCase()};
    const data = await request(inviteMode === "create" ? "/api/invites" : "/api/invites/join", {method: "POST", body: JSON.stringify(body)});
    $("inviteDialog").close();
    renderGame(data.room, data.message, data.timeline);
    const url = new URL(location.href); url.searchParams.delete("invite"); history.replaceState(history.state, "", url);
    writeRoomRoute(data.room.room_id, {mode: "push", backToLobby: true}); startRoomPolling();
  } catch (e) { $("inviteError").textContent = e.message; }
  finally { $("inviteSubmit").disabled = false; }
});
function syncChatRoomMode() {
  const enabled = !!room;
  document.querySelector(".game-chat-area .chat-hint")?.classList.toggle("hidden", !enabled);
  const input = $("chatInput");
  for (const [attribute, value] of Object.entries({role: "combobox", "aria-autocomplete": "list", "aria-controls": "mentionOptions"})) {
    if (enabled) input.setAttribute(attribute, value);
    else input.removeAttribute(attribute);
  }
  if (enabled && !input.hasAttribute("aria-expanded")) input.setAttribute("aria-expanded", "false");
  if (!enabled) {
    $("mentionOptions").replaceChildren();
    closeMentionOptions();
  }
}
let mentionActiveIndex = -1;
function closeMentionOptions() {
  $("mentionOptions").classList.add("hidden");
  if (room) $("chatInput").setAttribute("aria-expanded", "false");
  else $("chatInput").removeAttribute("aria-expanded");
  $("chatInput").removeAttribute("aria-activedescendant");
  mentionActiveIndex = -1;
}
function selectMentionOption(index) {
  const options = [...$("mentionOptions").children];
  if (!options.length) return;
  mentionActiveIndex = (index + options.length) % options.length;
  options.forEach((option, i) => option.setAttribute("aria-selected", String(i === mentionActiveIndex)));
  const option = options[mentionActiveIndex];
  $("chatInput").setAttribute("aria-activedescendant", option.id);
  // Scroll only the list: scrollIntoView can move the board/page on phones.
  const menu = $("mentionOptions");
  if (option.offsetTop < menu.scrollTop) menu.scrollTop = option.offsetTop;
  else if (option.offsetTop + option.offsetHeight > menu.scrollTop + menu.clientHeight) {
    menu.scrollTop = option.offsetTop + option.offsetHeight - menu.clientHeight;
  }
}
function mentionCompletion() {
  const input = $("chatInput");
  const prefix = input.value.slice(0, input.selectionStart);
  const match = prefix.match(/(?:^|\s)@([^\s@]*)$/u);
  const list = $("mentionOptions");
  list.replaceChildren();
  closeMentionOptions();
  const candidates = match ? (room?.participants || []).filter((p) => p.participant_kind !== "system_npc" && p.handle && p.player_id !== viewerPlayerIdFor(room) && (p.handle.includes(match[1]) || p.display_name.includes(match[1]))) : [];
  if (!candidates.length) return;
  list.classList.remove("hidden");
  input.setAttribute("aria-expanded", "true");
  for (const [index, p] of candidates.entries()) {
    const b = document.createElement("button");
    b.type = "button";
    b.id = `mention-option-${index}`;
    b.tabIndex = -1;
    b.setAttribute("role", "option");
    b.setAttribute("aria-selected", "false");
    const name = document.createElement("span");
    name.className = "mention-name";
    name.textContent = p.display_name;
    const id = document.createElement("span");
    id.className = "mention-id";
    id.textContent = ` · ID ${participantAccountId(p)}`;
    b.append(name, id);
    b.title = b.textContent;
    // Keep the input focused and the software keyboard open on selection.
    b.addEventListener("pointerdown", (event) => event.preventDefault());
    b.addEventListener("click", () => {
      const end = input.selectionStart; const start = prefix.lastIndexOf("@");
      input.setRangeText(`@${p.handle} `, start, end, "end");
      closeMentionOptions(); input.focus();
    });
    list.appendChild(b);
  }
}
$("chatInput").addEventListener("input", mentionCompletion);
$("chatInput").addEventListener("click", mentionCompletion);
$("chatInput").addEventListener("blur", closeMentionOptions);
$("chatInput").addEventListener("keydown", (e) => {
  if (e.isComposing) return;
  const list = $("mentionOptions");
  if (!list.classList.contains("hidden") && ["ArrowDown", "ArrowUp", "Tab", "Enter"].includes(e.key)) {
    if (e.key === "Tab" && e.shiftKey) { closeMentionOptions(); return; }
    e.preventDefault();
    if (e.key === "Enter" || e.key === "Tab") list.children[Math.max(0, mentionActiveIndex)]?.click();
    else selectMentionOption(mentionActiveIndex < 0 ? (e.key === "ArrowDown" ? 0 : list.children.length - 1) : mentionActiveIndex + (e.key === "ArrowDown" ? 1 : -1));
  }
  if (e.key === "Escape") {
    e.preventDefault(); closeMentionOptions();
  }
});


$("createButton").addEventListener("click", createRoom);
$("aiPlayer").addEventListener("change", machineSelectionChanged);
$("aiMultiTrigger").addEventListener("click", toggleMachineMultiPicker);
$("aiMultiTrigger").addEventListener("keydown", (event) => {
  if (["ArrowDown", "ArrowUp"].includes(event.key)) {
    event.preventDefault();
    openMachineMultiPicker();
    if (event.key === "ArrowUp") {
      const options = [...$("aiMultiMenu").querySelectorAll('[role="option"]')];
      if (options.length) options[options.length - 1].focus();
    }
  } else if (event.key === "Escape") {
    closeMachineMultiPicker();
  }
});
$("targetPlayerCount").addEventListener("change", () => {
  const target = selectedTargetPlayerCount();
  const selected = selectedParticipantIds();
  if (selected.length > target - 1) {
    const retained = new Set(selected.slice(0, target - 1));
    [...selectedMachineIds].forEach((playerId) => {
      if (!retained.has(playerId)) selectedMachineIds.delete(playerId);
    });
  }
  renderMachineMultiPicker();
  machineSelectionChanged();
});
$("fillWithNpcs").addEventListener("change", renderCreateSeatPreview);
$("gameCategory").addEventListener("change", gameCategoryChanged);
$("gameType").addEventListener("change", () => {
  renderGameRulePicker($("gameType"));
  updateGameTokenEstimate();
  configureParticipantPicker();
  renderSelectedStakeHint();
});
$("mode").addEventListener("change", updateCreateButtonState);
$("stake").addEventListener("input", () => {
  updateCreateButtonState();
  renderSelectedStakeHint();
});
$("refreshRoomsButton").addEventListener("click", () => roomListScope === "machines" ? loadSpectatorRooms() : loadIdentity());
$("roomListScope").addEventListener("click", (event) => {
  const button = event.target.closest("[data-room-scope]");
  if (!button || button.dataset.roomScope === roomListScope) return;
  setRoomListScope(button.dataset.roomScope);
  if (roomListScope === "machines") void loadSpectatorRooms();
  else void loadIdentity({quiet: true});
});
$("roomListScope").addEventListener("keydown", (event) => {
  const tabs = [...$("roomListScope").querySelectorAll('[role="tab"]')];
  const index = tabs.indexOf(event.target);
  if (index < 0) return;
  const next = {ArrowRight: (index + 1) % tabs.length, ArrowLeft: (index + tabs.length - 1) % tabs.length,
    Home: 0, End: tabs.length - 1}[event.key];
  if (next === undefined) return;
  event.preventDefault();
  tabs[next].focus();
  tabs[next].click();
});
for (const id of ["turn", "spectatorName"]) {
  $(id).addEventListener("click", () => { if (room?.spectating) toast($(id).textContent); });
  $(id).addEventListener("keydown", (event) => {
    if (room?.spectating && ["Enter", " "].includes(event.key)) {
      event.preventDefault();
      toast($(id).textContent);
    }
  });
}
$("spectatorSelect").addEventListener("change", () => {
  if (!room?.spectating) return;
  const perspective = $("spectatorSelect").value;
  $("spectatorSelect").value = viewerPlayerIdFor(room);
  void openRoom(room.room_id, {historyMode: "none", perspective});
});
$("notificationBell").addEventListener("click", openNotificationList);
$("closeNotificationModalButton").addEventListener("click", closeNotificationModal);
$("backButton").addEventListener("click", backToLobby);
$("refreshButton").addEventListener("click", () => refreshRoom());
$("copyRoomButton").addEventListener("click", () => copyRoomNumber());
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
$("notificationModal").addEventListener("click", (event) => {
  if (event.target === $("notificationModal")) closeNotificationModal();
});
document.addEventListener("click", (event) => {
  if (!eventStartedInsideMachinePicker(event)) closeMachineMultiPicker();
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
    if (event.isComposing || event.defaultPrevented || !$("mentionOptions").classList.contains("hidden")) return;
    event.preventDefault();
    sendMessage();
  }
});
window.addEventListener("keydown", (event) => {
  if (event.key === "Escape") {
    closeMachineMultiPicker();
    hideWaitModeModal();
    closeNotificationModal();
    closeRules();
    closeHistory();
  }
});
window.addEventListener("storage", (event) => {
  if (event.key === UNREAD_SYNC_STORAGE_KEY) {
    void refreshHumanUnreadState({autoPopup: true});
  }
});
window.addEventListener("focus", resumeRoomRefresh);
window.addEventListener("pageshow", resumeRoomRefresh);
window.addEventListener("pagehide", suspendRoomRefresh);
document.addEventListener("visibilitychange", () => {
  if (document.hidden) suspendRoomRefresh();
  else resumeRoomRefresh();
});

window.addEventListener("popstate", () => {
  const routeRoomId = roomIdFromLocation();
  if (routeRoomId) {
    void openRoom(routeRoomId, {historyMode: "none", routeRestore: true});
  } else {
    void backToLobby({fromHistory: true});
  }
});

// All room reads, including our-turn chat refreshes, use the single cancellable chain.
$("chipBalanceLink").href = apiPath("/chips");
$("chipCenterLink").href = apiPath("/chips");
ensureInitialRouteState();
void (async () => {
  const invite = new URL(location.href).searchParams.get("invite");
  if (invite && /^[A-Fa-f0-9]{12}$/.test(invite)) {
    const saved = localStorage.getItem("cedartoy_token");
    if (saved && !new URL(location.href).searchParams.has("token")) {
      const url = new URL(location.href); url.searchParams.set("token", saved); location.replace(url.href); return;
    }
  }
  await loadIdentity();
  // Remove credentials from browser history; invitation links never include them.
  const clean = new URL(location.href); clean.searchParams.delete("token"); history.replaceState(history.state, "", clean);
  if (invite && /^[A-Fa-f0-9]{12}$/.test(invite)) {
    if (!identity) { location.href = `/?duel_invite=${encodeURIComponent(invite)}`; return; }
    await showInviteDialog("join", invite.toUpperCase());
  }
  const routeRoomId = roomIdFromLocation();
  if (identity && routeRoomId) {
    await openRoom(routeRoomId, {historyMode: "none", routeRestore: true});
  }
})();

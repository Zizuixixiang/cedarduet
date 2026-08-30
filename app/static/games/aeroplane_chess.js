(function registerAeroplaneChessRenderer() {
  "use strict";

  const SVG_NS = "http://www.w3.org/2000/svg";
  const STYLE_ID = "duel-game-aeroplane-chess-styles";
  const STYLE_HREF = "/static/games/aeroplane_chess.css?v=0.1.1";
  const COLORS = ["red", "yellow", "blue", "green"];
  const COLOR_LABELS = {
    red: "红方",
    yellow: "黄方",
    blue: "蓝方",
    green: "绿方",
  };
  const PIP_POSITIONS = {
    1: [5],
    2: [1, 9],
    3: [1, 5, 9],
    4: [1, 3, 7, 9],
    5: [1, 3, 5, 7, 9],
    6: [1, 3, 4, 6, 7, 9],
  };
  const AIRPORT_POINTS = {
    red: [[13, 78], [23, 78], [13, 88], [23, 88]],
    yellow: [[13, 12], [23, 12], [13, 22], [23, 22]],
    blue: [[77, 12], [87, 12], [77, 22], [87, 22]],
    green: [[77, 78], [87, 78], [77, 88], [87, 88]],
  };
  const LAUNCH_POINTS = {
    red: [50, 94],
    yellow: [6, 50],
    blue: [50, 6],
    green: [94, 50],
  };
  const HOME_LANE_POINTS = {
    red: [[50, 80], [50, 75], [50, 70], [50, 65], [50, 60], [50, 55]],
    yellow: [[20, 50], [25, 50], [30, 50], [35, 50], [40, 50], [45, 50]],
    blue: [[50, 20], [50, 25], [50, 30], [50, 35], [50, 40], [50, 45]],
    green: [[80, 50], [75, 50], [70, 50], [65, 50], [60, 50], [55, 50]],
  };
  const HOME_POINTS = {
    red: [[47, 53], [49, 54], [51, 54], [53, 53]],
    yellow: [[46, 47], [46, 49], [46, 51], [47, 53]],
    blue: [[47, 47], [49, 46], [51, 46], [53, 47]],
    green: [[53, 47], [54, 49], [54, 51], [53, 53]],
  };
  const STACK_OFFSETS = [
    [0, 0], [-1.25, -1.25], [1.25, -1.25], [-1.25, 1.25],
    [1.25, 1.25], [0, -1.7], [0, 1.7],
  ];

  function ensureStylesheet(documentRef) {
    if (!documentRef || !documentRef.head) return null;
    const existing = typeof documentRef.getElementById === "function"
      ? documentRef.getElementById(STYLE_ID)
      : null;
    if (existing) return existing;
    const link = documentRef.createElement("link");
    link.id = STYLE_ID;
    link.rel = "stylesheet";
    link.href = STYLE_HREF;
    link.dataset.duelGameStyle = "aeroplane_chess";
    documentRef.head.appendChild(link);
    return link;
  }

  function svgNode(documentRef, tag, attributes = {}) {
    const node = documentRef.createElementNS(SVG_NS, tag);
    Object.entries(attributes).forEach(([name, value]) => {
      node.setAttribute(name, String(value));
    });
    return node;
  }

  function rotatePoint(point, quarterTurns) {
    let [x, y] = point;
    for (let turn = 0; turn < quarterTurns; turn += 1) {
      [x, y] = [y, 100 - x];
    }
    return [x, y];
  }

  function ringPoint(ringIndex) {
    const angle = (90 + (Number(ringIndex) * 360 / 52)) * Math.PI / 180;
    return [50 + 36 * Math.cos(angle), 50 + 36 * Math.sin(angle)];
  }

  function playerName(participant) {
    return participant && (
      participant.display_name || participant.player_id
    ) || "玩家";
  }

  function createEdgeIdentity(documentRef, context, participant, color, point) {
    const item = documentRef.createElement("article");
    const viewerId = context.viewer && context.viewer.player_id;
    item.className = [
      "board-edge-participant",
      "aeroplane-edge-participant",
      `seat-${participant.seat_index}`,
      `color-${color}`,
      participant.player_id === context.room.current_player_id ? "current" : "",
      participant.player_id === viewerId ? "viewer" : "",
    ].filter(Boolean).join(" ");
    item.dataset.playerId = participant.player_id;
    item.dataset.color = color;
    item.dataset.visualEdge = `${point[1] < 50 ? "top" : "bottom"}-${point[0] < 50 ? "left" : "right"}`;
    item.style.setProperty("--edge-column", point[0] < 50 ? 1 : 2);
    item.setAttribute(
      "aria-label",
      `${playerName(participant)}，${COLOR_LABELS[color]}${participant.player_id === viewerId ? "，你" : ""}`
    );
    const avatar = documentRef.createElement("span");
    avatar.className = "board-edge-avatar";
    if (
      context.helpers
      && typeof context.helpers.renderParticipantAvatar === "function"
    ) {
      context.helpers.renderParticipantAvatar(avatar, participant);
    } else {
      avatar.textContent = Array.from(playerName(participant))[0] || "?";
    }
    const copy = documentRef.createElement("span");
    copy.className = "board-edge-copy";
    const name = documentRef.createElement("strong");
    name.textContent = `${playerName(participant)}${participant.player_id === viewerId ? "（你）" : ""}`;
    const label = documentRef.createElement("small");
    label.textContent = COLOR_LABELS[color];
    copy.append(name, label);
    item.append(avatar, copy);
    return item;
  }

  function edgeRosters(documentRef, context, quarterTurns) {
    if ((context.participants || []).length <= 2) return null;
    const top = documentRef.createElement("div");
    const bottom = documentRef.createElement("div");
    top.className = "aeroplane-edge-roster top";
    bottom.className = "aeroplane-edge-roster bottom";
    (context.participants || []).forEach((participant) => {
      const color = colorForPlayer(context.state, participant.player_id);
      const airport = AIRPORT_POINTS[color] || AIRPORT_POINTS.red;
      const center = airport.reduce(
        (total, point) => [total[0] + point[0] / 4, total[1] + point[1] / 4],
        [0, 0]
      );
      const visualPoint = rotatePoint(center, quarterTurns);
      const item = createEdgeIdentity(
        documentRef, context, participant, color, visualPoint
      );
      (visualPoint[1] < 50 ? top : bottom).appendChild(item);
    });
    return {top, bottom};
  }

  function colorForPlayer(state, playerId) {
    return (state.color_by_player || {})[playerId] || "red";
  }

  function viewerQuarterTurns(context) {
    const viewerId = context.viewer && context.viewer.player_id;
    const color = colorForPlayer(context.state, viewerId);
    return Math.max(0, COLORS.indexOf(color));
  }

  function locationPoint(location, color, planeIndex, quarterTurns) {
    if (!location) return rotatePoint([50, 50], quarterTurns);
    let point;
    if (location.zone === "airport") {
      point = (AIRPORT_POINTS[color] || AIRPORT_POINTS.red)[planeIndex] || [18, 83];
    } else if (location.zone === "launch") {
      point = LAUNCH_POINTS[color] || LAUNCH_POINTS.red;
    } else if (location.zone === "track") {
      point = ringPoint(location.ring_index);
    } else if (location.zone === "home_lane") {
      point = (HOME_LANE_POINTS[color] || HOME_LANE_POINTS.red)[
        Math.max(0, Number(location.home_lane_index || 1) - 1)
      ];
    } else {
      point = (HOME_POINTS[color] || HOME_POINTS.red)[planeIndex] || [50, 50];
    }
    return rotatePoint(point, quarterTurns);
  }

  function planeLocation(plane) {
    return {
      zone: plane.zone,
      route_step: plane.route_step,
      ring_index: plane.ring_index,
      home_lane_index: plane.home_lane_index,
    };
  }

  function positionLabel(plane) {
    if (plane.zone === "airport") return "在机场";
    if (plane.zone === "launch") return "在安全起飞区";
    if (plane.zone === "track") return `在公共环线第 ${plane.ring_index + 1} 格`;
    if (plane.zone === "home_lane") return `在终点航道第 ${plane.home_lane_index} 格`;
    return "已经到家";
  }

  function createPlaneGraphic(documentRef) {
    const svg = svgNode(documentRef, "svg", {
      viewBox: "0 0 64 64",
      "aria-hidden": "true",
      focusable: "false",
    });
    svg.classList.add("aeroplane-token-svg");
    const shadow = svgNode(documentRef, "path", {
      class: "aeroplane-token-shadow",
      d: "M32 4 C35 4 37 8 37 13 L37 23 L58 34 L58 40 L37 35 L36 51 L45 57 L45 61 L32 57 L19 61 L19 57 L28 51 L27 35 L6 40 L6 34 L27 23 L27 13 C27 8 29 4 32 4 Z",
    });
    const body = svgNode(documentRef, "path", {
      class: "aeroplane-token-body",
      d: "M32 3 C35 3 36 8 36 13 L36 24 L57 34 L57 38 L36 34 L35 51 L44 57 L44 59 L32 56 L20 59 L20 57 L29 51 L28 34 L7 38 L7 34 L28 24 L28 13 C28 8 29 3 32 3 Z",
    });
    const shine = svgNode(documentRef, "path", {
      class: "aeroplane-token-shine",
      d: "M32 7 C33 7 33 10 33 15 L33 29 L48 35 L35 32 L33 49 L32 53 Z",
    });
    svg.append(shadow, body, shine);
    return svg;
  }

  function createDie(documentRef, value, compact = false) {
    const die = documentRef.createElement("span");
    die.className = `aeroplane-die${compact ? " compact" : ""}${value ? "" : " empty"}`;
    die.setAttribute("role", "img");
    die.setAttribute("aria-label", value ? `${value} 点` : "尚未掷骰");
    const active = new Set(PIP_POSITIONS[value] || []);
    for (let position = 1; position <= 9; position += 1) {
      const pip = documentRef.createElement("span");
      pip.className = `aeroplane-pip${active.has(position) ? " on" : ""}`;
      pip.setAttribute("aria-hidden", "true");
      die.appendChild(pip);
    }
    return die;
  }

  function appendAirport(documentRef, svg, color, quarterTurns) {
    const unrotated = AIRPORT_POINTS[color];
    const corners = unrotated.map((point) => rotatePoint(point, quarterTurns));
    const center = corners.reduce(
      (total, point) => [total[0] + point[0] / 4, total[1] + point[1] / 4],
      [0, 0]
    );
    const base = svgNode(documentRef, "rect", {
      x: center[0] - 11,
      y: center[1] - 11,
      width: 22,
      height: 22,
      rx: 5,
      class: `aeroplane-airport color-${color}`,
    });
    svg.appendChild(base);
    corners.forEach(([x, y]) => {
      svg.appendChild(svgNode(documentRef, "circle", {
        cx: x,
        cy: y,
        r: 3.7,
        class: `aeroplane-airport-slot color-${color}`,
      }));
    });
  }

  function appendStaticBoard(documentRef, svg, state, quarterTurns) {
    svg.appendChild(svgNode(documentRef, "rect", {
      x: 1,
      y: 1,
      width: 98,
      height: 98,
      rx: 7,
      class: "aeroplane-board-base",
    }));
    COLORS.forEach((color) => appendAirport(documentRef, svg, color, quarterTurns));

    const mappings = state.path_mappings || {};
    COLORS.forEach((color) => {
      const shortcut = (mappings[color] || {}).shortcut || {};
      if (!Number.isInteger(shortcut.from_ring_index)) return;
      const [fromX, fromY] = rotatePoint(
        ringPoint(shortcut.from_ring_index), quarterTurns
      );
      const [toX, toY] = rotatePoint(
        ringPoint(shortcut.to_ring_index), quarterTurns
      );
      svg.appendChild(svgNode(documentRef, "path", {
        d: `M ${fromX} ${fromY} Q 50 50 ${toX} ${toY}`,
        class: `aeroplane-shortcut-line color-${color}`,
      }));
      const [crossX, crossY] = rotatePoint(
        ringPoint(shortcut.cross_ring_index), quarterTurns
      );
      svg.appendChild(svgNode(documentRef, "circle", {
        cx: crossX,
        cy: crossY,
        r: 2.2,
        class: `aeroplane-shortcut-cross color-${color}`,
      }));
    });

    for (let ringIndex = 0; ringIndex < 52; ringIndex += 1) {
      const [x, y] = rotatePoint(ringPoint(ringIndex), quarterTurns);
      const color = COLORS[ringIndex % 4];
      const isShortcut = COLORS.some((candidate) => (
        ((mappings[candidate] || {}).shortcut || {}).from_ring_index === ringIndex
      ));
      const cell = svgNode(documentRef, "circle", {
        cx: x,
        cy: y,
        r: isShortcut ? 2.55 : 2.15,
        class: `aeroplane-track-cell color-${color}${isShortcut ? " shortcut" : " jump"}`,
      });
      svg.appendChild(cell);
      if (isShortcut) {
        svg.appendChild(svgNode(documentRef, "path", {
          d: `M ${x - 1.25} ${y + 0.7} L ${x} ${y - 1.2} L ${x + 1.25} ${y + 0.7}`,
          class: "aeroplane-shortcut-mark",
        }));
      } else {
        svg.appendChild(svgNode(documentRef, "circle", {
          cx: x,
          cy: y,
          r: 0.55,
          class: "aeroplane-jump-mark",
        }));
      }
    }

    COLORS.forEach((color) => {
      const launch = rotatePoint(LAUNCH_POINTS[color], quarterTurns);
      svg.appendChild(svgNode(documentRef, "rect", {
        x: launch[0] - 4,
        y: launch[1] - 2.5,
        width: 8,
        height: 5,
        rx: 2.2,
        class: `aeroplane-launch color-${color}`,
      }));
      HOME_LANE_POINTS[color].forEach((point, index) => {
        const [x, y] = rotatePoint(point, quarterTurns);
        svg.appendChild(svgNode(documentRef, "circle", {
          cx: x,
          cy: y,
          r: 2.55,
          class: `aeroplane-home-lane color-${color}`,
          "data-lane-index": index + 1,
        }));
      });
    });

    const centerPoints = [
      [50, 45], [55, 50], [50, 55], [45, 50],
    ].map((point) => rotatePoint(point, quarterTurns));
    centerPoints.forEach(([x, y], index) => {
      svg.appendChild(svgNode(documentRef, "path", {
        d: `M 50 50 L ${x} ${y} L ${
          centerPoints[(index + 1) % centerPoints.length][0]
        } ${centerPoints[(index + 1) % centerPoints.length][1]} Z`,
        class: `aeroplane-center color-${COLORS[(index - quarterTurns + 4) % 4]}`,
      }));
    });
    svg.appendChild(svgNode(documentRef, "circle", {
      cx: 50,
      cy: 50,
      r: 2.2,
      class: "aeroplane-center-core",
    }));
  }

  function lastRoutePoints(state, quarterTurns) {
    const action = state.last_action;
    if (!action || action.action !== "move") return [];
    const color = action.color || "red";
    const points = [action.from, ...(action.landings || []).map((item) => item.location)];
    return points.map((location) => locationPoint(
      location, color, Number(action.plane_index || 0), quarterTurns
    ));
  }

  function appendLastRoute(documentRef, svg, state, quarterTurns) {
    const points = lastRoutePoints(state, quarterTurns);
    if (points.length < 2) return;
    svg.appendChild(svgNode(documentRef, "polyline", {
      points: points.map((point) => point.join(",")).join(" "),
      class: "aeroplane-last-route",
    }));
    const last = points[points.length - 1];
    svg.appendChild(svgNode(documentRef, "circle", {
      cx: last[0],
      cy: last[1],
      r: 3.4,
      class: "aeroplane-last-target",
    }));
  }

  function appendLegalTargets(documentRef, shell, context, quarterTurns) {
    const state = context.state;
    const currentColor = colorForPlayer(state, context.room.current_player_id);
    const seen = new Set();
    (Array.isArray(context.legalMoves) ? context.legalMoves : []).forEach((move) => {
      if (move.action !== "move" || !move.to) return;
      const point = locationPoint(
        move.to, currentColor, Number(move.plane_index || 0), quarterTurns
      );
      const key = `${point[0].toFixed(2)}:${point[1].toFixed(2)}`;
      if (seen.has(key)) return;
      seen.add(key);
      const marker = documentRef.createElement("span");
      marker.className = "aeroplane-legal-target";
      marker.style.left = `${point[0]}%`;
      marker.style.top = `${point[1]}%`;
      marker.setAttribute("aria-hidden", "true");
      shell.appendChild(marker);
    });
  }

  function collectPlaneEntries(state, quarterTurns) {
    const entries = [];
    Object.entries(state.planes || {}).forEach(([playerId, planes]) => {
      const color = colorForPlayer(state, playerId);
      planes.forEach((plane) => {
        entries.push({
          playerId,
          color,
          plane,
          point: locationPoint(
            planeLocation(plane), color, Number(plane.plane_index), quarterTurns
          ),
        });
      });
    });
    const grouped = new Map();
    entries.forEach((entry) => {
      const {plane, color, point} = entry;
      const key = plane.zone === "track"
        ? `track:${plane.ring_index}`
        : plane.zone === "home_lane"
          ? `lane:${color}:${plane.home_lane_index}`
          : `${color}:${plane.zone}:${plane.plane_index}`;
      const group = grouped.get(key) || [];
      group.push(entry);
      grouped.set(key, group);
      entry.point = point;
    });
    grouped.forEach((group) => {
      group.forEach((entry, index) => {
        const offset = STACK_OFFSETS[index] || [0, 0];
        entry.point = [entry.point[0] + offset[0], entry.point[1] + offset[1]];
      });
    });
    return entries;
  }

  function appendPlanes(documentRef, shell, context, quarterTurns) {
    const {state, helpers, room} = context;
    const legalIds = new Set(
      (Array.isArray(context.legalActions) ? context.legalActions : [])
        .filter((action) => action.action === "move")
        .map((action) => action.plane_id)
    );
    const participantMap = new Map(
      (context.participants || []).map((participant) => [
        participant.player_id, participant,
      ])
    );
    const lastAction = state.last_action || {};
    const returnedIds = new Set(lastAction.returned_plane_ids || []);
    collectPlaneEntries(state, quarterTurns).forEach((entry) => {
      const {playerId, color, plane, point} = entry;
      const legal = Boolean(context.canMove) && legalIds.has(plane.plane_id);
      const token = documentRef.createElement("button");
      token.type = "button";
      token.className = [
        "aeroplane-token",
        `color-${color}`,
        `zone-${plane.zone}`,
        legal ? "legal" : "",
        lastAction.plane_id === plane.plane_id ? "last-moved" : "",
        returnedIds.has(plane.plane_id) ? "recently-returned" : "",
        plane.zone === "home" ? "arrived-home" : "",
      ].filter(Boolean).join(" ");
      token.dataset.planeId = plane.plane_id;
      token.dataset.planeIndex = String(plane.plane_index);
      token.dataset.playerId = playerId;
      token.dataset.logicalZone = plane.zone;
      token.dataset.logicalRouteStep = String(plane.route_step);
      token.style.left = `${point[0]}%`;
      token.style.top = `${point[1]}%`;
      token.disabled = !legal;
      const participant = participantMap.get(playerId);
      token.setAttribute(
        "aria-label",
        `${playerName(participant)}的${COLOR_LABELS[color]} ${plane.plane_index + 1} 号机，${positionLabel(plane)}${legal ? "，可以移动" : ""}`
      );
      token.appendChild(createPlaneGraphic(documentRef));
      if (legal) {
        const badge = documentRef.createElement("span");
        badge.className = "aeroplane-move-badge";
        badge.textContent = String((state.last_roll || {}).value || "");
        badge.setAttribute("aria-hidden", "true");
        token.appendChild(badge);
        token.addEventListener("click", async () => {
          if (!helpers || typeof helpers.submitMove !== "function") return;
          if (typeof helpers.canMove === "function" && !helpers.canMove()) return;
          token.disabled = true;
          const submitted = await helpers.submitMove({
            action: "move",
            plane_id: plane.plane_id,
            plane_index: plane.plane_index,
          });
          if (!submitted) token.disabled = false;
        });
      }
      if (room.current_player_id === playerId) token.dataset.currentPlayer = "true";
      shell.appendChild(token);
    });
  }

  function phaseCopy(context) {
    const phase = (context.state.flow || {}).phase;
    if (context.isTerminal || phase === "finished") return "本局已经结束";
    if (phase === "awaiting_plane_choice") {
      return context.canMove ? "请选择一架高亮飞机" : "等待当前玩家选择飞机";
    }
    return context.canMove ? "可以掷骰" : "等待当前玩家掷骰";
  }

  function renderBoard(context) {
    const documentRef = context.document || window.document;
    ensureStylesheet(documentRef);
    const {board, state, room} = context;
    const quarterTurns = viewerQuarterTurns(context);
    if (context.helpers && typeof context.helpers.setBoardLayout === "function") {
      context.helpers.setBoardLayout({
        rows: 1,
        cols: 1,
        visualRows: 1,
        visualCols: 1,
        large: true,
        ariaLabel: "飞行棋棋盘：四角机场、公共环线、终点航道和中央终点",
      });
    }
    board.dataset.viewerColor = colorForPlayer(
      state, context.viewer && context.viewer.player_id
    );
    board.dataset.viewerRotation = String(quarterTurns * 90);

    const root = documentRef.createElement("section");
    root.className = "aeroplane-game";
    const heading = documentRef.createElement("header");
    heading.className = "aeroplane-board-heading";
    const current = (context.participants || []).find(
      (participant) => participant.player_id === room.current_player_id
    );
    const title = documentRef.createElement("strong");
    title.textContent = `${playerName(current)} · ${COLOR_LABELS[colorForPlayer(state, room.current_player_id)]}`;
    const status = documentRef.createElement("span");
    status.textContent = phaseCopy(context);
    heading.append(title, status);

    const shell = documentRef.createElement("div");
    shell.className = "aeroplane-board-shell";
    shell.dataset.viewerRotation = String(quarterTurns * 90);
    const svg = svgNode(documentRef, "svg", {
      viewBox: "0 0 100 100",
      class: "aeroplane-board-svg",
      "aria-hidden": "true",
      focusable: "false",
    });
    appendStaticBoard(documentRef, svg, state, quarterTurns);
    appendLastRoute(documentRef, svg, state, quarterTurns);
    shell.appendChild(svg);
    appendLegalTargets(documentRef, shell, context, quarterTurns);
    appendPlanes(documentRef, shell, context, quarterTurns);
    const edgeRoster = edgeRosters(documentRef, context, quarterTurns);

    const activity = documentRef.createElement("div");
    activity.className = "aeroplane-activity";
    activity.setAttribute("role", "status");
    const lastRoll = state.last_roll;
    activity.appendChild(createDie(documentRef, lastRoll && lastRoll.value, true));
    const activityCopy = documentRef.createElement("span");
    activityCopy.textContent = state.last_action_note || (
      lastRoll ? `上一骰 ${lastRoll.value} 点` : "等待第一位玩家掷骰"
    );
    activity.appendChild(activityCopy);
    root.appendChild(heading);
    if (edgeRoster) root.appendChild(edgeRoster.top);
    root.appendChild(shell);
    if (edgeRoster) root.appendChild(edgeRoster.bottom);
    root.appendChild(activity);
    board.appendChild(root);
    return true;
  }

  function renderControls(context) {
    const documentRef = context.document || window.document;
    ensureStylesheet(documentRef);
    const {controls, state, room} = context;
    const legalActions = Array.isArray(context.legalActions)
      ? context.legalActions
      : [];
    const phase = (state.flow || {}).phase;
    const root = documentRef.createElement("section");
    root.className = "aeroplane-controls";

    const actionPanel = documentRef.createElement("div");
    actionPanel.className = "aeroplane-action-panel";
    const die = createDie(documentRef, (state.last_roll || {}).value);
    const actionCopy = documentRef.createElement("div");
    actionCopy.className = "aeroplane-action-copy";
    const actionTitle = documentRef.createElement("strong");
    actionTitle.textContent = phase === "awaiting_plane_choice"
      ? `本次 ${Number((state.last_roll || {}).value || 0)} 点`
      : "本回合掷骰";
    const streak = documentRef.createElement("span");
    const sixes = Number(state.consecutive_sixes || 0);
    streak.textContent = sixes
      ? `连续 6：${sixes} / 3${sixes === 2 ? "，下个 6 将触发惩罚" : ""}`
      : phaseCopy(context);
    actionCopy.append(actionTitle, streak);
    const rollButton = documentRef.createElement("button");
    rollButton.type = "button";
    rollButton.className = "pixel-btn aeroplane-roll-button";
    rollButton.textContent = "掷骰";
    const rollIsLegal = legalActions.some((action) => action.action === "roll");
    rollButton.disabled = !context.canMove || !rollIsLegal;
    rollButton.addEventListener("click", async () => {
      if (!context.helpers || typeof context.helpers.submitMove !== "function") return;
      if (typeof context.helpers.canMove === "function" && !context.helpers.canMove()) return;
      rollButton.disabled = true;
      const submitted = await context.helpers.submitMove({action: "roll"});
      if (!submitted) rollButton.disabled = !rollIsLegal;
    });
    actionPanel.append(die, actionCopy, rollButton);

    root.appendChild(actionPanel);
    if ((context.participants || []).length <= 2) {
      const roster = documentRef.createElement("div");
      roster.className = "aeroplane-roster";
      (context.participants || []).forEach((participant) => {
        const playerId = participant.player_id;
        const color = colorForPlayer(state, playerId);
        const planes = (state.planes || {})[playerId] || [];
        const homeCount = planes.filter((plane) => plane.zone === "home").length;
        const airportCount = planes.filter((plane) => plane.zone === "airport").length;
        const item = documentRef.createElement("div");
        item.className = [
          "aeroplane-roster-item",
          `color-${color}`,
          room.current_player_id === playerId ? "current" : "",
          context.viewer && context.viewer.player_id === playerId ? "viewer" : "",
        ].filter(Boolean).join(" ");
        const swatch = documentRef.createElement("span");
        swatch.className = "aeroplane-color-swatch";
        swatch.setAttribute("aria-hidden", "true");
        const name = documentRef.createElement("strong");
        name.textContent = playerName(participant);
        const count = documentRef.createElement("span");
        count.textContent = `到家 ${homeCount}/4 · 机场 ${airportCount}`;
        item.append(swatch, name, count);
        roster.appendChild(item);
      });
      root.appendChild(roster);
    }
    controls.appendChild(root);
  }

  const renderer = {
    participantPresentation: "board-edge",
    glyph: "飞",
    usesStandardMoveConfirmation: false,
    boardLabel: "飞行棋棋盘",
    ensureStylesheet,
    renderBoard,
    renderControls,
  };

  window.DuelGameUI.register("aeroplane_chess", renderer);
}());

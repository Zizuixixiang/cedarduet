(function registerAeroplaneChessRenderer() {
  "use strict";

  const SVG_NS = "http://www.w3.org/2000/svg";
  const STYLE_ID = "duel-game-aeroplane-chess-styles";
  const STYLE_HREF = "/static/games/aeroplane_chess.css?v=0.2.2";
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
    red: [[6.75, 87.75], [12.25, 87.75], [6.75, 93.25], [12.25, 93.25]],
    yellow: [[6.75, 6.75], [12.25, 6.75], [6.75, 12.25], [12.25, 12.25]],
    blue: [[87.75, 6.75], [93.25, 6.75], [87.75, 12.25], [93.25, 12.25]],
    green: [[87.75, 87.75], [93.25, 87.75], [87.75, 93.25], [93.25, 93.25]],
  };
  const LAUNCH_POINTS = {
    red: [42, 87.5],
    yellow: [12.5, 42],
    blue: [58, 12.5],
    green: [87.5, 58],
  };
  const HOME_LANE_POINTS = {
    red: [[50, 77], [50, 72.4], [50, 67.8], [50, 63.2], [50, 58.6], [50, 54]],
    yellow: [[23, 50], [27.6, 50], [32.2, 50], [36.8, 50], [41.4, 50], [46, 50]],
    blue: [[50, 23], [50, 27.6], [50, 32.2], [50, 36.8], [50, 41.4], [50, 46]],
    green: [[77, 50], [72.4, 50], [67.8, 50], [63.2, 50], [58.6, 50], [54, 50]],
  };
  const HOME_POINTS = {
    red: [[47, 53], [49, 54], [51, 54], [53, 53]],
    yellow: [[46, 47], [46, 49], [46, 51], [47, 53]],
    blue: [[47, 47], [49, 46], [51, 46], [53, 47]],
    green: [[53, 47], [54, 49], [54, 51], [53, 53]],
  };
  const TRACK_MIN = 18;
  const TRACK_MAX = 82;

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
    const normalized = ((Number(ringIndex) % 52) + 52) % 52;
    const side = TRACK_MAX - TRACK_MIN;
    const halfSide = side / 2;
    const distance = normalized * (side * 4 / 52);
    if (distance <= halfSide) {
      return [50 - distance, TRACK_MAX];
    }
    if (distance <= halfSide + side) {
      return [TRACK_MIN, TRACK_MAX - (distance - halfSide)];
    }
    if (distance <= halfSide + side * 2) {
      return [TRACK_MIN + (distance - halfSide - side), TRACK_MIN];
    }
    if (distance <= halfSide + side * 3) {
      return [TRACK_MAX, TRACK_MIN + (distance - halfSide - side * 2)];
    }
    return [TRACK_MAX - (distance - halfSide - side * 3), TRACK_MAX];
  }

  function stackOffsets(size) {
    if (size <= 1) return [[0, 0]];
    if (size === 2) return [[-.28, -.18], [.28, .18]];
    if (size === 3) return [[0, -.3], [-.3, .2], [.3, .2]];
    if (size === 4) {
      return [[-.3, -.3], [.3, -.3], [-.3, .3], [.3, .3]];
    }
    return Array.from({length: size}, (_, index) => {
      const angle = -Math.PI / 2 + index * Math.PI * 2 / size;
      return [Math.cos(angle) * .35, Math.sin(angle) * .35];
    });
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
      x: center[0] - 6,
      y: center[1] - 6,
      width: 12,
      height: 12,
      rx: 3.2,
      class: `aeroplane-airport color-${color}`,
    });
    svg.appendChild(base);
    corners.forEach(([x, y]) => {
      svg.appendChild(svgNode(documentRef, "circle", {
        cx: x,
        cy: y,
        r: 2.35,
        class: `aeroplane-airport-slot color-${color}`,
      }));
    });
  }

  function shortcutControl(from, to) {
    const midpoint = [(from[0] + to[0]) / 2, (from[1] + to[1]) / 2];
    return [
      midpoint[0] + (50 - midpoint[0]) * .42,
      midpoint[1] + (50 - midpoint[1]) * .42,
    ];
  }

  function shortcutPath(from, cross, to) {
    const firstControl = shortcutControl(from, cross);
    const secondControl = shortcutControl(cross, to);
    return {
      d: [
        `M ${from[0]} ${from[1]}`,
        `Q ${firstControl[0]} ${firstControl[1]} ${cross[0]} ${cross[1]}`,
        `Q ${secondControl[0]} ${secondControl[1]} ${to[0]} ${to[1]}`,
      ].join(" "),
      finalControl: secondControl,
    };
  }

  function shortcutArrowPoints(control, destination) {
    const dx = destination[0] - control[0];
    const dy = destination[1] - control[1];
    const length = Math.max(.01, Math.hypot(dx, dy));
    const unitX = dx / length;
    const unitY = dy / length;
    const tip = [destination[0] - unitX * .8, destination[1] - unitY * .8];
    const base = [tip[0] - unitX * 3, tip[1] - unitY * 3];
    return [
      tip,
      [base[0] - unitY * 1.55, base[1] + unitX * 1.55],
      [base[0] + unitY * 1.55, base[1] - unitX * 1.55],
    ].map((point) => point.join(",")).join(" ");
  }

  function appendShortcutRoutes(documentRef, svg, state, quarterTurns) {
    const mappings = state.path_mappings || {};
    COLORS.forEach((color) => {
      const shortcut = (mappings[color] || {}).shortcut || {};
      if (!Number.isInteger(shortcut.from_ring_index)) return;
      const from = rotatePoint(ringPoint(shortcut.from_ring_index), quarterTurns);
      const cross = rotatePoint(ringPoint(shortcut.cross_ring_index), quarterTurns);
      const to = rotatePoint(ringPoint(shortcut.to_ring_index), quarterTurns);
      const route = shortcutPath(from, cross, to);
      svg.appendChild(svgNode(documentRef, "path", {
        d: route.d,
        class: "aeroplane-shortcut-underlay",
      }));
      svg.appendChild(svgNode(documentRef, "path", {
        d: route.d,
        class: `aeroplane-shortcut-line color-${color}`,
        "data-route-kind": "shortcut",
      }));
      svg.appendChild(svgNode(documentRef, "polygon", {
        points: shortcutArrowPoints(route.finalControl, to),
        class: `aeroplane-shortcut-arrow color-${color}`,
      }));
      svg.appendChild(svgNode(documentRef, "circle", {
        cx: cross[0],
        cy: cross[1],
        r: 2.15,
        class: `aeroplane-shortcut-cross color-${color}`,
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

    COLORS.forEach((color) => {
      const lane = HOME_LANE_POINTS[color].map(
        (point) => rotatePoint(point, quarterTurns)
      );
      svg.appendChild(svgNode(documentRef, "path", {
        d: `M ${lane[0][0]} ${lane[0][1]} L ${lane[lane.length - 1][0]} ${lane[lane.length - 1][1]}`,
        class: `aeroplane-home-runway color-${color}`,
      }));
    });

    const mappings = state.path_mappings || {};

    for (let ringIndex = 0; ringIndex < 52; ringIndex += 1) {
      const [x, y] = rotatePoint(ringPoint(ringIndex), quarterTurns);
      const color = COLORS[ringIndex % 4];
      const isShortcut = COLORS.some((candidate) => (
        ((mappings[candidate] || {}).shortcut || {}).from_ring_index === ringIndex
      ));
      const size = isShortcut ? 5.5 : 5;
      const cell = svgNode(documentRef, "rect", {
        x: x - size / 2,
        y: y - size / 2,
        width: size,
        height: size,
        rx: isShortcut ? 1.6 : 1.3,
        class: `aeroplane-track-cell color-${color}${isShortcut ? " shortcut" : " jump"}`,
        "data-ring-index": ringIndex,
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
      svg.appendChild(svgNode(documentRef, "circle", {
        cx: launch[0],
        cy: launch[1],
        r: 2.8,
        class: `aeroplane-launch color-${color}`,
      }));
      HOME_LANE_POINTS[color].forEach((point, index) => {
        const [x, y] = rotatePoint(point, quarterTurns);
        svg.appendChild(svgNode(documentRef, "rect", {
          x: x - 2.5,
          y: y - 2.5,
          width: 5,
          height: 5,
          rx: 1.35,
          class: `aeroplane-home-lane color-${color}`,
          "data-lane-index": index + 1,
        }));
      });
    });

    const centerPoints = [
      [50, 44], [56, 50], [50, 56], [44, 50],
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
    appendShortcutRoutes(documentRef, svg, state, quarterTurns);
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
          : plane.zone === "launch"
            ? `launch:${color}`
            : `${color}:${plane.zone}:${plane.plane_index}`;
      const group = grouped.get(key) || [];
      group.push(entry);
      grouped.set(key, group);
      entry.point = point;
    });
    grouped.forEach((group) => {
      const offsets = stackOffsets(group.length);
      group.forEach((entry, index) => {
        const offset = offsets[index] || [0, 0];
        entry.point = [entry.point[0] + offset[0], entry.point[1] + offset[1]];
        entry.stackIndex = index;
        entry.stackSize = group.length;
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
      token.dataset.stackIndex = String(entry.stackIndex || 0);
      token.dataset.stackSize = String(entry.stackSize || 1);
      token.style.setProperty("--stack-order", String(entry.stackIndex || 0));
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

  function turnCopy(context) {
    const phase = (context.state.flow || {}).phase;
    if (context.isTerminal || phase === "finished") return "本局已经结束";
    const current = (context.participants || []).find(
      (participant) => participant.player_id === context.room.current_player_id
    );
    const currentName = playerName(current);
    const viewerId = context.viewer && context.viewer.player_id;
    const viewerTurn = Boolean(viewerId && viewerId === context.room.current_player_id);
    if (phase === "awaiting_plane_choice") {
      return viewerTurn
        ? "轮到你选择飞机"
        : `等待${currentName}选择飞机`;
    }
    return viewerTurn ? "轮到你掷骰" : `等待${currentName}掷骰`;
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
    status.textContent = turnCopy(context);
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
    const {controls, state} = context;
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
      ? `本次 ${Number((state.last_roll || {}).value || 0)} 点 · ${turnCopy(context)}`
      : turnCopy(context);
    const streak = documentRef.createElement("span");
    const sixes = Number(state.consecutive_sixes || 0);
    streak.textContent = sixes
      ? `连续 6：${sixes} / 3${sixes === 2 ? "，下个 6 将触发惩罚" : ""}`
      : (phase === "awaiting_plane_choice"
        ? "点击棋盘上高亮的飞机完成行动"
        : "本回合掷骰");
    actionCopy.append(actionTitle, streak);
    const rollButton = documentRef.createElement("button");
    rollButton.type = "button";
    rollButton.className = "pixel-btn aeroplane-roll-button";
    const rollIsLegal = legalActions.some((action) => action.action === "roll");
    rollButton.disabled = !context.canMove || !rollIsLegal;
    if (rollButton.disabled) {
      rollButton.textContent = context.isTerminal || phase === "finished"
        ? "已结束"
        : (phase === "awaiting_plane_choice" ? "选飞机" : "等待");
    } else {
      rollButton.textContent = "掷骰";
      rollButton.classList.add("ready");
      rollButton.setAttribute("aria-label", "轮到你掷骰，点击掷骰");
    }
    rollButton.addEventListener("click", async () => {
      if (!context.helpers || typeof context.helpers.submitMove !== "function") return;
      if (typeof context.helpers.canMove === "function" && !context.helpers.canMove()) return;
      rollButton.disabled = true;
      const submitted = await context.helpers.submitMove({action: "roll"});
      if (!submitted) rollButton.disabled = !rollIsLegal;
    });
    actionPanel.append(die, actionCopy, rollButton);

    root.appendChild(actionPanel);
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

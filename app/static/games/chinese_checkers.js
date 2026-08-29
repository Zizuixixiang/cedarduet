(function registerChineseCheckersGameUI() {
  "use strict";

  const SVG_NS = "http://www.w3.org/2000/svg";
  const STYLE_ID = "duel-chinese-checkers-styles";
  const STYLE_HREF = "/static/games/chinese_checkers.css?v=0.1.0";

  function ensureStylesheet() {
    if (!document || !document.head || document.getElementById(STYLE_ID)) return;
    const link = document.createElement("link");
    link.id = STYLE_ID;
    link.rel = "stylesheet";
    link.href = STYLE_HREF;
    document.head.appendChild(link);
  }

  function rotateAxial(q, r, steps) {
    let rotatedQ = q;
    let rotatedR = r;
    for (let index = 0; index < ((steps % 6) + 6) % 6; index += 1) {
      [rotatedQ, rotatedR] = [-rotatedR, rotatedQ + rotatedR];
    }
    return {q: rotatedQ, r: rotatedR};
  }

  function visualPosition(node, rotationSteps) {
    const rotated = rotateAxial(Number(node.q), Number(node.r), rotationSteps);
    const x = rotated.q + rotated.r / 2;
    const y = rotated.r * Math.sqrt(3) / 2;
    return {
      q: rotated.q,
      r: rotated.r,
      left: 8 + ((x + 6) / 12) * 84,
      top: 5 + ((y + 4 * Math.sqrt(3)) / (8 * Math.sqrt(3))) * 90,
    };
  }

  function viewerPlayerId(context) {
    return String(
      (context.viewer && context.viewer.player_id)
      || (context.room && context.room.viewer && context.room.viewer.player_id)
      || ""
    );
  }

  function viewerToken(context, state) {
    const playerId = viewerPlayerId(context);
    return String(
      (state.tokens_by_player && state.tokens_by_player[playerId])
      || (state.marks_by_player && state.marks_by_player[playerId])
      || ""
    );
  }

  function movePayload(move) {
    return {from: move.from, to: move.to, kind: move.kind};
  }

  function sameMove(left, right) {
    return Boolean(
      left && right
      && left.from === right.from
      && left.to === right.to
      && (!left.kind || !right.kind || left.kind === right.kind)
    );
  }

  function currentPath(context, moves) {
    const pending = context.pendingMove;
    const selected = moves.find((move) => sameMove(move, pending));
    if (selected && Array.isArray(selected.path)) return selected.path;
    return Array.isArray(context.uiState.previewPath)
      ? context.uiState.previewPath
      : [];
  }

  function appendPathPreview(board, path, nodeById, positions) {
    if (!Array.isArray(path) || path.length < 2) return;
    const points = path
      .map((nodeId) => positions.get(nodeId))
      .filter(Boolean);
    if (points.length !== path.length) return;
    const svg = document.createElementNS(SVG_NS, "svg");
    svg.classList.add("cc-path-preview");
    svg.setAttribute("viewBox", "0 0 100 100");
    svg.setAttribute("preserveAspectRatio", "none");
    svg.setAttribute("aria-hidden", "true");
    const line = document.createElementNS(SVG_NS, "polyline");
    line.setAttribute(
      "points",
      points.map((point) => `${point.left},${point.top}`).join(" ")
    );
    svg.appendChild(line);
    points.forEach((point, index) => {
      const marker = document.createElementNS(SVG_NS, "circle");
      marker.setAttribute("cx", String(point.left));
      marker.setAttribute("cy", String(point.top));
      marker.setAttribute("r", index === 0 || index === points.length - 1 ? "0.72" : "0.48");
      marker.classList.add(index === 0 ? "path-origin" : "path-landing");
      svg.appendChild(marker);
    });
    board.appendChild(svg);
  }

  function ownerSeat(state, token) {
    const order = Array.isArray(state.participant_order)
      ? state.participant_order
      : [];
    const tokens = state.tokens_by_player || {};
    return order.findIndex((playerId) => tokens[playerId] === token);
  }

  function chooseOrigin(context, nodeId) {
    context.helpers.clearSelection({render: false});
    context.uiState.selectedNode = nodeId;
    delete context.uiState.previewPath;
    context.helpers.rerender();
  }

  function renderBoard(context) {
    ensureStylesheet();
    const {board, state, uiState, helpers} = context;
    const nodes = Array.isArray(state.nodes) ? state.nodes : [];
    const currentPlayerId = String(
      (context.room && context.room.current_player_id) || ""
    );
    const openingMoves = (
      state.legal_moves_by_player
      && Array.isArray(state.legal_moves_by_player[currentPlayerId])
    ) ? state.legal_moves_by_player[currentPlayerId] : null;
    const legalMoves = openingMoves || (
      Array.isArray(state.legal_moves)
        ? state.legal_moves
        : (Array.isArray(context.legalMoves) ? context.legalMoves : [])
    );
    if (nodes.length !== 121) return false;

    helpers.setBoardLayout({
      rows: 17,
      cols: 25,
      visualRows: 17,
      visualCols: 21,
      large: true,
      ariaLabel: "标准一百二十一孔中国跳棋六角星棋盘",
    });
    board.classList.add("chinese_checkers");

    const playerId = viewerPlayerId(context);
    const token = viewerToken(context, state);
    const startCamp = Number(
      state.start_camps_by_player && state.start_camps_by_player[playerId]
    );
    const targetCamp = Number(
      state.target_camps_by_player && state.target_camps_by_player[playerId]
    );
    const rotationSteps = Number.isInteger(startCamp)
      ? (3 - startCamp + 6) % 6
      : 0;
    const selectedNode = String(
      uiState.selectedNode
      || (context.pendingMove && context.pendingMove.from)
      || ""
    );
    const originMoves = selectedNode
      ? legalMoves.filter((move) => move.from === selectedNode)
      : [];
    const legalOrigins = new Set(legalMoves.map((move) => move.from));
    const nodeById = new Map(nodes.map((node) => [node.id, node]));
    const positions = new Map(
      nodes.map((node) => [node.id, visualPosition(node, rotationSteps)])
    );
    const pieces = state.pieces || {};
    const lastMove = state.last_move || {};
    const movable = Boolean(context.canMove && token);

    board.dataset.viewerPlayerId = playerId;
    board.dataset.viewerToken = token;
    board.dataset.startCamp = Number.isInteger(startCamp) ? String(startCamp) : "";
    board.dataset.targetCamp = Number.isInteger(targetCamp) ? String(targetCamp) : "";
    board.dataset.rotationSteps = String(rotationSteps);
    board.style.setProperty("--viewer-rotation", `${rotationSteps * 60}deg`);

    const surface = document.createElement("span");
    surface.className = "cc-star-surface";
    surface.setAttribute("aria-hidden", "true");
    board.appendChild(surface);

    appendPathPreview(
      board,
      currentPath(context, legalMoves),
      nodeById,
      positions
    );

    nodes.forEach((node) => {
      const position = positions.get(node.id);
      const owner = pieces[node.id] || null;
      const seat = ownerSeat(state, owner);
      const targetMove = originMoves.find((move) => move.to === node.id) || null;
      const selectableOrigin = Boolean(
        movable && owner === token && legalOrigins.has(node.id)
      );
      const selectableTarget = Boolean(movable && targetMove);
      const selectedOrigin = node.id === selectedNode;
      const pendingTarget = Boolean(
        context.pendingMove && context.pendingMove.to === node.id
      );
      const hole = document.createElement("button");
      hole.type = "button";
      hole.className = "cc-hole";
      hole.dataset.nodeId = node.id;
      hole.dataset.q = String(node.q);
      hole.dataset.r = String(node.r);
      hole.dataset.displayQ = String(position.q);
      hole.dataset.displayR = String(position.r);
      hole.style.setProperty("--node-left", `${position.left}%`);
      hole.style.setProperty("--node-top", `${position.top}%`);
      if (Number.isInteger(node.camp)) hole.classList.add(`camp-${node.camp}`);
      if (node.camp === startCamp) hole.classList.add("viewer-start-camp");
      if (node.camp === targetCamp) hole.classList.add("viewer-target-camp");
      if (owner) hole.classList.add("occupied", `owner-seat-${seat}`);
      if (selectedOrigin) hole.classList.add("selected-origin");
      if (pendingTarget) hole.classList.add("selected-target");
      if (targetMove) {
        hole.classList.add(
          "legal-target",
          targetMove.kind === "jump" ? "jump-target" : "step-target"
        );
      }
      if (lastMove.from === node.id) hole.classList.add("last-move-from");
      if (lastMove.to === node.id) hole.classList.add("last-move-to");
      if (owner === token && node.camp === targetCamp) {
        hole.classList.add("target-arrived");
      }
      hole.disabled = !selectableOrigin && !selectableTarget;
      hole.setAttribute("aria-pressed", String(selectedOrigin || pendingTarget));
      hole.setAttribute(
        "aria-label",
        `${node.id}，轴坐标 ${node.q},${node.r}`
        + `${node.camp === startCamp ? "，你的起始营" : ""}`
        + `${node.camp === targetCamp ? "，你的目标营" : ""}`
        + `${owner ? `，第 ${seat + 1} 席弹珠` : "，空孔"}`
        + `${targetMove ? `，合法${targetMove.kind === "jump" ? "跳跃" : "相邻一步"}终点` : ""}`
        + `${lastMove.from === node.id ? "，上一手起点" : ""}`
        + `${lastMove.to === node.id ? "，上一手终点" : ""}`
      );

      if (targetMove) {
        const targetMarker = document.createElement("span");
        targetMarker.className = targetMove.kind === "jump"
          ? "cc-legal-marker jump-marker"
          : "cc-legal-marker step-marker";
        targetMarker.setAttribute("aria-hidden", "true");
        hole.appendChild(targetMarker);
      }
      if (owner) {
        const marble = document.createElement("span");
        marble.className = `cc-marble seat-${seat}`;
        marble.dataset.owner = owner;
        marble.setAttribute("aria-hidden", "true");
        const gleam = document.createElement("span");
        gleam.className = "cc-marble-gleam";
        marble.appendChild(gleam);
        hole.appendChild(marble);
      }
      if (lastMove.to === node.id) {
        const marker = document.createElement("span");
        marker.className = "cc-last-move-marker";
        marker.setAttribute("aria-hidden", "true");
        hole.appendChild(marker);
      }

      hole.addEventListener("click", () => {
        if (!context.helpers.canMove()) return;
        if (selectableOrigin) {
          if (selectedOrigin && !context.pendingMove) {
            context.helpers.clearSelection();
          } else {
            chooseOrigin(context, node.id);
          }
          return;
        }
        if (!targetMove) return;
        uiState.selectedNode = targetMove.from;
        uiState.previewPath = Array.isArray(targetMove.path)
          ? [...targetMove.path]
          : [targetMove.from, targetMove.to];
        helpers.selectMove(movePayload(targetMove));
      });
      board.appendChild(hole);
    });

    const progress = Number(
      state.target_progress_by_player
      && state.target_progress_by_player[playerId]
    );
    const badge = document.createElement("span");
    badge.className = "cc-progress-badge";
    badge.setAttribute("role", "status");
    badge.textContent = `目标营 ${Number.isFinite(progress) ? progress : 0} / 10`;
    board.appendChild(badge);
    return true;
  }

  function renderControls(context) {
    ensureStylesheet();
    const controls = context.controls;
    const legend = document.createElement("div");
    legend.className = "cc-legend";
    const step = document.createElement("span");
    step.className = "cc-legend-item step";
    step.textContent = "相邻一步";
    const jump = document.createElement("span");
    jump.className = "cc-legend-item jump";
    jump.textContent = "连续跳终点";
    const path = document.createElement("span");
    path.className = "cc-legend-item path";
    path.textContent = "细线为服务端 canonical path";
    legend.append(step, jump, path);
    controls.appendChild(legend);
  }

  ensureStylesheet();
  const renderer = {
    usesStandardMoveConfirmation: true,
    ensureStylesheet,
    rotateAxial,
    renderBoard,
    renderControls,
  };
  if (window.DuelGameUI && typeof window.DuelGameUI.register === "function") {
    window.DuelGameUI.register("chinese_checkers", renderer);
  } else {
    window.DuelGameUIPending = window.DuelGameUIPending || [];
    window.DuelGameUIPending.push(["chinese_checkers", renderer]);
  }
}());

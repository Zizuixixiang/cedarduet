(function registerUnoRenderer() {
  "use strict";

  const STYLE_ID = "duel-game-uno-styles";
  const STYLE_HREF = "/static/games/uno.css?v=1.0.1";
  const COLORS = ["red", "yellow", "green", "blue"];
  const COLOR_LABELS = {
    red: "红色",
    yellow: "黄色",
    green: "绿色",
    blue: "蓝色",
  };
  const KIND_LABELS = {
    skip: "禁",
    reverse: "转",
    draw_two: "+2",
    wild: "W",
    wild_draw_four: "+4",
  };
  const KIND_NAMES = {
    skip: "Skip",
    reverse: "Reverse",
    draw_two: "Draw Two",
    wild: "Wild",
    wild_draw_four: "Wild Draw Four",
  };

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
    link.dataset.duelGameStyle = "uno";
    documentRef.head.appendChild(link);
    return link;
  }

  function participantName(context, playerId) {
    const participant = (context.participants || []).find(
      (item) => item.player_id === playerId
    );
    return participant
      ? (participant.display_name || participant.player_id)
      : (playerId || "玩家");
  }

  function renderAvatar(documentRef, context, participant) {
    const avatar = documentRef.createElement("span");
    avatar.className = "uno-opponent-avatar";
    const name = participant && (participant.display_name || participant.player_id) || "玩家";
    avatar.textContent = Array.from(String(name).trim())[0] || "?";
    if (
      context.helpers
      && typeof context.helpers.renderParticipantAvatar === "function"
    ) {
      context.helpers.renderParticipantAvatar(avatar, participant);
    }
    return avatar;
  }

  function cardSymbol(card) {
    if (!card) return "?";
    return card.kind === "number"
      ? String(card.value)
      : (KIND_LABELS[card.kind] || "?");
  }

  function cardName(card) {
    if (!card) return "未知牌";
    const symbol = card.kind === "number"
      ? String(card.value)
      : (KIND_NAMES[card.kind] || card.kind);
    const color = COLOR_LABELS[card.color];
    return color ? `${color} ${symbol}` : symbol;
  }

  function createCard(documentRef, card, options = {}) {
    const interactive = Boolean(options.interactive);
    const node = documentRef.createElement(interactive ? "button" : "div");
    if (interactive) node.type = "button";
    const colorClass = COLORS.includes(card && card.color)
      ? ` color-${card.color}`
      : " color-wild";
    node.className = `uno-card${colorClass}${options.back ? " is-back" : ""}`;
    if (options.selected) node.classList.add("selected");
    if (options.legal) node.classList.add("legal");
    if (options.back) {
      node.setAttribute("aria-label", "UNO 牌背");
      const backMark = documentRef.createElement("span");
      backMark.className = "uno-back-mark";
      backMark.textContent = "UNO";
      backMark.setAttribute("aria-hidden", "true");
      node.appendChild(backMark);
      return node;
    }
    node.dataset.cardId = card.id || "";
    const cornerTop = documentRef.createElement("span");
    cornerTop.className = "uno-card-corner top";
    cornerTop.textContent = cardSymbol(card);
    const face = documentRef.createElement("span");
    face.className = "uno-card-face";
    face.textContent = cardSymbol(card);
    const cornerBottom = documentRef.createElement("span");
    cornerBottom.className = "uno-card-corner bottom";
    cornerBottom.textContent = cardSymbol(card);
    cornerTop.setAttribute("aria-hidden", "true");
    face.setAttribute("aria-hidden", "true");
    cornerBottom.setAttribute("aria-hidden", "true");
    node.append(cornerTop, face, cornerBottom);
    node.setAttribute("aria-label", cardName(card));
    return node;
  }

  function appendOpponents(documentRef, shell, context) {
    const viewerId = context.viewer && context.viewer.player_id;
    const counts = context.state.hand_counts || {};
    const opponents = documentRef.createElement("section");
    opponents.className = "uno-opponents";
    opponents.setAttribute("aria-label", "其他玩家手牌数量");
    (context.participants || []).filter(
      (participant) => participant.player_id !== viewerId
    ).forEach((participant) => {
      const count = Number(counts[participant.player_id] || 0);
      const seat = documentRef.createElement("article");
      seat.className = "uno-opponent";
      if (context.room.current_player_id === participant.player_id) {
        seat.classList.add("current");
      }
      const identity = documentRef.createElement("div");
      identity.className = "uno-opponent-identity";
      const heading = documentRef.createElement("strong");
      heading.textContent = participant.display_name || participant.player_id;
      identity.append(renderAvatar(documentRef, context, participant), heading);
      const cards = documentRef.createElement("div");
      cards.className = "uno-opponent-backs";
      for (let index = 0; index < Math.min(3, count); index += 1) {
        const back = createCard(documentRef, null, {back: true});
        back.classList.add("compact");
        cards.appendChild(back);
      }
      const amount = documentRef.createElement("span");
      amount.className = "uno-hand-count";
      amount.textContent = `${count} 张`;
      seat.setAttribute("aria-label", `${heading.textContent}，手牌 ${count} 张`);
      seat.append(identity, cards, amount);
      opponents.appendChild(seat);
    });
    shell.appendChild(opponents);
  }

  function appendStatus(documentRef, shell, context) {
    const state = context.state;
    const status = documentRef.createElement("section");
    status.className = "uno-status";
    status.setAttribute("aria-label", "UNO 当前状态");
    const color = documentRef.createElement("span");
    color.className = `uno-current-color color-${state.current_color}`;
    color.textContent = `当前：${COLOR_LABELS[state.current_color] || "未定"}`;
    const direction = documentRef.createElement("span");
    direction.className = "uno-direction";
    direction.dataset.direction = Number(state.direction) < 0 ? "reverse" : "forward";
    direction.textContent = Number(state.direction) < 0 ? "逆时针" : "顺时针";
    const turn = documentRef.createElement("span");
    turn.className = "uno-turn";
    turn.textContent = context.room.winner_player_id
      ? `${participantName(context, context.room.winner_player_id)} 获胜`
      : `轮到 ${participantName(context, context.room.current_player_id)}`;
    status.append(color, direction, turn);
    shell.appendChild(status);
  }

  function legalAction(context, actionName) {
    return (context.legalActions || []).find(
      (action) => action.action === actionName
    ) || null;
  }

  function saveHandScroll(context, scroller) {
    const scrollLeft = Number(scroller.scrollLeft);
    if (Number.isFinite(scrollLeft)) {
      context.uiState.unoHandScrollLeft = Math.max(0, scrollLeft);
    }
  }

  function restoreHandScroll(context, scroller) {
    const scrollLeft = Number(context.uiState.unoHandScrollLeft);
    if (Number.isFinite(scrollLeft) && scrollLeft >= 0) {
      scroller.scrollLeft = scrollLeft;
    }
  }

  function appendCenter(documentRef, shell, context) {
    const state = context.state;
    const center = documentRef.createElement("section");
    center.className = "uno-center";
    const deckArea = documentRef.createElement("div");
    deckArea.className = "uno-pile";
    const deckLabel = documentRef.createElement("span");
    deckLabel.textContent = `摸牌堆 · ${Number(state.deck_count || 0)} 张`;
    const drawMove = legalAction(context, "draw");
    const deck = createCard(documentRef, null, {back: true, interactive: true});
    deck.classList.add("uno-deck");
    deck.disabled = !context.canMove || !drawMove;
    deck.setAttribute(
      "aria-label",
      drawMove ? `从摸牌堆摸一张，剩余 ${state.deck_count} 张` : `摸牌堆，剩余 ${state.deck_count} 张`
    );
    deck.addEventListener("click", async () => {
      if (!context.helpers.canMove() || !drawMove) return;
      deck.disabled = true;
      await context.helpers.submitMove(drawMove);
    });
    deckArea.append(deck, deckLabel);

    const discardArea = documentRef.createElement("div");
    discardArea.className = "uno-pile";
    const discard = createCard(documentRef, state.top_discard || {});
    discard.classList.add("uno-discard");
    const discardLabel = documentRef.createElement("span");
    discardLabel.textContent = "弃牌堆顶";
    discardArea.append(discard, discardLabel);
    center.append(deckArea, discardArea);
    shell.appendChild(center);
  }

  function appendAlerts(documentRef, shell, context) {
    const state = context.state;
    const alerts = documentRef.createElement("section");
    alerts.className = "uno-alerts";
    alerts.setAttribute("aria-live", "polite");
    const pending = state.penalty_state
      && state.penalty_state.pending_wild_draw_four;
    if (pending) {
      const notice = documentRef.createElement("p");
      notice.className = "uno-alert penalty";
      notice.textContent = (
        `${participantName(context, pending.offender_player_id)} 打出 +4，`
        + `${participantName(context, pending.challenger_player_id)} 可选择质疑或摸 4 张。`
      );
      alerts.appendChild(notice);
    }
    const unoWindow = state.uno_state && state.uno_state.window;
    if (unoWindow) {
      const notice = documentRef.createElement("p");
      notice.className = "uno-alert uno";
      notice.textContent = (
        `${participantName(context, unoWindow.offender_player_id)} 未宣告 UNO；`
        + `${participantName(context, unoWindow.catcher_player_id)} 可在其他动作前抓漏报。`
      );
      alerts.appendChild(notice);
    }
    const challenge = state.penalty_state && state.penalty_state.last_challenge;
    if (!pending && challenge) {
      const notice = documentRef.createElement("p");
      notice.className = "uno-alert resolved";
      if (challenge.challenged) {
        notice.textContent = challenge.challenge_succeeded
          ? `+4 质疑成功：出牌者摸 ${challenge.draw_count} 张。`
          : `+4 质疑失败：质疑者摸 ${challenge.draw_count} 张并失去回合。`;
      } else {
        notice.textContent = `未质疑 +4：目标玩家摸 ${challenge.draw_count} 张并失去回合。`;
      }
      alerts.appendChild(notice);
    }
    if (alerts.children.length) shell.appendChild(alerts);
  }

  function appendHand(documentRef, shell, context) {
    const state = context.state;
    const privateState = context.privateState || {};
    const hand = Array.isArray(privateState.hand) ? privateState.hand : [];
    const actions = Array.isArray(context.legalActions) ? context.legalActions : [];
    const playableById = new Map();
    actions.filter((action) => action.action === "play").forEach((action) => {
      const values = playableById.get(action.card_id) || [];
      values.push(action);
      playableById.set(action.card_id, values);
    });
    if (
      context.uiState.selectedCardId
      && !playableById.has(context.uiState.selectedCardId)
    ) {
      delete context.uiState.selectedCardId;
      delete context.uiState.selectedColor;
    }
    const area = documentRef.createElement("section");
    area.className = "uno-hand-area";
    const heading = documentRef.createElement("div");
    heading.className = "uno-hand-heading";
    const title = documentRef.createElement("strong");
    title.textContent = "你的手牌";
    const count = documentRef.createElement("span");
    count.textContent = `${hand.length} 张`;
    heading.append(title, count);
    const scroller = documentRef.createElement("div");
    scroller.className = "uno-hand-scroll";
    scroller.setAttribute("role", "group");
    scroller.setAttribute("aria-label", `你的手牌，共 ${hand.length} 张`);
    scroller.addEventListener("scroll", () => saveHandScroll(context, scroller), {passive: true});
    hand.forEach((card, index) => {
      const legal = playableById.has(card.id);
      const selected = context.uiState.selectedCardId === card.id;
      const cardNode = createCard(documentRef, card, {
        interactive: true,
        legal,
        selected,
      });
      cardNode.setAttribute("aria-pressed", String(selected));
      cardNode.style.setProperty("--fan-index", String(index - (hand.length - 1) / 2));
      cardNode.disabled = !context.canMove || !legal;
      cardNode.addEventListener("click", () => {
        if (!context.helpers.canMove() || !legal) return;
        saveHandScroll(context, scroller);
        if (context.uiState.selectedCardId === card.id) {
          delete context.uiState.selectedCardId;
          delete context.uiState.selectedColor;
        } else {
          context.uiState.selectedCardId = card.id;
          delete context.uiState.selectedColor;
        }
        context.helpers.rerender();
      });
      scroller.appendChild(cardNode);
    });
    if (!hand.length) {
      const empty = documentRef.createElement("p");
      empty.className = "uno-empty-hand";
      empty.textContent = context.room.status === "finished" ? "手牌已出完" : "没有可显示的手牌";
      scroller.appendChild(empty);
    }
    area.append(heading, scroller);
    shell.appendChild(area);
    return scroller;
  }

  function renderBoard(context) {
    const documentRef = context.board.ownerDocument || window.document;
    ensureStylesheet(documentRef);
    context.board.classList.add("uno");
    context.board.dataset.direction = Number(context.state.direction) < 0
      ? "reverse" : "forward";
    context.board.setAttribute("aria-label", "UNO 牌桌");
    const shell = documentRef.createElement("div");
    shell.className = "uno-table";
    appendOpponents(documentRef, shell, context);
    appendStatus(documentRef, shell, context);
    appendCenter(documentRef, shell, context);
    appendAlerts(documentRef, shell, context);
    const handScroller = appendHand(documentRef, shell, context);
    context.board.appendChild(shell);
    restoreHandScroll(context, handScroller);
    return true;
  }

  function makeActionButton(documentRef, label, className, action, context) {
    const button = documentRef.createElement("button");
    button.type = "button";
    button.className = `uno-action ${className}`;
    button.textContent = label;
    button.disabled = !context.canMove || !action;
    button.addEventListener("click", async () => {
      if (!context.helpers.canMove() || !action) return;
      button.disabled = true;
      const submitted = await context.helpers.submitMove(action);
      if (!submitted) button.disabled = false;
    });
    return button;
  }

  function renderControls(context) {
    const documentRef = context.controls.ownerDocument || window.document;
    ensureStylesheet(documentRef);
    const actions = Array.isArray(context.legalActions) ? context.legalActions : [];
    const controls = documentRef.createElement("section");
    controls.className = "uno-controls";
    controls.setAttribute("aria-label", "UNO 操作");

    const catchAction = actions.find((action) => action.action === "catch_uno");
    const challengeAction = actions.find(
      (action) => action.action === "challenge_wild_draw_four"
    );
    const acceptAction = actions.find(
      (action) => action.action === "accept_draw_four"
    );
    if (catchAction) {
      controls.appendChild(makeActionButton(
        documentRef, "抓 UNO：漏报者摸 2", "catch", catchAction, context
      ));
    }
    if (challengeAction || acceptAction) {
      const challengeGroup = documentRef.createElement("div");
      challengeGroup.className = "uno-challenge-actions";
      challengeGroup.append(
        makeActionButton(documentRef, "质疑 +4", "challenge", challengeAction, context),
        makeActionButton(documentRef, "不质疑，摸 4", "accept", acceptAction, context)
      );
      controls.appendChild(challengeGroup);
    }

    const selectedId = context.uiState.selectedCardId;
    const selectedActions = actions.filter(
      (action) => action.action === "play" && action.card_id === selectedId
    );
    if (selectedActions.length) {
      const playPanel = documentRef.createElement("div");
      playPanel.className = "uno-play-panel";
      const wild = selectedActions.some((action) => COLORS.includes(action.color));
      if (wild) {
        const colorLabel = documentRef.createElement("strong");
        colorLabel.textContent = "为 Wild 选择颜色";
        const colorChoices = documentRef.createElement("div");
        colorChoices.className = "uno-color-choices";
        COLORS.filter((color) => selectedActions.some(
          (action) => action.color === color
        )).forEach((color) => {
          const colorButton = documentRef.createElement("button");
          colorButton.type = "button";
          colorButton.className = `uno-color-choice color-${color}`;
          colorButton.textContent = COLOR_LABELS[color];
          const selected = context.uiState.selectedColor === color;
          colorButton.setAttribute("aria-pressed", String(selected));
          if (selected) colorButton.classList.add("selected");
          colorButton.addEventListener("click", () => {
            context.uiState.selectedColor = color;
            context.helpers.rerender();
          });
          colorChoices.appendChild(colorButton);
        });
        playPanel.append(colorLabel, colorChoices);
      }
      const chosen = selectedActions.filter((action) => (
        wild ? action.color === context.uiState.selectedColor : !action.color
      ));
      const plainPlay = chosen.find((action) => action.uno !== true);
      const unoPlay = chosen.find((action) => action.uno === true);
      const submitGroup = documentRef.createElement("div");
      submitGroup.className = "uno-submit-actions";
      submitGroup.appendChild(makeActionButton(
        documentRef, "出这张牌", "play", plainPlay, context
      ));
      if (unoPlay) {
        submitGroup.appendChild(makeActionButton(
          documentRef, "宣告 UNO 并出牌", "declare", unoPlay, context
        ));
      }
      playPanel.appendChild(submitGroup);
      controls.appendChild(playPanel);
    } else if (!challengeAction && !acceptAction) {
      const hint = documentRef.createElement("p");
      hint.className = "uno-control-hint";
      hint.textContent = context.canMove
        ? "选择一张高亮手牌，或点击摸牌堆。"
        : "等待当前玩家行动。";
      controls.appendChild(hint);
    }

    const passAction = actions.find((action) => action.action === "pass");
    if (passAction) {
      controls.appendChild(makeActionButton(
        documentRef, "保留刚摸的牌，结束回合", "pass", passAction, context
      ));
    }
    context.controls.appendChild(controls);
  }

  const renderer = {
    participantPresentation: "embedded",
    ownsPrivateStatePresentation: true,
    usesStandardMoveConfirmation: false,
    renderBoard,
    renderControls,
  };

  window.DuelGameUI.register("uno", renderer);
}());

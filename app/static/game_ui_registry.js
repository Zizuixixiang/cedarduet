(function installDuelGameUI(global) {
  "use strict";

  const renderers = new Map();
  const scriptLoads = new Map();
  const GAME_TYPE_PATTERN = /^[a-z0-9][a-z0-9_-]{0,63}$/;
  const PARTICIPANT_PRESENTATIONS = new Set([
    "generic", "embedded", "board-edge",
  ]);

  function normalizedGameType(gameType) {
    const value = typeof gameType === "string" ? gameType.trim() : "";
    if (!GAME_TYPE_PATTERN.test(value)) {
      throw new TypeError(
        "DuelGameUI gameType must use 1-64 lowercase letters, numbers, _ or -"
      );
    }
    return value;
  }

  function register(gameType, renderer) {
    const key = normalizedGameType(gameType);
    if (!renderer || typeof renderer !== "object") {
      throw new TypeError("DuelGameUI renderer must be an object");
    }
    if (typeof renderer.renderBoard !== "function") {
      throw new TypeError("DuelGameUI renderer.renderBoard(context) is required");
    }
    if (
      renderer.participantPresentation !== undefined
      && !PARTICIPANT_PRESENTATIONS.has(renderer.participantPresentation)
    ) {
      throw new TypeError(
        "DuelGameUI renderer.participantPresentation must be generic, embedded or board-edge"
      );
    }
    if (renderers.has(key)) {
      throw new Error(`DuelGameUI renderer already registered for ${key}`);
    }
    renderers.set(key, renderer);
    return renderer;
  }

  function get(gameType) {
    if (typeof gameType !== "string") return null;
    const key = gameType.trim();
    if (!GAME_TYPE_PATTERN.test(key)) return null;
    return renderers.get(key) || null;
  }

  function load(gameType, scriptUrl = null) {
    const key = normalizedGameType(gameType);
    const registered = get(key);
    if (registered) return Promise.resolve(registered);
    if (scriptLoads.has(key)) return scriptLoads.get(key);

    const source = scriptUrl || `/static/games/${encodeURIComponent(key)}.js`;
    const loading = new Promise((resolve, reject) => {
      const script = global.document.createElement("script");
      script.src = source;
      script.async = true;
      script.dataset.duelGameUi = key;
      script.addEventListener("load", () => {
        const loadedRenderer = get(key);
        if (loadedRenderer) {
          resolve(loadedRenderer);
        } else {
          reject(new Error(
            `Game UI script ${source} did not register ${key}`
          ));
        }
      }, {once: true});
      script.addEventListener("error", () => {
        reject(new Error(`Unable to load game UI script ${source}`));
      }, {once: true});
      global.document.head.appendChild(script);
    });
    scriptLoads.set(key, loading);
    return loading;
  }

  global.DuelGameUI = Object.freeze({
    version: 1,
    register,
    get,
    load,
    has(gameType) {
      return Boolean(get(gameType));
    },
    registeredGameTypes() {
      return [...renderers.keys()];
    },
  });
}(window));

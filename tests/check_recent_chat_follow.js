// Local DOM regression; geometry/ResizeObserver are simulated, not visual QA.
// node tests/check_recent_chat_follow.js (requires jsdom)
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, '..');

(async () => {
  const {JSDOM} = await import('jsdom');
  let cases = 0;
  for (const hasObserver of [true, false]) {
    const dom = new JSDOM(fs.readFileSync(path.join(root, 'app/static/index.html'), 'utf8'), {
      runScripts: 'outside-only', url: 'http://duel.test', pretendToBeVisual: true,
    });
    const {window} = dom;
    try {
      window.setInterval = () => 0;
      window.fetch = () => { throw new Error('No network allowed in this test'); };
      const list = window.document.getElementById('recentChatMessages');
      let height = 100;
      let rowHeight = 30;
      let top = 0;
      let writes = 0;
      const maxTop = () => Math.max(0, list.scrollHeight - height);
      Object.defineProperties(list, {
        clientHeight: {get: () => height},
        scrollHeight: {get: () => Math.max(height, list.children.length * rowHeight)},
        scrollTop: {
          get: () => (top = Math.max(0, Math.min(top, maxTop()))),
          set: value => { writes++; top = Math.max(0, Math.min(value, maxTop())); },
        },
      });
      window.HTMLElement.prototype.getBoundingClientRect = function () {
        const y = this.parentElement === list
          ? [...list.children].indexOf(this) * rowHeight - list.scrollTop : 0;
        return {top: y, bottom: y + rowHeight};
      };
      const observers = [];
      if (hasObserver) window.ResizeObserver = class {
        constructor(callback) { this.callback = callback; observers.push(this); }
        observe(target) { assert.equal(target, list); }
      };
      let scrollListeners = 0;
      let resizeListeners = 0;
      const addListListener = list.addEventListener.bind(list);
      list.addEventListener = (type, ...args) => {
        if (type === 'scroll') scrollListeners++;
        addListListener(type, ...args);
      };
      const addWindowListener = window.addEventListener.bind(window);
      window.addEventListener = (type, ...args) => {
        if (type === 'resize') resizeListeners++;
        addWindowListener(type, ...args);
      };
      for (const name of ['game_ui_registry.js', 'app.js']) {
        let source = fs.readFileSync(path.join(root, 'app/static', name), 'utf8');
        if (name === 'app.js') source = source.replace(/void \(async \(\) => \{\n  const invite[\s\S]*$/, '')
          + '\nwindow.run = source => eval(source);';
        window.eval(source);
      }
      const initialResizeListeners = resizeListeners;
      window.run(`
        identity = {human_player_id: 'human:1', human_name: '玩家一', machines: [], games: []};
        room = {room_id: 'first', revision: 1, status: 'playing', room_kind: 'ordinary',
          game_type: 'gomoku', game_name: '五子棋', human_player_id: 'human:1', ai_player_id: 'ai:2',
          current_player_id: 'human:1', turn: 'human', rules_text: '测试规则',
          viewer: {player_id: 'human:1', role: 'human', can_move: true, is_participant: true},
          participants: [
            {player_id: 'human:1', display_name: '玩家一', role: 'human', seat_index: 0},
            {player_id: 'ai:2', display_name: '小机', role: 'ai', seat_index: 1}],
          board_state: {size: 15, board: Array.from({length: 15}, () => Array(15).fill(null))}};
        currentTimeline = Array.from({length: 40}, (_, i) => ({sequence: i + 1,
          event_type: 'message', sender_role: 'human', sender_player_id: 'human:1',
          sender: {role: 'human', name: '玩家一'}, text: '消息 ' + (i + 1), is_public: true}));
      `);
      const render = () => window.run('renderRecentChat(currentTimeline)');
      const fullRender = () => window.run('renderGame(room, "", currentTimeline); hideWaitModeModal()');
      const append = () => window.run(`
        currentTimeline.push({...currentTimeline.at(-1), sequence: currentTimeline.at(-1).sequence + 1});
        renderRecentChat(currentTimeline);
      `);
      const scroll = () => list.dispatchEvent(new window.Event('scroll'));
      const resize = () => hasObserver ? observers[0].callback() : window.dispatchEvent(new window.Event('resize'));
      const bottom = label => { assert.equal(list.scrollTop, maxTop(), label); cases++; };
      fullRender();
      bottom('first room opens at bottom');
      append();
      bottom('new messages follow');
      height = 45; // No scroll or ResizeObserver event before the message arrives.
      append();
      bottom('container shrink before delivery must not look like manual scrolling');
      rowHeight = 50; // Width change wraps messages, also before observer delivery.
      append();
      bottom('viewport reflow before delivery retains follow mode');
      height = 20;
      resize();
      bottom('resize alone pins the bottom');
      height = 300;
      scroll(); // Browser clamps scrollTop before ResizeObserver runs.
      resize();
      append();
      bottom('growth and its clamped scroll event retain follow mode');

      list.scrollTop = 125;
      scroll();
      append();
      assert.equal(list.scrollTop, 125, 'manual reading survives a new message'); cases++;
      fullRender();
      assert.equal(list.scrollTop, 125, 'ordinary full refresh preserves reading'); cases++;
      height = 80;
      resize();
      append();
      assert.equal(list.scrollTop, 125, 'resize while reading does not enable follow'); cases++;
      window.run('currentTimeline = currentTimeline.slice(1)');
      render();
      assert.equal(list.scrollTop, 75, 'server history trimming preserves visible message offset'); cases++;
      scroll(); // Delayed event from the programmatic anchor restoration.
      append();
      assert.equal(list.scrollTop, 75, 'restoration does not re-enable follow'); cases++;

      list.scrollTop = list.scrollHeight;
      scroll();
      append();
      bottom('manually returning to bottom resumes follow');
      list.scrollTop -= 10;
      scroll();
      const nearBottomReading = list.scrollTop;
      append();
      assert.equal(list.scrollTop, nearBottomReading, 'small upward scroll also pauses follow'); cases++;
      list.scrollTop = list.scrollHeight;
      scroll();
      list.scrollTop = 125; // User moves before scroll-event delivery.
      resize();
      append();
      assert.equal(list.scrollTop, 125, 'pending manual scroll wins over resize'); cases++;
      height = list.scrollHeight - 100;
      scroll(); // Reading position clamps to the bottom, but user did not return.
      resize();
      height = 100;
      resize();
      append();
      assert.equal(list.scrollTop, 100, 'layout clamp must not re-enable follow'); cases++;

      window.run('renderGame({...room, room_id: "second"}, "", currentTimeline); hideWaitModeModal()');
      bottom('switching rooms resets reading mode');
      scroll();
      append();
      bottom('old queued scroll event cannot disable the new room follow mode');
      window.run('currentTimeline = currentTimeline.slice(0, 1)');
      render();
      bottom('short list has no overflow');
      rowHeight = 200;
      resize();
      bottom('previously short list follows when viewport reflow creates overflow');
      height = 0;
      resize();
      height = 100;
      resize();
      bottom('hidden then visible list follows');
      render();
      const beforeIdle = writes;
      scroll();
      assert.equal(writes, beforeIdle, 'scroll listener does not write or start a feedback loop'); cases++;
      assert.equal(scrollListeners, 1, 'one list listener across rooms and renders');
      assert.equal(observers.length, hasObserver ? 1 : 0);
      assert.equal(resizeListeners - initialResizeListeners, hasObserver ? 0 : 1);
      cases++;
    } finally {
      dom.window.close();
    }
  }
  // Source contract only: jsdom cannot establish rendered CSS alignment.
  const css = fs.readFileSync(path.join(root, 'app/static/styles.css'), 'utf8');
  assert.match(css, /\.recent-chat-messages\s*\{[^}]*align-content:\s*safe end;/);
  console.log(JSON.stringify({ok: true, cases, shortListAlignment: 'CSS source only', realLayoutVerified: false}));
})().catch(error => { console.error(error); process.exitCode = 1; });

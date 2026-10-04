// Focused DOM regression: local fixtures only, no server/database required.
// node tests/check_chat_scroll.js (requires jsdom; geometry is simulated)
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {spawnSync} = require('node:child_process');
const root = path.resolve(__dirname, '..');
const generated = spawnSync(process.env.PYTHON || '.venv/bin/python', ['-c', `
import json, random
from app.games.monopoly import Monopoly
ps = [dict(player_id=pid, display_name=name, role=role, participant_kind=kind,
           seat_index=i, handle=handle, join_status='joined')
      for i, (pid, name, role, kind, handle) in enumerate([
          ('human:1', '玩家一', 'human', 'human', '1'),
          ('ai:2', '小机', 'ai', 'bound_machine', '2'),
          ('human:3', '玩家三', 'human', 'human', '3'),
          ('npc:4', 'NPC', 'ai', 'system_npc', None)])]
g = Monopoly(random.Random(1))
s = g.initialize(ps)
print(json.dumps(dict(participants=ps, board_state=g.public_state(s, ps),
                     private_state=g.private_state(s, ps[0], ps))))
`], {cwd: root, encoding: 'utf8'});
assert.equal(generated.status, 0, generated.stderr);
const fixture = JSON.parse(generated.stdout);

(async () => {
  const {JSDOM} = await import('jsdom');
  let cases = 0;
  for (const invite of [false, true]) {
    const dom = new JSDOM(fs.readFileSync(path.join(root, 'app/static/index.html'), 'utf8'), {
      runScripts: 'outside-only', url: 'http://duel.test', pretendToBeVisual: true,
    });
    const {window} = dom;
    const doc = window.document;
    const $ = id => doc.getElementById(id);
    window.setInterval = () => 0;
    window.scrollTo = ({left, top, behavior}) => {
      assert.equal(behavior, 'instant');
      window.scrollX = left;
      window.scrollY = top;
    };
    for (const name of ['game_ui_registry.js', 'games/monopoly.js', 'app.js']) {
      let source = fs.readFileSync(path.join(root, 'app/static', name), 'utf8');
      if (name === 'app.js') source = source.replace(/void \(async \(\) => \{\n  const invite[\s\S]*$/, '')
        + '\nwindow.run = source => eval(source);';
      window.eval(source);
    }
    const list = $('recentChatMessages');
    // Model a scrollable feed with fixed row heights to exercise its actual
    // follow-bottom and visible-message anchor logic without a layout engine.
    let chatScroll = 0;
    Object.defineProperties(list, {
      clientHeight: {get: () => 100},
      scrollHeight: {get: () => list.children.length * 30},
      scrollTop: {get: () => chatScroll, set: value => {
        chatScroll = Math.max(0, Math.min(value, list.scrollHeight - list.clientHeight));
      }},
    });
    window.HTMLElement.prototype.getBoundingClientRect = function () {
      const top = this.parentElement === list ? [...list.children].indexOf(this) * 30 - chatScroll : 0;
      return {top, bottom: top + 30};
    };
    window.fixture = {
      ...structuredClone(fixture), room_id: 'chat-scroll', revision: 1, status: 'playing',
      room_kind: invite ? 'invite' : 'ordinary', game_type: invite ? 'monopoly' : 'gomoku',
      game_name: invite ? '大富翁' : '五子棋', human_player_id: 'human:1', ai_player_id: 'ai:2',
      current_player_id: 'human:1', turn: 'human', rules_text: '测试规则',
      viewer: {player_id: 'human:1', role: 'human', can_move: true, is_participant: true},
    };
    if (!invite) {
      window.fixture.participants = fixture.participants.slice(0, 2);
      window.fixture.board_state = {size: 15, board: Array.from({length: 15}, () => Array(15).fill(null))};
      window.fixture.private_state = null;
    }
    window.run(`
      identity = {human_player_id: 'human:1', human_name: '玩家一', machines: [], games: []};
      startRoomPolling = () => { window.pollStarts = (window.pollStarts || 0) + 1; };
      currentTimeline = Array.from({length: 40}, (_, i) => {
        const p = fixture.participants[i % fixture.participants.length];
        return {sequence: i + 1, event_type: 'message', sender_role: p.role,
          sender_player_id: p.player_id, sender: {role: p.role, name: p.display_name},
          text: '聊天记录 ' + (i + 1), is_public: true};
      });
      renderGame(fixture, '', currentTimeline); hideWaitModeModal();
      const fullRender = renderGame;
      renderGame = (...args) => {
        fullRender(...args);
        // Model a page jump during a necessary full render to verify recovery.
        window.scrollY = 0;
      };
      request = (url, options) => new Promise((resolve, reject) => {
        window.pendingChat = {url, body: JSON.parse(options.body), resolve, reject};
      });
    `);
    assert.equal(list.children.length, 40);
    assert.match(list.textContent, /小机/);
    if (invite) {
      assert.match(list.textContent, /NPC/);
      assert.ok(list.querySelector('.recent-chat-reply'));
    }
    const mutations = new window.MutationObserver(() => {});
    for (const id of ['board', 'gameControls', 'privateStatePanel', 'roomParticipants', 'rulesText']) {
      mutations.observe($(id), {subtree: true, childList: true, attributes: true, characterData: true});
    }
    const enter = () => $('chatInput').dispatchEvent(new window.KeyboardEvent('keydown', {
      key: 'Enter', bubbles: true, cancelable: true,
    }));
    $('chatInput').value = '正在输入';
    $('chatInput').dispatchEvent(new window.KeyboardEvent('keydown', {
      key: 'Enter', isComposing: true, bubbles: true, cancelable: true,
    }));
    assert.equal(window.pendingChat, undefined, 'IME confirmation must not send');
    $('chatInput').value = '   ';
    $('sendMessageButton').click();
    assert.equal(window.pendingChat, undefined, 'empty input must not send');
    for (const mode of ['click', 'enter', 'dismiss', 'reading', 'revision', 'status', 'error', 'stale']) {
      const input = $('chatInput');
      input.value = invite ? '@2 测试留言' : '测试留言';
      input.focus();
      window.pendingChat = null;
      if (invite && mode === 'enter') {
        input.value = '@2';
        input.dispatchEvent(new window.Event('input'));
        assert.equal($('mentionOptions').classList.contains('hidden'), false);
        enter();
        assert.equal(input.value, '@2 ');
        assert.equal(window.pendingChat, null, 'completion must not send');
        input.value += '测试留言';
      }
      list.scrollTop = mode === 'reading' ? 70 : list.scrollHeight;
      const chatTop = list.scrollTop;
      const previousCount = list.children.length;
      const pollStarts = window.pollStarts || 0;
      mutations.takeRecords();
      window.scrollY = 700;
      if (mode === 'enter') enter();
      else { $('sendMessageButton').focus(); $('sendMessageButton').click(); }
      assert.ok(window.pendingChat);
      assert.equal(window.pendingChat.url, '/api/rooms/chat-scroll/messages');
      const sent = input.value.trim();
      assert.equal(window.pendingChat.body.message, sent);
      // Scrolling/dismissing the keyboard during the pending request must not
      // restore the older send-time position or programmatically refocus input.
      if (mode === 'dismiss') doc.activeElement.blur();
      window.scrollY = 620;
      const focus = doc.activeElement;
      const expectedRoom = window.run('room');
      window.mode = mode;
      if (mode === 'error') window.pendingChat.reject(new Error('测试网络失败'));
      else window.run(`
        if (window.mode === 'stale') stopPolling();
        const next = {...room, revision: room.revision + (window.mode === 'revision' ? 1 : 0),
          status: window.mode === 'status' ? 'finished' : room.status};
        const event = {sequence: currentTimeline.length + 1, event_type: 'message',
          sender_role: 'human', sender_player_id: 'human:1', sender: {role: 'human', name: '玩家一'},
          text: window.pendingChat.body.message};
        window.pendingChat.resolve({room: next, message: '留言已发送。', timeline: [...currentTimeline, event]});
      `);
      // sendMessage resumes before this microtask; inspect before the observer
      // callback consumes mutations, then let the next turn settle.
      await Promise.resolve();
      const records = mutations.takeRecords();
      assert.equal(window.scrollY, 620, mode);
      if (mode !== 'status') assert.equal(doc.activeElement, focus, mode);
      if (['error', 'stale'].includes(mode)) {
        assert.equal(input.value, sent);
        assert.equal(list.children.length, previousCount);
        assert.equal(window.run('room'), expectedRoom);
        assert.equal(records.length, 0);
      } else {
        assert.equal(input.value, '');
        assert.equal(list.children.length, previousCount + 1);
        assert.match(list.lastElementChild.textContent, /测试留言/);
        assert.equal(window.run('currentTimeline').length, list.children.length);
        if (['revision', 'status'].includes(mode)) assert.ok(records.length > 0);
        else assert.equal(records.length, 0, 'chat must not rebuild unrelated UI');
        if (mode === 'reading') assert.equal(list.scrollTop, chatTop);
        else assert.equal(list.scrollHeight - list.clientHeight - list.scrollTop, 0);
        if (!invite) assert.match($('humanSpeechText').textContent, /测试留言/);
      }
      assert.equal(window.pollStarts || 0, pollStarts + (['stale', 'status'].includes(mode) ? 0 : 1));
      await new Promise(resolve => setImmediate(resolve));
      cases++;
    }
    dom.window.close();
  }
  console.log(JSON.stringify({ok: true, cases, mode: 'DOM', realKeyboardAndLayoutVerified: false}));
})().catch(error => { console.error(error); process.exitCode = 1; });

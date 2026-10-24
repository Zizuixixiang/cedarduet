/* DOM/lifecycle/positioning tests only. jsdom supplies no layout or touch pixels. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {JSDOM} = require('jsdom');
const root = path.resolve(__dirname, '..');
const source = fs.readFileSync(path.join(root, 'app/static/games/four_color_tenhu.js'), 'utf8');
const css = fs.readFileSync(path.join(root, 'app/static/games/four_color_tenhu.css'), 'utf8');
const key = 'cedarduet.four_color.tutorial.v1';
const freeze = value => { if (value && typeof value === 'object') { Object.values(value).forEach(freeze); Object.freeze(value); } return value; };
function visit({remember, blockedStorage = false, playing = false, seats = 4, mode = 'tenhu'} = {}) {
  const dom = new JSDOM('<main id="gameView"><section id="inviteRoomPanel"></section><div id="board"><div class="fc-game">real board sentinel</div></div><div id="controls"></div><button id="origin">开局</button></main><div id="waitModeModal">ROOM TIP</div>', {url:'http://four-color.test/', runScripts:'outside-only'});
  const w = dom.window, d = w.document, renderers = {}, watched = [];
  w.HTMLDialogElement.prototype.showModal = function() { this.setAttribute('open', ''); };
  w.HTMLDialogElement.prototype.close = function() { this.removeAttribute('open'); };
  w.ResizeObserver = class { observe(n) { watched.push(n); } disconnect() { watched.length = 0; } };
  if (remember) w.localStorage.setItem(key, remember);
  if (blockedStorage) Object.defineProperty(w, 'localStorage', {get() { throw new Error('storage disabled'); }});
  w.DuelGameUI = {register: (type, renderer) => renderers[type] = renderer}; w.eval(source);
  const participants = Array.from({length:seats}, (_, i) => ({player_id:`p${i}`, display_name:`玩家${i}`, seat_index:i}));
  const order = participants.map(p => p.player_id);
  const state = playing ? {board_kind:`four_color_${mode}`, participant_order:order, wall_count:42, flow:{phase:'discard'},
    melds:Object.fromEntries(order.map((p, i) => [p, [{cards:[`${'RYGW'[i % 4]}0-1`, `${'RYGW'[i % 4]}1-1`]}]])),
    discards:['R5-1', 'Y5-1'], discard_sources:{'R5-1':'p0', 'Y5-1':'p2'}} : {};
  const c = {board:d.getElementById('board'), controls:d.getElementById('controls'),
    room:{room_id:'ROOM', room_kind:'invite', status:playing ? 'playing':'waiting', game_type:`four_color_${mode}`},
    viewer:{player_id:'p0'}, state, participants, privateState:{hand:[{id:'G6-1'}], legal_actions:[{action_id:'discard:G6-1'}]},
    uiState:{fourColor:{selected:'G6-1', handScroll:9, histories:{sentinel:{top:7}}}}, canMove:true, isTerminal:false,
    helpers:{setBoardLayout() { assert.fail('real layout helper'); }, rerender() { assert.fail('real rerender'); },
      canMove() { assert.fail('real canMove'); }, submitMove() { assert.fail('real move'); },
      renderParticipantAvatar(target, participant) { assert.ok(!participants.includes(participant)); target.textContent='人'; }}};
  const data = () => JSON.stringify([c.room, c.state, c.privateState, c.uiState, c.participants, c.viewer]);
  const before = data(), boardBefore = c.board.outerHTML, controlsBefore = c.controls.outerHTML;
  ['state', 'privateState', 'uiState', 'participants', 'viewer'].forEach(k => freeze(c[k]));
  const click = text => { const b = [...d.querySelectorAll('.fc-tutorial button')].find(n => n.textContent === text); assert.ok(b, text); b.click(); };
  const sync = () => renderers[`four_color_${mode}`].syncTutorial(c);
  const isolated = () => { assert.equal(data(), before); assert.equal(c.board.outerHTML, boardBefore); assert.equal(c.controls.outerHTML, controlsBefore); };
  const complete = () => { while ([...d.querySelectorAll('button')].some(n => n.textContent === '下一步')) click('下一步'); click('完成'); };
  d.getElementById('origin').focus(); sync();
  return {dom, w, d, c, click, sync, isolated, complete, renderers, watched};
}
(async () => {
  let v = visit();
  assert.ok(v.d.querySelector('dialog[open]'));
  assert.ok(v.d.querySelector('.fc-tutorial-spotlight').hidden);
  assert.ok(v.d.querySelector('.fc-tutorial-progress').hidden);
  assert.equal(v.d.querySelector('#fc-tutorial-copy').textContent, '滑动公开牌和弃牌区，就能查看被叠住的牌。');
  v.click('查看教程');
  const sample = v.d.querySelector('.fc-tutorial-board');
  assert.equal(sample.dataset.source, 'example');
  assert.equal(v.d.querySelector('.fc-tutorial-demo'), null, 'rejected standalone rails are gone');
  assert.equal(sample.querySelectorAll('.fc-table .fc-seat').length, 4);
  assert.equal(sample.querySelectorAll('.room-participant-avatar').length, 4);
  assert.equal(sample.querySelectorAll('.fc-history').length, 4);
  assert.ok(sample.querySelector('.fc-stock .fc-card-back'));
  assert.equal(sample.querySelectorAll('.fc-hand button').length, 20);
  const ids = [...sample.querySelectorAll('[data-card-id]')].map(n => n.dataset.cardId);
  assert.equal(new Set(ids).size, ids.length, 'sample cards have unique physical IDs');
  assert.ok(ids.every(id => /^[RYGW][0-6]-(tutorial|history|hand)-/.test(id)), 'visual examples cannot masquerade as real physical card IDs');
  for (const port of sample.querySelectorAll('.fc-seat-public')) {
    assert.equal(port.querySelectorAll('.fc-meld').length, 3);
    assert.equal(port.querySelector('.fc-meld').querySelectorAll('.fc-card').length, 3);
  }
  assert.ok(!v.d.querySelector('.fc-tutorial-panel').contains(sample));
  // Exercise native scroll events and attempted hand actions against frozen inputs.
  sample.querySelectorAll('.fc-seat-public, .fc-discard-history, .fc-hand').forEach(port => {
    port.scrollLeft = 31; port.scrollTop = 29; port.dispatchEvent(new v.w.Event('scroll'));
  });
  sample.querySelectorAll('button').forEach(button => { assert.ok(button.disabled); button.click(); button.dispatchEvent(new v.w.MouseEvent('click', {bubbles:true})); });
  for (let i = 0; i < 2; i++) { v.click('下一步'); assert.equal(v.d.querySelector('.fc-tutorial-board'), sample, 'steps retain the isolated board container'); }
  assert.match(sample.querySelector('[data-tutorial-target]').className, /fc-discard-history/);
  assert.equal(v.d.querySelector('.fc-tutorial-progress').textContent, '3 / 3');
  v.click('上一步'); v.isolated();
  // Synthetic geometry verifies non-overlap arithmetic, not browser rendering.
  for (const [width, height] of [[360,800], [430,800], [1280,800], [360,320]]) {
    const dialog = v.d.querySelector('dialog'), panel = v.d.querySelector('.fc-tutorial-panel');
    Object.defineProperty(dialog, 'clientHeight', {value:height, configurable:true});
    dialog.getBoundingClientRect = () => ({left:0, top:0, width, height});
    panel.getBoundingClientRect = () => ({height:180});
    sample.querySelectorAll('.fc-seat-public, .fc-seat-identity, .fc-history').forEach(n => {
      n.getBoundingClientRect = () => ({left:12, top:180, width:100, height:142});
    });
    v.w.dispatchEvent(new v.w.Event('resize'));
    const pTop = parseFloat(panel.style.top);
    assert.ok(pTop >= 336 || pTop + 180 <= 166, 'explanation avoids the actual target');
    assert.equal(v.d.querySelector('.fc-tutorial-spotlight').style.top, '176px');
  }
  v.click('×'); assert.equal(v.w.localStorage.getItem(key), null); assert.equal(v.d.activeElement.id, 'origin');
  assert.equal(v.d.querySelector('.fc-tutorial-board, .fc-tutorial-spotlight'), null); assert.equal(v.watched.length, 0);
  v.isolated(); v.sync(); assert.equal(v.d.querySelector('dialog'), null);
  v.d.getElementById('gameView').classList.add('hidden'); await Promise.resolve();
  v.d.getElementById('gameView').classList.remove('hidden'); v.sync(); assert.ok(v.d.querySelector('dialog[open]'));
  v.click('不再提示'); assert.equal(v.w.localStorage.getItem(key), 'dismissed'); v.dom.window.close();
  for (const remember of ['dismissed', 'completed']) { v = visit({remember}); assert.equal(v.d.querySelector('dialog'), null); v.dom.window.close(); }
  v = visit(); v.click('查看教程'); v.complete(); v.isolated(); assert.equal(v.w.localStorage.getItem(key), 'completed'); v.dom.window.close();
  v = visit(); v.c.state = {participant_order:['p0', 'p1']}; v.click('查看教程');
  assert.equal(v.d.querySelectorAll('.fc-tutorial-board .fc-seat').length, 4, 'waiting projection still gets a complete example'); v.dom.window.close();
  for (const mode of ['tenhu', 'jiuzhi', 'qunhu']) {
    v = visit({mode}); v.click('查看教程'); v.c.room.status = 'playing'; v.sync();
    assert.equal(v.d.querySelector('.fc-tutorial'), null, 'start removes example synchronously');
    assert.equal(v.watched.length, 0); assert.equal(v.w.localStorage.getItem(key), null);
    v.sync(); assert.equal(v.d.querySelector('dialog'), null); v.dom.window.close();
  }
  for (const seats of [2, 3, 4, 5]) {
    v = visit({playing:true, seats, mode:seats === 5 ? 'qunhu' : 'tenhu'}); v.click('查看教程');
    const board = v.d.querySelector('.fc-tutorial-board');
    assert.equal(board.dataset.source, 'example'); assert.equal(board.querySelectorAll('.fc-seat').length, seats);
    assert.equal(board.querySelector('.fc-stock-copy strong').textContent, '24 张');
    assert.equal(board.querySelectorAll('.fc-hand button').length, 20);
    assert.equal(board.querySelectorAll('.fc-seat-public .fc-meld').length, seats * 3);
    assert.equal(board.querySelectorAll('.fc-discard-history .fc-card').length, seats * 2);
    assert.equal(board.querySelector('.fc-seat-name').textContent, '玩家0（你）', 'keep actual avatar ownership');
    board.querySelectorAll('button').forEach(n => { n.click(); n.dispatchEvent(new v.w.MouseEvent('click', {bubbles:true})); });
    v.sync(); assert.equal(v.d.querySelector('.fc-tutorial-board'), board, 'polling keeps independent practice');
    v.complete(); v.isolated(); v.dom.window.close();
  }
  v = visit({playing:true}); v.c.state = {...v.c.state, melds:{}, discards:[]}; v.click('查看教程');
  assert.match(v.d.querySelector('#fc-tutorial-copy').textContent, /示例牌桌/); v.complete(); v.dom.window.close();
  v = visit(); v.click('查看教程'); v.c.room = {...v.c.room, room_id:'NEW'}; v.sync();
  assert.equal(v.d.querySelectorAll('dialog').length, 1); assert.equal(v.d.querySelector('.fc-tutorial-board').children.length, 0); v.dom.window.close();
  v = visit(); v.click('查看教程'); v.d.getElementById('inviteRoomPanel').classList.add('hidden'); await Promise.resolve();
  assert.equal(v.d.querySelector('dialog'), null); v.dom.window.close();
  v = visit({playing:true}); v.click('查看教程'); v.c.board.replaceChildren(); await Promise.resolve();
  assert.equal(v.d.querySelector('dialog'), null); v.dom.window.close();
  v = visit(); const event = new v.w.Event('cancel', {cancelable:true}); v.d.querySelector('dialog').dispatchEvent(event);
  assert.ok(event.defaultPrevented); assert.equal(v.d.querySelector('dialog'), null); assert.equal(v.w.localStorage.getItem(key), null); v.dom.window.close();
  v = visit({blockedStorage:true}); v.click('不再提示'); v.sync(); assert.equal(v.d.querySelector('dialog'), null); v.dom.window.close();
  assert.match(css, /body:has\(dialog\.fc-tutorial\[open\]\) #waitModeModal \{ visibility: hidden; \}/);
  console.log('PASS: normal renderer (2/3/4/5 seats), viewport-fitted isolated examples, three nonduplicated steps with real discard port, frozen data, blocked actions, scroll events, X/Esc/completion/preferences, SPA/room/start cleanup, synthetic positioning, ROOM TIP selector retained. No browser/pixel claims.');
})().catch(error => { console.error(error); process.exitCode = 1; });

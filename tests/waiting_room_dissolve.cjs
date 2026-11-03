// Minimal DOM regression against production callbacks; no browser or network.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('app/static/app.js', 'utf8');
const html = fs.readFileSync('app/static/index.html', 'utf8');
function functionSource(name) {
  const start = source.indexOf(`function ${name}(`);
  assert(start >= 0, name);
  const rest = source.slice(start);
  const end = rest.slice(1).search(/\n(?:async )?function |\n\$\("reclaimControl"\)/);
  return rest.slice(0, end + 1);
}
function harness(kind) {
  const elements = new Map(), calls = [], notices = [];
  const document = {activeElement: null, createElement: () => new Element()};
  class Element {
    constructor() {
      this.children = []; this.dataset = {}; this.listeners = {};
      this.disabled = false; this.open = false; this.textContent = '';
      this.classList = {toggle() {}, remove() {}, add() {}};
    }
    append(...children) { this.children.push(...children); }
    appendChild(child) { this.append(child); }
    prepend(child) { this.children.unshift(child); }
    replaceChildren(...children) { this.children = children; }
    setAttribute() {}
    addEventListener(name, fn) { this.listeners[name] = fn; }
    focus() { if (!this.disabled) document.activeElement = this; }
    showModal() { this.open = true; }
    close(value) {
      if (!this.open) return;
      this.returnValue = value; this.open = false;
      queueMicrotask(() => this.onclose?.());
    }
    click() { if (!this.disabled) return (this.onclick || this.listeners.click)?.(); }
    querySelector(selector) {
      const action = selector.match(/data-action="([^"]+)"/)[1];
      return this.children.find(child => child.dataset.action === action)
        || this.children.map(child => child.querySelector(selector)).find(Boolean);
    }
  }
  for (const match of html.matchAll(/\bid="([^"]+)"/g)) elements.set(match[1], new Element());
  const $ = id => { assert(elements.has(id), `missing HTML element ${id}`); return elements.get(id); };
  const context = vm.createContext({document, $, console, Date, Promise,
    room: {room_id: 'room-1', revision: 1, room_kind: kind, status: 'waiting',
      game_type: 'liars_dice', viewer: {player_id: 'owner'}, initiator_player_id: 'owner',
      participants: [{player_id: 'owner', display_name: '房主'}], target_player_count: 4,
      ...(kind === 'invite' ? {} : {team_selection: {can_start: false, seats: {red: [null,null], blue: [null,null]}}})},
    identity: {games: []}, diceSetupRefreshTimer: null,
    window: {DuelDiceTeams: {renderSelection() {}}, confirm() { assert.fail('window.confirm'); }},
    clearTimeout() {}, setTimeout() { assert.fail('unexpected timer'); },
    renderParticipantAvatar() {}, chooseDiceTeamSeat() {},
    showNotice: message => notices.push(message),
    request: async (path, options) => { calls.push({path, ...options}); if (context.fail) throw Error('请求失败'); },
    backToLobby: async () => { context.lobbyCalls++; context.room = null; }, lobbyCalls: 0,
  });
  vm.runInContext(['viewerPlayerIdFor', 'isInviteWaiting', 'confirmWaitingRoomDissolve',
    'participantAccountId', 'renderInviteRoomPanel'].map(functionSource).join('\n'), context);
  const render = () => context.renderInviteRoomPanel();
  render();
  const button = text => $('inviteRoomPanel').querySelector(`[data-action="${text}"]`);
  const open = () => { button('解散房间').focus(); return button('解散房间').click(); };
  return {context, $, calls, notices, document, render, button, open};
}

(async () => {
  assert.match(html, /<dialog id="dissolveRoomDialog"[^>]*aria-labelledby="dissolveRoomTitle"[^>]*aria-describedby="dissolveRoomDescription"/);
  for (const kind of ['normal', 'invite']) {
    const h = harness(kind);
    for (const action of ['cancel', 'escape', 'confirm']) {
      const pending = h.open();
      assert(h.$('dissolveRoomDialog').open);
      assert.equal(h.$('dissolveRoomTitle').textContent, kind === 'invite' ? '确定解散邀请房间？' : '确定解散组队房间？');
      assert.match(h.$('dissolveRoomDescription').textContent, /全部成员将离开房间，无法重新进入/);
      assert.equal(h.$('dissolveRoomDescription').textContent.includes('邀请码也将失效'), kind === 'invite');
      assert.equal(h.document.activeElement, h.$('dissolveRoomCancel'));
      if (action === 'escape') {
        let prevented = false;
        h.$('dissolveRoomDialog').oncancel({preventDefault() { prevented = true; }});
        assert(prevented);
      } else h.$(action === 'cancel' ? 'dissolveRoomCancel' : 'dissolveRoomConfirm').click();
      await pending;
      assert(!h.$('dissolveRoomDialog').open);
      if (action !== 'confirm') {
        assert.equal(h.calls.length, 0); assert.equal(h.context.lobbyCalls, 0);
        assert.equal(h.document.activeElement, h.button('解散房间'));
      }
    }
    assert.deepEqual(h.calls, [{path: '/api/rooms/room-1/leave', method: 'POST', body: '{}'}]);
    assert.equal(h.context.lobbyCalls, 1);
    const navigation = harness(kind);
    await navigation.button('返回大厅').click();
    assert.equal(navigation.calls.length, 0); assert.equal(navigation.context.lobbyCalls, 1);
  }
  for (const change of [{room_id: 'room-2'}, {viewer: {player_id: 'member'}},
    {initiator_player_id: 'member'}, {status: 'playing'}, {spectating: true}]) {
    const h = harness('normal'); const pending = h.open();
    Object.assign(h.context.room, change);
    h.$('dissolveRoomConfirm').click(); await pending;
    assert.equal(h.calls.length, 0); assert.equal(h.context.lobbyCalls, 0);
  }
  for (const change of [{viewer: {player_id: 'member'}}, {spectating: true}]) {
    const h = harness('invite'); Object.assign(h.context.room, change); h.render();
    assert.equal(h.button('解散房间'), undefined);
  }
  const refreshed = harness('normal'); const pending = refreshed.open();
  refreshed.context.room.revision++; refreshed.render();
  refreshed.$('dissolveRoomCancel').click(); await pending;
  assert.equal(refreshed.document.activeElement, refreshed.button('解散房间'));
  const failed = harness('invite'); failed.context.fail = true;
  const failure = failed.open(); failed.$('dissolveRoomConfirm').click(); await failure;
  assert.equal(failed.context.lobbyCalls, 0); assert.deepEqual(failed.notices, ['请求失败']);
  assert.equal(failed.document.activeElement, failed.button('解散房间'));
  console.log('PASS: team/invite copy, cancel/Esc/focus, owner confirmation, navigation, stale state, refresh, failure');
})().catch(error => { console.error(error); process.exitCode = 1; });

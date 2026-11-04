/* Default: jsdom. DICE_BADGES_BROWSER=1 also checks Chromium at 360/430/1280.
   JSDOM_MODULE / PLAYWRIGHT_MODULE / CHROMIUM_EXECUTABLE may use local installs.
   DICE_SCREENSHOTS optionally retains browser evidence. No server or real rooms. */
const assert = require('node:assert/strict');
(async () => {
const fs = require('node:fs'), path = require('node:path');
const root = path.resolve(__dirname, '..');
const app = fs.readFileSync(path.join(root, 'app/static/app.js'), 'utf8');
const functions = ['participantAvatarFallback', 'accountAvatarForParticipant',
  'renderParticipantAvatar', 'viewerPlayerIdFor', 'viewerParticipantFor',
  'participantAccountId', 'isFourColorGame', 'createParticipantBadge'];
const source = 'let identity = null, room = null; const apiPath = p => p;\n' + functions.map(name => {
  const start = app.indexOf(`function ${name}(`);
  assert.ok(start >= 0, name);
  return app.slice(start, app.indexOf('\n}', start) + 2);
}).join('\n');
const html = '<main class="game-view"><section id="battleStage" class="battle-stage" data-game-type="liars_dice"><div class="table-layout layout-multiplayer"><div class="room-participants count-6"></div></div></section></main>';
const fixture = {
  game_type: 'liars_dice', status: 'playing', current_player_id: 'p0', viewer: {player_id: 'p0'},
  board_state: {mode: 'party', teams: {red: ['p1', 'p4', 'p5'], blue: ['p0', 'p2', 'p3']}},
  participants: Array.from({length: 6}, (_, seat_index) => ({
    player_id: `p${seat_index}`, seat_index, display_name: ['南杉', '长昵称玩家测试', '小机', '松果', '人类队友', 'NPC'][seat_index],
    participant_kind: seat_index === 5 ? 'system_npc' : 'human', role: 'human',
    active: true, activity_state: 'active', avatar: {type: 'emoji', value: '🌲'},
    game_metadata: {dice_count: 5, score: 0},
  })),
};
// Use the actual server summary contract, including string half-points, without
// importing the framework or opening a database.
const execFile = require('node:util').promisify(require('node:child_process').execFile);
const summaries = JSON.parse((await execFile(path.join(root, '.venv/bin/python'), ['-c', `
import json
from app.games.liars_dice import LiarsDice
game = LiarsDice()
people = [{'player_id': f'p{i}'} for i in range(6)]
state = {'mode':'party', 'participant_order':[p['player_id'] for p in people],
         'dice_counts':{p['player_id']:5 for p in people},
         'teams':{'red':['p1','p4','p5'], 'blue':['p0','p2','p3']},
         'team_scores':{'red':-1, 'blue':0}}
result = []
for version in (None, 1, 2):
    if version is not None: state['team_rules_version'] = version
    result.append([game.participant_summary(state, p, people) for p in people])
del state['teams']
state['scores'] = {p['player_id']:-0.5 for p in people}
result.append([game.participant_summary(state, p, people) for p in people])
print(json.dumps(result))
`], {cwd:root, encoding:'utf8'})).stdout);
const {JSDOM} = require(process.env.JSDOM_MODULE || 'jsdom');
const dom = new JSDOM(html, {runScripts: 'outside-only'});
const style = dom.window.document.createElement('style');
style.textContent = fs.readFileSync(path.join(root, 'app/static/styles.css'), 'utf8');
dom.window.document.head.append(style);
dom.window.eval(source);
const render = room => room.participants.map(p => dom.window.createParticipantBadge(p, room));
for (const version of [undefined, 1, 2, 3]) {
  const room = structuredClone(fixture);
  room.board_state.team_rules_version = version;
  room.participants.forEach((p, i) => {p.game_metadata = {...summaries[version === undefined ? 0 : Math.min(version, 2)][i]};});
  // The badge must never consult any private dice, including a teammate's cup.
  for (const [object, key] of [[room, 'private_state'], [room.board_state, 'dice_by_player']]) {
    Object.defineProperty(object, key, {get() {throw Error(`Read private field ${key}`);}});
  }
  room.participants.forEach(p => {p.game_metadata.team = '故意冲突的元数据';});
  const cards = render(room);
  assert.deepEqual(cards.map(c => c.querySelector('.room-participant-team-marker').textContent), ['蓝', '红', '蓝', '蓝', '红', '红']);
  cards.forEach((c, i) => {
    const marker = c.querySelector('.room-participant-team-marker');
    assert.equal(marker.getAttribute('aria-label'), `${marker.textContent}队`);
    assert.ok(c.classList.contains(`seat-${i}`));
    assert.equal(marker.parentElement, c, 'marker belongs to card, outside avatar and copy');
    assert.equal(c.querySelector('.party-participant-score').textContent, `队分 ${['p1','p4','p5'].includes(`p${i}`) ? -1 : 0}`);
    assert.doesNotMatch(c.querySelector('.room-participant-copy small').textContent, /座位/);
    assert.doesNotMatch(c.textContent, /骰子|共用|你的席位|正在行动|故意冲突/);
    assert.equal(c.classList.contains('current'), i === 0);
    assert.equal(c.querySelector('.room-participant-avatar').classList.contains('current-turn-avatar'), i === 0);
  });
  // Perspective / participant order cannot change membership.
  room.spectating = true; room.viewer.player_id = 'p4'; room.participants.reverse();
  assert.deepEqual(render(room).map(c => c.querySelector('.room-participant-team-marker').textContent), ['红', '红', '蓝', '蓝', '红', '蓝']);
}
for (const patch of [{game_type: 'yahtzee'}, {board_state: {mode: 'classic', teams: fixture.board_state.teams}},
  {board_state: {mode: 'party'}}, {board_state: {mode: 'party', teams: {}}},
  {board_state: {mode: 'party', teams: {red: 'p0', blue: null}}}]) {
  render({...fixture, ...patch}).forEach(c => {
    assert.equal(c.querySelector('.room-participant-team-marker'), null);
    assert.ok(c.firstElementChild.classList.contains('room-participant-avatar'));
  });
}
const room = structuredClone(fixture);
room.board_state.teams = {red: ['p5']};
room.participants[5].avatar_url = '/missing.png';
const cards = render(room);
assert.equal(cards[0].querySelector('.room-participant-team-marker'), null);
cards[5].querySelector('img').dispatchEvent(new dom.window.Event('error'));
assert.equal(cards[5].querySelector('.room-participant-avatar').textContent, 'N');
assert.equal(cards[5].querySelector('.room-participant-team-marker').textContent, '红');
dom.window.document.querySelector('.room-participants').replaceChildren(...cards);
const markerStyle = dom.window.getComputedStyle(cards[5].querySelector('.room-participant-team-marker'));
assert.equal(markerStyle.position, 'absolute');
assert.equal(markerStyle.fontSize, '11px');
assert.equal(markerStyle.minWidth, '16px');
assert.equal(markerStyle.height, '16px');
assert.equal(markerStyle.right, '-3px');
assert.equal(cards[5].querySelector('.room-participant-detail').textContent, '队分 0');
const solo = structuredClone(fixture);
solo.board_state = {mode:'party'};
solo.participants.forEach((p, i) => {p.game_metadata = summaries[3][i];});
solo.participants[0].confirmation_status = 'pending';
solo.participants[1].join_status = 'left';
solo.participants[2].participant_kind = 'bound_machine';
const soloCards = render(solo);
soloCards.forEach((c, i) => {
  assert.equal(c.querySelector('.party-participant-score').textContent, '积分 -0.5');
  assert.equal(c.querySelector('small').textContent, i === 5 ? 'NPC' : `ID p${i} · ${i === 2 ? '小机' : '人类'}`);
  assert.equal(c.querySelector('.room-participant-team-marker'), null);
});
assert.equal(soloCards[0].querySelector('.party-participant-status').textContent, '待确认');
assert.equal(soloCards[1].querySelector('.party-participant-status').textContent, '已离开');
const fallback = structuredClone(fixture);
fallback.board_state.team_scores = {red:-2.5, blue:0};
fallback.participants.forEach(p => {delete p.game_metadata.score;});
assert.equal(render(fallback)[1].querySelector('.party-participant-score').textContent, '队分 -2.5');
delete fallback.board_state.team_scores;
assert.equal(render(fallback)[1].querySelector('.party-participant-score').textContent, '队分 —', 'missing score is not fabricated as zero');
solo.participants[0].game_metadata = {};
solo.board_state.scores = {p0:-1};
assert.equal(render(solo)[0].querySelector('.party-participant-score').textContent, '积分 -1');
for (const game_type of ['liars_dice', 'yahtzee', 'uno', 'four_color_classic']) {
  const other = structuredClone(fixture);
  other.game_type = game_type; other.board_state.mode = 'classic';
  const c = render(other)[0];
  assert.equal(c.querySelector('.party-participant-score'), null);
  assert.equal(c.querySelector('.room-participant-team-marker'), null);
  assert.match(c.querySelector('small').textContent, /座位 1/);
  assert.match(c.querySelector('.room-participant-detail').textContent, /得分 0/);
  assert.match(c.querySelector('.room-participant-detail').textContent, /剩余骰子 5/);
}
// CSSOM checks are structural only: jsdom does not calculate rendered geometry.
dom.window.document.querySelector('.room-participants').replaceChildren(...soloCards);
const scoreStyle = dom.window.getComputedStyle(soloCards[0].querySelector('.party-participant-score'));
assert.equal(scoreStyle.whiteSpace, 'nowrap');
assert.equal(scoreStyle.overflow, 'visible');
assert.equal(scoreStyle.textOverflow, 'clip');
assert.equal(scoreStyle.gridRow, '3');
assert.equal(dom.window.getComputedStyle(soloCards[0].querySelector('small')).gridRow, '2');
dom.window.close();
console.log('PASS: real server score summaries, legacy/v2 teams, solo scores, statuses, scope, perspective, image fallback; DOM/CSS only');

if (process.env.DICE_BADGES_BROWSER === '1') {
  fixture.participants.forEach((p, i) => {p.game_metadata = summaries[2][i];});
  const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
  const browser = await chromium.launch({headless: true, args: ['--no-sandbox'],
    ...(process.env.CHROMIUM_EXECUTABLE ? {executablePath: process.env.CHROMIUM_EXECUTABLE} : {})});
  try {
    for (const width of [360, 430, 1280]) {
      const page = await browser.newPage({viewport: {width, height: 900}, isMobile: width < 600, hasTouch: width < 600});
      await page.setContent(html);
      await page.addStyleTag({content: fs.readFileSync(path.join(root, 'app/static/styles.css'), 'utf8')});
      await page.addScriptTag({content: source});
      await page.evaluate(fixture => document.querySelector('.room-participants').replaceChildren(
        ...fixture.participants.map(p => createParticipantBadge(p, fixture))), fixture);
      const checks = await page.locator('.room-participant').evaluateAll(cards => cards.map(card => {
        const box = el => el.getBoundingClientRect();
        const m = box(card.querySelector('.room-participant-team-marker'));
        const a = box(card.querySelector('.room-participant-avatar'));
        const c = box(card);
        const name = card.querySelector('.room-participant-copy strong');
        const nameBox = box(name);
        const copy = {left:nameBox.left, top:nameBox.top, bottom:nameBox.bottom,
          right:nameBox.right - parseFloat(getComputedStyle(name).paddingRight)};
        const detail = box(card.querySelector('.room-participant-detail'));
        const overlaps = r => m.left < r.right && m.right > r.left && m.top < r.bottom && m.bottom > r.top;
        return {inside: m.left >= c.left && m.right <= c.right && m.top >= c.top && m.bottom <= c.bottom,
          clear: !overlaps(copy) && !overlaps(detail) && !overlaps(a),
          scoreFits: card.querySelector('.party-participant-score').scrollWidth <= detail.width,
          rightCorner: Math.abs(m.right - c.right) <= 1 && Math.abs(m.top - c.top) <= 1,
          readable: m.width >= 16 && m.height >= 16,
          seatColor: getComputedStyle(card).borderTopColor};
      }));
      assert.equal(checks.length, 6);
      checks.forEach(c => assert.ok(c.inside && c.clear && c.readable && c.scoreFits && c.rightCorner, JSON.stringify({width, ...c})));
      assert.equal(new Set(checks.map(c => c.seatColor)).size, 6, 'seat colors stay distinct');
      assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), 'no page overflow');
      if (process.env.DICE_SCREENSHOTS) {
        fs.mkdirSync(process.env.DICE_SCREENSHOTS, {recursive: true});
        await page.screenshot({path: path.join(process.env.DICE_SCREENSHOTS, `avatar-teams-${width}.png`)});
      }
      console.log(`PASS: ${width}px, six readable badges, avatar centers/copy clear, seat colors retained`);
      await page.close();
    }
  } finally {await browser.close();}
}

})().catch(error => {console.error(error); process.exitCode = 1;});

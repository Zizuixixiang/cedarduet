/* Result renderer regression tests: no browser or external packages required. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('app/static/app.js', 'utf8');
function functionSource(name) {
  const start = source.indexOf(`function ${name}(`), end = source.indexOf('\nfunction ', start + 1);
  assert.ok(start >= 0);
  return source.slice(start, end < 0 ? undefined : end);
}
class Element {
  constructor(tag, doc) {
    this.tag = tag; this.ownerDocument = doc; this.children = []; this.className = ''; this.textContent = '';
    this.attributes = {}; this.open = false;
    this.classList = {toggle: (name, on) => {if (on) this.className += ` ${name}`;}};
  }
  appendChild(node) {this.children.push(node); return node;}
  append(...nodes) {nodes.forEach(node => this.appendChild(node));}
  setAttribute(name, value) {this.attributes[name] = value;}
  addEventListener() {}
}
const document = {createElement: tag => new Element(tag, document)};
const participants = [{player_id: 'a', display_name: '唐焰'}, {player_id: 'b', display_name: '南杉'},
  {player_id: 'c', display_name: '青禾'}, {player_id: 'd', display_name: '小满'}];
const context = vm.createContext({document, window: {document, DuelGameUI: {register() {}}}, room: {status: 'finished', participants},
  participantByPlayerId: id => participants.find(p => p.player_id === id),
  canAcknowledgeLiarsRound: () => false});
vm.runInContext(fs.readFileSync('app/static/games/liars_dice.js', 'utf8'), context);
vm.runInContext(['liarsParticipantName', 'liarsRoundResultIsVisible', 'liarsRoundResultLines',
  'createLiarsDie', 'renderLiarsDice'].map(functionSource).join('\n'), context);
const lines = context.liarsRoundResultLines;
const find = (node, cls) => [ ...(node.className.split(' ').includes(cls) ? [node] : []),
  ...node.children.flatMap(child => find(child, cls)) ];
const text = node => [node.textContent, ...node.children.map(text)].filter(Boolean).join(' ');
const base = {round: 1, phase: 'revealed', bid: {bidder_player_id: 'a', quantity: 5, face: 3, exact: true},
  bidder_player_id: 'a', challenger_player_id: 'b', actual_count: 5, bid_holds: true,
  loser_player_id: 'b', loser_remaining_dice: 4, revealed_dice_by_player: {a: [1, 3, 3], b: [3, 3, 3]}};
const party = {...base, mode: 'party', winner_player_id: 'a',
  score_changes: [{unit_id: 'red', score_delta: 1}, {unit_id: 'blue', score_delta: -1}],
  rewards: [{player_id: 'a', team_id: 'red', score_delta: 1}],
  penalties: [{player_id: 'b', team_id: 'blue', score_delta: -1, hits: []}],
  score_delta: -99}; // Authoritative net changes must win over all other summaries.
const render = (outcome, state = {}) => {
  const board = document.createElement('div');
  const snapshot = {mode: outcome?.mode, flow: {phase: 'finished', round_number: 1}, last_round_result: outcome, ...state};
  const before = JSON.stringify(snapshot);
  context.renderLiarsDice(board, snapshot);
  assert.equal(JSON.stringify(snapshot), before, 'rendering must not change a snapshot');
  if (outcome) {
    const card = find(board, 'liars-round-result')[0];
    assert.deepEqual(card.children.slice(0, 3).map(node => node.className), [
      'liars-result-header', 'liars-result-line liars-result-outcome',
      'liars-result-line liars-result-loss',
    ]);
    assert.deepEqual(card.children[0].children.map(node => node.className), [
      'liars-result-round', 'liars-result-separator', 'liars-result-title',
    ]);
    assert.equal(card.children[0].children[1].textContent, '·');
    assert.doesNotMatch(text(card), /喊了|数量够了|数量不够/);
  }
  return board;
};
for (const outcome of [base, party]) {
  let board = render(outcome);
  assert.equal(board.children.length, 1, 'terminal has only the result, no redundant status card');
  const card = board.children[0];
  assert.equal(card.children[0].children[0].textContent, '第 1 轮结算');
  assert.equal(card.children[0].children[2].textContent, '叫点成功');
  assert.equal(find(board, 'liars-result-outcome')[0].textContent, '唐焰叫 5 个 3 点（斋），开骰共 5 个 3 点。');
  assert.equal(find(board, 'liars-result-loss').length, 1);
  assert.equal(find(board, 'liars-penalty-details').length, 0);
  assert.doesNotMatch(text(board), /实际5个3点|原始|胜方\+1|累计|整局已结束/);
  if (outcome.mode === 'party') {
    assert.equal(find(board, 'liars-result-loss')[0].textContent, '红队 +1 分　蓝队 -1 分');
    assert.doesNotMatch(text(board), /颗骰子|剩余/);
  } else {
    assert.equal(find(board, 'liars-result-loss')[0].textContent, '南杉 -1颗骰子 · 剩余4颗');
    assert.doesNotMatch(text(board), /积分|计分|[+-]\d+分/);
  }
  const failed = {...outcome, actual_count: 2, bid_holds: false, loser_player_id: 'a',
    ...(outcome.mode === 'party' ? {winner_player_id: 'b'} : {})};
  board = render(failed);
  assert.equal(find(board, 'liars-result-title')[0].textContent, '质疑成功');
  assert.equal(find(board, 'liars-result-outcome')[0].textContent, '唐焰叫 5 个 3 点（斋），开骰共 2 个 3 点。');
}
// Use the authoritative count, including wild ones / cup bonuses, without
// describing it as a count of physical dice. Exact bids exclude wild ones.
for (const [exact, dice, count, bonus] of [
  [false, [1, 1, 1, 3, 3], 6, 1],
  [false, [3, 3, 3, 3, 3], 7, 2],
  [false, [1, 1, 1, 1, 1], 5, 0],
  [true, [1, 1, 1, 3, 3], 2, 0],
  [true, [3, 3, 3, 3, 3], 7, 2],
]) {
  const outcome = {...party, round: 4, bid: {...party.bid, exact}, actual_count: count,
    bid_holds: count >= 5, winner_player_id: count >= 5 ? 'a' : 'b',
    loser_player_id: count >= 5 ? 'b' : 'a',
    cup_bonuses: bonus ? {a: bonus} : {}, revealed_dice_by_player: {a: dice, b: [2, 4, 5]}};
  const board = render(outcome);
  assert.equal(find(board, 'liars-result-round')[0].textContent, '第 4 轮结算');
  assert.equal(find(board, 'liars-result-outcome')[0].textContent,
    `唐焰叫 5 个 3 点${exact ? '（斋）' : ''}，开骰${exact && !bonus ? '共' : '按规则算'} ${count} 个 3 点。`);
  assert.doesNotMatch(text(board), /实际|真实|同面/);
}
assert.equal(lines({...base, loser_remaining_dice: 0}).loss, '南杉 -1颗骰子 · 已淘汰');
// Final winner / loser override count success, in both dark-rule directions.
for (const holds of [true, false]) {
  const reversed = {...party, actual_count: holds ? 5 : 0, bid_holds: holds, dark_reversed: true,
    winner_player_id: holds ? 'b' : 'a', loser_player_id: holds ? 'a' : 'b'};
  for (const mode of ['classic', 'party']) {
    const outcome = {...reversed, mode};
    if (mode === 'classic') delete outcome.winner_player_id;
    const board = render(outcome);
    assert.equal(find(board, 'liars-result-title')[0].textContent, holds ? '质疑成功' : '叫点成功');
    assert.equal(find(board, 'liars-result-round')[0].textContent, '第 1 轮结算 · 天黑黑反转');
    assert.equal(find(board, 'liars-result-outcome')[0].textContent,
      `唐焰叫 5 个 3 点（斋），开骰共 ${holds ? 5 : 0} 个 3 点。`);
    if (mode === 'classic') assert.equal(find(board, 'liars-penalty-details').length, 0);
  }
}
assert.equal(lines({...base, bid_holds: false, winner_player_id: 'a'}).title, '叫点成功');
assert.equal(lines({bid_holds: true, dark_reversed: true}).title, '质疑成功');
// Same team: sum once, even if rewards and penalties mention different teammates.
const teams = {red: ['a', 'b'], blue: ['c', 'd']};
for (const penalty of [-1, -2]) {
  const outcome = {...party, score_changes: [{unit_id: 'red', score_delta: 1 + penalty}],
    penalties: [{player_id: 'b', team_id: 'red', score_delta: penalty}]};
  for (const legacy of [false, true]) {
    if (legacy) delete outcome.score_changes;
    assert.equal(lines(outcome).loss, `红队 ${penalty === -1 ? '0' : '-1'} 分`);
  }
  delete outcome.rewards[0].team_id; // Old snapshots can recover team membership from state.
  delete outcome.penalties[0].team_id;
  assert.equal(lines(outcome, {teams}).loss, `红队 ${penalty === -1 ? '0' : '-1'} 分`);
  outcome.rewards[0].team_id = 'red';
}
const companion = {...party, penalties: [
  {player_id: 'b', team_id: 'blue', score_delta: -2, hits: ['under_half'], multiplier: 2},
  {player_id: 'a', team_id: 'red', score_delta: -2, hits: ['companion'], multiplier: 2}],
  score_changes: [{unit_id: 'red', delta_half_units: -2}, {unit_id: 'blue', delta_half_units: -4}],
  companion: {player_id: 'a', rolls: [1], seat: 1, same_unit: false}};
assert.equal(lines(companion).loss, '红队 -1 分　蓝队 -2 分');
delete companion.score_changes;
assert.equal(lines(companion).loss, '红队 -1 分　蓝队 -2 分');
const oldSolo = {mode: 'party', rewards: [{player_id: 'a', reward_half_units: 2}],
  penalties: [{player_id: 'b', deduction_half_units: 4}]};
assert.equal(lines(oldSolo).loss, '唐焰 +1 分　南杉 -2 分');
assert.equal(lines({mode: 'party', loser_player_id: 'b', loser_team_id: 'blue', score_delta: -.5}).loss, '蓝队 -0.5 分');
assert.equal(lines({mode: 'party', loser_player_id: 'b'}).loss, '', 'never guess missing deductions');
assert.equal(lines({...party, score_changes: []}).loss, '', 'do not fall back over authoritative empty changes');
assert.equal(lines({...party, mode: undefined}, {mode: 'party'}).loss, '红队 +1 分　蓝队 -1 分');
for (const keep of [false, true]) {
  const surrendered = {...party, phase: 'surrendered', preserve_dice: keep, actual_count: null,
    revealed_dice_by_player: {}, rewards: [], penalties: [],
    score_changes: [{unit_id: 'blue', score_delta: -.5}]};
  const board = render(surrendered);
  assert.equal(find(board, 'liars-result-title')[0].textContent, '主动投降');
  assert.equal(find(board, 'liars-result-outcome')[0].textContent, '南杉投降认罚。');
  assert.equal(find(board, 'liars-result-loss')[0].textContent, '蓝队 -0.5 分');
  assert.equal(find(board, 'liars-reveal-details').length, 0, 'no empty reveal');
  assert.doesNotMatch(text(board), /叫点成功|质疑成功|开骰计入|数量|null|undefined/);
}
// A surrendered legacy room may contain a real reveal: display it without invented counting.
assert.equal(find(render({...party, phase: 'surrendered'}), 'liars-revealed-player').length, 2);
const special = {...party, dark_reversed: true, penalties: [{player_id: 'b', team_id: 'blue',
  hits: ['cup_loser', 'under_half', 'companion_self'], raw_multiplier: 8, multiplier: 4, capped: true, score_delta: -4}]};
const details = find(render(special), 'liars-penalty-details')[0];
assert.equal(details.children[0].textContent, '计分原因');
assert.equal(details.open, false);
assert.match(text(details), /蓝队：围骰加倍、点数不足一半再加倍/);
assert.match(text(details), /最多扣4分/);
assert.match(text(details), /无人持有所叫点数，胜负反转/);
assert.doesNotMatch(text(details), /原始|倍率|围骰败方|胜方\+1|[+-]\d+分|score_/);
assert.equal(find(render({...party, penalties: [], dark_reversed: true}), 'liars-penalty-details').length, 1);
assert.equal(find(render({...party, penalties: [{hits: ['unknown_internal_field']}]}), 'liars-penalty-details').length, 0);
assert.equal(render(null).children.length, 0, 'no placeholder card without a round result');
console.log('PASS result titles, authoritative counts, classic dice, party net scores, legacy, reversals, surrender and terminal DOM');
